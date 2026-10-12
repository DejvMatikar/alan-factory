from __future__ import annotations

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_gate import MERGE_LANE_POLL_CLASSES
from alan.merge_recovery import MergeBlockClass, classify_block, unblock_instruction
from alan.teams._pr_freshness import MEASURED_BASELINE_STALE_REASON
from alan.teams._pr_ready import PrReadiness


def _readiness(
    *,
    ok: bool = False,
    merge_state_status: str = "CLEAN",
    review_decision: str = "APPROVED",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    passing_checks: tuple[str, ...] = (),
    failed_reasons: tuple[str, ...] = (),
    check_count: int = 0,
) -> PrReadiness:
    return PrReadiness(
        ok=ok,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        passing_checks=passing_checks,
        failed_reasons=failed_reasons,
        check_count=check_count,
    )


def test_classify_block_no_check_results_on_clean_empty_rollup() -> None:
    r = _readiness(failed_reasons=("no check results",))
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.NO_CHECK_RESULTS


def test_classify_block_dirty_outranks_no_check_results() -> None:
    r = _readiness(merge_state_status="DIRTY", failed_reasons=("no check results",))
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.DIRTY


def test_classify_block_stale_measured_baseline_outranks_no_check_results() -> None:
    r = _readiness(failed_reasons=(MEASURED_BASELINE_STALE_REASON, "no check results"))
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STALE_MEASURED_BASELINE


def test_unblock_instruction_is_a_transient_wait() -> None:
    blocker = unblock_instruction(MergeBlockClass.NO_CHECK_RESULTS, pr_number=42)
    assert blocker is not None
    assert blocker.kind == "reason"
    assert blocker.name == "unblock"
    assert blocker.instruction is not None
    assert "failed" not in blocker.instruction.lower()
    assert "escalate" not in blocker.instruction.lower()
    assert "re-dispatch pr_merge" in blocker.instruction


def test_no_check_results_absent_from_ci_reset_delay_classes() -> None:
    assert MergeBlockClass.NO_CHECK_RESULTS not in merge_recovery.CI_RESET_DELAY_CLASSES


def test_no_check_results_absent_from_merge_lane_poll_classes() -> None:
    assert MergeBlockClass.NO_CHECK_RESULTS not in MERGE_LANE_POLL_CLASSES


def test_attempt_mechanical_recovery_does_not_recover_no_check_results() -> None:
    r = _readiness(failed_reasons=("no check results",))
    recovered, block_class, rec = merge_recovery.attempt_mechanical_recovery(
        42, "task-x", r, (), forge_target=DEFAULT_FORGE_TARGET
    )
    assert recovered is False
    assert block_class is MergeBlockClass.NO_CHECK_RESULTS
    assert rec is None
