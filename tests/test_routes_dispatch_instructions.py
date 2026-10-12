from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.queue import Queue
from alan.workflow import PendingTaskSpec


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
def client(ws_root: Path, queue: Queue) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_unknown_workspace_returns_200_with_empty_map(client: TestClient) -> None:
    res = client.get("/api/workspaces/ghost/dispatch-instructions")
    assert res.status_code == 200
    assert res.json() == {}


def test_workspace_with_no_invocations_returns_empty_map(
    client: TestClient, ws_root: Path
) -> None:
    workspace_mod.create("no-invocations", "d", root=ws_root)
    res = client.get("/api/workspaces/no-invocations/dispatch-instructions")
    assert res.status_code == 200
    assert res.json() == {}


def test_workspace_with_mixed_invocation_statuses(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("mixed", "d", root=ws_root)
    attached_inv = workflow_mod.seed_pending_workflow(
        ws,
        team="implementation",
        tasks=[PendingTaskSpec(id="worker", role="worker", model="haiku")],
        source_task_id=ws.task_id,
    )
    queue.enqueue(
        ws.task_id,
        "implementation",
        triggered_by_invocation_id=attached_inv,
        instructions="ship the feature",
    )
    none_inv = workflow_mod.seed_pending_workflow(
        ws,
        team="implementation",
        tasks=[PendingTaskSpec(id="worker", role="worker", model="haiku")],
        source_task_id=ws.task_id,
    )
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=none_inv)
    not_delivered_inv = workflow_mod.seed_pending_workflow(
        ws,
        team="pr_merge",
        tasks=[PendingTaskSpec(id="worker", role="worker", model="haiku")],
        source_task_id=ws.task_id,
    )
    queue.enqueue(
        ws.task_id,
        "pr_merge",
        triggered_by_invocation_id=not_delivered_inv,
        instructions="merge now",
    )
    unknown_inv = workflow_mod.seed_pending_workflow(
        ws,
        team="verification",
        tasks=[PendingTaskSpec(id="worker", role="worker", model="haiku")],
        source_task_id=ws.task_id,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/dispatch-instructions")
    assert res.status_code == 200
    body = res.json()
    assert body == {
        attached_inv: {"status": "attached", "text": "ship the feature", "origin": None},
        none_inv: {"status": "none", "text": None, "origin": None},
        not_delivered_inv: {"status": "not_delivered", "text": "merge now", "origin": None},
        unknown_inv: {"status": "unknown", "text": None, "origin": None},
    }


def test_literal_path_is_not_shadowed_by_the_parameterised_invocations_routes(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("no-params", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}/dispatch-instructions")
    assert res.status_code == 200
    assert res.json() == {}


def test_get_sticky_instructions_unknown_workspace_404s(client: TestClient) -> None:
    res = client.get("/api/workspaces/ghost/sticky-instructions")
    assert res.status_code == 404


def test_get_sticky_instructions_empty_when_none_persisted(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("no-sticky", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}/sticky-instructions")
    assert res.status_code == 200
    assert res.json() == {"text": None, "origin": None}


def test_get_sticky_instructions_returns_persisted_text_and_origin(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("has-sticky", "d", root=ws_root)
    ws = workspace_mod.set_sticky_instructions(ws, ws.task_id, "focus here", origin="dispatch")
    res = client.get(f"/api/workspaces/{ws.task_id}/sticky-instructions")
    assert res.status_code == 200
    assert res.json() == {"text": "focus here", "origin": "dispatch"}


def test_delete_sticky_instructions_unknown_workspace_404s(client: TestClient) -> None:
    res = client.delete("/api/workspaces/ghost/sticky-instructions")
    assert res.status_code == 404


def test_delete_sticky_instructions_clears_and_is_idempotent(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("clear-sticky", "d", root=ws_root)
    ws = workspace_mod.set_sticky_instructions(ws, ws.task_id, "focus here", origin="dispatch")
    first = client.delete(f"/api/workspaces/{ws.task_id}/sticky-instructions")
    assert first.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions == {}
    second = client.delete(f"/api/workspaces/{ws.task_id}/sticky-instructions")
    assert second.status_code == 204
