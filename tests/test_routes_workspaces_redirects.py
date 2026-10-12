from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan.dispatcher import PubSub
from alan.queue import Queue
from alan.workspace import create


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, pubsub: PubSub
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=False) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_legacy_dispatch_returns_308(client: TestClient, ws_root: Path) -> None:
    create("Redirect WS", "r", root=ws_root)
    res = client.post(
        "/api/workspaces/redirect-ws/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 308


def test_legacy_dispatch_location_header_points_to_canonical(
    client: TestClient, ws_root: Path
) -> None:
    create("Location WS", "l", root=ws_root)
    res = client.post(
        "/api/workspaces/location-ws/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 308
    assert res.headers["location"] == (
        "/api/workspaces/location-ws/tasks/location-ws/dispatch"
    )


def test_canonical_dispatch_returns_202(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    create("Canonical WS", "c", root=ws_root)
    res = client.post(
        "/api/workspaces/canonical-ws/tasks/canonical-ws/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    payload = res.json()
    assert "job_id" in payload
    assert payload["invocation_id"] == "inv_001"
    jobs = queue.list_jobs(task_id="canonical-ws")
    assert len(jobs) == 1
    assert jobs[0].team == "research"


def test_canonical_dispatch_404_for_unknown_workspace(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces/ghost/tasks/ghost/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404


def test_canonical_dispatch_400_for_unknown_team(
    client: TestClient, ws_root: Path
) -> None:
    create("Bad Team WS", "b", root=ws_root)
    res = client.post(
        "/api/workspaces/bad-team-ws/tasks/bad-team-ws/dispatch",
        json={"team": "ghost"},
    )
    assert res.status_code == 400
    assert "ghost" in res.json()["detail"]


def test_canonical_dispatch_404_for_mismatched_task_id(
    client: TestClient, ws_root: Path
) -> None:
    create("Mismatch WS", "m", root=ws_root)
    res = client.post(
        "/api/workspaces/mismatch-ws/tasks/other-task/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404
    assert "other-task" in res.json()["detail"]


def test_canonical_dispatch_409_for_archived_workspace(
    client: TestClient, ws_root: Path
) -> None:
    from alan import workspace as workspace_mod
    from alan.workspace import Status

    ws = create("Archived WS", "a", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 409
    assert "archived" in res.json()["detail"]
