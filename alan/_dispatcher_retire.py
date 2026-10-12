from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import alan._dispatcher_base as _base
from alan import lifecycle
from alan import workspace as workspace_mod
from alan.archive_guard import ArchiveRequest, record_forced_archive, refusal_detail
from alan.clock import SYSTEM_CLOCK, Clock
from alan.queue import JobStatus, Queue
from alan.workspace import Status

_DEFAULT_SETTLE_TIMEOUT_S = 5.0
_DEFAULT_SETTLE_POLL_INTERVAL_S = 0.1

WORKSPACE_DELETE_BLOCKED_DETAIL = (
    "workspace still has an active job finishing up; wait a moment and retry"
)
WORKSPACE_ACTIVE_JOBS_DETAIL = "workspace has active jobs"


@dataclass(frozen=True)
class RetireResult:
    deleted: bool
    blocked_reason: str | None = None


def _settle_for_delete(
    queue: Queue,
    workspace_id: str,
    *,
    clock: Clock,
    timeout_s: float,
    poll_interval_s: float,
    sleep: Callable[[float], None],
) -> bool:
    # Bypasses has_active_work's paused_tasks exclusion on purpose: that
    # exclusion is what lets a stopped-but-still-unwinding job look settled
    # before it actually is (docs/architecture/workspace_deletion.md, Shape A).
    deadline = clock.monotonic().value + timeout_s
    while queue.list_jobs_for_workspace(workspace_id, status=JobStatus.RUNNING):
        if clock.monotonic().value >= deadline:
            return False
        sleep(poll_interval_s)
    return True


def retire_workspace(
    dispatcher: _base.DispatcherState | None,
    task_id: str,
    queue: Queue,
    workspaces_root: Path | None,
    *,
    clock: Clock = SYSTEM_CLOCK,
    settle_timeout_s: float = _DEFAULT_SETTLE_TIMEOUT_S,
    poll_interval_s: float = _DEFAULT_SETTLE_POLL_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> RetireResult:
    # With no live dispatcher there is no worker thread to race — settling
    # would just burn settle_timeout_s waiting on a row nothing will ever
    # clear, so both the lock and the settle wait are skipped in that mode.
    lock = (
        dispatcher._get_workspace_lock(task_id)  # pyright: ignore[reportPrivateUsage]
        if dispatcher is not None
        else contextlib.nullcontext()
    )
    with lock:
        if dispatcher is not None and not _settle_for_delete(
            queue, task_id, clock=clock, timeout_s=settle_timeout_s,
            poll_interval_s=poll_interval_s, sleep=sleep,
        ):
            return RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
        if queue.has_active_work(task_id):
            return RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
        queue.delete_jobs_by_workspace(task_id)
        try:
            workspace_mod.delete(task_id, root=workspaces_root)
        except workspace_mod.WorkspaceError:
            pass
        if workspace_mod.exists(task_id, root=workspaces_root):
            return RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
        return RetireResult(deleted=True)


@dataclass(frozen=True)
class ArchiveOutcome:
    archived: bool
    detail: str | None = None


def archive_or_refuse(
    dispatcher: _base.DispatcherState | None,
    task_id: str,
    queue: Queue,
    workspaces_root: Path | None,
    body: ArchiveRequest | None,
) -> ArchiveOutcome:
    lock = (
        dispatcher._get_workspace_lock(task_id)  # pyright: ignore[reportPrivateUsage]
        if dispatcher is not None
        else contextlib.nullcontext()
    )
    with lock:
        ws = workspace_mod.read(task_id, root=workspaces_root)
        if queue.has_active_work(task_id):
            return ArchiveOutcome(archived=False, detail=WORKSPACE_ACTIVE_JOBS_DETAIL)
        detail = refusal_detail(ws)
        if detail is not None:
            if body is None or not body.force:
                return ArchiveOutcome(archived=False, detail=detail)
            workspace_mod.transition(ws, Status.ARCHIVED)
            record_forced_archive(ws, body.reason)
            return ArchiveOutcome(archived=True)
        workspace_mod.transition(ws, Status.ARCHIVED)
        return ArchiveOutcome(archived=True)


def restore_from_archive(ws: workspace_mod.Workspace, queue: Queue) -> workspace_mod.Workspace:
    has_pause_marker = bool(queue.list_paused_task_ids_for_workspace(ws.task_id))
    status, park_reason = lifecycle.unarchive_target(
        ws.meta.pre_archive_status, has_pause_marker=has_pause_marker
    )
    fallback = park_reason is not None
    return workspace_mod.transition(
        ws,
        status,
        park_reason=park_reason,
        clear_blockers=fallback,
        clear_merge_block_class=fallback,
    )
