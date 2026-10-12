from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workspace as workspace_mod
from alan._dispatcher_retire import WORKSPACE_DELETE_BLOCKED_DETAIL
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, pubsub: PubSub, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: dispatcher
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_delete_workspace_with_dispatcher_succeeds_when_idle(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("Idle", "d", root=ws_root)
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert not workspace_mod.exists(ws.task_id, root=ws_root)


def test_delete_workspace_blocked_uses_dedicated_detail_message(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Active", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 409
    assert res.json()["detail"] == WORKSPACE_DELETE_BLOCKED_DETAIL
    assert workspace_mod.exists(ws.task_id, root=ws_root)


def test_delete_workspace_settles_a_job_that_completes_mid_request(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("Finishing", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()

    def finish_soon() -> None:
        time.sleep(0.15)
        queue.complete(job.id)

    threading.Thread(target=finish_soon, daemon=True).start()
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert not workspace_mod.exists(ws.task_id, root=ws_root)


def test_delete_workspace_returns_404_for_unknown_workspace(client: TestClient) -> None:
    res = client.delete("/api/workspaces/ghost")
    assert res.status_code == 404
