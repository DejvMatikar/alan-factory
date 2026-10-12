from __future__ import annotations

from pathlib import Path

import pytest

from alan import merge_gate, merge_recovery
from alan import workspace as workspace_mod
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_freshness, _pr_ready
from tests import _advisory_check_runs_support as support

_SONAR_LISTING = [support.check_run(support.SONAR, support.SONAR_APP)]


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _baseline_is_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_gate, "stale_measured_baseline_forces_check", lambda *_a: False)


def _spy_recovery(
    monkeypatch: pytest.MonkeyPatch, block_class: MergeBlockClass = MergeBlockClass.GENUINE,
) -> list[_pr_ready.PrReadiness]:
    seen: list[_pr_ready.PrReadiness] = []

    def fake(
        _pr: int, _task: str, readiness: _pr_ready.PrReadiness, *_a: object, **_kw: object,
    ) -> tuple[bool, MergeBlockClass, None]:
        seen.append(readiness)
        return (False, block_class, None)

    monkeypatch.setattr(merge_recovery, "attempt_mechanical_recovery", fake)
    return seen


def _sonar_rollup() -> list[object]:
    return [support.failing_run(support.SONAR), support.passing_run("ci")]


def _attempt(tmp_path: Path) -> merge_gate.MergeGateAttempt:
    ws = workspace_mod.create("merge-gate-advisory", "d", root=tmp_path / "ws")
    return merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")


def test_advisory_only_red_unstable_pr_is_not_attempted_and_reads_as_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload(_sonar_rollup()))
    seen = _spy_recovery(monkeypatch)
    attempt = _attempt(tmp_path)
    assert isinstance(attempt, merge_gate.MergeGateNotAttempted)
    assert isinstance(attempt.readiness, _pr_ready.PrReadiness)
    assert attempt.readiness.ok is True
    assert attempt.readiness.merge_state_status == "CLEAN"
    assert seen == []
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "merge_gate"
    assert row["ignored_checks"] == [support.SONAR]


def test_same_pr_with_the_check_green_is_not_attempted_either(tmp_path: Path) -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload(
        [support.passing_run(support.SONAR), support.passing_run("ci")], merge_state="CLEAN",
    ))
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateNotAttempted)
    assert shas == []


def test_advisory_only_red_blocked_pr_reaches_recovery_without_the_red_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload(_sonar_rollup(), merge_state="BLOCKED"))
    seen = _spy_recovery(monkeypatch, MergeBlockClass.BLOCKED_NO_SIGNAL)
    attempt = _attempt(tmp_path)
    assert isinstance(attempt, merge_gate.MergeGateBlocked)
    [readiness] = seen
    assert readiness.failing_checks == ()
    assert readiness.failed_reasons == ("mergeStateStatus=BLOCKED",)
    assert merge_recovery.classify_block(
        readiness, (), support.FORGE
    ) is MergeBlockClass.BLOCKED_NO_SIGNAL


def test_dirty_pr_with_only_advisory_red_still_reaches_the_dirty_rung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload(_sonar_rollup(), merge_state="DIRTY"))
    seen = _spy_recovery(monkeypatch, MergeBlockClass.DIRTY)
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateBlocked)
    [readiness] = seen
    assert readiness.failed_reasons == ("mergeStateStatus=DIRTY",)
    assert merge_recovery.classify_block(readiness, (), support.FORGE) is MergeBlockClass.DIRTY


def test_blocking_red_next_to_advisory_red_reaches_recovery_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    rollup = [support.failing_run(support.SONAR), support.failing_run(support.BACKEND)]
    support.serve_pr_reads(support.pr_payload(rollup))
    seen = _spy_recovery(monkeypatch)
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateBlocked)
    assert seen == [support.merge_readiness(rollup)]
    assert support.impl_audit_rows(audit_root, "advisory_check_failures_ignored") == []


def test_red_actions_check_still_stops_the_merge_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs([support.check_run(support.DRIFT_GATE, support.ACTIONS_APP)])
    support.serve_pr_reads(support.pr_payload([support.failing_run(support.DRIFT_GATE)]))
    _spy_recovery(monkeypatch)
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateBlocked)


def test_unreadable_listing_leaves_the_advisory_red_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs(None)
    support.serve_pr_reads(support.pr_payload(_sonar_rollup()))
    seen = _spy_recovery(monkeypatch)
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateBlocked)
    assert seen == [support.merge_readiness(_sonar_rollup())]


def test_advisory_view_is_applied_before_the_freshness_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload(_sonar_rollup()))
    monkeypatch.setattr(merge_gate, "stale_measured_baseline_forces_check", lambda *_a: True)
    seen = _spy_recovery(monkeypatch, MergeBlockClass.STALE_MEASURED_BASELINE)
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateBlocked)
    [readiness] = seen
    assert readiness.failing_checks == ()
    assert readiness.failed_reasons == (_pr_freshness.MEASURED_BASELINE_STALE_REASON,)


def test_readiness_without_any_red_check_reads_no_listing(tmp_path: Path) -> None:
    shas = support.serve_check_runs([])
    support.serve_pr_reads(support.pr_payload([support.passing_run("ci")], merge_state="CLEAN"))
    assert isinstance(_attempt(tmp_path), merge_gate.MergeGateNotAttempted)
    assert shas == []
