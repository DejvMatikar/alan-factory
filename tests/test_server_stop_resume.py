from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue
from alan.teams._base import Team, TeamEvent
from alan.workspace import Status, create


def _register_dummy_proc(dispatcher: Dispatcher, job_id: int) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], text=True
    )
    dispatcher._proc_registry.attach(job_id, proc)  # noqa: SLF001
    return proc


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
    teams: dict[str, Team] = {}
    return Dispatcher(queue=queue, teams=teams, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_stop_returns_404_when_workspace_missing(client: TestClient) -> None:
    res = client.post("/api/workspaces/ghost/stop")
    assert res.status_code == 404


def test_stop_returns_404_when_no_running_job(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("nojob", "d", root=ws_root)
    res = client.post(f"/api/workspaces/{ws.task_id}/stop")
    assert res.status_code == 404
    assert res.json()["detail"] == "no running job to stop"


def test_stop_returns_409_when_workspace_already_paused(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("already-paused", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PAUSED)
    res = client.post(f"/api/workspaces/{ws.task_id}/stop")
    assert res.status_code == 409
    assert "paused" in res.json()["detail"]


def test_stop_returns_409_when_workspace_archived(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("archived", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    res = client.post(f"/api/workspaces/{ws.task_id}/stop")
    assert res.status_code == 409


def test_stop_returns_409_when_workspace_completed(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("done", "d", root=ws_root)
    workspace_mod.transition(ws, Status.COMPLETED)
    res = client.post(f"/api/workspaces/{ws.task_id}/stop")
    assert res.status_code == 409


def test_stop_happy_path_returns_job_id_and_flips_status(
    client: TestClient,
    ws_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> None:
    ws = create("running-job", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    proc = _register_dummy_proc(dispatcher, job.id)
    try:
        received: list[TeamEvent] = []
        pubsub.subscribe(ws.task_id, received.append)
        res = client.post(f"/api/workspaces/{ws.task_id}/stop")
        assert res.status_code == 202
        assert res.json() == {"job_id": job.id}
        reloaded = workspace_mod.read(ws.task_id, root=ws_root)
        assert reloaded.meta.status is Status.PAUSED
        assert queue.is_task_paused(ws.task_id) is True
        kinds = [e.kind for e in received]
        assert "workspace_paused" in kinds
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_resume_returns_404_when_workspace_missing(client: TestClient) -> None:
    res = client.post("/api/workspaces/ghost/resume")
    assert res.status_code == 404


def test_resume_returns_409_when_workspace_active(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("active-not-paused", "d", root=ws_root)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 409


def test_resume_happy_path_flips_status_and_unpauses_queue(
    client: TestClient,
    ws_root: Path,
    queue: Queue,
    pubsub: PubSub,
) -> None:
    ws = create("paused-ws", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PAUSED)
    queue.pause_task(ws.task_id)
    queue.enqueue(ws.task_id, "research")
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 202
    assert res.json() == {"queued_jobs": 1}
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert queue.is_task_paused(ws.task_id) is False
    kinds = [e.kind for e in received]
    assert "workspace_resumed" in kinds


def test_resume_records_resume_intervention(
    client: TestClient,
    ws_root: Path,
    queue: Queue,
) -> None:
    ws = create("paused-intervention-ws", "d", root=ws_root)
    workspace_mod.transition(ws, Status.PAUSED)
    queue.pause_task(ws.task_id)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 202
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert [i.action for i in reloaded.meta.interventions] == [
        workspace_mod.InterventionAction.RESUME
    ]


def test_resume_409_records_no_intervention(
    client: TestClient,
    ws_root: Path,
) -> None:
    ws = create("active-no-intervention-ws", "d", root=ws_root)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 409
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_get_workspace_includes_has_running_job_false_when_idle(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("idle", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["has_running_job"] is False


def test_get_workspace_includes_has_running_job_true_when_running(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("running", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["has_running_job"] is True


def test_stop_returns_404_when_interrupt_loses_race(
    client: TestClient, ws_root: Path, queue: Queue, dispatcher: Dispatcher
) -> None:
    ws = create("race", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    dispatcher.interrupt_task = lambda task_id: False  # type: ignore[method-assign]  # noqa: ARG005
    res = client.post(f"/api/workspaces/{ws.task_id}/stop")
    assert res.status_code == 404
    assert res.json()["detail"] == "no running job to stop"


def test_resume_awaiting_input_with_resumable_failed_job_returns_202(
    client: TestClient,
    ws_root: Path,
    queue: Queue,
    pubsub: PubSub,
) -> None:
    ws = create("awaiting-ws", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_001")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "error")
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 202
    assert res.json()["queued_jobs"] == 1
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    kinds = [e.kind for e in received]
    assert "workspace_resumed" in kinds


def test_resume_returns_409_when_workspace_awaiting_input_without_resumable_failed(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("awaiting-no-job", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    res = client.post(f"/api/workspaces/{ws.task_id}/resume")
    assert res.status_code == 409
    assert "awaiting_input" in res.json()["detail"]


def test_get_workspace_includes_has_resumable_failed_job_true(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("resumable-ws", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_001")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "error")
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["has_resumable_failed_job"] is True


def test_get_workspace_includes_requeue_count_zero_when_no_requeues(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("no-requeue", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["requeue_count"] == 0


def test_get_workspace_includes_requeue_count_after_requeue_for_resume(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("with-requeue", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_001")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "error", resume_inv_id="inv_001")
    queue.requeue_failed_for_resume(job.id)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["requeue_count"] == 1


def test_dispatcher_dep_reads_app_state(dispatcher: Dispatcher) -> None:
    from types import SimpleNamespace

    from alan.server import _dispatcher_dep

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(dispatcher=dispatcher))
    )
    assert _dispatcher_dep(request) is dispatcher  # type: ignore[arg-type]


def test_dispatcher_dep_raises_503_when_disabled() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from alan.server import _dispatcher_dep

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(dispatcher=None))
    )
    with pytest.raises(HTTPException) as excinfo:
        _dispatcher_dep(request)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 503
    assert "ALAN_DISABLE_DISPATCHER" in excinfo.value.detail
