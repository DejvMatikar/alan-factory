from __future__ import annotations

from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan import workflow_interrupt
from alan import workspace as workspace_mod
from alan._reconciler_orphans import (
    find_orphaned_interrupted_workspaces,
    reconcile_orphaned_workflows,
)
from alan.events import PubSub
from alan.park import ParkReason
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec, seed_pending_workflow
from alan.workspace import Status, Workspace, create


def _seed_interrupted_workspace(
    ws_root: Path, *, status: Status = Status.AWAITING_INPUT
) -> Workspace:
    ws = create("orphan-me", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-orphan")
    workflow_mod.save_workflow(ws, graph.doc)
    workflow_interrupt.interrupt_workflow_on_disk(ws, inv_id, error="x", resumable=True)
    return workspace_mod.transition(ws, status)


def test_find_orphaned_interrupted_workspaces_finds_awaiting_input(ws_root: Path) -> None:
    ws = _seed_interrupted_workspace(ws_root, status=Status.AWAITING_INPUT)
    found = find_orphaned_interrupted_workspaces(workspaces_root=ws_root)
    assert [w.task_id for w, _inv in found] == [ws.task_id]


def test_find_orphaned_interrupted_workspaces_finds_active(ws_root: Path) -> None:
    ws = _seed_interrupted_workspace(ws_root, status=Status.ACTIVE)
    found = find_orphaned_interrupted_workspaces(workspaces_root=ws_root)
    assert [w.task_id for w, _inv in found] == [ws.task_id]


def test_find_orphaned_interrupted_workspaces_skips_completed(ws_root: Path) -> None:
    _seed_interrupted_workspace(ws_root, status=Status.COMPLETED)
    assert find_orphaned_interrupted_workspaces(workspaces_root=ws_root) == []


def test_find_orphaned_interrupted_workspaces_skips_no_interrupted_node(ws_root: Path) -> None:
    ws = create("no-interrupt", "d", root=ws_root)
    seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    assert find_orphaned_interrupted_workspaces(workspaces_root=ws_root) == []


def test_find_orphaned_interrupted_workspaces_skips_no_invocations(ws_root: Path) -> None:
    ws = create("no-invocations", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    assert find_orphaned_interrupted_workspaces(workspaces_root=ws_root) == []


def test_find_orphaned_interrupted_workspaces_skips_unreadable_doc(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _seed_interrupted_workspace(ws_root)

    def _boom(*_a: object, **_k: object) -> object:
        raise workflow_mod.WorkflowError("corrupt")

    monkeypatch.setattr(workflow_mod, "load_workflow", _boom)
    del ws
    assert find_orphaned_interrupted_workspaces(workspaces_root=ws_root) == []


def test_reconcile_orphaned_workflows_parks_orphan_with_no_live_job(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws = _seed_interrupted_workspace(ws_root)
    events: list[TeamEvent] = []
    pubsub.subscribe(ws.workspace_id, events.append)
    parked = reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert parked == [ws.task_id]
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.CRASH
    assert len(reloaded.meta.blockers) == 1
    assert "restart" in reloaded.meta.blockers[0].detail.lower()
    assert any(e.kind == "workspace_interrupted" for e in events)


def test_reconcile_orphaned_workflows_skips_workspace_with_live_running_job(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws = _seed_interrupted_workspace(ws_root)
    queue.enqueue(ws.task_id, "verification", workspace_id=ws.workspace_id)
    queue.claim()
    parked = reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert parked == []
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_reconcile_orphaned_workflows_never_requeues(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    ws = _seed_interrupted_workspace(ws_root)
    reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert queue.list_jobs_for_workspace(ws.workspace_id) == []


def test_reconcile_orphaned_workflows_returns_empty_when_none_found(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    create("clean", "d", root=ws_root)
    assert reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub) == []


def test_reconcile_orphaned_workflows_still_parks_when_publish_raises(
    ws_root: Path, queue: Queue, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _seed_interrupted_workspace(ws_root)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("pubsub broken")

    monkeypatch.setattr(PubSub, "publish", _boom)
    parked = reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert parked == [ws.task_id]
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_reconcile_orphaned_workflows_skips_workspace_when_park_raises(
    ws_root: Path, queue: Queue, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _seed_interrupted_workspace(ws_root)

    def _boom(*_a: object, **_k: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_mod, "transition", _boom)
    parked = reconcile_orphaned_workflows(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert parked == []
    del ws
