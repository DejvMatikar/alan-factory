from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan import agent_key, claude_cli, launch_spec
from alan.fleet_load import FLEET_NICE_DELTA
from alan.launch_spec import LaunchSpec

_RESULT_EVENT = {"type": "result", "result": "done", "usage": {"input_tokens": 1}}


def _events() -> list[tuple[str, dict[str, Any]]]:
    return [(json.dumps(_RESULT_EVENT), dict(_RESULT_EVENT))]


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"cmds": []}

    def fake_stream(cmd: list[str], user_text: str, **kwargs: Any) -> list[Any]:
        seen["cmds"].append(cmd)
        seen["kwargs"] = kwargs
        seen["user_text"] = user_text
        return _events()

    def fake_invoke(cmd: list[str], user_text: str) -> subprocess.CompletedProcess[str]:
        seen["cmds"].append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "one-shot"}), stderr=""
        )

    monkeypatch.setattr(claude_cli, "_stream_subprocess", fake_stream)
    monkeypatch.setattr(claude_cli, "_invoke_cli", fake_invoke)
    return seen


def test_run_agent_streaming_hands_the_final_spec_to_on_spec_before_spawning(
    captured: dict[str, Any],
) -> None:
    specs: list[LaunchSpec] = []

    def on_spec(spec: LaunchSpec) -> None:
        assert captured["cmds"] == []
        specs.append(spec)

    claude_cli.run_agent_streaming(
        "prompt",
        "user",
        model="opus",
        max_turns=12,
        allowed_tools=["Read", "Bash(uv run:*)"],
        add_dirs=["/work"],
        proc_config=claude_cli.ProcessConfig(cwd=Path("/checkout"), timeout_seconds=99.0),
        session_id="sid",
        journal_stream=True,
        fleet=True,
        agent=agent_key.team_role("verification", "judge"),
        on_spec=on_spec,
    )
    (spec,) = specs
    assert spec.agent == agent_key.team_role("verification", "judge")
    assert spec.model == "opus"
    assert spec.effort == "xhigh"
    assert spec.max_turns == 12
    assert spec.allowed_tools == ("Read",)
    assert spec.add_dirs == ("/work",)
    assert spec.session_mode == "new"
    assert spec.process.cwd == "/checkout"
    assert spec.process.idle_timeout_seconds == 99.0
    assert spec.process.fleet is True
    assert spec.process.detached is True
    assert spec.process.priority_delta == FLEET_NICE_DELTA
    assert captured["cmds"] == [launch_spec.to_argv(spec)]


def test_run_agent_streaming_records_the_effort_the_cli_receives(
    captured: dict[str, Any],
) -> None:
    specs: list[LaunchSpec] = []
    claude_cli.run_agent_streaming("p", "u", model="sonnet", on_spec=specs.append)
    claude_cli.run_agent_streaming("p", "u", model="haiku", on_spec=specs.append)
    claude_cli.run_agent_streaming("p", "u", model="haiku", effort="low", on_spec=specs.append)
    assert [spec.effort for spec in specs] == ["high", None, "low"]
    assert specs[0].process.fleet is False
    assert specs[0].process.priority_delta is None
    assert specs[0].process.cwd is None
    assert specs[0].process.idle_timeout_seconds == claude_cli.idle_timeout_seconds()


def test_run_agent_one_shot_path_passes_the_agent_and_spec(captured: dict[str, Any]) -> None:
    specs: list[LaunchSpec] = []
    text = claude_cli.run_agent(
        "prompt", "user", model="haiku", deny_all_tools=True, agent=agent_key.COLLISION,
        on_spec=specs.append,
    )
    assert text == "one-shot"
    (spec,) = specs
    assert spec.agent == agent_key.COLLISION
    assert spec.deny_all_tools is True
    assert spec.output_format == "json"
    assert spec.process.abs_timeout_seconds is None
    assert spec.process.idle_timeout_seconds == float(claude_cli._cli_timeout_seconds())  # noqa: SLF001
    assert captured["cmds"] == [launch_spec.to_argv(spec)]


def test_run_agent_streaming_route_passes_agent_and_on_spec_through(
    captured: dict[str, Any], tmp_path: Path
) -> None:
    specs: list[LaunchSpec] = []
    claude_cli.run_agent(
        "prompt", "user", stream_path=tmp_path / "s.jsonl", agent=agent_key.MANAGER_SUMMARY,
        on_spec=specs.append,
    )
    (spec,) = specs
    assert spec.agent == agent_key.MANAGER_SUMMARY
    assert spec.max_turns == 3
    assert spec.process.idle_timeout_seconds == float(claude_cli._cli_timeout_seconds())  # noqa: SLF001


def test_run_chat_turn_launches_with_the_argv_of_chat_launch_spec(
    captured: dict[str, Any],
) -> None:
    result = claude_cli.run_chat_turn(
        user_text="hi",
        session_id="sid",
        resume=True,
        system_prompt="chat",
        model="sonnet",
        cwd=Path("/repo"),
        agent=agent_key.BUILD_CHAT,
    )
    assert result.result_text == "done"
    spec = claude_cli.chat_launch_spec(
        model="sonnet",
        session_id="sid",
        resume=True,
        max_turns=80,
        allowed_tools=claude_cli._DEFAULT_CHAT_ALLOWED_TOOLS,  # noqa: SLF001
        system_prompt="chat",
        effort="high",
        cwd=Path("/repo"),
        agent=agent_key.BUILD_CHAT,
    )
    assert spec.layout == "chat"
    assert spec.agent == agent_key.BUILD_CHAT
    assert spec.session_mode == "resumed"
    assert spec.effort == "high"
    assert spec.process.cwd == "/repo"
    assert spec.process.idle_timeout_seconds == float(claude_cli._chat_turn_timeout_seconds())  # noqa: SLF001
    assert captured["cmds"] == [launch_spec.to_argv(spec)]


def test_a_failing_on_spec_is_logged_and_never_aborts_the_run(
    captured: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_spec: LaunchSpec) -> None:
        raise RuntimeError("record disk full")

    with caplog.at_level(logging.ERROR, logger="alan.claude_cli"):
        result = claude_cli.run_agent_streaming("p", "u", on_spec=boom)
    assert result.result_text == "done"
    assert "on_spec callback raised" in caplog.text
    assert len(captured["cmds"]) == 1


def test_worker_env_policy_marks_fleet_ceilings_only_for_fleet_workers() -> None:
    plain = {entry.category for entry in claude_cli._worker_env_policy()}  # noqa: SLF001
    fleet = {entry.category for entry in claude_cli._worker_env_policy(fleet=True)}  # noqa: SLF001
    assert "fleet_ceiling" not in plain
    assert "fleet_ceiling" in fleet


@pytest.mark.parametrize("deny_all_tools", [False, True])
def test_the_appended_system_prompt_is_the_joined_segments(
    monkeypatch: pytest.MonkeyPatch, deny_all_tools: bool
) -> None:
    monkeypatch.setattr(claude_cli, "_project_doc_block", lambda: "\n\nDOC")
    segments = claude_cli._prompt_segments("body", deny_all_tools=deny_all_tools)  # noqa: SLF001
    appended = claude_cli._appended_system_prompt("body", deny_all_tools=deny_all_tools)  # noqa: SLF001
    assert appended == "".join(segment.text for segment in segments)
    assert appended.startswith("body")
    assert appended.endswith("\n\nDOC") is (not deny_all_tools)
