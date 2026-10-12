from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher, PubSub
from alan.lifecycle import ParkReason, Status
from alan.queue import Queue
from alan.teams._base import Team, TeamEvent
from alan.workspace import Workspace, create

_RESTORED_AS_SAVED = [
    Status.COMPLETED,
    Status.FAILED,
    Status.PARTIAL,
    Status.ESCALATED,
    Status.AWAITING_INPUT,
]


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    teams: dict[str, Team] = {}
    return Dispatcher(queue=queue, teams=teams, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, pubsub: PubSub, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _archive(client: TestClient, ws: Workspace) -> None:
    assert client.post(f"/api/workspaces/{ws.task_id}/archive").status_code == 204


def _unarchive(client: TestClient, ws: Workspace) -> None:
    assert client.post(f"/api/workspaces/{ws.task_id}/unarchive").status_code == 204


def _archived_workspace(client: TestClient, ws_root: Path, status: Status) -> Workspace:
    ws = create("Restore Me", "d", root=ws_root)
    workspace_mod.transition(ws, status)
    _archive(client, ws)
    return ws


def _forget_saved_status(ws: Workspace) -> None:
    meta_path = ws.path / "meta.json"
    data = json.loads(meta_path.read_text())
    data.pop("pre_archive_status", None)
    meta_path.write_text(json.dumps(data))


def _status_of(ws_root: Path, ws: Workspace) -> Status:
    return workspace_mod.read(ws.task_id, root=ws_root).meta.status


@pytest.mark.parametrize("saved", _RESTORED_AS_SAVED)
def test_unarchive_restores_the_status_the_workspace_had_when_archived(
    client: TestClient, ws_root: Path, queue: Queue, saved: Status
) -> None:
    ws = _archived_workspace(client, ws_root, saved)
    _unarchive(client, ws)
    assert _status_of(ws_root, ws) is saved
    assert client.get(f"/api/workspaces/{ws.task_id}").json()["status"] == saved.value
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_unarchive_of_a_workspace_archived_while_active_needs_the_operator(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _archived_workspace(client, ws_root, Status.ACTIVE)
    _unarchive(client, ws)
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.UNARCHIVED
    assert client.get(f"/api/workspaces/{ws.task_id}").json()["park_reason"] == "unarchived"
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_unarchive_of_a_workspace_archived_before_the_status_was_saved_needs_the_operator(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _archived_workspace(client, ws_root, Status.COMPLETED)
    _forget_saved_status(ws)
    _unarchive(client, ws)
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.UNARCHIVED
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_unarchive_with_a_pause_marker_restores_paused_and_resume_works(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Marked Paused", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_pending(job.id)
    queue.pause_task(ws.task_id)
    _archive(client, ws)
    _unarchive(client, ws)
    assert _status_of(ws_root, ws) is Status.PAUSED
    assert client.post(f"/api/workspaces/{ws.task_id}/resume").status_code == 202
    assert _status_of(ws_root, ws) is Status.ACTIVE
    assert queue.is_task_paused(ws.task_id) is False


def test_unarchive_of_a_workspace_archived_while_paused_restores_paused_and_resume_works(
    client: TestClient, ws_root: Path
) -> None:
    ws = _archived_workspace(client, ws_root, Status.PAUSED)
    _unarchive(client, ws)
    assert _status_of(ws_root, ws) is Status.PAUSED
    assert client.post(f"/api/workspaces/{ws.task_id}/resume").status_code == 202
    assert _status_of(ws_root, ws) is Status.ACTIVE


def test_unarchive_publishes_workspace_unarchived_once(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = _archived_workspace(client, ws_root, Status.COMPLETED)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    _unarchive(client, ws)
    unarchived = [e for e in received if e.kind == "workspace_unarchived"]
    assert [e.payload for e in unarchived] == [{"task_id": ws.task_id}]


def test_unarchive_of_an_active_workspace_is_a_silent_no_op(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("Already Active", "d", root=ws_root)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    _unarchive(client, ws)
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert reloaded.meta.park_reason is None
    assert received == []


@pytest.mark.parametrize("status", [Status.COMPLETED, Status.FAILED])
def test_unarchive_refuses_a_workspace_that_is_not_archived(
    client: TestClient, ws_root: Path, pubsub: PubSub, status: Status
) -> None:
    ws = create("Not Archived", "d", root=ws_root)
    workspace_mod.transition(ws, status)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 409
    assert res.json()["detail"] == "workspace is not archived"
    assert _status_of(ws_root, ws) is status
    assert received == []


def test_unarchive_returns_404_for_a_missing_workspace(client: TestClient) -> None:
    assert client.post("/api/workspaces/ghost/unarchive").status_code == 404


@pytest.mark.parametrize("restore", [False, True])
def test_workspace_detail_never_exposes_the_saved_status(
    client: TestClient, ws_root: Path, restore: bool
) -> None:
    ws = _archived_workspace(client, ws_root, Status.COMPLETED)
    if restore:
        _unarchive(client, ws)
    assert "pre_archive_status" not in client.get(f"/api/workspaces/{ws.task_id}").json()
