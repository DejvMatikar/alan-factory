from __future__ import annotations

from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.teams import _base as base
from alan.teams import _pr_forge, architecture, decomposer, maintenance, scenario_authoring
from alan.teams import _static_pipeline as sp
from alan.teams.implementation_plan import ImplementationPlanTeam
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create


def _seed_success_node(
    ws: Workspace, team_name: str, role_name: str, *, session_id: str = "sess-1"
) -> str:
    specs = [PendingTaskSpec(id=role_name, role=role_name, model="sonnet")]
    inv_id = workflow_mod.seed_pending_workflow(ws, team=team_name, tasks=specs)
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(role_name, role=role_name, model="sonnet", depends_on=[])
    graph.set_session_id(role_name, session_id)
    graph.complete_node(role_name)
    workflow_mod.save_workflow(ws, graph.doc)
    return inv_id


def test_architecture_decomposer_maintenance_keep_unmodified_role_prompts(
    tmp_path: Path,
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    for team in (architecture.TEAM, decomposer.TEAM, maintenance.TEAM):
        for role in team.roles:
            assert team._role_system_prompt(role, ws) == role.system_prompt  # noqa: SLF001


def test_implementation_plan_overrides_only_the_synthesizer_role(tmp_path: Path) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    team = ImplementationPlanTeam()
    architect_role = next(r for r in team.roles if r.name == "architect")
    synthesizer_role = next(r for r in team.roles if r.name == "synthesizer")
    assert team._role_system_prompt(architect_role, ws) == architect_role.system_prompt  # noqa: SLF001
    cfg = _pr_forge.governing_config_for_ws(ws)
    assert team._role_system_prompt(synthesizer_role, ws) == (  # noqa: SLF001
        scenario_authoring.synthesizer_prompt(cfg)
    )


def test_role_system_prompt_seam_applies_to_both_the_fresh_and_correction_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("ws", "d", root=tmp_path / "ws")

    def fake_role_system_prompt(
        self: sp.StaticPipelineTeam, role: sp.Role, workspace: Workspace
    ) -> str:
        del role
        return f"scoped-prompt:{workspace.task_id}"

    monkeypatch.setattr(sp.StaticPipelineTeam, "_role_system_prompt", fake_role_system_prompt)
    team = sp.StaticPipelineTeam(
        name="t", roles=(sp.Role("r", "BASE_PROMPT", "sonnet"),), to_team_suggestions=()
    )
    scoped_marker = f"scoped-prompt:{ws.task_id}"
    captured: list[str] = []

    def fake_streaming(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del user_text, model, on_event, max_turns, cwd
        captured.append(system_prompt)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
        return AgentStreamingResult(result_text="fresh-output")

    monkeypatch.setattr(base, "run_agent_streaming", fake_streaming)
    specs = team.plan_workflow(ws, None)
    inv_id = workflow_mod.seed_pending_workflow(ws, team=team.name, tasks=specs)
    team.run_pipeline(ws, None, lambda _e: None, inv_id=inv_id)
    assert len(captured) == 1
    assert scoped_marker in captured[0]
    fresh_turn_prompt = captured[0]

    inv_id2 = _seed_success_node(ws, team.name, "r")
    captured.clear()

    def fake_streaming_correction(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del user_text, model, on_event, max_turns, cwd
        captured.append(system_prompt)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
        return AgentStreamingResult(result_text="fixed")

    monkeypatch.setattr(base, "run_agent_streaming", fake_streaming_correction)

    def _parse(text: str) -> str:
        if text == "fixed":
            return text.upper()
        raise sp.StaticPipelineParseError("bad")

    team._parse_or_invalidate(  # noqa: SLF001
        ws,
        inv_id2,
        _parse,
        "broken",
        block_name="decisions.yaml",
        emit=lambda _e: None,
        on_proc_started=None,
    )
    assert captured == [fresh_turn_prompt]
