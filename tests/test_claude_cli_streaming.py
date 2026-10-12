from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli
from alan.claude_cli import (
    ClaudeCliError,
    ClaudeCliTimeout,
    ProcessConfig,
    StreamEvent,
    TurnCeilingReached,
    run_agent_streaming,
)
from alan.claude_cli_grants import NO_TOOLS_BLOCK, credential_deny_rules


class _BlockingIter:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def __iter__(self) -> "_BlockingIter":
        return self

    def __next__(self) -> str:
        self._ev.wait()
        raise StopIteration

    def release(self) -> None:
        self._ev.set()

    def close(self) -> None:
        pass


class _FakePopen:
    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr: str = "",
        returncode: int = 0,
        stall_stdout: bool = False,
        hang_on_wait: bool = False,
    ) -> None:
        self.stdin = io.StringIO()
        self._blocking: _BlockingIter | None = None
        if stall_stdout:
            self._blocking = _BlockingIter()
            self.stdout: Iterator[str] = self._blocking
        else:
            self.stdout = io.StringIO("".join(stdout_lines or []))
        self.stderr = io.StringIO(stderr)
        self._rc = returncode
        self.returncode: int | None = None
        self.killed = False
        self._hang_on_wait = hang_on_wait

    def wait(self, timeout: float | None = None) -> int:
        if self._hang_on_wait and not self.killed and timeout is not None:
            raise subprocess.TimeoutExpired("fake-claude", timeout)
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        self.killed = True
        if self._blocking is not None:
            self._blocking.release()


def _result_event(text: str = "final text", **extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "result": text, **extra}


def _lines(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout_lines: list[str] | None = None,
    stderr: str = "",
    returncode: int = 0,
    raises: BaseException | None = None,
    capture_cmds: list[list[str]] | None = None,
    capture_stdin: list[str] | None = None,
) -> None:
    def fake(cmd: list[str], **_kwargs: Any) -> _FakePopen:
        if capture_cmds is not None:
            capture_cmds.append(cmd)
        if raises is not None:
            raise raises
        proc = _FakePopen(
            stdout_lines=stdout_lines,
            stderr=stderr,
            returncode=returncode,
        )
        if capture_stdin is not None:
            orig_write = proc.stdin.write

            def capture(s: str) -> int:
                capture_stdin.append(s)
                return orig_write(s)

            proc.stdin.write = capture  # type: ignore[method-assign]
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)


def test_streaming_returns_result_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": "hi"}},
            _result_event("hello world"),
        ),
    )
    assert run_agent_streaming("sys", "user").result_text == "hello world"


def test_streaming_strips_result_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("  spaced  ")))
    assert run_agent_streaming("sys", "user").result_text == "spaced"


def test_streaming_writes_raw_lines_to_stream_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "agents" / "x.stream.jsonl"
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({"type": "system"}, _result_event("ok")),
    )
    run_agent_streaming("sys", "user", stream_path=stream)
    written = stream.read_text(encoding="utf-8").splitlines()
    assert len(written) == 2
    assert json.loads(written[0]) == {"type": "system"}
    assert json.loads(written[1])["type"] == "result"


def test_streaming_appends_when_stream_path_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = tmp_path / "x.stream.jsonl"
    stream.write_text('{"type":"prev"}\n', encoding="utf-8")
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    run_agent_streaming("sys", "user", stream_path=stream)
    lines = stream.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"type": "prev"}
    assert json.loads(lines[1])["type"] == "result"


def test_streaming_passes_ci_parity_slot_pool_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", "/srv/alan-ci/slots")
    captured: list[dict[str, Any]] = []

    def fake(cmd: list[str], **kwargs: Any) -> _FakePopen:
        captured.append(kwargs)
        return _FakePopen(stdout_lines=_lines(_result_event("ok")))

    monkeypatch.setattr(subprocess, "Popen", fake)
    run_agent_streaming("sys", "user")

    assert captured[0]["env"]["ALAN_SLOT_POOL_ROOT"] == ""


def test_streaming_picks_up_active_cache_env_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    captured: list[dict[str, Any]] = []

    def fake(cmd: list[str], **kwargs: Any) -> _FakePopen:
        captured.append(kwargs)
        return _FakePopen(stdout_lines=_lines(_result_event("ok")))

    monkeypatch.setattr(subprocess, "Popen", fake)
    claude_cli.set_active_cache_env({"NUGET_PACKAGES": "nuget"})
    try:
        run_agent_streaming("sys", "user")
    finally:
        claude_cli.set_active_cache_env(None)

    assert captured[0]["env"]["NUGET_PACKAGES"] == str(tmp_path / "cache" / "nuget")


def test_streaming_on_event_called_live_per_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[StreamEvent] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system"},
            {"type": "assistant"},
            _result_event("ok"),
        ),
    )
    run_agent_streaming("sys", "user", on_event=captured.append)
    assert [e["type"] for e in captured] == ["system", "assistant", "result"]


def test_streaming_writes_stdin_then_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_stdin=captured,
    )
    run_agent_streaming("sys", "user payload")
    assert captured == ["user payload"]


def test_streaming_no_event_handler_or_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("done")))
    assert run_agent_streaming("sys", "user").result_text == "done"


def test_streaming_omits_allowed_tools_flag_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    assert len(cmds) == 1
    assert "--allowedTools" not in cmds[0]


def test_streaming_omits_allowed_tools_flag_when_list_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", allowed_tools=[])
    assert "--allowedTools" not in cmds[0]


def test_streaming_appends_allowed_tools_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming(
        "sys", "user", allowed_tools=["Edit", "Bash(git:*)"]
    )
    cmd = cmds[0]
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == "Edit"
    assert cmd[idx + 2] == "Bash(git:*)"


def test_streaming_opus_model_adds_effort_xhigh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", model="opus")
    cmd = cmds[0]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_streaming_sonnet_uses_effort_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", model="sonnet")
    cmd = cmds[0]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "high"


def test_streaming_effort_override_beats_model_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", model="sonnet", effort="xhigh")
    cmd = cmds[0]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "xhigh"


def test_streaming_defaults_permission_mode_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    cmd = cmds[0]
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "auto"


def test_streaming_overrides_permission_mode_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", permission_mode="acceptEdits")
    cmd = cmds[0]
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "acceptEdits"


def test_streaming_disallows_write_tools_in_auto_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    cmd = cmds[0]
    assert "--disallowedTools" in cmd
    deny_tokens = cmd[cmd.index("--disallowedTools") + 1].split()
    assert deny_tokens == ["Write", "Edit", "NotebookEdit"]


def test_streaming_accept_edits_denies_credentials_without_write_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", permission_mode="acceptEdits")
    deny = cmds[0][cmds[0].index("--disallowedTools") + 1 :]
    assert "Write Edit NotebookEdit" not in deny
    assert all(rule in deny for rule in credential_deny_rules())


def test_streaming_deny_all_tools_sets_wildcard_disallowed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", deny_all_tools=True)
    cmd = cmds[0]
    assert "--allowedTools" not in cmd
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"


def test_streaming_deny_all_tools_omits_allowed_tools_even_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming(
        "sys", "user", deny_all_tools=True, allowed_tools=["Read"]
    )
    assert "--allowedTools" not in cmds[0]


def test_streaming_deny_all_tools_omits_headless_and_project_doc_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    monkeypatch.setattr(claude_cli, "_project_doc_block", lambda: "PROJECT_DOC_SENTINEL")
    run_agent_streaming("sys prompt", "user", deny_all_tools=True)
    cmd = cmds[0]
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert NO_TOOLS_BLOCK in prompt_arg
    assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg
    assert "PROJECT_DOC_SENTINEL" not in prompt_arg


def test_streaming_skips_blank_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=[
            json.dumps({"type": "system"}) + "\n",
            "\n",
            "   \n",
            json.dumps(_result_event("ok")) + "\n",
        ],
    )
    assert run_agent_streaming("sys", "user").result_text == "ok"


def test_streaming_no_result_event_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines({"type": "system"}))
    with pytest.raises(ClaudeCliError, match="no result event"):
        run_agent_streaming("sys", "user")


def test_streaming_non_json_line_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(monkeypatch, stdout_lines=["not json {\n"])
    with pytest.raises(ClaudeCliError, match="non-JSON line"):
        run_agent_streaming("sys", "user")


def test_streaming_non_object_line_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, stdout_lines=['["array"]\n'])
    with pytest.raises(ClaudeCliError, match="non-object line"):
        run_agent_streaming("sys", "user")


def test_streaming_result_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "result", "result": "rate limited", "is_error": True}
        ),
    )
    with pytest.raises(ClaudeCliError, match="rate limited"):
        run_agent_streaming("sys", "user")


def test_streaming_result_error_subtype_no_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({"type": "result", "subtype": "error_quota"}),
    )
    with pytest.raises(ClaudeCliError, match="error_quota"):
        run_agent_streaming("sys", "user")


def test_streaming_result_error_unknown_no_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({"type": "result", "is_error": True}),
    )
    with pytest.raises(ClaudeCliError, match="unknown"):
        run_agent_streaming("sys", "user")


def test_streaming_result_empty_result_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "result", "subtype": "success", "result": "  "}
        ),
    )
    with pytest.raises(ClaudeCliError, match="result field empty"):
        run_agent_streaming("sys", "user")


def test_streaming_propagates_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        returncode=1,
        stderr="boom",
    )
    with pytest.raises(ClaudeCliError, match="exited 1"):
        run_agent_streaming("sys", "user")


def test_streaming_surfaces_error_max_turns_from_result_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "num_turns": 11,
            "stop_reason": "tool_use",
            "result": None,
        }),
        returncode=1,
        stderr="",
    )
    with pytest.raises(TurnCeilingReached) as excinfo:
        run_agent_streaming("sys", "user")
    msg = str(excinfo.value)
    assert "error_max_turns" in msg
    assert "num_turns=11" in msg
    assert "stop_reason=tool_use" in msg


def test_streaming_error_result_without_turns_metadata_still_surfaces_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": None,
        }),
        returncode=1,
        stderr="",
    )
    with pytest.raises(ClaudeCliError, match="error_during_execution"):
        run_agent_streaming("sys", "user")


def test_streaming_unknown_error_result_uses_unknown_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "is_error": True,
            "result": None,
        }),
        returncode=1,
        stderr="",
    )
    with pytest.raises(ClaudeCliError, match="claude CLI: unknown"):
        run_agent_streaming("sys", "user")


def test_streaming_nonzero_exit_with_empty_stream_falls_back_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=[],
        returncode=1,
        stderr="boom",
    )
    with pytest.raises(ServerErrorRetryable, match=r"before emitting a result event"):
        run_agent_streaming("sys", "user")


def test_streaming_nonzero_exit_without_terminator_result_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": "abc"}
        ),
        returncode=1,
        stderr="boom",
    )
    with pytest.raises(ServerErrorRetryable, match=r"before emitting a result event"):
        run_agent_streaming("sys", "user")


def test_streaming_error_result_appends_stderr_snippet_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "num_turns": 7,
        }),
        returncode=1,
        stderr="auxiliary warning text",
    )
    with pytest.raises(TurnCeilingReached) as excinfo:
        run_agent_streaming("sys", "user")
    assert "error_max_turns" in str(excinfo.value)
    assert "auxiliary warning text" in str(excinfo.value)


def test_streaming_resume_session_lost_detected_alongside_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ResumeSessionLostError

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
        }),
        stderr="Error: No conversation found with session ID: sid-gone",
        returncode=1,
    )
    with pytest.raises(ResumeSessionLostError) as exc_info:
        run_agent_streaming("sys", "user", resume_session_id="sid-gone")
    assert exc_info.value.session_id == "sid-gone"


def test_streaming_propagates_file_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, raises=FileNotFoundError("no claude"))
    with pytest.raises(ClaudeCliError, match="not found on PATH"):
        run_agent_streaming("sys", "user")


def test_streaming_propagates_timeout_and_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.05")
    proc_holder: list[_FakePopen] = []

    def fake(cmd: list[str], **_kwargs: Any) -> _FakePopen:
        proc = _FakePopen(stall_stdout=True)
        proc_holder.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout, match="idle for"):
        run_agent_streaming("sys", "user")
    assert proc_holder[0].killed is True


def test_streaming_timeout_reaps_subprocess_after_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ReapingPopen(_FakePopen):
        def __init__(self) -> None:
            super().__init__(stall_stdout=True)
            self.reap_wait_called = False

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                self.reap_wait_called = True
            return 0

    holder: list[_ReapingPopen] = []

    def fake(cmd: list[str], **_kwargs: Any) -> _ReapingPopen:
        proc = _ReapingPopen()
        holder.append(proc)
        return proc

    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout):
        run_agent_streaming("sys", "user")
    assert holder[0].killed is True
    assert holder[0].reap_wait_called is True


def test_streaming_raises_when_stdin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoStdin(_FakePopen):
        def __init__(self) -> None:
            super().__init__(stdout_lines=[json.dumps(_result_event("ok")) + "\n"])
            self.stdin = None  # type: ignore[assignment]

    holder: list[_NoStdin] = []

    def fake(cmd: list[str], **_kwargs: Any) -> _NoStdin:
        proc = _NoStdin()
        holder.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError, match="no stdin"):
        run_agent_streaming("sys", "user")
    assert holder[0].killed is True


def test_streaming_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=[json.dumps(_result_event("ok")) + "\n"],
        returncode=1,
    )
    with pytest.raises(ClaudeCliError, match="exited 1"):
        run_agent_streaming("sys", "user")


def test_streaming_passes_model_to_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_agent_streaming("sys", "user", model="opus")
    cmd = captured[0]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "10"


def test_streaming_passes_custom_max_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=captured,
    )
    run_agent_streaming("sys", "user", max_turns=80)
    cmd = captured[0]
    assert cmd[cmd.index("--max-turns") + 1] == "80"


def test_streaming_module_exports_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert callable(claude_cli.run_agent_streaming)
    assert hasattr(claude_cli, "StreamEvent")
    assert hasattr(claude_cli, "OnEvent")


def test_streaming_passes_session_id_flag_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", session_id="uuid-1234")
    cmd = cmds[0]
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == "uuid-1234"


def test_streaming_omits_session_id_flag_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    assert "--session-id" not in cmds[0]


def test_streaming_on_session_id_fires_on_system_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": "sid-abc"},
            _result_event("ok"),
        ),
    )
    run_agent_streaming("sys", "user", on_session_id=captured.append)
    assert captured == ["sid-abc"]


def test_streaming_on_session_id_fires_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": "first"},
            {"type": "system", "subtype": "init", "session_id": "second"},
            _result_event("ok"),
        ),
    )
    run_agent_streaming("sys", "user", on_session_id=captured.append)
    assert captured == ["first"]


def test_streaming_on_session_id_skipped_when_no_init_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({"type": "assistant"}, _result_event("ok")),
    )
    run_agent_streaming("sys", "user", on_session_id=captured.append)
    assert captured == []


def test_streaming_on_session_id_skipped_when_id_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": ""},
            {"type": "system", "subtype": "init", "session_id": "later"},
            _result_event("ok"),
        ),
    )
    run_agent_streaming("sys", "user", on_session_id=captured.append)
    assert captured == ["later"]


def test_streaming_on_session_id_callback_exception_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_sid: str) -> None:
        raise RuntimeError("disk full")

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {"type": "system", "subtype": "init", "session_id": "sid-x"},
            _result_event("ok"),
        ),
    )
    assert run_agent_streaming("sys", "user", on_session_id=boom).result_text == "ok"


def test_streaming_passes_resume_flag_instead_of_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", session_id="ignored", resume_session_id="sid-prior")
    cmd = cmds[0]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "sid-prior"
    assert "--session-id" not in cmd


def test_streaming_resume_session_lost_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ResumeSessionLostError

    _patch_popen(
        monkeypatch,
        stderr="Error: No conversation found with session ID: sid-gone",
        returncode=1,
    )
    with pytest.raises(ResumeSessionLostError) as exc_info:
        run_agent_streaming("sys", "user", resume_session_id="sid-gone")
    assert exc_info.value.session_id == "sid-gone"


def test_streaming_resume_passes_through_unrelated_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ClaudeCliError, ResumeSessionLostError

    _patch_popen(
        monkeypatch,
        stderr="some other failure",
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user", resume_session_id="sid-any")
    assert not isinstance(exc_info.value, ResumeSessionLostError)


def test_streaming_result_contains_usage_from_result_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "ok",
                usage={"input_tokens": 100, "output_tokens": 250},
            )
        ),
    )
    result = run_agent_streaming("sys", "user")
    assert result.usage["input_tokens"] == 100
    assert result.usage["output_tokens"] == 250


def test_streaming_result_usage_merges_total_cost_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event(
                "ok",
                usage={"input_tokens": 50},
                total_cost_usd=0.0012,
            )
        ),
    )
    result = run_agent_streaming("sys", "user")
    assert result.usage["total_cost_usd"] == 0.0012
    assert result.usage["input_tokens"] == 50


def test_streaming_result_usage_empty_when_no_usage_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    result = run_agent_streaming("sys", "user")
    assert result.usage == {}


def test_streaming_result_usage_captures_num_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok", num_turns=5)))
    result = run_agent_streaming("sys", "user")
    assert result.usage["num_turns"] == 5


def test_streaming_result_usage_skips_bool_num_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(monkeypatch, stdout_lines=_lines(_result_event("ok", num_turns=True)))
    result = run_agent_streaming("sys", "user")
    assert "num_turns" not in result.usage


def test_streaming_result_usage_skips_non_result_events_in_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            _result_event("ok", usage={"input_tokens": 77}),
            {"type": "system", "subtype": "finished"},
        ),
    )
    result = run_agent_streaming("sys", "user")
    assert result.usage["input_tokens"] == 77


def test_extract_stream_usage_returns_empty_when_no_result_event() -> None:
    events: list[tuple[str, StreamEvent]] = [
        ("line1", {"type": "system", "subtype": "init"}),
    ]
    assert claude_cli._extract_stream_usage(events) == {}


def test_streaming_server_error_overloaded_text_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "overloaded",
                "subtype": "error_during_execution",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")


def test_streaming_server_error_by_api_status_code_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "server failure",
                "subtype": "error_during_execution",
                "api_status_code": 503,
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")


def test_streaming_server_error_4xx_status_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "unauthorized",
                "subtype": "error_during_execution",
                "api_status_code": 401,
            }
        ),
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_server_error_by_api_error_type_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "api unavailable",
                "subtype": "error_during_execution",
                "api_error_type": "api_error",
            }
        ),
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")


def test_streaming_usage_limit_not_misidentified_as_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable, UsageLimitError

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "hit your limit",
                "subtype": "error_during_execution",
            }
        ),
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_is_server_error_no_structured_field_no_text_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "result": "max turns exceeded",
                "subtype": "error_max_turns",
            }
        ),
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_nonzero_exit_overloaded_result_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "subtype": "success",
                "num_turns": 1,
                "stop_reason": "stop_sequence",
                "result": "API Error: 529 Overloaded",
            }
        ),
        returncode=1,
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")


def test_streaming_nonzero_exit_server_error_status_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "server failure",
                "api_status_code": 503,
            }
        ),
        returncode=1,
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")


def test_streaming_nonzero_exit_usage_limit_raises_usage_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable, UsageLimitError

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "hit your limit",
            }
        ),
        returncode=1,
    )
    with pytest.raises(UsageLimitError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_nonzero_exit_generic_error_preserves_result_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "kaboom detail",
            }
        ),
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)
    assert "kaboom detail" in str(exc_info.value)


def test_streaming_nonzero_exit_4xx_status_not_retryable_preserves_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "unauthorized",
                "api_status_code": 401,
            }
        ),
        returncode=1,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)
    assert "unauthorized" in str(exc_info.value)


def test_abs_timeout_raises_claude_cli_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "9999")
    monkeypatch.setenv("ALAN_ABS_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakePopen(stall_stdout=True))
    with pytest.raises(ClaudeCliTimeout, match="absolute ceiling"):
        run_agent_streaming("sys", "user")


def test_continuous_output_not_killed_prematurely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_IDLE_TIMEOUT_SECONDS", "0.5")
    assistant_line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "x"}]},
    }) + "\n"
    lines = [assistant_line] * 20 + [json.dumps(_result_event("done")) + "\n"]
    _patch_popen(monkeypatch, stdout_lines=lines)
    result = run_agent_streaming("sys", "user")
    assert result.result_text == "done"


def test_streaming_timeout_when_proc_hangs_after_stdout_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_ABS_TIMEOUT_SECONDS", "0.05")
    proc_holder: list[_FakePopen] = []

    def fake(cmd: list[str], **_kwargs: Any) -> _FakePopen:
        proc = _FakePopen(
            stdout_lines=_lines(_result_event("ok")),
            hang_on_wait=True,
        )
        proc_holder.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliTimeout, match="absolute ceiling"):
        run_agent_streaming("sys", "user")
    assert proc_holder[0].killed is True


def test_stderr_flood_no_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr_payload = "x" * 200_000
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        stderr=stderr_payload,
    )
    result = run_agent_streaming("sys", "user")
    assert result.result_text == "ok"


def test_streaming_crash_before_result_with_terminal_stderr_stays_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=[],
        returncode=1,
        stderr="Authentication failed: invalid api key",
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_crash_before_result_not_logged_in_stays_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=[],
        returncode=1,
        stderr="not logged in",
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_streaming_crash_before_result_with_empty_stderr_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=[],
        returncode=1,
        stderr="",
    )
    with pytest.raises(ServerErrorRetryable) as exc_info:
        run_agent_streaming("sys", "user")
    assert "stderr" not in str(exc_info.value)


def test_streaming_network_signal_with_success_result_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        returncode=1,
        stderr="Error: connection reset by peer (ECONNRESET)",
    )
    with pytest.raises(ServerErrorRetryable, match=r"network signal in stderr"):
        run_agent_streaming("sys", "user")


def test_is_stderr_network_transient_true_for_transient_signal() -> None:
    from alan.claude_cli import _is_stderr_network_transient

    assert _is_stderr_network_transient("connection refused") is True


def test_is_stderr_network_transient_false_for_plain_error() -> None:
    from alan.claude_cli import _is_stderr_network_transient

    assert _is_stderr_network_transient("some random error") is False


def test_turn_ceiling_reached_is_subclass_of_claude_cli_error() -> None:
    assert issubclass(TurnCeilingReached, ClaudeCliError)


def test_turn_ceiling_reached_is_not_server_error_retryable() -> None:
    from alan.claude_cli import ServerErrorRetryable

    assert not issubclass(TurnCeilingReached, ServerErrorRetryable)


def test_streaming_nonzero_rc_error_max_turns_raises_turn_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "num_turns": 42,
        }),
        returncode=1,
    )
    with pytest.raises(TurnCeilingReached) as exc_info:
        run_agent_streaming("sys", "user")
    assert "error_max_turns" in str(exc_info.value)
    assert "num_turns=42" in str(exc_info.value)


def test_streaming_zero_rc_error_max_turns_raises_turn_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "num_turns": 5,
        }),
        returncode=0,
    )
    with pytest.raises(TurnCeilingReached) as exc_info:
        run_agent_streaming("sys", "user")
    assert "error_max_turns" in str(exc_info.value)


def test_streaming_non_ceiling_error_subtype_not_turn_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "some error",
        }),
        returncode=0,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, TurnCeilingReached)


def test_is_turn_ceiling_true_for_error_max_turns() -> None:
    event = {"type": "result", "subtype": "error_max_turns", "is_error": True}
    assert claude_cli._is_turn_ceiling(event) is True


def test_is_turn_ceiling_false_for_other_subtypes() -> None:
    for subtype in ("error_during_execution", "success", "", None):
        event: dict[str, object] = {"type": "result", "subtype": subtype, "is_error": True}
        assert claude_cli._is_turn_ceiling(event) is False


def test_raise_for_error_event_turn_ceiling_before_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ServerErrorRetryable

    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "api_status_code": 500,
            "num_turns": 10,
        }),
        returncode=0,
    )
    with pytest.raises(TurnCeilingReached) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)
    assert "error_max_turns" in str(exc_info.value)


_REAL_CHILD_OK = r"""
import json, sys
sys.stdin.read()
sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "result": "ok"}) + "\n")
sys.stdout.flush()
"""

_REAL_CHILD_FAIL = r"""
import json, sys
sys.stdin.read()
sys.stdout.write(json.dumps({
    "type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom",
}) + "\n")
sys.stdout.flush()
sys.exit(1)
"""

_REAL_CHILD_HANG = r"""
import sys, time
sys.stdin.read()
time.sleep(30)
"""

_REAL_CHILD_SLEEP_NO_STDIN = r"""
import time
time.sleep(30)
"""

_REAL_CHILD_GRANDCHILD_HOLDS_STDERR = r"""
import subprocess, sys, time
subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(2)"],
    stdout=subprocess.DEVNULL,
    stderr=sys.stderr,
)
time.sleep(2)
"""


def _spawn_real_child(
    monkeypatch: pytest.MonkeyPatch, script: str, *, no_stdin: bool = False
) -> None:
    real_popen = subprocess.Popen

    def fake(cmd: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        if no_stdin:
            kwargs["stdin"] = None
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake)


def _assert_pipes_closed(proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    assert proc.stdout.closed
    assert proc.stderr is not None
    assert proc.stderr.closed


def test_streaming_closes_pipes_on_normal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spawn_real_child(monkeypatch, _REAL_CHILD_OK)
    procs: list[subprocess.Popen[str]] = []
    result = run_agent_streaming("sys", "user", on_proc_started=procs.append)
    assert result.result_text == "ok"
    _assert_pipes_closed(procs[0])


def test_streaming_closes_pipes_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spawn_real_child(monkeypatch, _REAL_CHILD_FAIL)
    procs: list[subprocess.Popen[str]] = []
    with pytest.raises(ClaudeCliError):
        run_agent_streaming("sys", "user", on_proc_started=procs.append)
    _assert_pipes_closed(procs[0])


def test_streaming_closes_pipes_on_timeout_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spawn_real_child(monkeypatch, _REAL_CHILD_HANG)
    procs: list[subprocess.Popen[str]] = []
    with pytest.raises(ClaudeCliTimeout):
        run_agent_streaming(
            "sys", "user",
            on_proc_started=procs.append,
            proc_config=ProcessConfig(timeout_seconds=0.05),
        )
    _assert_pipes_closed(procs[0])


def test_streaming_closes_pipes_when_stream_sink_open_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _spawn_real_child(monkeypatch, _REAL_CHILD_HANG)
    bad_parent = tmp_path / "not_a_dir"
    bad_parent.write_text("x", encoding="utf-8")
    stream_path = bad_parent / "sub" / "log.jsonl"
    procs: list[subprocess.Popen[str]] = []
    with pytest.raises(OSError):
        run_agent_streaming(
            "sys", "user", on_proc_started=procs.append, stream_path=stream_path,
        )
    _assert_pipes_closed(procs[0])


def test_spawn_streaming_cli_closes_pipes_when_stdin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def fake(cmd: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        kwargs["stdin"] = None
        proc = real_popen(
            [sys.executable, "-c", _REAL_CHILD_SLEEP_NO_STDIN], **kwargs
        )
        captured.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(ClaudeCliError, match="no stdin"):
        run_agent_streaming("sys", "user")
    _assert_pipes_closed(captured[0])


def test_abort_child_returns_within_grace_when_reader_never_eofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claude_cli, "_REAP_GRACE_SECONDS", 0.1)
    _spawn_real_child(monkeypatch, _REAL_CHILD_GRANDCHILD_HOLDS_STDERR)
    start = time.monotonic()
    with pytest.raises(ClaudeCliTimeout):
        run_agent_streaming(
            "sys", "user", proc_config=ProcessConfig(timeout_seconds=0.05)
        )
    assert time.monotonic() - start < 2.0


def test_abort_child_kills_before_joining_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spawn_real_child(monkeypatch, _REAL_CHILD_HANG)
    calls: list[str] = []
    real_kill_and_reap = claude_cli._kill_and_reap
    real_join = threading.Thread.join

    def recording_kill_and_reap(proc: subprocess.Popen[str]) -> None:
        calls.append("kill")
        real_kill_and_reap(proc)

    def recording_join(
        self: threading.Thread, timeout: float | None = None
    ) -> None:
        calls.append("join")
        real_join(self, timeout)

    monkeypatch.setattr(claude_cli, "_kill_and_reap", recording_kill_and_reap)
    monkeypatch.setattr(threading.Thread, "join", recording_join)
    with pytest.raises(ClaudeCliTimeout):
        run_agent_streaming(
            "sys", "user", proc_config=ProcessConfig(timeout_seconds=0.05)
        )
    assert calls[0] == "kill"
    assert calls.count("kill") == 1
    assert calls.count("join") == 2
