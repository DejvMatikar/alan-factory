from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli import (
    AgentStreamingResult,
    ClaudeCliError,
    OnProcStarted,
    ServerErrorRetryable,
    UsageLimitError,
)
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover
from alan.queue import Job, JobStatus, Queue, QueueError
from alan.teams import _forge_errors
from alan.teams._base import TeamEvent, run_or_resume_node, workflow_lifecycle
from alan.workflow import PendingTaskSpec, result_path, save_node_result
from alan.workspace import Status, Task, TaskStatus, Workspace


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


class _ServerErrorTeam:
    name = "research"
    supports_resume = False

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
        raise ServerErrorRetryable("overloaded: 529")


class _ClientErrorTeam:
    name = "research"
    supports_resume = False

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
        raise ClaudeCliError("bad request: client error")


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


class _ServerErrorThenSuccessTeam:
    name = "research"
    supports_resume = False

    def __init__(self) -> None:
        self._call_count = 0

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
        self._call_count += 1
        if self._call_count == 1:
            raise ServerErrorRetryable("overloaded: 529")
        return _make_handover(ws.task_id)


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


def test_server_error_requeues_job_to_pending_with_future_eligible_at(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at > time.time()


def test_server_error_bumps_retry_count_independently_of_interruption_count(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].server_error_retry_count == 1
    assert jobs[0].interruption_count == 0


def test_server_error_clears_resume_inv_id_on_requeue(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    j = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id="inv_001")
    queue._conn.execute(  # noqa: SLF001 - white-box: pre-set resume_inv_id on pending row
        "UPDATE jobs SET resume_inv_id = 'inv_001' WHERE id = ?", (j.id,)
    )
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].resume_inv_id is None


def test_server_error_preserves_triggered_by_invocation_id(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research", triggered_by_invocation_id="inv_orig")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].triggered_by_invocation_id == "inv_orig"


def test_server_error_does_not_flip_workspace_to_awaiting_input(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is not Status.AWAITING_INPUT


def test_server_error_publishes_job_retry_scheduled_event(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _ServerErrorTeam()
    d = Dispatcher(
        queue, {"research": team}, pubsub=pubsub, workspaces_root=root
    )
    events, unsub = _collect_events(pubsub, ws.task_id)
    d.run_once()
    unsub()
    kinds = [e.kind for e in events]
    assert "job_retry_scheduled" in kinds
    retry_evt = next(e for e in events if e.kind == "job_retry_scheduled")
    assert retry_evt.payload["attempt"] == "1"
    assert "eligible_at" in retry_evt.payload


def test_client_error_is_not_retried(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    team = _ClientErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.FAILED
    assert jobs[0].server_error_retry_count == 0


def test_client_error_flips_workspace_to_awaiting_input(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ClientErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is Status.AWAITING_INPUT


def test_usage_limit_path_unchanged_by_server_error_handling(
    queue: Queue, root: Path
) -> None:
    reset_at = time.time() + 3600.0
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _UsageLimitTeam(reset_at)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    assert queue.paused_until() is not None
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].server_error_retry_count == 0


def test_exhausted_server_error_budget_fails_job(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id="inv_001")
    queue._conn.execute(  # noqa: SLF001 - white-box: set retry count to max
        "UPDATE jobs SET server_error_retry_count = 3 WHERE id = ?", (job.id,)
    )
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.FAILED
    assert "overloaded" in (jobs[0].error or "")


def test_exhausted_server_error_budget_flips_workspace_to_awaiting_input(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id="inv_001")
    queue._conn.execute(  # noqa: SLF001 - white-box: set retry count to max
        "UPDATE jobs SET server_error_retry_count = 3 WHERE id = ?", (job.id,)
    )
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is Status.AWAITING_INPUT


def test_job_in_backoff_not_eligible_to_run(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_with_server_error_backoff(job.id, time.time() + 3600.0)
    assert queue.claim() is None


def test_job_in_backoff_survives_queue_restart(
    db_path: Path, root: Path
) -> None:
    q1 = Queue(db_path=db_path)
    ws = workspace_mod.create("t1", "d", root=root)
    job = q1.enqueue(ws.task_id, "research")
    q1.claim()
    future = time.time() + 3600.0
    q1.requeue_with_server_error_backoff(job.id, future)
    q1.close()
    q2 = Queue(db_path=db_path)
    try:
        jobs = q2.list_jobs()
        assert jobs[0].status is JobStatus.PENDING
        assert jobs[0].eligible_at is not None
        assert abs(jobs[0].eligible_at - future) < 0.01
        assert q2.claim() is None
    finally:
        q2.close()


def test_server_error_retry_succeeds_on_second_attempt(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ServerErrorThenSuccessTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].server_error_retry_count == 1
    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?",
        (time.time() - 1.0, jobs[0].id),
    )
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.COMPLETED
    assert team._call_count == 2


def test_server_error_requeue_fails_gracefully_on_queue_error(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _ServerErrorTeam()
    d = Dispatcher(
        queue, {"research": team}, pubsub=pubsub, workspaces_root=root
    )

    original = queue.requeue_with_server_error_backoff

    def raising(_job_id: int, _eligible_at: float) -> Job:
        raise QueueError("already claimed")

    queue.requeue_with_server_error_backoff = raising  # type: ignore[method-assign, assignment]
    events, unsub = _collect_events(pubsub, ws.task_id)
    try:
        with caplog.at_level("WARNING", logger="alan.dispatcher"):
            d.run_once()
    finally:
        queue.requeue_with_server_error_backoff = original  # type: ignore[method-assign]
        unsub()

    assert any("server_error requeue failed" in r.message for r in caplog.records)
    assert not any(e.kind == "job_retry_scheduled" for e in events)


class _RaceServerErrorTeam(_ServerErrorTeam):
    def __init__(self, queue: Queue) -> None:
        self._queue = queue

    def run(self, *_args: object, **_kwargs: object) -> Handover:
        for running in self._queue.list_running():
            self._queue.mark_interrupted_and_fail(running.id, "interrupted_by_restart")
        raise ServerErrorRetryable("upstream 529")


def test_server_error_recovers_job_failed_out_from_under_the_worker(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _RaceServerErrorTeam(queue)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)

    d.run_once()
    unsub()

    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.server_error_retry_count == 1
    assert requeued.eligible_at is not None
    assert requeued.eligible_at > time.time()
    assert requeued.resume_inv_id is None
    assert any(e.kind == "job_retry_scheduled" for e in events)


def test_server_error_recovered_job_still_honours_the_retry_budget(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _RaceServerErrorTeam(queue)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    for expected in (1, 2, 3):
        d.run_once()
        job = queue.list_jobs()[0]
        assert job.server_error_retry_count == expected
        queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
            "UPDATE jobs SET eligible_at = ? WHERE id = ?",
            (time.time() - 1.0, job.id),
        )

    d.run_once()
    assert queue.list_jobs()[0].status is JobStatus.FAILED


def test_server_error_does_not_schedule_a_retry_for_an_already_completed_job(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()

    class _CompletingTeam(_ServerErrorTeam):
        def run(self, *_args: object, **_kwargs: object) -> Handover:
            for running in queue.list_running():
                queue.complete(running.id)
            raise ServerErrorRetryable("upstream 529")

    team = _CompletingTeam()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.task_id)

    d.run_once()
    unsub()

    assert queue.list_jobs()[0].status is JobStatus.COMPLETED
    assert not any(e.kind == "job_retry_scheduled" for e in events)
    assert not any(e.kind == "job_stranded" for e in events)
    assert workspace_mod.read(ws.task_id, root=root).meta.status is not Status.AWAITING_INPUT


def test_server_error_strands_and_parks_when_recovery_also_fails(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _RaceServerErrorTeam(queue)
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)

    def raising(*_args: object, **_kwargs: object) -> Job:
        raise QueueError("row vanished")

    queue.requeue_failed_with_resume = raising  # type: ignore[method-assign]
    events, unsub = _collect_events(pubsub, ws.task_id)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d.run_once()
    unsub()

    assert any("server_error requeue failed" in r.message for r in caplog.records)
    stranded = next(e for e in events if e.kind == "job_stranded")
    assert stranded.payload["reason"] == "server_error"
    assert not any(e.kind == "job_retry_scheduled" for e in events)
    assert workspace_mod.read(ws.task_id, root=root).meta.status is Status.AWAITING_INPUT


def test_server_error_does_not_cause_spurious_job_failed_event(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    team = _ServerErrorTeam()
    d = Dispatcher(
        queue, {"research": team}, pubsub=pubsub, workspaces_root=root
    )
    events, unsub = _collect_events(pubsub, ws.task_id)
    d.run_once()
    unsub()
    assert not any(e.kind == "job_failed" for e in events)


def test_positive_float_env_non_numeric_falls_back_to_default(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_SERVER_ERROR_BASE_DELAY_S", "not-a-number")
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at > time.time() + 100


def test_positive_float_env_non_positive_falls_back_to_default(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_SERVER_ERROR_BASE_DELAY_S", "-5.0")
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at > time.time() + 100


class _MultiNodeServerErrorTeam:
    name = "research"
    supports_resume = False

    def __init__(self) -> None:
        self._calls: list[tuple[str, str | None]] = []

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
        inv_id: str = "",
        resume_inv_id: str | None = None,
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self._calls.append((inv_id, resume_inv_id))
        if len(self._calls) == 1:
            raise ServerErrorRetryable("overloaded: 529")
        return _make_handover(ws.task_id)


class _ResumeCapableServerErrorTeam(_ServerErrorTeam):
    supports_resume = True


def test_exhausted_server_error_recoverable_sets_resume_inv_id_and_resets_nodes(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t-multi", "d", root=root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[
            PendingTaskSpec(id="upstream", role="upstream", model="sonnet"),
            PendingTaskSpec(id="current", role="current", model="sonnet"),
        ],
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("upstream", role="upstream", model="sonnet")
    save_node_result(
        ws, inv_id, "upstream", role="upstream", model="sonnet", result_text="upstream-ok"
    )
    graph.complete_node("upstream")
    graph.start_node("current", role="current", model="sonnet")
    graph.fail_node("current", error="API Error: 529 Overloaded")
    workflow_mod.save_workflow(ws, graph.doc)

    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue._conn.execute(  # noqa: SLF001 - white-box: set retry count to max
        "UPDATE jobs SET server_error_retry_count = 3 WHERE id = ?", (job.id,)
    )
    team = _ResumeCapableServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()

    failed = queue.list_jobs()[0]
    assert failed.status is JobStatus.FAILED
    assert failed.resume_inv_id == inv_id
    assert queue.list_resumable_failed_by_workspace(ws.workspace_id)
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    by_id = {t.id: t.status for t in doc.tasks}
    assert by_id["upstream"] == "success"
    assert by_id["current"] == "pending"


def test_exhausted_server_error_non_recoverable_stays_plain_failure(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id="inv_001")
    queue._conn.execute(  # noqa: SLF001 - white-box: set retry count to max
        "UPDATE jobs SET server_error_retry_count = 3 WHERE id = ?", (job.id,)
    )
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()
    failed = queue.list_jobs()[0]
    assert failed.status is JobStatus.FAILED
    assert failed.resume_inv_id is None


def test_server_error_retry_multi_node_reuses_upstream_cache_reruns_failed_fresh(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t-multi", "d", root=root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[
            PendingTaskSpec(id="upstream", role="upstream", model="sonnet"),
            PendingTaskSpec(id="current", role="current", model="sonnet"),
        ],
    )
    save_node_result(
        ws, inv_id, "upstream", role="upstream", model="sonnet", result_text="upstream-ok"
    )
    upstream_path = result_path(ws, inv_id, "upstream")
    current_path = result_path(ws, inv_id, "current")

    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    team = _MultiNodeServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d.run_once()
    assert len(team._calls) == 1
    assert queue.list_jobs()[0].status is JobStatus.PENDING

    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?", (time.time() - 1.0, job.id)
    )
    d.run_once()
    assert len(team._calls) == 2

    first_inv, first_resume = team._calls[0]
    retry_inv, retry_resume = team._calls[1]
    assert first_inv == inv_id
    assert retry_inv == inv_id
    assert retry_resume is None
    assert upstream_path.exists()
    assert not current_path.exists()


def test_server_error_retry_does_not_cancel_sibling_pending_job(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("multi-sib", "d", root=root, tasks=[
        Task(id="task-a", status=TaskStatus.PENDING),
        Task(id="task-b", status=TaskStatus.PENDING),
    ])
    queue.enqueue("task-a", "research", workspace_id=ws.workspace_id)
    queue.enqueue("task-b", "research", workspace_id=ws.workspace_id)

    pubsub = PubSub()
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, pubsub=pubsub, workspaces_root=root)
    events, unsub = _collect_events(pubsub, ws.workspace_id)
    d.run_once()
    unsub()

    sibling_jobs = queue.list_jobs(task_id="task-b")
    assert len(sibling_jobs) == 1
    assert sibling_jobs[0].status is JobStatus.PENDING

    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status is Status.ACTIVE

    retry_evts = [e for e in events if e.kind == "job_retry_scheduled"]
    assert len(retry_evts) == 1
    payload = retry_evts[0].payload
    assert "attempt" in payload
    assert "eligible_at" in payload
    assert payload["team"] == "research"


def test_requeue_pending_resets_server_error_retry_count(queue: Queue) -> None:
    j = queue.enqueue("t", "research")
    queue.claim()
    queue.requeue_with_server_error_backoff(j.id, time.time() + 3600.0)
    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?", (time.time() - 1.0, j.id)
    )
    queue.claim()
    requeued = queue.requeue_pending(j.id)
    assert requeued.server_error_retry_count == 0


def test_requeue_for_resume_resets_server_error_retry_count(queue: Queue) -> None:
    j = queue.enqueue("t", "research")
    queue.claim()
    queue.requeue_with_server_error_backoff(j.id, time.time() + 3600.0)
    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?", (time.time() - 1.0, j.id)
    )
    queue.claim()
    requeued = queue.requeue_for_resume(j.id, "inv_001")
    assert requeued.server_error_retry_count == 0


def test_requeue_failed_for_resume_resets_server_error_retry_count(queue: Queue) -> None:
    j = queue.enqueue("t", "research")
    queue.claim()
    queue.requeue_with_server_error_backoff(j.id, time.time() + 3600.0)
    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?", (time.time() - 1.0, j.id)
    )
    queue.claim()
    queue.fail(j.id, "exhausted")
    requeued = queue.requeue_failed_for_resume(j.id)
    assert requeued.server_error_retry_count == 0


class _RealNodeServerErrorTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return [PendingTaskSpec(id="research", role="research", model="sonnet")]

    def run(
        self,
        ws: Workspace,
        _prior: Handover | None,
        emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "",
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        with workflow_lifecycle(ws, team_name="research", inv_id=inv_id, emit=emit) as graph:
            run_or_resume_node(
                ws, graph, inv_id, emit,
                node_id="research",
                role="research",
                model="sonnet",
                depends_on=[],
                system_prompt="test",
                user_text="test",
                on_proc_started=on_proc_started,
            )
        return _make_handover(ws.task_id)


def test_server_error_reset_called_before_requeue(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t-oserr", "d", root=root)
    queue.enqueue(ws.task_id, "research")

    called: list[tuple[object, ...]] = []

    def _spy(*args: object, **_kwargs: object) -> None:
        called.append(args)

    monkeypatch.setattr("alan.dispatcher.workflow_mod.reset_failed_nodes_for_rerun", _spy)
    team = _ServerErrorTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    d.run_once()

    assert len(called) == 1
    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].server_error_retry_count == 1


def test_real_node_server_error_retry_does_not_crash_on_duplicate_node_id(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t-realnode", "d", root=root)
    team = _RealNodeServerErrorTeam()
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="research", tasks=team.plan_workflow(ws, None)
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)

    call_count = 0

    def _fake_streaming(*_args: object, **_kwargs: object) -> AgentStreamingResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ServerErrorRetryable("overloaded: 529")
        return AgentStreamingResult(result_text="done")

    monkeypatch.setattr("alan.teams._base.run_agent_streaming", _fake_streaming)
    monkeypatch.setenv("ALAN_SERVER_ERROR_BASE_DELAY_S", "0.001")

    d = Dispatcher(queue, {"research": team}, workspaces_root=root)

    d.run_once()

    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].server_error_retry_count == 1

    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    node_statuses = {t.id: t.status for t in doc.tasks}
    assert node_statuses["research"] == "pending"

    queue._conn.execute(  # noqa: SLF001 - white-box: expire backoff so claim sees it eligible
        "UPDATE jobs SET eligible_at = ? WHERE id = ?",
        (time.time() - 1.0, job.id),
    )

    d.run_once()

    jobs = queue.list_jobs()
    assert jobs[0].status is JobStatus.COMPLETED
    assert call_count == 2


class _ForgeTransientTeam:
    name = "research"
    supports_resume = False

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
        raise _forge_errors.ForgeTransientError("gh pr view failed (rc=1): dial tcp")


def test_forge_transient_subclass_rides_server_error_backoff(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    job = queue.enqueue(ws.task_id, "research")
    d = Dispatcher(queue, {"research": _ForgeTransientTeam()}, workspaces_root=root)
    d.run_once()
    jobs = queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == job.id
    assert jobs[0].status is JobStatus.PENDING
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at > time.time()
    assert jobs[0].server_error_retry_count == 1
