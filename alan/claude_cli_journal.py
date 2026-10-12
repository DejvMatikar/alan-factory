from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol, cast

from alan import worker_records

StreamEvent = dict[str, Any]
OnEvent = Callable[[StreamEvent], None]
OnSessionId = Callable[[str], None]
OnProcStarted = Callable[[subprocess.Popen[str]], None]
OnJournalOpened = Callable[[int, int], None]

_POLL_INTERVAL = 0.1
_ERR_SNIPPET_CHARS = 500
_READ_CHUNK_SIZE = 65536
_WAIT_GRACE_SECONDS = 5.0
_FILE_MODE = 0o600


def _private_opener(path: str, flags: int) -> int:
    return os.open(path, flags, _FILE_MODE)


def _open_private_append(path: Path) -> IO[str]:
    handle = open(str(path), "a", encoding="utf-8", opener=_private_opener)
    try:
        os.fchmod(handle.fileno(), _FILE_MODE)
    except OSError:
        handle.close()
        raise
    return handle


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...  # pragma: no cover


@dataclass(frozen=True)
class PidHandle:
    record: worker_records.WorkerRecord
    this_service: tuple[int, str]

    def poll(self) -> int | None:
        verdict = worker_records.probe(self.record, this_service=self.this_service)
        return 0 if verdict is worker_records.ProbeVerdict.DEAD else None


_LOG_CONTROL_CHARS_RE = re.compile(r"[\r\n\t]")


def log_safe(text: str) -> str:
    return _LOG_CONTROL_CHARS_RE.sub(" ", text)


class LineConsumer(Protocol):
    def handle(  # pragma: no cover
        self, line: str, events: list[tuple[str, StreamEvent]]
    ) -> None: ...


@dataclass(frozen=True)
class JournalSpec:
    journal_path: Path
    err_path: Path
    offset: int
    err_offset: int = 0


@dataclass(frozen=True)
class PumpOutcome:
    timed_out: bool
    message: str | None
    offset: int


def journal_err_path(journal: Path) -> Path:
    return journal.with_suffix(".err")


@contextmanager
def open_stream_sink(path: Path | None) -> Generator[IO[str] | None]:
    if path is None:
        yield None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = _open_private_append(path)
    try:
        yield handle
    finally:
        handle.close()


def open_journal_handles(journal: Path) -> tuple[IO[str], IO[str], int, int]:
    journal.parent.mkdir(parents=True, exist_ok=True)
    jh = _open_private_append(journal)
    try:
        eh = _open_private_append(journal_err_path(journal))
    except OSError:
        jh.close()
        raise
    offset = os.fstat(jh.fileno()).st_size
    err_offset = os.fstat(eh.fileno()).st_size
    return jh, eh, offset, err_offset


def deadline_state(
    start: float, last: float, idle_timeout: float, abs_timeout: float, now: float
) -> tuple[str | None, float]:
    if now - start >= abs_timeout:
        return f"claude CLI exceeded absolute ceiling of {abs_timeout:.3g}s", 0.0
    idle_remaining = idle_timeout - (now - last)
    if idle_remaining <= 0:
        return f"claude CLI idle for {idle_timeout:.3g}s without output", 0.0
    return None, min(idle_remaining, abs_timeout - (now - start))


def read_new_lines(handle: IO[bytes], buffer: list[bytes]) -> list[str]:
    lines: list[str] = []
    while True:
        chunk = handle.read(_READ_CHUNK_SIZE)
        if not chunk:
            return lines
        pending = buffer.pop() if buffer else b""
        parts = (pending + chunk).split(b"\n")
        tail = parts.pop()
        if tail:
            buffer.append(tail)
        lines.extend(part.decode("utf-8", errors="replace") for part in parts if part)


def _combined_size(journal_path: Path, err_path: Path) -> int:
    return journal_path.stat().st_size + err_path.stat().st_size


def _safe_offset(handle: IO[bytes], buffer: list[bytes]) -> int:
    tail_len = len(buffer[0]) if buffer else 0
    return handle.tell() - tail_len


def _pump_iteration(
    handle: IO[bytes],
    buffer: list[bytes],
    events: list[tuple[str, StreamEvent]],
    consumer: LineConsumer,
) -> None:
    for line in read_new_lines(handle, buffer):
        consumer.handle(line, events)


@dataclass
class _PumpClock:
    start: float
    last: float
    prev_size: int


def _pump_tick(
    proc: ProcessHandle,
    handle: IO[bytes],
    buffer: list[bytes],
    spec: JournalSpec,
    clock: _PumpClock,
    *,
    idle_timeout: float,
    abs_timeout: float,
    kill: Callable[[ProcessHandle], None],
) -> PumpOutcome | None:
    size = _combined_size(spec.journal_path, spec.err_path)
    if size > clock.prev_size:
        clock.last = time.monotonic()
        clock.prev_size = size
    if proc.poll() is not None:
        return PumpOutcome(False, None, _safe_offset(handle, buffer))
    message, _ = deadline_state(
        clock.start, clock.last, idle_timeout, abs_timeout, time.monotonic()
    )
    if message is None:
        return None
    kill(proc)
    return PumpOutcome(True, message, _safe_offset(handle, buffer))


def pump_journal(
    spec: JournalSpec,
    proc: ProcessHandle,
    events: list[tuple[str, StreamEvent]],
    consumer: LineConsumer,
    *,
    idle_timeout: float,
    abs_timeout: float,
    kill: Callable[[ProcessHandle], None],
    poll_interval: float = _POLL_INTERVAL,
) -> PumpOutcome:
    buffer: list[bytes] = []
    with spec.journal_path.open("rb") as handle:
        handle.seek(spec.offset)
        clock = _PumpClock(
            start=time.monotonic(),
            last=time.monotonic(),
            prev_size=_combined_size(spec.journal_path, spec.err_path),
        )
        while True:
            try:
                _pump_iteration(handle, buffer, events, consumer)
            except BaseException:
                kill(proc)
                raise
            outcome = _pump_tick(
                proc, handle, buffer, spec, clock,
                idle_timeout=idle_timeout, abs_timeout=abs_timeout, kill=kill,
            )
            if outcome is not None:
                return outcome
            time.sleep(poll_interval)


def _wait_with_grace(
    proc: subprocess.Popen[str], kill: Callable[[subprocess.Popen[str]], None]
) -> int:
    try:
        return proc.wait(timeout=_WAIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        kill(proc)
        return proc.wait()


def run_journal_pump(
    stream_path: Path,
    consumer: LineConsumer,
    *,
    spawn: Callable[[IO[str], IO[str]], subprocess.Popen[str]],
    on_proc_started: Callable[[subprocess.Popen[str]], None],
    idle_timeout: float,
    abs_timeout: float,
    kill: Callable[[subprocess.Popen[str]], None],
    on_journal_opened: OnJournalOpened | None = None,
) -> tuple[list[tuple[str, StreamEvent]], int, str, PumpOutcome]:
    jh, eh, offset, err_offset = open_journal_handles(stream_path)
    if on_journal_opened is not None:
        on_journal_opened(offset, err_offset)
    try:
        proc = spawn(jh, eh)
    finally:
        jh.close()
        eh.close()
    on_proc_started(proc)
    events: list[tuple[str, StreamEvent]] = []
    spec = JournalSpec(
        journal_path=stream_path,
        err_path=journal_err_path(stream_path),
        offset=offset,
        err_offset=err_offset,
    )
    outcome = pump_journal(
        spec, proc, events, consumer,
        idle_timeout=idle_timeout, abs_timeout=abs_timeout,
        kill=lambda h: kill(cast("subprocess.Popen[str]", h)),
    )
    rc = _wait_with_grace(proc, kill)
    reread_tail(spec, events, consumer, outcome.offset)
    stderr = read_err_file(spec.err_path, spec.err_offset)
    return events, rc, stderr, outcome


def reread_tail(
    spec: JournalSpec,
    events: list[tuple[str, StreamEvent]],
    consumer: LineConsumer,
    offset: int,
) -> None:
    buffer: list[bytes] = []
    with spec.journal_path.open("rb") as handle:
        handle.seek(offset)
        _pump_iteration(handle, buffer, events, consumer)


def read_err_file(err_path: Path, offset: int = 0) -> str:
    with err_path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class AttachSpec:
    journal_path: Path
    err_path: Path
    journal_offset: int
    err_offset: int
    idle_timeout: float
    abs_timeout: float
    idle_start: float
    abs_start: float


@dataclass(frozen=True)
class TailOutcome:
    events: list[tuple[str, StreamEvent]]
    stderr: str
    pump: PumpOutcome


def _deadline_kill_and_reread(
    handle: IO[bytes],
    buffer: list[bytes],
    events: list[tuple[str, StreamEvent]],
    consumer: LineConsumer,
    proc: ProcessHandle,
    kill: Callable[[ProcessHandle], None],
) -> int:
    kill(proc)
    time.sleep(_WAIT_GRACE_SECONDS)
    _pump_iteration(handle, buffer, events, consumer)
    return _safe_offset(handle, buffer)


def tail_to_result(
    spec: AttachSpec,
    proc: ProcessHandle,
    events: list[tuple[str, StreamEvent]],
    consumer: LineConsumer,
    *,
    kill: Callable[[ProcessHandle], None],
    poll_interval: float = _POLL_INTERVAL,
) -> TailOutcome:
    journal_spec = JournalSpec(
        journal_path=spec.journal_path,
        err_path=spec.err_path,
        offset=spec.journal_offset,
        err_offset=spec.err_offset,
    )
    last = spec.idle_start
    buffer: list[bytes] = []
    with journal_spec.journal_path.open("rb") as handle:
        handle.seek(journal_spec.offset)
        prev_size = _combined_size(journal_spec.journal_path, journal_spec.err_path)
        while True:
            _pump_iteration(handle, buffer, events, consumer)
            size = _combined_size(journal_spec.journal_path, journal_spec.err_path)
            if size > prev_size:
                last = time.monotonic()
                prev_size = size
            if proc.poll() is not None:
                _pump_iteration(handle, buffer, events, consumer)
                outcome = PumpOutcome(False, None, _safe_offset(handle, buffer))
                break
            message, _ = deadline_state(
                spec.abs_start, last, spec.idle_timeout, spec.abs_timeout, time.monotonic()
            )
            if message is not None:
                offset = _deadline_kill_and_reread(
                    handle, buffer, events, consumer, proc, kill
                )
                outcome = PumpOutcome(True, message, offset)
                break
            time.sleep(poll_interval)
    stderr = read_err_file(journal_spec.err_path, journal_spec.err_offset)
    return TailOutcome(events, stderr, outcome)


def rc_after_pump(
    rc: int, outcome: PumpOutcome, events: list[tuple[str, StreamEvent]]
) -> int | None:
    if not outcome.timed_out:
        return rc
    last = last_result_event(events)
    if last is None:
        return None
    return 1 if is_error_result(last) else 0


def last_result_event(
    events: list[tuple[str, StreamEvent]],
) -> StreamEvent | None:
    for _, parsed in reversed(events):
        if parsed.get("type") == "result":
            return parsed
    return None


def is_system_init(parsed: StreamEvent) -> bool:
    return parsed.get("type") == "system" and parsed.get("subtype") == "init"


def is_error_result(event: StreamEvent) -> bool:
    subtype = str(event.get("subtype") or "")
    return bool(event.get("is_error")) or subtype.startswith("error_")


def is_turn_ceiling(event: StreamEvent) -> bool:
    return str(event.get("subtype") or "") == "error_max_turns"


def is_success_contradiction(event: StreamEvent) -> bool:
    subtype = str(event.get("subtype") or "")
    return bool(event.get("is_error")) and subtype == "success"


def result_detail(data: dict[str, Any]) -> str:
    return str(data.get("result") or data.get("subtype") or "unknown")


def _extract_result_usage(parsed: StreamEvent) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    usage = parsed.get("usage")
    if isinstance(usage, dict):
        raw.update(cast(dict[str, Any], usage))
    cost = parsed.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        raw["total_cost_usd"] = float(cost)
    num_turns = parsed.get("num_turns")
    if isinstance(num_turns, int) and not isinstance(num_turns, bool):
        raw["num_turns"] = num_turns
    return raw


def extract_stream_usage(events: list[tuple[str, StreamEvent]]) -> dict[str, Any]:
    for _, parsed in reversed(events):
        if parsed.get("type") == "result":
            return _extract_result_usage(parsed)
    return {}


def stream_error_parts(last: StreamEvent) -> list[str]:
    parts = [f"claude CLI: {str(last.get('subtype') or 'unknown')}"]
    num_turns = last.get("num_turns")
    if isinstance(num_turns, int):
        parts.append(f"num_turns={num_turns}")
    stop_reason = last.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        parts.append(f"stop_reason={stop_reason}")
    result_text = str(last.get("result") or "").strip()
    if result_text:
        parts.append(f"result={result_text[:_ERR_SNIPPET_CHARS]}")
    return parts


def describe_stream_error(
    events: list[tuple[str, StreamEvent]],
) -> str | None:
    if not events:
        return None
    _, last = events[-1]
    if last.get("type") != "result" or not is_error_result(last):
        return None
    return "; ".join(stream_error_parts(last))
