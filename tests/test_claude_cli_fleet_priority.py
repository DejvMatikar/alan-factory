from __future__ import annotations

import io
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli
from alan.claude_cli import run_agent_streaming, run_chat_turn
from alan.fleet_load import FLEET_NICE_DELTA
from tests.test_claude_cli_streaming import _lines, _result_event


class _FakeProc:
    def __init__(self, pid: int, stdout_lines: list[str]) -> None:
        self.pid = pid
        self.stdin = io.StringIO()
        self.stdout: Iterator[str] = io.StringIO("".join(stdout_lines))
        self.stderr = io.StringIO("")
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        pass


def _patch_popen_capturing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout_lines: list[str],
    captured_envs: list[dict[str, str]] | None = None,
    pid: int = 4242,
) -> None:
    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        if captured_envs is not None:
            captured_envs.append(kwargs["env"])
        return _FakeProc(pid, stdout_lines)

    monkeypatch.setattr(subprocess, "Popen", fake)


def _patch_setpriority(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int]]:
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        os, "setpriority", lambda which, pid, value: calls.append((which, pid, value))
    )
    return calls


def test_fleet_spawn_lowers_priority_of_child_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen_capturing(monkeypatch, stdout_lines=_lines(_result_event("ok")), pid=9999)
    calls = _patch_setpriority(monkeypatch)
    run_agent_streaming("sys", "user", fleet=True)
    assert calls == [(os.PRIO_PROCESS, 9999, FLEET_NICE_DELTA)]


def test_non_fleet_spawn_does_not_touch_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_popen_capturing(monkeypatch, stdout_lines=_lines(_result_event("ok")))
    calls = _patch_setpriority(monkeypatch)
    run_agent_streaming("sys", "user")
    assert calls == []


def test_interactive_chat_turn_does_not_apply_fleet_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_popen_capturing(monkeypatch, stdout_lines=_lines(_result_event("ack")))
    calls = _patch_setpriority(monkeypatch)
    run_chat_turn(user_text="hi", session_id="sid-1", resume=False, system_prompt="sys prompt")
    assert calls == []


def test_fleet_spawn_env_carries_both_worker_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "setpriority", lambda *_a: None)
    envs: list[dict[str, str]] = []
    _patch_popen_capturing(
        monkeypatch, stdout_lines=_lines(_result_event("ok")), captured_envs=envs
    )
    run_agent_streaming("sys", "user", fleet=True)
    assert envs[0]["PYTEST_ADDOPTS"].startswith("-n 2")
    assert envs[0]["VITEST_MAX_WORKERS"] == "2"


def test_non_fleet_spawn_env_has_no_worker_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    envs: list[dict[str, str]] = []
    _patch_popen_capturing(
        monkeypatch, stdout_lines=_lines(_result_event("ok")), captured_envs=envs
    )
    run_agent_streaming("sys", "user")
    assert "VITEST_MAX_WORKERS" not in envs[0]


def test_interactive_chat_turn_env_has_no_worker_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    envs: list[dict[str, str]] = []
    _patch_popen_capturing(
        monkeypatch, stdout_lines=_lines(_result_event("ack")), captured_envs=envs
    )
    run_chat_turn(user_text="hi", session_id="sid-1", resume=False, system_prompt="sys")
    assert "VITEST_MAX_WORKERS" not in envs[0]


def test_ambient_fleet_worker_caps_are_scrubbed_from_every_test_env() -> None:
    assert "VITEST_MAX_WORKERS" not in os.environ
    assert "PYTEST_ADDOPTS" not in os.environ
    assert "ALAN_DISPATCHER_CONCURRENCY" not in os.environ
    assert "ALAN_DETACHED_WORKERS" not in os.environ


def test_spawn_streaming_cli_detached_path_also_gets_priority_reduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: list[dict[str, Any]] = []

    def fake(cmd: list[str], **kwargs: Any) -> _FakeProc:
        captured_kwargs.append(kwargs)
        return _FakeProc(pid=5555, stdout_lines=[])

    monkeypatch.setattr(subprocess, "Popen", fake)
    calls = _patch_setpriority(monkeypatch)
    proc = claude_cli._spawn_streaming_cli(  # noqa: SLF001
        ["claude", "-p"], "input", start_new_session=True, fleet=True
    )
    assert captured_kwargs[0]["start_new_session"] is True
    assert captured_kwargs[0]["env"]["VITEST_MAX_WORKERS"] == "2"
    assert calls == [(os.PRIO_PROCESS, 5555, FLEET_NICE_DELTA)]
    assert proc.pid == 5555


class _ImmediateFakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        self.returncode = 0
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_stream_subprocess_journal_forwards_fleet_flag_to_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_spawn(cmd: list[str], user_text: str, **kwargs: Any) -> _ImmediateFakeProc:
        captured.append(kwargs)
        return _ImmediateFakeProc(pid=7777)

    monkeypatch.setattr(claude_cli, "_spawn_streaming_cli", fake_spawn)
    stream = tmp_path / "agents" / "x.stream.jsonl"
    claude_cli._stream_subprocess_journal(  # noqa: SLF001
        ["claude"], "input", stream,
        on_event=None, on_session_id=None, cwd=None, timeout_seconds=10,
        on_proc_started=lambda _p: None, fleet=True,
    )
    assert captured[0]["start_new_session"] is True
    assert captured[0]["fleet"] is True
