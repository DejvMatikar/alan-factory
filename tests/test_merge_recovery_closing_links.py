from __future__ import annotations

from alan import merge_gate, merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_ready


def _readiness(**kwargs: object) -> _pr_ready.PrReadiness:
    base: dict[str, object] = {
        "ok": False,
        "merge_state_status": "CLEAN",
        "review_decision": "APPROVED",
        "check_count": 1,
        "passing_checks": ("ci/test",),
    }
    base.update(kwargs)
    return _pr_ready.PrReadiness(**base)  # type: ignore[arg-type]


def test_classify_block_closing_links_missing() -> None:
    readiness = _readiness(failed_reasons=("closing_links_missing #42",))
    block_class = merge_recovery.classify_block(readiness, (), DEFAULT_FORGE_TARGET)
    assert block_class is MergeBlockClass.CLOSING_LINKS_MISSING


def test_classify_block_dirty_wins_over_closing_links_missing() -> None:
    readiness = _readiness(
        merge_state_status="DIRTY",
        failed_reasons=("mergeStateStatus=DIRTY", "closing_links_missing #42"),
    )
    block_class = merge_recovery.classify_block(readiness, (), DEFAULT_FORGE_TARGET)
    assert block_class is MergeBlockClass.DIRTY


def test_classify_block_no_check_results_wins_over_closing_links_missing() -> None:
    readiness = _readiness(
        failed_reasons=("closing_links_missing #42",),
        check_count=0, passing_checks=(),
    )
    block_class = merge_recovery.classify_block(readiness, (), DEFAULT_FORGE_TARGET)
    assert block_class is MergeBlockClass.NO_CHECK_RESULTS


def test_attempt_mechanical_recovery_returns_unrecovered_for_closing_links_missing() -> None:
    readiness = _readiness(failed_reasons=("closing_links_missing #42",))
    recovered, block_class, rec = merge_recovery.attempt_mechanical_recovery(
        99991, "task-closing-links", readiness, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert recovered is False
    assert block_class is MergeBlockClass.CLOSING_LINKS_MISSING
    assert rec is None


def test_closing_links_missing_absent_from_ci_reset_delay_classes() -> None:
    assert MergeBlockClass.CLOSING_LINKS_MISSING not in merge_recovery.CI_RESET_DELAY_CLASSES


def test_closing_links_missing_absent_from_merge_lane_poll_classes() -> None:
    assert MergeBlockClass.CLOSING_LINKS_MISSING not in merge_gate.MERGE_LANE_POLL_CLASSES


def test_closing_links_missing_has_static_unblock_recipe() -> None:
    blocker = merge_recovery.unblock_instruction(
        MergeBlockClass.CLOSING_LINKS_MISSING, pr_number=42,
    )
    assert blocker is not None
    assert blocker.instruction is not None
    assert "Fixes #N" in blocker.instruction
