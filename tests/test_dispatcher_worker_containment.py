from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import worker_liveness
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover, find_handover_by_id, save_handover
from alan.queue import WORKER_STATE_META_KEY, JobStatus, Queue, QueueError
from alan.routes._deps import optional_dispatcher_dep, queue_dep
from alan.teams._base import TeamEvent
from alan.teams.triage_pregate import gather_worker_pool
from alan.workspace import Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _make_handover(task_id: str, from_team: str = "research") -> Handover:
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


class _NoopTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(self, _ws: Workspace, _prior: Handover | None) -> list[Any]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _make_handover(workspace.task_id, from_team=self.name)


_NOOP_TEAMS: dict[str, Any] = {"research": _NoopTeam()}


def test_execute_unexpected_error_yields_failed_job_worker_survives(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_mod.create("ws-err", "d", root=root)
    workspace_mod.create("ws-ok", "d", root=root)

    call_count = {"n": 0}
    real_read = workspace_mod.read

    def maybe_raise(task_id: str, *, root: Path | None = None) -> Workspace:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("unexpected db crash")
        return real_read(task_id, root=root)

    monkeypatch.setattr("alan.dispatcher.workspace_mod.read", maybe_raise)

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01)
    queue.enqueue("ws-err", "research")
    queue.enqueue("ws-ok", "research")

    d.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        jobs = queue.list_jobs()
        failed = [j for j in jobs if j.status == JobStatus.FAILED]
        completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
        if failed and completed:
            break
        time.sleep(0.02)
    d.stop(timeout=3.0)

    jobs = queue.list_jobs()
    failed = [j for j in jobs if j.status == JobStatus.FAILED]
    completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
    assert len(failed) == 1, f"expected 1 failed job, got statuses {[j.status for j in jobs]}"
    assert len(completed) == 1, f"expected 1 completed job, got statuses {[j.status for j in jobs]}"
    assert "internal error" in (failed[0].error or "")


def test_execute_unexpected_error_logs_exception(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_mod.create("err-log", "d", root=root)

    def always_raise(task_id: str, *, root: Path | None = None) -> Workspace:
        raise RuntimeError("boom from read")

    monkeypatch.setattr("alan.dispatcher.workspace_mod.read", always_raise)

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    queue.enqueue("err-log", "research")

    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d.run_once()

    assert any("_execute: unexpected error" in r.message for r in caplog.records)
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert "internal error" in (finished.error or "")


def test_safe_fail_job_queue_error_does_not_crash(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_mod.create("sfj-qe", "d", root=root)

    def raise_queue_error(job: Any, reason: str) -> None:  # noqa: ARG001
        raise QueueError("job 1 is not running")

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    monkeypatch.setattr(d, "_fail_job", raise_queue_error)

    job = queue.enqueue("sfj-qe", "research")
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._safe_fail_job(job, "test reason")  # noqa: SLF001

    assert any("no longer RUNNING" in r.message for r in caplog.records)


def test_safe_fail_job_unexpected_exception_does_not_crash(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_mod.create("sfj-exc", "d", root=root)

    def raise_unexpected(job: Any, reason: str) -> None:  # noqa: ARG001
        raise OSError("disk full")

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    monkeypatch.setattr(d, "_fail_job", raise_unexpected)

    job = queue.enqueue("sfj-exc", "research")
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._safe_fail_job(job, "test reason")  # noqa: SLF001

    assert any("_safe_fail_job: unexpected error" in r.message for r in caplog.records)


def test_safe_fail_job_succeeds_normally(
    queue: Queue,
    root: Path,
) -> None:
    workspace_mod.create("sfj-ok", "d", root=root)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    job = queue.enqueue("sfj-ok", "research")
    queue.claim()
    d._safe_fail_job(job, "some error")  # noqa: SLF001
    assert queue.list_jobs()[0].status is JobStatus.FAILED


def test_loop_iteration_exception_does_not_kill_worker(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_mod.create("loop-ok", "d", root=root)

    call_count = {"n": 0}
    real_run_once = Dispatcher.run_once

    def maybe_raise(self: Dispatcher) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("iteration blow-up")
        return real_run_once(self)

    monkeypatch.setattr(Dispatcher, "run_once", maybe_raise)

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01)
    queue.enqueue("loop-ok", "research")

    d.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        jobs = queue.list_jobs()
        if any(j.status == JobStatus.COMPLETED for j in jobs):
            break
        time.sleep(0.02)
    d.stop(timeout=3.0)

    assert any(j.status == JobStatus.COMPLETED for j in queue.list_jobs())


def test_start_rolls_back_alive_count_on_thread_start_failure(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=1)

    def fail_start(self: threading.Thread) -> None:
        raise RuntimeError("OS refused to start thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)

    with pytest.raises(RuntimeError, match="OS refused to start thread"):
        d.start()

    assert d.live_worker_count == 0
    assert d._threads == []  # noqa: SLF001


def test_live_worker_count_zero_before_start(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert d.live_worker_count == 0


def test_live_worker_count_equals_concurrency_while_running(
    queue: Queue, root: Path
) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01, concurrency=2)
    d.start()
    try:
        assert d.live_worker_count == 2
    finally:
        d.stop(timeout=2.0)


def test_live_worker_count_zero_after_stop(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01)
    d.start()
    d.stop(timeout=2.0)
    assert d.live_worker_count == 0


def test_configured_worker_count_matches_concurrency(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=3)
    assert d.configured_worker_count == 3


def test_find_handover_by_id_skips_corrupt_file_finds_valid(root: Path) -> None:
    ws = workspace_mod.create("hov-skip", "d", root=root)
    valid = _make_handover(ws.task_id)
    save_handover(ws, valid)

    handovers_dir = ws.path / "handovers"
    corrupt_path = handovers_dir / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [unclosed", encoding="utf-8")

    result = find_handover_by_id(ws, valid.handover_id)
    assert result is not None
    assert result.handover_id == valid.handover_id


def test_find_handover_by_id_all_corrupt_returns_none(root: Path) -> None:
    ws = workspace_mod.create("hov-all-bad", "d", root=root)
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )

    assert find_handover_by_id(ws, "some-id") is None


def test_find_handover_by_id_corrupt_is_logged(
    root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("hov-log", "d", root=root)
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )

    with caplog.at_level("WARNING", logger="alan.handover"):
        find_handover_by_id(ws, "missing-id")

    assert any("skipping unreadable" in r.message for r in caplog.records)


def test_corrupt_handover_job_fails_cleanly_worker_survives(
    queue: Queue,
    root: Path,
) -> None:
    ws = workspace_mod.create("corrupt-hov", "d", root=root)
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "not: valid: yaml: [", encoding="utf-8"
    )
    ws2 = workspace_mod.create("ok-ws", "d", root=root)

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01)
    queue.enqueue(ws.task_id, "research", parent_handover_id="handover_corrupt_001")
    queue.enqueue(ws2.task_id, "research")

    d.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        jobs = queue.list_jobs()
        if (
            any(j.status == JobStatus.FAILED for j in jobs)
            and any(j.status == JobStatus.COMPLETED for j in jobs)
        ):
            break
        time.sleep(0.02)
    d.stop(timeout=3.0)

    jobs = queue.list_jobs()
    failed = [j for j in jobs if j.status == JobStatus.FAILED]
    completed = [j for j in jobs if j.status == JobStatus.COMPLETED]
    assert len(failed) == 1
    assert "parent handover missing" in (failed[0].error or "")
    assert len(completed) == 1


def test_health_endpoint_worker_counts_none_when_no_dispatcher(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    q = Queue(db_path=tmp_path / "q.db")
    server.app.dependency_overrides[queue_dep] = lambda: q
    server.app.dependency_overrides[optional_dispatcher_dep] = lambda: None
    try:
        with TestClient(server.app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dispatch_loops_alive"] is None
        assert data["dispatch_loops_configured"] is None
    finally:
        server.app.dependency_overrides.clear()
        with contextlib.suppress(Exception):
            q.close()


def test_health_endpoint_worker_counts_present_when_dispatcher_provided(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from alan import server

    q = Queue(db_path=tmp_path / "q2.db")
    d = Dispatcher(q, {}, workspaces_root=tmp_path / "ws", concurrency=3)

    server.app.dependency_overrides[queue_dep] = lambda: q
    server.app.dependency_overrides[optional_dispatcher_dep] = lambda: d
    try:
        with TestClient(server.app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dispatch_loops_alive"] == 0
        assert data["dispatch_loops_configured"] == 3
    finally:
        server.app.dependency_overrides.clear()
        with contextlib.suppress(Exception):
            q.close()


def test_optional_dispatcher_dep_returns_none_when_no_dispatcher(
    queue: Queue, root: Path
) -> None:
    import types

    from fastapi import Request

    app_state = types.SimpleNamespace(dispatcher=None)
    fake_app = types.SimpleNamespace(state=app_state)
    scope = {"type": "http", "app": fake_app}
    request = Request(scope)
    result = optional_dispatcher_dep(request)
    assert result is None


def test_optional_dispatcher_dep_returns_dispatcher_when_present(
    queue: Queue, root: Path
) -> None:
    import types

    from fastapi import Request

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    app_state = types.SimpleNamespace(dispatcher=d)
    fake_app = types.SimpleNamespace(state=app_state)
    scope = {"type": "http", "app": fake_app}
    request = Request(scope)
    result = optional_dispatcher_dep(request)
    assert result is d


def test_normal_restart_produces_no_worker_pool_alert(
    queue: Queue, root: Path
) -> None:
    d1 = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=2, poll_interval=0.01)
    d1.start()
    d1.stop(timeout=3.0)

    assert not gather_worker_pool(queue).is_blocked

    d2 = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=2, poll_interval=0.01)
    d2.start()
    try:
        assert not gather_worker_pool(queue).is_blocked
    finally:
        d2.stop(timeout=3.0)


def test_shutdown_with_running_job_in_flight_produces_no_worker_pool_alert(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("ws-inflight", "d", root=root)
    queue.enqueue("ws-inflight", "research")
    queue.claim()

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=2, poll_interval=0.01)
    d.start()
    try:
        d._on_worker_exit()  # noqa: SLF001
        assert not gather_worker_pool(queue).is_blocked
    finally:
        d.stop(timeout=3.0)


def test_detached_survivor_across_restart_produces_no_worker_pool_alert(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d1 = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, concurrency=2, poll_interval=0.01)
    d1.start()
    d1.stop(timeout=3.0)

    queue.set_meta(
        WORKER_STATE_META_KEY,
        worker_liveness.encode(
            worker_liveness.DispatchLoopReading(
                live_loops=0, configured_loops=2, observed_at=datetime.now(UTC).timestamp()
            )
        ),
    )
    monkeypatch.setattr(worker_liveness, "probe_active_workers", lambda: 1)

    assert not gather_worker_pool(queue).is_blocked
