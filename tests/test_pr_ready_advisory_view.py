from __future__ import annotations

from pathlib import Path

import pytest

from alan.teams import _pr_ready
from tests import _advisory_check_runs_support as support


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _sonar_is_third_party() -> None:
    support.serve_check_runs([
        support.check_run(support.SONAR, support.SONAR_APP),
        support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])


def _merge_view(readiness: _pr_ready.PrReadiness) -> _pr_ready.PrReadiness:
    return _pr_ready.merge_lane_without_advisory(
        readiness, 42, support.TASK_ID, lane="merge_gate", forge_target=support.FORGE,
    )


def _verifier_view(readiness: _pr_ready.ReadinessResult) -> _pr_ready.ReadinessResult:
    return _pr_ready.verifier_lane_without_advisory(
        readiness, 42, support.TASK_ID, forge_target=support.FORGE,
    )


def _sonar_only() -> list[object]:
    return [support.failing_run(support.SONAR), support.passing_run("ci")]


def test_merge_lane_reads_advisory_only_red_unstable_pr_as_green(audit_root: Path) -> None:
    original = support.merge_readiness(_sonar_only())
    assert original.failed_reasons == (
        "mergeStateStatus=UNSTABLE", f"failing checks: {support.SONAR}",
    )
    view = _merge_view(original)
    assert view.ok is True
    assert view.merge_state_status == "CLEAN"
    assert view.failing_checks == ()
    assert view.failed_reasons == ()
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "merge_gate"
    assert row["task_id"] == support.TASK_ID
    assert row["pr_number"] == 42
    assert row["head_sha"] == support.HEAD_SHA
    assert row["ignored_checks"] == [support.SONAR]


def test_merge_lane_view_matches_the_same_pr_with_the_check_green() -> None:
    green = support.merge_readiness(
        [support.passing_run(support.SONAR), support.passing_run("ci")], merge_state="CLEAN",
    )
    view = _merge_view(support.merge_readiness(_sonar_only()))
    assert (view.ok, view.merge_state_status, view.failed_reasons, view.failing_checks) == (
        green.ok, green.merge_state_status, green.failed_reasons, green.failing_checks,
    )


def test_merge_lane_keeps_blocked_merge_state() -> None:
    view = _merge_view(support.merge_readiness(_sonar_only(), merge_state="BLOCKED"))
    assert view.ok is False
    assert view.failed_reasons == ("mergeStateStatus=BLOCKED",)


def test_merge_lane_keeps_dirty_merge_state() -> None:
    view = _merge_view(support.merge_readiness(_sonar_only(), merge_state="DIRTY"))
    assert view.failed_reasons == ("mergeStateStatus=DIRTY",)


def test_merge_lane_keeps_unstable_while_a_check_is_pending() -> None:
    rollup = [*_sonar_only(), support.pending_run("slow")]
    view = _merge_view(support.merge_readiness(rollup))
    assert view.merge_state_status == "UNSTABLE"
    assert view.pending_checks == ("slow",)
    assert view.failed_reasons == ("mergeStateStatus=UNSTABLE", "pending checks: slow")


def test_merge_lane_keeps_the_changes_requested_reason() -> None:
    view = _merge_view(support.merge_readiness(_sonar_only(), review_decision="CHANGES_REQUESTED"))
    assert view.failed_reasons == ("review decision CHANGES_REQUESTED",)


def test_merge_lane_keeps_a_quarantined_red_and_the_unstable_state() -> None:
    rollup = [*_sonar_only(), support.failing_run(support.BACKEND)]
    original = support.merge_readiness(rollup, quarantined_checks=(support.BACKEND,))
    view = _merge_view(original)
    assert view.failing_checks == (support.BACKEND,)
    assert view.quarantined_checks == (support.BACKEND,)
    assert view.merge_state_status == "UNSTABLE"
    assert view.failed_reasons == (
        "mergeStateStatus=UNSTABLE", f"failing checks: {support.BACKEND}",
    )


def test_merge_lane_drops_a_quarantined_advisory_check_from_the_quarantine() -> None:
    original = support.merge_readiness(_sonar_only(), quarantined_checks=(support.SONAR,))
    view = _merge_view(original)
    assert view.quarantined_checks == ()
    assert view.ok is True


def test_merge_lane_returns_the_same_readiness_when_a_blocking_red_is_present(
    audit_root: Path,
) -> None:
    original = support.merge_readiness([
        support.failing_run(support.SONAR), support.failing_run(support.BACKEND),
    ])
    assert _merge_view(original) is original
    assert support.impl_audit_rows(audit_root, "advisory_check_failures_ignored") == []


def test_merge_lane_returns_the_same_readiness_when_nothing_is_red(audit_root: Path) -> None:
    calls = support.serve_check_runs([])
    original = support.merge_readiness([support.passing_run("ci")], merge_state="CLEAN")
    assert _merge_view(original) is original
    assert calls == []
    assert support.impl_audit_rows(audit_root, "advisory_check_failures_ignored") == []


def test_verifier_lane_reads_advisory_only_red_as_green(audit_root: Path) -> None:
    original = support.verifier_readiness(_sonar_only())
    assert original.failed_reasons == (f"failing checks: {support.SONAR}",)
    view = _verifier_view(original)
    assert isinstance(view, _pr_ready.PrReadiness)
    assert view.ok is True
    assert view.failed_reasons == ()
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "verification"
    assert row["ignored_checks"] == [support.SONAR]


def test_verifier_lane_keeps_the_dirty_reason() -> None:
    view = _verifier_view(support.verifier_readiness(_sonar_only(), merge_state="DIRTY"))
    assert isinstance(view, _pr_ready.PrReadiness)
    assert view.failed_reasons == ("mergeStateStatus=DIRTY",)
    assert view.ok is False


def test_verifier_lane_ignores_quarantined_reds_when_rebuilding_reasons() -> None:
    rollup = [*_sonar_only(), support.failing_run(support.BACKEND)]
    original = support.verifier_readiness(rollup, quarantined_checks=(support.BACKEND,))
    view = _verifier_view(original)
    assert isinstance(view, _pr_ready.PrReadiness)
    assert view.failed_reasons == ()
    assert view.quarantined_checks == (support.BACKEND,)


def test_verifier_lane_returns_the_same_readiness_when_a_blocking_red_is_present() -> None:
    original = support.verifier_readiness([
        support.failing_run(support.SONAR), support.failing_run(support.BACKEND),
    ])
    assert _verifier_view(original) is original


def test_verifier_lane_passes_an_unavailable_readiness_through() -> None:
    unavailable = _pr_ready.ReadinessUnavailable("gh unavailable")
    assert _verifier_view(unavailable) is unavailable


def test_audit_row_for_the_merge_team_lane_names_the_ignored_checks(audit_root: Path) -> None:
    _pr_ready.audit_advisory_ignored(
        "pr_merge", support.TASK_ID, 42, support.merge_readiness(_sonar_only()), (support.SONAR,),
    )
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "pr_merge"
    assert row["task_id"] == support.TASK_ID
    assert row["pr_number"] == 42
    assert row["head_sha"] == support.HEAD_SHA
    assert row["ignored_checks"] == [support.SONAR]
