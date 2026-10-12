from __future__ import annotations

import json
import time as _time_mod
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import acceptance as _acceptance_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _base as base_mod
from alan.teams import _pr_ready, _verification_gates
from alan.teams import verification as verif_mod
from alan.teams.verification import (
    ROLES,
    SCENARIOS_COVERED_BY_DOD_MARKER,
    TEAM,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover


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


def test_default_settle_max_polls_sized_for_worst_case_single_pr_lane_drain() -> None:
    assert _verification_gates._DEFAULT_SETTLE_MAX_POLLS == 108  # noqa: SLF001


def test_settle_max_polls_env_override_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "7")
    assert _verification_gates._settle_max_polls() == 7  # noqa: SLF001


def _pending_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
        pending_checks=("ci",),
    )


def test_settle_wait_pending_then_green_approves(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "3")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    calls: dict[str, int] = {"n": 0}

    def fake_blockers(pr: int, tid: str, **_kw: object) -> _pr_ready.PrReadiness:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return _pending_readiness()
        return _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        )

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake_blockers)
    result = verif_mod.resolve_external_gates(
        _DEFAULT_JUDGE_OUTPUT, 42, workspace.task_id,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert not result.downgraded
    assert "APPROVE" in result.output
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(e["action"] == "verifier_settle_green" for e in entries)


def test_settle_wait_pending_then_fail_downgrades(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "3")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    calls: dict[str, int] = {"n": 0}

    def fake_blockers(pr: int, tid: str, **_kw: object) -> _pr_ready.PrReadiness:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return _pending_readiness()
        return _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
            failing_checks=("Tests",), failed_reasons=("failing checks: Tests",),
        )

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake_blockers)
    result = verif_mod.resolve_external_gates(
        _DEFAULT_JUDGE_OUTPUT, 42, workspace.task_id,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.downgraded
    assert result.flaky_only is False
    assert "REQUEST_CHANGES" in result.output
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(e["action"] == "verifier_settle_fail" for e in entries)


def test_settle_budget_exhausted_proceeds_to_approve(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "2")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _pending_readiness()
    )
    result = verif_mod.resolve_external_gates(
        _DEFAULT_JUDGE_OUTPUT, 42, workspace.task_id,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert not result.downgraded
    assert "APPROVE" in result.output
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(e["action"] == "verifier_settle_timeout" for e in entries)


def test_settle_transient_gh_fail_during_settle_approves(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "5")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    calls: dict[str, int] = {"n": 0}

    def fake_blockers(pr: int, tid: str, **_kw: object) -> _pr_ready.ReadinessResult:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return _pending_readiness()
        return _pr_ready.ReadinessUnavailable("timeout")

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake_blockers)
    result = verif_mod.resolve_external_gates(
        _DEFAULT_JUDGE_OUTPUT, 42, workspace.task_id,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert not result.downgraded
    assert calls["n"] == 2


class TestScopedDodVerification:
    @pytest.fixture(autouse=True)
    def _no_real_review_post(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")

    def _patch_streaming_capturing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        captured: list[str],
        judge_output: str = _DEFAULT_JUDGE_OUTPUT,
    ) -> None:
        counter = {"n": 0}

        def fake(
            system_prompt: str,  # noqa: ARG001
            user_text: str,
            *,
            model: str = "sonnet",  # noqa: ARG001
            on_event: Callable[[StreamEvent], None] | None = None,
            stream_path: Path | None = None,
            max_turns: int = 10,  # noqa: ARG001
            cwd: object = None,  # noqa: ARG001
            **_kwargs: object,
        ) -> AgentStreamingResult:
            counter["n"] += 1
            text = judge_output if counter["n"] == len(ROLES) else f"out-{counter['n']}"
            captured.append(user_text)
            if stream_path is not None:
                stream_path.parent.mkdir(parents=True, exist_ok=True)
                stream_path.write_text(
                    json.dumps({"type": "result", "result": text}) + "\n"
                )
            if on_event is not None:
                on_event({"type": "result", "result": text})
            return AgentStreamingResult(result_text=text)

        monkeypatch.setattr(base_mod, "run_agent_streaming", fake)

    def test_in_scope_block_appears_in_judge_text(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _acceptance_mod,
            "scoped_dod",
            lambda _meta, _tid: (["feature works"], ["sibling feature"]),
        )
        captured: list[str] = []
        self._patch_streaming_capturing(monkeypatch, captured)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        judge_text = captured[-1]
        assert "## IN-SCOPE ACCEPTANCE OUTCOMES" in judge_text
        assert "- feature works" in judge_text
        assert "## OUT-OF-SCOPE ACCEPTANCE OUTCOMES" in judge_text
        assert "- sibling feature" in judge_text

    def test_no_scope_block_when_scoped_dod_returns_none(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _acceptance_mod, "scoped_dod", lambda _meta, _tid: None
        )
        captured: list[str] = []
        self._patch_streaming_capturing(monkeypatch, captured)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        judge_text = captured[-1]
        assert "## IN-SCOPE ACCEPTANCE OUTCOMES" not in judge_text

    def test_missing_scenarios_replaced_by_covered_marker(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _acceptance_mod,
            "scoped_dod",
            lambda _meta, _tid: (["owned outcome"], []),
        )
        captured: list[str] = []
        self._patch_streaming_capturing(monkeypatch, captured)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        assert any(SCENARIOS_COVERED_BY_DOD_MARKER in t for t in captured)

    def test_task_id_forwarded_to_scoped_dod(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_ids: list[str] = []

        def fake_scoped(
            meta: object, task_id: str  # noqa: ARG001
        ) -> tuple[list[str], list[str]] | None:
            captured_ids.append(task_id)
            return None

        monkeypatch.setattr(_acceptance_mod, "scoped_dod", fake_scoped)
        _patch_streaming(monkeypatch)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        assert captured_ids and captured_ids[0] == workspace.task_id

    def test_explicit_subtask_task_id_forwarded_to_scoped_dod(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_ids: list[str] = []

        def fake_scoped(
            meta: object, task_id: str  # noqa: ARG001
        ) -> tuple[list[str], list[str]] | None:
            captured_ids.append(task_id)
            return None

        monkeypatch.setattr(_acceptance_mod, "scoped_dod", fake_scoped)
        _patch_streaming(monkeypatch)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None, task_id="subtask-99")
        assert captured_ids and captured_ids[0] == "subtask-99"

    def test_empty_in_scope_does_not_append_scope_block(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _acceptance_mod,
            "scoped_dod",
            lambda _meta, _tid: ([], ["sibling outcome"]),
        )
        captured: list[str] = []
        self._patch_streaming_capturing(monkeypatch, captured)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        assert not any("## IN-SCOPE ACCEPTANCE OUTCOMES" in t for t in captured)

    def test_empty_in_scope_does_not_emit_covered_marker(
        self,
        workspace: Workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            _acceptance_mod,
            "scoped_dod",
            lambda _meta, _tid: ([], ["sibling outcome"]),
        )
        captured: list[str] = []
        self._patch_streaming_capturing(monkeypatch, captured)
        prior = _impl_handover(workspace)
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
        assert not any(SCENARIOS_COVERED_BY_DOD_MARKER in t for t in captured)
