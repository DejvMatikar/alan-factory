from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from alan import driver_loop_cmd, store, worker_records
from alan.claude_cli_journal import (
    StreamEvent,
    is_error_result,
    last_result_event,
    result_detail,
)
from alan.driver_loop_registry import DriverLoopEntry
from alan.roots import logs_root, state_root
from alan.usage_limit import is_usage_limit_message

MAX_RETAINED_RUNS = 20
NEXT_WAKE_MARKER = "ALAN_LOOP_NEXT_WAKE="
NEXT_WAKE_MIN_S = 180.0
NEXT_WAKE_MAX_S = 3600.0
NEXT_WAKE_DEFAULT_S = 1800.0


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    HELD_USAGE_PAUSE = "held_usage_pause"
    RAN_WITH_DENIALS = "ran_with_denials"
    UNKNOWN_AFTER_RESTART = "unknown_after_restart"


class ProcHandle(Protocol):
    @property
    def pid(self) -> int: ...  # pragma: no cover

    def poll(self) -> int | None: ...  # pragma: no cover

    def wait(self, timeout: float | None = None) -> int: ...  # pragma: no cover


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    loop_name: str
    started_at: float
    deadline_at: float
    stdout_log: str
    stderr_log: str
    pid: int | None = None
    pgid: int | None = None
    lstart: str | None = None
    cmdline: str | None = None
    ended_at: float | None = None
    exit_code: int | None = None
    exit_code_unavailable: bool = False
    outcome: RunOutcome | None = None
    denial_count: int = 0
    next_wake_hint_s: float | None = None
    final_message: str = ""

    @property
    def is_finished(self) -> bool:
        return self.ended_at is not None


def _runs_dir(loop_name: str) -> Path:
    return state_root() / "driver_loops" / loop_name / "runs"

def _logs_dir(loop_name: str) -> Path:
    return logs_root() / "driver_loops" / loop_name


def _run_path(loop_name: str, run_id: str) -> Path:
    return _runs_dir(loop_name) / f"{run_id}.json"


def new_run_id() -> str:
    return f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"


def log_path(loop_name: str, run_id: str, stream: str) -> Path:
    return _logs_dir(loop_name) / f"{run_id}.{stream}.log"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(path, json.dumps(data), prefix=".driver_loop_run_")


def _to_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "loop_name": record.loop_name,
        "started_at": record.started_at,
        "deadline_at": record.deadline_at,
        "stdout_log": record.stdout_log,
        "stderr_log": record.stderr_log,
        "pid": record.pid,
        "pgid": record.pgid,
        "lstart": record.lstart,
        "cmdline": record.cmdline,
        "ended_at": record.ended_at,
        "exit_code": record.exit_code,
        "exit_code_unavailable": record.exit_code_unavailable,
        "outcome": record.outcome.value if record.outcome is not None else None,
        "denial_count": record.denial_count,
        "next_wake_hint_s": record.next_wake_hint_s,
        "final_message": record.final_message,
    }


def _write_record(record: RunRecord) -> None:
    _atomic_write(_run_path(record.loop_name, record.run_id), _to_dict(record))


def _opt_int(v: object) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    return int(v) if isinstance(v, int) else None


def _opt_float(v: object) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _opt_str(v: object) -> str | None:
    return str(v) if isinstance(v, str) else None


def _from_dict(raw: dict[str, Any]) -> RunRecord | None:
    try:
        outcome_raw = raw.get("outcome")
        outcome = RunOutcome(outcome_raw) if outcome_raw is not None else None
        return RunRecord(
            run_id=str(raw["run_id"]),
            loop_name=str(raw["loop_name"]),
            started_at=float(raw["started_at"]),
            deadline_at=float(raw["deadline_at"]),
            stdout_log=str(raw["stdout_log"]),
            stderr_log=str(raw["stderr_log"]),
            pid=_opt_int(raw.get("pid")),
            pgid=_opt_int(raw.get("pgid")),
            lstart=_opt_str(raw.get("lstart")),
            cmdline=_opt_str(raw.get("cmdline")),
            ended_at=_opt_float(raw.get("ended_at")),
            exit_code=_opt_int(raw.get("exit_code")),
            exit_code_unavailable=bool(raw.get("exit_code_unavailable", False)),
            outcome=outcome,
            denial_count=int(raw.get("denial_count") or 0),
            next_wake_hint_s=_opt_float(raw.get("next_wake_hint_s")),
            final_message=str(raw.get("final_message") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_run(loop_name: str, run_id: str) -> RunRecord | None:
    path = _run_path(loop_name, run_id)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return _from_dict(cast(dict[str, Any], raw))


def list_runs(loop_name: str) -> list[RunRecord]:
    directory = _runs_dir(loop_name)
    if not directory.is_dir():
        return []
    records = (
        load_run(loop_name, path.stem) for path in sorted(directory.glob("*.json"))
    )
    return [r for r in records if r is not None]


def latest_run(loop_name: str) -> RunRecord | None:
    runs = list_runs(loop_name)
    return runs[-1] if runs else None


def prune_old_runs(loop_name: str, *, keep: int = MAX_RETAINED_RUNS) -> None:
    runs = list_runs(loop_name)
    if len(runs) <= keep:
        return
    for record in runs[: len(runs) - keep]:
        _run_path(loop_name, record.run_id).unlink(missing_ok=True)
        Path(record.stdout_log).unlink(missing_ok=True)
        Path(record.stderr_log).unlink(missing_ok=True)


def _parse_events(log_text: str) -> list[tuple[str, StreamEvent]]:
    events: list[tuple[str, StreamEvent]] = []
    for line in log_text.splitlines():
        parsed = store.parse_jsonl_row(line)
        if parsed is not None:
            events.append((line, parsed))
    return events


def final_result_event(log_text: str) -> StreamEvent | None:
    return last_result_event(_parse_events(log_text))


def permission_denial_count(log_text: str) -> int:
    result = final_result_event(log_text)
    if result is None:
        return 0
    raw = result.get("permission_denials")
    return int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0


def log_usage_limit_detected(log_text: str) -> bool:
    result = final_result_event(log_text)
    if result is None or not is_error_result(result):
        return False
    return is_usage_limit_message(result_detail(result))


def log_succeeded(log_text: str) -> bool | None:
    result = final_result_event(log_text)
    if result is None:
        return None
    return not is_error_result(result)


def final_message_text(log_text: str) -> str:
    result = final_result_event(log_text)
    if result is None:
        return ""
    return str(result.get("result") or "")


def _clamp_next_wake(value: float) -> float:
    return min(max(value, NEXT_WAKE_MIN_S), NEXT_WAKE_MAX_S)


def parse_next_wake_marker(final_text: str) -> float:
    lines = [line.strip() for line in final_text.splitlines() if line.strip()]
    if not lines:
        return NEXT_WAKE_DEFAULT_S
    last_line = lines[-1]
    if not last_line.startswith(NEXT_WAKE_MARKER):
        return NEXT_WAKE_DEFAULT_S
    raw = last_line[len(NEXT_WAKE_MARKER) :].strip()
    try:
        value = float(raw)
    except ValueError:
        return NEXT_WAKE_DEFAULT_S
    return _clamp_next_wake(value)


def derive_outcome(
    *,
    timed_out: bool,
    succeeded: bool | None,
    usage_limit_detected: bool,
    denial_count: int,
) -> RunOutcome:
    if succeeded is None:
        return RunOutcome.UNKNOWN_AFTER_RESTART
    if timed_out:
        return RunOutcome.TIMED_OUT
    if usage_limit_detected:
        return RunOutcome.HELD_USAGE_PAUSE
    if not succeeded:
        return RunOutcome.FAILED
    if denial_count > 0:
        return RunOutcome.RAN_WITH_DENIALS
    return RunOutcome.SUCCEEDED


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _replace(record: RunRecord, /, **changes: Any) -> RunRecord:
    return cast(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        RunRecord, replace(record, **changes)
    )


def finalize_from_log(
    record: RunRecord, *, timed_out: bool, exit_code_unavailable: bool
) -> RunRecord:
    log_text = _read_text(Path(record.stdout_log))
    denial_count = permission_denial_count(log_text)
    outcome = derive_outcome(
        timed_out=timed_out,
        succeeded=log_succeeded(log_text),
        usage_limit_detected=log_usage_limit_detected(log_text),
        denial_count=denial_count,
    )
    updated = _replace(
        record,
        ended_at=time.time(),
        exit_code=None,
        exit_code_unavailable=exit_code_unavailable,
        outcome=outcome,
        denial_count=denial_count,
        next_wake_hint_s=parse_next_wake_marker(final_message_text(log_text)),
        final_message=final_message_text(log_text)[:2000],
    )
    _write_record(updated)
    return updated


def finalize_live_run(
    record: RunRecord, *, exit_code: int, timed_out: bool
) -> RunRecord:
    log_text = _read_text(Path(record.stdout_log))
    denial_count = permission_denial_count(log_text)
    outcome = derive_outcome(
        timed_out=timed_out,
        succeeded=(exit_code == 0),
        usage_limit_detected=log_usage_limit_detected(log_text),
        denial_count=denial_count,
    )
    updated = _replace(
        record,
        ended_at=time.time(),
        exit_code=exit_code,
        exit_code_unavailable=False,
        outcome=outcome,
        denial_count=denial_count,
        next_wake_hint_s=parse_next_wake_marker(final_message_text(log_text)),
        final_message=final_message_text(log_text)[:2000],
    )
    _write_record(updated)
    return updated


def _identity_matches(record: RunRecord, identity: tuple[str, str]) -> bool:
    lstart, cmdline = identity
    return lstart == record.lstart and cmdline == record.cmdline


def _probe_liveness(record: RunRecord, pid: int) -> bool | None:
    if not worker_records.is_pid_alive_or_ambiguous(pid):
        return False
    identity = worker_records.ps_identity(pid)
    if identity is None:
        return None
    return _identity_matches(record, identity)


def _kill_group_for_timeout(record: RunRecord, pid: int, *, identity_confirmed: bool) -> None:
    if record.pgid is None:
        if identity_confirmed:
            worker_records.terminate_pid(pid)
        return
    try:
        worker_records.guard_no_group_signal(pid)
    except worker_records.GroupSignalGuardError:
        return
    worker_records.terminate_group(record.pgid)


def _reconcile_live_record(
    record: RunRecord, pid: int, *, now: float, identity_confirmed: bool
) -> RunRecord:
    if now < record.deadline_at:
        return record
    _kill_group_for_timeout(record, pid, identity_confirmed=identity_confirmed)
    return finalize_from_log(record, timed_out=True, exit_code_unavailable=True)


def reconcile_after_restart(loop_name: str) -> RunRecord | None:
    record = latest_run(loop_name)
    if record is None or record.is_finished:
        return None
    pid = record.pid
    if pid is None:
        return finalize_from_log(record, timed_out=False, exit_code_unavailable=True)
    liveness = _probe_liveness(record, pid)
    if liveness is not False:
        return _reconcile_live_record(
            record, pid, now=time.time(), identity_confirmed=liveness is True
        )
    return finalize_from_log(record, timed_out=False, exit_code_unavailable=True)


def spawn_run(entry: DriverLoopEntry) -> tuple[RunRecord, ProcHandle]:
    run_id = new_run_id()
    now = time.time()
    stdout_path = log_path(entry.name, run_id, "stdout")
    stderr_path = log_path(entry.name, run_id, "stderr")
    record = RunRecord(
        run_id=run_id,
        loop_name=entry.name,
        started_at=now,
        deadline_at=now + entry.timeout_seconds,
        stdout_log=str(stdout_path),
        stderr_log=str(stderr_path),
    )
    _write_record(record)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    argv = driver_loop_cmd.build_argv(entry)
    env = driver_loop_cmd.build_env()
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(
        stderr_path, "w", encoding="utf-8"
    ) as err_f:
        proc = subprocess.Popen(  # noqa: S603 - argv built from the validated registry entry
            argv,
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            cwd=str(Path(entry.cwd).expanduser()),
            env=env,
            start_new_session=True,
            text=True,
        )
    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = None
    identity = worker_records.ps_identity(pid)
    lstart, cmdline = identity if identity is not None else (None, None)
    record = _replace(record, pid=pid, pgid=pgid, lstart=lstart, cmdline=cmdline)
    _write_record(record)
    prune_old_runs(entry.name)
    return record, proc


__all__ = [
    "MAX_RETAINED_RUNS",
    "NEXT_WAKE_DEFAULT_S",
    "NEXT_WAKE_MARKER",
    "NEXT_WAKE_MAX_S",
    "NEXT_WAKE_MIN_S",
    "ProcHandle",
    "RunOutcome",
    "RunRecord",
    "derive_outcome",
    "finalize_from_log",
    "finalize_live_run",
    "final_message_text",
    "final_result_event",
    "latest_run",
    "list_runs",
    "load_run",
    "log_path",
    "log_succeeded",
    "log_usage_limit_detected",
    "new_run_id",
    "parse_next_wake_marker",
    "permission_denial_count",
    "prune_old_runs",
    "reconcile_after_restart",
    "spawn_run",
]
