from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import to_entry
from alan.claude_cli_grants import (
    GENERAL_EXEC_GRANTS,
    PROMPT_INSTRUCTED_GRANTS,
    WRITE_TOOLS,
    gate_tool_grants,
)
from alan.engine_config import RoleConfig
from alan.teams import _base, _clause_audit, verification
from alan.teams._base import render_allowed_tools
from alan.teams._static_pipeline import (
    _coverage_block,  # pyright: ignore[reportPrivateUsage]
    assemble_role_system_prompt,
    build_role_user_text,
)
from alan.teams.verification import TEAM, roles_for_config
from tests._agent_catalogue_impl_support import (
    ALL_KEYS,
    CLAUSE_AUDITOR_KEY,
    CONFIG_FACTORIES,
    DIFF_REVIEWER_KEY,
    JUDGE_KEY,
    SCENARIO_RUNNER_KEY,
    bare_config,
    custom_config,
    default_config,
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

VERIFICATION_KEYS = [DIFF_REVIEWER_KEY, SCENARIO_RUNNER_KEY, CLAUSE_AUDITOR_KEY, JUDGE_KEY]
ENGINE_ROLE_OF = {
    DIFF_REVIEWER_KEY: "diff_reviewer",
    SCENARIO_RUNNER_KEY: "diff_reviewer",
    CLAUSE_AUDITOR_KEY: "diff_reviewer",
    JUDGE_KEY: "judge",
}
ENGINE_ROLES = ["implementer", "remediator", "judge", "diff_reviewer", "failure_scanner"]


def test_verification_roles_resolve_in_stage_order() -> None:
    agents = resolve_agents(default_config())
    ordered = [key for key in agents if key in VERIFICATION_KEYS]
    assert ordered == VERIFICATION_KEYS


@pytest.mark.parametrize("key", VERIFICATION_KEYS)
def test_verification_role_runs_as_a_repo_scoped_auto_mode_node(key: str) -> None:
    agent = resolve_agents(default_config())[key]
    entry = to_entry(agent, [])
    assert entry.scope == "repo"
    assert entry.team == "verification"
    assert entry.permission_mode == "auto"
    assert entry.turn_limit.max_turns == 150
    assert entry.add_dirs == []
    assert agent.spec.process.fleet is True
    assert entry.session.behaviour == "resumable"
    assert set(WRITE_TOOLS) <= set(entry.tools.denied)


@pytest.mark.parametrize(("key", "engine_role"), list(ENGINE_ROLE_OF.items()))
def test_verification_model_comes_from_the_named_engine_role(key: str, engine_role: str) -> None:
    agent = resolve_agents(custom_config())[key]
    entry = to_entry(agent, [])
    assert agent.model_source.kind == "engine_role"
    assert agent.model_source.engine_role == engine_role
    assert agent.engine_role_hint == engine_role
    assert entry.model.value == custom_config().roles[engine_role].model


@pytest.mark.parametrize(
    ("key", "model", "effort"),
    [
        (DIFF_REVIEWER_KEY, "sonnet", "high"),
        (SCENARIO_RUNNER_KEY, "sonnet", "high"),
        (CLAUSE_AUDITOR_KEY, "sonnet", "high"),
        (JUDGE_KEY, "opus", "xhigh"),
    ],
)
def test_verification_effort_is_derived_from_the_model_not_the_engine_role(
    key: str, model: str, effort: str
) -> None:
    agent = resolve_agents(default_config())[key]
    entry = to_entry(agent, [])
    assert (entry.model.value, entry.effort.value) == (model, effort)
    assert agent.effort_source.kind == "builtin"
    assert agent.effort_source.engine_role is None


def test_verification_effort_shows_the_cli_default_for_a_model_without_a_derived_effort() -> None:
    entry = to_entry(resolve_agents(custom_config())[JUDGE_KEY], [])
    assert entry.model.value == "haiku"
    assert (entry.effort.value, entry.effort.display) == (None, "CLI default")


@pytest.mark.parametrize(
    ("key", "model"),
    [(DIFF_REVIEWER_KEY, "sonnet"), (CLAUSE_AUDITOR_KEY, "sonnet"), (JUDGE_KEY, "opus")],
)
def test_verification_falls_back_to_the_builtin_role_table_when_the_engine_has_none(
    key: str, model: str
) -> None:
    agent = resolve_agents(bare_config())[key]
    assert agent.spec.model == model
    assert agent.model_source.kind == "builtin"
    assert agent.model_source.engine_role is None


@pytest.mark.parametrize("key", VERIFICATION_KEYS)
def test_verification_flags_an_engine_effort_it_ignores(key: str) -> None:
    cfg = custom_config()
    flags = agent_flags(resolve_agents(cfg)[key], cfg)
    assert [flag.code for flag in flags] == ["ignores_engine_effort"]
    assert flags[0].severity == "warn"


def test_verification_raises_no_effort_flag_for_the_default_engine() -> None:
    cfg = default_config()
    for key in VERIFICATION_KEYS:
        assert agent_flags(resolve_agents(cfg)[key], cfg) == []


@pytest.mark.parametrize("role", ENGINE_ROLES)
def test_a_mutated_engine_role_changes_the_launch_model_iff_declared_as_its_source(
    role: str,
) -> None:
    base_cfg = default_config()
    mutated_cfg = with_role(base_cfg, role, RoleConfig(model="sentinel-model", effort="opus"))
    base = resolve_agents(base_cfg)
    mutated = resolve_agents(mutated_cfg)
    for key in ALL_KEYS:
        declared = base[key].model_source
        reads_role = declared.kind == "engine_role" and declared.engine_role == role
        assert (base[key].spec.model != mutated[key].spec.model) is reads_role


@pytest.mark.parametrize("role", ENGINE_ROLES)
def test_a_mutated_engine_role_changes_the_launch_effort_iff_declared_as_its_source(
    role: str,
) -> None:
    base_cfg = default_config()
    mutated_cfg = with_role(
        base_cfg, role, RoleConfig(model=base_cfg.roles[role].model, effort="sentinel-effort")
    )
    base = resolve_agents(base_cfg)
    mutated = resolve_agents(mutated_cfg)
    for key in ALL_KEYS:
        declared = base[key].effort_source
        reads_role = declared.kind == "engine_role" and declared.engine_role == role
        assert (base[key].spec.effort != mutated[key].spec.effort) is reads_role


@pytest.mark.parametrize("key", VERIFICATION_KEYS)
def test_verification_effective_tools_narrow_the_stored_list_and_add_gate_grants(
    key: str,
) -> None:
    cfg = default_config()
    entry = to_entry(resolve_agents(cfg)[key], [])
    assert entry.tools.stored == list(render_allowed_tools(cfg, "verification"))
    assert set(GENERAL_EXEC_GRANTS) <= set(entry.tools.stored)
    assert not set(GENERAL_EXEC_GRANTS) & set(entry.tools.effective)
    assert set(PROMPT_INSTRUCTED_GRANTS) <= set(entry.tools.effective)
    assert set(gate_tool_grants(cfg.gate_commands)) - set(GENERAL_EXEC_GRANTS) <= set(
        entry.tools.effective
    )


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
@pytest.mark.parametrize("key", VERIFICATION_KEYS)
def test_verification_requested_tools_equal_the_team_role_overrides(
    tmp_path: Path, config_name: str, key: str
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    role = key.rsplit("/", 1)[1]
    real = TEAM._role_overrides(  # pyright: ignore[reportPrivateUsage]
        role, real_workspace(cfg, tmp_path)
    )
    assert list(resolve_agents(cfg)[key].spec.requested_tools) == real.allowed_tools
    assert real.max_turns is None
    assert real.permission_mode is None
    assert real.add_dirs is None


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
@pytest.mark.parametrize(
    ("key", "index"), [(DIFF_REVIEWER_KEY, 0), (SCENARIO_RUNNER_KEY, 1), (JUDGE_KEY, 2)]
)
def test_verification_prompt_template_matches_the_real_role_prompt(
    config_name: str, key: str, index: int
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    role = roles_for_config(cfg)[index]
    real = assemble_role_system_prompt(
        role.system_prompt,
        terse=TEAM._role_terse(role.name),  # pyright: ignore[reportPrivateUsage]
    )
    assert prompt_regex(resolve_agents(cfg)[key]).fullmatch(launched_prompt(real)) is not None


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
def test_clause_auditor_template_matches_the_first_attempt_and_the_retry_prompt(
    config_name: str,
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    pattern = prompt_regex(resolve_agents(cfg)[CLAUSE_AUDITOR_KEY])
    first = assemble_role_system_prompt(_clause_audit.clause_auditor_prompt(cfg), terse=True)
    retry = assemble_role_system_prompt(_clause_audit.clause_auditor_retry_prompt(cfg), terse=True)
    assert pattern.fullmatch(launched_prompt(first)) is not None
    assert pattern.fullmatch(launched_prompt(retry)) is not None


def test_clause_auditor_marks_the_test_ownership_paragraph_as_a_placeholder() -> None:
    entry = to_entry(resolve_agents(default_config())[CLAUSE_AUDITOR_KEY], [])
    assert "CLAUSE_TEST_OWNERSHIP" in placeholder_labels(entry.prompt)
    assert entry.prompt[0].text.startswith("You are the clause_auditor on the verification team")
    assert "TEST OWNERSHIP" not in "".join(segment.text for segment in entry.prompt)


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
def test_clause_auditor_model_equals_the_one_the_run_path_selects(
    tmp_path: Path, config_name: str
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    workspace = real_workspace(cfg, tmp_path)
    by_name = {role.name: role for role in roles_for_config(cfg)}
    staged = _clause_audit.stage_roles(("clause_auditor",), by_name, cfg)
    spec = resolve_agents(cfg)[CLAUSE_AUDITOR_KEY].spec
    assert spec.model == staged[0].model
    assert spec.model == _clause_audit._clause_auditor_model(  # pyright: ignore[reportPrivateUsage]
        workspace
    )


@pytest.mark.parametrize(
    ("key", "terse"),
    [
        (DIFF_REVIEWER_KEY, False),
        (SCENARIO_RUNNER_KEY, True),
        (CLAUSE_AUDITOR_KEY, True),
        (JUDGE_KEY, False),
    ],
)
def test_verification_prompt_labels_the_terseness_and_untrusted_content_blocks(
    key: str, terse: bool
) -> None:
    labels = shared_labels(to_entry(resolve_agents(default_config())[key], []).prompt)
    assert ("terseness_block" in labels) is terse
    assert "untrusted_content_notice" in labels


@pytest.mark.parametrize(
    ("config_name", "overridden", "clauses"),
    [
        ("default", False, set()),
        ("custom", False, set()),
        ("overridden", True, {"verifier_anti_self_approval", "merge_gate_clause"}),
    ],
)
def test_judge_marks_a_prompt_override_and_labels_the_safety_clauses(
    config_name: str, overridden: bool, clauses: set[str]
) -> None:
    entry = to_entry(resolve_agents(CONFIG_FACTORIES[config_name]())[JUDGE_KEY], [])
    labels = shared_labels(entry.prompt)
    assert entry.prompt_overridden is overridden
    assert labels & {"verifier_anti_self_approval", "merge_gate_clause"} == clauses
    assert ("OVERRIDDEN JUDGE" in "".join(s.text for s in entry.prompt)) is overridden


@pytest.mark.parametrize("key", [DIFF_REVIEWER_KEY, SCENARIO_RUNNER_KEY, CLAUSE_AUDITOR_KEY])
def test_only_the_judge_is_marked_overridden(key: str) -> None:
    assert to_entry(resolve_agents(overridden_config())[key], []).prompt_overridden is False


@pytest.mark.parametrize(
    ("key", "first"),
    [
        (DIFF_REVIEWER_KEY, "DIFF_REVIEW_CONTEXT"),
        (SCENARIO_RUNNER_KEY, "SCENARIO_EVIDENCE"),
        (CLAUSE_AUDITOR_KEY, "CLAUSE_AUDIT_EVIDENCE"),
        (JUDGE_KEY, "WORK_ITEM_TITLE"),
    ],
)
def test_verification_user_message_starts_with_the_role_evidence(key: str, first: str) -> None:
    labels = placeholder_labels(to_entry(resolve_agents(default_config())[key], []).user_message)
    assert labels[0] == first
    assert {"WORK_ITEM_DESCRIPTION", "IMPLEMENTATION_HANDOVER", "REVIEW_INPUTS"} <= set(labels)


@pytest.mark.parametrize(
    ("key", "role"),
    [
        (DIFF_REVIEWER_KEY, "diff_reviewer"),
        (SCENARIO_RUNNER_KEY, "scenario_runner"),
        (JUDGE_KEY, "judge"),
    ],
)
def test_verification_user_message_scopes_the_role(key: str, role: str) -> None:
    agent = resolve_agents(default_config())[key]
    assert f"Your scope is only {role} " in agent.user_message


def test_clause_auditor_user_message_leaves_its_coverage_note_open() -> None:
    labels = placeholder_labels(
        to_entry(resolve_agents(default_config())[CLAUSE_AUDITOR_KEY], []).user_message
    )
    assert labels[-1] == "CLAUSE_AUDITOR_COVERAGE"


def test_judge_user_message_carries_the_earlier_role_outputs_and_the_optional_sections() -> None:
    labels = placeholder_labels(
        to_entry(resolve_agents(default_config())[JUDGE_KEY], []).user_message
    )
    assert [label for label in labels if label != "ROLE_OUTPUT_NONCE"][-4:] == [
        "VERIFICATION_DIFF_REVIEWER_OUTPUT",
        "VERIFICATION_SCENARIO_RUNNER_OUTPUT",
        "JUDGE_CLAUSE_AUDITOR_SECTION",
        "JUDGE_RETRY_NUDGE",
    ]


def test_judge_user_message_leaves_the_fence_nonce_open_in_every_marker() -> None:
    labels = placeholder_labels(
        to_entry(resolve_agents(default_config())[JUDGE_KEY], []).user_message
    )
    assert labels.count("ROLE_OUTPUT_NONCE") == 1 + 2 * 2


VERIFICATION_HANDOVERS = {
    "no_handover": None,
    "first_iteration": real_handover(from_team="implementation", iteration=1),
}
EARLIER_OUTPUTS = {
    DIFF_REVIEWER_KEY: [],
    SCENARIO_RUNNER_KEY: [],
    CLAUSE_AUDITOR_KEY: [],
    JUDGE_KEY: [("diff_reviewer", "reviewed"), ("scenario_runner", "ran")],
}


@pytest.mark.parametrize("key", VERIFICATION_KEYS)
@pytest.mark.parametrize("handover_name", list(VERIFICATION_HANDOVERS))
@pytest.mark.parametrize("instructions", [None, "also check z"], ids=["plain", "instructed"])
def test_verification_user_template_matches_the_real_user_text(
    tmp_path: Path, key: str, handover_name: str, instructions: str | None
) -> None:
    cfg = default_config()
    role = key.rsplit("/", 1)[1]
    workspace = real_workspace(cfg, tmp_path)
    base = TEAM._build_base_user_text(  # pyright: ignore[reportPrivateUsage]
        workspace, VERIFICATION_HANDOVERS[handover_name], instructions
    )
    real = TEAM._role_user_prefix(role) + build_role_user_text(  # pyright: ignore[reportPrivateUsage]
        base, EARLIER_OUTPUTS[key], _coverage_block(role, TEAM.roles)
    )
    assert user_regex(resolve_agents(cfg)[key]).fullmatch(real) is not None


@pytest.mark.parametrize("key", [DIFF_REVIEWER_KEY, SCENARIO_RUNNER_KEY, CLAUSE_AUDITOR_KEY])
def test_first_stage_user_message_has_no_prior_outputs(key: str) -> None:
    agent = resolve_agents(default_config())[key]
    assert "## CONTEXT" not in agent.user_message


@pytest.mark.parametrize("key", VERIFICATION_KEYS)
def test_verification_entry_describes_when_it_runs_and_where(key: str) -> None:
    entry = to_entry(resolve_agents(default_config())[key], [])
    assert entry.when_runs
    assert "grounded checkout" in entry.working_directory
    assert entry.timeouts.idle_seconds is not None


def capture_node_launch(monkeypatch: pytest.MonkeyPatch, module: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "retry output"

    monkeypatch.setattr(module, "run_or_resume_node", fake_run)
    return captured


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
def test_clause_auditor_retry_launch_matches_the_catalogue_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    workspace = real_workspace(cfg, tmp_path)
    captured = capture_node_launch(monkeypatch, _base)
    _clause_audit._run_clause_auditor_retry(  # pyright: ignore[reportPrivateUsage]
        workspace,
        MagicMock(),
        "inv_001",
        MagicMock(),
        TEAM._build_base_user_text(workspace, None, None),  # pyright: ignore[reportPrivateUsage]
        [],
        None,
        "unparseable first output",
        overrides=TEAM._role_overrides("clause_auditor", workspace),  # pyright: ignore[reportPrivateUsage]
    )
    agent = resolve_agents(cfg)[CLAUSE_AUDITOR_KEY]
    assert captured["role"] == "clause_auditor"
    assert captured["model"] == agent.spec.model
    assert list(agent.spec.requested_tools) == captured["overrides"].allowed_tools
    assert prompt_regex(agent).fullmatch(launched_prompt(captured["system_prompt"])) is not None
    assert user_regex(agent).fullmatch(captured["user_text"]) is not None


@pytest.mark.parametrize("config_name", list(CONFIG_FACTORIES))
def test_judge_retry_launch_matches_the_catalogue_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    cfg = CONFIG_FACTORIES[config_name]()
    workspace = real_workspace(cfg, tmp_path)
    captured = capture_node_launch(monkeypatch, verification)
    TEAM._run_judge_retry(  # pyright: ignore[reportPrivateUsage]
        workspace,
        MagicMock(),
        "inv_001",
        MagicMock(),
        TEAM._build_base_user_text(workspace, None, None),  # pyright: ignore[reportPrivateUsage]
        [("diff_reviewer", "reviewed"), ("scenario_runner", "ran")],
        None,
        roles_for_config(cfg),
        reason="missing_verdict",
    )
    agent = resolve_agents(cfg)[JUDGE_KEY]
    assert captured["role"] == "judge"
    assert captured["model"] == agent.spec.model
    assert list(agent.spec.requested_tools) == captured["overrides"].allowed_tools
    assert prompt_regex(agent).fullmatch(launched_prompt(captured["system_prompt"])) is not None
    assert user_regex(agent).fullmatch(captured["user_text"]) is not None
