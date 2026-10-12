from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import park as park_mod
from alan.handover import Handover, OpenQuestion
from alan.routes import _answer_waiver
from alan.teams import _pr_audit_log, _pr_waiver_dismissal
from alan.teams._verdict import CriticalCountReport
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import create

_REWRITE_OPTION = "Rewrite or drop this clause, then re-dispatch verification."
_WAIVE_OPTION = "Waive this clause and proceed to merge."
_CAPABILITY_RESOLVE_OPTION = (
    "Resolve this outside the fix-worker loop, then re-dispatch verification."
)
_CAPABILITY_WAIVE_OPTION = "Waive this finding and proceed to merge."
_ZERO_CRITICAL_ARTIFACT = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"


def _clause_stall_question() -> OpenQuestion:
    return OpenQuestion(
        id="clause-stall-abc",
        question="waive?",
        proposed_answer_space=[_REWRITE_OPTION, _WAIVE_OPTION],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        recommended=_REWRITE_OPTION,
        option_resolvers={_REWRITE_OPTION: "verification", _WAIVE_OPTION: "pr_merge"},
    )


def _capability_boundary_question() -> OpenQuestion:
    return OpenQuestion(
        id="verif-capability-boundary-abc",
        question="unresolvable",
        proposed_answer_space=[_CAPABILITY_RESOLVE_OPTION, _CAPABILITY_WAIVE_OPTION],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        recommended=_CAPABILITY_RESOLVE_OPTION,
        option_resolvers={
            _CAPABILITY_RESOLVE_OPTION: "verification", _CAPABILITY_WAIVE_OPTION: "pr_merge",
        },
    )


def _merge_block_question(*, proceed_option: str = "proceed") -> OpenQuestion:
    return OpenQuestion(
        id="mb-1",
        question="unblock?",
        proposed_answer_space=[proceed_option, "wait"],
        question_class="merge_block_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={proceed_option: "pr_merge"},
    )


def _handover(
    task_id: str, *, pr_number: int | None, from_invocation_id: str = "inv_verify",
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id=from_invocation_id,
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        open_questions=[],
        pr_number=pr_number,
    )


def test_is_waiver_true_for_clause_stall_waive_option() -> None:
    assert _answer_waiver.is_waiver(_clause_stall_question(), _WAIVE_OPTION) is True


def test_is_waiver_true_for_capability_boundary_waive_option() -> None:
    assert (
        _answer_waiver.is_waiver(_capability_boundary_question(), _CAPABILITY_WAIVE_OPTION)
        is True
    )


def test_is_waiver_false_for_non_waiver_option_on_verification_escalation() -> None:
    assert _answer_waiver.is_waiver(_clause_stall_question(), _REWRITE_OPTION) is False


def test_is_waiver_false_when_class_is_not_verification_escalation() -> None:
    q = _merge_block_question()
    assert _answer_waiver.is_waiver(q, "proceed") is False


def test_is_waiver_false_when_answer_not_in_option_resolvers() -> None:
    q = _clause_stall_question()
    assert _answer_waiver.is_waiver(q, "unknown-answer") is False


def test_is_waiver_true_for_legacy_operator_only_escalation_with_empty_option_resolvers() -> None:
    from alan import clause_withdrawal  # noqa: PLC0415
    from alan.handover import OpenQuestion  # noqa: PLC0415

    q = OpenQuestion(
        id=clause_withdrawal.CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID,
        question="operator-only?",
        proposed_answer_space=[
            clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_LEGACY,
            clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING,
        ],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    assert (
        _answer_waiver.is_waiver(q, clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_LEGACY)
        is True
    )


def test_is_waiver_false_when_class_matches_but_resolve_human_ruling_returns_none() -> None:
    q = _merge_block_question()
    assert (
        _answer_waiver.is_waiver(
            q.model_copy(update={"question_class": "verification_escalation"}), "wait"
        )
        is False
    )


def test_is_waiver_true_for_pr_state_override_wording() -> None:
    from alan import clause_withdrawal  # noqa: PLC0415
    from alan.handover import OpenQuestion  # noqa: PLC0415

    q = OpenQuestion(
        id=clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
        question="pr state?",
        proposed_answer_space=[
            "Update the PR's state on GitHub, then re-dispatch verification.",
            clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING,
        ],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    assert (
        _answer_waiver.is_waiver(q, clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING) is True
    )


def test_waiver_floor_inputs_reads_reviewer_and_floor_reason(ws_root: Path) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    blocking = _handover(ws.task_id, pr_number=42)
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(ws, blocking)
    assert reviewer_count == 0
    assert floor_reason is None


def test_waiver_floor_inputs_withholds_when_artifact_missing(ws_root: Path) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    blocking = _handover(ws.task_id, pr_number=42, from_invocation_id="inv_none")
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(ws, blocking)
    assert reviewer_count is None
    assert floor_reason == "count_not_reported"


def test_waiver_floor_inputs_withholds_when_record_missing(ws_root: Path) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT)
    blocking = _handover(ws.task_id, pr_number=42)
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(ws, blocking)
    assert reviewer_count is None
    assert floor_reason == "count_not_reported"


def test_apply_returns_default_application_when_pr_number_none(ws_root: Path) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    blocking = _handover(ws.task_id, pr_number=None)
    q = _clause_stall_question()
    result = _answer_waiver.apply(ws=ws, question=q, answer=_WAIVE_OPTION, blocking=blocking)
    assert result == _answer_waiver.WaiverApplication()


def test_apply_returns_default_application_when_not_a_waiver(ws_root: Path) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    blocking = _handover(ws.task_id, pr_number=42)
    q = _clause_stall_question()
    result = _answer_waiver.apply(ws=ws, question=q, answer=_REWRITE_OPTION, blocking=blocking)
    assert result == _answer_waiver.WaiverApplication()


def test_apply_cleared_on_dismissed_with_no_other_author_blocking(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    blocking = _handover(ws.task_id, pr_number=42)
    q = _clause_stall_question()
    monkeypatch.setattr(
        _answer_waiver, "dismiss_for_waiver",
        lambda *_a, **_kw: _pr_waiver_dismissal.WaiverDismissal("dismissed", (7,), False),
    )
    result = _answer_waiver.apply(ws=ws, question=q, answer=_WAIVE_OPTION, blocking=blocking)
    assert result.cleared is True
    assert result.blocker is None


def test_apply_not_cleared_and_no_blocker_when_other_author_blocking(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    blocking = _handover(ws.task_id, pr_number=42)
    q = _clause_stall_question()
    monkeypatch.setattr(
        _answer_waiver, "dismiss_for_waiver",
        lambda *_a, **_kw: _pr_waiver_dismissal.WaiverDismissal("dismissed", (7,), True),
    )
    result = _answer_waiver.apply(ws=ws, question=q, answer=_WAIVE_OPTION, blocking=blocking)
    assert result.cleared is False
    assert result.blocker is None


def test_apply_default_application_when_nothing_found_to_dismiss(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    blocking = _handover(ws.task_id, pr_number=42)
    monkeypatch.setattr(
        _answer_waiver, "dismiss_for_waiver",
        lambda *_a, **_kw: _pr_waiver_dismissal.WaiverDismissal("none_found", (), True),
    )
    result = _answer_waiver.apply(
        ws=ws, question=_clause_stall_question(), answer=_WAIVE_OPTION, blocking=blocking,
    )
    assert result == _answer_waiver.WaiverApplication()


def test_apply_withheld_by_floor_carries_blocker_naming_the_floor_reason(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Rules", "d", root=ws_root)
    blocking = _handover(ws.task_id, pr_number=42, from_invocation_id="inv-nocount")
    q = _clause_stall_question()
    result = _answer_waiver.apply(ws=ws, question=q, answer=_WAIVE_OPTION, blocking=blocking)
    assert result.cleared is False
    blocker = result.blocker
    assert blocker is not None
    assert blocker.kind == "reason"
    assert blocker.name == park_mod.WAIVER_WITHHELD_BLOCKER == "waiver-dismissal-withheld"
    assert "PR #42" in blocker.detail
    assert "withheld_floor" in blocker.detail
    assert "count_not_reported" in blocker.detail
    assert blocker.instruction is not None
    assert "PR #42" in blocker.instruction
    assert "by hand" in blocker.instruction
    assert "pr_merge" in blocker.instruction
    assert blocker.action_url is None


def test_apply_falls_back_to_failed_on_unexpected_exception(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("Waiver Rules", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    blocking = _handover(ws.task_id, pr_number=42)
    q = _clause_stall_question()

    def boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("gh exploded")

    monkeypatch.setattr(_answer_waiver, "dismiss_for_waiver", boom)
    result = _answer_waiver.apply(ws=ws, question=q, answer=_WAIVE_OPTION, blocking=blocking)
    assert result.cleared is False
    assert result.blocker is not None
    assert result.blocker.name == park_mod.WAIVER_WITHHELD_BLOCKER
    assert "failed" in result.blocker.detail
    assert "dismissal call failed" in result.blocker.detail
    log = tmp_path / "logs" / _pr_audit_log.AUDIT_FILE
    entries = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    row = next(e for e in entries if e["action"] == _pr_waiver_dismissal.AUDIT_ACTION)
    assert row["outcome"] == "failed"
