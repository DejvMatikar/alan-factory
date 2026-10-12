from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alan import agent_key, claude_cli
from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.launch_spec import LaunchSpec
from alan.teams import _base as base
from alan.teams import _node_attach, _node_launch
from alan.teams._base import SingleAgentTeam, WorkerOverrides
from alan.workflow import WorkflowGraph, load_node_config, load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("node launch", "d", root=tmp_path / "workspaces")


def _fake_streaming(
    seen: list[dict[str, Any]], *, record_launch: bool = True
) -> Callable[..., AgentStreamingResult]:
    def fake(system_prompt: str, user_text: str, **kwargs: Any) -> AgentStreamingResult:
        seen.append(kwargs)
        if record_launch:
            kwargs["on_spec"](
                claude_cli.streaming_spec(
                    system_prompt,
                    model=kwargs["model"],
                    effort=kwargs.get("effort"),
                    max_turns=kwargs["max_turns"],
                    session_id=kwargs.get("session_id"),
                    resume_session_id=kwargs.get("resume_session_id"),
                    mode=claude_cli.ProcessMode(fleet=kwargs.get("fleet", False)),
                    agent=kwargs["agent"],
                )
            )
        return AgentStreamingResult(result_text="agent output")

    return fake


def _run_team(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "architecture",
    record_launch: bool = True,
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    fake = _fake_streaming(seen, record_launch=record_launch)
    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(name=name, system_prompt="role prompt", to_team_suggestions=())
    seed_and_run(team, workspace, None, lambda _e: None)
    return seen


def test_a_fresh_node_launch_passes_its_agent_key_and_a_spec_recorder(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    (kwargs,) = _run_team(workspace, monkeypatch)
    assert kwargs["agent"] == agent_key.team_role("architecture", "architecture")
    assert callable(kwargs["on_spec"])


def test_the_orchestrated_worker_roles_share_one_agent_key(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    (kwargs,) = _run_team(workspace, monkeypatch, name="research")
    assert kwargs["agent"] == agent_key.team_role("research", "any_worker_role")
    assert kwargs["agent"].name == "research/worker"


def test_on_spec_writes_the_launch_record_next_to_the_node_config(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_team(workspace, monkeypatch)
    cfg = load_node_config(workspace, "inv_001", "architecture")
    assert cfg is not None
    launch = cfg["launch"]
    assert launch["agent"] == "team_role:architecture/architecture"
    assert launch["session_mode"] == "new"
    assert launch["fleet"] is True
    assert launch["max_turns"] == cfg["max_turns"]
    assert [segment["label"] for segment in launch["prompt_segments"]][:2] == [
        "system_prompt",
        "turn_budget",
    ]
    assert cfg["system_prompt"] == "".join(
        segment["text"] for segment in launch["prompt_segments"][:2]
    )


def test_no_launch_key_is_written_when_the_launch_never_reports_a_spec(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_team(workspace, monkeypatch, record_launch=False)
    cfg = load_node_config(workspace, "inv_001", "architecture")
    assert cfg is not None
    assert "launch" not in cfg


@pytest.mark.parametrize(
    ("model", "requested", "recorded"),
    [("opus", None, "xhigh"), ("sonnet", None, "high"), ("haiku", None, None),
     ("haiku", "low", "low"), ("opus", "medium", "medium")],
)
def test_start_node_records_the_effort_the_cli_receives(
    workspace: Workspace, model: str, requested: str | None, recorded: str | None
) -> None:
    inv_id = workflow_mod.create_invocation_dir(workspace)
    graph = WorkflowGraph(task_id=workspace.task_id, inv_id=inv_id, team="architecture")
    node = base._start_node_locked(  # noqa: SLF001
        workspace, graph, "node_a", role="node_a", model=model, effort=requested, depends_on=[]
    )
    assert node.effort == recorded
    assert load_workflow(workflow_path(workspace, inv_id)).tasks[0].effort == recorded


def _interrupt(workspace: Workspace) -> None:
    graph = workflow_mod.load_graph(workspace, "inv_001")
    graph.set_session_id("architecture", "sess-1")
    graph.mark_interrupted("architecture", error="restart")
    workflow_mod.save_workflow(workspace, graph.doc)


def test_resuming_a_node_rewrites_the_launch_with_the_resumed_session(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_team(workspace, monkeypatch)
    first = load_node_config(workspace, "inv_001", "architecture")
    assert first is not None and first["launch"]["session_mode"] == "new"
    _interrupt(workspace)
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(base, "run_agent_streaming", _fake_streaming(seen))
    graph = workflow_mod.load_graph(workspace, "inv_001")
    base.resume_workflow_node(
        workspace, graph, "inv_001", lambda _e: None, node_id="architecture",
        system_prompt="role prompt", user_text="again", overrides=None,
    )
    resumed = load_node_config(workspace, "inv_001", "architecture")
    assert resumed is not None
    assert resumed["launch"]["session_mode"] == "resumed"
    assert resumed["user_text"] == "again"
    assert seen[0]["resume_session_id"] == "sess-1"
    assert seen[0]["agent"] == agent_key.team_role("architecture", "architecture")


def test_attaching_to_a_surviving_worker_writes_nothing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_team(workspace, monkeypatch)
    path = workflow_mod.config_path(workspace, "inv_001", "architecture")
    before = path.read_text(encoding="utf-8")
    _interrupt(workspace)

    def completed_attach(*_args: Any, **_kwargs: Any) -> _node_attach.AttachOutcome:
        return _node_attach.AttachOutcome(completed=True, result_text="attached", session_id="s")

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("attach must not write a launch record")

    monkeypatch.setattr(_node_attach, "attach", completed_attach)
    monkeypatch.setattr(workflow_mod, "save_node_launch", forbidden)
    monkeypatch.setattr(workflow_mod, "save_node_config", forbidden)
    graph = workflow_mod.load_graph(workspace, "inv_001")
    result = base._attach_or_resume(  # noqa: SLF001
        workspace, graph, "inv_001", lambda _e: None, node_id="architecture",
        system_prompt="p", user_text="u", overrides=None, on_proc_started=None, task_id=None,
    )
    assert result == "attached"
    assert path.read_text(encoding="utf-8") == before


def test_a_recorder_failure_cannot_abort_the_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(workflow_mod, "save_node_launch", broken)
    seen: list[dict[str, Any]] = []

    def fake(system_prompt: str, user_text: str, **kwargs: Any) -> AgentStreamingResult:
        seen.append(kwargs)
        spec = claude_cli.streaming_spec(system_prompt, agent=kwargs["agent"])
        claude_cli._notify_spec(kwargs["on_spec"], spec)  # noqa: SLF001
        return AgentStreamingResult(result_text="survived")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    team = SingleAgentTeam(name="architecture", system_prompt="p", to_team_suggestions=())
    handover = seed_and_run(team, workspace, None, lambda _e: None)
    assert handover.full_synthesis == "survived"
    assert len(seen) == 1


def test_prepare_node_context_persists_the_budgeted_prompt_and_returns_the_context(
    workspace: Workspace,
) -> None:
    inv_id = workflow_mod.create_invocation_dir(workspace)
    opts = WorkerOverrides(max_turns=40)
    budget = _node_launch.TurnBudget(applied=40, derived=40, source="static")
    prepared = _node_launch.prepare_node_context(
        workspace, inv_id, "node_x",
        opts=opts,
        effective_kwargs={"allowed_tools": ["Read"], "permission_mode": "acceptEdits"},
        budget=budget,
        system_prompt="BASE",
        user_text="u",
        model="sonnet",
    )
    assert prepared.augmented_prompt == "BASE\n\nTurn budget for this run: 40"
    assert prepared.effective_max_turns == 40
    cfg = json.loads(workflow_mod.config_path(workspace, inv_id, "node_x").read_text("utf-8"))
    assert cfg["permission_mode"] == "acceptEdits"
    assert cfg["turns_source"] == "static"


def test_launch_recorder_returns_a_callable_that_persists_into_the_node_config(
    workspace: Workspace,
) -> None:
    inv_id = workflow_mod.create_invocation_dir(workspace)
    workflow_mod.save_node_config(
        workspace, inv_id, "node_y", system_prompt="s", user_text="u", allowed_tools=[],
        max_turns=1, permission_mode="auto", model="haiku", turns_source="static",
        derived_max_turns=1,
    )
    spec: LaunchSpec = claude_cli.streaming_spec("s", model="haiku")
    _node_launch.launch_recorder(workspace, inv_id, "node_y")(spec)
    cfg = load_node_config(workspace, inv_id, "node_y")
    assert cfg is not None and cfg["launch"]["effort"] is None
