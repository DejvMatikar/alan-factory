from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from alan import acceptance as _acceptance_mod
from alan import process_runner
from alan.handover import TestScenario
from alan.teams import _scenario_gate
from alan.teams import verification as verif_mod
from alan.teams.verification import TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _fake_gh_ok(captured: list[list[str]] | None = None) -> object:
    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        if captured is not None:
            captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def test_count_flip_banner_fires_through_the_real_reviewer_fallback_and_clause_audit_path(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    judge_out = (
        "## Rationale\n"
        "Example of a well-formed line:\n"
        "`DIFF_FINDINGS_CRITICAL: 0`\n\n"
        "## Verdict\n\n"
        "VERDICT: APPROVE\n"
    )
    support.patch_streaming(
        monkeypatch, judge_output=judge_out, reviewer_output="DIFF_FINDINGS_CRITICAL: 1\n"
    )
    prior = support.impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert "VERDICT FLIPPED" in handover.full_synthesis
    assert "VERDICT: REQUEST_CHANGES" in handover.full_synthesis
    assert "VERDICT: APPROVE" not in handover.full_synthesis
    artifact_content = Path(handover.artifacts_produced[0].path).read_text()
    assert "VERDICT FLIPPED" in artifact_content
    assert "VERDICT: REQUEST_CHANGES" in artifact_content


def test_scenario_gate_banner_fires_through_the_real_scenario_runner_result(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    fail_scenario = TestScenario(
        id="s_fail",
        description="deliberately fails",
        given="nothing",
        when="cat /nonexistent-file-xyz-verdict-composition-test",
        then="",
    )
    prior = support.impl_handover(workspace).model_copy(
        update={"test_scenarios": [fail_scenario]}
    )
    support.patch_streaming(monkeypatch)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert "SCENARIO GATE" in handover.full_synthesis
    assert "s_fail" in handover.full_synthesis
    assert "VERDICT: REQUEST_CHANGES" in handover.full_synthesis
    assert "VERDICT: APPROVE" not in handover.full_synthesis
    artifact_content = Path(handover.artifacts_produced[0].path).read_text()
    assert "SCENARIO GATE" in artifact_content


def test_resume_invocation_gate_blocks_from_persisted_outcomes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    fail_scenario = TestScenario(
        id="s_fail",
        description="deliberately fails",
        given="nothing",
        when="cat /nonexistent-file-xyz-resume-test",
        then="",
    )
    prior = support.impl_handover(workspace).model_copy(
        update={"test_scenarios": [fail_scenario]}
    )
    support.patch_streaming(monkeypatch)
    handover1 = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    inv_id = handover1.from_invocation_id
    assert inv_id is not None
    outcomes_file = _scenario_gate.outcomes_path(workspace, inv_id)
    assert outcomes_file.exists()
    before_bytes = outcomes_file.read_bytes()
    before_mtime = outcomes_file.stat().st_mtime_ns

    monkeypatch.setattr(verif_mod, "_scenario_runner_already_succeeded", lambda _ws, _inv: True)
    support.patch_streaming(monkeypatch)
    handover2 = TEAM.run(workspace, prior, lambda _e: None, inv_id=inv_id)

    assert outcomes_file.read_bytes() == before_bytes
    assert outcomes_file.stat().st_mtime_ns == before_mtime
    assert "SCENARIO GATE" in handover2.full_synthesis
    assert "s_fail" in handover2.full_synthesis
    assert "VERDICT: REQUEST_CHANGES" in handover2.full_synthesis


def test_reviewer_count_does_not_leak_between_sequential_invocations(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    support.patch_streaming(monkeypatch, reviewer_output="DIFF_FINDINGS_CRITICAL: 4\n")
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    support.patch_streaming(monkeypatch)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts_rows = [e for e in entries if e["action"] == "verdict_counts"]
    assert len(counts_rows) == 2
    assert counts_rows[0]["critical_reviewer"] == 4
    assert counts_rows[1]["critical_reviewer"] is None


@pytest.mark.parametrize(
    "judge_body",
    [
        (
            "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
            "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n\n"
            "## Rationale\nThe flagged item is an explicitly out-of-scope deliverable."
        ),
        (
            "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
            "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n\n"
            "## Rationale\nRan the linter myself; the reported complaint is a false positive."
        ),
    ],
)
def test_scoped_in_scope_count_keeps_approve_and_records_divergence(
    judge_body: str, monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _acceptance_mod,
        "scoped_dod",
        lambda _meta, _tid: (["owned outcome"], ["sibling outcome"]),
    )
    support.patch_streaming(
        monkeypatch, judge_output=judge_body, reviewer_output="DIFF_FINDINGS_CRITICAL: 1\n"
    )
    monkeypatch.setattr(process_runner, "run_subprocess", _fake_gh_ok())
    prior = support.impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert "VERDICT: APPROVE" in handover.full_synthesis
    assert "VERDICT FLIPPED" not in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts = next(e for e in entries if e["action"] == "verdict_counts")
    assert counts["critical_reviewer"] == 1
    assert counts["reviewer_divergence"] is True
    assert counts["effective_verdict"] == "APPROVE"
