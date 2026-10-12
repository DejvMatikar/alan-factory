from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, overload

from pydantic import BaseModel, Field

__all__ = [
    "JOB_TABLE",
    "WORKSPACE_TABLE",
    "Blocker",
    "BlockerKind",
    "Effect",
    "JobPlan",
    "JobRow",
    "JobStatus",
    "JobTransition",
    "ParkReason",
    "Status",
    "WorkspaceRow",
    "advance_target",
    "held_by_unarchive",
    "pre_archive_status",
    "stamps_finish",
    "transition",
    "unarchive_target",
]


class Status(StrEnum):
    ACTIVE = "active"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    ESCALATED = "escalated"
    FAILED = "failed"
    PAUSED = "paused"
    PARTIAL = "partial"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ParkReason(StrEnum):
    QUESTION = "question"
    CRASH = "crash"
    MERGE_BLOCK = "merge_block"
    STALL = "stall"
    ROUTING_ERROR = "routing_error"
    ESCALATED = "escalated"
    FROZEN_CHAIN = "frozen_chain"
    CREDENTIAL = "credential"
    UNARCHIVED = "unarchived"


BlockerKind = Literal[
    "reason",
    "failing_check",
    "pending_check",
    "failed_reason",
    "review_decision",
    "merge_state_status",
    "quarantined_check",
    "failing_test",
    "conflicted_path",
    "behind_commit",
]


class Blocker(BaseModel):
    kind: BlockerKind
    name: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    instruction: str | None = None
    action_url: str | None = None


class Effect(StrEnum):
    KEEP = "keep"
    NULL = "null"
    SET = "set"
    INCREMENT = "increment"


class JobTransition(StrEnum):
    INTERRUPT = "interrupt"
    INTERRUPT_WITH_RESUME = "interrupt_with_resume"
    INTERRUPT_CLEAR_RESUME = "interrupt_clear_resume"
    REQUEUE_RESUME = "requeue_resume"
    REQUEUE_PENDING = "requeue_pending"
    REQUEUE_FAILED_FOR_RESUME = "requeue_failed_for_resume"
    REQUEUE_FAILED_WITH_RESUME = "requeue_failed_with_resume"
    REQUEUE_SERVER_ERROR = "requeue_server_error"
    HOLD_UNTIL_READY = "hold_until_ready"


@dataclass(frozen=True)
class JobRow:
    from_status: JobStatus
    to_status: JobStatus
    miss_message: str
    started_at: Effect = Effect.KEEP
    finished_at: Effect = Effect.KEEP
    error: Effect = Effect.KEEP
    resume_inv_id: Effect = Effect.KEEP
    interruption_count: Effect = Effect.KEEP
    requeue_count: Effect = Effect.KEEP
    eligible_at: Effect = Effect.KEEP
    server_error_retry_count: Effect = Effect.KEEP


@dataclass(frozen=True)
class JobPlan:
    row: JobRow
    error: str | None = None
    resume_inv_id: str | None = None
    eligible_at: float | None = None
    server_error_retry_count: int = 0


@dataclass(frozen=True)
class WorkspaceRow:
    clears_park_reason: bool
    clears_blockers: bool


_NOT_RUNNING = "job {job_id} is not running"
_NOT_FAILED = "job {job_id} is not a failed job"
_INTERRUPT = JobRow(
    JobStatus.RUNNING,
    JobStatus.FAILED,
    _NOT_RUNNING,
    finished_at=Effect.SET,
    error=Effect.SET,
    interruption_count=Effect.INCREMENT,
)
_REQUEUE = JobRow(
    JobStatus.RUNNING,
    JobStatus.PENDING,
    _NOT_RUNNING,
    started_at=Effect.NULL,
    finished_at=Effect.NULL,
    error=Effect.NULL,
    resume_inv_id=Effect.NULL,
    interruption_count=Effect.INCREMENT,
    eligible_at=Effect.NULL,
    server_error_retry_count=Effect.SET,
)
_REQUEUE_FAILED = JobRow(
    JobStatus.FAILED,
    JobStatus.PENDING,
    _NOT_FAILED,
    started_at=Effect.NULL,
    finished_at=Effect.NULL,
    error=Effect.NULL,
    interruption_count=Effect.INCREMENT,
    requeue_count=Effect.INCREMENT,
    eligible_at=Effect.NULL,
    server_error_retry_count=Effect.SET,
)

_HOLD = JobRow(
    JobStatus.RUNNING,
    JobStatus.PENDING,
    _NOT_RUNNING,
    started_at=Effect.NULL,
    eligible_at=Effect.SET,
)

JOB_TABLE: Mapping[JobTransition, JobRow] = {
    JobTransition.INTERRUPT: _INTERRUPT,
    JobTransition.INTERRUPT_WITH_RESUME: replace(_INTERRUPT, resume_inv_id=Effect.SET),
    JobTransition.INTERRUPT_CLEAR_RESUME: replace(_INTERRUPT, resume_inv_id=Effect.NULL),
    JobTransition.REQUEUE_RESUME: replace(_REQUEUE, resume_inv_id=Effect.SET),
    JobTransition.REQUEUE_PENDING: _REQUEUE,
    JobTransition.REQUEUE_FAILED_FOR_RESUME: _REQUEUE_FAILED,
    JobTransition.REQUEUE_FAILED_WITH_RESUME: replace(
        _REQUEUE_FAILED, resume_inv_id=Effect.SET, eligible_at=Effect.SET
    ),
    JobTransition.REQUEUE_SERVER_ERROR: replace(
        _REQUEUE,
        interruption_count=Effect.KEEP,
        eligible_at=Effect.SET,
        server_error_retry_count=Effect.INCREMENT,
    ),
    JobTransition.HOLD_UNTIL_READY: _HOLD,
}

_CLEARS_PARK_STATE = WorkspaceRow(clears_park_reason=True, clears_blockers=True)
_KEEPS_PARK_STATE = WorkspaceRow(clears_park_reason=False, clears_blockers=False)

WORKSPACE_TABLE: Mapping[Status, WorkspaceRow] = {
    Status.ACTIVE: _CLEARS_PARK_STATE,
    Status.AWAITING_INPUT: _KEEPS_PARK_STATE,
    Status.COMPLETED: _CLEARS_PARK_STATE,
    Status.ARCHIVED: _KEEPS_PARK_STATE,
    Status.ESCALATED: _KEEPS_PARK_STATE,
    Status.FAILED: _KEEPS_PARK_STATE,
    Status.PAUSED: _KEEPS_PARK_STATE,
    Status.PARTIAL: _KEEPS_PARK_STATE,
}


_FINISHED_STATUSES = frozenset(
    {Status.COMPLETED, Status.FAILED, Status.ESCALATED, Status.PARTIAL}
)


def stamps_finish(
    prev_status: Status, new_status: Status, current_terminal_at: datetime | None
) -> bool:
    if prev_status is Status.ARCHIVED or new_status not in _FINISHED_STATUSES:
        return False
    return prev_status is not new_status or current_terminal_at is None


_RESTORES_AS_SAVED = frozenset(
    {
        Status.COMPLETED,
        Status.FAILED,
        Status.PARTIAL,
        Status.ESCALATED,
        Status.AWAITING_INPUT,
        Status.PAUSED,
    }
)


def pre_archive_status(prev: Status, new: Status, current: Status | None) -> Status | None:
    if new is not Status.ARCHIVED:
        return None
    return current if prev is Status.ARCHIVED else prev


def unarchive_target(
    pre: Status | None, *, has_pause_marker: bool
) -> tuple[Status, ParkReason | None]:
    if has_pause_marker:
        return Status.PAUSED, None
    if pre is not None and pre in _RESTORES_AS_SAVED:
        return pre, None
    return Status.AWAITING_INPUT, ParkReason.UNARCHIVED


def held_by_unarchive(status: Status, park_reason: ParkReason | None) -> bool:
    return status is Status.AWAITING_INPUT and park_reason is ParkReason.UNARCHIVED


def _field_update(
    name: str, value: Any, *, clear: bool, cleared: Any
) -> dict[str, Any]:
    if value is not None:
        return {name: value}
    return {name: cleared} if clear else {}


def _plan_workspace(
    status: Status,
    *,
    merge_block_class: str | None = None,
    clear_merge_block_class: bool = False,
    park_reason: ParkReason | None = None,
    blockers: list[Blocker] | None = None,
    clear_park_reason: bool = False,
    clear_blockers: bool = False,
) -> dict[str, Any]:
    row = WORKSPACE_TABLE[status]
    return {
        "status": status,
        **_field_update(
            "merge_block_class",
            merge_block_class,
            clear=clear_merge_block_class,
            cleared=None,
        ),
        **_field_update(
            "park_reason",
            park_reason,
            clear=clear_park_reason or row.clears_park_reason,
            cleared=None,
        ),
        **_field_update(
            "blockers",
            blockers,
            clear=clear_blockers or row.clears_blockers,
            cleared=[],
        ),
    }


@overload  # pragma: no cover
def transition(
    target: Status,
    *,
    merge_block_class: str | None = None,
    clear_merge_block_class: bool = False,
    park_reason: ParkReason | None = None,
    blockers: list[Blocker] | None = None,
    clear_park_reason: bool = False,
    clear_blockers: bool = False,
) -> dict[str, Any]: ...


@overload  # pragma: no cover
def transition(
    target: JobTransition,
    *,
    error: str | None = None,
    resume_inv_id: str | None = None,
    eligible_at: float | None = None,
    server_error_retry_count: int = 0,
) -> JobPlan: ...


def transition(target: Status | JobTransition, **fields: Any) -> dict[str, Any] | JobPlan:
    if isinstance(target, JobTransition):
        return JobPlan(JOB_TABLE[target], **fields)
    return _plan_workspace(target, **fields)


def advance_target(
    current: Status, *, has_block_class: bool, has_awaiting_subtask: bool
) -> Status | None:
    if current is Status.ESCALATED:
        return None if has_awaiting_subtask else Status.ACTIVE
    return current if has_block_class else None
