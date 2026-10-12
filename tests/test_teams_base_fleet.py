from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.detached_mode import DIAL_ENV
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS, FLEET_NICE_DELTA
from alan.teams import _base as base
from alan.workspace import create
from tests.test_claude_cli_fleet_priority import _FakeProc, _patch_setpriority
from tests.test_claude_cli_streaming import _lines, _result_event


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _patch_popen_for_claude_only(
    monkeypatch: pytest.MonkeyPatch, *, stdout_lines: list[str], pid: int
) -> None:
    real_popen = subprocess.Popen

    def fake(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        if cmd and cmd[0] == "claude":
            return _FakeProc(pid, stdout_lines)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake)


def test_run_or_resume_node_reaches_spawn_with_fleet_true(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    _patch_popen_for_claude_only(monkeypatch, stdout_lines=_lines(_result_event("ok")), pid=6001)
    calls = _patch_setpriority(monkeypatch)

    workspace = create("fleet-wiring", "d", root=root)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="implementation"
    )
    base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet", depends_on=[],
        system_prompt="p", user_text="u",
    )
    assert calls == [(os.PRIO_PROCESS, 6001, FLEET_NICE_DELTA)]


def test_run_or_resume_node_passes_fleet_idle_timeout_via_proc_config(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    captured: dict[str, Any] = {}

    def fake_run_agent_streaming(*_args: Any, **kwargs: Any) -> AgentStreamingResult:
        captured.update(kwargs)
        return AgentStreamingResult(result_text="ok")

    monkeypatch.setattr(base, "run_agent_streaming", fake_run_agent_streaming)

    workspace = create("fleet-idle-timeout", "d", root=root)
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = workflow_mod.WorkflowGraph(
        task_id=workspace.task_id, inv_id=inv_id, team="implementation"
    )
    base.run_or_resume_node(
        workspace, graph, inv_id, lambda _e: None,
        node_id="implementer", role="implementer", model="sonnet", depends_on=[],
        system_prompt="p", user_text="u",
    )
    assert captured["proc_config"].timeout_seconds == FLEET_IDLE_TIMEOUT_SECONDS
