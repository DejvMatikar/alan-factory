from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome

MID_RUN_WAKE_FLOOR_S: float = 180.0
BACKOFF_BASE_S: float = 300.0
BACKOFF_CAP_S: float = 14400.0

_BACKOFF_COUNTED: frozenset[RunOutcome] = frozenset({RunOutcome.FAILED, RunOutcome.TIMED_OUT})
_BACKOFF_SKIPPED: frozenset[RunOutcome] = frozenset(
    {RunOutcome.HELD_USAGE_PAUSE, RunOutcome.UNKNOWN_AFTER_RESTART}
)


class HoldReason(StrEnum):
    NOT_SUPERVISED = "not_supervised"
    DISABLED = "disabled"
    RUNNING = "running"
    PAUSED = "paused"
    USAGE_PAUSE = "usage_pause"
    INTERACTIVE_LOCK = "interactive_lock"
    FAST_FORWARD_FAILED = "fast_forward_failed"
    BACKOFF = "backoff"


DecisionAction = Literal["hold", "idle", "spawn"]


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    reason: HoldReason | None = None
    detail: str | None = None
    next_wake_at: float | None = None


@dataclass(frozen=True)
class ScheduleContext:
    supervised: bool
    entry: DriverLoopEntry
    paused: bool
    running: bool
    usage_paused_until: float | None
    interactive_lock_present: bool
    fast_forward_error: str | None
    recent_outcomes: tuple[RunOutcome, ...]
    last_run_started_at: float | None
    last_run_ended_at: float | None
    self_paced_next_wake_at: float | None
    now: float


def backoff_failure_streak(recent_outcomes: tuple[RunOutcome, ...]) -> int:
    streak = 0
    for outcome in reversed(recent_outcomes):
        if outcome in _BACKOFF_SKIPPED:
            continue
        if outcome in _BACKOFF_COUNTED:
            streak += 1
            continue
        break
    return streak


def backoff_delay_s(streak: int) -> float:
    if streak <= 0:
        return 0.0
    return float(min(BACKOFF_BASE_S * (2 ** (streak - 1)), BACKOFF_CAP_S))


def _backoff_until(ctx: ScheduleContext) -> float | None:
    if ctx.last_run_ended_at is None:
        return None
    streak = backoff_failure_streak(ctx.recent_outcomes)
    delay = backoff_delay_s(streak)
    if delay <= 0.0:
        return None
    return ctx.last_run_ended_at + delay


def _fixed_interval_due_at(ctx: ScheduleContext) -> float:
    interval = ctx.entry.interval_seconds
    if interval is None:
        raise ValueError("fixed-interval schedule requires interval_seconds")
    if ctx.last_run_started_at is None:
        return ctx.now
    due = ctx.last_run_started_at + interval
    if ctx.last_run_ended_at is not None:
        due = max(due, ctx.last_run_ended_at + MID_RUN_WAKE_FLOOR_S)
    return due


def _self_paced_due_at(ctx: ScheduleContext) -> float:
    if ctx.self_paced_next_wake_at is None:
        return ctx.now
    return ctx.self_paced_next_wake_at


def _due_at(ctx: ScheduleContext) -> float:
    if ctx.entry.self_paced:
        return _self_paced_due_at(ctx)
    return _fixed_interval_due_at(ctx)


def _static_hold(ctx: ScheduleContext) -> Decision | None:
    if not ctx.supervised:
        return Decision("hold", HoldReason.NOT_SUPERVISED)
    if not ctx.entry.enabled:
        return Decision("hold", HoldReason.DISABLED)
    if ctx.running:
        return Decision("hold", HoldReason.RUNNING)
    if ctx.paused:
        return Decision("hold", HoldReason.PAUSED)
    return None


def _external_hold(ctx: ScheduleContext) -> Decision | None:
    if ctx.usage_paused_until is not None and ctx.usage_paused_until > ctx.now:
        return Decision("hold", HoldReason.USAGE_PAUSE, next_wake_at=ctx.usage_paused_until)
    if ctx.interactive_lock_present:
        return Decision("hold", HoldReason.INTERACTIVE_LOCK)
    if ctx.fast_forward_error is not None:
        return Decision("hold", HoldReason.FAST_FORWARD_FAILED, detail=ctx.fast_forward_error)
    return None


def _backoff_hold(ctx: ScheduleContext) -> Decision | None:
    backoff_until = _backoff_until(ctx)
    if backoff_until is not None and backoff_until > ctx.now:
        return Decision("hold", HoldReason.BACKOFF, next_wake_at=backoff_until)
    return None


_HOLD_CHECKS: tuple[Callable[[ScheduleContext], Decision | None], ...] = (
    _static_hold,
    _external_hold,
    _backoff_hold,
)


def decide(ctx: ScheduleContext) -> Decision:
    for check in _HOLD_CHECKS:
        decision = check(ctx)
        if decision is not None:
            return decision
    due_at = _due_at(ctx)
    if due_at > ctx.now:
        return Decision("idle", next_wake_at=due_at)
    return Decision("spawn")


__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "MID_RUN_WAKE_FLOOR_S",
    "Decision",
    "HoldReason",
    "ScheduleContext",
    "backoff_delay_s",
    "backoff_failure_streak",
    "decide",
]
