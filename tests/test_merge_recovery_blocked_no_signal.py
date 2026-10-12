from __future__ import annotations

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams._pr_ready import PrReadiness, is_blocked_without_signal


def _readiness(
    *,
    merge_state_status: str = "BLOCKED",
    review_decision: str = "APPROVED",
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


def test_blocked_no_signal_classifies_bare_blocked_readiness() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="",
        failed_reasons=("mergeStateStatus=BLOCKED",),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.BLOCKED_NO_SIGNAL


def test_blocked_no_signal_classifies_bare_blocked_readiness_on_recurrence() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="",
        failed_reasons=("mergeStateStatus=BLOCKED",),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.BLOCKED_NO_SIGNAL


def test_classify_block_blocked_changes_requested_is_stale_review_not_blocked_no_signal() -> None:
    r = _readiness(
        review_decision="CHANGES_REQUESTED",
        failed_reasons=(
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STALE_REVIEW


def test_blocked_no_signal_declines_when_pending_checks_present() -> None:
    r = _readiness(
        pending_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "pending checks: ci/test"),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.BLOCKED_PENDING


def test_blocked_no_signal_declines_when_a_check_is_failing() -> None:
    r = _readiness(
        failing_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: ci/test"),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_is_blocked_without_signal_false_when_clean() -> None:
    r = _readiness(merge_state_status="CLEAN", failed_reasons=())
    assert is_blocked_without_signal(r) is False


def test_is_blocked_without_signal_false_when_behind() -> None:
    r = _readiness(
        merge_state_status="BEHIND", failed_reasons=("mergeStateStatus=BEHIND",)
    )
    assert is_blocked_without_signal(r) is False


def test_is_blocked_without_signal_true_for_bare_blocked_shape() -> None:
    r = _readiness(
        merge_state_status="BLOCKED", failed_reasons=("mergeStateStatus=BLOCKED",)
    )
    assert is_blocked_without_signal(r) is True


def test_attempt_mechanical_recovery_blocked_no_signal_no_recovery() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        review_decision="",
        failed_reasons=("mergeStateStatus=BLOCKED",),
    )
    ok, cls, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, forge_target=DEFAULT_FORGE_TARGET
    )
    assert ok is False
    assert cls is MergeBlockClass.BLOCKED_NO_SIGNAL
    assert rec is None
