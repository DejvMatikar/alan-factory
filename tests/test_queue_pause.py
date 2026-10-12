from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import JobStatus, Queue, QueueError


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


def test_idempotent_queue_meta_table_creation(db_path: Path) -> None:
    q1 = Queue(db_path=db_path)
    q1.close()
    q2 = Queue(db_path=db_path)
    q2.close()


def test_get_meta_returns_none_when_missing(q: Queue) -> None:
    assert q.get_meta("nonexistent") is None


def test_set_meta_and_get_meta_round_trip(q: Queue) -> None:
    q.set_meta("mykey", "myval")
    assert q.get_meta("mykey") == "myval"


def test_set_meta_overwrites_existing(q: Queue) -> None:
    q.set_meta("k", "v1")
    q.set_meta("k", "v2")
    assert q.get_meta("k") == "v2"


def test_paused_until_returns_none_when_no_entry(q: Queue) -> None:
    assert q.paused_until() is None


def test_paused_until_returns_none_for_past_timestamp(q: Queue) -> None:
    q.pause_until(time.time() - 1.0)
    assert q.paused_until() is None


def test_paused_until_treats_malformed_meta_as_not_paused(
    q: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    q.set_meta("paused_until", "not-a-float")
    with caplog.at_level("WARNING", logger="alan.queue"):
        assert q.paused_until() is None
    assert any("paused_until meta has non-numeric value" in r.message for r in caplog.records)


def test_paused_until_returns_epoch_when_in_future(q: Queue) -> None:
    future = time.time() + 3600.0
    q.pause_until(future)
    result = q.paused_until()
    assert result is not None
    assert abs(result - future) < 1.0


def test_pause_until_monotonic_max_does_not_shorten(q: Queue) -> None:
    far_future = time.time() + 10_000.0
    q.pause_until(far_future)
    q.pause_until(time.time() + 60.0)
    result = q.paused_until()
    assert result is not None
    assert abs(result - far_future) < 1.0


def test_pause_until_monotonic_max_extends_when_longer(q: Queue) -> None:
    near = time.time() + 60.0
    far = time.time() + 3600.0
    q.pause_until(near)
    q.pause_until(far)
    result = q.paused_until()
    assert result is not None
    assert abs(result - far) < 1.0


def test_claim_returns_none_while_paused(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_until(time.time() + 3600.0)
    assert q.claim() is None


def test_claim_works_after_pause_expires(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_until(time.time() - 1.0)
    job = q.claim()
    assert job is not None
    assert job.status is JobStatus.RUNNING


def test_requeue_pending_flips_running_to_pending(q: Queue) -> None:
    q.enqueue("t1", "research")
    job = q.claim()
    assert job is not None
    requeued = q.requeue_pending(job.id)
    assert requeued.status is JobStatus.PENDING


def test_requeue_pending_bumps_interruption_count(q: Queue) -> None:
    q.enqueue("t1", "research")
    job = q.claim()
    assert job is not None
    assert job.interruption_count == 0
    requeued = q.requeue_pending(job.id)
    assert requeued.interruption_count == 1


def test_requeue_pending_clears_started_at(q: Queue) -> None:
    q.enqueue("t1", "research")
    job = q.claim()
    assert job is not None
    assert job.started_at is not None
    requeued = q.requeue_pending(job.id)
    assert requeued.started_at is None


def test_requeue_pending_clears_resume_inv_id(q: Queue) -> None:
    q.enqueue("t1", "research")
    job = q.claim()
    assert job is not None
    q.requeue_for_resume(job.id, "inv_001")
    job2 = q.claim()
    assert job2 is not None
    requeued = q.requeue_pending(job2.id)
    assert requeued.resume_inv_id is None


def test_requeue_pending_raises_when_not_running(q: Queue) -> None:
    job = q.enqueue("t1", "research")
    with pytest.raises(QueueError, match="not running"):
        q.requeue_pending(job.id)


def test_clear_pause_removes_active_pause(q: Queue) -> None:
    q.pause_until(time.time() + 3600.0)
    q.clear_pause()
    assert q.paused_until() is None


def test_clear_pause_when_not_paused_is_noop(q: Queue) -> None:
    assert q.clear_pause() is None
    assert q.paused_until() is None


def test_clear_pause_then_pause_until_repauses(q: Queue) -> None:
    q.pause_until(time.time() + 3600.0)
    q.clear_pause()
    future = time.time() + 60.0
    q.pause_until(future)
    result = q.paused_until()
    assert result is not None
    assert abs(result - future) < 1.0


def test_clear_pause_returns_prior_paused_until(q: Queue) -> None:
    future = time.time() + 3600.0
    q.pause_until(future)
    prior = q.clear_pause()
    assert prior is not None
    assert abs(prior - future) < 1.0


def test_claim_returns_job_after_clear_pause(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_until(time.time() + 3600.0)
    assert q.claim() is None
    q.clear_pause()
    job = q.claim()
    assert job is not None
    assert job.status is JobStatus.RUNNING
