from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan.queue import Job, JobStatus, Queue, QueueError

_STALE = datetime(2000, 1, 2, tzinfo=UTC)
_SEEDED: dict[str, Any] = {
    "started_at": datetime(2000, 1, 1, tzinfo=UTC),
    "finished_at": _STALE,
    "error": "boom-before",
    "resume_inv_id": "resume-before",
    "interruption_count": 5,
    "requeue_count": 7,
    "eligible_at": 123.5,
    "server_error_retry_count": 9,
}
_NOW = object()
_Writer = Callable[[Queue, int], Job]


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    yield q
    q.close()


def _seed(q: Queue, status: JobStatus) -> int:
    job = q.enqueue("task", "team", workspace_id="ws")
    q.run(
        "UPDATE jobs SET status = ?, started_at = ?, finished_at = ?, error = ?, "
        "resume_inv_id = ?, interruption_count = ?, requeue_count = ?, "
        "eligible_at = ?, server_error_retry_count = ? WHERE id = ?",
        (
            status.value,
            _SEEDED["started_at"].isoformat(),
            _SEEDED["finished_at"].isoformat(),
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


_CASES: list[Any] = [
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.mark_interrupted_and_fail(i, "err"),
        JobStatus.FAILED,
        {"finished_at": _NOW, "error": "err", "interruption_count": 6},
        id="interrupt",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.mark_interrupted_and_fail(i, "err", resume_inv_id="new"),
        JobStatus.FAILED,
        {
            "finished_at": _NOW,
            "error": "err",
            "interruption_count": 6,
            "resume_inv_id": "new",
        },
        id="interrupt-with-resume",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.mark_interrupted_and_fail(i, "err", clear_resume=True),
        JobStatus.FAILED,
        {
            "finished_at": _NOW,
            "error": "err",
            "interruption_count": 6,
            "resume_inv_id": None,
        },
        id="interrupt-clear-resume",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.requeue_for_resume(i, "new"),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "resume_inv_id": "new",
            "interruption_count": 6,
            "eligible_at": None,
            "server_error_retry_count": 0,
        },
        id="requeue-resume",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.requeue_pending(i),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "resume_inv_id": None,
            "interruption_count": 6,
            "eligible_at": None,
            "server_error_retry_count": 0,
        },
        id="requeue-pending",
    ),
    pytest.param(
        JobStatus.FAILED,
        lambda q, i: q.requeue_failed_for_resume(i),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "interruption_count": 6,
            "requeue_count": 8,
            "eligible_at": None,
            "server_error_retry_count": 0,
        },
        id="requeue-failed-for-resume",
    ),
    pytest.param(
        JobStatus.FAILED,
        lambda q, i: q.requeue_failed_with_resume(
            i, "new", eligible_at=55.0, server_error_retry_count=3
        ),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "resume_inv_id": "new",
            "interruption_count": 6,
            "requeue_count": 8,
            "eligible_at": 55.0,
            "server_error_retry_count": 3,
        },
        id="requeue-failed-with-resume",
    ),
    pytest.param(
        JobStatus.FAILED,
        lambda q, i: q.requeue_failed_with_resume(i, None),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "resume_inv_id": None,
            "interruption_count": 6,
            "requeue_count": 8,
            "eligible_at": None,
            "server_error_retry_count": 0,
        },
        id="requeue-failed-with-resume-nulls",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.requeue_with_server_error_backoff(i, 99.5),
        JobStatus.PENDING,
        {
            "started_at": None,
            "finished_at": None,
            "error": None,
            "resume_inv_id": None,
            "eligible_at": 99.5,
            "server_error_retry_count": 10,
        },
        id="requeue-server-error",
    ),
]


@pytest.mark.parametrize(("seeded", "write", "to_status", "changes"), _CASES)
def test_transition_writes_exactly_the_columns_the_table_names(
    queue: Queue,
    seeded: JobStatus,
    write: _Writer,
    to_status: JobStatus,
    changes: dict[str, Any],
) -> None:
    job_id = _seed(queue, seeded)
    returned = write(queue, job_id)
    stored = queue.get_job(job_id)
    assert stored == returned
    expected = {**_SEEDED, **changes}
    actual = returned.model_dump()
    assert returned.status is to_status
    for column, value in expected.items():
        if value is _NOW:
            assert actual[column] is not None
            assert actual[column] > _STALE
        else:
            assert actual[column] == value, column


@pytest.mark.parametrize(("seeded", "write", "to_status", "changes"), _CASES)
def test_transition_leaves_identity_columns_untouched(
    queue: Queue,
    seeded: JobStatus,
    write: _Writer,
    to_status: JobStatus,
    changes: dict[str, Any],
) -> None:
    job_id = _seed(queue, seeded)
    before = queue.get_job(job_id)
    assert before is not None
    after = write(queue, job_id)
    for column in ("id", "task_id", "workspace_id", "team", "enqueued_at", "instructions"):
        assert getattr(after, column) == getattr(before, column)


_MISSES: list[Any] = [
    pytest.param(
        JobStatus.PENDING,
        lambda q, i: q.mark_interrupted_and_fail(i, "e"),
        "job {} is not running",
        id="interrupt",
    ),
    pytest.param(
        JobStatus.COMPLETED,
        lambda q, i: q.requeue_pending(i),
        "job {} is not running",
        id="requeue-pending",
    ),
    pytest.param(
        JobStatus.FAILED,
        lambda q, i: q.requeue_for_resume(i, "r"),
        "job {} is not running",
        id="requeue-resume",
    ),
    pytest.param(
        JobStatus.PENDING,
        lambda q, i: q.requeue_with_server_error_backoff(i, 1.0),
        "job {} is not running",
        id="requeue-server-error",
    ),
    pytest.param(
        JobStatus.RUNNING,
        lambda q, i: q.requeue_failed_for_resume(i),
        "job {} is not a failed job",
        id="requeue-failed-for-resume",
    ),
    pytest.param(
        JobStatus.PENDING,
        lambda q, i: q.requeue_failed_with_resume(i, "r"),
        "job {} is not a failed job",
        id="requeue-failed-with-resume",
    ),
]


@pytest.mark.parametrize(("seeded", "write", "message"), _MISSES)
def test_transition_from_the_wrong_status_raises_and_changes_nothing(
    queue: Queue, seeded: JobStatus, write: _Writer, message: str
) -> None:
    job_id = _seed(queue, seeded)
    before = queue.get_job(job_id)
    with pytest.raises(QueueError) as excinfo:
        write(queue, job_id)
    assert str(excinfo.value) == message.format(job_id)
    assert queue.get_job(job_id) == before


def test_transition_on_an_unknown_job_raises_with_the_job_id(queue: Queue) -> None:
    with pytest.raises(QueueError, match="job 999 is not running"):
        queue.requeue_pending(999)


def test_transition_on_a_closed_queue_raises_queue_closed(queue: Queue) -> None:
    job_id = _seed(queue, JobStatus.RUNNING)
    queue.close()
    with pytest.raises(QueueError, match="queue is closed"):
        queue.requeue_pending(job_id)
