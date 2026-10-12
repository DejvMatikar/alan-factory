from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from typing import Any

from alan import agent_key, engine_config
from alan.agent_key import AgentKey
from alan.engine_config import EngineConfig
from alan.handover import Handover, TestScenario
from alan.launch_spec import LaunchSpec
from alan.teams import architecture, brief_refine, decomposer, implementation_plan, maintenance
from alan.teams._static_pipeline import StaticPipelineParseError, StaticPipelineTeam
from alan.workspace import Requirement, Workspace, create
from tests import agent_catalogue_parity as parity
from tests._helpers import make_handover, seed_and_run

Reply = Callable[[LaunchSpec], str]

DECISIONS_REPLY = (
    "```yaml decisions.yaml\n"
    "decisions:\n"
    "  - id: d1\n"
    '    statement: "keep one process"\n'
    '    rationale: "simplest thing that works"\n'
    "    confidence: 0.9\n"
    '    reversibility: "medium"\n'
    "```"
)

PLAN_REPLY = (
    "```yaml decisions.yaml\n"
    "decisions:\n"
    "  - id: d1\n"
    '    statement: "keep one process"\n'
    '    rationale: "simplest thing that works"\n'
    "    confidence: 0.9\n"
    '    reversibility: "medium"\n'
    "files_to_edit:\n"
    "  - alan/queue.py\n"
    "test_scenarios:\n"
    "  - id: history_is_readable\n"
    '    description: "The commit history can be listed."\n'
    '    given: "A checkout."\n'
    '    when: "git log"\n'
    '    then: "commit"\n'
    "```"
)

DECOMPOSITION_REPLY = (
    "```yaml decomposition.yaml\n"
    "split_count: 1\n"
    'split_rationale: "one small change"\n'
    "```"
)

BRIEF_REPLY = (
    "```yaml brief_refine.yaml\n"
    "requirements: []\n"
    "assumptions:\n"
    '  - "the brief is complete"\n'
    "```"
)

MAINTENANCE_REPLY = "ROLE_SEVERITY: NONE"


def _covers(team: StaticPipelineTeam) -> Callable[[EngineConfig], Sequence[AgentKey]]:
    return lambda _cfg: [agent_key.team_role(team.name, role.name) for role in team.roles]


def _workspace(
    env: parity.ParityEnv,
    title: str,
    *,
    requirements: list[Requirement] | None = None,
    source_file: bool = False,
) -> Workspace:
    engine_config.register_replacing(env.cfg)
    root = env.tmp_path / "workspaces"
    env.monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(root))
    workspace = create(
        title,
        f"{title} description",
        root=root,
        engine_key=env.cfg.repo.slug,
        requirements=requirements,
    )
    if source_file:
        (workspace.path / brief_refine.SOURCE_FILE_NAME).write_text("source", encoding="utf-8")
    return workspace


def _launch(
    env: parity.ParityEnv,
    team: StaticPipelineTeam,
    workspace: Workspace,
    reply: Reply,
    *,
    prior: Handover | None = None,
    instructions: str | None = None,
) -> None:
    previous = env.recorder.reply
    env.recorder.reply = reply
    try:
        with contextlib.suppress(StaticPipelineParseError):
            seed_and_run(team, workspace, prior, lambda _event: None, instructions=instructions)
    finally:
        env.recorder.reply = previous


def _final_reply(team: StaticPipelineTeam, text: str) -> Reply:
    last = agent_key.team_role(team.name, team.roles[-1].name)
    return lambda spec: text if spec.agent == last else parity.DEFAULT_RESULT_TEXT


def _prior(workspace: Workspace, team: StaticPipelineTeam, **update: Any) -> Handover:
    handover = make_handover(
        workspace.task_id,
        "prior",
        to_team=team.name,
        full_synthesis="prior synthesis",
        decision_statement="reuse the existing queue",
    )
    return handover.model_copy(update=update)


def _run_pipeline(
    env: parity.ParityEnv, team: StaticPipelineTeam, final: str, **prior_update: Any
) -> None:
    reply = _final_reply(team, final)
    _launch(env, team, _workspace(env, f"{team.name} bare"), reply)
    full = _workspace(env, f"{team.name} full")
    _launch(
        env,
        team,
        full,
        reply,
        prior=_prior(full, team, **prior_update),
        instructions="focus on the data layer",
    )


def _run_architecture(env: parity.ParityEnv) -> None:
    _run_pipeline(env, architecture.TEAM, DECISIONS_REPLY)


def _run_implementation_plan(env: parity.ParityEnv) -> None:
    _run_pipeline(env, implementation_plan.TEAM, PLAN_REPLY)


def _run_maintenance(env: parity.ParityEnv) -> None:
    _run_pipeline(env, maintenance.TEAM, MAINTENANCE_REPLY)


def _run_decomposer(env: parity.ParityEnv) -> None:
    scenario = TestScenario(
        id="history_is_readable",
        description="The commit history can be listed.",
        given="A checkout.",
        when="git log",
        then="commit",
    )
    _run_pipeline(
        env,
        decomposer.TEAM,
        DECOMPOSITION_REPLY,
        files_to_edit=["alan/queue.py"],
        test_scenarios=[scenario],
    )


def _carrying_reply(workspace: Workspace) -> str:
    requirements = workspace.meta.requirements or []
    lines = "".join(f'  - id: {r.id}\n    text: "{r.text}"\n' for r in requirements)
    return f"```yaml brief_refine.yaml\nrequirements:\n{lines}assumptions: []\n```"


def _brief_carried(env: parity.ParityEnv, title: str, *, source_file: bool) -> None:
    carried = _workspace(
        env,
        title,
        requirements=[Requirement(text="the export must be csv")],
        source_file=source_file,
    )
    _launch(env, brief_refine.TEAM, carried, lambda _spec: _carrying_reply(carried))


def _run_brief_refine(env: parity.ParityEnv) -> None:
    _launch(env, brief_refine.TEAM, _workspace(env, "brief bare"), lambda _spec: BRIEF_REPLY)
    _brief_carried(env, "brief carried", source_file=False)
    _brief_carried(env, "brief sourced", source_file=True)


def register() -> None:
    drivers: tuple[tuple[StaticPipelineTeam, parity.DriverFn], ...] = (
        (architecture.TEAM, _run_architecture),
        (brief_refine.TEAM, _run_brief_refine),
        (decomposer.TEAM, _run_decomposer),
        (implementation_plan.TEAM, _run_implementation_plan),
        (maintenance.TEAM, _run_maintenance),
    )
    for team, run in drivers:
        parity.register(
            parity.Driver(
                name=f"static_pipeline:{team.name}", covers=_covers(team), run=run
            )
        )
