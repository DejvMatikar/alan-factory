from __future__ import annotations

import io
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from alan.claude_cli import ClaudeCliError, ServerErrorRetryable, run_agent_streaming
from tests.test_claude_cli_streaming import _lines, _result_event

_DISCONNECT_MESSAGE = (
    "API Error: The response stopped arriving. "
    "The response above may be incomplete."
)


class _FakeProc:
    def __init__(self, stdout_lines: list[str], returncode: int) -> None:
        self.stdin = io.StringIO()
        self.stdout: Iterator[str] = io.StringIO("".join(stdout_lines))
        self.stderr = io.StringIO("")
        self._rc = returncode
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        pass


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch, *, stdout_lines: list[str], returncode: int
) -> None:
    def fake(cmd: list[str], **_kwargs: Any) -> _FakeProc:
        return _FakeProc(stdout_lines, returncode)

    monkeypatch.setattr(subprocess, "Popen", fake)


def test_nonzero_exit_disconnect_result_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "num_turns": 125,
            "stop_reason": "stop_sequence",
            "result": _DISCONNECT_MESSAGE,
        }),
        returncode=1,
    )
    with pytest.raises(ServerErrorRetryable) as exc_info:
        run_agent_streaming("sys", "user")
    assert _DISCONNECT_MESSAGE in str(exc_info.value)


def test_zero_exit_error_result_disconnect_raises_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "is_error": True,
            "subtype": "error_during_execution",
            "result": _DISCONNECT_MESSAGE,
        }),
        returncode=0,
    )
    with pytest.raises(ServerErrorRetryable) as exc_info:
        run_agent_streaming("sys", "user")
    assert _DISCONNECT_MESSAGE in str(exc_info.value)


def test_unrelated_error_result_still_raises_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event(
            "API Error: 400 Bad Request", is_error=True, subtype="error_during_execution",
        )),
        returncode=0,
    )
    with pytest.raises(ClaudeCliError) as exc_info:
        run_agent_streaming("sys", "user")
    assert not isinstance(exc_info.value, ServerErrorRetryable)


def test_uppercase_disconnect_text_still_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines({
            "type": "result",
            "is_error": True,
            "subtype": "error_during_execution",
            "result": _DISCONNECT_MESSAGE.upper(),
        }),
        returncode=0,
    )
    with pytest.raises(ServerErrorRetryable):
        run_agent_streaming("sys", "user")
