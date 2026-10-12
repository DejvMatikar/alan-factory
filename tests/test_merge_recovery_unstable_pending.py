from __future__ import annotations

from alan import merge_gate as merge_brief
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.merge_recovery import MergeBlockClass, classify_block
from alan.teams._pr_ready import PrReadiness

_FLAKY_GATE = "Frontend E2E (Playwright, blocking)"


def _readiness(
    *,
    merge_state_status: str = "UNSTABLE",
    review_decision: str = "APPROVED",
    failing_checks: tuple[str, ...] = (),
    pending_checks: tuple[str, ...] = (),
    failed_reasons: tuple[str, ...] = (),
    quarantined_checks: tuple[str, ...] = (),
    check_count: int = 1,
) -> PrReadiness:
    return PrReadiness(
        ok=False,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing_checks,
        pending_checks=pending_checks,
        failed_reasons=failed_reasons,
        quarantined_checks=quarantined_checks,
        check_count=check_count,
    )


def test_unstable_pending_only_classifies_as_still_computing() -> None:
    r = _readiness(
        pending_checks=("ci/advisory",),
        failed_reasons=("mergeStateStatus=UNSTABLE", "pending checks: ci/advisory"),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STILL_COMPUTING


def test_unstable_pending_with_genuine_failing_check_classifies_as_genuine() -> None:
    r = _readiness(
        failing_checks=("Ruff",),
        pending_checks=("ci/advisory",),
        failed_reasons=(
            "mergeStateStatus=UNSTABLE",
            "failing checks: Ruff",
            "pending checks: ci/advisory",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_unstable_pending_with_quarantined_failing_check_classifies_as_pre_existing_red() -> None:
    r = _readiness(
        failing_checks=("ci/tests",),
        pending_checks=("ci/advisory",),
        quarantined_checks=("ci/tests",),
        failed_reasons=(
            "mergeStateStatus=UNSTABLE",
            "failing checks: ci/tests",
            "pending checks: ci/advisory",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.PRE_EXISTING_RED


def test_unstable_pending_with_allowlisted_failing_check_classifies_as_transient_flake() -> None:
    r = _readiness(
        failing_checks=(_FLAKY_GATE,),
        pending_checks=("ci/advisory",),
        failed_reasons=(
            "mergeStateStatus=UNSTABLE",
            f"failing checks: {_FLAKY_GATE}",
            "pending checks: ci/advisory",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.TRANSIENT_FLAKE


def test_unstable_pending_with_changes_requested_does_not_classify_as_still_computing() -> None:
    r = _readiness(
        review_decision="CHANGES_REQUESTED",
        pending_checks=("ci/advisory",),
        failed_reasons=(
            "mergeStateStatus=UNSTABLE",
            "review decision CHANGES_REQUESTED",
            "pending checks: ci/advisory",
        ),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_unstable_with_no_pending_checks_does_not_classify_as_still_computing() -> None:
    r = _readiness(failed_reasons=("mergeStateStatus=UNSTABLE",))
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.GENUINE


def test_clean_pending_only_still_classifies_as_still_computing() -> None:
    r = _readiness(
        merge_state_status="CLEAN",
        pending_checks=("ci/test",),
        failed_reasons=("pending checks: ci/test",),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.STILL_COMPUTING


def test_blocked_pending_only_still_classifies_as_blocked_pending() -> None:
    r = _readiness(
        merge_state_status="BLOCKED",
        pending_checks=("ci/test",),
        failed_reasons=("mergeStateStatus=BLOCKED", "pending checks: ci/test"),
    )
    assert classify_block(r, (), DEFAULT_FORGE_TARGET) is MergeBlockClass.BLOCKED_PENDING


def test_unstable_pending_only_routes_to_merge_lane() -> None:
    r = _readiness(
        pending_checks=("ci/advisory",),
        failed_reasons=("mergeStateStatus=UNSTABLE", "pending checks: ci/advisory"),
    )
    assert merge_brief.routes_to_merge_lane(classify_block(r, (), DEFAULT_FORGE_TARGET)) is True
