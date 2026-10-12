from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import alan.dispatcher as dispatcher_mod
from alan import merge_gate as merge_brief
from alan import workspace as workspace_mod
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _build_dispatcher_with_pr_merge,
    _pr_merge_handover,
    _read_merge_gate_audit,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_routes_to_merge_lane_membership() -> None:
    lane_members = {
        MergeBlockClass.STILL_COMPUTING,
        MergeBlockClass.BLOCKED_PENDING,
        MergeBlockClass.BLOCKED_NO_SIGNAL,
    }
    non_lane_members = {
        MergeBlockClass.GENUINE,
        MergeBlockClass.SONAR_HOTSPOT,
        MergeBlockClass.DIRTY,
        MergeBlockClass.PRE_EXISTING_RED,
        MergeBlockClass.STALE_REVIEW,
    }
    for cls in lane_members:
        assert merge_brief.routes_to_merge_lane(cls) is True
    for cls in non_lane_members:
        assert merge_brief.routes_to_merge_lane(cls) is False


def test_blocked_no_signal_is_excluded_from_bounded_retry_remediation_classes() -> None:
    assert (
        MergeBlockClass.BLOCKED_NO_SIGNAL
        not in dispatcher_mod._BOUNDED_RETRY_REMEDIATION_CLASSES  # noqa: SLF001
    )


def test_blocked_no_signal_routes_to_pr_merge_without_brief(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-blocked-no-signal-routing", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="",
            failed_reasons=("mergeStateStatus=BLOCKED",),
            check_count=1,
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" in enqueued_teams
    assert "implementation_fix" not in enqueued_teams
    assert recording.called == 0


def test_blocked_no_signal_audit_row_starts_its_own_bucket(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-blocked-no-signal-audit", "d", root=root)
    d, _recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="",
            failed_reasons=("mergeStateStatus=BLOCKED",),
            check_count=1,
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    rows = _read_merge_gate_audit(tmp_path)
    assert len(rows) == 1
    assert rows[0]["block_class"] == "blocked_no_signal"
    assert rows[0]["recovered"] is False
    assert rows[0]["outcome"] == "pr_merge"
    assert rows[0]["brief_attached"] is False


def test_blocked_no_signal_wins_over_genuine_readiness_fixture(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    ws = workspace_mod.create("mg-blocked-no-signal-pr-1147", "d", root=root)
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="",
            failing_checks=(),
            pending_checks=(),
            failed_reasons=("mergeStateStatus=BLOCKED",),
            check_count=1,
        ),
    )
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class != "genuine"
    assert all(j.team != "implementation_fix" for j in queue.list_jobs())
    assert recording.called == 0
