from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan._dispatcher_base as dispatcher_mod
import alan.workspace_dag as workspace_dag
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.park import Blocker, ParkReason
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams import TEAMS as REAL_TEAMS
from alan.teams._base import Team
from alan.workspace import Status, Task, TaskStatus, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def _handover(task_id: str, from_team: str = "research") -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_test",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


def _escalated_with_park_state(root: Path) -> Workspace:
    ws = workspace_mod.create("park-rollback", "d", root=root)
    blockers = [Blocker(kind="reason", name="escalation", detail="original cause")]
    return workspace_mod.transition(
        ws, Status.ESCALATED, park_reason=ParkReason.ESCALATED, blockers=blockers
    )


def test_stamp_for_advance_non_escalated_workspace_returns_no_rollback(root: Path) -> None:
    ws = workspace_mod.create("not-escalated", "d", root=root)
    stamped, rollback = workspace_dag.stamp_for_advance(ws, None)
    assert rollback is None
    assert stamped.meta.status is Status.ACTIVE


def test_stamp_for_advance_non_escalated_without_block_class_writes_nothing(
    root: Path,
) -> None:
    ws = workspace_mod.create("no-write", "d", root=root)
    before = (ws.path / "meta.json").read_bytes()
    stamped, _ = workspace_dag.stamp_for_advance(ws, None)
    assert stamped is ws
    assert (ws.path / "meta.json").read_bytes() == before


def test_stamp_for_advance_block_class_restamps_status_with_merge_block_class(
    root: Path,
) -> None:
    ws = workspace_mod.transition(
        workspace_mod.create("blocked", "d", root=root), Status.AWAITING_INPUT
    )
    stamped, rollback = workspace_dag.stamp_for_advance(ws, "stale_review")
    assert rollback is None
    assert stamped.meta.status is Status.AWAITING_INPUT
    assert stamped.meta.merge_block_class == "stale_review"
    assert workspace_mod.read(ws.task_id, root=root).meta.merge_block_class == "stale_review"


def test_stamp_for_advance_escalated_with_awaiting_subtask_defers_stamp(root: Path) -> None:
    ws = _escalated_with_park_state(root)
    task = Task(id="sub", status=TaskStatus.AWAITING_INPUT)
    ws = Workspace(
        task_id=ws.task_id, path=ws.path, meta=ws.meta.model_copy(update={"tasks": [task]})
    )
    stamped, rollback = workspace_dag.stamp_for_advance(ws, None)
    assert stamped.meta.status is Status.ESCALATED
    assert rollback is not None
    assert rollback.park_reason is ParkReason.ESCALATED
    assert len(rollback.blockers) == 1


def test_stamp_for_advance_escalated_with_awaiting_subtask_defers_even_with_block_class(
    root: Path,
) -> None:
    ws = _escalated_with_park_state(root)
    task = Task(id="sub", status=TaskStatus.AWAITING_INPUT)
    ws = Workspace(
        task_id=ws.task_id, path=ws.path, meta=ws.meta.model_copy(update={"tasks": [task]})
    )
    stamped, rollback = workspace_dag.stamp_for_advance(ws, "dirty")
    assert stamped.meta.status is Status.ESCALATED
    assert stamped.meta.merge_block_class is None
    assert rollback is not None


def test_stamp_for_advance_escalated_stamps_active_and_captures_rollback(root: Path) -> None:
    ws = _escalated_with_park_state(root)
    stamped, rollback = workspace_dag.stamp_for_advance(ws, None)
    assert stamped.meta.status is Status.ACTIVE
    assert stamped.meta.park_reason is None
    assert stamped.meta.blockers == []
    assert rollback is not None
    assert rollback.park_reason is ParkReason.ESCALATED
    assert len(rollback.blockers) == 1
    assert rollback.blockers[0].detail == "original cause"


def test_stamp_for_advance_escalated_carries_block_class_onto_active(root: Path) -> None:
    ws = _escalated_with_park_state(root)
    stamped, rollback = workspace_dag.stamp_for_advance(ws, "dirty")
    assert stamped.meta.status is Status.ACTIVE
    assert stamped.meta.merge_block_class == "dirty"
    assert rollback is not None


def test_restore_escalated_restores_captured_park_state(root: Path) -> None:
    ws = _escalated_with_park_state(root)
    stamped, rollback = workspace_dag.stamp_for_advance(ws, None)
    assert rollback is not None
    workspace_dag.restore_escalated(stamped, rollback)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ESCALATED
    assert reloaded.meta.park_reason is ParkReason.ESCALATED
    assert len(reloaded.meta.blockers) == 1
    assert reloaded.meta.blockers[0].detail == "original cause"


def test_handle_decision_enqueue_failure_from_escalated_preserves_park_state(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _escalated_with_park_state(root)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(dispatcher_mod, "enqueue_with_pending_workflow", boom)
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    with pytest.raises(RuntimeError, match="enqueue failed"):
        d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ESCALATED
    assert reloaded.meta.park_reason is ParkReason.ESCALATED
    assert len(reloaded.meta.blockers) == 1
    assert reloaded.meta.blockers[0].detail == "original cause"


def test_handle_decision_done_failure_from_escalated_preserves_park_state(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _escalated_with_park_state(root)
    d = Dispatcher(queue, {}, workspaces_root=root)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("dag error")

    monkeypatch.setattr(d, "_advance_workspace_dag", boom)
    handover = _handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    with pytest.raises(RuntimeError, match="dag error"):
        d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ESCALATED
    assert reloaded.meta.park_reason is ParkReason.ESCALATED
    assert len(reloaded.meta.blockers) == 1


def test_handle_decision_success_from_escalated_clears_park_state(
    queue: Queue, root: Path
) -> None:
    ws = _escalated_with_park_state(root)
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ACTIVE
    assert reloaded.meta.park_reason is None
    assert reloaded.meta.blockers == []
