from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import agent_catalogue, agent_key, launch_spec
from alan.agent_catalogue_helpers import ResolvedAgent, ValueSource, segments_from_text
from alan.agent_catalogue_models import PromptSegmentOut
from alan.claude_cli import CONSULTANTS
from alan.engine_config import EngineConfig, RoleConfig, default_engine_config
from alan.launch_spec import LaunchSpec
from alan.operator_prompt import Scope
from alan.teams import (
    TEAMS,
    architecture,
    brainstorming,
    brief_refine,
    decomposer,
    implementation,
    implementation_fix,
    implementation_plan,
    maintenance,
    opportunity_scout,
    research,
    ui_review,
    verification,
)
from alan.teams._orchestrated import OrchestratedTeam
from alan.teams._static_pipeline import StaticPipelineTeam
from tests import agent_catalogue_parity as parity
from tests import agent_catalogue_parity_drivers_standalone as standalone_drivers
from tests.agent_catalogue_parity import CallSite, Captured, LaunchRecorder, ParityEnv
from tests.agent_catalogue_parity_drivers import register_all_drivers

register_all_drivers()

SENTINEL_MODEL = "sentinel-model-7f3"
SENTINEL_EFFORT = "sentinel-effort-7f3"
_CLAUSE_AUDITOR_ROLE = "clause_auditor"
_NO_LLM_TEAMS = frozenset({"deploy_verify", "dependency_sweep", "pr_merge", "triage"})
_NODE_ONLY_TEAMS = {
    implementation.TEAM.name: implementation._NODE_ROLE,  # noqa: SLF001
    implementation_fix.TEAM.name: implementation_fix._NODE_ROLE,  # noqa: SLF001
}
_LLM_TEAM_MODULES = (
    architecture,
    brainstorming,
    brief_refine,
    decomposer,
    implementation,
    implementation_fix,
    implementation_plan,
    maintenance,
    opportunity_scout,
    research,
    ui_review,
    verification,
)


def _team_role_ids(cfg: EngineConfig) -> set[str]:
    ids: set[str] = set()
    for name, team in TEAMS.items():
        if name in _NO_LLM_TEAMS:
            continue
        if name in _NODE_ONLY_TEAMS:
            roles = [_NODE_ONLY_TEAMS[name]]
        elif isinstance(team, OrchestratedTeam):
            roles = ["orchestrator", "worker", "synthesizer"]
            roles += ["verifier"] if team.verifier_prompt is not None else []
        elif name == verification.TEAM.name:
            roles = [*(r.name for r in verification.roles_for_config(cfg)), _CLAUSE_AUDITOR_ROLE]
        else:
            assert isinstance(team, StaticPipelineTeam), name
            roles = [role.name for role in team.roles]
        ids.update(agent_key.team_role(name, role).id for role in roles)
    return ids


def expected_agent_ids(cfg: EngineConfig, loop_names: list[str]) -> set[str]:
    ids = {key.id for key in agent_key.STATIC_KEYS}
    ids |= {agent_key.explore_chat(c.key).id for c in CONSULTANTS}
    ids |= {agent_key.operator_session(scope.value).id for scope in Scope}
    ids |= {agent_key.legacy_planning(role.name).id for role in implementation_plan.ROLES}
    ids |= {agent_key.driver_loop(name or "template").id for name in (loop_names or [""])}
    return ids | _team_role_ids(cfg)


def test_every_production_launch_passes_a_non_none_agent() -> None:
    sites = parity.production_call_sites()
    assert {site.function for site in sites} == parity.LAUNCH_FUNCTIONS
    offenders = [
        f"{site.path}:{site.line} {site.function}"
        for site in sites
        if not site.has_agent or site.agent_is_none
    ]
    assert offenders == []


def test_the_launch_scanner_flags_a_launch_that_omits_or_nulls_the_agent(tmp_path: Path) -> None:
    source = (
        "def go(run_agent, run_chat_turn, run_agent_streaming):\n"
        "    run_agent('p', 'u')\n"
        "    run_chat_turn(agent=None)\n"
        "    run_agent_streaming('p', 'u', agent=KEY)\n"
        "    other_call(agent=None)\n"
        "    handlers['x']()\n"
    )
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "skipped.py").write_text("run_agent('p', 'u')\n", encoding="utf-8")
    sites = parity.production_call_sites(tmp_path)
    assert [(s.function, s.has_agent, s.agent_is_none) for s in sites] == [
        ("run_agent", False, False),
        ("run_chat_turn", True, True),
        ("run_agent_streaming", True, False),
    ]
    assert all(isinstance(site, CallSite) for site in sites)


def test_argv_is_only_ever_built_by_the_three_launch_modules() -> None:
    callers: set[str] = set()
    for path in sorted(parity.ALAN_ROOT.rglob("*.py")):
        relative = path.relative_to(parity.ALAN_ROOT)
        if "web" in relative.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_argv"
            ):
                callers.add(relative.as_posix())
    assert callers == {"claude_cli.py", "driver_loop_cmd.py", "operator_launch.py"}


def test_every_static_agent_key_constant_is_enumerated() -> None:
    tree = ast.parse((parity.ALAN_ROOT / "agent_key.py").read_text(encoding="utf-8"))
    declared = {
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", None) == "AgentKey"
    }
    enumerated = {
        name for name in dir(agent_key) if getattr(agent_key, name) in agent_key.STATIC_KEYS
    }
    assert declared == enumerated


def test_every_registered_team_is_classified_by_this_gate() -> None:
    classified = {module.TEAM.name for module in _LLM_TEAM_MODULES} | _NO_LLM_TEAMS
    assert classified == set(TEAMS) | {"triage"}


@pytest.fixture(params=["empty", "populated"])
def loop_registry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[str]:
    directory = tmp_path / "driver-loops"
    directory.mkdir()
    if request.param == "populated":
        standalone_drivers.write_driver_loop_entry(directory, tmp_path)
    standalone_drivers.use_driver_loop_registry(monkeypatch, directory)
    return ["parity-loop"] if request.param == "populated" else []


def _customised_engine() -> EngineConfig:
    base = default_engine_config()
    roles = {
        **base.roles,
        "implementer": RoleConfig(model="haiku", effort="low"),
        "judge": RoleConfig(model="haiku", effort="low"),
        "diff_reviewer": RoleConfig(model="opus", effort="medium"),
    }
    return base.model_copy(update={"roles": roles})


_ENGINE_VARIANTS: dict[str, Callable[[], EngineConfig]] = {
    "default": default_engine_config,
    "customised": _customised_engine,
}


def _is_template(agent: ResolvedAgent) -> bool:
    return agent.key == agent_key.driver_loop("template")


def _resolved_by_id(cfg: EngineConfig) -> dict[str, ResolvedAgent]:
    resolved, errors = agent_catalogue.resolve_all(cfg)
    assert errors == []
    return {agent.key.id: agent for agent in resolved}


def test_every_agent_key_has_exactly_one_catalogue_entry(loop_registry: list[str]) -> None:
    cfg = default_engine_config()
    resolved, errors = agent_catalogue.resolve_all(cfg)
    ids = [agent.key.id for agent in resolved]
    assert errors == []
    assert len(ids) == len(set(ids))
    assert set(ids) == expected_agent_ids(cfg, loop_registry)


def _prompt_for(cfg: EngineConfig, key_id: str) -> list[PromptSegmentOut]:
    entries = {entry.id: entry for entry in agent_catalogue.build_catalogue(cfg).entries}
    return entries[key_id].prompt


def _user_text_problems(agent: ResolvedAgent, launch: Captured) -> list[str]:
    text = launch.user_text if launch.user_text is not None else launch.spec.prompt_arg
    if text is None:
        return []
    template = parity.template_regex(segments_from_text(agent.user_message, "user_message"))
    if template.fullmatch(text) is None:
        return [f"{agent.key.id}: user message template does not match the launched text"]
    return []


def _compare(
    agent: ResolvedAgent, prompt: list[PromptSegmentOut], launches: list[Captured]
) -> list[str]:
    if not launches:
        return [f"{agent.key.id}: the driver never launched this agent"]
    problems: list[str] = []
    for launch in launches:
        diffs = parity.spec_differences(agent.spec, launch.spec, agent)
        if diffs:
            problems.append(f"{agent.key.id}: spec differs {diffs}")
        mismatch = parity.prompt_mismatch(agent, prompt, launch.spec)
        if mismatch is not None:
            problems.append(f"{agent.key.id}: {mismatch}")
        problems.extend(_user_text_problems(agent, launch))
    return problems


def _run_all_drivers(
    cfg: EngineConfig, monkeypatch: pytest.MonkeyPatch, workdir: Path
) -> tuple[LaunchRecorder, dict[str, ResolvedAgent], list[str]]:
    resolved = _resolved_by_id(cfg)
    ran: set[str] = set()
    missing: list[str] = []
    with monkeypatch.context() as patch:
        recorder = parity.install_recorder(patch)
        for key_id, agent in resolved.items():
            if _is_template(agent):
                continue
            driver = parity.covering(agent.key, cfg)
            if driver is None:
                missing.append(f"{key_id}: no parity driver")
            elif driver.name not in ran:
                ran.add(driver.name)
                with patch.context() as driver_patch:
                    driver.run(ParityEnv(driver_patch, workdir, recorder, cfg))
    return recorder, resolved, missing


@pytest.mark.parametrize("variant", sorted(_ENGINE_VARIANTS))
def test_the_catalogue_matches_what_every_launch_actually_passes(
    variant: str,
    loop_registry: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _ENGINE_VARIANTS[variant]()
    recorder, resolved, failures = _run_all_drivers(cfg, monkeypatch, tmp_path / "run")
    for key_id, agent in resolved.items():
        if _is_template(agent):
            continue
        launches = recorder.for_agent(agent.key)
        failures.extend(_compare(agent, _prompt_for(cfg, key_id), launches))
    assert failures == [], "\n".join(failures)
    assert recorder.agents() <= {agent.key for agent in resolved.values()}


def test_the_driver_loop_template_describes_no_real_launch(
    loop_registry: list[str],
) -> None:
    resolved = _resolved_by_id(default_engine_config())
    templates = [a for a in resolved.values() if _is_template(a)]
    assert [a.key.id for a in templates] == (
        [] if loop_registry else [agent_key.driver_loop("template").id]
    )
    assert all("driver_loop" in a.tags for a in templates)


def _mutated(cfg: EngineConfig, role: str) -> EngineConfig:
    roles = {**cfg.roles, role: RoleConfig(model=SENTINEL_MODEL, effort=SENTINEL_EFFORT)}
    return cfg.model_copy(update={"roles": roles})


def _launches(recorder: LaunchRecorder, agent: ResolvedAgent) -> list[Captured]:
    launches = recorder.for_agent(agent.key)
    assert launches, agent.key.id
    return launches


def _follows(source: ValueSource, role: str) -> bool:
    return source.kind == "engine_role" and source.engine_role == role


def _expected_effort(role: str, agent: ResolvedAgent, base: LaunchSpec) -> str | None:
    if _follows(agent.effort_source, role):
        return SENTINEL_EFFORT
    base_effort = base.effort
    derived = base_effort == launch_spec.effort_for_model(str(base.model))
    if _follows(agent.model_source, role) and derived:
        return launch_spec.effort_for_model(SENTINEL_MODEL)
    return base_effort


def _source_problems(
    role: str,
    agent: ResolvedAgent,
    base: list[Captured],
    after: list[Captured],
    mutated_agent: ResolvedAgent,
) -> list[str]:
    problems: list[str] = []
    if len(base) != len(after):
        problems.append(f"{agent.key.id}: launch count changed {len(base)} -> {len(after)}")
    for before, mutated in zip(base, after, strict=False):
        expected = {
            "model": SENTINEL_MODEL if _follows(agent.model_source, role) else before.spec.model,
            "effort": _expected_effort(role, agent, before.spec),
        }
        for field, want in expected.items():
            for label, value in (
                ("launch", getattr(mutated.spec, field)),
                ("catalogue", getattr(mutated_agent.spec, field)),
            ):
                if value != want:
                    problems.append(f"{agent.key.id}: {field} {label} {value!r} != {want!r}")
    return problems


def test_declared_model_and_effort_sources_are_proven_by_sentinel_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loop_registry: list[str]
) -> None:
    cfg = default_engine_config()
    base_recorder, base_agents, missing = _run_all_drivers(cfg, monkeypatch, tmp_path / "base")
    assert missing == []
    declared_roles = {
        source.engine_role
        for agent in base_agents.values()
        for source in (agent.model_source, agent.effort_source)
        if source.kind == "engine_role" and source.engine_role is not None
    }
    unread_role = next(role for role in cfg.roles if role not in declared_roles)
    problems: list[str] = []
    for role in sorted(declared_roles) + [unread_role]:
        mutated_cfg = _mutated(cfg, role)
        recorder, mutated_agents, _ = _run_all_drivers(
            mutated_cfg, monkeypatch, tmp_path / f"mutated-{role}"
        )
        for key_id, agent in base_agents.items():
            if _is_template(agent):
                continue
            problems.extend(
                _source_problems(
                    role,
                    agent,
                    _launches(base_recorder, agent),
                    _launches(recorder, agent),
                    mutated_agents[key_id],
                )
            )
    assert problems == [], "\n".join(problems)
    assert declared_roles
