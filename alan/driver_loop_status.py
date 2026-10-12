from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from alan import driver_loop_pause as pause_mod
from alan import driver_loop_runs as runs_mod
from alan.detached_mode import resolve_detached_mode
from alan.driver_loop_registry import DriverLoopEntry, load_registry
from alan.driver_loop_schedule import Decision, ScheduleContext, decide
from alan.queue import Queue
from alan.roots import state_root

_INTERACTIVE_LOCK_NAME = ".claude/scheduled_tasks.lock"

_run_now_lock = threading.Lock()
_run_now_requested: set[str] = set()


def interactive_lock_path(entry: DriverLoopEntry) -> Path:
    return Path(entry.cwd).expanduser() / _INTERACTIVE_LOCK_NAME


def interactive_lock_present(entry: DriverLoopEntry) -> bool:
    return interactive_lock_path(entry).is_file()


def _fast_forward_state_path(name: str) -> Path:
    return state_root() / "driver_loops" / name / "fast_forward_error.txt"


def record_fast_forward_failure(name: str, detail: str) -> None:
    path = _fast_forward_state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(detail, encoding="utf-8")


def clear_fast_forward_failure(name: str) -> None:
    _fast_forward_state_path(name).unlink(missing_ok=True)


def last_fast_forward_failure(name: str) -> str | None:
    path = _fast_forward_state_path(name)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class _RunsSnapshot:
    last_run: runs_mod.RunRecord | None
    running: bool
    recent_outcomes: tuple[runs_mod.RunOutcome, ...]


def _runs_snapshot(entry: DriverLoopEntry) -> _RunsSnapshot:
    runs = runs_mod.list_runs(entry.name)
    last_run = runs[-1] if runs else None
    running = last_run is not None and not last_run.is_finished
    recent_outcomes = tuple(r.outcome for r in runs if r.outcome is not None)
    return _RunsSnapshot(last_run=last_run, running=running, recent_outcomes=recent_outcomes)


def _self_paced_next_wake(
    entry: DriverLoopEntry, last_run: runs_mod.RunRecord | None
) -> float | None:
    if not entry.self_paced or last_run is None:
        return None
    if last_run.ended_at is None or last_run.next_wake_hint_s is None:
        return None
    return last_run.ended_at + last_run.next_wake_hint_s


def build_context(
    entry: DriverLoopEntry,
    *,
    queue: Queue,
    fast_forward_error: str | None = None,
    now: float | None = None,
) -> ScheduleContext:
    current_now = now if now is not None else time.time()
    snapshot = _runs_snapshot(entry)
    last_run = snapshot.last_run
    return ScheduleContext(
        supervised=resolve_detached_mode(),
        entry=entry,
        paused=pause_mod.is_paused(entry.name),
        running=snapshot.running,
        usage_paused_until=queue.paused_until(),
        interactive_lock_present=interactive_lock_present(entry),
        fast_forward_error=fast_forward_error,
        recent_outcomes=snapshot.recent_outcomes,
        last_run_started_at=last_run.started_at if last_run is not None else None,
        last_run_ended_at=last_run.ended_at if last_run is not None else None,
        self_paced_next_wake_at=_self_paced_next_wake(entry, last_run),
        now=current_now,
    )


@dataclass(frozen=True)
class LoopStatusView:
    name: str
    enabled: bool
    dedicated_clone: bool
    decision: Decision
    last_outcome: runs_mod.RunOutcome | None
    final_message: str
    denial_count: int
    run_now_pending: bool


def _last_finished(entry: DriverLoopEntry) -> runs_mod.RunRecord | None:
    for record in reversed(runs_mod.list_runs(entry.name)):
        if record.is_finished:
            return record
    return None


def _status_for(
    entry: DriverLoopEntry, *, queue: Queue, now: float | None = None
) -> LoopStatusView:
    ctx = build_context(
        entry,
        queue=queue,
        fast_forward_error=last_fast_forward_failure(entry.name),
        now=now,
    )
    decision = decide(ctx)
    last = _last_finished(entry)
    with _run_now_lock:
        pending = entry.name in _run_now_requested
    return LoopStatusView(
        name=entry.name,
        enabled=entry.enabled,
        dedicated_clone=entry.dedicated_clone,
        decision=decision,
        last_outcome=last.outcome if last is not None else None,
        final_message=last.final_message if last is not None else "",
        denial_count=last.denial_count if last is not None else 0,
        run_now_pending=pending,
    )


def snapshot(*, queue: Queue, now: float | None = None) -> list[LoopStatusView]:
    entries, _quarantined = load_registry()
    return [_status_for(entry, queue=queue, now=now) for entry in entries]


def request_run_now(name: str, *, queue: Queue) -> str | None:
    entries, _quarantined = load_registry()
    entry = next((e for e in entries if e.name == name), None)
    if entry is None:
        return "unknown_loop"
    ctx = build_context(entry, queue=queue)
    decision = decide(ctx)
    if decision.action == "hold":
        return decision.reason.value if decision.reason is not None else "held"
    with _run_now_lock:
        _run_now_requested.add(name)
    return None


def run_now_pending(name: str) -> bool:
    with _run_now_lock:
        return name in _run_now_requested


def pop_run_now_request(name: str) -> bool:
    with _run_now_lock:
        if name in _run_now_requested:
            _run_now_requested.discard(name)
            return True
        return False


def clear_run_now_request(name: str) -> None:
    with _run_now_lock:
        _run_now_requested.discard(name)


__all__ = [
    "LoopStatusView",
    "build_context",
    "clear_fast_forward_failure",
    "clear_run_now_request",
    "interactive_lock_path",
    "interactive_lock_present",
    "last_fast_forward_failure",
    "pop_run_now_request",
    "record_fast_forward_failure",
    "request_run_now",
    "run_now_pending",
    "snapshot",
]
