from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import fleet_timing as fleet_timing_mod
from alan.fleet_timing import ActiveFleetTime
from alan.workflow import (
    WorkflowDoc,
    WorkflowRowSummary,
    WorkflowTask,
    create_invocation_dir,
    save_workflow,
)
from alan.workspace import Workspace, create

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _summary(*intervals: tuple[datetime, datetime | None]) -> WorkflowRowSummary:
    return WorkflowRowSummary(team="t", status="success", cost=None, active_intervals=intervals)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path, title: str = "t1") -> Workspace:
    return create(title, "desc", root=ws_root)


def test_live_seconds_none_when_no_data() -> None:
    assert ActiveFleetTime(closed_seconds=None, open_since=None).live_seconds(T0) is None


def test_live_seconds_closed_only() -> None:
    assert ActiveFleetTime(closed_seconds=120.0, open_since=None).live_seconds(T0) == 120.0


def test_live_seconds_open_only_adds_elapsed() -> None:
    active = ActiveFleetTime(closed_seconds=None, open_since=T0 - timedelta(seconds=30))
    assert active.live_seconds(T0) == pytest.approx(30.0)


def test_live_seconds_combines_closed_and_open() -> None:
    active = ActiveFleetTime(closed_seconds=100.0, open_since=T0 - timedelta(seconds=10))
    assert active.live_seconds(T0) == pytest.approx(110.0)


def test_live_seconds_never_negative_when_now_before_open_since() -> None:
    active = ActiveFleetTime(closed_seconds=None, open_since=T0 + timedelta(seconds=5))
    assert active.live_seconds(T0) == pytest.approx(0.0)


def test_no_intervals_is_empty_state() -> None:
    result = fleet_timing_mod.active_fleet_time_for_summaries([])
    assert result.closed_seconds is None
    assert result.open_since is None


def test_single_closed_interval() -> None:
    summary = _summary((T0, T0 + timedelta(seconds=60)))
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds == pytest.approx(60.0)
    assert result.open_since is None


def test_disjoint_closed_intervals_sum() -> None:
    summary = _summary(
        (T0, T0 + timedelta(seconds=10)),
        (T0 + timedelta(seconds=20), T0 + timedelta(seconds=25)),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds == pytest.approx(15.0)


def test_overlapping_closed_intervals_are_unioned_not_summed() -> None:
    summary = _summary(
        (T0, T0 + timedelta(seconds=30)),
        (T0 + timedelta(seconds=10), T0 + timedelta(seconds=40)),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds == pytest.approx(40.0)


def test_touching_closed_intervals_merge_without_gap() -> None:
    summary = _summary(
        (T0, T0 + timedelta(seconds=10)),
        (T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds == pytest.approx(20.0)


def test_intervals_across_multiple_invocations_are_unioned() -> None:
    s1 = _summary((T0, T0 + timedelta(seconds=10)))
    s2 = _summary((T0 + timedelta(seconds=5), T0 + timedelta(seconds=20)))
    result = fleet_timing_mod.active_fleet_time_for_summaries([s1, s2])
    assert result.closed_seconds == pytest.approx(20.0)


def test_single_open_interval() -> None:
    summary = _summary((T0, None))
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds is None
    assert result.open_since == T0


def test_open_interval_absorbs_overlapping_closed_interval() -> None:
    summary = _summary(
        (T0, T0 + timedelta(seconds=10)),
        (T0 + timedelta(seconds=5), None),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds is None
    assert result.open_since == T0


def test_disjoint_closed_block_before_open_block_keeps_both() -> None:
    summary = _summary(
        (T0, T0 + timedelta(seconds=10)),
        (T0 + timedelta(seconds=50), None),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds == pytest.approx(10.0)
    assert result.open_since == T0 + timedelta(seconds=50)


def test_multiple_open_intervals_reduce_to_earliest_start() -> None:
    s1 = _summary((T0 + timedelta(seconds=10), None))
    s2 = _summary((T0, None))
    result = fleet_timing_mod.active_fleet_time_for_summaries([s1, s2])
    assert result.closed_seconds is None
    assert result.open_since == T0


def test_open_interval_absorbs_later_starting_closed_interval() -> None:
    summary = _summary(
        (T0, None),
        (T0 + timedelta(seconds=5), T0 + timedelta(seconds=8)),
    )
    result = fleet_timing_mod.active_fleet_time_for_summaries([summary])
    assert result.closed_seconds is None
    assert result.open_since == T0


def test_active_fleet_time_for_workspace_no_invocations_is_empty(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.closed_seconds is None
    assert result.open_since is None


def test_active_fleet_time_for_workspace_excludes_sentinel_nodes(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = T0
    completed = T0 + timedelta(seconds=45)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="deploy_verify",
        status="success",
        started_at=started,
        completed_at=completed,
        tasks=[
            WorkflowTask(
                id="deploy_verify",
                role="deploy_verify",
                model="none",
                status="success",
                started_at=started,
                completed_at=completed,
            )
        ],
    )
    save_workflow(ws, doc)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.closed_seconds is None
    assert result.open_since is None


def test_active_fleet_time_for_workspace_clamps_open_node_on_terminal_doc(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = T0
    doc_completed = T0 + timedelta(seconds=90)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="failed",
        started_at=started,
        completed_at=doc_completed,
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="failed",
                started_at=started,
                completed_at=None,
            )
        ],
    )
    save_workflow(ws, doc)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.open_since is None
    assert result.closed_seconds == pytest.approx(90.0)


def test_active_fleet_time_for_workspace_leaves_node_open_on_non_terminal_doc(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    started = T0
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="running",
        started_at=started,
        completed_at=None,
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="running",
                started_at=started,
                completed_at=None,
            )
        ],
    )
    save_workflow(ws, doc)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.closed_seconds is None
    assert result.open_since == started


def test_active_fleet_time_for_workspace_unions_across_invocations(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv1 = create_invocation_dir(ws)
    doc1 = WorkflowDoc(
        id=inv1,
        namespace=f"alan.workspace.{ws.task_id}",
        team="research",
        status="success",
        started_at=T0,
        completed_at=T0 + timedelta(seconds=20),
        tasks=[
            WorkflowTask(
                id="researcher",
                role="researcher",
                model="opus",
                status="success",
                started_at=T0,
                completed_at=T0 + timedelta(seconds=20),
            )
        ],
    )
    save_workflow(ws, doc1)
    inv2 = create_invocation_dir(ws)
    doc2 = WorkflowDoc(
        id=inv2,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="success",
        started_at=T0 + timedelta(seconds=30),
        completed_at=T0 + timedelta(seconds=50),
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="success",
                started_at=T0 + timedelta(seconds=30),
                completed_at=T0 + timedelta(seconds=50),
            )
        ],
    )
    save_workflow(ws, doc2)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.closed_seconds == pytest.approx(40.0)


def test_active_fleet_time_for_workspace_node_never_started_is_excluded(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="pending",
        started_at=T0,
        completed_at=None,
        tasks=[
            WorkflowTask(id="worker", role="worker", model="sonnet", status="pending")
        ],
    )
    save_workflow(ws, doc)
    result = fleet_timing_mod.active_fleet_time_for_workspace(ws.path)
    assert result.closed_seconds is None
    assert result.open_since is None


def test_lead_seconds_null_when_not_delivered() -> None:
    assert fleet_timing_mod.lead_seconds(created_at=T0, delivered_at=None) is None


def test_lead_seconds_computes_delta() -> None:
    delivered = T0 + timedelta(hours=3)
    assert fleet_timing_mod.lead_seconds(
        created_at=T0, delivered_at=delivered
    ) == pytest.approx(3 * 3600)
