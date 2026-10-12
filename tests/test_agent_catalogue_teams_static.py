from __future__ import annotations

import pytest

from alan import agent_catalogue_teams
from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import ResolvedAgent, ValueSource, to_entry
from alan.agent_catalogue_models import AgentEntry, PromptSegmentOut
from alan.agent_catalogue_node import SESSION_PLACEHOLDER
from alan.agent_catalogue_teams_static_common import (
    EFFORT_DETAIL,
    MODEL_DETAIL,
    WORKING_DIRECTORY,
    template_workspace,
)
from alan.claude_cli_grants import GENERAL_EXEC_GRANTS, PROMPT_INSTRUCTED_GRANTS, WRITE_TOOLS
from alan.engine_config import EngineConfig, RoleConfig, default_engine_config
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS, FLEET_NICE_DELTA
from alan.headless_prompt import HEADLESS_EXECUTION_BLOCK
from alan.launch_spec import TURN_BUDGET_PREFIX
from alan.teams import (
    architecture,
    brief_refine,
    decomposer,
    implementation_plan,
    maintenance,
    scenario_authoring,
)
from alan.teams._base import MAX_TURNS, render_allowed_tools
from alan.teams._static_pipeline import StaticPipelineTeam, assemble_role_system_prompt

TEAMS: dict[str, StaticPipelineTeam] = {
    "architecture": architecture.TEAM,
    "brief_refine": brief_refine.TEAM,
    "decomposer": decomposer.TEAM,
    "implementation_plan": implementation_plan.TEAM,
    "maintenance": maintenance.TEAM,
}

ROLE_NAMES: dict[str, list[str]] = {
    "architecture": [
        "structure_mapper",
        "complexity_analyst",
        "debt_auditor",
        "change_planner",
        "devil_advocate",
        "judge",
    ],
    "brief_refine": ["brief_refiner"],
    "decomposer": ["decomposer"],
    "implementation_plan": [
        "architect",
        "backend_analyst",
        "frontend_analyst",
        "qa_planner",
        "devil_advocate",
        "synthesizer",
    ],
    "maintenance": [
        "code_health_analyst",
        "ci_dependency_auditor",
        "log_repo_scanner",
        "security_scanner",
    ],
}

OPUS_ROLES = {
    "architecture/judge",
    "brief_refine/brief_refiner",
    "decomposer/decomposer",
    "implementation_plan/architect",
    "implementation_plan/synthesizer",
    "maintenance/security_scanner",
}

ALL_KEYS = [f"{team}/{role}" for team, roles in ROLE_NAMES.items() for role in roles]

IMPLEMENTATION_PLAN_PRIOR: dict[str, list[str]] = {
    "architect": [],
    "backend_analyst": ["architect"],
    "frontend_analyst": ["architect", "backend_analyst"],
    "qa_planner": ["architect", "backend_analyst"],
    "devil_advocate": ["architect", "backend_analyst", "frontend_analyst", "qa_planner"],
    "synthesizer": [
        "architect",
        "backend_analyst",
        "frontend_analyst",
        "qa_planner",
        "devil_advocate",
    ],
}

CUSTOM_GATES = [["make", "check"]]


def custom_config() -> EngineConfig:
    return default_engine_config().model_copy(
        update={
            "roles": {
                "judge": RoleConfig(model="haiku", effort="low"),
                "diff_reviewer": RoleConfig(model="haiku", effort="low"),
            },
            "base_tools": ["Read", "Grep"],
            "team_tools": {"maintenance": ["Bash(ps:*)"]},
            "gate_commands": CUSTOM_GATES,
        }
    )


@pytest.fixture(params=["default", "custom"])
def cfg(request: pytest.FixtureRequest) -> EngineConfig:
    return default_engine_config() if request.param == "default" else custom_config()


def resolve_all(cfg: EngineConfig) -> dict[str, ResolvedAgent]:
    return {
        agent.key.name: agent
        for group in agent_catalogue_teams.GROUPS
        for agent in group.resolve(cfg)
    }


def entry_of(agent: ResolvedAgent, cfg: EngineConfig) -> AgentEntry:
    return to_entry(agent, agent_flags(agent, cfg))


def entry_of_key(key: str, cfg: EngineConfig) -> AgentEntry:
    return entry_of(resolve_all(cfg)[key], cfg)


def render(segments: list[PromptSegmentOut]) -> str:
    return "".join(s.text if s.kind == "text" else f"<{s.label}>" for s in segments)


def prior_roles(key: str) -> list[str]:
    team_name, role_name = key.split("/")
    if team_name == "implementation_plan":
        return IMPLEMENTATION_PLAN_PRIOR[role_name]
    names = ROLE_NAMES[team_name]
    return names[: names.index(role_name)]


def role_prompt(key: str, cfg: EngineConfig) -> str:
    team_name, role_name = key.split("/")
    if key == "implementation_plan/synthesizer":
        return scenario_authoring.synthesizer_prompt(cfg)
    return next(r.system_prompt for r in TEAMS[team_name].roles if r.name == role_name)


def expected_prompt(key: str, cfg: EngineConfig) -> str:
    system = assemble_role_system_prompt(role_prompt(key, cfg), terse=True)
    return (
        f"{system}{TURN_BUDGET_PREFIX}<TURN_BUDGET>\n\n{HEADLESS_EXECUTION_BLOCK}"
        "<PROJECT_DOC_BLOCK>"
    )


def labels(segments: list[PromptSegmentOut], kind: str = "placeholder") -> list[str]:
    return [s.label for s in segments if s.kind == kind]


def test_groups_cover_exactly_the_static_teams():
    assert [group.id for group in agent_catalogue_teams.GROUPS] == [
        f"static_pipeline:{team}" for team in ROLE_NAMES
    ]
    assert [group.key.id for group in agent_catalogue_teams.GROUPS] == [
        f"team_role:{team}/{roles[0]}" for team, roles in ROLE_NAMES.items()
    ]
    assert [group.name for group in agent_catalogue_teams.GROUPS] == [
        f"{team} team" for team in ROLE_NAMES
    ]


@pytest.mark.parametrize("team", list(ROLE_NAMES))
def test_roles_follow_the_team_role_table(team, cfg):
    agents = [
        agent
        for group in agent_catalogue_teams.GROUPS
        for agent in group.resolve(cfg)
        if agent.team == team
    ]
    assert [agent.role for agent in agents] == ROLE_NAMES[team]
    assert [role.name for role in TEAMS[team].roles] == ROLE_NAMES[team]
    assert [agent.key.id for agent in agents] == [
        f"team_role:{team}/{role}" for role in ROLE_NAMES[team]
    ]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_model_and_effort_are_built_in_and_ignore_engine_roles(key, cfg):
    agent = resolve_all(cfg)[key]
    expected_model, expected_effort = ("opus", "xhigh") if key in OPUS_ROLES else ("sonnet", "high")
    assert agent.spec.model == expected_model
    assert agent.spec.effort == expected_effort
    assert agent.model_source == ValueSource("builtin", MODEL_DETAIL)
    assert agent.effort_source == ValueSource("builtin", EFFORT_DETAIL)
    assert agent.configured_model is None
    assert agent.configured_effort is None


@pytest.mark.parametrize("key", ALL_KEYS)
def test_entry_shows_model_and_effort_with_builtin_source(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    expected_model, expected_effort = ("opus", "xhigh") if key in OPUS_ROLES else ("sonnet", "high")
    assert (entry.model.display, entry.model.source) == (expected_model, "builtin")
    assert (entry.effort.display, entry.effort.source) == (expected_effort, "builtin")
    assert entry.model.source_detail == MODEL_DETAIL
    assert entry.effort.source_detail == EFFORT_DETAIL
    assert entry.kind == "team_role"
    assert entry.scope == "repo"
    assert entry.tags == []
    assert entry.error is None


def test_launch_does_not_change_when_engine_roles_change():
    default_agents = resolve_all(default_engine_config())
    custom_agents = resolve_all(custom_config())
    for key in ALL_KEYS:
        assert default_agents[key].spec.model == custom_agents[key].spec.model
        assert default_agents[key].spec.effort == custom_agents[key].spec.effort


@pytest.mark.parametrize("key", ALL_KEYS)
def test_prompt_round_trips_to_the_real_composition(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    assert render(entry.prompt) == expected_prompt(key, cfg)


@pytest.mark.parametrize("key", ALL_KEYS)
def test_prompt_labels_the_shared_blocks_and_the_run_time_values(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    shared = {s.label: s.shared for s in entry.prompt if s.kind == "text" and s.shared}
    assert {"terseness_block", "untrusted_content_notice", "headless_execution"} <= set(shared)
    assert labels(entry.prompt) == ["TURN_BUDGET", "PROJECT_DOC_BLOCK"]
    assert all(s.description for s in entry.prompt if s.kind == "placeholder")


@pytest.mark.parametrize(
    "key", ["architecture/judge", "implementation_plan/frontend_analyst"]
)
def test_planning_blocks_are_labelled_shared(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    shared = {s.label for s in entry.prompt if s.kind == "text" and s.shared}
    assert {"planning_ux_lens", "planning_screen_capture"} <= shared


@pytest.mark.parametrize("key", [k for k in ALL_KEYS if k.startswith("maintenance/")])
def test_maintenance_blocks_are_labelled_shared(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    shared = {s.label for s in entry.prompt if s.kind == "text" and s.shared}
    assert {
        "maintenance_report_contract",
        "maintenance_guardrail",
        "maintenance_live_state_access",
    } <= shared


@pytest.mark.parametrize("key", [k for k in ALL_KEYS if not k.startswith("maintenance/")])
def test_maintenance_blocks_appear_only_for_maintenance(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    assert not {s.label for s in entry.prompt if s.label.startswith("maintenance_")}


def test_synthesizer_prompt_follows_the_engine_config():
    key = "implementation_plan/synthesizer"
    default_text = render(entry_of_key(key, default_engine_config()).prompt)
    custom_text = render(entry_of_key(key, custom_config()).prompt)
    assert default_text != custom_text
    assert "make check" in custom_text
    assert "make check" not in default_text


@pytest.mark.parametrize("key", ALL_KEYS)
def test_user_message_carries_the_coverage_block_and_prior_outputs(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    team_name, role_name = key.split("/")
    prior = prior_roles(key)
    expected = [f"{team_name}_{name}_OUTPUT".upper() for name in prior]
    output_labels = [label for label in labels(entry.user_message) if label.endswith("_OUTPUT")]
    assert output_labels == expected
    text = render(entry.user_message)
    single = len(TEAMS[team_name].roles) == 1
    coverage = f"You are the only role in this pipeline ({role_name})." if single else (
        f"Your scope is only {role_name}"
    )
    assert "## COVERAGE" in text
    assert coverage in text
    assert ("## CONTEXT — prior agent outputs in this pipeline:" in text) == bool(prior)
    assert all(s.label == "user_message" for s in entry.user_message if s.kind == "text")
    assert all(s.description for s in entry.user_message if s.kind == "placeholder")


DEFAULT_USER_HEAD = (
    "Title: <PIPELINE_TASK_TITLE>\n\nDescription:\n<PIPELINE_TASK_DESCRIPTION>"
    "<PIPELINE_DISPATCH_INSTRUCTIONS><PIPELINE_PRIOR_HANDOVER>"
)


@pytest.mark.parametrize(
    "key",
    [
        k
        for k in ALL_KEYS
        if k not in {"brief_refine/brief_refiner", "maintenance/security_scanner"}
    ],
)
def test_user_message_starts_with_the_task_and_run_time_context(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    assert render(entry.user_message).startswith(DEFAULT_USER_HEAD)


def test_brief_refine_user_message_uses_its_own_brief_layout(cfg):
    entry = entry_of_key("brief_refine/brief_refiner", cfg)
    assert render(entry.user_message).startswith(
        "## Brief\n\nTitle: <PIPELINE_TASK_TITLE>\n\nDescription:\n<PIPELINE_TASK_DESCRIPTION>"
        "<BRIEF_REFINE_PRIOR_REQUIREMENTS><BRIEF_REFINE_SOURCE_CONVERSATION>"
    )
    assert "PIPELINE_PRIOR_HANDOVER" not in labels(entry.user_message)


def test_decomposer_user_message_lists_the_plan_context(cfg):
    entry = entry_of_key("decomposer/decomposer", cfg)
    assert render(entry.user_message).startswith(f"{DEFAULT_USER_HEAD}<DECOMPOSER_PLAN_CONTEXT>")


def test_security_scanner_user_message_leads_with_the_host_read_permissions(cfg):
    entry = entry_of_key("maintenance/security_scanner", cfg)
    rendered = render(entry.user_message)
    assert rendered.startswith("<MAINTENANCE_SECRET_PERMISSIONS>Title: ")
    described = {s.label: s.description for s in entry.user_message if s.kind == "placeholder"}
    secret_description = described["MAINTENANCE_SECRET_PERMISSIONS"] or ""
    assert "HOST-READ SECRET FILE PERMISSIONS" in secret_description


@pytest.mark.parametrize("key", [k for k in ALL_KEYS if k != "maintenance/security_scanner"])
def test_only_the_security_scanner_has_a_host_read_prefix(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    assert "MAINTENANCE_SECRET_PERMISSIONS" not in labels(entry.user_message)


@pytest.mark.parametrize("key", ALL_KEYS)
def test_tools_show_stored_and_effective_lists(key, cfg):
    agent = resolve_all(cfg)[key]
    entry = entry_of(agent, cfg)
    team_name = key.split("/")[0]
    stored = list(render_allowed_tools(cfg, team_name))
    assert entry.tools.stored == stored
    assert entry.tools.effective == list(agent.spec.allowed_tools)
    assert not set(GENERAL_EXEC_GRANTS) & set(entry.tools.effective)
    assert set(PROMPT_INSTRUCTED_GRANTS) <= set(entry.tools.effective)
    assert set(entry.tools.stored) - set(GENERAL_EXEC_GRANTS) <= set(entry.tools.effective)
    assert set(WRITE_TOOLS) <= set(entry.tools.denied)
    assert entry.permission_mode == "auto"


def test_default_tool_list_stores_the_general_exec_grants_the_launch_drops():
    entry = entry_of_key("architecture/judge", default_engine_config())
    assert set(GENERAL_EXEC_GRANTS) <= set(entry.tools.stored)
    assert not set(GENERAL_EXEC_GRANTS) & set(entry.tools.effective)


def test_gate_commands_of_the_engine_reach_the_effective_tools():
    entry = entry_of_key("architecture/judge", custom_config())
    assert "Bash(make check:*)" in entry.tools.effective
    assert entry.tools.stored == ["Read", "Grep"]


@pytest.mark.parametrize("key", ALL_KEYS)
def test_turn_limit_is_the_static_ceiling(key, cfg, monkeypatch):
    monkeypatch.delenv("ALAN_ADAPTIVE_TURNS", raising=False)
    entry = entry_of(resolve_all(cfg)[key], cfg)
    assert entry.turn_limit.max_turns == MAX_TURNS
    assert entry.turn_limit.adaptive is False
    assert entry.turn_limit.note


def test_turn_limit_reports_adaptive_turns_when_enabled(monkeypatch):
    monkeypatch.setenv("ALAN_ADAPTIVE_TURNS", "1")
    entry = entry_of_key("architecture/judge", default_engine_config())
    assert entry.turn_limit.adaptive is True


@pytest.mark.parametrize("key", ALL_KEYS)
def test_session_working_directory_timeouts_and_process(key, cfg):
    agent = resolve_all(cfg)[key]
    entry = entry_of(agent, cfg)
    assert entry.session.behaviour == "resumable"
    assert entry.working_directory == WORKING_DIRECTORY
    assert agent.cwd_is_runtime is True
    assert agent.spec.session_id == SESSION_PLACEHOLDER
    assert entry.timeouts.idle_seconds == FLEET_IDLE_TIMEOUT_SECONDS
    assert entry.timeouts.absolute_seconds is not None
    assert entry.process.priority_delta == FLEET_NICE_DELTA
    assert entry.environment
    assert entry.environment_note


@pytest.mark.parametrize("key", ALL_KEYS)
def test_extra_directories_are_granted_only_to_maintenance(key, cfg):
    entry = entry_of(resolve_all(cfg)[key], cfg)
    expected = (
        [str(maintenance._state_tree_root())] if key.startswith("maintenance/") else []
    )
    assert entry.add_dirs == expected


@pytest.mark.parametrize("key", ALL_KEYS)
def test_only_the_architecture_judge_ignores_an_engine_role(key, cfg):
    flags = entry_of(resolve_all(cfg)[key], cfg).flags
    ignores = [f for f in flags if f.code.startswith("ignores_")]
    if key != "architecture/judge":
        assert ignores == []
        return
    codes = [f.code for f in ignores]
    assert "ignores_engine_roles" in codes
    assert ("ignores_engine_effort" in codes) == (cfg.roles["judge"].effort is not None)


def test_judge_flag_is_informational_when_the_stored_role_matches():
    flags = entry_of_key("architecture/judge", default_engine_config()).flags
    assert [(f.code, f.severity) for f in flags] == [("ignores_engine_roles", "info")]


def test_judge_flags_warn_when_the_stored_role_differs():
    flags = entry_of_key("architecture/judge", custom_config()).flags
    by_code = {f.code: f for f in flags}
    assert by_code["ignores_engine_roles"].severity == "warn"
    assert "haiku" in by_code["ignores_engine_roles"].detail
    assert by_code["ignores_engine_effort"].severity == "warn"
    assert "low" in by_code["ignores_engine_effort"].detail


def test_ci_dependency_auditor_is_flagged_when_the_run_list_grant_is_missing():
    flags = entry_of_key("maintenance/ci_dependency_auditor", custom_config()).flags
    assert [(f.code, f.severity) for f in flags] == [("other_mismatch", "warn")]
    assert "gh run list" in flags[0].detail


def test_ci_dependency_auditor_is_not_flagged_with_the_default_grants():
    assert entry_of_key("maintenance/ci_dependency_auditor", default_engine_config()).flags == []


@pytest.mark.parametrize(
    "key",
    [
        k
        for k in ALL_KEYS
        if k not in {"maintenance/ci_dependency_auditor", "brief_refine/brief_refiner"}
    ],
)
def test_no_other_agent_carries_an_other_mismatch_flag(key, cfg):
    flags = entry_of(resolve_all(cfg)[key], cfg).flags
    assert [f for f in flags if f.code == "other_mismatch"] == []


def test_brief_refiner_flags_the_bare_prompt_of_its_correction_reask(cfg):
    flags = entry_of_key("brief_refine/brief_refiner", cfg).flags
    (flag,) = [f for f in flags if f.code == "other_mismatch"]
    assert flag.severity == "warn"
    assert "re-ask" in flag.title
    assert "terseness" in flag.detail and "untrusted-content" in flag.detail
    assert "Informational" in flag.detail


@pytest.mark.parametrize("key", ALL_KEYS)
def test_entries_identify_team_role_and_when_they_run(key, cfg):
    agent = resolve_all(cfg)[key]
    entry = entry_of(agent, cfg)
    team_name, role_name = key.split("/")
    assert entry.id == f"team_role:{key}"
    assert entry.name == f"{team_name} / {role_name}"
    assert (entry.team, entry.role) == (team_name, role_name)
    assert entry.when_runs
    assert entry.prompt_overridden is False


def test_brief_refiner_declares_its_conditional_extra_directory(cfg):
    agent = resolve_all(cfg)["brief_refine/brief_refiner"]
    entry = entry_of(agent, cfg)
    assert "extra directory" in agent.when_runs
    assert agent.spec.add_dirs == ()
    assert entry.add_dirs == []
    (conditional,) = entry.conditional_add_dirs
    assert conditional.label == "WORKSPACE_DIR"
    assert brief_refine.SOURCE_FILE_NAME in conditional.condition


@pytest.mark.parametrize("key", [k for k in ALL_KEYS if k != "brief_refine/brief_refiner"])
def test_no_other_agent_declares_a_conditional_directory(key, cfg):
    assert entry_of(resolve_all(cfg)[key], cfg).conditional_add_dirs == []


def test_template_workspace_pins_the_viewed_engine_and_slots_the_task():
    cfg = custom_config()
    workspace = template_workspace(cfg)
    assert workspace.meta.engine_snapshot is not None
    assert workspace.meta.engine_snapshot.config is cfg
    assert workspace.meta.title == "⟦slot:PIPELINE_TASK_TITLE⟧"
    assert workspace.meta.description == "⟦slot:PIPELINE_TASK_DESCRIPTION⟧"
    assert not workspace.path.exists()
