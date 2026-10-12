from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, DispatcherError, PubSub
from alan.handover import Handover
from alan.queue import JobStatus, Queue
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _make_collector, _write_multi_task_meta
from tests._dispatcher_support_teams import (
    _NOOP_TEAMS,
    _EmittingTeam,
    _HangingTeam,
    _make_handover,
    _NoopTeam,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_pubsub_publishes_lifecycle_events(queue: Queue, root: Path) -> None:
    workspace_mod.create("lifecycle", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, {"research": _EmittingTeam()}, workspaces_root=root)
    d.pubsub.subscribe("lifecycle", collect)
    queue.enqueue("lifecycle", "research")
    d.run_once()
    kinds = [e.kind for e in events]
    assert kinds[:3] == ["job_started", "role_started", "job_completed"]
    assert events[0].payload["team"] == "research"
    assert "job_id" in events[0].payload
    assert any(k.startswith("router_") for k in kinds)


def test_pubsub_subscribe_returns_unsubscribe_handle(queue: Queue, root: Path) -> None:
    workspace_mod.create("unsub", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    unsub = d.pubsub.subscribe("unsub", collect)
    unsub()
    queue.enqueue("unsub", "research")
    d.run_once()
    assert events == []


def test_pubsub_unsubscribe_unknown_callback_is_noop(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    events, collect = _make_collector()
    unsub = d.pubsub.subscribe("x", collect)
    unsub()
    unsub()
    assert events == []


def test_pubsub_unsubscribe_unknown_task_is_noop() -> None:
    bus = PubSub()
    _events, collect = _make_collector()
    bus.publish("nobody-home", TeamEvent("k", {}))

    def fake_remove() -> None:
        bus._unsubscribe("ghost", collect)  # noqa: SLF001

    fake_remove()


def test_pubsub_publish_continues_when_subscriber_raises(queue: Queue, root: Path) -> None:
    workspace_mod.create("isolate", "d", root=root)
    received: list[TeamEvent] = []

    def good(event: TeamEvent) -> None:
        received.append(event)

    def bad(_event: TeamEvent) -> None:
        raise RuntimeError("listener exploded")

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    d.pubsub.subscribe("isolate", bad)
    d.pubsub.subscribe("isolate", good)
    queue.enqueue("isolate", "research")
    d.run_once()
    assert queue.list_jobs()[0].status is JobStatus.COMPLETED
    kinds = [e.kind for e in received]
    assert kinds[:2] == ["job_started", "job_completed"]
    assert any(k.startswith("router_") for k in kinds)


def test_pubsub_unsubscribe_last_callback_removes_task_key() -> None:
    bus = PubSub()
    _events, collect = _make_collector()
    unsub = bus.subscribe("ephemeral", collect)
    assert "ephemeral" in bus._listeners  # noqa: SLF001
    unsub()
    assert "ephemeral" not in bus._listeners  # noqa: SLF001


def test_pubsub_unsubscribe_one_of_many_keeps_others() -> None:
    bus = PubSub()
    received_a: list[TeamEvent] = []
    received_b: list[TeamEvent] = []
    unsub_a = bus.subscribe("shared", received_a.append)
    bus.subscribe("shared", received_b.append)
    unsub_a()
    bus.publish("shared", TeamEvent("k", {}))
    assert received_a == []
    assert len(received_b) == 1
    assert "shared" in bus._listeners  # noqa: SLF001


def test_stop_raises_when_thread_does_not_terminate(queue: Queue, root: Path) -> None:
    workspace_mod.create("stuck", "d", root=root)
    release = threading.Event()
    d = Dispatcher(
        queue,
        {"research": _HangingTeam(release)},
        workspaces_root=root,
        poll_interval=0.01,
    )
    queue.enqueue("stuck", "research")
    d.start()
    time.sleep(0.1)
    try:
        with pytest.raises(DispatcherError, match="did not stop"):
            d.stop(timeout=0.1)
    finally:
        release.set()
        with contextlib.suppress(DispatcherError):
            d.stop(timeout=5.0)


def test_pubsub_supports_multiple_subscribers(queue: Queue, root: Path) -> None:
    workspace_mod.create("multi", "d", root=root)
    events_a, collect_a = _make_collector()
    events_b, collect_b = _make_collector()
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    d.pubsub.subscribe("multi", collect_a)
    d.pubsub.subscribe("multi", collect_b)
    queue.enqueue("multi", "research")
    d.run_once()
    kinds_a = [e.kind for e in events_a]
    kinds_b = [e.kind for e in events_b]
    assert kinds_a[:2] == ["job_started", "job_completed"]
    assert kinds_b[:2] == ["job_started", "job_completed"]
    assert kinds_a == kinds_b


def test_background_thread_drains_queue(queue: Queue, root: Path) -> None:
    workspace_mod.create("bg", "d", root=root)
    d = Dispatcher(
        queue,
        _NOOP_TEAMS,
        workspaces_root=root,
        poll_interval=0.01,
    )
    queue.enqueue("bg", "research")
    queue.enqueue("bg", "research")
    queue.enqueue("bg", "research")
    d.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if all(j.status is JobStatus.COMPLETED for j in queue.list_jobs()):
            break
        time.sleep(0.02)
    d.stop(timeout=2.0)
    statuses = [j.status for j in queue.list_jobs()]
    assert statuses == [JobStatus.COMPLETED, JobStatus.COMPLETED, JobStatus.COMPLETED]


def test_start_twice_raises(queue: Queue, root: Path) -> None:
    d = Dispatcher(
        queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01
    )
    d.start()
    try:
        with pytest.raises(DispatcherError, match="already started"):
            d.start()
    finally:
        d.stop(timeout=2.0)


def test_stop_when_never_started_is_noop(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    d.stop()


def test_default_pubsub_constructed_when_none(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert isinstance(d.pubsub, PubSub)


def test_teams_dict_copied_on_init(queue: Queue, root: Path) -> None:
    teams: dict[str, Team] = {"research": _NoopTeam()}
    d = Dispatcher(queue, teams, workspaces_root=root)
    teams.clear()
    workspace_mod.create("copy", "d", root=root)
    queue.enqueue("copy", "research")
    assert d.run_once() is True
    assert queue.list_jobs()[0].status is JobStatus.COMPLETED


def test_dispatcher_concurrency_default_is_one(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert d._concurrency == 1  # noqa: SLF001


@pytest.mark.parametrize("bad_concurrency", [0, -1, -100])
def test_dispatcher_rejects_non_positive_concurrency(
    queue: Queue, root: Path, bad_concurrency: int
) -> None:
    with pytest.raises(DispatcherError, match=r"concurrency must be >= 1"):
        Dispatcher(
            queue,
            _NOOP_TEAMS,
            workspaces_root=root,
            concurrency=bad_concurrency,
        )


def test_dispatcher_concurrency_n_starts_n_threads(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01, concurrency=3)
    d.start()
    try:
        names = {t.name for t in d._threads}  # noqa: SLF001
        assert names == {"alan-dispatcher-0", "alan-dispatcher-1", "alan-dispatcher-2"}
        assert len(d._threads) == 3  # noqa: SLF001
    finally:
        d.stop(timeout=2.0)


def test_stop_with_none_timeout_does_not_raise(queue: Queue, root: Path) -> None:
    workspace_mod.create("none-to", "d", root=root)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, poll_interval=0.01)
    queue.enqueue("none-to", "research")
    d.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if all(j.status is JobStatus.COMPLETED for j in queue.list_jobs()):
            break
        time.sleep(0.01)
    d.stop(timeout=None)
    assert queue.list_jobs()[0].status is JobStatus.COMPLETED


def test_within_workspace_serialisation_under_concurrent_workers(
    queue: Queue, root: Path
) -> None:
    _NWRK = 3
    barrier = threading.Barrier(_NWRK, timeout=5.0)

    class BarrierTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            barrier.wait()
            return _make_handover(workspace.task_id, from_team=self.name)

    workspaces = [workspace_mod.create(f"sr {i}", "d", root=root) for i in range(_NWRK)]
    task_ids = [ws.task_id for ws in workspaces]
    for tid in task_ids:
        queue.enqueue(tid, "research")
        queue.enqueue(tid, "research")

    violations: list[str] = []
    stop_monitor = threading.Event()

    def _monitor() -> None:
        while not stop_monitor.is_set():
            running = queue.list_running()
            seen: set[str] = set()
            for job in running:
                if job.task_id in seen:
                    violations.append(job.task_id)
                seen.add(job.task_id)
            stop_monitor.wait(0.005)

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()
    d = Dispatcher(
        queue,
        {"research": BarrierTeam()},
        workspaces_root=root,
        poll_interval=0.01,
        concurrency=_NWRK,
    )
    d.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if all(j.status is JobStatus.COMPLETED for j in queue.list_jobs()):
            break
        time.sleep(0.02)
    d.stop(timeout=5.0)
    stop_monitor.set()
    mon.join(timeout=2.0)

    assert all(j.status is JobStatus.COMPLETED for j in queue.list_jobs())
    assert not violations, f"concurrent same-workspace jobs detected: {violations}"
    for tid in task_ids:
        jobs = sorted(queue.list_jobs(task_id=tid), key=lambda j: j.id)
        assert len(jobs) == 2
        j1, j2 = jobs
        assert j1.finished_at is not None
        assert j2.started_at is not None
        assert j2.started_at >= j1.finished_at, (
            f"{tid}: job2.started_at={j2.started_at} < job1.finished_at={j1.finished_at}"
        )


def test_post_processing_holds_workspace_guard(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("post-proc", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    queue.enqueue(ws.task_id, "research")

    in_post_proc = threading.Event()
    overlap = threading.Event()
    real_increment = workspace_mod.increment_revisit_count

    def slow_increment(ws_arg: Workspace, team_name: str, chain_id: str) -> Workspace:
        in_post_proc.set()
        try:
            time.sleep(0.3)
            return real_increment(ws_arg, team_name, chain_id)
        finally:
            in_post_proc.clear()

    monkeypatch.setattr(workspace_mod, "increment_revisit_count", slow_increment)

    class T:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            if in_post_proc.is_set():
                overlap.set()
            return _make_handover(workspace.task_id, from_team=self.name).model_copy(
                update={"to_team_suggestions": []}
            )

    d = Dispatcher(
        queue,
        {"research": T()},
        workspaces_root=root,
        poll_interval=0.01,
        concurrency=2,
    )
    d.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if all(j.status is JobStatus.COMPLETED for j in queue.list_jobs()):
            break
        time.sleep(0.02)
    d.stop(timeout=5.0)

    assert all(j.status is JobStatus.COMPLETED for j in queue.list_jobs())
    assert not overlap.is_set(), (
        "second team.run started during first job's post-processing; "
        "per-workspace guard must hold across queue.complete + post-processing"
    )


def test_concurrent_workers_run_distinct_workspace_jobs_simultaneously(
    queue: Queue, root: Path
) -> None:
    rendezvous = threading.Barrier(2, timeout=3.0)

    class RendezvousTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            rendezvous.wait()
            return _make_handover(workspace.task_id, from_team=self.name)

    workspace_mod.create("rend-1", "d", root=root)
    workspace_mod.create("rend-2", "d", root=root)
    queue.enqueue("rend-1", "research")
    queue.enqueue("rend-2", "research")
    d = Dispatcher(
        queue,
        {"research": RendezvousTeam()},
        workspaces_root=root,
        poll_interval=0.01,
        concurrency=2,
    )
    d.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(j.status is JobStatus.COMPLETED for j in queue.list_jobs()):
            break
        time.sleep(0.02)
    d.stop(timeout=5.0)
    assert all(j.status is JobStatus.COMPLETED for j in queue.list_jobs())


def test_interrupt_task_returns_false_for_multi_task_workspace_with_no_running_job(
    queue: Queue,
    root: Path,
) -> None:
    ws = workspace_mod.create("mt-stop", "d", root=root)
    _write_multi_task_meta(
        ws,
        [
            {"id": "t1", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "t2", "effort": "medium", "status": "pending", "depends_on": ["t1"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d.interrupt_task(ws.task_id)
    assert result is False


def test_resume_task_is_noop_for_multi_task_workspace_with_no_paused_job(
    queue: Queue,
    root: Path,
) -> None:
    ws = workspace_mod.create("mt-resume", "d", root=root)
    _write_multi_task_meta(
        ws,
        [
            {"id": "t1", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "t2", "effort": "medium", "status": "pending", "depends_on": ["t1"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task(ws.task_id)


async def test_workspace_event_stream_filter_passes_matching_blocks_other(
    queue: Queue, root: Path
) -> None:
    import asyncio

    from alan.routes._deps import workspace_event_stream

    bus = PubSub()
    gen = workspace_event_stream("chan", bus, filter_task_id="task-a")
    collect_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish("chan", TeamEvent("evt1", {"task_id": "task-b"}))
    bus.publish("chan", TeamEvent("evt2", {"task_id": "task-a"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect_task, timeout=1.0)
    await gen.aclose()
    assert "evt2" in chunk
    assert "evt1" not in chunk


async def test_workspace_event_stream_no_filter_passes_all(
    queue: Queue, root: Path
) -> None:
    import asyncio

    from alan.routes._deps import workspace_event_stream

    bus = PubSub()
    gen = workspace_event_stream("chan2", bus)
    collect_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish("chan2", TeamEvent("evtA", {"task_id": "anything"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect_task, timeout=1.0)
    await gen.aclose()
    assert "evtA" in chunk


def test_interrupt_task_handles_missing_workspace(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d.interrupt_task("no-such-workspace-xyz")
    assert result is False


def test_resume_task_handles_missing_workspace(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task("no-such-workspace-xyz")


def test_pubsub_subscribe_all_receives_keyed_and_global_publishes() -> None:
    bus = PubSub()
    seen: list[str] = []
    unsubscribe = bus.subscribe_all(lambda e: seen.append(e.kind))
    bus.publish("task-x", TeamEvent("keyed", {"task_id": "task-x"}))
    bus.publish_global(TeamEvent("global", {}))
    assert seen == ["keyed", "global"]
    unsubscribe()
    bus.publish_global(TeamEvent("after", {}))
    assert seen == ["keyed", "global"]


def test_pubsub_publish_global_skips_keyed_listeners() -> None:
    bus = PubSub()
    keyed: list[str] = []
    bus.subscribe("task-x", lambda e: keyed.append(e.kind))
    bus.publish_global(TeamEvent("global-only", {}))
    assert keyed == []


def test_pubsub_global_fanout_contains_listener_exception() -> None:
    bus = PubSub()
    seen: list[str] = []

    def boom(_e: TeamEvent) -> None:
        raise RuntimeError("listener exploded")

    bus.subscribe_all(boom)
    bus.subscribe_all(lambda e: seen.append(e.kind))
    bus.publish_global(TeamEvent("survives", {}))
    assert seen == ["survives"]


def test_pubsub_unsubscribe_all_unknown_callback_is_noop() -> None:
    bus = PubSub()
    bus._unsubscribe_all(lambda _e: None)  # noqa: SLF001
    bus.subscribe_all(lambda _e: None)
    bus._unsubscribe_all(lambda _e: None)  # noqa: SLF001
