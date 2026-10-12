from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workspace as workspace_mod
from alan.archive_guard import INCOMPLETE_SUBTASKS_PREFIX
from alan.dispatcher import PubSub
from alan.queue import Queue
from alan.roots import logs_root
from alan.workspace import Status, Task, TaskStatus, create


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _archive_audit_rows() -> list[dict[str, object]]:
    log_path = logs_root() / "archive_audit.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines()]


def test_archive_bodyless_all_subtasks_complete_succeeds(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "All Done", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.COMPLETED)]
    )
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_archive_bodyless_zero_subtasks_succeeds(client: TestClient, ws_root: Path) -> None:
    ws = create("No Subtasks", "d", root=ws_root)
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_archive_bodyless_awaiting_input_subtask_refused(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Awaiting",
        "d",
        root=ws_root,
        tasks=[
            Task(id="a", status=TaskStatus.COMPLETED),
            Task(id="b", status=TaskStatus.AWAITING_INPUT),
        ],
    )
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail.startswith(INCOMPLETE_SUBTASKS_PREFIX)
    assert "b" in detail
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE


def test_archive_bodyless_failed_and_completed_mix_succeeds(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Terminal Mix",
        "d",
        root=ws_root,
        tasks=[
            Task(id="a", status=TaskStatus.FAILED),
            Task(id="b", status=TaskStatus.COMPLETED),
        ],
    )
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_archive_forced_with_reason_on_incomplete_succeeds_and_audits(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Force Me", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    res = client.post(
        f"/api/workspaces/{ws.task_id}/archive",
        json={"force": True, "reason": "abandon"},
    )
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED
    assert reloaded.meta.interventions == []
    rows = _archive_audit_rows()
    assert len(rows) == 1
    assert rows[0]["task_id"] == ws.task_id
    assert rows[0]["reason"] == "abandon"


def test_archive_forced_with_empty_reason_rejected(client: TestClient, ws_root: Path) -> None:
    ws = create(
        "Empty Reason", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    res = client.post(
        f"/api/workspaces/{ws.task_id}/archive",
        json={"force": True, "reason": ""},
    )
    assert res.status_code == 422
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert _archive_audit_rows() == []


def test_archive_forced_on_already_complete_workspace_writes_no_audit_row(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Complete Force", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.COMPLETED)]
    )
    res = client.post(
        f"/api/workspaces/{ws.task_id}/archive",
        json={"force": True, "reason": "x"},
    )
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED
    assert _archive_audit_rows() == []


def test_archive_active_queued_work_refused_before_subtask_check(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create(
        "Active Jobs", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    queue.enqueue(ws.task_id, "research")
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 409
    assert res.json()["detail"] == "workspace has active jobs"
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE


def test_archive_paused_workspace_with_incomplete_subtask_hits_new_refusal(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create(
        "Paused Incomplete", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    queue.enqueue(ws.task_id, "research", workspace_id=ws.task_id)
    queue.claim()
    queue.pause_task(ws.task_id)
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 409
    assert res.json()["detail"].startswith(INCOMPLETE_SUBTASKS_PREFIX)
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE


def test_archive_already_archived_complete_workspace_still_succeeds(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Rearchive", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.COMPLETED)]
    )
    workspace_mod.transition(ws, Status.ARCHIVED)
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_force_archive_then_unarchive_parks_for_operator_without_reenqueue(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create(
        "Force Then Restore",
        "d",
        root=ws_root,
        tasks=[Task(id="a", status=TaskStatus.PENDING)],
    )
    res = client.post(
        f"/api/workspaces/{ws.task_id}/archive",
        json={"force": True, "reason": "abandon"},
    )
    assert res.status_code == 204
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.tasks[0].status is TaskStatus.PENDING
    assert queue.list_jobs(task_id=ws.task_id) == []
