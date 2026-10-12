from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from alan import worker_records
from alan.claude_cli import OnProcStarted, abs_timeout_seconds, idle_timeout_seconds
from alan.claude_cli_journal import OnJournalOpened
from alan.clock import SYSTEM_CLOCK, Clock
from alan.detached_mode import resolve_detached_mode
from alan.workspace import Workspace


def _job_id_from_callback(callback: OnProcStarted | None) -> int | None:
    raw = getattr(callback, "job_id", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def _ps_identity(pid: int) -> tuple[str, str] | None:
    return worker_records.ps_identity(pid)


def _pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


@dataclass
class _DetachedSpawnState:
    offsets: tuple[int, int] | None = None


def _stamp_detached_if_opened(
    session_id: str, pid: int, state: "_DetachedSpawnState"
) -> None:
    if state.offsets is None:
        return
    pgid = _pgid(pid)
    if pgid is None:
        return
    offset, err_offset = state.offsets
    worker_records.stamp_detached(
        session_id, pgid=pgid, journal_offset=offset, err_offset=err_offset
    )


def _stamp_after_spawn(
    session_id: str,
    proc: "subprocess.Popen[str]",
    *,
    clock: Clock,
    state: "_DetachedSpawnState",
) -> None:
    identity = _ps_identity(proc.pid)
    lstart = identity[0] if identity is not None else None
    cmdline = identity[1] if identity is not None else None
    worker_records.stamp_started(
        session_id,
        pid=proc.pid,
        started_at=clock.now().value,
        lstart=lstart,
        cmdline=cmdline,
    )
    _stamp_detached_if_opened(session_id, proc.pid, state)


def _wrap_on_started(
    session_id: str,
    on_proc_started: OnProcStarted | None,
    *,
    clock: Clock,
    state: "_DetachedSpawnState",
) -> OnProcStarted:
    def _on_started(proc: "subprocess.Popen[str]") -> None:
        if on_proc_started is not None:
            on_proc_started(proc)
        try:
            _stamp_after_spawn(session_id, proc, clock=clock, state=state)
        except OSError:
            pass

    return _on_started


def _wrap_on_journal_opened(state: "_DetachedSpawnState") -> OnJournalOpened:
    def _on_opened(offset: int, err_offset: int) -> None:
        state.offsets = (offset, err_offset)

    return _on_opened


class WorkerHooks(NamedTuple):
    on_proc_started: OnProcStarted
    on_journal_opened: OnJournalOpened
    journal_stream: bool


def _build_record(
    workspace: Workspace,
    *,
    job_id: int | None,
    task_id: str | None,
    team: str,
    inv_id: str,
    node_id: str,
    role: str,
    model: str,
    session_id: str,
    stream_path: Path,
    cwd: Path | None,
    max_turns: int,
    clock: Clock,
) -> worker_records.WorkerRecord:
    service_pid, service_started_at = worker_records.this_service_identity()
    return worker_records.WorkerRecord(
        schema_version=worker_records.RECORD_VERSION,
        job_id=job_id,
        task_id=task_id,
        workspace_id=workspace.workspace_id,
        team=team,
        inv_id=inv_id,
        node_id=node_id,
        role=role,
        model=model,
        engine=workspace.meta.engine_key or "",
        slot_path=str(cwd) if cwd is not None else "",
        session_id=session_id,
        output_path=str(stream_path),
        pid=None,
        started_at=None,
        lstart=None,
        cmdline=None,
        deadline_at=clock.now().value + abs_timeout_seconds(),
        idle_timeout_s=idle_timeout_seconds(),
        max_turns=max_turns,
        service_pid=service_pid,
        service_started_at=service_started_at,
        host=worker_records.current_host(),
    )


@contextmanager
def worker_record(
    workspace: Workspace,
    *,
    task_id: str | None,
    team: str,
    inv_id: str,
    node_id: str,
    role: str,
    model: str,
    session_id: str,
    stream_path: Path,
    cwd: Path | None,
    max_turns: int,
    on_proc_started: OnProcStarted | None,
    clock: Clock = SYSTEM_CLOCK,
) -> Generator[WorkerHooks, None, None]:
    record = _build_record(
        workspace,
        job_id=_job_id_from_callback(on_proc_started),
        task_id=task_id,
        team=team,
        inv_id=inv_id,
        node_id=node_id,
        role=role,
        model=model,
        session_id=session_id,
        stream_path=stream_path,
        cwd=cwd,
        max_turns=max_turns,
        clock=clock,
    )
    worker_records.write_initial(record)
    state = _DetachedSpawnState()
    try:
        yield WorkerHooks(
            on_proc_started=_wrap_on_started(session_id, on_proc_started, clock=clock, state=state),
            on_journal_opened=_wrap_on_journal_opened(state),
            journal_stream=resolve_detached_mode(),
        )
    finally:
        try:
            worker_records.remove_record(session_id)
        except OSError:
            pass


__all__ = ["WorkerHooks", "worker_record"]
