from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import Queue


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def test_complete_clears_a_stale_resume_inv_id(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv-stale")
    queue.claim()

    completed = queue.complete(job.id)

    assert completed.resume_inv_id is None


def test_fail_clears_a_stale_resume_inv_id(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv-stale")
    queue.claim()

    failed = queue.fail(job.id, "boom")

    assert failed.resume_inv_id is None


def test_try_complete_clears_a_stale_resume_inv_id(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.requeue_for_resume(job.id, "inv-stale")
    queue.claim()

    completed = queue.try_complete(job.id)

    assert completed is not None
    assert completed.resume_inv_id is None


def test_mark_interrupted_and_fail_with_resume_inv_id_still_sets_it(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()

    failed = queue.mark_interrupted_and_fail(job.id, "interrupted", resume_inv_id="inv-live")

    assert failed.resume_inv_id == "inv-live"


def test_complete_with_no_prior_resume_inv_id_stays_none(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()

    completed = queue.complete(job.id)

    assert completed.resume_inv_id is None
