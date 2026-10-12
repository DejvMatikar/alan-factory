from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from alan import worker_records
from alan.claude_cli_journal import (
    AttachSpec,
    PidHandle,
    PumpOutcome,
    journal_err_path,
    last_result_event,
    rc_after_pump,
    run_journal_pump,
    tail_to_result,
)


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def _make_record(**overrides: object) -> worker_records.WorkerRecord:
    base: dict[str, object] = {
        "schema_version": worker_records.RECORD_VERSION,
        "job_id": 1, "task_id": "task-1", "workspace_id": "ws-1", "team": "implementation",
        "inv_id": "inv_001", "node_id": "implementer", "role": "implementer", "model": "sonnet",
        "engine": "default", "slot_path": "/fake/slot_0", "session_id": "sess-1",
        "output_path": "/fake/slot_0/stream.jsonl",
        "pid": None, "started_at": None, "lstart": None, "cmdline": None,
        "deadline_at": None, "idle_timeout_s": None, "max_turns": 150,
        "service_pid": 999999, "service_started_at": "Thu Jan  1 00:00:00 1970",
        "host": worker_records.current_host(),
        "pgid": None, "journal_offset": None, "err_offset": None,
    }
    base.update(overrides)
    return worker_records.WorkerRecord(**base)  # type: ignore[arg-type]


class _RecordingConsumer:
    def handle(self, line: str, events: list[tuple[str, Any]]) -> None:
        events.append((line, json.loads(line)))


def _result_event(text: str = "ok", **extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "result": text, **extra}


def _write_lines(path: Path, *events: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def test_pid_handle_poll_none_while_live() -> None:
    identity = worker_records.ps_identity(os.getpid())
    assert identity is not None
    lstart, cmdline = identity
    record = _make_record(pid=os.getpid(), lstart=lstart, cmdline=cmdline)
    handle = PidHandle(record=record, this_service=(424242, "Thu Jan  1 00:00:00 1970"))
    assert handle.poll() is None


def test_pid_handle_poll_terminal_when_dead() -> None:
    record = _make_record(pid=999999, lstart="x", cmdline="y")
    handle = PidHandle(record=record, this_service=(424242, "Thu Jan  1 00:00:00 1970"))
    assert handle.poll() == 0


def test_pid_handle_poll_none_when_unknown_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record(pid=12345, lstart="a", cmdline="b")
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    handle = PidHandle(record=record, this_service=(424242, "Thu Jan  1 00:00:00 1970"))
    assert handle.poll() is None


def test_rc_after_pump_returns_rc_unchanged_when_not_timed_out() -> None:
    outcome = PumpOutcome(timed_out=False, message=None, offset=10)
    assert rc_after_pump(0, outcome, []) == 0


def test_rc_after_pump_returns_none_when_timed_out_with_no_result() -> None:
    outcome = PumpOutcome(timed_out=True, message="idle", offset=0)
    assert rc_after_pump(1, outcome, []) is None


def test_rc_after_pump_classifies_success_result_despite_timeout() -> None:
    outcome = PumpOutcome(timed_out=True, message="idle", offset=0)
    events = [("line", _result_event("finished just in time"))]
    assert rc_after_pump(1, outcome, events) == 0


def test_rc_after_pump_classifies_error_result_despite_timeout() -> None:
    outcome = PumpOutcome(timed_out=True, message="idle", offset=0)
    events = [("line", {"type": "result", "is_error": True, "subtype": "error", "result": "x"})]
    assert rc_after_pump(1, outcome, events) == 1


class _FakeHandle:
    def __init__(self, poll_sequence: list[int | None]) -> None:
        self._seq = list(poll_sequence)

    def poll(self) -> int | None:
        return self._seq.pop(0) if self._seq else 0


class _HangingHandle:
    def poll(self) -> int | None:
        return None


def _spec(
    journal: Path, *, idle_timeout: float = 10.0, abs_timeout: float = 10.0,
    journal_offset: int = 0, err_offset: int = 0,
) -> AttachSpec:
    now = time.monotonic()
    return AttachSpec(
        journal_path=journal, err_path=journal_err_path(journal),
        journal_offset=journal_offset, err_offset=err_offset,
        idle_timeout=idle_timeout, abs_timeout=abs_timeout,
        idle_start=now, abs_start=now,
    )


def test_tail_to_result_reads_available_lines_and_terminates_on_poll(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    _write_lines(journal, _result_event("done"))
    err.write_bytes(b"")
    handle = _FakeHandle([0])
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal), handle, events, _RecordingConsumer(), kill=lambda h: None,
    )
    assert outcome.pump.timed_out is False
    assert [e["result"] for _, e in outcome.events] == ["done"]


class _WritesResultThenExitsHandle:
    def __init__(self, journal: Path) -> None:
        self._journal = journal
        self._polls = 0

    def poll(self) -> int | None:
        self._polls += 1
        if self._polls == 1:
            return None
        _write_lines(self._journal, _result_event("finished during the exit probe"))
        return 0


def test_tail_to_result_drains_a_line_written_while_the_exit_was_being_probed(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    handle = _WritesResultThenExitsHandle(journal)
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal), handle, events, _RecordingConsumer(),
        kill=lambda h: None, poll_interval=0.0,
    )
    assert outcome.pump.timed_out is False
    last = last_result_event(outcome.events)
    assert last is not None
    assert last["result"] == "finished during the exit probe"
    assert outcome.pump.offset == journal.stat().st_size


def test_tail_to_result_offset_excludes_prior_run_content(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    _write_lines(journal, _result_event("prior"))
    offset = journal.stat().st_size
    _write_lines(journal, _result_event("new run"))
    err.write_bytes(b"")
    handle = _FakeHandle([0])
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal, journal_offset=offset), handle, events, _RecordingConsumer(),
        kill=lambda h: None,
    )
    assert [e["result"] for _, e in outcome.events] == ["new run"]


def test_tail_to_result_idle_timeout_kills_and_reports(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    handle = _HangingHandle()
    killed: list[Any] = []
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal, idle_timeout=0.05), handle, events, _RecordingConsumer(),
        kill=killed.append, poll_interval=0.0,
    )
    assert outcome.pump.timed_out is True
    assert "idle for" in (outcome.pump.message or "")
    assert killed == [handle]


def test_tail_to_result_completion_beats_deadline_kill_via_single_reread(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    handle = _HangingHandle()

    def kill_writes_result(_handle: Any) -> None:
        _write_lines(journal, _result_event("finished in the race window"))

    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal, idle_timeout=0.05), handle, events, _RecordingConsumer(),
        kill=kill_writes_result, poll_interval=0.0,
    )
    assert outcome.pump.timed_out is True
    last = last_result_event(outcome.events)
    assert last is not None
    assert last["result"] == "finished in the race window"


def test_tail_to_result_abs_ceiling_kills_and_reports(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    handle = _HangingHandle()
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal, idle_timeout=9999, abs_timeout=0.05), handle, events, _RecordingConsumer(),
        kill=lambda h: None, poll_interval=0.0,
    )
    assert outcome.pump.timed_out is True
    assert "absolute ceiling" in (outcome.pump.message or "")


def test_tail_to_result_classifies_stderr_from_recorded_err_offset(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_text("prior run stderr\n", encoding="utf-8")
    err_offset = err.stat().st_size
    with err.open("ab") as handle:
        handle.write(b"current run stderr")
    fake = _FakeHandle([0])
    events: list[tuple[str, Any]] = []
    outcome = tail_to_result(
        _spec(journal, err_offset=err_offset), fake, events, _RecordingConsumer(),
        kill=lambda h: None,
    )
    assert outcome.stderr == "current run stderr"


class _ImmediateFakeProc:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        self.returncode = 0
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_run_journal_pump_fires_on_journal_opened_with_offsets(tmp_path: Path) -> None:
    stream = tmp_path / "agents" / "impl.stream.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text('{"type": "prior"}\n', encoding="utf-8")
    journal_err_path(stream).write_text("prior err\n", encoding="utf-8")
    captured: list[tuple[int, int]] = []

    def spawn(jh: Any, eh: Any) -> Any:
        return _ImmediateFakeProc()

    run_journal_pump(
        stream, _RecordingConsumer(), spawn=spawn, on_proc_started=lambda p: None,
        idle_timeout=10, abs_timeout=10, kill=lambda p: None,
        on_journal_opened=lambda offset, err_offset: captured.append((offset, err_offset)),
    )
    assert captured == [(len(b'{"type": "prior"}\n'), len(b"prior err\n"))]


def test_run_journal_pump_without_on_journal_opened_is_a_noop(tmp_path: Path) -> None:
    stream = tmp_path / "agents" / "impl2.stream.jsonl"

    def spawn(jh: Any, eh: Any) -> Any:
        return _ImmediateFakeProc()

    events, rc, stderr, outcome = run_journal_pump(
        stream, _RecordingConsumer(), spawn=spawn, on_proc_started=lambda p: None,
        idle_timeout=10, abs_timeout=10, kill=lambda p: None,
    )
    assert rc == 0
