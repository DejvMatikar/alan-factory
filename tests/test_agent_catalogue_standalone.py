from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from alan import (
    agent_catalogue,
    agent_key,
    claude_cli,
    claude_cli_grants,
    driver_loop_cmd,
    driver_loop_registry,
    engine_config,
    operator_tmux,
    roots,
)
from alan import engine_config_proposal as proposal_mod
from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import ResolvedAgent, to_entry
from alan.agent_catalogue_models import AgentEntry
from alan.agent_catalogue_standalone import GROUPS
from alan.agent_catalogue_standalone_common import ALLOWLISTED_ENVIRONMENT_NOTE
from alan.agent_catalogue_standalone_driver_loops import TEMPLATE_NAME
from alan.agent_catalogue_standalone_operator import _DOCS_DIR
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.driver_loop_cmd import VERIFIER_GH_TOKEN_ENV
from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig, default_engine_config
from alan.operator_prompt import Scope
from alan.teams import implementation_plan
from tests.agent_catalogue_standalone_drivers import launched_models, parity_failures

_NON_CHAT_GROUPS = tuple(group for group in GROUPS if group.key.kind != "chat")

_VARIANTS: dict[str, Callable[[], EngineConfig]] = {
    "default": default_engine_config,
    "customised": lambda: default_engine_config().model_copy(
        update={
            "roles": {
                **default_engine_config().roles,
                "judge": RoleConfig(model="haiku", effort="low"),
                "failure_scanner": RoleConfig(model="opus", effort="high"),
            }
        }
    ),
}

_HELPERS = [
    (agent_key.COLLISION, "haiku", None, 3, True),
    (agent_key.MANAGER_SUMMARY, "haiku", None, 3, True),
    (agent_key.INVESTIGATOR, "sonnet", "high", 40, False),
    (agent_key.HANDOVER_COMPACTOR, "haiku", None, 1, True),
    (agent_key.ENGINE_CONFIG_PROPOSAL, "sonnet", "high", 40, False),
    (agent_key.PLANNER_GRADER_EVAL, "haiku", None, 3, True),
]

_OPERATOR_KEYS = [agent_key.operator_session(scope.value) for scope in Scope]
_LEGACY_KEYS = [agent_key.legacy_planning(role.name) for role in implementation_plan.ROLES]


def _resolved(cfg: EngineConfig | None = None) -> dict[str, ResolvedAgent]:
    resolved, errors = agent_catalogue.resolve_all(cfg or default_engine_config(), GROUPS)
    assert errors == []
    return {agent.key.id: agent for agent in resolved}


def _entry(key: agent_key.AgentKey, cfg: EngineConfig | None = None) -> AgentEntry:
    cfg = cfg or default_engine_config()
    agent = _resolved(cfg)[key.id]
    return to_entry(agent, agent_flags(agent, cfg))


def _all_entries(cfg: EngineConfig | None = None) -> list[AgentEntry]:
    cfg = cfg or default_engine_config()
    return [to_entry(agent, agent_flags(agent, cfg)) for agent in _resolved(cfg).values()]


def _text(segments: list[Any]) -> str:
    return "".join(s.text if s.kind == "text" else "{" + s.label + "}" for s in segments)


def _write_loop(directory: Path, **fields: object) -> None:
    (directory / f"{fields['name']}.yaml").write_text(yaml.safe_dump(fields), encoding="utf-8")


@pytest.fixture
def empty_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "driver_loops.d"
    directory.mkdir()
    monkeypatch.setattr(driver_loop_registry, "config_dir", lambda: directory)
    return directory


def _register_default_judge(model: str, effort: str | None) -> None:
    config = EngineConfig(
        repo=RepoIdentity.from_slug("acme/judge-engine"),
        roles={"judge": RoleConfig(model=model, effort=effort)},
    )
    engine_config.register(config, default=True, validate_env=False)


def test_the_standalone_groups_produce_every_non_team_agent_once(empty_registry: Path) -> None:
    expected = {key.id for key in agent_key.STATIC_KEYS}
    expected.discard(agent_key.IMPLEMENTATION_DRIFT_BACKSTOP.id)
    expected |= {agent_key.explore_chat(c.key).id for c in claude_cli.CONSULTANTS}
    expected |= {key.id for key in _OPERATOR_KEYS + _LEGACY_KEYS}
    expected.add(agent_key.driver_loop(TEMPLATE_NAME).id)
    ids = list(_resolved())
    assert sorted(ids) == sorted(expected)
    assert ids == [group.id for group in GROUPS]


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
@pytest.mark.parametrize(
    ("key", "model", "effort", "turns", "denies_all"),
    _HELPERS,
    ids=[row[0].id for row in _HELPERS],
)
def test_background_helpers_declare_their_fixed_model_effort_and_turns(
    variant: str,
    key: agent_key.AgentKey,
    model: str,
    effort: str | None,
    turns: int,
    denies_all: bool,
) -> None:
    entry = _entry(key, _VARIANTS[variant]())
    assert (entry.model.value, entry.model.source) == (model, "builtin")
    assert entry.effort.value == effort
    assert entry.effort.display == (effort or "CLI default")
    assert entry.turn_limit.max_turns == turns
    assert entry.kind == "background_helper"
    assert entry.scope == "machine"
    assert entry.flags == []
    assert (entry.tools.denied == ["*"]) is denies_all
    assert (entry.tools.effective == []) is denies_all


@pytest.mark.parametrize(
    "key",
    [row[0] for row in _HELPERS if row[4]],
    ids=lambda key: key.id,
)
def test_tool_less_helpers_append_only_the_no_tools_block(key: agent_key.AgentKey) -> None:
    prompt = _entry(key).prompt
    assert [s.label for s in prompt if s.shared][-1] == "no_tools"
    assert "headless_execution" not in {s.label for s in prompt}
    assert "PROJECT_DOC_BLOCK" not in {s.label for s in prompt}


def test_the_investigator_gets_read_only_tools_and_the_incident_workspace_folder() -> None:
    entry = _entry(agent_key.INVESTIGATOR)
    assert entry.tools.effective[:3] == ["Read", "Grep", "Glob"]
    assert "Bash(gh run view:*)" in entry.tools.effective
    assert {"Write", "Edit", "NotebookEdit"} <= set(entry.tools.denied)
    assert entry.add_dirs == ["{{INCIDENT_WORKSPACE_DIR}}"]
    assert {"headless_execution", "PROJECT_DOC_BLOCK"} <= {s.label for s in entry.prompt}
    placeholders = {s.label for s in entry.user_message if s.kind == "placeholder"}
    assert placeholders == {"INCIDENT_FINGERPRINT", "INCIDENT_DISPOSITIONS", "INCIDENT_EVIDENCE"}


def test_helper_timeouts_come_from_the_cli_timeouts() -> None:
    cli = float(claude_cli._cli_timeout_seconds())
    assert _entry(agent_key.COLLISION).timeouts.idle_seconds == cli
    assert _entry(agent_key.HANDOVER_COMPACTOR).timeouts.idle_seconds == 15.0
    investigator = _entry(agent_key.INVESTIGATOR).timeouts
    assert investigator.idle_seconds == claude_cli.idle_timeout_seconds()
    assert investigator.absolute_seconds == claude_cli.abs_timeout_seconds()
    assert _entry(agent_key.PLANNER_GRADER_EVAL).timeouts.absolute_seconds is None


def test_the_collision_and_summary_user_messages_are_slotted_around_the_real_builders() -> None:
    collision = _text(_entry(agent_key.COLLISION).user_message)
    assert collision == "Brief:\n{COLLISION_BRIEF}\n\nCandidates:{COLLISION_CANDIDATES}"
    summary = _text(_entry(agent_key.MANAGER_SUMMARY).user_message)
    assert summary == (
        "Task: {SUMMARY_TASK_TITLE}\nFinal status: {SUMMARY_FINAL_STATUS}"
        "{SUMMARY_DETAILS}\n\nWrite the plain-language summary now."
    )


def test_the_proposal_prompt_slots_the_scan_results_and_the_baseline_json() -> None:
    entry = _entry(agent_key.ENGINE_CONFIG_PROPOSAL)
    labels = [s.label for s in entry.prompt if s.kind == "placeholder"]
    assert labels == ["PROPOSAL_PRESET", "PROPOSAL_ADDITIONAL_STACKS", "PROPOSAL_BASELINE_JSON"]
    assert _text(entry.user_message).startswith("Inspect this repository")
    assert entry.session.behaviour == "new"
    assert "temporary clone" in entry.working_directory
    assert entry.tools.effective == list(claude_cli._DEFAULT_CHAT_ALLOWED_TOOLS)


def test_the_proposal_entry_fails_loudly_when_the_prompt_stops_embedding_the_baseline_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proposal_mod, "_proposal_system_prompt", lambda _baseline, _detection: "no baseline here"
    )
    resolved, errors = agent_catalogue.resolve_all(default_engine_config(), GROUPS)
    assert agent_key.ENGINE_CONFIG_PROPOSAL.id not in {agent.key.id for agent in resolved}
    failing = [entry for entry in errors if entry.id == agent_key.ENGINE_CONFIG_PROPOSAL.id]
    assert [entry.error for entry in failing] == [
        "ValueError: the engine-config proposal prompt no longer embeds the baseline JSON"
    ]


def test_the_judge_eval_reads_the_default_engines_judge_role_not_another_viewed_engine() -> None:
    other = _VARIANTS["customised"]().model_copy(
        update={"repo": RepoIdentity.from_slug("acme/widget")}
    )
    entry = _entry(agent_key.JUDGE_EVAL, other)
    assert (entry.model.value, entry.model.source) == ("opus", "engine_role")
    assert entry.scope == "default_engine"
    assert entry.tags == ["eval"]
    assert entry.effort.value == "xhigh"
    assert entry.flags == []
    assert _resolved()[agent_key.JUDGE_EVAL.id].model_source.engine_role == "judge"


def test_the_judge_eval_reads_the_viewed_engines_judge_role_when_it_is_the_default_engine() -> None:
    entry = _entry(agent_key.JUDGE_EVAL, _VARIANTS["customised"]())
    assert (entry.model.value, entry.model.source) == ("haiku", "engine_role")
    assert entry.effort.display == "CLI default"
    assert [flag.code for flag in entry.flags] == ["effort_mismatch"]


def test_the_judge_eval_follows_the_registered_default_engines_judge_role() -> None:
    _register_default_judge("haiku", "low")
    entry = _entry(agent_key.JUDGE_EVAL)
    assert (entry.model.value, entry.model.source) == ("haiku", "engine_role")
    assert entry.effort.display == "CLI default"
    assert [flag.code for flag in entry.flags] == ["effort_mismatch"]


def test_the_judge_eval_prompt_is_the_judge_prompt_with_the_no_tools_tail() -> None:
    entry = _entry(agent_key.JUDGE_EVAL)
    assert [s.label for s in entry.user_message] == ["JUDGE_CASE_CONTEXT"]
    assert [s.label for s in entry.prompt if s.shared][-1] == "no_tools"


def test_the_planner_grader_user_message_slots_the_rubric_brief_and_plan() -> None:
    text = _text(_entry(agent_key.PLANNER_GRADER_EVAL).user_message)
    assert text.startswith("## Rubric\n{PLANNER_RUBRIC}\n\n## Task brief\n{PLANNER_CASE_BRIEF}")
    assert "{PLANNER_CASE_PLAN}" in text
    assert _entry(agent_key.PLANNER_GRADER_EVAL).tags == ["eval"]


@pytest.mark.parametrize("key", _LEGACY_KEYS, ids=lambda key: key.id)
def test_legacy_planning_runs_every_role_on_the_one_shot_default_and_is_flagged(
    key: agent_key.AgentKey,
) -> None:
    entry = _entry(key)
    role = next(r for r in implementation_plan.ROLES if agent_key.legacy_planning(r.name) == key)
    expected = ["legacy_planning_endpoint"]
    if role.model != "sonnet":
        expected.append("model_mismatch")
    assert [flag.code for flag in entry.flags] == expected
    assert entry.tags == ["legacy"]
    assert entry.kind == "team_role"
    assert (entry.model.value, entry.model.source) == ("sonnet", "builtin")
    assert entry.effort.value == "high"
    assert entry.turn_limit.max_turns == 3
    assert (entry.team, entry.role) == ("implementation_plan", role.name)
    assert entry.tools.effective == []
    assert {"Write", "Edit", "NotebookEdit"} <= set(entry.tools.denied)


def test_legacy_user_messages_list_the_earlier_role_outputs() -> None:
    first = _entry(_LEGACY_KEYS[0]).user_message
    last = _entry(_LEGACY_KEYS[-1]).user_message
    assert [s.label for s in first if s.kind == "placeholder"] == [
        "LEGACY_PLAN_TITLE",
        "LEGACY_PLAN_DESCRIPTION",
    ]
    outputs = [s.label for s in last if s.kind == "placeholder"][2:]
    assert outputs == [f"LEGACY_{r.name.upper()}_OUTPUT" for r in implementation_plan.ROLES[:-1]]


@pytest.mark.parametrize("key", _OPERATOR_KEYS, ids=lambda key: key.id)
def test_operator_sessions_are_interactive_with_cli_defaults_and_credential_denies(
    key: agent_key.AgentKey,
) -> None:
    entry = _entry(key)
    assert entry.kind == "operator_launched"
    assert entry.session.behaviour == "interactive"
    assert (entry.model.display, entry.effort.display) == ("CLI default", "CLI default")
    assert entry.permission_mode is None
    assert entry.turn_limit.max_turns is None
    assert entry.tools.effective == []
    assert entry.tools.denied == claude_cli_grants.credential_deny_rules()
    assert entry.prompt == []
    assert entry.process.detached is True
    assert entry.timeouts.idle_seconds is None and entry.timeouts.absolute_seconds is None


@pytest.mark.parametrize("key", _OPERATOR_KEYS, ids=lambda key: key.id)
def test_operator_sessions_pass_only_the_allow_listed_environment(
    key: agent_key.AgentKey,
) -> None:
    entry = _entry(key)
    allow_listed = [e.name for e in driver_loop_cmd.env_policy()]
    assert [e.name for e in entry.environment] == [*allow_listed, "TERM"]
    assert {e.category for e in entry.environment if e.name != "TERM"} == {"allowlisted"}
    assert entry.environment_note == ALLOWLISTED_ENVIRONMENT_NOTE


@pytest.mark.parametrize("key", _OPERATOR_KEYS, ids=lambda key: key.id)
def test_operator_sessions_name_every_variable_the_tmux_session_sets(
    key: agent_key.AgentKey,
) -> None:
    listed = {e.name: e.category for e in _entry(key).environment}
    assert set(operator_tmux.session_env()) <= set(listed)
    assert listed["TERM"] == "fixed_override"


def test_the_fleet_session_adds_the_fleet_roots_and_sends_no_first_message() -> None:
    entry = _entry(agent_key.operator_session("fleet"))
    assert entry.add_dirs == [
        str(path.absolute())
        for path in (roots.workspaces_root(), roots.logs_root(), roots.state_root())
    ]
    assert entry.user_message == []
    assert entry.scope == "machine"


def test_the_repo_session_names_the_viewed_repository_and_adds_no_folders() -> None:
    entry = _entry(agent_key.operator_session("repo"))
    assert entry.add_dirs == []
    assert entry.scope == "repo"
    assert default_engine_config().repo.slug in entry.working_directory


def test_the_workspace_session_slots_the_prompt_and_declares_the_conditional_docs_folder() -> None:
    entry = _entry(agent_key.operator_session("workspace"))
    placeholders = [s.label for s in entry.user_message if s.kind == "placeholder"]
    assert {"OPERATOR_TASK_ID", "OPERATOR_REPO", "OPERATOR_WORKSPACE_DIR"} <= set(placeholders)
    assert "OPERATOR_DOCS_DIR" in placeholders
    assert entry.add_dirs[0] == "{{OPERATOR_WORKSPACE_DIR}}"
    assert len(entry.add_dirs) == 2
    assert entry.conditional_add_dirs == [_DOCS_DIR]
    assert "Rules for this session" in _text(entry.user_message)


def test_an_empty_registry_yields_exactly_one_driver_loop_template(empty_registry: Path) -> None:
    del empty_registry
    template_ids = [i for i in _resolved() if "driver_loop/" in i]
    assert template_ids == [agent_key.driver_loop(TEMPLATE_NAME).id]
    entry = _entry(agent_key.driver_loop(TEMPLATE_NAME))
    assert entry.tags == ["driver_loop", "template"]
    assert [s.label for s in entry.user_message] == ["DRIVER_LOOP_COMMAND"]
    assert entry.permission_mode == "dontAsk"
    assert entry.prompt == []
    assert "template" in entry.when_runs
    assert (entry.model.display, entry.effort.display) == ("CLI default", "CLI default")
    assert [e.name for e in entry.environment] == [e.name for e in driver_loop_cmd.env_policy()]
    assert entry.environment_note == ALLOWLISTED_ENVIRONMENT_NOTE


def test_a_populated_registry_yields_one_entry_per_loop_built_from_its_launch_spec(
    empty_registry: Path, tmp_path: Path
) -> None:
    clone, docs = tmp_path / "clone", tmp_path / "docs"
    _write_loop(
        empty_registry,
        name="alan-issue-driver",
        command="/alan-issue-driver",
        args="acme/widget",
        cwd=str(clone),
        dedicated_clone=True,
        interval_seconds=1800,
        enabled=True,
        timeout_seconds=900,
        model="opus",
        add_dirs=[str(docs)],
        allowed_tools=["Bash(gh issue view:*)"],
        own_state_write=True,
        web_fetch_domains=["docs.python.org"],
    )
    _write_loop(
        empty_registry,
        name="plain-loop",
        command="/plain",
        cwd=str(tmp_path / "plain"),
        self_paced=True,
    )
    (empty_registry / "broken.yaml").write_text("not: [valid", encoding="utf-8")
    ids = [i for i in _resolved() if "driver_loop/" in i]
    assert ids == [
        agent_key.driver_loop("alan-issue-driver").id,
        agent_key.driver_loop("plain-loop").id,
    ]
    granted = _entry(agent_key.driver_loop("alan-issue-driver"))
    entries, _ = driver_loop_registry.load_registry()
    spec = driver_loop_cmd.build_spec(entries[0])
    assert (granted.model.value, granted.model.source) == ("opus", "builtin")
    assert granted.tags == ["driver_loop"]
    assert granted.tools.stored == ["Bash(gh issue view:*)"]
    assert granted.tools.effective == list(spec.allowed_tools)
    assert "WebFetch(domain:docs.python.org)" in granted.tools.effective
    assert granted.add_dirs == [str(docs), str(entries[0].state_dir)]
    assert granted.timeouts.absolute_seconds == 900.0
    assert granted.timeouts.idle_seconds is None
    assert _text(granted.user_message) == "/alan-issue-driver acme/widget"
    assert "every 1800 seconds" in granted.when_runs and "while enabled" in granted.when_runs
    assert "dedicated clone" in granted.working_directory
    assert granted.process.detached is True
    assert granted.scope == "machine"
    plain = _entry(agent_key.driver_loop("plain-loop"))
    assert plain.model.display == "CLI default"
    assert plain.tools.stored == [] and plain.tools.effective == []
    assert "self-paced" in plain.when_runs and "while disabled" in plain.when_runs
    assert "named in the registry entry" in plain.working_directory
    assert _text(plain.user_message) == "/plain"


def test_no_secret_value_appears_in_any_serialised_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    names = engine_config.registered_secret_env_names() | {VERIFIER_GH_TOKEN_ENV, CONTROL_TOKEN_ENV}
    values = {name: f"s3cr3t-value-{index}-xyz" for index, name in enumerate(sorted(names))}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    entries = _all_entries()
    dumped = "".join(entry.model_dump_json() for entry in entries)
    assert [name for name, value in values.items() if value in dumped] == []
    collision = next(e for e in entries if e.id == agent_key.COLLISION.id)
    removed = {e.name for e in collision.environment if e.category == "secret_removed"}
    assert removed == engine_config.registered_secret_env_names() | {CONTROL_TOKEN_ENV}
    for entry in entries:
        if entry.kind == "operator_launched":
            assert not {e.name for e in entry.environment} & names


def test_every_placeholder_segment_carries_a_description() -> None:
    for entry in _all_entries():
        for segment in [*entry.prompt, *entry.user_message]:
            if segment.kind == "placeholder":
                assert segment.description
                assert segment.description != segment.label


def test_every_standalone_entry_serialises_and_reads_back() -> None:
    for entry in _all_entries():
        assert AgentEntry.model_validate_json(entry.model_dump_json()) == entry


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_the_catalogue_matches_what_every_standalone_launch_actually_passes(
    variant: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failures = parity_failures(_VARIANTS[variant](), monkeypatch, tmp_path, _NON_CHAT_GROUPS)
    assert failures == []


def test_declared_sources_are_proven_by_mutating_each_engine_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = default_engine_config()
    base = launched_models(cfg, monkeypatch, tmp_path / "base", _NON_CHAT_GROUPS)
    problems: list[str] = []
    for role in sorted(cfg.roles):
        roles = {**cfg.roles, role: RoleConfig(model="sentinel-model", effort="sentinel-effort")}
        mutated = launched_models(
            cfg.model_copy(update={"roles": roles}),
            monkeypatch,
            tmp_path / f"mutated-{role}",
            _NON_CHAT_GROUPS,
        )
        for key_id, before in base.items():
            source = before.agent.model_source
            follows = source.kind == "engine_role" and source.engine_role == role
            expected = "sentinel-model" if follows else before.launched[0]
            after = mutated[key_id]
            problems += [
                f"{key_id} {role}: {value} != {expected}"
                for value in (after.launched[0], after.catalogued[0])
                if value != expected
            ]
    assert problems == []
    reads_a_role = {k for k, v in base.items() if v.agent.model_source.kind == "engine_role"}
    assert reads_a_role == {agent_key.JUDGE_EVAL.id}
    assert all(v.agent.effort_source.kind != "engine_role" for v in base.values())


def test_the_populated_driver_loop_registry_matches_the_real_launch_command(
    empty_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_loop(
        empty_registry, name="plain-loop", command="/plain", cwd=str(tmp_path), self_paced=True
    )
    groups = tuple(g for g in _NON_CHAT_GROUPS if "driver_loop/" in g.id)
    assert parity_failures(default_engine_config(), monkeypatch, tmp_path, groups) == []
