from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alan import workflow_interrupt
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted, UsageLimitError
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover
from alan.queue import FORGE_DEGRADED_META_KEY, Job, JobStatus, Queue, QueueError
from alan.teams import _forge_errors, _forge_quota
from alan.teams._base import TeamEvent
from alan.workflow import (
    PendingTaskSpec,
    WorkflowGraph,
    create_invocation_dir,
    load_workflow,
    save_workflow,
    workflow_path,
)
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


def _make_handover(task_id: str) -> Handover:
    from datetime import UTC, datetime

    return Handover(
        handover_id=f"handover_{task_id}_test",
        handover_type="forward",
        task_id=task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


class _UsageLimitTeam:
    name = "research"
    supports_resume = False

    def __init__(self, reset_at: float) -> None:
        self._reset_at = reset_at

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
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise UsageLimitError("hit your limit", self._reset_at)


class _UsageLimitResumableTeam(_UsageLimitTeam):
    supports_resume = True


def _collect_events(
    pubsub: PubSub, task_id: str
) -> tuple[list[TeamEvent], Callable[[], None]]:
    events: list[TeamEvent] = []
    lock = threading.Lock()

    def on_event(e: TeamEvent) -> None:
        with lock:
            events.append(e)

    unsub = pubsub.subscribe(task_id, on_event)
    return events, unsub


def test_usage_limit_pauses_queue(queue: Queue, root: Path) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _UsageLimitTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    result = queue.paused_until()
    assert result is not None
    assert abs(result - reset_at) < 1.0


def test_usage_limit_requeues_pending_for_non_resume_team(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    team = _UsageLimitTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].resume_inv_id is None
    assert jobs[0].interruption_count == 1


def test_usage_limit_requeues_for_resume_for_resume_team(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _UsageLimitResumableTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].resume_inv_id is not None
    assert jobs[0].interruption_count == 1


def test_usage_limit_does_not_flip_workspace_to_awaiting_input(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _UsageLimitTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is not Status.AWAITING_INPUT


def test_usage_limit_publishes_queue_paused_event(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _UsageLimitTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)
    d.run_once()
    unsub()
    kinds = [e.kind for e in events]
    assert "queue_paused" in kinds
    paused_evt = next(e for e in events if e.kind == "queue_paused")
    assert paused_evt.payload["mode"] == "pending"
    assert "reset_at" in paused_evt.payload


def test_usage_limit_resume_team_publishes_mode_resume(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _UsageLimitResumableTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)
    d.run_once()
    unsub()
    paused_evt = next(e for e in events if e.kind == "queue_paused")
    assert paused_evt.payload["mode"] == "resume"


def test_handle_usage_limit_queue_error_logs_warning(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    team = _UsageLimitTeam(reset_at)
    pubsub = PubSub()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)

    original_requeue = queue.requeue_pending

    def raising_requeue(_job_id: int) -> Job:
        raise QueueError("already claimed")

    queue.requeue_pending = raising_requeue  # type: ignore[method-assign, assignment]
    events, unsub = _collect_events(pubsub, ws.task_id)
    try:
        with caplog.at_level("WARNING", logger="alan.dispatcher"):
            d.run_once()
    finally:
        queue.requeue_pending = original_requeue  # type: ignore[method-assign]
        unsub()

    assert any("usage_limit requeue failed" in r.message for r in caplog.records)
    assert not [e for e in events if e.kind == "queue_paused"]
    _ = job


def _seed_failed_workflow_with_session(
    ws: Workspace, *, session_id: str = "sid-x"
) -> str:
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("research", role="research", model="sonnet")
    g.set_session_id("research", session_id)
    g.fail_node("research", error="claude CLI exited 1: hit your limit")
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    return inv_id


def test_usage_limit_recovers_failed_node_to_interrupted_on_disk(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    team = _UsageLimitResumableTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d._call_team(claimed, ws, team, None, inv_id)

    doc = load_workflow(workflow_path(ws, inv_id))
    assert doc.status == "interrupted"
    assert doc.tasks[0].status == "interrupted"
    assert doc.tasks[0].claude_session_id == "sid-x"
    assert queue.paused_until() is not None
    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id


def test_usage_limit_interrupt_on_disk_oserror_logs_and_still_requeues(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    team = _UsageLimitResumableTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    def raising_interrupt(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(
        "alan._dispatcher_usage_limit.workflow_interrupt.interrupt_workflow_on_disk",
        raising_interrupt,
    )
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._call_team(claimed, ws, team, None, inv_id)

    assert any(
        "interrupt_workflow_on_disk failed" in r.message for r in caplog.records
    )
    assert queue.paused_until() is not None
    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id


class _RaceUsageLimitTeam(_UsageLimitResumableTeam):
    def __init__(self, reset_at: float, queue: Queue, error: str) -> None:
        super().__init__(reset_at)
        self._queue = queue
        self._error = error

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        for running in self._queue.list_running():
            self._queue.mark_interrupted_and_fail(running.id, self._error)
        raise UsageLimitError("hit your limit", self._reset_at)


def test_usage_limit_recovers_job_failed_out_from_under_the_worker(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    team = _RaceUsageLimitTeam(reset_at, queue, "interrupted_by_restart")
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d._call_team(claimed, ws, team, None, inv_id)

    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id
    assert requeued.error is None
    doc = load_workflow(workflow_path(ws, inv_id))
    assert doc.tasks[0].claude_session_id == "sid-x"
    assert workspace_mod.read(ws.task_id, root=root).meta.status is not Status.AWAITING_INPUT


def test_usage_limit_recovered_job_is_claimable_once_the_pause_lapses(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    team = _RaceUsageLimitTeam(time.time() + 0.5, queue, "interrupted_by_restart")
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d._call_team(claimed, ws, team, None, inv_id)
    assert queue.claim() is None

    time.sleep(0.6)
    reclaimed = queue.claim()
    assert reclaimed is not None
    assert reclaimed.resume_inv_id == inv_id


def test_usage_limit_recovery_publishes_queue_paused(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    pubsub = PubSub()
    team = _RaceUsageLimitTeam(time.time() + 3600.0, queue, "interrupted_by_restart")
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)

    d._call_team(claimed, ws, team, None, inv_id)
    unsub()

    paused_evt = next(e for e in events if e.kind == "queue_paused")
    assert paused_evt.payload["mode"] == "resume"
    assert not [e for e in events if e.kind == "job_stranded"]


def test_usage_limit_recovery_clears_resume_id_for_non_resume_team(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    queue.mark_interrupted_and_fail(claimed.id, "boom", resume_inv_id=inv_id)
    queue.requeue_failed_for_resume(claimed.id)
    assert queue.claim() is not None

    class _NonResumeRaceTeam(_RaceUsageLimitTeam):
        supports_resume = False

    team = _NonResumeRaceTeam(time.time() + 3600.0, queue, "interrupted_by_restart")
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d._call_team(queue.get_job(claimed.id), ws, team, None, inv_id)  # type: ignore[arg-type]

    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id is None


def test_usage_limit_skips_requeue_when_job_already_completed(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    claimed_id = claimed.id

    class _CompletingTeam(_UsageLimitResumableTeam):
        def run(self, *_args: object, **_kwargs: object) -> Handover:
            queue.complete(claimed_id)
            raise UsageLimitError("hit your limit", self._reset_at)

    team = _CompletingTeam(time.time() + 3600.0)
    pubsub = PubSub()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._call_team(claimed, ws, team, None, inv_id)
    unsub()

    assert queue.list_jobs()[0].status is JobStatus.COMPLETED
    assert not [e for e in events if e.kind == "job_stranded"]
    assert not [r for r in caplog.records if "requeue failed" in r.message]
    assert workspace_mod.read(ws.task_id, root=root).meta.status is not Status.AWAITING_INPUT
    assert not [e for e in events if e.kind == "queue_paused"]


def test_usage_limit_strands_and_parks_when_recovery_also_fails(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    pubsub = PubSub()
    team = _RaceUsageLimitTeam(time.time() + 3600.0, queue, "interrupted_by_restart")
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)

    def raising_recovery(*_args: object, **_kwargs: object) -> Job:
        raise QueueError("row vanished")

    queue.requeue_failed_with_resume = raising_recovery  # type: ignore[method-assign]
    events, unsub = _collect_events(pubsub, ws.task_id)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._call_team(claimed, ws, team, None, inv_id)
    unsub()

    assert any("usage_limit requeue failed" in r.message for r in caplog.records)
    stranded = next(e for e in events if e.kind == "job_stranded")
    assert stranded.payload["reason"] == "usage_limit"
    assert workspace_mod.read(ws.task_id, root=root).meta.status is Status.AWAITING_INPUT


def test_usage_limit_strands_when_job_row_is_gone(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    pubsub = PubSub()

    class _VanishingTeam(_UsageLimitResumableTeam):
        def run(self, *_args: object, **_kwargs: object) -> Handover:
            queue.delete_jobs(ws.task_id)
            raise UsageLimitError("hit your limit", self._reset_at)

    team = _VanishingTeam(time.time() + 3600.0)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._call_team(claimed, ws, team, None, inv_id)
    unsub()

    assert any("usage_limit requeue failed" in r.message for r in caplog.records)
    assert [e for e in events if e.kind == "job_stranded"]


class _MidStreamRejectionTeam(_UsageLimitResumableTeam):
    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise UsageLimitError("rate limit rejected mid-stream", self._reset_at)


def test_mid_stream_rejection_pauses_globally_and_requeues_resumable_with_no_eligible_at(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    pubsub = PubSub()
    team = _MidStreamRejectionTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)

    d._call_team(claimed, ws, team, None, inv_id)
    unsub()

    assert queue.claim() is None
    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id
    assert requeued.eligible_at is None
    paused_evt = next(e for e in events if e.kind == "queue_paused")
    assert paused_evt.payload["mode"] == "resume"
    paused_until = queue.paused_until()
    assert paused_until is not None
    assert abs(paused_until - reset_at) < 1.0


def test_worker_reclaims_job_after_pause_expires(
    queue: Queue, root: Path
) -> None:
    called = [0]
    pause_window = 0.5

    class _CountingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self, _ws: Workspace, _prior: Handover | None
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            ws: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            if called[0] == 0:
                called[0] += 1
                raise UsageLimitError("hit your limit", time.time() + pause_window)
            called[0] += 1
            return _make_handover(ws.task_id)

    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _CountingTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d.run_once()
    assert queue.paused_until() is not None

    time.sleep(pause_window + 0.1)
    assert queue.paused_until() is None

    d.run_once()
    assert called[0] == 2
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.COMPLETED


def test_loop_shortens_sleep_to_pause_expiry(
    db_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    q = Queue(db_path=db_path)
    q.pause_until(time.time() + 0.5)

    wait_calls: list[float] = []
    d = Dispatcher(q, {}, workspaces_root=root, poll_interval=1.0)

    def mock_wait(timeout: float | None = None) -> bool:
        if timeout is not None:
            wait_calls.append(timeout)
        d._stop.set()
        return True

    monkeypatch.setattr(d._stop, "wait", mock_wait)
    d._loop()
    q.close()

    assert len(wait_calls) == 1
    assert 0 < wait_calls[0] <= d._poll_interval


class _ForgeRateLimitedTeam:
    name = "research"
    supports_resume = False

    def __init__(self, reset_at: float) -> None:
        self._reset_at = reset_at

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
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise _forge_errors.ForgeRateLimited("github rate limited", self._reset_at)


def test_forge_rate_limited_subclass_pauses_queue_and_requeues(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    paused = queue.paused_until()
    assert paused is not None
    assert abs(paused - reset_at) < 1.0
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.PENDING


def test_forge_rate_limited_writes_degraded_meta(queue: Queue, root: Path) -> None:
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    raw = queue.get_meta(FORGE_DEGRADED_META_KEY)
    assert raw is not None
    data = json.loads(raw)
    assert data["reset_at"] == pytest.approx(reset_at)
    assert data["detail"] == "github rate limited"


def test_plain_usage_limit_leaves_degraded_meta_unset(queue: Queue, root: Path) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(queue, {"research": _UsageLimitTeam(reset_at)}, workspaces_root=root)
    d.run_once()
    assert queue.get_meta(FORGE_DEGRADED_META_KEY) is None


def test_forge_rate_limited_interrupt_carries_github_error_text(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def capture(_ws: Workspace, _inv: str, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(workflow_interrupt, "interrupt_workflow_on_disk", capture)
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue,
        {"research": _ForgeRateLimitedTeam(time.time() + 900.0)},
        workspaces_root=root,
    )
    d.run_once()
    assert captured["error"] == "github rate limited"


def test_plain_usage_limit_interrupt_keeps_usage_error_text(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def capture(_ws: Workspace, _inv: str, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(workflow_interrupt, "interrupt_workflow_on_disk", capture)
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _UsageLimitTeam(time.time() + 3600.0)}, workspaces_root=root
    )
    d.run_once()
    assert captured["error"] == "usage limit reached"


def test_forge_degraded_meta_not_shortened_by_earlier_reset(
    queue: Queue, root: Path
) -> None:
    later = time.time() + 3000.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": later, "detail": "first"})
    )
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue,
        {"research": _ForgeRateLimitedTeam(time.time() + 900.0)},
        workspaces_root=root,
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert data["reset_at"] == pytest.approx(later)
    assert data["detail"] == "first"


def test_forge_degraded_meta_extended_by_later_reset(queue: Queue, root: Path) -> None:
    queue.set_meta(
        FORGE_DEGRADED_META_KEY,
        json.dumps({"reset_at": time.time() + 60.0, "detail": "old"}),
    )
    later = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(later)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert data["reset_at"] == pytest.approx(later)


def test_forge_degraded_meta_overwrites_malformed(queue: Queue, root: Path) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, "not-json{{{")
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert data["reset_at"] == pytest.approx(reset_at)


def test_forge_rate_limited_writes_degraded_meta_with_quota_headroom(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=5000, cost=1, remaining=42, reset_at=None, outcome="ok",
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: sample)
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert data["remaining"] == 42
    assert data["limit"] == 5000


def test_forge_rate_limited_writes_degraded_meta_without_quota_headroom_when_unknown(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: None)
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert "remaining" not in data
    assert "limit" not in data


def test_forge_rate_limited_writes_degraded_meta_omits_partial_quota_sample(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=None, cost=None, remaining=None, reset_at=None, outcome="error",
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: sample)
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert "remaining" not in data
    assert "limit" not in data


def test_forge_rate_limited_writes_degraded_meta_omits_when_only_limit_known(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=5000, cost=1, remaining=None, reset_at=None, outcome="error",
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: sample)
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert "remaining" not in data
    assert "limit" not in data


def test_forge_rate_limited_writes_degraded_meta_omits_when_only_remaining_known(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=None, cost=1, remaining=42, reset_at=None, outcome="error",
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: sample)
    reset_at = time.time() + 900.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()
    data = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    assert "remaining" not in data
    assert "limit" not in data


def _trip_forge_rate_limit(queue: Queue, root: Path, reset_at: float) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ForgeRateLimitedTeam(reset_at)}, workspaces_root=root
    )
    d.run_once()


def _stored_forge_marker(queue: Queue) -> dict[str, object]:
    marker: dict[str, object] = json.loads(queue.get_meta(FORGE_DEGRADED_META_KEY) or "{}")
    return marker


def test_forge_degraded_meta_written_with_only_reset_and_detail_when_headroom_unknown(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: None)
    reset_at = time.time() + 900.0
    _trip_forge_rate_limit(queue, root, reset_at)
    assert _stored_forge_marker(queue) == {"reset_at": reset_at, "detail": "github rate limited"}


def test_forge_degraded_meta_keeps_the_established_key_order_with_headroom(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _forge_quota.QuotaSample(
        resource="graphql", call_site=_forge_quota.PR_FEEDBACK_CALL_SITE,
        limit=5000, cost=1, remaining=42, reset_at=None, outcome="ok",
    )
    monkeypatch.setattr(_forge_quota, "last_sample", lambda _cs: sample)
    _trip_forge_rate_limit(queue, root, time.time() + 900.0)
    assert list(_stored_forge_marker(queue)) == ["reset_at", "detail", "remaining", "limit"]


@pytest.mark.parametrize("stored", ["[1, 2]", "null", "42", '"text"', "true"])
def test_forge_degraded_meta_overwrites_non_object_json_and_still_pauses_the_queue(
    queue: Queue, root: Path, stored: str,
) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, stored)
    reset_at = time.time() + 900.0
    _trip_forge_rate_limit(queue, root, reset_at)
    assert _stored_forge_marker(queue)["reset_at"] == reset_at
    assert queue.paused_until() == pytest.approx(reset_at)
    assert queue.list_jobs()[0].status is JobStatus.PENDING


@pytest.mark.parametrize(
    "stored", ['{"detail": "no reset"}', '{"reset_at": "soon"}', '{"reset_at": null}', "{}"]
)
def test_forge_degraded_meta_overwrites_and_warns_when_reset_at_is_unusable(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture, stored: str,
) -> None:
    queue.set_meta(FORGE_DEGRADED_META_KEY, stored)
    reset_at = time.time() + 900.0
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        _trip_forge_rate_limit(queue, root, reset_at)
    assert _stored_forge_marker(queue)["reset_at"] == reset_at
    assert any("overwriting malformed" in r.message for r in caplog.records)


def test_forge_degraded_meta_kept_when_stored_reset_equals_the_new_one(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    reset_at = time.time() + 900.0
    queue.set_meta(
        FORGE_DEGRADED_META_KEY, json.dumps({"reset_at": reset_at, "detail": "first"})
    )
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        _trip_forge_rate_limit(queue, root, reset_at)
    assert _stored_forge_marker(queue)["detail"] == "first"
    assert not [r for r in caplog.records if "overwriting malformed" in r.message]


def test_forge_degraded_meta_with_non_text_detail_still_guards_a_later_reset(
    queue: Queue, root: Path,
) -> None:
    stored = json.dumps({"reset_at": time.time() + 3000.0, "detail": None})
    queue.set_meta(FORGE_DEGRADED_META_KEY, stored)
    _trip_forge_rate_limit(queue, root, time.time() + 900.0)
    assert queue.get_meta(FORGE_DEGRADED_META_KEY) == stored
