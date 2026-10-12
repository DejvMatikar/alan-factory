from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alan import worker_records, workflow_interrupt
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan._dispatcher_base import ProcRegistry, ProcStartedHook
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover
from alan.queue import Job, JobStatus, Queue, QueueError
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Workspace


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


_TRACKED_PROCS: list[subprocess.Popen[str]] = []


@pytest.fixture(autouse=True)
def _kill_blocking_team_procs() -> Iterator[None]:
    yield
    for proc in _TRACKED_PROCS:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    _TRACKED_PROCS.clear()


class _BlockingTeam:
    name = "research"
    supports_resume = True

    def __init__(
        self,
        release: threading.Event,
        *,
        running: threading.Event | None = None,
        attach_proc: bool = True,
        supports_resume: bool = True,
    ) -> None:
        self._release = release
        self._running = running
        self._attach = attach_proc
        self.supports_resume = supports_resume

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        if self._attach and on_proc_started is not None:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"], text=True
            )
            _TRACKED_PROCS.append(proc)
            on_proc_started(proc)
        if self._running is not None:
            self._running.set()
        self._release.wait(timeout=10.0)
        raise RuntimeError("user-cancelled")


def _collect(
    pubsub: PubSub, task_id: str
) -> tuple[list[TeamEvent], Callable[[], None]]:
    events: list[TeamEvent] = []
    lock = threading.Lock()

    def on_event(e: TeamEvent) -> None:
        with lock:
            events.append(e)

    return events, pubsub.subscribe(task_id, on_event)


def test_proc_registry_attach_and_terminate() -> None:
    reg = ProcRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        reg.attach(1, proc)
        assert reg.terminate(1) is True
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_proc_registry_terminate_unknown_returns_false() -> None:
    reg = ProcRegistry()
    assert reg.terminate(999) is False


def test_proc_registry_detach_removes_handle() -> None:
    reg = ProcRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait()
    reg.attach(1, proc)
    reg.detach(1)
    assert reg.terminate(1) is False


def test_proc_registry_skips_terminate_for_already_exited_proc() -> None:
    reg = ProcRegistry()
    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait()
    reg.attach(1, proc)
    assert reg.terminate(1) is True


def test_proc_registry_sigterm_then_sigkill_when_unresponsive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg = ProcRegistry()
    ready_file = tmp_path / "sigterm-ignore-ready"
    script = (
        "import signal, sys, time, pathlib;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"pathlib.Path({str(ready_file)!r}).write_text('1');"
        "time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], text=True)
    try:
        for _ in range(50):
            if ready_file.exists():
                break
            import time as _t

            _t.sleep(0.05)
        assert ready_file.exists(), "child never installed SIGTERM-ignore"
        reg.attach(1, proc)
        monkeypatch.setattr(worker_records, "SIGTERM_GRACE_S", 0.5)
        assert reg.terminate(1) is True
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_proc_registry_attach_multiple_procs_same_job_terminates_all() -> None:
    reg = ProcRegistry()
    proc1 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    proc2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        reg.attach(1, proc1)
        reg.attach(1, proc2)
        assert reg.terminate(1) is True
        proc1.wait(timeout=5.0)
        proc2.wait(timeout=5.0)
        assert proc1.returncode is not None
        assert proc2.returncode is not None
    finally:
        for p in (proc1, proc2):
            if p.poll() is None:
                p.kill()
                p.wait()


def test_proc_registry_detach_removes_all_procs_for_job() -> None:
    reg = ProcRegistry()
    proc1 = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc2 = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc1.wait()
    proc2.wait()
    reg.attach(1, proc1)
    reg.attach(1, proc2)
    reg.detach(1)
    assert reg.terminate(1) is False


def _write_terminable_record(
    session_id: str, *, job_id: int | None, pid: int, pgid: int | None = None
) -> None:
    identity = worker_records.ps_identity(pid)
    assert identity is not None
    lstart, cmdline = identity
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=job_id, task_id="t1", workspace_id="ws-1", team="implementation",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path="/fake/slot_0", session_id=session_id,
            output_path="/fake/slot_0/stream.jsonl",
            pid=pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=pgid,
        )
    )


def test_proc_registry_terminate_falls_back_to_worker_records_when_no_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        _write_terminable_record("sess-fallback", job_id=7, pid=proc.pid)
        reg = ProcRegistry()
        assert reg.terminate(7) is True
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
        assert worker_records.load_all() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_proc_registry_terminate_returns_false_when_no_handle_and_no_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    reg = ProcRegistry()
    assert reg.terminate(999) is False


def test_proc_registry_terminate_via_records_ignores_null_job_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        _write_terminable_record("sess-null-job", job_id=None, pid=proc.pid)
        reg = ProcRegistry()
        assert reg.terminate(7) is False
        assert proc.poll() is None
        assert worker_records.load_all()[0][1] is not None
    finally:
        proc.kill()
        proc.wait()


def test_proc_registry_terminate_via_records_skips_non_terminable_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=7, task_id="t1", workspace_id="ws-1", team="implementation",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path="/fake/slot_0", session_id="sess-unknown",
            output_path="/fake/slot_0/stream.jsonl",
            pid=None, started_at=None, lstart=None, cmdline=None,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(),
        )
    )
    reg = ProcRegistry()
    assert reg.terminate(7) is False
    assert worker_records.load_all()[0][1] is not None


def test_proc_registry_terminate_via_records_kills_group_when_pgid_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True,
        start_new_session=True,
    )
    try:
        _write_terminable_record(
            "sess-group", job_id=7, pid=proc.pid, pgid=os.getpgid(proc.pid)
        )
        reg = ProcRegistry()
        assert reg.terminate(7) is True
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
        assert worker_records.load_all() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_proc_registry_terminate_via_records_refuses_own_group_and_keeps_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _write_terminable_record("sess-own-group", job_id=7, pid=os.getpid(), pgid=999999999)
    reg = ProcRegistry()
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        assert reg.terminate(7) is False
    assert any("refusing to signal own process group" in r.message for r in caplog.records)
    assert worker_records.load_all()[0][1] is not None


def test_proc_started_hook_attaches_proc_to_registry() -> None:
    reg = ProcRegistry()
    hook = ProcStartedHook(reg, 5)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        hook(proc)
        assert reg.terminate(5) is True
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_proc_started_hook_carries_job_id_attribute() -> None:
    hook = ProcStartedHook(ProcRegistry(), 42)
    assert hook.job_id == 42


def test_interrupt_task_returns_false_when_no_running_job(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("t1", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d.interrupt_task("t1") is False


def test_interrupt_task_rolls_back_pause_when_proc_not_registered(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d.interrupt_task("t1") is False
    assert queue.is_task_paused(ws.task_id) is False
    with d._user_paused_lock:  # noqa: SLF001
        assert not d._user_paused  # noqa: SLF001


def test_resume_task_clears_pause_flag(queue: Queue, root: Path) -> None:
    workspace_mod.create("t1", "d", root=root)
    queue.pause_task("t1")
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task("t1")
    assert queue.is_task_paused("t1") is False


def test_interrupt_task_pauses_and_terminates(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        assert d.interrupt_task("t1") is True
        assert queue.is_task_paused("t1") is True
    finally:
        release.set()
        worker.join(timeout=10.0)


def test_user_pause_requeues_for_resume_on_resume_team(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running, supports_resume=True)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    jobs = queue.list_jobs(task_id="t1")
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].resume_inv_id is not None
    assert jobs[0].interruption_count == 1


def test_user_pause_requeues_pending_for_non_resume_team(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running, supports_resume=False)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    jobs = queue.list_jobs(task_id="t1")
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].resume_inv_id is None
    assert jobs[0].interruption_count == 1


def test_user_pause_does_not_flip_workspace_to_awaiting_input(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is not workspace_mod.Status.AWAITING_INPUT


def test_user_pause_publishes_job_paused_event(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    pubsub = PubSub()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect(pubsub, ws.task_id)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    unsub()
    paused = [e for e in events if e.kind == "job_paused"]
    assert paused, [e.kind for e in events]
    assert paused[0].payload["mode"] == "resume"


def test_paused_task_is_not_reclaimed_until_resumed(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    assert d.run_once() is False
    d.resume_task("t1")
    next_release = threading.Event()
    next_release.set()
    next_team = _BlockingTeam(next_release)
    d2 = Dispatcher(queue, {"research": next_team}, workspaces_root=root)
    assert d2.run_once() is True


def test_handle_team_exception_requeue_skipped_when_job_already_pending(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    release.set()
    team = _BlockingTeam(release, supports_resume=True)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_001")

    with d._user_paused_lock:  # noqa: SLF001
        d._user_paused.add(job.id)  # noqa: SLF001

    with caplog.at_level("INFO", logger="alan.dispatcher"):
        d._pause_job(  # noqa: SLF001
            queue.list_jobs(task_id=ws.task_id)[0],
            ws,
            "inv_001",
            team,
        )

    assert any("already pending" in r.message for r in caplog.records)
    assert not any("user-pause requeue failed" in r.message for r in caplog.records)
    assert queue.list_jobs(task_id=ws.task_id)[0].status is JobStatus.PENDING
    assert workspace_mod.read(ws.task_id, root=root).meta.status is not Status.AWAITING_INPUT


def test_user_pause_records_the_live_invocation_id_not_an_empty_string(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running, supports_resume=True)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)

    paused = queue.list_jobs(task_id="t1")[0]
    assert paused.resume_inv_id
    assert paused.resume_inv_id.startswith("inv_")


def test_user_pause_recovers_job_failed_out_from_under_the_worker(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    release.set()
    team = _BlockingTeam(release, supports_resume=True)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "interrupted_by_restart")

    d._pause_job(job, ws, "inv_001", team)  # noqa: SLF001

    recovered = queue.list_jobs(task_id=ws.task_id)[0]
    assert recovered.status is JobStatus.PENDING
    assert recovered.resume_inv_id == "inv_001"
    assert workspace_mod.read(ws.task_id, root=root).meta.status is not Status.AWAITING_INPUT


def test_user_pause_strands_and_parks_when_recovery_also_fails(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    release.set()
    team = _BlockingTeam(release, supports_resume=True)
    pubsub = PubSub()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "interrupted_by_restart")

    def raising(*_args: object, **_kwargs: object) -> Job:
        raise QueueError("row vanished")

    queue.requeue_failed_with_resume = raising  # type: ignore[method-assign]
    events: list[TeamEvent] = []
    unsub = pubsub.subscribe(ws.task_id, events.append)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._pause_job(job, ws, "inv_001", team)  # noqa: SLF001
    unsub()

    assert any("user-pause requeue failed" in r.message for r in caplog.records)
    assert [e for e in events if e.kind == "job_stranded"]
    assert not [e for e in events if e.kind == "job_paused"]
    assert workspace_mod.read(ws.task_id, root=root).meta.status is Status.AWAITING_INPUT


def test_pause_job_repairs_workflow_finalized_failed_by_lifecycle(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    release.set()
    team = _BlockingTeam(release, supports_resume=True)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv_001")

    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="devil_advocate", role="devil_advocate", model="sonnet")],
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("devil_advocate", role="devil_advocate", model="sonnet")
    graph.set_session_id("devil_advocate", "sid-race")
    graph.fail_node("devil_advocate", error="claude CLI exited 143: stderr=<empty>; stdout=<empty>")
    graph.finalize(status="failed")
    workflow_mod.save_workflow(ws, graph.doc)

    with d._user_paused_lock:  # noqa: SLF001
        d._user_paused.add(job.id)  # noqa: SLF001
    d._pause_job(  # noqa: SLF001
        queue.list_jobs(task_id=ws.task_id)[0],
        ws,
        inv_id,
        team,
    )

    repaired = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert repaired.status == "interrupted"
    assert repaired.completed_at is None
    task = repaired.tasks[0]
    assert task.status == "interrupted"
    assert task.claude_session_id == "sid-race"
    assert task.completed_at is None
    assert task.error == "user paused"


def test_interrupt_workflow_on_disk_failure_is_logged(
    queue: Queue,
    root: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    release.set()
    team = _BlockingTeam(release)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()

    def fail_interrupt(*_a: object, **_k: object) -> None:
        raise OSError("disk read-only")

    monkeypatch.setattr(workflow_interrupt, "interrupt_workflow_on_disk", fail_interrupt)
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._pause_job(  # noqa: SLF001
            queue.list_jobs(task_id=ws.task_id)[0],
            ws,
            "inv_001",
            team,
        )
    assert any("interrupt_workflow_on_disk failed" in r.message for r in caplog.records)
    jobs = queue.list_jobs(task_id=ws.task_id)
    assert jobs[0].status is JobStatus.PENDING


def test_claim_user_paused_returns_false_when_not_paused(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("t1", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d._claim_user_paused(42) is False  # noqa: SLF001


def test_terminate_proc_noop_for_exited_process() -> None:
    from alan._dispatcher_base import _terminate_proc

    proc = subprocess.Popen([sys.executable, "-c", "pass"], text=True)
    proc.wait()
    _terminate_proc(proc)
    assert proc.returncode is not None


def _make_dispatcher_failed_resumable(queue: Queue, root: Path, task_id: str) -> Job:
    j = queue.enqueue(task_id, "research", triggered_by_invocation_id="inv_001")
    queue.claim()
    queue.requeue_for_resume(j.id, "inv_001")
    queue.claim()
    return queue.mark_interrupted_and_fail(j.id, "interrupted")


def test_resume_task_requeues_failed_resumable_job_to_pending(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    _make_dispatcher_failed_resumable(queue, root, ws.task_id)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task(ws.task_id)
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.PENDING
    assert rows[0].resume_inv_id == "inv_001"


def test_resume_task_publishes_job_requeued_for_resume_event(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    _make_dispatcher_failed_resumable(queue, root, ws.task_id)
    pubsub = PubSub()
    events, unsub = _collect(pubsub, ws.task_id)
    d = Dispatcher(queue, {}, pubsub=pubsub, workspaces_root=root)
    d.resume_task(ws.task_id)
    unsub()
    matching = [e for e in events if e.kind == "job_requeued_for_resume"]
    assert matching, [e.kind for e in events]
    assert matching[0].payload["inv_id"] == "inv_001"


def test_resume_task_logs_warning_when_requeue_fails(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    _make_dispatcher_failed_resumable(queue, root, ws.task_id)

    def explode(self: Queue, job_id: int) -> Job:
        del self, job_id
        raise QueueError("db error")

    monkeypatch.setattr(Queue, "requeue_failed_for_resume", explode)
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d.resume_task(ws.task_id)
    assert any("requeue_failed_for_resume failed" in r.message for r in caplog.records)


def test_interrupt_task_works_for_multi_task_workspace(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("multi-outer", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    job = queue.enqueue("subtask-a", "research", workspace_id=ws.task_id)
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        assert d.interrupt_task(ws.task_id) is True
        assert queue.is_task_paused(ws.task_id) is True
        assert queue.is_task_paused("subtask-a") is False
        release.set()
    finally:
        worker.join(timeout=10.0)
    jobs = queue.list_jobs(task_id="subtask-a")
    assert jobs[0].status is JobStatus.PENDING
    del job


def test_resume_task_works_for_multi_task_workspace(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("multi-outer", "d", root=root)
    j = queue.enqueue("subtask-a", "research", workspace_id=ws.task_id,
                      triggered_by_invocation_id="inv_001")
    queue.claim()
    queue.requeue_for_resume(j.id, "inv_001")
    queue.claim()
    queue.mark_interrupted_and_fail(j.id, "interrupted")
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task(ws.task_id)
    rows = queue.list_jobs(task_id="subtask-a")
    assert rows[0].status is JobStatus.PENDING
    assert rows[0].resume_inv_id == "inv_001"


def test_resume_task_skips_requeue_when_resume_inv_id_empty(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = _make_dispatcher_failed_resumable(queue, root, ws.task_id)
    empty_inv_job = job.model_copy(update={"resume_inv_id": ""})
    monkeypatch.setattr(
        Queue, "list_resumable_failed_by_workspace", lambda self, tid: [empty_inv_job]
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d.resume_task(ws.task_id)
    assert any("empty resume_inv_id" in r.message for r in caplog.records)
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_user_pause_does_not_trigger_manager_summary(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trigger_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **kwargs: trigger_calls.append(args),
    )
    ws = workspace_mod.create("t1", "d", root=root)
    release = threading.Event()
    running = threading.Event()
    team = _BlockingTeam(release, running=running)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    worker = threading.Thread(target=d.run_once, daemon=True)
    worker.start()
    assert running.wait(timeout=5.0)
    try:
        d.interrupt_task("t1")
        release.set()
    finally:
        worker.join(timeout=10.0)
    assert trigger_calls == []
