from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan.queue import READINESS_HOLD_DELAY_S, JobStatus, Queue, QueueError
from tests.fake_clock import FakeClock

_NOW = 1_000_000.0
_STARTED = datetime(2000, 1, 1, tzinfo=UTC)
_SEEDED: dict[str, Any] = {
    "started_at": _STARTED,
    "finished_at": None,
    "error": "boom-before",
    "resume_inv_id": "resume-before",
    "interruption_count": 5,
    "requeue_count": 7,
    "eligible_at": 123.5,
    "server_error_retry_count": 9,
}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=_NOW)


@pytest.fixture
def queue(tmp_path: Path, clock: FakeClock) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db", clock=clock)
    try:
        yield q
    finally:
        q.close()


def _seed(q: Queue, status: JobStatus, *, workspace_id: str = "ws") -> int:
    job = q.enqueue(workspace_id, "team", workspace_id=workspace_id)
    q.run(
        "UPDATE jobs SET status = ?, started_at = ?, finished_at = ?, error = ?, "
        "resume_inv_id = ?, interruption_count = ?, requeue_count = ?, "
        "eligible_at = ?, server_error_retry_count = ? WHERE id = ?",
        (
            status.value,
            _SEEDED["started_at"].isoformat(),
            _SEEDED["finished_at"],
            _SEEDED["error"],
            _SEEDED["resume_inv_id"],
            _SEEDED["interruption_count"],
            _SEEDED["requeue_count"],
            _SEEDED["eligible_at"],
            _SEEDED["server_error_retry_count"],
            job.id,
        ),
    )
    return job.id


def test_the_hold_delay_is_sixty_seconds() -> None:
    assert READINESS_HOLD_DELAY_S == 60.0


def test_a_hold_returns_the_running_job_to_pending_with_a_later_eligible_time(
    queue: Queue,
) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    held = queue.hold_until_ready(job_id)
    assert held.status is JobStatus.PENDING
    assert held.started_at is None
    assert held.eligible_at == _NOW + READINESS_HOLD_DELAY_S
    assert queue.get_job(job_id) == held


def test_a_hold_leaves_every_other_column_exactly_as_it_was(queue: Queue) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    before = queue.get_job(job_id)
    assert before is not None
    held = queue.hold_until_ready(job_id)
    changed = {"status", "started_at", "eligible_at"}
    for column, value in before.model_dump().items():
        if column not in changed:
            assert held.model_dump()[column] == value, column


def test_a_hold_keeps_every_retry_interruption_and_requeue_count(queue: Queue) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    held = queue.hold_until_ready(job_id)
    assert held.interruption_count == _SEEDED["interruption_count"]
    assert held.requeue_count == _SEEDED["requeue_count"]
    assert held.server_error_retry_count == _SEEDED["server_error_retry_count"]
    assert held.resume_inv_id == _SEEDED["resume_inv_id"]


def test_a_hold_writes_no_job_error(queue: Queue) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    queue.run("UPDATE jobs SET error = NULL WHERE id = ?", (job_id,))
    assert queue.hold_until_ready(job_id).error is None


def test_holding_twice_in_a_row_changes_nothing_but_the_eligible_time(
    queue: Queue, clock: FakeClock
) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    queue.hold_until_ready(job_id)
    queue.run("UPDATE jobs SET status = ? WHERE id = ?", (JobStatus.RUNNING.value, job_id))
    clock.advance(30.0)
    again = queue.hold_until_ready(job_id)
    assert again.eligible_at == _NOW + 30.0 + READINESS_HOLD_DELAY_S
    assert again.requeue_count == _SEEDED["requeue_count"]


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.COMPLETED, JobStatus.FAILED])
def test_a_hold_from_any_status_but_running_raises_and_changes_nothing(
    queue: Queue, status: JobStatus
) -> None:
    job_id = _seed(queue, status)
    before = queue.get_job(job_id)
    with pytest.raises(QueueError) as excinfo:
        queue.hold_until_ready(job_id)
    assert str(excinfo.value) == f"job {job_id} is not running"
    assert queue.get_job(job_id) == before


def test_a_hold_on_an_unknown_job_raises_with_the_job_id(queue: Queue) -> None:
    with pytest.raises(QueueError, match="job 999 is not running"):
        queue.hold_until_ready(999)


def test_a_hold_on_a_closed_queue_raises_queue_closed(queue: Queue) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    queue.close()
    with pytest.raises(QueueError, match="queue is closed"):
        queue.hold_until_ready(job_id)


def test_a_held_job_is_not_claimed_until_its_eligible_time_then_resumes_by_itself(
    queue: Queue, clock: FakeClock
) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    queue.hold_until_ready(job_id)
    assert queue.claim() is None
    clock.advance(READINESS_HOLD_DELAY_S - 1.0)
    assert queue.claim() is None
    clock.advance(1.0)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.requeue_count == _SEEDED["requeue_count"]


def test_a_held_job_does_not_block_other_workspaces_from_being_claimed(queue: Queue) -> None:
    held_id = _seed(queue, JobStatus.RUNNING, workspace_id="held-ws")
    queue.hold_until_ready(held_id)
    other = queue.enqueue("other-ws", "team", workspace_id="other-ws")
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == other.id

