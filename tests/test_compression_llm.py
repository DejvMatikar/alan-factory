from __future__ import annotations

import io
import json
import subprocess
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan.claude_cli_grants import NO_TOOLS_BLOCK
from alan.compression import (
    _COMPACTOR_TARGET_CHARS,
    _COMPACTOR_VERSION,
    _LLM_COMPACT_DISABLE_ENV,
    _LLM_COMPACT_THRESHOLD,
    load_cross_team_prior_outputs,
    summarize_handover,
)
from alan.handover import Handover, save_handover
from alan.workspace import Workspace, create

_STUB_SUMMARY = "LLM-compacted summary for test."
_LONG_SYNTHESIS = "x" * (_LLM_COMPACT_THRESHOLD + 100)
_SHORT_SYNTHESIS = "short"


def _make_handover(
    ws: Workspace,
    *,
    handover_id: str | None = None,
    synthesis: str = _LONG_SYNTHESIS,
    from_team: str = "research",
    to_team_suggestions: list[str] | None = None,
) -> Handover:
    hid = handover_id or f"h_{ws.task_id}"
    return Handover(
        handover_id=hid,
        handover_type="forward",
        task_id=ws.task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or ["next"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[],
        open_questions=[],
        forbidden_actions=[],
        full_synthesis=synthesis,
    )


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
        cmd: list[str],
        *,
        result_text: str | None = _STUB_SUMMARY,
        returncode: int = 0,
        stall_stdout: bool = False,
        call_log: list[int] | None = None,
        **_kwargs: Any,
    ) -> None:
        if call_log is not None:
            call_log.append(1)
        self.stdin = io.StringIO()
        self._blocking: _BlockingIter | None = None
        if stall_stdout:
            self._blocking = _BlockingIter()
            self.stdout: Iterator[str] = self._blocking
        elif result_text is not None and returncode == 0:
            line = json.dumps(
                {"type": "result", "subtype": "success", "result": result_text}
            )
            self.stdout = io.StringIO(line + "\n")
        else:
            self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self._rc = returncode
        self.returncode: int | None = None
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        self.killed = True
        if self._blocking is not None:
            self._blocking.release()


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result_text: str | None = _STUB_SUMMARY,
    returncode: int = 0,
    stall_stdout: bool = False,
    call_log: list[int] | None = None,
) -> None:
    def _fake(cmd: list[str], **kwargs: Any) -> _FakePopen:
        return _FakePopen(
            cmd,
            result_text=result_text,
            returncode=returncode,
            stall_stdout=stall_stdout,
            call_log=call_log,
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "Popen", _fake)


def test_under_threshold_bypasses_haiku(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("short", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_SHORT_SYNTHESIS)
    result = summarize_handover(h, workspace=ws)
    assert _SHORT_SYNTHESIS in result
    assert len(popen_calls) == 0


def test_env_disabled_bypasses_haiku(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    monkeypatch.setenv(_LLM_COMPACT_DISABLE_ENV, "1")
    ws = create("disabled", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_LONG_SYNTHESIS)
    result = summarize_handover(h, workspace=ws)
    assert "x" * 10 in result
    assert len(popen_calls) == 0


def test_over_threshold_compacts(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("compact", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_LONG_SYNTHESIS)
    result = summarize_handover(h, workspace=ws)
    assert _STUB_SUMMARY in result
    assert len(popen_calls) == 1


def test_cache_hit_on_second_call(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("cachehit", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_LONG_SYNTHESIS)
    result1 = summarize_handover(h, workspace=ws)
    result2 = summarize_handover(h, workspace=ws)
    assert result1 == result2
    assert len(popen_calls) == 1


def test_cache_miss_different_handover_id(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("cachemiss", "d", root=ws_root)
    h1 = _make_handover(ws, handover_id="h_a", synthesis=_LONG_SYNTHESIS)
    h2 = _make_handover(ws, handover_id="h_b", synthesis=_LONG_SYNTHESIS)
    summarize_handover(h1, workspace=ws)
    summarize_handover(h2, workspace=ws)
    assert len(popen_calls) == 2


def test_stale_cache_version_triggers_new_call(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("stale", "d", root=ws_root)
    h = _make_handover(ws, handover_id="h_stale", synthesis=_LONG_SYNTHESIS)
    cache_dir = ws.path / "handovers" / ".compact"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stale_path = cache_dir / "h_stale.txt"
    stale_path.write_text(
        f"# v=deadbeef target={_COMPACTOR_TARGET_CHARS}\nold body\n",
        encoding="utf-8",
    )
    result = summarize_handover(h, workspace=ws)
    assert _STUB_SUMMARY in result
    assert len(popen_calls) == 1


def test_cache_empty_body_treated_as_miss(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("emptybody", "d", root=ws_root)
    h = _make_handover(ws, handover_id="h_empty", synthesis=_LONG_SYNTHESIS)
    cache_dir = ws.path / "handovers" / ".compact"
    cache_dir.mkdir(parents=True, exist_ok=True)
    empty_path = cache_dir / "h_empty.txt"
    empty_path.write_text(
        f"# v={_COMPACTOR_VERSION} target={_COMPACTOR_TARGET_CHARS}\n",
        encoding="utf-8",
    )
    result = summarize_handover(h, workspace=ws)
    assert _STUB_SUMMARY in result
    assert len(popen_calls) == 1


def test_haiku_failure_falls_back_to_lexical(
    monkeypatch: pytest.MonkeyPatch,
    ws_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_popen(monkeypatch, returncode=1, result_text=None)
    ws = create("failback", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_LONG_SYNTHESIS)
    with caplog.at_level("WARNING", logger="alan.compression"):
        result = summarize_handover(h, workspace=ws)
    assert "x" * 10 in result
    assert any("falling back to lexical" in r.message for r in caplog.records)


def test_haiku_timeout_falls_back_to_lexical(
    monkeypatch: pytest.MonkeyPatch,
    ws_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("alan.compression._COMPACT_TIMEOUT_SECONDS", 0.05)
    _patch_popen(monkeypatch, stall_stdout=True)
    ws = create("timeoutback", "d", root=ws_root)
    h = _make_handover(ws, synthesis=_LONG_SYNTHESIS)
    with caplog.at_level("WARNING", logger="alan.compression"):
        result = summarize_handover(h, workspace=ws)
    assert "x" * 10 in result
    assert any("falling back to lexical" in r.message for r in caplog.records)


def test_load_cross_team_prior_outputs_compacts_over_threshold(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    popen_calls: list[int] = []
    _patch_popen(monkeypatch, call_log=popen_calls)
    ws = create("crossteam", "d", root=ws_root)
    h = _make_handover(
        ws,
        handover_id=f"h_{ws.task_id}_ct",
        synthesis=_LONG_SYNTHESIS,
        to_team_suggestions=["next"],
    )
    save_handover(ws, h)
    result = load_cross_team_prior_outputs(ws, "next")
    assert _STUB_SUMMARY in result
    assert len(popen_calls) == 1


def test_run_agent_streaming_timeout_seconds_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import ProcessConfig, run_agent_streaming

    captured_cmds: list[list[str]] = []

    class _FP:
        def __init__(self, cmd: list[str], **_kw: Any) -> None:
            captured_cmds.append(cmd)
            self.stdin = io.StringIO()
            line = json.dumps(
                {"type": "result", "subtype": "success", "result": "ok"}
            )
            self.stdout: Any = io.StringIO(line + "\n")
            self.stderr = io.StringIO("")
            self.returncode: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", _FP)
    result = run_agent_streaming("sys", "user", proc_config=ProcessConfig(timeout_seconds=15))
    assert result.result_text == "ok"
    assert len(captured_cmds) == 1


def test_compactor_system_prompt_carries_verbatim_preserve_list() -> None:
    from alan.compression import _COMPACTOR_SYSTEM_PROMPT

    assert "Preserve verbatim" in _COMPACTOR_SYSTEM_PROMPT
    assert "file paths" in _COMPACTOR_SYSTEM_PROMPT
    assert "commands" in _COMPACTOR_SYSTEM_PROMPT
    assert "error messages and gate names" in _COMPACTOR_SYSTEM_PROMPT
    assert "PR/issue numbers" in _COMPACTOR_SYSTEM_PROMPT
    assert "unresolved blockers" in _COMPACTOR_SYSTEM_PROMPT
    assert "the concrete next step" in _COMPACTOR_SYSTEM_PROMPT
    assert "cut oldest progress detail first" in _COMPACTOR_SYSTEM_PROMPT


def test_call_compactor_llm_uses_deny_all_tools_and_omits_appended_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.claude_cli import _HEADLESS_EXECUTION_BLOCK
    from alan.compression import _call_compactor_llm

    monkeypatch.setattr("alan.claude_cli._project_doc_block", lambda: "PROJECT_DOC_SENTINEL")
    captured_cmds: list[list[str]] = []

    class _FP:
        def __init__(self, cmd: list[str], **_kw: Any) -> None:
            captured_cmds.append(cmd)
            self.stdin = io.StringIO()
            line = json.dumps(
                {"type": "result", "subtype": "success", "result": "compacted"}
            )
            self.stdout: Any = io.StringIO(line + "\n")
            self.stderr = io.StringIO("")
            self.returncode: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", _FP)
    result = _call_compactor_llm("some long synthesis text")

    assert result == "compacted"
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"
    assert "--allowedTools" not in cmd
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert NO_TOOLS_BLOCK in prompt_arg
    assert _HEADLESS_EXECUTION_BLOCK not in prompt_arg
    assert "PROJECT_DOC_SENTINEL" not in prompt_arg
    assert "Preserve verbatim" in prompt_arg
