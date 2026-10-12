from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import Job, JobStatus, Queue, QueueError


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def q(db_path: Path) -> Iterator[Queue]:
    instance = Queue(db_path=db_path)
    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            instance.close()


def _make_interrupted(q: Queue, *, inv_id: str = "inv_009") -> Job:
    job = q.enqueue("t", "implementation", triggered_by_invocation_id=inv_id)
    q.claim()
    return q.mark_interrupted_and_fail(job.id, "interrupted_by_restart")


def test_sets_resume_inv_id_on_a_null_column(q: Queue) -> None:
    failed = _make_interrupted(q)
    assert failed.resume_inv_id is None

    requeued = q.requeue_failed_with_resume(failed.id, "inv_009")

    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == "inv_009"
    assert requeued.error is None
    assert requeued.started_at is None
    assert requeued.finished_at is None
    assert requeued.eligible_at is None
    assert requeued.server_error_retry_count == 0
    assert requeued.requeue_count == failed.requeue_count + 1
    assert requeued.interruption_count == failed.interruption_count + 1


def test_recovered_row_is_immediately_claimable(q: Queue) -> None:
    failed = _make_interrupted(q)

    q.requeue_failed_with_resume(failed.id, "inv_009")

    claimed = q.claim()
    assert claimed is not None
    assert claimed.id == failed.id
    assert claimed.resume_inv_id == "inv_009"


def test_clears_a_stale_resume_inv_id(q: Queue) -> None:
    job = q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    q.claim()
    failed = q.mark_interrupted_and_fail(job.id, "boom", resume_inv_id="inv_001")
    assert failed.resume_inv_id == "inv_001"

    requeued = q.requeue_failed_with_resume(failed.id, None)

    assert requeued.resume_inv_id is None


def test_preserves_a_server_error_backoff(q: Queue) -> None:
    failed = _make_interrupted(q)
    eligible_at = time.time() + 300.0

    requeued = q.requeue_failed_with_resume(
        failed.id, None, eligible_at=eligible_at, server_error_retry_count=2
    )

    assert requeued.eligible_at == eligible_at
    assert requeued.server_error_retry_count == 2
    assert q.claim() is None


def test_raises_on_pending_job(q: Queue) -> None:
    job = q.enqueue("t", "implementation")
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_with_resume(job.id, "inv_001")


def test_raises_on_running_job(q: Queue) -> None:
    job = q.enqueue("t", "implementation")
    q.claim()
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_with_resume(job.id, "inv_001")


def test_raises_on_completed_job(q: Queue) -> None:
    job = q.enqueue("t", "implementation")
    q.claim()
    q.complete(job.id)
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_with_resume(job.id, "inv_001")


def test_raises_on_unknown_id(q: Queue) -> None:
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_with_resume(9999, "inv_001")


def test_raises_on_closed_queue(q: Queue) -> None:
    failed = _make_interrupted(q)
    q.close()
    with pytest.raises(QueueError, match="closed"):
        q.requeue_failed_with_resume(failed.id, "inv_009")
