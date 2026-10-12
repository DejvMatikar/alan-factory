from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.queue import JobStatus, Queue


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


def test_idempotent_paused_tasks_table_creation(db_path: Path) -> None:
    q1 = Queue(db_path=db_path)
    q1.close()
    q2 = Queue(db_path=db_path)
    q2.close()


def test_pause_task_marks_task_paused(q: Queue) -> None:
    q.pause_task("t1")
    assert q.is_task_paused("t1") is True


def test_is_task_paused_false_when_not_paused(q: Queue) -> None:
    assert q.is_task_paused("t1") is False


def test_pause_task_is_idempotent(q: Queue) -> None:
    q.pause_task("t1")
    q.pause_task("t1")
    assert q.list_paused_tasks() == ["t1"]


def test_unpause_task_removes_pause_flag(q: Queue) -> None:
    q.pause_task("t1")
    q.unpause_task("t1")
    assert q.is_task_paused("t1") is False


def test_unpause_task_noop_when_not_paused(q: Queue) -> None:
    q.unpause_task("t1")
    assert q.is_task_paused("t1") is False


def test_list_paused_tasks_returns_sorted(q: Queue) -> None:
    q.pause_task("zeta")
    q.pause_task("alpha")
    q.pause_task("mu")
    assert q.list_paused_tasks() == ["alpha", "mu", "zeta"]


def test_list_paused_tasks_empty_by_default(q: Queue) -> None:
    assert q.list_paused_tasks() == []


def test_claim_skips_paused_task(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_task("t1")
    assert q.claim() is None


def test_claim_resumes_after_unpause(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_task("t1")
    assert q.claim() is None
    q.unpause_task("t1")
    job = q.claim()
    assert job is not None
    assert job.status is JobStatus.RUNNING


def test_claim_picks_other_task_when_one_is_paused(q: Queue) -> None:
    q.enqueue("paused_task", "research")
    q.enqueue("free_task", "research")
    q.pause_task("paused_task")
    job = q.claim()
    assert job is not None
    assert job.task_id == "free_task"


def test_delete_jobs_also_clears_paused_flag(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.pause_task("t1")
    q.delete_jobs("t1")
    assert q.is_task_paused("t1") is False
    assert q.list_paused_tasks() == []


def test_delete_jobs_returns_only_jobs_count_not_paused_rows(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.enqueue("t1", "review")
    q.pause_task("t1")
    deleted = q.delete_jobs("t1")
    assert deleted == 2


def test_list_jobs_filters_by_status(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.enqueue("t1", "review")
    job = q.claim()
    assert job is not None
    running = q.list_jobs(status=JobStatus.RUNNING)
    pending = q.list_jobs(status=JobStatus.PENDING)
    assert [j.team for j in running] == ["research"]
    assert [j.team for j in pending] == ["review"]


def test_list_jobs_filters_by_task_and_status(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.enqueue("t2", "research")
    job = q.claim()
    assert job is not None
    assert job.task_id == "t1"
    running_t1 = q.list_jobs(task_id="t1", status=JobStatus.RUNNING)
    running_t2 = q.list_jobs(task_id="t2", status=JobStatus.RUNNING)
    assert [j.task_id for j in running_t1] == ["t1"]
    assert running_t2 == []
