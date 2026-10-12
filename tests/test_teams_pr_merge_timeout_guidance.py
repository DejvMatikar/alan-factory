from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import OpenQuestion
from alan.teams import _pr_merge_escalation, _pr_merge_no_signal, _pr_ready
from alan.teams import pr_merge as pr_merge_mod
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-timeout-guidance-task", "gate the merge", root=tmp_path / "ws")


def test_checks_recovery_recipe_has_no_timeout_entry() -> None:
    assert "timeout" not in _pr_merge_escalation.CHECKS_RECOVERY_RECIPE


def test_timeout_ctx_records_factual_state_line(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="CHANGES_REQUESTED",
            failing_checks=("Backend tests",),
            pending_checks=("Frontend tests", "Lint"),
        ),
    )
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 12.34, 5, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ctx["timeout_state"] == (
        "Recorded state at give-up: 1 failing check(s), 2 pending "
        "check(s), review decision CHANGES_REQUESTED."
    )


def test_timeout_ctx_reports_none_review_decision_when_blank(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="",
        ),
    )
    ctx = _pr_merge_no_signal.timeout_ctx(workspace, 42, 1.0, 0, forge_target=DEFAULT_FORGE_TARGET)
    assert ctx["timeout_state"] == (
        "Recorded state at give-up: 0 failing check(s), 0 pending "
        "check(s), review decision none."
    )


def test_timeout_ctx_omits_timeout_state_when_gh_unavailable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_kw: _pr_ready.ReadinessUnavailable("forge unavailable: rate limited"),
    )
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 12.34, 5, forge_target=DEFAULT_FORGE_TARGET
    )
    assert "timeout_state" not in ctx
    assert ctx["blocked_no_signal"] is False


def test_timeout_recipe_returns_factual_state_when_present() -> None:
    ctx = {
        "blocked_no_signal": False,
        "timeout_state": "Recorded state at give-up: 1 failing check(s).",
    }
    assert _pr_merge_no_signal.timeout_recipe(ctx, "default text") == ctx["timeout_state"]


def test_timeout_recipe_prefers_blocked_no_signal_over_timeout_state() -> None:
    ctx = {
        "blocked_no_signal": True,
        "timeout_state": "Recorded state at give-up: 0 failing check(s).",
    }
    recipe = _pr_merge_no_signal.timeout_recipe(ctx, "default text")
    assert recipe == _pr_merge_no_signal.BLOCKED_NO_SIGNAL_TIMEOUT_RECIPE


def test_timeout_reason_reports_recorded_state_without_asserting_a_cause() -> None:
    ctx = {
        "elapsed": 600.0,
        "polls": 20,
        "blocked_no_signal": False,
        "timeout_state": (
            "Recorded state at give-up: 1 failing check(s), 0 pending "
            "check(s), review decision none."
        ),
    }
    reason = _pr_merge_escalation.checks_reason("timeout", 42, ctx)
    assert "Recorded state at give-up: 1 failing check(s)" in reason
    assert "stuck workflow run" not in reason
    assert "missing required check" not in reason


_HEAD = "0123456789abcdef0123"


def _timeout_park(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, readiness: _pr_ready.ReadinessResult,
) -> tuple[pr_merge_mod.PrMergeOutcome, OpenQuestion, list[int]]:
    reads: list[int] = []

    def fake(pr_number: int, task_id: str, **_kw: object) -> _pr_ready.ReadinessResult:
        reads.append(pr_number)
        return readiness

    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", fake)
    ctx = _pr_merge_no_signal.timeout_ctx(
        workspace, 42, 630.0, 21, forge_target=DEFAULT_FORGE_TARGET
    )
    outcome = pr_merge_mod._outcome_from_checks(  # noqa: SLF001
        workspace, 42, "timeout", ctx, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome is not None
    handover = pr_merge_mod._await_human_handover(workspace, None, "inv_002", outcome)  # noqa: SLF001
    [question] = handover.open_questions
    return outcome, question, reads


def test_pr_merge_outcome_timeout_blocker_defaults_to_empty() -> None:
    outcome = pr_merge_mod.PrMergeOutcome(kind="timeout", pr_number=42, reason="r")
    assert outcome.timeout_blocker == ""


def test_settled_checks_with_a_standing_change_request_park_with_review_options(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness = _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="CHANGES_REQUESTED",
        head_sha=_HEAD,
    )
    outcome, question, reads = _timeout_park(workspace, monkeypatch, readiness)
    assert outcome.timeout_blocker == _pr_merge_no_signal.CHANGES_REQUESTED_BLOCKER
    assert "CHANGES_REQUESTED" in question.question
    assert _HEAD[:12] in question.question
    assert question.proposed_answer_space == list(_pr_merge_escalation.SETTLED_REVIEW_OPTIONS)
    assert question.recommended == _pr_merge_escalation.SETTLED_REVIEW_OPTIONS[0]
    assert all("settl" not in option.lower() for option in question.proposed_answer_space)
    assert reads == [42]


@pytest.mark.parametrize(
    "readiness",
    [
        _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="CHANGES_REQUESTED",
            pending_checks=("Lint",), head_sha=_HEAD,
        ),
        _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="",
            failing_checks=("Lint",), head_sha=_HEAD,
        ),
        _pr_ready.ReadinessUnavailable("gh unavailable"),
    ],
)
def test_a_timeout_that_is_not_a_settled_change_request_keeps_the_unsettled_checks_options(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, readiness: _pr_ready.ReadinessResult,
) -> None:
    outcome, question, reads = _timeout_park(workspace, monkeypatch, readiness)
    assert outcome.timeout_blocker == ""
    assert question.proposed_answer_space == list(_pr_merge_escalation.TIMEOUT_OPTIONS)
    assert reads == [42]


def test_checks_reason_timeout_falls_back_to_default_without_recorded_state() -> None:
    reason = _pr_merge_escalation.checks_reason(
        "timeout", 42, {"elapsed": 600.0, "polls": 20},
    )
    assert "re-dispatch pr_merge" in reason
    assert "stuck workflow run" not in reason
