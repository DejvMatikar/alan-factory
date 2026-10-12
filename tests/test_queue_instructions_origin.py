from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alan.queue import JobStatus, Queue


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


def _create_pre_migration_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT '',
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
            server_error_retry_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS jobs_status_id ON jobs(status, id);
        CREATE INDEX IF NOT EXISTS jobs_task_id ON jobs(task_id);
        CREATE TABLE IF NOT EXISTS queue_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paused_tasks (
            task_id TEXT PRIMARY KEY
        );
        """
    )
    conn.commit()
    conn.close()


def test_migration_adds_instructions_origin_column_to_a_pre_migration_db(
    db_path: Path,
) -> None:
    _create_pre_migration_db(db_path)
    q = Queue(db_path=db_path)
    job = q.enqueue("t1", "implementation_fix")
    assert job.instructions_origin is None
    q.close()


def test_enqueue_persists_instructions_origin(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    job = q.enqueue(
        "t1", "implementation_fix", instructions="do it", instructions_origin="dispatch"
    )
    assert job.instructions_origin == "dispatch"
    q.close()


def test_enqueue_defaults_instructions_origin_to_none(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    job = q.enqueue("t1", "implementation_fix")
    assert job.instructions_origin is None
    q.close()


def test_hydration_round_trips_instructions_origin_via_get_job(db_path: Path) -> None:
    q = Queue(db_path=db_path)
    job = q.enqueue(
        "t1", "implementation_fix", instructions="do it", instructions_origin="inherited"
    )
    fetched = q.get_job(job.id)
    assert fetched is not None
    assert fetched.instructions_origin == "inherited"
    q.close()


def test_advance_eligible_at_nulls_instructions_origin_when_rewriting_instructions(
    db_path: Path,
) -> None:
    q = Queue(db_path=db_path)
    job = q.enqueue(
        "t1",
        "implementation_fix",
        instructions="stale",
        instructions_origin="generated",
        eligible_at=5000.0,
    )
    advanced = q.advance_eligible_at(job.id, 1000.0, instructions="fresh")
    assert advanced is not None
    assert advanced.instructions == "fresh"
    assert advanced.instructions_origin is None
    assert advanced.status is JobStatus.PENDING
    q.close()
