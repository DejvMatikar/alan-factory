from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from alan import driver_loop_runs as runs_mod
from alan import driver_loop_status as status_mod
from alan import worker_records
from alan.detached_mode import resolve_detached_mode
from alan.driver_loop_registry import DriverLoopEntry, load_registry
from alan.driver_loop_schedule import Decision, ScheduleContext, decide
from alan.queue import Queue

logger = logging.getLogger("alan.driver_loop_runner")

DEFAULT_TICK_INTERVAL_S = 30.0
_FAST_FORWARD_TIMEOUT_S = 60.0
_JOIN_TIMEOUT_S = 5.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_live_procs: dict[str, runs_mod.ProcHandle] = {}
_live_procs_lock = threading.Lock()


def _fast_forward(entry: DriverLoopEntry) -> str | None:
    if not entry.dedicated_clone:
        return None
    cwd = str(Path(entry.cwd).expanduser())
    try:
        fetched = subprocess.run(  # noqa: S603 - fixed argv, cwd from validated registry entry
            ["git", "-C", cwd, "fetch", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=_FAST_FORWARD_TIMEOUT_S,
            check=False,
        )
        if fetched.returncode != 0:
            return f"git fetch failed: {fetched.stderr.strip()[:200]}"
        merged = subprocess.run(  # noqa: S603
            ["git", "-C", cwd, "merge", "--ff-only", "origin/main"],
            capture_output=True,
            text=True,
            timeout=_FAST_FORWARD_TIMEOUT_S,
            check=False,
        )
        if merged.returncode != 0:
            return f"git merge --ff-only failed: {merged.stderr.strip()[:200]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"fast-forward error: {exc}"
    return None


def _kill_for_timeout(record: runs_mod.RunRecord) -> None:
    if record.pid is None:
        return
    if record.pgid is not None:
        try:
            worker_records.guard_no_group_signal(record.pid)
        except worker_records.GroupSignalGuardError:
            return
        worker_records.terminate_group(record.pgid)
    else:
        worker_records.terminate_pid(record.pid)


def _reap_live_proc(name: str, proc: runs_mod.ProcHandle) -> None:
    record = runs_mod.latest_run(name)
    if record is None or record.is_finished:
        with _live_procs_lock:
            _live_procs.pop(name, None)
        return
    rc = proc.poll()
    timed_out = False
    if rc is None:
        if time.time() < record.deadline_at:
            return
        timed_out = True
        _kill_for_timeout(record)
        try:
            rc = proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return
    with _live_procs_lock:
        _live_procs.pop(name, None)
    runs_mod.finalize_live_run(record, exit_code=rc, timed_out=timed_out)


def _reap_if_finished(name: str) -> None:
    with _live_procs_lock:
        proc = _live_procs.get(name)
    if proc is not None:
        _reap_live_proc(name, proc)
        return
    runs_mod.reconcile_after_restart(name)


def _should_skip(decision: Decision, run_now: bool) -> bool:
    return decision.action != "spawn" and not run_now


def _run_fast_forward(entry: DriverLoopEntry) -> str | None:
    fast_forward_error = _fast_forward(entry)
    if fast_forward_error is not None:
        status_mod.record_fast_forward_failure(entry.name, fast_forward_error)
        logger.warning(
            "driver loop %s fast-forward failed: %s", entry.name, fast_forward_error
        )
    else:
        status_mod.clear_fast_forward_failure(entry.name)
    return fast_forward_error


def _context_after_fast_forward(
    entry: DriverLoopEntry,
    queue: Queue,
    cheap_ctx: ScheduleContext,
    fast_forward_error: str | None,
) -> ScheduleContext:
    if fast_forward_error is None:
        return cheap_ctx
    return status_mod.build_context(entry, queue=queue, fast_forward_error=fast_forward_error)


def _tick_one(
    entry: DriverLoopEntry, queue: Queue, on_change: Callable[[], None] | None
) -> None:
    _reap_if_finished(entry.name)
    cheap_ctx = status_mod.build_context(entry, queue=queue, fast_forward_error=None)
    cheap_decision = decide(cheap_ctx)
    if cheap_decision.action == "hold":
        return
    if _should_skip(cheap_decision, status_mod.run_now_pending(entry.name)):
        return
    fast_forward_error = _run_fast_forward(entry)
    ctx = _context_after_fast_forward(entry, queue, cheap_ctx, fast_forward_error)
    decision = decide(ctx)
    if decision.action == "hold":
        return
    run_now = status_mod.pop_run_now_request(entry.name)
    if _should_skip(decision, run_now):
        return
    _record, proc = runs_mod.spawn_run(entry)
    with _live_procs_lock:
        _live_procs[entry.name] = proc
    if on_change is not None:
        on_change()


def _run_loop(
    queue: Queue,
    on_change: Callable[[], None] | None,
    tick_interval_s: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        entries, _quarantined = load_registry()
        for entry in entries:
            try:
                _tick_one(entry, queue, on_change)
            except Exception:  # noqa: BLE001 - one loop's failure must not kill the runner thread
                logger.exception("driver loop tick failed for %s", entry.name)
        stop_event.wait(tick_interval_s)


def start(
    *,
    queue: Queue,
    on_change: Callable[[], None] | None = None,
    tick_interval_s: float = DEFAULT_TICK_INTERVAL_S,
) -> None:
    if not resolve_detached_mode():
        return
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        entries, _quarantined = load_registry()
        for entry in entries:
            runs_mod.reconcile_after_restart(entry.name)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_run_loop,
            args=(queue, on_change, tick_interval_s, stop_event),
            name="driver-loop-runner",
            daemon=True,
        )
        _thread = thread
        _stop_event = stop_event
        thread.start()


def stop() -> None:
    global _thread, _stop_event
    with _lock:
        thread, stop_event = _thread, _stop_event
        _thread, _stop_event = None, None
    if stop_event is not None:
        stop_event.set()
    if thread is not None:
        thread.join(timeout=_JOIN_TIMEOUT_S)


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


__all__ = [
    "DEFAULT_TICK_INTERVAL_S",
    "is_running",
    "start",
    "stop",
]
