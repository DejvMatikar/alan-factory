from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import Queue


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    yield q
    q.close()


def test_advance_pulls_eligible_at_forward(queue: Queue) -> None:
    now = time.time()
    job = queue.enqueue("t", "deploy_verify", eligible_at=now + 600.0)
    updated = queue.advance_eligible_at(job.id, now + 10.0, instructions="new")
    assert updated is not None
    assert updated.eligible_at == pytest.approx(now + 10.0)
    assert updated.instructions == "new"


def test_advance_refuses_to_push_eligible_at_later(queue: Queue) -> None:
    now = time.time()
    job = queue.enqueue("t", "deploy_verify", eligible_at=now + 100.0)
    updated = queue.advance_eligible_at(job.id, now + 200.0)
    assert updated is None
    reloaded = queue.get_job(job.id)
    assert reloaded is not None
    assert reloaded.eligible_at == pytest.approx(now + 100.0)


def test_advance_refuses_when_new_value_equals_existing(queue: Queue) -> None:
    now = time.time()
    job = queue.enqueue("t", "deploy_verify", eligible_at=now + 100.0)
    updated = queue.advance_eligible_at(job.id, now + 100.0)
    assert updated is None


def test_advance_refuses_when_eligible_at_is_null(queue: Queue) -> None:
    job = queue.enqueue("t", "deploy_verify")
    assert job.eligible_at is None
    updated = queue.advance_eligible_at(job.id, time.time())
    assert updated is None


def test_advance_refuses_nonexistent_job(queue: Queue) -> None:
    assert queue.advance_eligible_at(999999, time.time()) is None


def test_advance_refuses_when_job_already_claimed(queue: Queue) -> None:
    now = time.time()
    job = queue.enqueue("t", "deploy_verify", eligible_at=now - 1.0)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    updated = queue.advance_eligible_at(job.id, now - 10.0)
    assert updated is None


def test_advance_without_instructions_kwarg_clears_existing_instructions(
    queue: Queue,
) -> None:
    now = time.time()
    job = queue.enqueue("t", "deploy_verify", eligible_at=now + 100.0, instructions="old")
    updated = queue.advance_eligible_at(job.id, now + 10.0)
    assert updated is not None
    assert updated.instructions is None
