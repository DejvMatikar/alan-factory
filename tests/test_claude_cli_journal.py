from __future__ import annotations

import inspect
import io
import json
import subprocess
import time
from pathlib import Path
from typing import Any, TypeAlias, cast

import pytest

from alan import claude_cli, claude_cli_journal
from alan.claude_cli import (
    ClaudeCliError,
    ClaudeCliTimeout,
    ResumeSessionLostError,
    ServerErrorRetryable,
    run_agent_streaming,
)
from alan.claude_cli_journal import (
    JournalSpec,
    deadline_state,
    journal_err_path,
    open_journal_handles,
    pump_journal,
    read_err_file,
    read_new_lines,
    reread_tail,
    run_journal_pump,
)


class _RecordingConsumer:
    def handle(self, line: str, events: list[tuple[str, Any]]) -> None:
        events.append((line, json.loads(line)))


class _FakeProc:
    def __init__(
        self, poll_sequence: list[int | None] | None = None, final_rc: int = 0
    ) -> None:
        self.stdin: io.StringIO | None = io.StringIO()
        self.stdout: io.StringIO | None = None
        self.stderr: io.StringIO | None = None
        self._poll_sequence = list(poll_sequence) if poll_sequence is not None else [0]
        self._final_rc = final_rc
        self.killed = False
        self.returncode: int | None = None

    def poll(self) -> int | None:
        value = self._poll_sequence.pop(0) if self._poll_sequence else self._final_rc
        if value is not None:
            self.returncode = value
        return value

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._final_rc
        return self._final_rc


class _HangingProc(_FakeProc):
    def poll(self) -> int | None:
        return None


class _SlowToReapProc(_FakeProc):
    def __init__(self, final_rc: int = 0) -> None:
        super().__init__(poll_sequence=[0], final_rc=final_rc)
        self._wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        if self._wait_calls == 1 and timeout is not None:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        self.returncode = self._final_rc
        return self._final_rc


_PopenStr: TypeAlias = subprocess.Popen[str]


def _as_proc(proc: _FakeProc) -> subprocess.Popen[str]:
    return cast(_PopenStr, proc)


def _result_event(text: str = "ok", **extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "result": text, **extra}


def _write_lines(path: Path, *events: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def test_journal_err_path_is_sibling_with_err_suffix(tmp_path: Path) -> None:
    journal = tmp_path / "agents" / "impl.stream.jsonl"
    assert journal_err_path(journal) == journal.with_suffix(".err")


def test_deadline_state_no_breach_returns_remaining_budget() -> None:
    message, remaining = deadline_state(
        start=0.0, last=0.0, idle_timeout=10.0, abs_timeout=100.0, now=1.0
    )
    assert message is None
    assert remaining == pytest.approx(9.0)


def test_deadline_state_abs_breach() -> None:
    message, _ = deadline_state(
        start=0.0, last=0.0, idle_timeout=100.0, abs_timeout=10.0, now=10.0
    )
    assert message == "claude CLI exceeded absolute ceiling of 10s"


def test_deadline_state_idle_breach() -> None:
    message, _ = deadline_state(
        start=0.0, last=0.0, idle_timeout=5.0, abs_timeout=100.0, now=5.0
    )
    assert message == "claude CLI idle for 5s without output"


def test_deadline_state_abs_checked_before_idle_when_both_breach() -> None:
    message, _ = deadline_state(
        start=0.0, last=0.0, idle_timeout=1.0, abs_timeout=1.0, now=1.0
    )
    assert message is not None
    assert "absolute ceiling" in message


def test_read_new_lines_empty_read_returns_nothing(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(b"")
    buffer: list[bytes] = []
    with path.open("rb") as handle:
        assert read_new_lines(handle, buffer) == []
    assert buffer == []


def test_read_new_lines_multiple_complete_lines_in_one_chunk(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(b'{"a": 1}\n{"a": 2}\n')
    buffer: list[bytes] = []
    with path.open("rb") as handle:
        lines = read_new_lines(handle, buffer)
    assert lines == ['{"a": 1}', '{"a": 2}']
    assert buffer == []


def test_read_new_lines_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(b'{"a": 1}\n\n\n{"a": 2}\n')
    buffer: list[bytes] = []
    with path.open("rb") as handle:
        lines = read_new_lines(handle, buffer)
    assert lines == ['{"a": 1}', '{"a": 2}']


def test_read_new_lines_holds_partial_trailing_line_until_newline_arrives(
    tmp_path: Path,
) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(b"")
    buffer: list[bytes] = []
    with path.open("rb") as handle:
        with path.open("ab") as writer:
            writer.write(b'{"type": "a"}\n{"type"')
        lines = read_new_lines(handle, buffer)
        assert lines == ['{"type": "a"}']
        assert buffer == [b'{"type"']

        with path.open("ab") as writer:
            writer.write(b': "b"}\n')
        lines = read_new_lines(handle, buffer)
        assert lines == ['{"type": "b"}']
        assert buffer == []


def test_open_journal_handles_zero_offset_for_fresh_journal(tmp_path: Path) -> None:
    journal = tmp_path / "agents" / "fresh.stream.jsonl"
    jh, eh, offset, err_offset = open_journal_handles(journal)
    try:
        assert offset == 0
        assert err_offset == 0
        assert journal.exists()
        assert journal_err_path(journal).exists()
    finally:
        jh.close()
        eh.close()


def test_open_journal_handles_offset_matches_prior_content_size(tmp_path: Path) -> None:
    journal = tmp_path / "agents" / "impl.stream.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"type": "prior"}\n', encoding="utf-8")
    journal_err_path(journal).write_text("prior run stderr\n", encoding="utf-8")
    jh, eh, offset, err_offset = open_journal_handles(journal)
    try:
        assert offset == len(b'{"type": "prior"}\n')
        assert err_offset == len(b"prior run stderr\n")
    finally:
        jh.close()
        eh.close()


def test_open_journal_handles_closes_journal_and_reraises_when_err_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "agents" / "impl.stream.jsonl"
    err = journal_err_path(journal)
    captured: list[Any] = []
    real_open: Any = claude_cli_journal._open_private_append  # pyright: ignore[reportPrivateUsage]

    def fake_open(path: Path) -> Any:
        if path == err:
            raise OSError("boom")
        handle = real_open(path)
        if path == journal:
            captured.append(handle)
        return handle

    monkeypatch.setattr(claude_cli_journal, "_open_private_append", fake_open)
    with pytest.raises(OSError, match="boom"):
        open_journal_handles(journal)
    assert captured[0].closed is True


def test_pump_journal_normal_completion_reads_available_lines(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(json.dumps({"type": "assistant"}).encode() + b"\n")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _FakeProc(poll_sequence=[0])
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=10, abs_timeout=10, kill=lambda p: None, poll_interval=0.0,
    )
    assert outcome.timed_out is False
    assert outcome.message is None
    assert [e["type"] for _, e in events] == ["assistant"]


def test_pump_journal_offset_excludes_prior_run_content(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    _write_lines(journal, _result_event("prior run"))
    offset = journal.stat().st_size
    _write_lines(journal, _result_event("new run"))
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=offset)
    proc = _FakeProc(poll_sequence=[0])
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=10, abs_timeout=10, kill=lambda p: None, poll_interval=0.0,
    )
    assert [e["result"] for _, e in events] == ["new run"]


def test_pump_journal_detects_growth_of_journal_mid_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)

    def fake_sleep(_seconds: float) -> None:
        with journal.open("ab") as handle:
            handle.write(json.dumps({"type": "assistant"}).encode() + b"\n")

    monkeypatch.setattr(time, "sleep", fake_sleep)
    proc = _FakeProc(poll_sequence=[None, 0])
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=5, abs_timeout=10, kill=lambda p: None, poll_interval=0.0,
    )
    assert outcome.timed_out is False
    assert [e["type"] for _, e in events] == ["assistant"]


def test_pump_journal_growth_of_err_file_alone_counts_as_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] <= 3:
            with err.open("ab") as handle:
                handle.write(b"x")

    monkeypatch.setattr(time, "sleep", fake_sleep)
    proc = _FakeProc(poll_sequence=[None, None, None, 0])
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=2.0, abs_timeout=10.0, kill=lambda p: None, poll_interval=0.0,
    )
    assert outcome.timed_out is False


def test_pump_journal_idle_timeout_kills_and_reports(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _HangingProc()
    killed: list[Any] = []
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=0.05, abs_timeout=10, kill=killed.append, poll_interval=0.0,
    )
    assert outcome.timed_out is True
    assert outcome.message is not None
    assert "idle for" in outcome.message
    assert killed == [proc]


def test_pump_journal_abs_ceiling_kills_and_reports(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _HangingProc()
    killed: list[Any] = []
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=9999, abs_timeout=0.05, kill=killed.append, poll_interval=0.0,
    )
    assert outcome.timed_out is True
    assert outcome.message is not None
    assert "absolute ceiling" in outcome.message


def test_pump_journal_offset_accounts_for_pending_partial_line_on_timeout(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b'{"type": "partial"')
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _HangingProc()
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=0.05, abs_timeout=10, kill=lambda p: None, poll_interval=0.0,
    )
    assert outcome.timed_out is True
    assert events == []
    assert outcome.offset == 0


def test_completion_beats_kill_result_event_survives_reread(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    journal.write_bytes(b"")
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _HangingProc()
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()

    def kill_writes_result(_proc: Any) -> None:
        with journal.open("ab") as handle:
            handle.write(json.dumps(_result_event("finished just in time")).encode() + b"\n")

    outcome = pump_journal(
        spec, _as_proc(proc), events, consumer,
        idle_timeout=0.05, abs_timeout=10, kill=kill_writes_result, poll_interval=0.0,
    )
    assert outcome.timed_out is True
    assert events == []

    reread_tail(spec, events, consumer, outcome.offset)
    assert [e["result"] for _, e in events] == ["finished just in time"]


class _RaisingConsumer:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def handle(self, line: str, events: list[tuple[str, Any]]) -> None:
        raise self._exc


def test_pump_journal_kills_proc_and_reraises_when_consumer_raises(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    _write_lines(journal, _result_event("first"))
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _FakeProc(poll_sequence=[0])
    events: list[tuple[str, Any]] = []
    killed: list[Any] = []
    boom = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        pump_journal(
            spec, _as_proc(proc), events, _RaisingConsumer(boom),
            idle_timeout=10, abs_timeout=10, kill=killed.append, poll_interval=0.0,
        )
    assert killed == [proc]


def test_pump_journal_never_kills_on_the_clean_completion_path(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    err = journal_err_path(journal)
    _write_lines(journal, _result_event("done"))
    err.write_bytes(b"")
    spec = JournalSpec(journal_path=journal, err_path=err, offset=0)
    proc = _FakeProc(poll_sequence=[0])
    events: list[tuple[str, Any]] = []
    killed: list[Any] = []
    outcome = pump_journal(
        spec, _as_proc(proc), events, _RecordingConsumer(),
        idle_timeout=10, abs_timeout=10, kill=killed.append, poll_interval=0.0,
    )
    assert outcome.timed_out is False
    assert killed == []


class _StagedConsumer:
    def __init__(self, stream: Path) -> None:
        self._stream = stream
        self.calls = 0

    def handle(self, line: str, events: list[tuple[str, Any]]) -> None:
        self.calls += 1
        events.append((line, json.loads(line)))
        if self.calls == 1:
            _write_lines(self._stream, _result_event("late"))
        elif self.calls == 2:
            raise RuntimeError("boom-on-reread")


def test_reread_tail_call_site_is_unguarded_and_propagates_consumer_errors(
    tmp_path: Path,
) -> None:
    stream = tmp_path / "agents" / "boom.stream.jsonl"
    proc = _FakeProc(poll_sequence=[0])
    killed: list[Any] = []
    consumer = _StagedConsumer(stream)

    def spawn(jh: Any, eh: Any) -> subprocess.Popen[str]:
        _write_lines(stream, _result_event("first"))
        return _as_proc(proc)

    with pytest.raises(RuntimeError, match="boom-on-reread"):
        run_journal_pump(
            stream, consumer,
            spawn=spawn, on_proc_started=lambda p: None,
            idle_timeout=10, abs_timeout=10, kill=killed.append,
        )
    assert killed == []
    assert consumer.calls == 2


def test_run_journal_pump_rekills_when_first_wait_times_out(tmp_path: Path) -> None:
    stream = tmp_path / "agents" / "stuck.stream.jsonl"
    proc = _SlowToReapProc(final_rc=0)
    killed: list[Any] = []

    def spawn(jh: Any, eh: Any) -> subprocess.Popen[str]:
        _write_lines(stream, _result_event("done"))
        return _as_proc(proc)

    events, rc, stderr, outcome = run_journal_pump(
        stream,
        _RecordingConsumer(),
        spawn=spawn,
        on_proc_started=lambda p: None,
        idle_timeout=10,
        abs_timeout=10,
        kill=killed.append,
    )
    assert rc == 0
    assert killed == [proc]
    assert stderr == ""
    assert outcome.timed_out is False
    assert [e["result"] for _, e in events] == ["done"]


def test_reread_tail_reads_only_from_given_offset(tmp_path: Path) -> None:
    journal = tmp_path / "n.stream.jsonl"
    _write_lines(journal, _result_event("first"))
    offset = journal.stat().st_size
    _write_lines(journal, _result_event("second"))
    spec = JournalSpec(journal_path=journal, err_path=journal_err_path(journal), offset=0)
    events: list[tuple[str, Any]] = []
    consumer = _RecordingConsumer()
    reread_tail(spec, events, consumer, offset)
    assert [e["result"] for _, e in events] == ["second"]


def test_read_err_file_reads_full_content(tmp_path: Path) -> None:
    err = tmp_path / "n.stream.err"
    err.write_text("boom\ntrace", encoding="utf-8")
    assert read_err_file(err) == "boom\ntrace"


def test_read_err_file_excludes_bytes_before_offset(tmp_path: Path) -> None:
    err = tmp_path / "n.stream.err"
    err.write_text("prior run stderr\n", encoding="utf-8")
    offset = err.stat().st_size
    with err.open("ab") as handle:
        handle.write(b"current run stderr")
    assert read_err_file(err, offset) == "current run stderr"


def test_run_chat_turn_has_no_journal_stream_parameter() -> None:
    assert "journal_stream" not in inspect.signature(claude_cli.run_chat_turn).parameters


def test_run_agent_has_no_journal_stream_parameter() -> None:
    assert "journal_stream" not in inspect.signature(claude_cli.run_agent).parameters


def test_run_agent_streaming_journal_stream_defaults_to_false() -> None:
    default = inspect.signature(run_agent_streaming).parameters["journal_stream"].default
    assert default is False


def test_journal_stream_true_without_stream_path_raises() -> None:
    with pytest.raises(ClaudeCliError, match="requires a stream_path"):
        run_agent_streaming("sys", "user", journal_stream=True)


def test_journal_stream_false_by_default_uses_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        captured.update(kwargs)
        proc = _FakeProc(poll_sequence=[0])
        proc.stdout = io.StringIO(json.dumps(_result_event("ok")) + "\n")
        proc.stderr = io.StringIO("")
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    result = run_agent_streaming("sys", "user")
    assert result.result_text == "ok"
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE


def test_journal_stream_true_spawns_with_real_file_handles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "impl.stream.jsonl"
    captured: dict[str, Any] = {}

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        captured.update(kwargs)
        _write_lines(stream, _result_event("ok"))
        return _FakeProc(poll_sequence=[0])

    monkeypatch.setattr(subprocess, "Popen", fake)
    result = run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)
    assert result.result_text == "ok"
    assert Path(captured["stdout"].name) == stream
    assert Path(captured["stderr"].name) == journal_err_path(stream)
    assert captured["stdin"] == subprocess.PIPE


def test_journal_stream_no_replay_of_prior_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "impl.stream.jsonl"
    _write_lines(stream, _result_event("prior run"))

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        _write_lines(stream, _result_event("new run"))
        return _FakeProc(poll_sequence=[0])

    monkeypatch.setattr(subprocess, "Popen", fake)
    result = run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)
    assert result.result_text == "new run"


def test_stream_subprocess_journal_closes_handles_when_spawn_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "boom.stream.jsonl"

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("no claude")

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError, match="not found on PATH"):
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)


def test_journal_stream_no_stdin_close_child_pipes_is_none_tolerant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "nostdin.stream.jsonl"

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(poll_sequence=[0])
        proc.stdin = None
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError, match="no stdin"):
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)


def test_journal_stream_idle_timeout_raises_claude_cli_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "hang.stream.jsonl"
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.05")

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        return _HangingProc()

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout, match="idle for"):
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)


def test_journal_stream_abs_ceiling_raises_claude_cli_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "abshang.stream.jsonl"
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "9999")
    monkeypatch.setenv("ALAN_ABS_TIMEOUT_SECONDS", "0.05")

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        return _HangingProc()

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout, match="absolute ceiling"):
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)


def test_journal_stream_success_result_beats_idle_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "race.stream.jsonl"
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.05")

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        proc = _HangingProc()

        def kill_and_finish() -> None:
            proc.killed = True
            _write_lines(stream, _result_event("finished in the race window"))

        proc.kill = kill_and_finish  # type: ignore[method-assign]
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    result = run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)
    assert result.result_text == "finished in the race window"


def test_journal_stream_error_result_beats_idle_kill_and_is_classified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "race_err.stream.jsonl"
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.05")

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        proc = _HangingProc()

        def kill_and_finish() -> None:
            proc.killed = True
            _write_lines(stream, {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "boom",
            })

        proc.kill = kill_and_finish  # type: ignore[method-assign]
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)
    assert not isinstance(exc_info.value, ClaudeCliTimeout)


def _pipe_popen(
    monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], *,
    returncode: int = 0, stderr: str = "",
) -> None:
    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(poll_sequence=[returncode], final_rc=returncode)
        proc.stdout = io.StringIO("".join(json.dumps(e) + "\n" for e in events))
        proc.stderr = io.StringIO(stderr)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)


def _journal_popen(
    monkeypatch: pytest.MonkeyPatch, stream: Path, events: list[dict[str, Any]], *,
    returncode: int = 0, stderr: str = "",
) -> None:
    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        _write_lines(stream, *events)
        if stderr:
            journal_err_path(stream).parent.mkdir(parents=True, exist_ok=True)
            journal_err_path(stream).write_text(stderr, encoding="utf-8")
        return _FakeProc(poll_sequence=[returncode], final_rc=returncode)

    monkeypatch.setattr(subprocess, "Popen", fake)


def test_usage_limit_classification_parity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.claude_cli import UsageLimitError

    event = {
        "type": "result",
        "is_error": True,
        "subtype": "error_during_execution",
        "result": "hit your limit",
    }
    _pipe_popen(monkeypatch, [event], returncode=1)
    with pytest.raises(UsageLimitError) as pipe_exc:
        run_agent_streaming("sys", "user")

    stream = tmp_path / "agents" / "usage.stream.jsonl"
    _journal_popen(monkeypatch, stream, [event], returncode=1)
    with pytest.raises(UsageLimitError) as journal_exc:
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)

    assert not isinstance(pipe_exc.value, ServerErrorRetryable)
    assert not isinstance(journal_exc.value, ServerErrorRetryable)


def test_retryable_server_error_classification_parity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    event = {
        "type": "result",
        "is_error": True,
        "subtype": "error_during_execution",
        "result": "server failure",
        "api_status_code": 503,
    }
    _pipe_popen(monkeypatch, [event], returncode=1)
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")

    stream = tmp_path / "agents" / "servererr.stream.jsonl"
    _journal_popen(monkeypatch, stream, [event], returncode=1)
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)


def test_turn_ceiling_classification_parity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.claude_cli import TurnCeilingReached

    event = {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "num_turns": 11,
    }
    _pipe_popen(monkeypatch, [event], returncode=1)
    with pytest.raises(TurnCeilingReached) as pipe_exc:
        run_agent_streaming("sys", "user")

    stream = tmp_path / "agents" / "turnceiling.stream.jsonl"
    _journal_popen(monkeypatch, stream, [event], returncode=1)
    with pytest.raises(TurnCeilingReached) as journal_exc:
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)

    assert "num_turns=11" in str(pipe_exc.value)
    assert "num_turns=11" in str(journal_exc.value)


def test_lost_session_classification_parity_via_error_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stderr_text = "Error: No conversation found with session ID: sid-gone"

    _pipe_popen(monkeypatch, [], returncode=1, stderr=stderr_text)
    with pytest.raises(ResumeSessionLostError) as pipe_exc:
        run_agent_streaming("sys", "user", resume_session_id="sid-gone")

    stream = tmp_path / "agents" / "lost.stream.jsonl"
    _journal_popen(monkeypatch, stream, [], returncode=1, stderr=stderr_text)
    with pytest.raises(ResumeSessionLostError) as journal_exc:
        run_agent_streaming(
            "sys", "user", resume_session_id="sid-gone",
            stream_path=stream, journal_stream=True,
        )

    assert pipe_exc.value.session_id == "sid-gone"
    assert journal_exc.value.session_id == "sid-gone"


def test_terminal_error_signal_classification_parity_via_error_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stderr_text = "Authentication failed: invalid api key"

    _pipe_popen(monkeypatch, [], returncode=1, stderr=stderr_text)
    with pytest.raises(ClaudeCliError) as pipe_exc:
        run_agent_streaming("sys", "user")
    assert not isinstance(pipe_exc.value, ServerErrorRetryable)

    stream = tmp_path / "agents" / "term.stream.jsonl"
    _journal_popen(monkeypatch, stream, [], returncode=1, stderr=stderr_text)
    with pytest.raises(ClaudeCliError) as journal_exc:
        run_agent_streaming("sys", "user", stream_path=stream, journal_stream=True)
    assert not isinstance(journal_exc.value, ServerErrorRetryable)


def test_stale_err_content_from_prior_run_does_not_leak_into_classification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "staleerr.stream.jsonl"
    err = journal_err_path(stream)
    err.parent.mkdir(parents=True, exist_ok=True)
    err.write_text(
        "Error: No conversation found with session ID: sid-prior\n", encoding="utf-8"
    )
    event = {
        "type": "result",
        "is_error": True,
        "subtype": "error_during_execution",
        "result": "boom",
    }

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        _write_lines(stream, event)
        return _FakeProc(poll_sequence=[1], final_rc=1)

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming(
            "sys", "user", resume_session_id="sid-current",
            stream_path=stream, journal_stream=True,
        )
    assert not isinstance(exc_info.value, ResumeSessionLostError)
