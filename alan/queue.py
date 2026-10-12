from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from collections.abc import Generator, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict

from alan import lifecycle
from alan.clock import SYSTEM_CLOCK, Clock, Epoch
from alan.lifecycle import Effect, JobPlan, JobTransition
from alan.lifecycle import JobStatus as JobStatus
from alan.usage_limit import SPEND_CAP_HOLD_EPOCH

QUEUE_DB = Path.home() / ".alan" / "queue.db"
_MIN_SQLITE_VERSION = (3, 35)

# Maximum concurrent jobs per workspace (intra-workspace fan-out cap).
# INTRA_WORKSPACE_CAP is the pool-off default: CI (ALAN_SLOT_POOL_ROOT="") and any
# deployment without a slot pool stay serial. INTRA_WORKSPACE_CAP_POOLED applies only
# when a slot pool is active, so concurrent same-workspace jobs always run in isolated
# clones. The cap is selected from actual pool presence in alan.routes.infra.
# Legacy single-task workspaces remain serial via the (workspace_id, task_id)
# exclusion in _CLAIM_SQL regardless of this cap.
INTRA_WORKSPACE_CAP: int = 1
INTRA_WORKSPACE_CAP_POOLED: int = 2
READINESS_HOLD_DELAY_S: float = 60.0

_log = logging.getLogger(__name__)


class Job(BaseModel):
    id: int
    task_id: str
    workspace_id: str = ""
    team: str
    status: JobStatus
    parent_handover_id: str | None = None
    triggered_by_invocation_id: str | None = None
    enqueued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    interruption_count: int = 0
    resume_inv_id: str | None = None
    instructions: str | None = None
    instructions_origin: str | None = None
    requeue_count: int = 0
    eligible_at: float | None = None
    server_error_retry_count: int = 0


class SeatJobFacts(BaseModel):
    has_running: bool
    has_pending_pickable: bool
    last_activity: datetime | None = None


class QueueError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
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
    instructions_origin TEXT,
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

_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "interruption_count",
        "ALTER TABLE jobs ADD COLUMN interruption_count INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "resume_inv_id",
        "ALTER TABLE jobs ADD COLUMN resume_inv_id TEXT",
    ),
    (
        "instructions",
        "ALTER TABLE jobs ADD COLUMN instructions TEXT",
    ),
    (
        "requeue_count",
        "ALTER TABLE jobs ADD COLUMN requeue_count INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "eligible_at",
        "ALTER TABLE jobs ADD COLUMN eligible_at REAL",
    ),
    (
        "server_error_retry_count",
        "ALTER TABLE jobs ADD COLUMN server_error_retry_count INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "instructions_origin",
        "ALTER TABLE jobs ADD COLUMN instructions_origin TEXT",
    ),
)

_INSERT_SQL = (
    "INSERT INTO jobs (task_id, workspace_id, team, status, parent_handover_id, "
    "triggered_by_invocation_id, enqueued_at, instructions, eligible_at, "
    "instructions_origin) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *"
)
_CLAIM_SQL = (
    "UPDATE jobs SET status = ?, started_at = ? "
    "WHERE id = ("
    "  SELECT id FROM jobs WHERE status = ?"
    "  AND NOT EXISTS ("
    "    SELECT 1 FROM jobs j3"
    "    WHERE j3.workspace_id = jobs.workspace_id"
    "    AND j3.task_id = jobs.task_id"
    "    AND j3.status = ?"
    "  )"
    "  AND workspace_id NOT IN (SELECT task_id FROM paused_tasks)"
    "  AND ("
    "    workspace_id = ''"
    "    OR (SELECT COUNT(*) FROM jobs j2"
    "        WHERE j2.workspace_id = jobs.workspace_id"
    "        AND j2.status = ?"
    "        AND j2.workspace_id != '') < ?"
    "  )"
    "  AND (eligible_at IS NULL OR eligible_at <= ?)"
    "  ORDER BY id LIMIT 1"
    ") RETURNING *"
)
_FINISH_SQL = (
    "UPDATE jobs SET status = ?, finished_at = ?, error = ?, resume_inv_id = NULL "
    "WHERE id = ? AND status = ? RETURNING *"
)
_GET_BY_ID_SQL = "SELECT * FROM jobs WHERE id = ?"
_TRANSITION_SQL = (
    "UPDATE jobs SET status = ?, "
    "started_at = CASE WHEN ? THEN ? ELSE started_at END, "
    "finished_at = CASE WHEN ? THEN ? ELSE finished_at END, "
    "error = CASE WHEN ? THEN ? ELSE error END, "
    "resume_inv_id = CASE WHEN ? THEN ? ELSE resume_inv_id END, "
    "interruption_count = interruption_count + ?, "
    "requeue_count = requeue_count + ?, "
    "eligible_at = CASE WHEN ? THEN ? ELSE eligible_at END, "
    "server_error_retry_count = "
    "CASE WHEN ? THEN ? ELSE server_error_retry_count + ? END "
    "WHERE id = ? AND status = ? RETURNING *"
)
_ADVANCE_ELIGIBLE_AT_SQL = (
    "UPDATE jobs SET eligible_at = ?, instructions = ?, instructions_origin = NULL "
    "WHERE id = ? AND status = ? AND eligible_at IS NOT NULL AND eligible_at > ? "
    "RETURNING *"
)
_REQUEUE_COUNT_FOR_TASK_SQL = (
    "SELECT COALESCE(MAX(requeue_count), 0) FROM jobs WHERE task_id = ?"
)
_LIST_RESUMABLE_FAILED_SQL = (
    "SELECT * FROM jobs WHERE task_id = ? AND status = ? AND resume_inv_id IS NOT NULL "
    "AND resume_inv_id != '' ORDER BY id DESC"
)
_PAUSE_UNTIL_SQL = (
    "INSERT INTO queue_meta (key, value) VALUES ('paused_until', ?) "
    "ON CONFLICT(key) DO UPDATE SET value = "
    "CAST(MAX(CAST(value AS REAL), CAST(excluded.value AS REAL)) AS TEXT)"
)
WORKER_STATE_META_KEY = "dispatcher_worker_state_v2"
FORGE_DEGRADED_META_KEY = "forge_degraded_v1"
SPEND_CAP_STOP_META_KEY = "spend_cap_stop_v1"


class ForgeDegradedMarker(BaseModel):
    model_config = ConfigDict(frozen=True)

    reset_at: float
    detail: Annotated[str, BeforeValidator(str)] = ""
    remaining: int | None = None
    limit: int | None = None


def parse_forge_degraded(raw: str | None) -> ForgeDegradedMarker | None:
    if raw is None:
        return None
    try:
        return ForgeDegradedMarker.model_validate(json.loads(raw))
    except ValueError:
        return None


_GET_META_SQL = "SELECT value FROM queue_meta WHERE key = ?"
_SET_META_SQL = (
    "INSERT INTO queue_meta (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)
_DELETE_META_SQL = "DELETE FROM queue_meta WHERE key = ?"
_LIST_ALL_SQL = "SELECT * FROM jobs ORDER BY id"
_LIST_BY_TASK_SQL = "SELECT * FROM jobs WHERE task_id = ? ORDER BY id"
_LIST_BY_STATUS_SQL = "SELECT * FROM jobs WHERE status = ? ORDER BY id"
_LIST_RUNNING_BY_TASK_SQL = (
    "SELECT * FROM jobs WHERE task_id = ? AND status = ? ORDER BY id"
)
_LIST_BY_WORKSPACE_TASK_STATUS_SQL = (
    "SELECT * FROM jobs WHERE workspace_id = ? AND task_id = ? AND status = ? ORDER BY id"
)
_LIST_BY_WORKSPACE_STATUS_SQL = (
    "SELECT * FROM jobs WHERE workspace_id = ? AND status = ? ORDER BY id"
)
_LIST_ALL_BY_WORKSPACE_SQL = "SELECT * FROM jobs WHERE workspace_id = ? ORDER BY id"
_LIST_RESUMABLE_FAILED_BY_WORKSPACE_SQL = (
    "SELECT * FROM jobs WHERE workspace_id = ? AND status = ? "
    "AND resume_inv_id IS NOT NULL AND resume_inv_id != '' ORDER BY id DESC"
)
_HAS_ACTIVE_WORK_SQL = (
    "SELECT 1 FROM jobs WHERE workspace_id = ? AND status IN (?, ?) "
    "AND workspace_id NOT IN (SELECT task_id FROM paused_tasks) LIMIT 1"
)
_DELETE_BY_WORKSPACE_SQL = "DELETE FROM jobs WHERE workspace_id = ?"
_LIST_PAUSED_TASK_IDS_FOR_WORKSPACE_SQL = (
    "SELECT task_id FROM paused_tasks WHERE task_id = ?"
)
_COUNT_BY_WORKSPACE_SQL = (
    "SELECT workspace_id, COUNT(*) AS n FROM jobs GROUP BY workspace_id"
)
_DELETE_BY_TASK_SQL = "DELETE FROM jobs WHERE task_id = ?"
_PAUSE_TASK_SQL = "INSERT OR IGNORE INTO paused_tasks (task_id) VALUES (?)"
_UNPAUSE_TASK_SQL = "DELETE FROM paused_tasks WHERE task_id = ?"
_IS_TASK_PAUSED_SQL = "SELECT 1 FROM paused_tasks WHERE task_id = ?"
_LIST_PAUSED_SQL = "SELECT task_id FROM paused_tasks ORDER BY task_id"


class Queue:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        intra_workspace_cap: int = 1,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        if intra_workspace_cap < 1:
            raise QueueError(f"intra_workspace_cap must be >= 1, got {intra_workspace_cap}")
        if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
            required = ".".join(str(n) for n in _MIN_SQLITE_VERSION)
            raise QueueError(
                f"SQLite >= {required} required for RETURNING clause; "
                f"got {sqlite3.sqlite_version}"
            )
        path = db_path if db_path is not None else QUEUE_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._intra_workspace_cap = intra_workspace_cap
        self._clock = clock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._apply_migrations()

    def _apply_migrations(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                self._conn.execute(ddl)
        col_added = "workspace_id" not in existing
        if col_added:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''"
                )
                self._conn.execute(
                    "UPDATE jobs SET workspace_id = task_id WHERE workspace_id = ''"
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS jobs_workspace_id ON jobs(workspace_id)"
        )

    def enqueue(
        self,
        task_id: str,
        team: str,
        *,
        workspace_id: str | None = None,
        parent_handover_id: str | None = None,
        triggered_by_invocation_id: str | None = None,
        instructions: str | None = None,
        instructions_origin: str | None = None,
        eligible_at: float | None = None,
    ) -> Job:
        _workspace_id = workspace_id if workspace_id is not None else task_id
        with self._lock:
            self._check_open()
            cur = self._conn.execute(
                _INSERT_SQL,
                (
                    task_id,
                    _workspace_id,
                    team,
                    JobStatus.PENDING.value,
                    parent_handover_id,
                    triggered_by_invocation_id,
                    _now_iso(),
                    instructions,
                    eligible_at,
                    instructions_origin,
                ),
            )
            row = cur.fetchone()
        return _row_to_job(row)

    def claim(self) -> Job | None:
        with self._lock:
            self._check_open()
            if self.paused_until() is not None:
                return None
            cur = self._conn.execute(
                _CLAIM_SQL,
                (
                    JobStatus.RUNNING.value,
                    _now_iso(),
                    JobStatus.PENDING.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RUNNING.value,
                    self._intra_workspace_cap,
                    self._clock.now().value,
                ),
            )
            row = cur.fetchone()
        return _row_to_job(row) if row is not None else None

    def run(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            self._check_open()
            return self._conn.execute(sql, tuple(params)).fetchall()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            self._check_open()
            row = self._conn.execute(_GET_META_SQL, (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._check_open()
            self._conn.execute(_SET_META_SQL, (key, value))

    def delete_meta(self, key: str) -> None:
        with self._lock:
            self._check_open()
            self._conn.execute(_DELETE_META_SQL, (key,))

    def paused_until(self) -> float | None:
        val = self.get_meta("paused_until")
        if val is None:
            return None
        try:
            epoch = float(val)
        except ValueError:
            _log.warning("paused_until meta has non-numeric value %r; treating as not paused", val)
            return None
        if Epoch(epoch) <= self._clock.now():
            return None
        return epoch

    def pause_until(self, epoch: float) -> None:
        with self._lock:
            self._check_open()
            self._conn.execute(_PAUSE_UNTIL_SQL, (str(epoch),))

    @contextlib.contextmanager
    def _transaction(self) -> Generator[None]:
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def stop_until_resume(self, regain_at: str | None) -> None:
        marker = json.dumps(
            {"regain_at": regain_at, "stopped_at": self._clock.now().value}
        )
        with self._lock:
            self._check_open()
            with self._transaction():
                self._conn.execute(_PAUSE_UNTIL_SQL, (str(SPEND_CAP_HOLD_EPOCH),))
                self._conn.execute(_SET_META_SQL, (SPEND_CAP_STOP_META_KEY, marker))

    def clear_pause(self) -> float | None:
        with self._lock:
            self._check_open()
            prior = self.paused_until()
            with self._transaction():
                self._conn.execute(_DELETE_META_SQL, ("paused_until",))
                self._conn.execute(_DELETE_META_SQL, (SPEND_CAP_STOP_META_KEY,))
        return prior

    def complete(self, job_id: int) -> Job:
        return self._finish(job_id, JobStatus.COMPLETED, error=None)

    def fail(self, job_id: int, error: str) -> Job:
        return self._finish(job_id, JobStatus.FAILED, error=error)

    def try_complete(self, job_id: int) -> Job | None:
        with self._lock:
            self._check_open()
            cur = self._conn.execute(
                _FINISH_SQL,
                (JobStatus.COMPLETED.value, _now_iso(), None, job_id, JobStatus.RUNNING.value),
            )
            row = cur.fetchone()
        return _row_to_job(row) if row is not None else None

    def try_complete_or_current(self, job_id: int) -> tuple[Job | None, Job | None]:
        # self._lock is an RLock: nesting try_complete + get_job in one acquisition
        # keeps a caller's failed-completion / current-state read atomic, so a
        # concurrent status transition between the two calls can't misclassify
        # the job (e.g. already-finalized vs. genuinely anomalous).
        with self._lock:
            completed = self.try_complete(job_id)
            if completed is not None:
                return completed, None
            return None, self.get_job(job_id)

    def get_job(self, job_id: int) -> Job | None:
        with self._lock:
            self._check_open()
            row = self._conn.execute(_GET_BY_ID_SQL, (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        task_id: str | None = None,
        workspace_id: str | None = None,
        status: JobStatus | None = None,
    ) -> list[Job]:
        with self._lock:
            self._check_open()
            if workspace_id is not None and task_id is not None and status is not None:
                rows: list[sqlite3.Row] = self._conn.execute(
                    _LIST_BY_WORKSPACE_TASK_STATUS_SQL,
                    (workspace_id, task_id, status.value),
                ).fetchall()
            else:
                rows = self._list_jobs_rows(task_id, status)
        return [_row_to_job(r) for r in rows]

    def _list_jobs_rows(
        self, task_id: str | None, status: JobStatus | None
    ) -> list[sqlite3.Row]:
        if task_id is not None and status is not None:
            return self._conn.execute(
                _LIST_RUNNING_BY_TASK_SQL, (task_id, status.value)
            ).fetchall()
        if task_id is not None:
            return self._conn.execute(_LIST_BY_TASK_SQL, (task_id,)).fetchall()
        if status is not None:
            return self._conn.execute(_LIST_BY_STATUS_SQL, (status.value,)).fetchall()
        return self._conn.execute(_LIST_ALL_SQL).fetchall()

    def list_running(self) -> list[Job]:
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                _LIST_BY_STATUS_SQL, (JobStatus.RUNNING.value,)
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def count_jobs_by_workspace(
        self, *, workspace_ids: Iterable[str] | None = None
    ) -> dict[str, int]:
        with self._lock:
            self._check_open()
            if workspace_ids is None:
                rows = self._conn.execute(_COUNT_BY_WORKSPACE_SQL).fetchall()
            else:
                ids = tuple(workspace_ids)
                if not ids:
                    return {}
                placeholders = ",".join("?" * len(ids))
                rows = self._conn.execute(
                    "SELECT workspace_id, COUNT(*) AS n FROM jobs "  # noqa: S608  # nosec B608 - placeholders are only "?" tokens; values bound via params
                    f"WHERE workspace_id IN ({placeholders}) GROUP BY workspace_id",
                    ids,
                ).fetchall()
        return {str(r["workspace_id"]): int(r["n"]) for r in rows}

    def seat_job_facts(self, workspace_ids: Iterable[str]) -> dict[str, SeatJobFacts]:
        ids = tuple(workspace_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                "SELECT workspace_id, "  # noqa: S608  # nosec B608 - placeholders are only "?" tokens; values bound via params
                "MAX(CASE WHEN status = ? THEN 1 ELSE 0 END) AS has_running, "
                "MAX(CASE WHEN status = ? AND workspace_id NOT IN "
                "(SELECT task_id FROM paused_tasks) THEN 1 ELSE 0 END) AS has_pending_pickable, "
                "MAX(COALESCE(finished_at, started_at, enqueued_at)) AS last_activity "
                "FROM jobs "
                f"WHERE workspace_id IN ({placeholders}) GROUP BY workspace_id",
                (JobStatus.RUNNING.value, JobStatus.PENDING.value, *ids),
            ).fetchall()
        return {
            str(r["workspace_id"]): SeatJobFacts.model_validate(dict(r)) for r in rows
        }

    def mark_interrupted_and_fail(
        self,
        job_id: int,
        error: str,
        *,
        resume_inv_id: str | None = None,
        clear_resume: bool = False,
    ) -> Job:
        member = _interrupt_member(resume_inv_id, clear_resume)
        return self._execute_transition(
            job_id, lifecycle.transition(member, error=error, resume_inv_id=resume_inv_id)
        )

    def requeue_count_for_task(self, task_id: str) -> int:
        with self._lock:
            self._check_open()
            row = self._conn.execute(_REQUEUE_COUNT_FOR_TASK_SQL, (task_id,)).fetchone()
        return int(row[0]) if row is not None else 0

    def requeue_pending(self, job_id: int) -> Job:
        return self._execute_transition(
            job_id, lifecycle.transition(JobTransition.REQUEUE_PENDING)
        )

    def requeue_for_resume(self, job_id: int, resume_inv_id: str) -> Job:
        return self._execute_transition(
            job_id,
            lifecycle.transition(JobTransition.REQUEUE_RESUME, resume_inv_id=resume_inv_id),
        )

    def requeue_with_server_error_backoff(
        self, job_id: int, eligible_at: float
    ) -> Job:
        return self._execute_transition(
            job_id,
            lifecycle.transition(JobTransition.REQUEUE_SERVER_ERROR, eligible_at=eligible_at),
        )

    def hold_until_ready(self, job_id: int) -> Job:
        return self._execute_transition(
            job_id,
            lifecycle.transition(
                JobTransition.HOLD_UNTIL_READY,
                eligible_at=self._clock.now().value + READINESS_HOLD_DELAY_S,
            ),
        )

    def advance_eligible_at(
        self, job_id: int, when: float, *, instructions: str | None = None
    ) -> Job | None:
        with self._lock:
            self._check_open()
            cur = self._conn.execute(
                _ADVANCE_ELIGIBLE_AT_SQL,
                (when, instructions, job_id, JobStatus.PENDING.value, when),
            )
            row = cur.fetchone()
        return _row_to_job(row) if row is not None else None

    def requeue_failed_for_resume(self, job_id: int) -> Job:
        return self._execute_transition(
            job_id, lifecycle.transition(JobTransition.REQUEUE_FAILED_FOR_RESUME)
        )

    def requeue_failed_with_resume(
        self,
        job_id: int,
        resume_inv_id: str | None,
        *,
        eligible_at: float | None = None,
        server_error_retry_count: int = 0,
    ) -> Job:
        return self._execute_transition(
            job_id,
            lifecycle.transition(
                JobTransition.REQUEUE_FAILED_WITH_RESUME,
                resume_inv_id=resume_inv_id,
                eligible_at=eligible_at,
                server_error_retry_count=server_error_retry_count,
            ),
        )

    def list_resumable_failed(self, task_id: str) -> list[Job]:
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                _LIST_RESUMABLE_FAILED_SQL,
                (task_id, JobStatus.FAILED.value),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def list_jobs_for_workspace(
        self, workspace_id: str, *, status: JobStatus | None = None
    ) -> list[Job]:
        with self._lock:
            self._check_open()
            if status is not None:
                rows: list[sqlite3.Row] = self._conn.execute(
                    _LIST_BY_WORKSPACE_STATUS_SQL,
                    (workspace_id, status.value),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    _LIST_ALL_BY_WORKSPACE_SQL,
                    (workspace_id,),
                ).fetchall()
        return [_row_to_job(r) for r in rows]

    def list_resumable_failed_by_workspace(self, workspace_id: str) -> list[Job]:
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                _LIST_RESUMABLE_FAILED_BY_WORKSPACE_SQL,
                (workspace_id, JobStatus.FAILED.value),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def has_active_work(self, workspace_id: str) -> bool:
        with self._lock:
            self._check_open()
            row = self._conn.execute(
                _HAS_ACTIVE_WORK_SQL,
                (workspace_id, JobStatus.RUNNING.value, JobStatus.PENDING.value),
            ).fetchone()
        return row is not None

    def delete_jobs_by_workspace(self, workspace_id: str) -> int:
        with self._lock:
            self._check_open()
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    _UNPAUSE_TASK_SQL,
                    (workspace_id,),
                )
                cur = self._conn.execute(_DELETE_BY_WORKSPACE_SQL, (workspace_id,))
                self._conn.execute("COMMIT")
                return cur.rowcount
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def list_paused_task_ids_for_workspace(self, workspace_id: str) -> list[str]:
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                _LIST_PAUSED_TASK_IDS_FOR_WORKSPACE_SQL,
                (workspace_id,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def delete_jobs(self, task_id: str) -> int:
        with self._lock:
            self._check_open()
            cur = self._conn.execute(_DELETE_BY_TASK_SQL, (task_id,))
            self._conn.execute(_UNPAUSE_TASK_SQL, (task_id,))
            return cur.rowcount

    def pause_task(self, task_id: str) -> None:
        with self._lock:
            self._check_open()
            self._conn.execute(_PAUSE_TASK_SQL, (task_id,))

    def unpause_task(self, task_id: str) -> None:
        with self._lock:
            self._check_open()
            self._conn.execute(_UNPAUSE_TASK_SQL, (task_id,))

    def is_task_paused(self, task_id: str) -> bool:
        with self._lock:
            self._check_open()
            row = self._conn.execute(_IS_TASK_PAUSED_SQL, (task_id,)).fetchone()
        return row is not None

    def list_paused_tasks(self) -> list[str]:
        with self._lock:
            self._check_open()
            rows = self._conn.execute(_LIST_PAUSED_SQL).fetchall()
        return [str(r["task_id"]) for r in rows]

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            with contextlib.suppress(Exception):
                self._conn.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def _check_open(self) -> None:
        if self._closed:
            raise QueueError("queue is closed")

    def _finish(self, job_id: int, status: JobStatus, *, error: str | None) -> Job:
        with self._lock:
            self._check_open()
            cur = self._conn.execute(
                _FINISH_SQL,
                (status.value, _now_iso(), error, job_id, JobStatus.RUNNING.value),
            )
            row = cur.fetchone()
        if row is None:
            raise QueueError(f"job {job_id} is not running")
        return _row_to_job(row)

    def _execute_transition(self, job_id: int, plan: JobPlan) -> Job:
        with self._lock:
            self._check_open()
            row = self._conn.execute(
                _TRANSITION_SQL, _transition_params(job_id, plan)
            ).fetchone()
        if row is None:
            raise QueueError(plan.row.miss_message.format(job_id=job_id))
        return _row_to_job(row)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _interrupt_member(resume_inv_id: str | None, clear_resume: bool) -> JobTransition:
    if resume_inv_id is not None:
        return JobTransition.INTERRUPT_WITH_RESUME
    if clear_resume:
        return JobTransition.INTERRUPT_CLEAR_RESUME
    return JobTransition.INTERRUPT


def _column(effect: Effect, value: object) -> tuple[bool, object]:
    return effect in (Effect.NULL, Effect.SET), value if effect is Effect.SET else None


def _transition_params(job_id: int, plan: JobPlan) -> tuple[object, ...]:
    row = plan.row
    return (
        row.to_status.value,
        *_column(row.started_at, None),
        *_column(row.finished_at, _now_iso()),
        *_column(row.error, plan.error),
        *_column(row.resume_inv_id, plan.resume_inv_id),
        int(row.interruption_count is Effect.INCREMENT),
        int(row.requeue_count is Effect.INCREMENT),
        *_column(row.eligible_at, plan.eligible_at),
        *_column(row.server_error_retry_count, plan.server_error_retry_count),
        int(row.server_error_retry_count is Effect.INCREMENT),
        job_id,
        row.from_status.value,
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job.model_validate(dict(row))
