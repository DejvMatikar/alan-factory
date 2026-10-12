from __future__ import annotations

import re

import pytest

from alan import agent_catalogue_helpers as helpers
from alan import agent_catalogue_teams_orchestrated as orchestrated
from alan import agent_key
from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, to_entry
from alan.agent_catalogue_models import AgentEntry, PromptSegmentOut
from alan.claude_cli import streaming_spec
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig, default_engine_config
from alan.fleet_load import FLEET_NICE_DELTA
from alan.launch_spec import (
    TURN_BUDGET_PREFIX,
    appended_prompt,
    effective_allowed_tools,
    flat_denied_tools,
)
from alan.teams import _native_capture, brainstorming, opportunity_scout, research, ui_review
from alan.teams._base import (
    MAX_TURNS,
    UNTRUSTED_CONTENT_NOTICE,
    compose_system_prompt,
    render_allowed_tools,
    render_base_tools,
)
from alan.teams._orchestrated import OrchestratedTeam, assemble_worker_prompt

_TEAMS: dict[str, OrchestratedTeam] = {
    team.name: team
    for team in (research.TEAM, brainstorming.TEAM, opportunity_scout.TEAM, ui_review.TEAM)
}
_FIXED_ROLES = ("orchestrator", "worker", "verifier", "synthesizer")
_TEAMS_WITHOUT_VERIFIER = ("brainstorming",)
_SOURCE_FETCH_TEAMS = ("research", "brainstorming", "opportunity_scout")
_ENGINE_ROLE_OF = {
    "orchestrator": "judge",
    "worker": "diff_reviewer",
    "verifier": "diff_reviewer",
    "synthesizer": "judge",
}
_MODEL_FIELD_OF = {
    "orchestrator": "orchestrator_model",
    "worker": "worker_model",
    "verifier": "worker_model",
    "synthesizer": "synthesizer_model",
}
_DEFAULT_CFG = default_engine_config()


def _resolve(cfg: EngineConfig) -> dict[str, ResolvedAgent]:
    return {agent.key.id: agent for group in orchestrated.GROUPS for agent in group.resolve(cfg)}


_AGENTS = _resolve(_DEFAULT_CFG)
_AGENT_IDS = sorted(_AGENTS)


def _split(agent_id: str) -> tuple[OrchestratedTeam, str]:
    team_name, role = agent_id.removeprefix("team_role:").split("/")
    return _TEAMS[team_name], role


def _custom_cfg() -> EngineConfig:
    return _DEFAULT_CFG.model_copy(
        update={
            "roles": {
                "judge": RoleConfig(model="haiku", effort="low"),
                "diff_reviewer": RoleConfig(model="opus", effort="high"),
            },
            "base_tools": ["Read", "Bash(uv run:*)"],
            "team_tools": {"research": ["WebSearch"]},
            "gate_commands": [["uv", "run", "pytest", "-q"]],
        }
    )


def _foreign_cfg() -> EngineConfig:
    return _DEFAULT_CFG.model_copy(update={"repo": RepoIdentity(owner="acme", repo="widgets")})


def _template_regex(entry: AgentEntry) -> re.Pattern[str]:
    parts = [re.escape(s.text) if s.kind == "text" else ".*" for s in entry.prompt]
    return re.compile("".join(parts), re.DOTALL)


def _rendered(segments: list[PromptSegmentOut]) -> str:
    return "".join(s.text if s.kind == "text" else f"<{s.label}>" for s in segments)


def _real_system_prompt(team: OrchestratedTeam, role: str, cfg: EngineConfig) -> str:
    if role == "orchestrator":
        return team.orchestrator_prompt
    if role == "worker":
        native = _native_capture.native_capture_block(cfg, team.name)
        return assemble_worker_prompt(team.worker_system_template, "comparator", extra_block=native)
    prompt = team.verifier_prompt if role == "verifier" else team.synthesizer_prompt
    return compose_system_prompt(prompt or "", dynamic_block=UNTRUSTED_CONTENT_NOTICE)


def _real_appended(system_prompt: str, model: str) -> str:
    spec = streaming_spec(f"{system_prompt}{TURN_BUDGET_PREFIX}{MAX_TURNS}", model=model)
    return appended_prompt(spec) or ""


def _shared_labels(entry: AgentEntry) -> set[str]:
    return {s.label for s in entry.prompt if s.shared}


def _entry(agent: ResolvedAgent, cfg: EngineConfig = _DEFAULT_CFG) -> AgentEntry:
    return to_entry(agent, agent_flags(agent, cfg))


def test_the_module_covers_exactly_the_orchestrated_teams() -> None:
    teams = {group.key.name.split("/")[0] for group in orchestrated.GROUPS}
    assert teams == agent_key.ORCHESTRATED_TEAMS == set(_TEAMS)


def test_the_fixed_roles_are_the_reserved_orchestrator_roles_plus_the_worker() -> None:
    assert set(_FIXED_ROLES) == agent_key.ORCHESTRATED_FIXED_ROLES | {agent_key.WORKER_ROLE}


@pytest.mark.parametrize("team_name", sorted(_TEAMS))
def test_each_team_gets_one_group_per_fixed_role_with_a_verifier_only_when_it_has_one(
    team_name: str,
) -> None:
    expected = {
        f"team_role:{team_name}/{role}"
        for role in _FIXED_ROLES
        if role != "verifier" or team_name not in _TEAMS_WITHOUT_VERIFIER
    }
    ids = [g.id for g in orchestrated.GROUPS if g.id.startswith(f"team_role:{team_name}/")]
    assert len(ids) == len(set(ids))
    assert set(ids) == expected
    assert (_TEAMS[team_name].verifier_prompt is None) == (team_name in _TEAMS_WITHOUT_VERIFIER)


@pytest.mark.parametrize("group", orchestrated.GROUPS, ids=lambda group: group.id)
def test_a_group_resolves_exactly_one_agent_under_its_own_key(group: ResolverGroup) -> None:
    agents = group.resolve(_DEFAULT_CFG)
    assert len(agents) == 1
    assert agents[0].key == group.key
    assert group.id == group.key.id
    assert group.key.kind == "team_role"


@pytest.mark.parametrize("team_name", sorted(_TEAMS))
def test_the_worker_key_is_the_canonical_worker_whatever_role_the_orchestrator_picks(
    team_name: str,
) -> None:
    agent = _AGENTS[f"team_role:{team_name}/worker"]
    assert agent.key == agent_key.team_role(team_name, "comparator")
    assert agent.key == agent_key.team_role(team_name, "any_other_role")
    assert agent.name == f"{team_name} / worker"
    assert (agent.team, agent.role) == (team_name, "worker")


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_models_are_the_team_fields_declared_as_builtin_copies_of_an_engine_role(
    agent_id: str,
) -> None:
    team, role = _split(agent_id)
    agent = _AGENTS[agent_id]
    engine_role = _ENGINE_ROLE_OF[role]
    field = _MODEL_FIELD_OF[role]
    assert agent.spec.model == getattr(team, field)
    assert agent.spec.model == _DEFAULT_CFG.roles[engine_role].model
    assert agent.model_source.kind == "builtin"
    assert agent.model_source.engine_role is None
    assert field in (agent.model_source.detail or "")
    assert agent.engine_role_hint == engine_role


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_effort_is_derived_from_the_model_and_declared_builtin(agent_id: str) -> None:
    agent = _AGENTS[agent_id]
    assert agent.spec.effort == {"opus": "xhigh", "sonnet": "high"}[agent.spec.model or ""]
    assert agent.effort_source.kind == "builtin"
    assert agent.effort_source.engine_role is None
    assert agent.configured_model is None
    assert agent.configured_effort is None


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_changing_engine_roles_never_changes_the_launch(agent_id: str) -> None:
    mutated = _resolve(_custom_cfg())[agent_id]
    original = _AGENTS[agent_id]
    assert mutated.spec.model == original.spec.model
    assert mutated.spec.effort == original.spec.effort


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_a_default_engine_flags_the_unread_role_as_info_and_no_effort_flag(
    agent_id: str,
) -> None:
    flags = agent_flags(_AGENTS[agent_id], _DEFAULT_CFG)
    assert [(f.code, f.severity) for f in flags] == [("ignores_engine_roles", "info")]


def test_a_customised_engine_role_raises_warnings_for_a_model_or_effort_that_differs() -> None:
    cfg = _custom_cfg()
    resolved = _resolve(cfg)
    by_role = {
        role: {
            f.code: f.severity
            for f in agent_flags(resolved[f"team_role:research/{role}"], cfg)
            if f.code != "other_mismatch"
        }
        for role in _FIXED_ROLES
    }
    assert by_role["orchestrator"] == {
        "ignores_engine_roles": "warn",
        "ignores_engine_effort": "warn",
    }
    assert by_role["synthesizer"] == by_role["orchestrator"]
    assert by_role["worker"] == {"ignores_engine_roles": "warn", "ignores_engine_effort": "info"}
    assert by_role["verifier"] == by_role["worker"]


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_the_launch_fields_match_the_fleet_node_defaults(agent_id: str) -> None:
    agent = _AGENTS[agent_id]
    spec = agent.spec
    assert spec.layout == "agent"
    assert spec.permission_mode == "auto"
    assert spec.max_turns == MAX_TURNS
    assert agent.turn_limit.max_turns == MAX_TURNS
    assert spec.add_dirs == ()
    assert spec.session_mode == "new"
    assert agent.session.behaviour == "resumable"
    assert spec.process.fleet is True
    assert spec.process.priority_delta == FLEET_NICE_DELTA
    assert spec.process.cwd is None
    assert agent.cwd_is_runtime is True
    assert agent.working_directory == orchestrated.WORKING_DIRECTORY
    assert {"Write", "Edit", "NotebookEdit"} <= set(flat_denied_tools(spec.denied))
    assert agent.scope == "repo"


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_no_orchestrated_entry_carries_a_tag(agent_id: str) -> None:
    assert _AGENTS[agent_id].tags == ()
    assert _entry(_AGENTS[agent_id]).tags == []


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_stored_and_effective_tools_follow_the_default_engine_grants(agent_id: str) -> None:
    team, role = _split(agent_id)
    agent = _AGENTS[agent_id]
    stored = (
        render_allowed_tools(_DEFAULT_CFG, team.name)
        if role == "worker"
        else render_base_tools(_DEFAULT_CFG)
    )
    granted = with_gate_reachability(stored, _DEFAULT_CFG.gate_commands)
    assert agent.stored_tools == stored
    assert agent.spec.allowed_tools == effective_allowed_tools(granted, "auto")
    assert "Bash(uv run:*)" in agent.stored_tools
    assert "Bash(uv run:*)" not in agent.spec.allowed_tools


@pytest.mark.parametrize(
    ("team_name", "web_tools"),
    [
        ("research", True),
        ("brainstorming", True),
        ("opportunity_scout", True),
        ("ui_review", False),
    ],
)
def test_only_the_web_research_teams_grant_web_tools_to_workers_and_nobody_else(
    team_name: str, web_tools: bool
) -> None:
    for role in _FIXED_ROLES:
        agent_id = f"team_role:{team_name}/{role}"
        if agent_id not in _AGENTS:
            continue
        has_web = "WebSearch" in _AGENTS[agent_id].spec.allowed_tools
        assert has_web == (web_tools and role == "worker")


def test_a_customised_engine_changes_stored_and_effective_tools() -> None:
    cfg = _custom_cfg()
    agents = _resolve(cfg)
    orchestrator = agents["team_role:research/orchestrator"]
    worker = agents["team_role:research/worker"]
    brainstormer = agents["team_role:brainstorming/worker"]
    assert orchestrator.stored_tools == ("Read", "Bash(uv run:*)")
    assert worker.stored_tools == ("Read", "Bash(uv run:*)", "WebSearch")
    assert brainstormer.stored_tools == ("Read", "Bash(uv run:*)")
    assert orchestrator.spec.allowed_tools[0] == "Read"
    assert "Bash(uv run pytest:*)" in orchestrator.spec.allowed_tools
    assert "Bash(uv run:*)" not in orchestrator.spec.allowed_tools
    assert "WebSearch" in worker.spec.allowed_tools
    assert "WebSearch" not in brainstormer.spec.allowed_tools
    assert orchestrator.extra_flags == ()
    assert [f.code for f in worker.extra_flags] == ["other_mismatch"]


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_the_prompt_matches_what_the_real_builders_produce(agent_id: str) -> None:
    team, role = _split(agent_id)
    agent = _AGENTS[agent_id]
    real = _real_appended(_real_system_prompt(team, role, _DEFAULT_CFG), agent.spec.model or "")
    assert _template_regex(_entry(agent)).fullmatch(real)
    assert not _template_regex(_entry(agent)).fullmatch(f"x{real}")


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_the_prompt_ends_with_the_turn_budget_headless_block_and_project_doc_placeholders(
    agent_id: str,
) -> None:
    prompt = _entry(_AGENTS[agent_id]).prompt
    placeholders = [s.label for s in prompt if s.kind == "placeholder"]
    assert placeholders[-2:] == ["TURN_BUDGET", "PROJECT_DOC_BLOCK"]
    assert [s.label for s in prompt if s.shared][-2:] == ["headless_execution", "PROJECT_DOC_BLOCK"]


_ALWAYS_ABSENT = {"terseness_block", "confidence_reporting_block", "four_field_requirement"}
_SHARED_BLOCKS_BY_ROLE: dict[str, tuple[set[str], set[str]]] = {
    "orchestrator": (
        {"four_field_requirement"},
        {"terseness_block", "untrusted_content_notice", "confidence_reporting_block"},
    ),
    "worker": (
        {"terseness_block", "untrusted_content_notice", "confidence_reporting_block"},
        {"four_field_requirement"},
    ),
    "verifier": ({"untrusted_content_notice"}, _ALWAYS_ABSENT),
    "synthesizer": ({"untrusted_content_notice"}, _ALWAYS_ABSENT),
}


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_shared_blocks_are_labelled_where_the_run_path_appends_them(agent_id: str) -> None:
    present, absent = _SHARED_BLOCKS_BY_ROLE[_split(agent_id)[1]]
    labels = _shared_labels(_entry(_AGENTS[agent_id]))
    assert present <= labels
    assert not absent & labels


@pytest.mark.parametrize(
    ("agent_id", "labels"),
    [
        ("team_role:research/orchestrator", {"coverage_union_rule"}),
        (
            "team_role:opportunity_scout/orchestrator",
            {"coverage_union_rule", "four_filter_rules", "ui_debt_lens"},
        ),
        ("team_role:opportunity_scout/worker", {"four_filter_rules", "ui_debt_lens"}),
        ("team_role:opportunity_scout/verifier", {"four_filter_rules"}),
        ("team_role:opportunity_scout/synthesizer", {"four_filter_rules"}),
        ("team_role:brainstorming/worker", {"planning_ux_lens", "planning_screen_capture"}),
        ("team_role:ui_review/orchestrator", {"ui_review_scope_statement"}),
        ("team_role:ui_review/worker", {"ui_review_scope_statement", "planning_screen_capture"}),
    ],
)
def test_text_repeated_across_a_teams_prompts_is_labelled_shared(
    agent_id: str, labels: set[str]
) -> None:
    assert labels <= _shared_labels(_entry(_AGENTS[agent_id]))


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_every_placeholder_in_the_prompt_and_user_message_is_described(agent_id: str) -> None:
    entry = _entry(_AGENTS[agent_id])
    placeholders = [s for s in [*entry.prompt, *entry.user_message] if s.kind == "placeholder"]
    assert placeholders
    assert all(s.description for s in placeholders)
    assert all(s.text == "" for s in placeholders)


def test_the_worker_role_is_a_labelled_placeholder_inside_the_worker_template() -> None:
    entry = _entry(_AGENTS["team_role:research/worker"])
    role_slot = next(s for s in entry.prompt if s.label == "WORKER_ROLE_NAME")
    assert role_slot.kind == "placeholder"
    assert role_slot.description == orchestrated.WORKER_ROLE_NAME.description
    assert entry.prompt[0].text.endswith("research agent with role '")
    assert entry.prompt[2].text.startswith("'. Investigate")
    assert "chosen by the orchestrator at run time" in entry.when_runs


@pytest.mark.parametrize(
    ("agent_id", "expected"),
    [
        (
            "team_role:research/orchestrator",
            "Title: <TASK_TITLE>\n\nDescription:\n<TASK_DESCRIPTION><OPTIONAL_DISPATCH_CONTEXT>",
        ),
        ("team_role:research/worker", "<DISPATCHED_PROMPT>"),
        (
            "team_role:research/verifier",
            "Title: <TASK_TITLE>\n\nDescription:\n<TASK_DESCRIPTION>"
            "\n\n## <WORKER_ROLE_NAME>\n\n<WORKER_OUTPUT>",
        ),
        (
            "team_role:research/synthesizer",
            "Title: <TASK_TITLE>\n\nDescription:\n<TASK_DESCRIPTION>"
            "\n\n## <WORKER_ROLE_NAME>\n\n<WORKER_OUTPUT>\n\n## verifier\n\n<VERIFIER_OUTPUT>",
        ),
        (
            "team_role:brainstorming/synthesizer",
            "Title: <TASK_TITLE>\n\nDescription:\n<TASK_DESCRIPTION>"
            "\n\n## <WORKER_ROLE_NAME>\n\n<WORKER_OUTPUT>",
        ),
    ],
)
def test_user_messages_have_the_shape_of_the_run_paths_builders(
    agent_id: str, expected: str
) -> None:
    assert _rendered(_entry(_AGENTS[agent_id]).user_message) == expected


@pytest.mark.parametrize(
    ("slot", "description"),
    [
        (orchestrated.TASK_TITLE, "Title of the workspace task the user entered"),
        (orchestrated.TASK_DESCRIPTION, "Description of the workspace task the user entered"),
        (
            orchestrated.WORKER_ROLE_NAME,
            "Role name the orchestrator chose for this worker, a snake_case identifier",
        ),
    ],
)
def test_slots_register_their_descriptions_and_resolving_twice_is_idempotent(
    slot: orchestrated.RuntimeSlot, description: str
) -> None:
    assert _resolve(_DEFAULT_CFG).keys() == _AGENTS.keys()
    assert helpers.slot_description(slot.name) == description
    assert slot.text() == f"⟦slot:{slot.name}⟧"


def test_the_native_capture_statement_is_a_shared_block_for_the_ios_ui_review_worker() -> None:
    cfg = _DEFAULT_CFG.model_copy(update={"preset": "ios"})
    agents = _resolve(cfg)
    ui_worker = _entry(agents["team_role:ui_review/worker"], cfg)
    research_worker = _entry(agents["team_role:research/worker"], cfg)
    ui_text = "".join(s.text for s in ui_worker.prompt)
    assert _native_capture.NATIVE_CAPTURE_UNAVAILABLE in ui_text
    assert "native_capture_block" in _shared_labels(ui_worker)
    assert "native_capture_block" not in _shared_labels(research_worker)
    assert _native_capture.NATIVE_CAPTURE_UNAVAILABLE not in "".join(
        s.text for s in research_worker.prompt
    )


def test_a_non_ios_engine_adds_no_native_capture_block() -> None:
    labels = _shared_labels(_entry(_AGENTS["team_role:ui_review/worker"]))
    assert "native_capture_block" not in labels


def test_the_ios_prompt_still_matches_the_real_builders() -> None:
    cfg = _DEFAULT_CFG.model_copy(update={"preset": "ios"})
    agent = _resolve(cfg)["team_role:ui_review/worker"]
    team = _TEAMS["ui_review"]
    real = _real_appended(_real_system_prompt(team, "worker", cfg), team.worker_model)
    assert _template_regex(_entry(agent, cfg)).fullmatch(real)


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_a_default_engine_adds_no_extra_flags(agent_id: str) -> None:
    assert _AGENTS[agent_id].extra_flags == ()


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_an_engine_for_another_repository_flags_the_shared_checkout(agent_id: str) -> None:
    agent = _resolve(_foreign_cfg())[agent_id]
    flags = [f for f in agent.extra_flags if f.code == "other_mismatch"]
    assert [f.severity for f in flags] == ["warn"]
    assert "acme/widgets" in flags[0].detail
    assert "alan's own checkout" in flags[0].title


@pytest.mark.parametrize("team_name", sorted(_TEAMS))
def test_a_worker_told_to_fetch_sources_without_the_grant_is_flagged(team_name: str) -> None:
    cfg = _DEFAULT_CFG.model_copy(update={"base_tools": ["Read"], "team_tools": {}})
    worker = _resolve(cfg)[f"team_role:{team_name}/worker"]
    expected = 1 if team_name in _SOURCE_FETCH_TEAMS else 0
    flags = [f for f in worker.extra_flags if "alan.source_fetch" in f.detail]
    assert len(flags) == expected
    assert all(f.code == "other_mismatch" and f.severity == "warn" for f in flags)


@pytest.mark.parametrize(
    "granted",
    [["Bash(python3 -m alan.source_fetch:*)"], ["Bash(python3:*)"]],
)
def test_a_worker_with_any_python_grant_for_the_source_fetch_command_is_not_flagged(
    granted: list[str],
) -> None:
    cfg = _DEFAULT_CFG.model_copy(
        update={"base_tools": ["Read"], "team_tools": {"research": granted}}
    )
    assert _resolve(cfg)["team_role:research/worker"].extra_flags == ()


@pytest.mark.parametrize("agent_id", _AGENT_IDS)
def test_to_entry_projects_the_entry_for_every_orchestrated_agent(agent_id: str) -> None:
    agent = _AGENTS[agent_id]
    entry = _entry(agent)
    assert entry.id == agent_id
    assert entry.kind == "team_role"
    assert entry.scope == "repo"
    assert (entry.team, entry.role) == (agent.team, agent.role)
    assert entry.when_runs == agent.when_runs
    assert entry.model.source == "builtin"
    assert entry.effort.source == "builtin"
    assert entry.model.display == agent.spec.model
    assert entry.permission_mode == "auto"
    assert entry.tools.stored == list(agent.stored_tools)
    assert entry.timeouts.idle_seconds == agent.spec.process.idle_timeout_seconds
    assert entry.process.priority_delta == FLEET_NICE_DELTA
    assert "⟦slot:" not in entry.model_dump_json()
    assert entry.prompt_overridden is False
