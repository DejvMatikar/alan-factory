from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from alan import agent_catalogue_teams_impl, agent_key
from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import to_entry
from alan.claude_cli import ProcessMode, streaming_spec
from alan.claude_cli_grants import WRITE_TOOLS
from alan.engine_config import EngineConfig, RoleConfig
from alan.handover import ArtifactRef, Decision
from alan.launch_spec import appended_prompt
from alan.teams import _base, implementation, implementation_fix
from alan.teams._base import render_allowed_tools
from tests._agent_catalogue_impl_support import (
    BACKSTOP_KEY,
    CONFIG_FACTORIES,
    FIX_KEY,
    IMPLEMENTER_KEY,
    SUBTASK,
    bare_config,
    custom_config,
    default_config,
    ios_config,
    launched_prompt,
    overridden_config,
    placeholder_labels,
    prompt_regex,
    real_handover,
    real_workspace,
    resolve_agents,
    shared_labels,
    user_regex,
    with_role,
)

TEAMS = [(IMPLEMENTER_KEY, "implementation"), (FIX_KEY, "implementation_fix")]
PARITY = [
    (IMPLEMENTER_KEY, implementation.render_system_prompt, 500),
    (FIX_KEY, implementation_fix.render_system_prompt, 300),
]
Render = Callable[[EngineConfig, Path], str]


def test_group_ids_and_agent_keys_cover_the_implementation_family() -> None:
    cfg = default_config()
    groups = {
        group.id: [agent.key.id for agent in group.resolve(cfg)]
        for group in agent_catalogue_teams_impl.GROUPS
    }
    assert groups["team:implementation"] == [IMPLEMENTER_KEY, BACKSTOP_KEY]
    assert groups["team:implementation_fix"] == [FIX_KEY]
    assert groups["team:verification"][-1] == "team_role:verification/judge"


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
@pytest.mark.parametrize("with_guidance", [False, True], ids=["no_claude_md", "claude_md"])
@pytest.mark.parametrize(("key", "render", "turns"), PARITY)
def test_worktree_prompt_template_matches_the_real_render(
    tmp_path: Path, config_name: str, with_guidance: bool, key: str, render: Render, turns: int
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    if with_guidance:
        (tmp_path / "CLAUDE.md").write_text("guidance")
    agent = resolve_agents(cfg)[key]
    captured = launched_prompt(render(cfg, tmp_path), turns)
    assert prompt_regex(agent).fullmatch(captured) is not None


@pytest.mark.parametrize(("key", "render", "turns"), PARITY)
def test_worktree_agent_runs_with_the_team_turn_ceiling_and_fleet_process(
    key: str, render: Render, turns: int
) -> None:
    agent = resolve_agents(default_config())[key]
    entry = to_entry(agent, [])
    assert entry.permission_mode == "acceptEdits"
    assert entry.turn_limit.max_turns == turns
    assert agent.spec.max_turns == turns
    assert entry.scope == "repo"
    assert entry.role == "implementer"
    assert entry.session.behaviour == "resumable"
    assert agent.spec.process.fleet is True
    assert agent.spec.process.priority_delta is not None


@pytest.mark.parametrize(("key", "team"), TEAMS)
def test_worktree_agent_keeps_every_stored_tool_and_denies_no_write_tool(
    key: str, team: str
) -> None:
    cfg = custom_config()
    entry = to_entry(resolve_agents(cfg)[key], [])
    assert entry.team == team
    assert entry.tools.stored == list(render_allowed_tools(cfg, team))
    assert entry.tools.effective == entry.tools.stored
    assert "Edit" in entry.tools.effective
    assert not set(WRITE_TOOLS) & set(entry.tools.denied)


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
def test_worktree_agent_declares_both_values_as_the_engine_implementer_role(key: str) -> None:
    agent = resolve_agents(custom_config())[key]
    entry = to_entry(agent, [])
    assert (entry.model.value, entry.model.source) == ("opus", "engine_role")
    assert (entry.effort.value, entry.effort.source) == ("max", "engine_role")
    assert agent.model_source.engine_role == "implementer"
    assert agent.effort_source.engine_role == "implementer"


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
def test_worktree_agent_falls_back_to_the_builtin_role_when_the_engine_has_none(
    key: str,
) -> None:
    agent = resolve_agents(bare_config())[key]
    assert agent.model_source.kind == "builtin"
    assert agent.effort_source.kind == "builtin"
    assert agent.model_source.engine_role is None
    assert (agent.spec.model, agent.spec.effort) == ("sonnet", "xhigh")


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
def test_worktree_agent_follows_a_mutated_implementer_role(key: str) -> None:
    sentinel = RoleConfig(model="sentinel-model", effort="sentinel-effort")
    spec = resolve_agents(with_role(default_config(), "implementer", sentinel))[key].spec
    assert (spec.model, spec.effort) == ("sentinel-model", "sentinel-effort")


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
@pytest.mark.parametrize(
    ("config_name", "overridden"),
    [("default", False), ("custom", False), ("overridden", True)],
)
def test_worktree_agent_marks_a_prompt_override(
    key: str, config_name: str, overridden: bool
) -> None:
    entry = to_entry(resolve_agents(CONFIG_FACTORIES[config_name]())[key], [])
    text = "".join(segment.text for segment in entry.prompt)
    assert entry.prompt_overridden is overridden
    assert ("OVERRIDDEN START" in text) is overridden


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
def test_overridden_prompt_labels_the_merge_gate_clause_and_leaves_the_guidance_open(
    key: str,
) -> None:
    entry = to_entry(resolve_agents(overridden_config())[key], [])
    assert "merge_gate_clause" in shared_labels(entry.prompt)
    assert "verifier_anti_self_approval" not in shared_labels(entry.prompt)
    assert "REPOSITORY_GUIDANCE" in placeholder_labels(entry.prompt)


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
@pytest.mark.parametrize(
    ("config_name", "label", "expected"),
    [
        ("default", "environment_block", True),
        ("default", "pr_body_block_instructions", True),
        ("default", "native_capture_recipe", False),
        ("default", "REPOSITORY_GUIDANCE", False),
        ("custom", "environment_block", True),
        ("custom", "REPOSITORY_GUIDANCE", True),
        ("ios", "native_capture_recipe", True),
    ],
)
def test_worktree_prompt_labels_shared_blocks_and_run_time_placeholders(
    key: str, config_name: str, label: str, expected: bool
) -> None:
    entry = to_entry(resolve_agents(CONFIG_FACTORIES[config_name]())[key], [])
    labelled = shared_labels(entry.prompt) | set(placeholder_labels(entry.prompt))
    assert (label in labelled) is expected


@pytest.mark.parametrize("key", [IMPLEMENTER_KEY, FIX_KEY])
def test_every_placeholder_in_a_worktree_agent_has_a_description(key: str) -> None:
    entry = to_entry(resolve_agents(ios_config())[key], [])
    for segment in [*entry.prompt, *entry.user_message]:
        if segment.kind == "placeholder":
            assert segment.description
            assert segment.description != segment.label


@pytest.mark.parametrize(
    ("key", "present", "absent"),
    [
        (
            IMPLEMENTER_KEY,
            ["SUBTASK_SECTION", "IMPLEMENTATION_PLAN_SECTIONS", "IMPLEMENTATION_FORBIDDEN_ACTIONS"],
            ["FIX_REVIEW_SECTIONS", "FIX_FORBIDDEN_ACTIONS"],
        ),
        (
            FIX_KEY,
            ["SUBTASK_SECTION", "FIX_REVIEW_SECTIONS", "FIX_UPSTREAM_SECTIONS"],
            ["IMPLEMENTATION_PLAN_SECTIONS", "IMPLEMENTATION_FORBIDDEN_ACTIONS"],
        ),
    ],
)
def test_user_message_template_marks_the_run_time_sections(
    key: str, present: list[str], absent: list[str]
) -> None:
    entry = to_entry(resolve_agents(default_config())[key], [])
    labels = placeholder_labels(entry.user_message)
    assert labels[:3] == ["WORK_ITEM_TITLE", "WORK_ITEM_DESCRIPTION", "SUBTASK_SECTION"]
    assert set(present) <= set(labels)
    assert not set(absent) & set(labels)


@pytest.mark.parametrize(
    ("key", "label", "prohibition"),
    [
        (IMPLEMENTER_KEY, "IMPLEMENTATION_FORBIDDEN_ACTIONS", "do not push branches or open PRs"),
        (
            FIX_KEY,
            "FIX_FORBIDDEN_ACTIONS",
            "do not modify files outside the scope of the review findings",
        ),
    ],
)
def test_forbidden_actions_slot_describes_the_built_in_prohibitions(
    key: str, label: str, prohibition: str
) -> None:
    entry = to_entry(resolve_agents(default_config())[key], [])
    slot = next(segment for segment in entry.user_message if segment.label == label)
    assert prohibition in (slot.description or "")


IMPLEMENTATION_HANDOVERS = {
    "no_handover": None,
    "plan": real_handover(
        plan_synthesis="## Implementation steps\n1. step\n## Other\nignored",
        decisions=[
            Decision(
                id="d1",
                statement="use x",
                rationale="because",
                confidence=0.5,
                reversibility="high",
            )
        ],
        files_to_edit=["a.py"],
        forbidden_actions=["do not touch y"],
        artifacts_produced=[ArtifactRef(path="/artifact", description="notes")],
    ),
    "escalation": real_handover(handover_type="escalation", plan_synthesis=None),
}


@pytest.mark.parametrize("handover_name", list(IMPLEMENTATION_HANDOVERS))
@pytest.mark.parametrize("with_task", [False, True], ids=["whole_task", "subtask"])
@pytest.mark.parametrize("instructions", [None, "also do z"], ids=["plain", "instructed"])
def test_implementation_user_template_matches_the_real_user_text(
    tmp_path: Path, handover_name: str, with_task: bool, instructions: str | None
) -> None:
    cfg = default_config()
    workspace = real_workspace(cfg, tmp_path, with_task=with_task)
    real = implementation._build_user_text(  # pyright: ignore[reportPrivateUsage]
        workspace,
        IMPLEMENTATION_HANDOVERS[handover_name],
        instructions,
        SUBTASK.id if with_task else None,
    )
    assert user_regex(resolve_agents(cfg)[IMPLEMENTER_KEY]).fullmatch(real) is not None


@pytest.mark.parametrize("with_task", [False, True], ids=["whole_task", "subtask"])
@pytest.mark.parametrize("instructions", [None, "also do z"], ids=["plain", "instructed"])
def test_fix_user_template_matches_the_real_user_text(
    tmp_path: Path, with_task: bool, instructions: str | None
) -> None:
    cfg = default_config()
    real = implementation_fix._build_user_text(  # pyright: ignore[reportPrivateUsage]
        real_workspace(cfg, tmp_path, with_task=with_task),
        real_handover(full_synthesis="review text", artifacts_produced=[]),
        instructions,
        SUBTASK.id if with_task else None,
        (),
    )
    assert user_regex(resolve_agents(cfg)[FIX_KEY]).fullmatch(real) is not None


def drive_backstop(cfg: EngineConfig, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(system_prompt: str, user_text: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs, system_prompt=system_prompt, user_text=user_text)
        return SimpleNamespace(result_text="done")

    monkeypatch.setattr(_base, "run_agent_streaming", fake_run)
    graph = MagicMock()
    graph.doc.tasks = [SimpleNamespace(id="implementer", claude_session_id="recorded-session")]
    handle = MagicMock()
    handle.path = Path("/leased/checkout")
    implementation._backstop_resume_attempt(  # pyright: ignore[reportPrivateUsage]
        graph, handle, cfg
    )
    return captured


@pytest.mark.parametrize("config_name", ["default", "custom", "bare", "overridden"])
def test_drift_backstop_spec_equals_the_launch_the_run_path_makes(
    config_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    kwargs = drive_backstop(cfg, monkeypatch)
    system_prompt = kwargs.pop("system_prompt")
    user_text = kwargs.pop("user_text")
    fleet = kwargs.pop("fleet")
    real = streaming_spec(system_prompt, mode=ProcessMode(fleet=fleet), **kwargs)
    agent = resolve_agents(cfg)[BACKSTOP_KEY]
    assert agent.user_message == user_text
    assert appended_prompt(agent.spec) == appended_prompt(real)
    assert replace(real, process=replace(real.process, cwd=None), session_id=None) == replace(
        agent.spec, session_id=None
    )


@pytest.mark.parametrize("config_name", ["default", "custom", "bare"])
def test_drift_backstop_is_a_resumed_accept_edits_launch_with_a_fixed_budget(
    config_name: str,
) -> None:
    agent = resolve_agents(CONFIG_FACTORIES[config_name]())[BACKSTOP_KEY]
    entry = to_entry(agent, [])
    assert agent.spec.agent == agent_key.IMPLEMENTATION_DRIFT_BACKSTOP
    assert agent.spec.session_mode == "resumed"
    assert entry.permission_mode == "acceptEdits"
    assert entry.turn_limit.max_turns == 30
    assert entry.turn_limit.adaptive is False
    assert entry.scope == "repo"
    assert (entry.team, entry.role) == ("implementation", "drift_backstop")
    assert agent.spec.process.detached is False


def test_drift_backstop_declares_builtin_sources_even_when_the_engine_role_differs() -> None:
    agent = resolve_agents(custom_config())[BACKSTOP_KEY]
    entry = to_entry(agent, [])
    assert (entry.model.value, entry.model.source) == ("sonnet", "builtin")
    assert (entry.effort.value, entry.effort.source) == ("xhigh", "builtin")
    assert (agent.configured_model, agent.configured_effort) == ("opus", "max")


def test_drift_backstop_flags_the_ignored_engine_role_and_prompt() -> None:
    cfg = custom_config()
    agent = resolve_agents(cfg)[BACKSTOP_KEY]
    flags = {flag.code: flag for flag in agent_flags(agent, cfg)}
    assert set(flags) == {"model_mismatch", "effort_mismatch", "other_mismatch"}
    assert flags["other_mismatch"].severity == "warn"
    assert "default engine's implementer system prompt" in flags["other_mismatch"].detail


def test_drift_backstop_flag_is_informational_on_the_default_engine() -> None:
    cfg = default_config()
    agent = resolve_agents(cfg)[BACKSTOP_KEY]
    flags = agent_flags(agent, cfg)
    assert [(flag.code, flag.severity) for flag in flags] == [("other_mismatch", "info")]


def test_drift_backstop_has_no_configured_values_when_the_engine_lacks_the_role() -> None:
    cfg = bare_config()
    agent = resolve_agents(cfg)[BACKSTOP_KEY]
    assert (agent.configured_model, agent.configured_effort) == (None, None)
    assert [flag.code for flag in agent_flags(agent, cfg)] == ["other_mismatch"]


def test_drift_backstop_uses_the_engine_tools_and_the_default_prompt() -> None:
    cfg = custom_config()
    agent = resolve_agents(cfg)[BACKSTOP_KEY]
    assert list(agent.spec.allowed_tools) == list(render_allowed_tools(cfg, "implementation"))
    assert (appended_prompt(agent.spec) or "").startswith(
        implementation._SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]
    )
