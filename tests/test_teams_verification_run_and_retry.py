from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from alan import acceptance as _acceptance_mod
from alan import process_runner
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _base as base_mod
from alan.teams import verification as verif_mod
from alan.teams._base import TeamEvent
from alan.teams.verification import (
    ROLES,
    TEAM,
    dry_run_enabled,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


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


def test_dry_run_enabled_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("1", "true", "yes", "on", "YES"):
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", val)
        assert dry_run_enabled() is True
    for val in ("0", "false", ""):
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", val)
        assert dry_run_enabled() is False


def test_run_dry_run_records_intent(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("gh should not be called in dry-run")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 42
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "dry_run" and e["verdict"] == "APPROVE" for e in entries
    )


def test_audit_inv_id_is_implementer_not_verifier_dry_run(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    dry_run_entry = next(e for e in entries if e["action"] == "dry_run")
    assert dry_run_entry["inv_id"] == "inv_001"


def test_audit_inv_id_is_implementer_not_verifier_live(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    review_ok_entry = next(e for e in entries if e["action"] == "review_ok")
    assert review_ok_entry["inv_id"] == "inv_001"


def test_run_live_approve_posts_an_approve_review_on_the_default_repository(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [(args[0], kwargs) for args, kwargs in forge.calls("post_review")] == [
        (42, {"forge_target": DEFAULT_FORGE_TARGET, "as_verifier": False}),
    ]
    payload = forge.review_payloads()[0]
    assert payload["event"] == "APPROVE"
    assert payload["body"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_ok" for e in entries)


def test_run_live_request_changes_posts_a_request_changes_review(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge_out = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    _patch_streaming(monkeypatch, judge_output=judge_out)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [p["event"] for p in forge.review_payloads()] == ["REQUEST_CHANGES"]


def test_run_approve_with_critical_findings_downgrades_to_request_changes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge_out = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 3\n"
    _patch_streaming(monkeypatch, judge_output=judge_out)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert [p["event"] for p in forge.review_payloads()] == ["REQUEST_CHANGES"]


def test_run_split_subtask_out_of_scope_criticals_approve_routes_pr_merge(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(
        _acceptance_mod,
        "scoped_dod",
        lambda _meta, _tid: (["owned outcome"], ["sibling outcome"]),
    )
    judge_out = (
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_out)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert [p["event"] for p in forge.review_payloads()] == ["APPROVE"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts = next(e for e in entries if e["action"] == "verdict_counts")
    assert counts["critical_raw"] == 2
    assert counts["critical_in_scope"] == 0
    assert counts["effective_verdict"] == "APPROVE"
    assert counts["critical_reviewer"] is None
    assert counts["reviewer_divergence"] is False


def test_run_in_scope_line_ignored_without_scope_block(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(_acceptance_mod, "scoped_dod", lambda _meta, _tid: None)
    judge_out = (
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_out)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert "DIFF_FINDINGS_CRITICAL_IN_SCOPE" not in handover.full_synthesis
    assert [p["event"] for p in forge.review_payloads()] == ["REQUEST_CHANGES"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts = next(e for e in entries if e["action"] == "verdict_counts")
    assert counts["critical_in_scope"] is None
    assert counts["effective_verdict"] == "REQUEST_CHANGES"
    assert counts["critical_reviewer"] is None
    assert counts["reviewer_divergence"] is False
    gate_path_entry = next(e for e in entries if e["action"] == "gate_path")
    assert gate_path_entry["mode"] == "skip"


def test_run_skips_when_judge_output_missing_verdict_line(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output="no verdict here\n")

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("gh should not be called without parseable verdict")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "skip" and "missing VERDICT" in e["reason"]
        for e in entries
    )
    assert any(e["action"] == "judge_verdict_missing_retry" for e in entries)
    assert any(e["action"] == "judge_parse_failed" for e in entries)
    assert any(e.kind == "judge_parse_failed" for e in events)
    assert handover.open_questions
    assert handover.open_questions[0].id == "verif-judge-parse"
    assert handover.open_questions[0].blocking is True
    assert "VERDICT" in handover.open_questions[0].question


def _patch_streaming_retry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}

    def fake(
        system_prompt: str,  # noqa: ARG001
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if counter["n"] == len(ROLES):
            text = "no verdict here\n"
        elif counter["n"] == len(ROLES) + 1:
            text = _DEFAULT_JUDGE_OUTPUT
        else:
            text = f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def test_run_judge_retry_succeeds_posts_review(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming_retry_succeeds(monkeypatch)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert not handover.open_questions
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    retry_entry = next(e for e in entries if e["action"] == "judge_verdict_missing_retry")
    assert retry_entry["reason"] == "missing_verdict"
    assert any(e["action"] == "dry_run" and e["verdict"] == "APPROVE" for e in entries)
    assert not any(e["action"] == "judge_parse_failed" for e in entries)


def test_run_judge_retry_four_node_events(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch, judge_output="no verdict here\n")
    events: list[TeamEvent] = []
    seed_and_run(TEAM, workspace, _impl_handover(workspace), events.append)
    started_roles = [e.payload["role"] for e in events if e.kind == "node_started"]
    completed_roles = [e.payload["role"] for e in events if e.kind == "node_completed"]
    assert sorted(started_roles) == sorted(
        ["diff_reviewer", "scenario_runner", "judge", "judge"]
    )
    assert sorted(completed_roles) == sorted(
        ["diff_reviewer", "scenario_runner", "judge", "judge"]
    )
    assert started_roles[2:] == ["judge", "judge"]
    assert completed_roles[2:] == ["judge", "judge"]
    assert set(started_roles[:2]) == {"diff_reviewer", "scenario_runner"}
    assert set(completed_roles[:2]) == {"diff_reviewer", "scenario_runner"}


def test_pipeline_emits_three_node_events(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    events: list[TeamEvent] = []
    seed_and_run(TEAM, workspace, _impl_handover(workspace), events.append)
    started = [e.payload["role"] for e in events if e.kind == "node_started"]
    completed = [
        e.payload["role"] for e in events if e.kind == "node_completed"
    ]
    assert len(started) == 3
    assert set(started[:2]) == {"diff_reviewer", "scenario_runner"}
    assert started[2] == "judge"
    assert len(completed) == 3
    assert set(completed[:2]) == {"diff_reviewer", "scenario_runner"}
    assert completed[2] == "judge"


def test_run_no_retry_when_verdict_present(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    seed_and_run(TEAM, workspace, _impl_handover(workspace), lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert not any(e["action"] == "judge_verdict_missing_retry" for e in entries)


def _patch_streaming_missing_in_scope_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"n": 0}
    first_judge = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n\n"
        "## Rationale\nlooks fine on the merits."
    )
    retried_judge = (
        "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n\n## Rationale\nboth findings out of scope."
    )

    def fake(
        system_prompt: str,  # noqa: ARG001
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if counter["n"] == len(ROLES):
            text = first_judge
        elif counter["n"] == len(ROLES) + 1:
            text = retried_judge
        else:
            text = f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def test_run_judge_retry_fires_when_in_scope_count_missing(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(
        _acceptance_mod,
        "scoped_dod",
        lambda _meta, _tid: (["owned outcome"], ["sibling outcome"]),
    )
    _patch_streaming_missing_in_scope_retry(monkeypatch)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    retry_entry = next(e for e in entries if e["action"] == "judge_verdict_missing_retry")
    assert retry_entry["reason"] == "missing_in_scope_count"


def _patch_streaming_reviewer_and_judge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    diff_reviewer_output: str,
    judge_output: str,
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt.startswith("You are the diff_reviewer"):
            text = diff_reviewer_output
        elif system_prompt.startswith("You are the judge"):
            text = judge_output
        else:
            text = "out-scenario_runner"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def test_run_judge_omits_critical_count_falls_back_to_reviewer_count(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming_reviewer_and_judge(
        monkeypatch,
        diff_reviewer_output="## Critical findings\n1. bug\n\nDIFF_FINDINGS_CRITICAL: 2\n",
        judge_output="## Verdict\n\nVERDICT: APPROVE\n\n## Rationale\nlooks fine.",
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert "VERDICT FLIPPED" in handover.full_synthesis
    assert "VERDICT: APPROVE" not in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts = next(e for e in entries if e["action"] == "verdict_counts")
    assert counts["critical_raw"] == 2
    assert counts["effective_verdict"] == "REQUEST_CHANGES"
    assert counts["critical_reviewer"] == 2
    assert counts["reviewer_divergence"] is False


def test_run_judge_omits_critical_count_falls_back_to_zero_when_reviewer_clean(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming_reviewer_and_judge(
        monkeypatch,
        diff_reviewer_output="## Critical findings\nNone.\n\nDIFF_FINDINGS_CRITICAL: 0\n",
        judge_output="## Verdict\n\nVERDICT: APPROVE\n\n## Rationale\nlooks fine.",
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    counts = next(e for e in entries if e["action"] == "verdict_counts")
    assert counts["critical_raw"] == 0
    assert counts["effective_verdict"] == "APPROVE"
    assert counts["critical_reviewer"] == 0
    assert counts["reviewer_divergence"] is False


def test_emit_judge_parse_failed_helper_with_pr(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    prior = _impl_handover(workspace, pr_number=77)
    events: list[TeamEvent] = []
    handover = verif_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, prior, events.append, "inv_x", "bad judge output"
    )
    assert handover.to_team_suggestions == []
    assert handover.pr_number == 77
    assert handover.decisions == []
    assert handover.scenario_results == []
    assert len(handover.open_questions) == 1
    assert handover.open_questions[0].id == "verif-judge-parse"
    assert handover.open_questions[0].blocking is True
    assert handover.open_questions[0].resolver_role == "human"
    assert len(handover.artifacts_produced) == 1
    artifact_content = Path(handover.artifacts_produced[0].path).read_text()
    assert "bad judge output" in artifact_content
    assert any(e.kind == "judge_parse_failed" for e in events)
    assert events[0].payload["pr_number"] == "77"
    assert events[0].payload["task_id"] == workspace.task_id


def test_emit_judge_parse_failed_helper_no_prior(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = verif_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, None, events.append, "inv_y", "judge output"
    )
    assert handover.pr_number is None
    assert handover.to_team_suggestions == []
    assert handover.decisions == []
    assert len(handover.open_questions) == 1
    assert any(e.kind == "judge_parse_failed" for e in events)
    assert events[0].payload["pr_number"] == ""


def test_emit_judge_parse_failed_helper_pr_none(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    prior = _impl_handover(workspace, pr_number=None)
    events: list[TeamEvent] = []
    handover = verif_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, prior, events.append, "inv_z", "judge output"
    )
    assert handover.pr_number is None
    assert events[0].payload["pr_number"] == ""


def test_emit_judge_parse_failed_populates_options_class_and_recommendation(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    prior = _impl_handover(workspace, pr_number=77)
    handover = verif_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, prior, lambda _e: None, "inv_x", "bad judge output"
    )
    [q] = handover.open_questions
    assert q.question_class == "verification_escalation"
    assert 2 <= len(q.proposed_answer_space) <= 4
    assert q.recommended in q.proposed_answer_space
