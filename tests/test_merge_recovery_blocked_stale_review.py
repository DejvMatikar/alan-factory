from __future__ import annotations

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams._pr_ready import PrReadiness


def _readiness(
    *,
    merge_state_status: str = "BLOCKED",
    review_decision: str = "CHANGES_REQUESTED",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    failed_reasons: tuple[str, ...] = (),
    check_count: int = 1,
) -> PrReadiness:
    return PrReadiness(
        ok=False,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        failed_reasons=failed_reasons,
        check_count=check_count,
    )


def test_classify_block_blocked_change_request_no_pending_no_failing_is_stale_review() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STALE_REVIEW


def test_classify_block_blocked_human_change_request_parks_with_outstanding_review_reason() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STALE_REVIEW
    blocker = merge_recovery.unblock_instruction(MergeBlockClass.STALE_REVIEW, pr_number=42)
    assert blocker is not None
    assert blocker.detail == "outstanding changes-requested review"
    assert blocker.instruction is not None
    assert "dismiss/resolve it on the PR" in blocker.instruction


def test_classify_block_blocked_change_request_with_pending_stays_genuine() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        pending_checks=("ci/test",),
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
            "pending checks: ci/test",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_classify_block_blocked_change_request_with_failing_check_is_not_stale_review() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
        failing_checks=("ci/test",),
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
            "failing checks: ci/test",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_classify_block_behind_change_request_without_pending_stays_genuine() -> None:
    r = _readiness(
        merge_state_status="BEHIND",
        review_decision="CHANGES_REQUESTED",
        failed_reasons=(
            "mergeStateStatus=BEHIND",
            "review decision CHANGES_REQUESTED",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_classify_block_unknown_merge_state_change_request_pending_stays_stale() -> None:
    r = _readiness(
        merge_state_status="",
        review_decision="CHANGES_REQUESTED",
        pending_checks=("ci/test",),
        failed_reasons=(
            "review decision CHANGES_REQUESTED",
            "pending checks: ci/test",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STALE_REVIEW
