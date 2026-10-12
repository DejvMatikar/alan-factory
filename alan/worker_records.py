from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictStr

from alan import store
from alan.roots import state_root

RECORD_VERSION = 1
SIGTERM_GRACE_S = 5.0
_RECORDS_SUBDIR = "worker_records"
_LSTART_WIDTH = 24
_PS_TIMEOUT_S = 5.0


def records_dir() -> Path:
    return state_root() / _RECORDS_SUBDIR


def current_host() -> str:
    return socket.gethostname()


def _check_record_version(value: object) -> int:
    if value != RECORD_VERSION:
        raise ValueError(f"unsupported worker record schema_version {value!r}")
    return RECORD_VERSION


class WorkerRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Annotated[int, BeforeValidator(_check_record_version)]
    job_id: int | None = None
    task_id: str | None = None
    workspace_id: str
    team: str
    inv_id: str
    node_id: str
    role: str
    model: str
    engine: str
    slot_path: str
    session_id: str
    output_path: str
    pid: int | None = None
    started_at: float | None = None
    lstart: str | None = None
    cmdline: str | None = None
    deadline_at: float | None = None
    idle_timeout_s: float | None = None
    max_turns: int | None = None
    service_pid: int
    service_started_at: str
    host: str
    pgid: int | None = None
    journal_offset: int | None = None
    err_offset: int | None = None


class ProbeVerdict(Enum):
    LIVE = "live"
    DEAD = "dead"
    UNKNOWN = "unknown"


class GroupSignalGuardError(RuntimeError):
    pass


def _record_path(session_id: str) -> Path:
    return records_dir() / f"{session_id}.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(path, json.dumps(data), prefix=".worker_record_")


def write_initial(record: WorkerRecord) -> None:
    _atomic_write(_record_path(record.session_id), record.model_dump())


def stamp_started(
    session_id: str,
    *,
    pid: int,
    started_at: float,
    lstart: str | None,
    cmdline: str | None,
) -> None:
    path = _record_path(session_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pid"] = pid
    raw["started_at"] = started_at
    raw["lstart"] = lstart
    raw["cmdline"] = cmdline
    _atomic_write(path, raw)


def stamp_detached(
    session_id: str,
    *,
    pgid: int,
    journal_offset: int,
    err_offset: int,
) -> None:
    path = _record_path(session_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pgid"] = pgid
    raw["journal_offset"] = journal_offset
    raw["err_offset"] = err_offset
    _atomic_write(path, raw)


def remove_record(session_id: str) -> None:
    _record_path(session_id).unlink(missing_ok=True)


_HOLDS_SUBDIR = "slot_holds"
HOLD_STALE_TIMEOUT_S: float = 8.0 * 3600


def holds_dir() -> Path:
    return state_root() / _HOLDS_SUBDIR


class SlotHold(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: StrictStr
    slot_path: StrictStr
    written_at: float


def _hold_path(workspace_id: str) -> Path:
    return holds_dir() / f"{workspace_id}.json"


def write_slot_hold(workspace_id: str, slot_path: str) -> None:
    hold = SlotHold(workspace_id=workspace_id, slot_path=slot_path, written_at=time.time())
    _atomic_write(_hold_path(workspace_id), hold.model_dump())


def _load_slot_hold(path: Path) -> SlotHold | None:
    try:
        return SlotHold.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def load_all_slot_holds() -> list[tuple[Path, SlotHold | None]]:
    directory = holds_dir()
    if not directory.is_dir():
        return []
    return [(path, _load_slot_hold(path)) for path in sorted(directory.glob("*.json"))]


def is_slot_hold_fresh(hold: SlotHold, *, now: float) -> bool:
    return (now - hold.written_at) <= HOLD_STALE_TIMEOUT_S


def _load_one(path: Path) -> WorkerRecord | None:
    try:
        return WorkerRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def load_all() -> list[tuple[Path, WorkerRecord | None]]:
    directory = records_dir()
    if not directory.is_dir():
        return []
    return [(path, _load_one(path)) for path in sorted(directory.glob("*.json"))]


def load_one(session_id: str) -> WorkerRecord | None:
    return _load_one(_record_path(session_id))


def ps_identity(pid: int) -> tuple[str, str] | None:
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = (result.stdout or "").splitlines()
    if not lines:
        return None
    line = lines[0]
    if len(line) <= _LSTART_WIDTH:
        return None
    lstart = line[:_LSTART_WIDTH]
    command = line[_LSTART_WIDTH:].lstrip(" ")
    if not command:
        return None
    return lstart, command


_this_service_identity_cache: tuple[int, str] | None = None


def this_service_identity() -> tuple[int, str]:
    global _this_service_identity_cache
    if _this_service_identity_cache is None:
        pid = os.getpid()
        identity = ps_identity(pid)
        lstart = identity[0] if identity is not None else ""
        _this_service_identity_cache = (pid, lstart)
    return _this_service_identity_cache


def _is_foreign_host(record: WorkerRecord) -> bool:
    return record.host != current_host()


def _is_same_service_run(record: WorkerRecord, this_service: tuple[int, str]) -> bool:
    return (record.service_pid, record.service_started_at) == this_service


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _identity_matches(record: WorkerRecord, identity: tuple[str, str]) -> bool:
    lstart, cmdline = identity
    return lstart == record.lstart and cmdline == record.cmdline


def probe(record: WorkerRecord, *, this_service: tuple[int, str]) -> ProbeVerdict:
    if _is_foreign_host(record) or _is_same_service_run(record, this_service):
        return ProbeVerdict.DEAD
    if record.pid is None:
        return ProbeVerdict.UNKNOWN
    if not _is_alive(record.pid):
        return ProbeVerdict.DEAD
    identity = ps_identity(record.pid)
    if identity is None:
        return ProbeVerdict.UNKNOWN
    return ProbeVerdict.LIVE if _identity_matches(record, identity) else ProbeVerdict.DEAD


def is_reservable(verdict: ProbeVerdict) -> bool:
    return verdict in (ProbeVerdict.LIVE, ProbeVerdict.UNKNOWN)


def is_terminable(verdict: ProbeVerdict) -> bool:
    return verdict is ProbeVerdict.LIVE


def is_detached_survivor(record: WorkerRecord, verdict: ProbeVerdict) -> bool:
    return record.pgid is not None and verdict is ProbeVerdict.LIVE


def terminate_pid(
    pid: int,
    *,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        return
    sleep(SIGTERM_GRACE_S)
    try:
        kill(pid, 0)
    except OSError:
        return
    try:
        kill(pid, signal.SIGKILL)
    except OSError:
        return


def terminate_group(
    pgid: int,
    *,
    kill: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    try:
        kill(pgid, signal.SIGTERM)
    except OSError:
        return
    sleep(SIGTERM_GRACE_S)
    try:
        kill(pgid, 0)
    except OSError:
        return
    try:
        kill(pgid, signal.SIGKILL)
    except OSError:
        return


def guard_no_group_signal(pid: int) -> None:
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    if pgid == os.getpgid(0):
        raise GroupSignalGuardError(
            f"refusing to signal process group of pid {pid}: matches the service's own group"
        )


def is_pid_alive_or_ambiguous(pid: int) -> bool:
    # Fail-safe liveness for reservation re-arm / attach re-probe: only a
    # confirmed-gone process (ProcessLookupError) is treated as dead. Any
    # other OSError (e.g. a transient PermissionError) keeps the reservation
    # rather than risking a lease() clean-up under a worker that is still
    # live — see docs/architecture/slot_pool.md.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def reservation_is_live(record: WorkerRecord) -> bool:
    if record.pid is None:
        return True
    if not is_pid_alive_or_ambiguous(record.pid):
        return False
    identity = ps_identity(record.pid)
    if identity is None:
        return True
    return _identity_matches(record, identity)


__all__ = [
    "RECORD_VERSION",
    "SIGTERM_GRACE_S",
    "HOLD_STALE_TIMEOUT_S",
    "GroupSignalGuardError",
    "ProbeVerdict",
    "SlotHold",
    "WorkerRecord",
    "current_host",
    "guard_no_group_signal",
    "holds_dir",
    "is_detached_survivor",
    "is_pid_alive_or_ambiguous",
    "is_reservable",
    "is_slot_hold_fresh",
    "is_terminable",
    "load_all",
    "load_all_slot_holds",
    "load_one",
    "probe",
    "ps_identity",
    "records_dir",
    "remove_record",
    "reservation_is_live",
    "stamp_detached",
    "stamp_started",
    "terminate_group",
    "terminate_pid",
    "this_service_identity",
    "write_initial",
    "write_slot_hold",
]
