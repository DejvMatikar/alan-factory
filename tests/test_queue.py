from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import queue as queue_mod
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


def test_enqueue_returns_job_with_pending_status(q: Queue) -> None:
    job = q.enqueue("task-1", "research")
    assert job.id >= 1
    assert job.task_id == "task-1"
    assert job.team == "research"
    assert job.status is JobStatus.PENDING
    assert job.parent_handover_id is None
    assert job.triggered_by_invocation_id is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.error is None
    assert job.enqueued_at.tzinfo is not None


def test_enqueue_with_parent_and_invocation_records_both(q: Queue) -> None:
    job = q.enqueue(
        "task-1",
        "brainstorming",
        parent_handover_id="handover_task-1_001",
        triggered_by_invocation_id="inv_002",
    )
    assert job.parent_handover_id == "handover_task-1_001"
    assert job.triggered_by_invocation_id == "inv_002"


def test_claim_returns_none_when_empty(q: Queue) -> None:
    assert q.claim() is None


def test_claim_pops_pending_jobs_in_fifo_order(q: Queue) -> None:
    j1 = q.enqueue("t1", "research")
    j2 = q.enqueue("t2", "architecture")
    claimed1 = q.claim()
    claimed2 = q.claim()
    assert claimed1 is not None
    assert claimed2 is not None
    assert claimed1.id == j1.id
    assert claimed2.id == j2.id
    assert claimed1.status is JobStatus.RUNNING
    assert claimed2.status is JobStatus.RUNNING
    assert claimed1.started_at is not None


def test_claim_returns_none_after_all_running(q: Queue) -> None:
    q.enqueue("t", "research")
    q.claim()
    assert q.claim() is None


def test_complete_marks_finished(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    done = q.complete(j.id)
    assert done.status is JobStatus.COMPLETED
    assert done.finished_at is not None
    assert done.error is None


def test_fail_marks_failed_with_error(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    failed = q.fail(j.id, "boom")
    assert failed.status is JobStatus.FAILED
    assert failed.error == "boom"
    assert failed.finished_at is not None


def test_complete_unknown_job_raises(q: Queue) -> None:
    with pytest.raises(QueueError, match="job 999 is not running"):
        q.complete(999)


def test_complete_pending_job_raises(q: Queue) -> None:
    j = q.enqueue("t", "research")
    with pytest.raises(QueueError, match=f"job {j.id} is not running"):
        q.complete(j.id)


def test_fail_already_completed_raises(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.complete(j.id)
    with pytest.raises(QueueError, match="is not running"):
        q.fail(j.id, "late")


def test_try_complete_marks_running_job_finished(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    done = q.try_complete(j.id)
    assert done is not None
    assert done.status is JobStatus.COMPLETED
    assert done.finished_at is not None


def test_try_complete_returns_none_for_pending_job(q: Queue) -> None:
    j = q.enqueue("t", "research")
    assert q.try_complete(j.id) is None
    pending = q.get_job(j.id)
    assert pending is not None
    assert pending.status is JobStatus.PENDING


def test_try_complete_returns_none_for_already_completed_job(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.complete(j.id)
    assert q.try_complete(j.id) is None
    completed = q.get_job(j.id)
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED


def test_try_complete_returns_none_for_unknown_job(q: Queue) -> None:
    assert q.try_complete(999) is None
    assert q.get_job(999) is None


def test_try_complete_or_current_completes_running_job(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    completed, current = q.try_complete_or_current(j.id)
    assert completed is not None
    assert completed.status is JobStatus.COMPLETED
    assert current is None


def test_try_complete_or_current_returns_current_state_for_pending_job(q: Queue) -> None:
    j = q.enqueue("t", "research")
    completed, current = q.try_complete_or_current(j.id)
    assert completed is None
    assert current is not None
    assert current.status is JobStatus.PENDING


def test_try_complete_or_current_returns_current_state_for_finalized_job(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.complete(j.id)
    completed, current = q.try_complete_or_current(j.id)
    assert completed is None
    assert current is not None
    assert current.status is JobStatus.COMPLETED


def test_try_complete_or_current_returns_none_none_for_unknown_job(q: Queue) -> None:
    completed, current = q.try_complete_or_current(999)
    assert completed is None
    assert current is None


def test_get_job_returns_current_row(q: Queue) -> None:
    j = q.enqueue("t", "research")
    fetched = q.get_job(j.id)
    assert fetched is not None
    assert fetched.id == j.id
    assert fetched.status is JobStatus.PENDING


def test_get_job_returns_none_for_unknown_id(q: Queue) -> None:
    assert q.get_job(999) is None


def test_list_jobs_returns_all_sorted(q: Queue) -> None:
    a = q.enqueue("alpha", "research")
    b = q.enqueue("bravo", "architecture")
    c = q.enqueue("alpha", "brainstorming")
    ids = [j.id for j in q.list_jobs()]
    assert ids == [a.id, b.id, c.id]


def test_list_jobs_filters_by_task_id(q: Queue) -> None:
    a = q.enqueue("alpha", "research")
    q.enqueue("bravo", "architecture")
    c = q.enqueue("alpha", "brainstorming")
    ids = [j.id for j in q.list_jobs(task_id="alpha")]
    assert ids == [a.id, c.id]


def test_list_jobs_empty_when_no_match(q: Queue) -> None:
    q.enqueue("alpha", "research")
    assert q.list_jobs(task_id="nope") == []


def test_close_blocks_further_use_with_queue_error(q: Queue) -> None:
    q.enqueue("t", "research")
    q.close()
    with pytest.raises(QueueError, match="queue is closed"):
        q.enqueue("t2", "research")


def test_close_is_idempotent(q: Queue) -> None:
    q.close()
    q.close()


def test_all_operations_raise_after_close(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.close()
    with pytest.raises(QueueError, match="queue is closed"):
        q.claim()
    with pytest.raises(QueueError, match="queue is closed"):
        q.complete(j.id)
    with pytest.raises(QueueError, match="queue is closed"):
        q.fail(j.id, "boom")
    with pytest.raises(QueueError, match="queue is closed"):
        q.list_jobs()
    with pytest.raises(QueueError, match="queue is closed"):
        q.delete_jobs("t")


def test_delete_jobs_removes_rows_for_task(q: Queue) -> None:
    completed = q.enqueue("doomed", "research")
    failed = q.enqueue("doomed", "architecture")
    q.enqueue("doomed", "implementation")
    q.claim()
    q.complete(completed.id)
    q.claim()
    q.fail(failed.id, "boom")
    count = q.delete_jobs("doomed")
    assert count == 3
    assert q.list_jobs(task_id="doomed") == []


def test_delete_jobs_returns_zero_when_no_match(q: Queue) -> None:
    q.enqueue("alpha", "research")
    assert q.delete_jobs("nope") == 0
    assert len(q.list_jobs(task_id="alpha")) == 1


def test_delete_jobs_does_not_touch_other_tasks(q: Queue) -> None:
    a = q.enqueue("alpha", "research")
    b = q.enqueue("bravo", "architecture")
    count = q.delete_jobs("alpha")
    assert count == 1
    remaining = q.list_jobs()
    assert [j.id for j in remaining] == [b.id]
    assert a.id not in {j.id for j in remaining}


def test_init_rejects_old_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alan.queue.sqlite3.sqlite_version_info", (3, 34, 0))
    monkeypatch.setattr("alan.queue.sqlite3.sqlite_version", "3.34.0")
    with pytest.raises(QueueError, match="SQLite >= 3.35 required"):
        Queue(db_path=tmp_path / "q.db")


def test_persists_across_instances(db_path: Path) -> None:
    a = Queue(db_path=db_path)
    a.enqueue("t", "research")
    a.close()
    b = Queue(db_path=db_path)
    rows = b.list_jobs()
    assert len(rows) == 1
    assert rows[0].task_id == "t"
    b.close()


def test_default_db_path_used_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "default" / "queue.db"
    monkeypatch.setattr(queue_mod, "QUEUE_DB", target)
    q = Queue()
    q.enqueue("t", "research")
    q.close()
    assert target.exists()


def test_concurrent_claim_returns_each_job_at_most_once(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    n = 50
    for i in range(n):
        q.enqueue(f"task-{i}", "research")
    claimed: list[Job] = []
    lock = threading.Lock()

    def worker() -> None:
        while True:
            job = q.claim()
            if job is None:
                return
            with lock:
                claimed.append(job)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    q.close()
    seen_ids = [j.id for j in claimed]
    assert len(seen_ids) == n
    assert len(set(seen_ids)) == n


def test_job_model_field_defaults() -> None:
    j = Job(
        id=1,
        task_id="t",
        team="research",
        status=JobStatus.PENDING,
        enqueued_at=datetime.now(UTC),
    )
    assert j.parent_handover_id is None
    assert j.started_at is None
    assert j.finished_at is None
    assert j.error is None


def test_jobstatus_values() -> None:
    assert {s.value for s in JobStatus} == {"pending", "running", "completed", "failed"}


def test_list_running_returns_only_running_in_order(q: Queue) -> None:
    q.enqueue("t1", "research")
    b = q.enqueue("t2", "architecture")
    q.enqueue("t3", "implementation")
    claimed_a = q.claim()
    claimed_b = q.claim()
    assert claimed_a is not None and claimed_b is not None
    q.complete(claimed_a.id)
    running = q.list_running()
    assert [j.id for j in running] == [b.id]


def test_list_running_empty_when_no_running_rows(q: Queue) -> None:
    q.enqueue("t", "research")
    assert q.list_running() == []


def test_count_jobs_by_workspace_empty_when_no_rows(q: Queue) -> None:
    assert q.count_jobs_by_workspace() == {}


def test_count_jobs_by_workspace_groups_all_statuses(q: Queue) -> None:
    q.enqueue("alpha", "research")
    q.enqueue("alpha", "architecture")
    q.enqueue("bravo", "implementation")
    q.enqueue("bravo", "verification")
    q.enqueue("bravo", "retrospective")
    first = q.claim()
    second = q.claim()
    assert first is not None and second is not None
    q.complete(first.id)
    q.fail(second.id, "boom")
    assert q.count_jobs_by_workspace() == {"alpha": 2, "bravo": 3}


def test_count_jobs_by_workspace_counts_subtask_jobs_under_root(q: Queue) -> None:
    q.enqueue("sub-1", "implementation", workspace_id="root")
    q.enqueue("sub-2", "verification", workspace_id="root")
    q.enqueue("solo", "research")
    assert q.count_jobs_by_workspace(workspace_ids=["root"]) == {"root": 2}
    assert q.count_jobs_by_workspace(workspace_ids=["root", "solo"]) == {
        "root": 2,
        "solo": 1,
    }
    assert q.count_jobs_by_workspace(workspace_ids=["sub-1"]) == {}


def test_count_jobs_by_workspace_returns_empty_when_filter_is_empty(q: Queue) -> None:
    q.enqueue("alpha", "research")
    assert q.count_jobs_by_workspace(workspace_ids=[]) == {}


def test_new_job_has_zero_interruption_count(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.interruption_count == 0


def test_mark_interrupted_and_fail_flips_status_and_bumps_counter(q: Queue) -> None:
    q.enqueue("t", "research")
    claimed = q.claim()
    assert claimed is not None
    failed = q.mark_interrupted_and_fail(claimed.id, "interrupted_by_restart")
    assert failed.status is JobStatus.FAILED
    assert failed.error == "interrupted_by_restart"
    assert failed.finished_at is not None
    assert failed.interruption_count == 1


def test_mark_interrupted_and_fail_idempotent_via_status_guard(q: Queue) -> None:
    q.enqueue("t", "research")
    claimed = q.claim()
    assert claimed is not None
    q.mark_interrupted_and_fail(claimed.id, "first")
    with pytest.raises(QueueError, match="not running"):
        q.mark_interrupted_and_fail(claimed.id, "second")


def test_mark_interrupted_and_fail_unknown_id_raises(q: Queue) -> None:
    with pytest.raises(QueueError, match="not running"):
        q.mark_interrupted_and_fail(9999, "missing")


def test_mark_interrupted_and_fail_after_multiple_interruptions_accumulates(
    q: Queue,
) -> None:
    q.enqueue("t", "research")
    claimed = q.claim()
    assert claimed is not None
    q.mark_interrupted_and_fail(claimed.id, "first")
    q._conn.execute(  # noqa: SLF001 - white-box test of cumulative counter
        "UPDATE jobs SET status = ?, finished_at = NULL, error = NULL WHERE id = ?",
        (JobStatus.RUNNING.value, claimed.id),
    )
    failed = q.mark_interrupted_and_fail(claimed.id, "second")
    assert failed.interruption_count == 2


def test_migration_is_idempotent_on_existing_db(db_path: Path) -> None:
    first = Queue(db_path=db_path)
    first.enqueue("t", "research")
    first.close()
    second = Queue(db_path=db_path)
    try:
        jobs = second.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].interruption_count == 0
    finally:
        second.close()


def test_migration_adds_missing_interruption_count_column(db_path: Path) -> None:
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            team TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            parent_handover_id TEXT,
            triggered_by_invocation_id TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT
        );
        INSERT INTO jobs (task_id, team, status, enqueued_at)
        VALUES ('t', 'research', 'pending', '2026-05-15T10:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    q = Queue(db_path=db_path)
    try:
        jobs = q.list_jobs()
        assert jobs[0].interruption_count == 0
        assert jobs[0].resume_inv_id is None
        assert jobs[0].instructions is None
    finally:
        q.close()


def test_enqueue_with_instructions_persists(q: Queue) -> None:
    job = q.enqueue(
        "t", "research", instructions="Focus narrowly on alan/foo.py."
    )
    assert job.instructions == "Focus narrowly on alan/foo.py."
    fetched = q.list_jobs(task_id="t")[0]
    assert fetched.instructions == "Focus narrowly on alan/foo.py."


def test_enqueue_without_instructions_defaults_to_none(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.instructions is None


def test_new_job_has_null_resume_inv_id(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.resume_inv_id is None


def test_requeue_for_resume_flips_to_pending_and_records_inv(q: Queue) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    requeued = q.requeue_for_resume(claimed.id, "inv_001")
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == "inv_001"
    assert requeued.interruption_count == 1
    assert requeued.started_at is None
    assert requeued.finished_at is None
    assert requeued.error is None


def test_requeue_for_resume_unknown_id_raises(q: Queue) -> None:
    with pytest.raises(QueueError, match="not running"):
        q.requeue_for_resume(999, "inv_001")


def test_requeue_for_resume_status_guard(q: Queue) -> None:
    q.enqueue("t", "implementation")
    claimed = q.claim()
    assert claimed is not None
    q.complete(claimed.id)
    with pytest.raises(QueueError, match="not running"):
        q.requeue_for_resume(claimed.id, "inv_001")


def test_requeue_then_claim_returns_pending_with_resume_inv(q: Queue) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    first = q.claim()
    assert first is not None
    q.requeue_for_resume(first.id, "inv_001")
    second = q.claim()
    assert second is not None
    assert second.id == first.id
    assert second.resume_inv_id == "inv_001"
    assert second.interruption_count == 1


def test_migration_adds_missing_resume_inv_id_column(db_path: Path) -> None:
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            team TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            parent_handover_id TEXT,
            triggered_by_invocation_id TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            interruption_count INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO jobs (task_id, team, status, enqueued_at)
        VALUES ('t', 'research', 'pending', '2026-05-15T10:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    q = Queue(db_path=db_path)
    try:
        assert q.list_jobs()[0].resume_inv_id is None
    finally:
        q.close()


def test_claim_skips_workspace_with_running_job(q: Queue) -> None:
    j1 = q.enqueue("ws-a", "research")
    j2 = q.enqueue("ws-a", "architecture")
    j3 = q.enqueue("ws-b", "research")
    claimed1 = q.claim()
    assert claimed1 is not None and claimed1.id == j1.id
    claimed2 = q.claim()
    assert claimed2 is not None and claimed2.id == j3.id
    assert claimed2.task_id == "ws-b"
    assert j2.id not in {claimed1.id, claimed2.id}


def test_claim_allows_workspace_after_running_job_completes(q: Queue) -> None:
    j1 = q.enqueue("ws-a", "research")
    j2 = q.enqueue("ws-a", "architecture")
    claimed1 = q.claim()
    assert claimed1 is not None and claimed1.id == j1.id
    assert q.claim() is None
    q.complete(claimed1.id)
    claimed2 = q.claim()
    assert claimed2 is not None and claimed2.id == j2.id


def _make_failed_resumable(q: Queue, task_id: str = "t") -> Job:
    j = q.enqueue(task_id, "implementation", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j.id, "inv_001")
    q.claim()
    return q.mark_interrupted_and_fail(j.id, "interrupted")


def test_requeue_failed_for_resume_flips_to_pending_and_preserves_resume_inv_id(
    q: Queue,
) -> None:
    failed = _make_failed_resumable(q)
    assert failed.resume_inv_id == "inv_001"
    requeued = q.requeue_failed_for_resume(failed.id)
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == "inv_001"
    assert requeued.interruption_count == failed.interruption_count + 1
    assert requeued.started_at is None
    assert requeued.finished_at is None
    assert requeued.error is None


def test_requeue_failed_for_resume_raises_on_pending_job(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_for_resume(j.id)


def test_requeue_failed_for_resume_raises_on_running_job(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    q.claim()
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_for_resume(j.id)


def test_requeue_failed_for_resume_raises_on_completed_job(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    q.claim()
    q.complete(j.id)
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_for_resume(j.id)


def test_requeue_failed_for_resume_raises_on_unknown_id(q: Queue) -> None:
    with pytest.raises(QueueError, match="not a failed job"):
        q.requeue_failed_for_resume(9999)


def test_list_resumable_failed_returns_failed_jobs_with_resume_inv_id(
    q: Queue,
) -> None:
    failed = _make_failed_resumable(q)
    results = q.list_resumable_failed("t")
    assert len(results) == 1
    assert results[0].id == failed.id
    assert results[0].resume_inv_id == "inv_001"


def test_list_resumable_failed_empty_when_no_resume_inv_id(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    q.claim()
    q.mark_interrupted_and_fail(j.id, "interrupted")
    assert q.list_resumable_failed("t") == []


def test_list_resumable_failed_excludes_empty_string_resume_inv_id(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    q.claim()
    q.mark_interrupted_and_fail(j.id, "interrupted")
    q._conn.execute(  # noqa: SLF001 - white-box: set empty-string resume_inv_id
        "UPDATE jobs SET resume_inv_id = '' WHERE id = ?", (j.id,)
    )
    assert q.list_resumable_failed("t") == []


def test_list_resumable_failed_excludes_non_failed_jobs(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    q._conn.execute(  # noqa: SLF001 - white-box: set resume_inv_id on PENDING row
        "UPDATE jobs SET resume_inv_id = 'inv_001' WHERE id = ?", (j.id,)
    )
    assert q.list_resumable_failed("t") == []


def test_list_resumable_failed_filters_by_task_id(q: Queue) -> None:
    _make_failed_resumable(q, "alpha")
    assert q.list_resumable_failed("bravo") == []
    assert len(q.list_resumable_failed("alpha")) == 1


def test_list_resumable_failed_returns_newest_first(q: Queue) -> None:
    j1 = q.enqueue("t", "impl", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j1.id, "inv_001")
    q.claim()
    q.mark_interrupted_and_fail(j1.id, "first")
    j2 = q.enqueue("t", "impl", triggered_by_invocation_id="inv_002")
    q.claim()
    q.requeue_for_resume(j2.id, "inv_002")
    q.claim()
    q.mark_interrupted_and_fail(j2.id, "second")
    results = q.list_resumable_failed("t")
    assert len(results) == 2
    assert results[0].id == j2.id
    assert results[1].id == j1.id


def test_enqueue_with_explicit_workspace_id(q: Queue) -> None:
    job = q.enqueue("task-1", "research", workspace_id="ws-outer")
    assert job.workspace_id == "ws-outer"
    fetched = q.list_jobs(task_id="task-1")[0]
    assert fetched.workspace_id == "ws-outer"


def test_enqueue_default_workspace_id_matches_task_id(q: Queue) -> None:
    job = q.enqueue("task-2", "research")
    assert job.workspace_id == "task-2"


def test_migration_adds_workspace_id_column_and_backfills(db_path: Path) -> None:
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            team TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            parent_handover_id TEXT,
            triggered_by_invocation_id TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            interruption_count INTEGER NOT NULL DEFAULT 0,
            resume_inv_id TEXT,
            instructions TEXT
        );
        INSERT INTO jobs (task_id, team, status, enqueued_at)
        VALUES ('task-abc', 'research', 'pending', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.close()
    q = Queue(db_path=db_path)
    try:
        jobs = q.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].workspace_id == "task-abc"
    finally:
        q.close()


def test_migration_workspace_id_already_present_skips_backfill(db_path: Path) -> None:
    q1 = Queue(db_path=db_path)
    q1.enqueue("task-xyz", "research", workspace_id="ws-abc")
    q1.close()
    q2 = Queue(db_path=db_path)
    try:
        jobs = q2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].workspace_id == "ws-abc"
    finally:
        q2.close()


def test_claim_skips_workspace_when_different_subtask_is_running(q: Queue) -> None:
    j_plan = q.enqueue("plan", "research", workspace_id="ws-x")
    j_impl = q.enqueue("impl", "architecture", workspace_id="ws-x")
    j_other = q.enqueue("anything", "research", workspace_id="ws-y")
    claimed1 = q.claim()
    assert claimed1 is not None and claimed1.id == j_plan.id
    claimed2 = q.claim()
    assert claimed2 is not None and claimed2.id == j_other.id
    assert j_impl.id not in {claimed1.id, claimed2.id}


def test_list_jobs_workspace_id_scopes_results(q: Queue) -> None:
    j1 = q.enqueue("plan", "research", workspace_id="ws-a")
    q.enqueue("plan", "research", workspace_id="ws-b")
    q.claim()
    running_a = q.list_jobs(task_id="plan", workspace_id="ws-a", status=JobStatus.RUNNING)
    running_b = q.list_jobs(task_id="plan", workspace_id="ws-b", status=JobStatus.RUNNING)
    assert len(running_a) == 1 and running_a[0].id == j1.id
    assert len(running_b) == 0


def test_new_job_has_zero_requeue_count(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.requeue_count == 0


def test_mark_interrupted_and_fail_with_resume_inv_id_sets_marker(q: Queue) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    failed = q.mark_interrupted_and_fail(claimed.id, "boom", resume_inv_id="inv_001")
    assert failed.status is JobStatus.FAILED
    assert failed.resume_inv_id == "inv_001"
    assert failed.interruption_count == 1
    assert failed.requeue_count == 0


def test_mark_interrupted_and_fail_without_resume_inv_id_does_not_set_marker(
    q: Queue,
) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    failed = q.mark_interrupted_and_fail(j.id, "oops")
    assert failed.resume_inv_id is None


def test_mark_interrupted_and_fail_clear_resume_nulls_stale_marker(q: Queue) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    q.requeue_for_resume(claimed.id, "inv_001")
    claimed2 = q.claim()
    assert claimed2 is not None and claimed2.resume_inv_id == "inv_001"
    failed = q.mark_interrupted_and_fail(claimed2.id, "boom", clear_resume=True)
    assert failed.resume_inv_id is None
    assert q.list_resumable_failed("t") == []


def test_mark_interrupted_and_fail_without_clear_resume_preserves_prior_value(
    q: Queue,
) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    q.requeue_for_resume(claimed.id, "inv_001")
    claimed2 = q.claim()
    assert claimed2 is not None
    failed = q.mark_interrupted_and_fail(claimed2.id, "boom")
    assert failed.resume_inv_id == "inv_001"
    assert len(q.list_resumable_failed("t")) == 1


def test_mark_interrupted_and_fail_clear_resume_ignored_when_resume_inv_id_given(
    q: Queue,
) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    failed = q.mark_interrupted_and_fail(
        claimed.id, "boom", resume_inv_id="inv_002", clear_resume=True
    )
    assert failed.resume_inv_id == "inv_002"


def test_requeue_failed_for_resume_bumps_requeue_count(q: Queue) -> None:
    failed = _make_failed_resumable(q)
    assert failed.requeue_count == 0
    requeued = q.requeue_failed_for_resume(failed.id)
    assert requeued.requeue_count == 1


def test_requeue_failed_for_resume_accumulates_requeue_count(q: Queue) -> None:
    j = q.enqueue("t", "impl", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j.id, "inv_001")
    q.claim()
    q.mark_interrupted_and_fail(j.id, "first", resume_inv_id="inv_001")
    q.requeue_failed_for_resume(j.id)
    q.claim()
    q.mark_interrupted_and_fail(j.id, "second", resume_inv_id="inv_001")
    second = q.requeue_failed_for_resume(j.id)
    assert second.requeue_count == 2


def test_requeue_count_for_task_returns_zero_when_no_jobs(q: Queue) -> None:
    assert q.requeue_count_for_task("no-such-task") == 0


def test_requeue_count_for_task_returns_zero_when_never_requeued(q: Queue) -> None:
    q.enqueue("t", "research")
    assert q.requeue_count_for_task("t") == 0


def test_requeue_count_for_task_returns_max_across_jobs(q: Queue) -> None:
    j1 = q.enqueue("t", "impl", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j1.id, "inv_001")
    q.claim()
    q.mark_interrupted_and_fail(j1.id, "err", resume_inv_id="inv_001")
    q.requeue_failed_for_resume(j1.id)
    q.claim()
    q.mark_interrupted_and_fail(j1.id, "err2", resume_inv_id="inv_001")
    q.requeue_failed_for_resume(j1.id)
    q.claim()
    q.complete(j1.id)
    j2 = q.enqueue("t", "impl", triggered_by_invocation_id="inv_002")
    assert q.requeue_count_for_task("t") == 2
    assert j2.requeue_count == 0


def test_has_active_work_returns_false_when_no_jobs(q: Queue) -> None:
    assert q.has_active_work("ws-x") is False


def test_has_active_work_returns_true_for_running_job(q: Queue) -> None:
    q.enqueue("task-a", "research", workspace_id="ws-x")
    q.claim()
    assert q.has_active_work("ws-x") is True


def test_has_active_work_returns_true_for_pending_unpaused_job(q: Queue) -> None:
    q.enqueue("task-a", "research", workspace_id="ws-x")
    assert q.has_active_work("ws-x") is True


def test_has_active_work_returns_false_for_paused_pending_job(q: Queue) -> None:
    q.enqueue("task-a", "research", workspace_id="ws-x")
    q.pause_task("ws-x")
    assert q.has_active_work("ws-x") is False


def test_has_active_work_returns_false_for_completed_jobs(q: Queue) -> None:
    j = q.enqueue("task-a", "research", workspace_id="ws-x")
    q.claim()
    q.complete(j.id)
    assert q.has_active_work("ws-x") is False


def test_has_active_work_ignores_other_workspaces(q: Queue) -> None:
    q.enqueue("task-b", "research", workspace_id="ws-y")
    q.claim()
    assert q.has_active_work("ws-x") is False


def test_list_jobs_for_workspace_returns_all_when_no_status(q: Queue) -> None:
    j1 = q.enqueue("subtask-a", "research", workspace_id="ws-p")
    j2 = q.enqueue("subtask-b", "architecture", workspace_id="ws-p")
    q.enqueue("other", "research", workspace_id="ws-q")
    q.claim()
    jobs = q.list_jobs_for_workspace("ws-p")
    assert {j.id for j in jobs} == {j1.id, j2.id}


def test_list_jobs_for_workspace_filters_by_status(q: Queue) -> None:
    j1 = q.enqueue("subtask-a", "research", workspace_id="ws-p")
    q.enqueue("subtask-b", "architecture", workspace_id="ws-p")
    q.claim()
    running = q.list_jobs_for_workspace("ws-p", status=JobStatus.RUNNING)
    assert len(running) == 1 and running[0].id == j1.id
    pending = q.list_jobs_for_workspace("ws-p", status=JobStatus.PENDING)
    assert len(pending) == 1


def test_list_resumable_failed_by_workspace_returns_failed_with_resume_inv_id(
    q: Queue,
) -> None:
    j = q.enqueue("subtask-a", "impl", workspace_id="ws-m", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j.id, "inv_001")
    q.claim()
    q.mark_interrupted_and_fail(j.id, "interrupted")
    results = q.list_resumable_failed_by_workspace("ws-m")
    assert len(results) == 1
    assert results[0].resume_inv_id == "inv_001"


def test_list_resumable_failed_by_workspace_excludes_other_workspaces(
    q: Queue,
) -> None:
    j = q.enqueue("subtask-a", "impl", workspace_id="ws-m", triggered_by_invocation_id="inv_001")
    q.claim()
    q.requeue_for_resume(j.id, "inv_001")
    q.claim()
    q.mark_interrupted_and_fail(j.id, "interrupted")
    assert q.list_resumable_failed_by_workspace("ws-other") == []


def test_delete_jobs_by_workspace_removes_all_jobs_and_paused_rows(q: Queue) -> None:
    j1 = q.enqueue("sub-a", "research", workspace_id="ws-del")
    q.enqueue("sub-b", "architecture", workspace_id="ws-del")
    q.enqueue("other", "research", workspace_id="ws-keep")
    q.claim()
    q.complete(j1.id)
    q.pause_task("ws-del")
    count = q.delete_jobs_by_workspace("ws-del")
    assert count == 2
    assert q.list_jobs_for_workspace("ws-del") == []
    assert q.list_jobs_for_workspace("ws-keep") != []
    assert q.is_task_paused("ws-del") is False


def test_delete_jobs_by_workspace_returns_zero_for_unknown(q: Queue) -> None:
    q.enqueue("other", "research", workspace_id="ws-keep")
    assert q.delete_jobs_by_workspace("ws-nope") == 0


def test_delete_jobs_by_workspace_rolls_back_on_error(
    q: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    q.enqueue("sub-a", "research", workspace_id="ws-del")
    q.claim()
    q.pause_task("ws-del")
    real_conn = q._conn

    class FailingConn:
        def execute(self, sql: str, parameters: tuple[str, ...] = ()) -> object:
            if sql == queue_mod._DELETE_BY_WORKSPACE_SQL:
                raise RuntimeError("boom")
            return real_conn.execute(sql, parameters)

    monkeypatch.setattr(q, "_conn", FailingConn())
    with pytest.raises(RuntimeError, match="boom"):
        q.delete_jobs_by_workspace("ws-del")
    monkeypatch.setattr(q, "_conn", real_conn)
    assert q.list_jobs_for_workspace("ws-del") != []
    assert q.is_task_paused("ws-del") is True


def test_list_paused_task_ids_for_workspace_returns_workspace_when_paused(q: Queue) -> None:
    q.enqueue("sub-a", "research", workspace_id="ws-p")
    q.enqueue("sub-b", "architecture", workspace_id="ws-p")
    q.claim()
    q.pause_task("ws-p")
    result = q.list_paused_task_ids_for_workspace("ws-p")
    assert result == ["ws-p"]


def test_list_paused_task_ids_for_workspace_empty_when_none_paused(q: Queue) -> None:
    q.enqueue("sub-a", "research", workspace_id="ws-p")
    assert q.list_paused_task_ids_for_workspace("ws-p") == []


def test_list_paused_task_ids_for_workspace_empty_for_unknown(q: Queue) -> None:
    assert q.list_paused_task_ids_for_workspace("ws-nope") == []


def test_migration_workspace_id_rollback_on_alter_failure(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3 as _sqlite3
    from unittest.mock import MagicMock

    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            team TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            parent_handover_id TEXT,
            triggered_by_invocation_id TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            interruption_count INTEGER NOT NULL DEFAULT 0,
            resume_inv_id TEXT,
            instructions TEXT,
            requeue_count INTEGER NOT NULL DEFAULT 0,
            eligible_at REAL,
            server_error_retry_count INTEGER NOT NULL DEFAULT 0,
            instructions_origin TEXT
        );
        """
    )
    conn.close()

    executed: list[str] = []
    real_connect = _sqlite3.connect
    opened_conns: list[_sqlite3.Connection] = []

    def patched_connect(path, **kwargs):
        real_conn = real_connect(path, **kwargs)
        opened_conns.append(real_conn)
        real_conn.row_factory = _sqlite3.Row
        mock_conn: MagicMock = MagicMock()

        def execute_side_effect(sql, *_):
            executed.append(sql)
            if sql.startswith("ALTER TABLE"):
                raise _sqlite3.OperationalError("simulated failure")
            return real_conn.execute(sql)

        mock_conn.execute.side_effect = execute_side_effect
        mock_conn.executescript.side_effect = real_conn.executescript
        return mock_conn

    monkeypatch.setattr(_sqlite3, "connect", patched_connect)
    try:
        with pytest.raises(_sqlite3.OperationalError, match="simulated failure"):
            Queue(db_path=db_path)
    finally:
        for c in opened_conns:
            c.close()

    assert "ROLLBACK" in executed


def test_new_job_has_null_eligible_at(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.eligible_at is None


def test_enqueue_with_eligible_at_defers_job(q: Queue) -> None:
    future = time.time() + 3600.0
    job = q.enqueue("t", "research", eligible_at=future)
    assert job.eligible_at is not None
    assert abs(job.eligible_at - future) < 0.01
    assert q.claim() is None


def test_new_job_has_zero_server_error_retry_count(q: Queue) -> None:
    job = q.enqueue("t", "research")
    assert job.server_error_retry_count == 0


def test_requeue_with_server_error_backoff_sets_eligible_at_and_bumps_count(
    q: Queue,
) -> None:
    q.enqueue("t", "implementation")
    claimed = q.claim()
    assert claimed is not None
    future = time.time() + 120.0
    requeued = q.requeue_with_server_error_backoff(claimed.id, future)
    assert requeued.status is JobStatus.PENDING
    assert requeued.eligible_at is not None
    assert abs(requeued.eligible_at - future) < 0.01
    assert requeued.server_error_retry_count == 1
    assert requeued.resume_inv_id is None
    assert requeued.started_at is None
    assert requeued.finished_at is None
    assert requeued.error is None


def test_requeue_with_server_error_backoff_preserves_triggered_by_invocation_id(
    q: Queue,
) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    requeued = q.requeue_with_server_error_backoff(claimed.id, time.time() + 60.0)
    assert requeued.triggered_by_invocation_id == "inv_001"


def test_requeue_with_server_error_backoff_clears_resume_inv_id(
    q: Queue,
) -> None:
    q.enqueue("t", "implementation", triggered_by_invocation_id="inv_001")
    claimed = q.claim()
    assert claimed is not None
    q.requeue_for_resume(claimed.id, "inv_001")
    claimed2 = q.claim()
    assert claimed2 is not None and claimed2.resume_inv_id == "inv_001"
    requeued = q.requeue_with_server_error_backoff(claimed2.id, time.time() + 60.0)
    assert requeued.resume_inv_id is None


def test_requeue_with_server_error_backoff_unknown_id_raises(q: Queue) -> None:
    with pytest.raises(QueueError, match="not running"):
        q.requeue_with_server_error_backoff(9999, time.time() + 60.0)


def test_requeue_with_server_error_backoff_status_guard(q: Queue) -> None:
    j = q.enqueue("t", "implementation")
    with pytest.raises(QueueError, match="not running"):
        q.requeue_with_server_error_backoff(j.id, time.time() + 60.0)


def test_claim_skips_job_with_future_eligible_at(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.requeue_with_server_error_backoff(j.id, time.time() + 3600.0)
    assert q.claim() is None


def test_claim_returns_job_when_eligible_at_elapsed(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.requeue_with_server_error_backoff(j.id, time.time() - 1.0)
    claimed = q.claim()
    assert claimed is not None and claimed.id == j.id


def test_eligible_at_persists_across_queue_instances(db_path: Path) -> None:
    q1 = Queue(db_path=db_path)
    j = q1.enqueue("t", "research")
    q1.claim()
    future = time.time() + 3600.0
    q1.requeue_with_server_error_backoff(j.id, future)
    q1.close()
    q2 = Queue(db_path=db_path)
    try:
        jobs = q2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].status is JobStatus.PENDING
        assert jobs[0].eligible_at is not None
        assert abs(jobs[0].eligible_at - future) < 0.01
        assert q2.claim() is None
    finally:
        q2.close()


def test_requeue_pending_clears_eligible_at(q: Queue) -> None:
    j = q.enqueue("t", "research")
    q.claim()
    q.requeue_with_server_error_backoff(j.id, time.time() + 3600.0)
    claimed = q.claim()
    assert claimed is None
    q2_job = q.list_jobs()[0]
    q._conn.execute(  # noqa: SLF001 - white-box: manually flip to running to test requeue_pending
        "UPDATE jobs SET status = ? WHERE id = ?",
        (JobStatus.RUNNING.value, q2_job.id),
    )
    requeued = q.requeue_pending(q2_job.id)
    assert requeued.eligible_at is None


def test_migration_adds_eligible_at_and_server_error_retry_count_columns(
    db_path: Path,
) -> None:
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            team TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            parent_handover_id TEXT,
            triggered_by_invocation_id TEXT,
            enqueued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            interruption_count INTEGER NOT NULL DEFAULT 0,
            resume_inv_id TEXT,
            instructions TEXT
        );
        INSERT INTO jobs (task_id, team, status, enqueued_at)
        VALUES ('t', 'research', 'pending', '2026-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()
    q = Queue(db_path=db_path)
    try:
        jobs = q.list_jobs()
        assert jobs[0].eligible_at is None
        assert jobs[0].server_error_retry_count == 0
    finally:
        q.close()
