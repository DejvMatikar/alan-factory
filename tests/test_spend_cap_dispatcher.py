from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted, SpendCapReached, UsageLimitError
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover
from alan.queue import SPEND_CAP_STOP_META_KEY, Job, JobStatus, Queue, QueueError
from alan.teams import _forge_errors
from alan.teams._base import TeamEvent
from alan.usage_limit import SPEND_CAP_HOLD_EPOCH
from alan.workflow import PendingTaskSpec, load_workflow, workflow_path
from alan.workspace import Status, Workspace
from tests.test_dispatcher_usage_limit import _collect_events, _seed_failed_workflow_with_session

_REGAIN = "2026-10-01T00:00:00+00:00"
_PROVIDER_BODY = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-10-01 at 00:00 UTC. org-9f3a-private-detail"
)


class _RaisingTeam:
    name = "research"
    supports_resume = False

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc if exc is not None else SpendCapReached(_PROVIDER_BODY, _REGAIN)
        self.runs = 0

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
        self.runs += 1
        raise self.exc


class _ResumableRaisingTeam(_RaisingTeam):
    supports_resume = True


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def _marker(queue: Queue) -> dict[str, object]:
    raw = queue.get_meta(SPEND_CAP_STOP_META_KEY)
    assert raw is not None
    loaded = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def _tripped(queue: Queue, root: Path, team: _RaisingTeam) -> tuple[Dispatcher, Workspace]:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    dispatcher = Dispatcher(queue, {"research": team}, workspaces_root=root)
    dispatcher.run_once()
    return dispatcher, ws


def test_spend_cap_stops_the_queue_at_the_sentinel_hold(queue: Queue, root: Path) -> None:
    _tripped(queue, root, _RaisingTeam())
    assert queue.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_spend_cap_records_the_regain_date_marker(queue: Queue, root: Path) -> None:
    _tripped(queue, root, _RaisingTeam())
    marker = _marker(queue)
    assert marker["regain_at"] == _REGAIN
    assert isinstance(marker["stopped_at"], float)


def test_spend_cap_without_a_regain_date_records_null(queue: Queue, root: Path) -> None:
    _tripped(queue, root, _RaisingTeam(SpendCapReached("capped", None)))
    assert _marker(queue)["regain_at"] is None


def test_spend_cap_persists_no_provider_text_anywhere_in_the_queue(
    queue: Queue, root: Path
) -> None:
    _tripped(queue, root, _RaisingTeam())
    stored = json.dumps([tuple(row) for row in queue.run("SELECT * FROM queue_meta")])
    stored += json.dumps([tuple(row) for row in queue.run("SELECT * FROM jobs")], default=str)
    assert "org-9f3a-private-detail" not in stored
    assert "You have reached" not in stored


def test_spend_cap_requeues_the_tripping_job_and_never_fails_it(
    queue: Queue, root: Path
) -> None:
    _tripped(queue, root, _RaisingTeam())
    (job,) = queue.list_jobs()
    assert job.status is JobStatus.PENDING
    assert job.error is None
    assert job.resume_inv_id is None
    assert job.interruption_count == 1


def test_spend_cap_requeues_a_resumable_team_for_resume(queue: Queue, root: Path) -> None:
    _tripped(queue, root, _ResumableRaisingTeam())
    (job,) = queue.list_jobs()
    assert job.status is JobStatus.PENDING
    assert job.resume_inv_id is not None
    assert job.error is None


def test_spend_cap_leaves_the_workspace_active(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    before = workspace_mod.read(ws.task_id, root=root).meta.status
    queue.enqueue(ws.task_id, "research")
    Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root).run_once()
    after = workspace_mod.read(ws.task_id, root=root).meta.status
    assert after is before
    assert after is not Status.AWAITING_INPUT


def test_no_queued_work_is_claimed_while_stopped(queue: Queue, root: Path) -> None:
    dispatcher, _ws = _tripped(queue, root, _RaisingTeam())
    assert dispatcher.run_once() is False
    assert queue.claim() is None


def test_tripping_job_is_claimable_after_resume(queue: Queue, root: Path) -> None:
    _tripped(queue, root, _RaisingTeam())
    (tripping,) = queue.list_jobs()
    queue.clear_pause()
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == tripping.id


def test_cap_still_applying_after_resume_trips_the_stop_again(
    queue: Queue, root: Path
) -> None:
    team = _RaisingTeam()
    dispatcher, _ws = _tripped(queue, root, team)
    queue.clear_pause()
    assert queue.get_meta(SPEND_CAP_STOP_META_KEY) is None
    assert dispatcher.run_once() is True
    assert team.runs == 2
    assert queue.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert _marker(queue)["regain_at"] == _REGAIN
    assert queue.list_jobs()[0].status is JobStatus.PENDING


def test_spend_cap_publishes_queue_paused_with_the_hold(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    dispatcher = Dispatcher(
        queue, {"research": _RaisingTeam()}, pubsub=pubsub, workspaces_root=root
    )
    events, unsub = _collect_events(pubsub, ws.task_id)
    dispatcher.run_once()
    unsub()
    (paused,) = [e for e in events if e.kind == "queue_paused"]
    assert paused.payload["reset_at"] == str(SPEND_CAP_HOLD_EPOCH)
    assert paused.payload["mode"] == "pending"


def _two_running_jobs(queue: Queue, root: Path) -> tuple[Job, Workspace, Job, Workspace]:
    first = workspace_mod.create("t1", "d", root=root)
    second = workspace_mod.create("t2", "d", root=root)
    queue.enqueue(first.task_id, "research")
    queue.enqueue(second.task_id, "research")
    job_a, job_b = queue.claim(), queue.claim()
    assert job_a is not None
    assert job_b is not None
    return job_a, first, job_b, second


@pytest.mark.parametrize(
    "ordinary",
    [
        pytest.param(
            lambda: UsageLimitError("hit your limit", time.time() + 60.0), id="usage-limit"
        ),
        pytest.param(
            lambda: _forge_errors.ForgeRateLimited("gh limited", time.time() + 900.0),
            id="github-rate-limit",
        ),
    ],
)
def test_ordinary_pause_tripping_during_a_stop_cannot_end_or_shorten_it(
    queue: Queue, root: Path, ordinary: Callable[[], Exception]
) -> None:
    job_a, ws_a, job_b, ws_b = _two_running_jobs(queue, root)
    spend_team = _RaisingTeam()
    ordinary_team = _RaisingTeam(ordinary())
    dispatcher = Dispatcher(queue, {"research": spend_team}, workspaces_root=root)
    dispatcher._call_team(job_a, ws_a, spend_team, None, "inv_001")
    dispatcher._call_team(job_b, ws_b, ordinary_team, None, "inv_001")
    assert queue.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert _marker(queue)["regain_at"] == _REGAIN


def test_spend_cap_recovers_a_job_failed_out_from_under_the_worker(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    inv_id = _seed_failed_workflow_with_session(ws)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None

    class _RaceTeam(_ResumableRaisingTeam):
        def run(self, *_args: object, **_kwargs: object) -> Handover:
            for running in queue.list_running():
                queue.mark_interrupted_and_fail(running.id, "interrupted_by_restart")
            raise self.exc

    team = _RaceTeam()
    dispatcher = Dispatcher(queue, {"research": team}, workspaces_root=root)
    dispatcher._call_team(claimed, ws, team, None, inv_id)
    (job,) = queue.list_jobs()
    assert job.status is JobStatus.PENDING
    assert job.resume_inv_id == inv_id
    assert queue.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert load_workflow(workflow_path(ws, inv_id)).status == "interrupted"


def test_spend_cap_requeue_failure_still_stops_the_queue_and_suppresses_the_event(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    pubsub = PubSub()
    dispatcher = Dispatcher(
        queue, {"research": _RaisingTeam()}, pubsub=pubsub, workspaces_root=root
    )

    def raising_requeue(_job_id: int) -> Job:
        raise QueueError("already claimed")

    monkeypatch.setattr(queue, "requeue_pending", raising_requeue)
    events, unsub = _collect_events(pubsub, ws.task_id)
    dispatcher.run_once()
    unsub()
    assert queue.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert not [e for e in events if e.kind == "queue_paused"]
