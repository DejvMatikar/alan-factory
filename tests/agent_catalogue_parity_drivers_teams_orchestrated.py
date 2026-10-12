from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial

from alan import agent_key, engine_config, roots
from alan.launch_spec import LaunchSpec
from alan.teams import brainstorming, opportunity_scout, research, ui_review
from alan.teams._orchestrated import (
    ORCHESTRATOR_ROLE,
    SYNTHESIZER_ROLE,
    VERIFIER_ROLE,
    OrchestratedTeam,
)
from alan.workspace import create
from tests import agent_catalogue_parity as parity
from tests._helpers import seed_and_run

TEAMS: tuple[OrchestratedTeam, ...] = (
    research.TEAM,
    brainstorming.TEAM,
    opportunity_scout.TEAM,
    ui_review.TEAM,
)

DISPATCHED_ROLES = ("first_specialist", "second_specialist")
DISPATCH_FIELDS = (
    "SCOPE: one narrow sub-question\n"
    "COVERAGE: the other specialist covers the rest\n"
    "SUCCESS: a concrete answer with numbers\n"
    "CONTEXT: the task description"
)
DISPATCH_INSTRUCTIONS = "Prefer the cheapest option."
STATE_SUBDIR_FOR_DOT = "driver-loop-state"
CREDENTIAL_RULE_ROOTS = frozenset(
    {"ALAN_STATE_ROOT", "ALAN_SECRETS_STORE_ROOT", "ALAN_ENGINES_ROOT"}
)


def dispatch_reply() -> str:
    agents = [{"role": role, "prompt": DISPATCH_FIELDS} for role in DISPATCHED_ROLES]
    return json.dumps({"agents": agents})


def reply_for(team: OrchestratedTeam) -> Callable[[LaunchSpec], str]:
    orchestrator = agent_key.team_role(team.name, ORCHESTRATOR_ROLE)

    def reply(spec: LaunchSpec) -> str:
        return dispatch_reply() if spec.agent == orchestrator else parity.DEFAULT_RESULT_TEXT

    return reply


def isolate_state(env: parity.ParityEnv) -> None:
    for token, subdir in roots.STATE_ROOT_SUBDIRS.items():
        if token in CREDENTIAL_RULE_ROOTS:
            continue
        target = env.tmp_path / (STATE_SUBDIR_FOR_DOT if subdir == "." else subdir)
        env.monkeypatch.setenv(token, str(target))


def run_team(team: OrchestratedTeam, env: parity.ParityEnv) -> None:
    isolate_state(env)
    engine_config.register_replacing(env.cfg)
    env.recorder.reply = reply_for(team)
    workspace = create(
        f"{team.name} parity",
        "Compare the options and recommend one.",
        root=env.tmp_path / "workspaces",
        engine_key=env.cfg.repo.slug,
    )
    seed_and_run(
        team, workspace, None, lambda _event: None, instructions=DISPATCH_INSTRUCTIONS
    )


def launched_roles(team: OrchestratedTeam) -> list[str]:
    verifier = [VERIFIER_ROLE] if team.verifier_prompt is not None else []
    return [ORCHESTRATOR_ROLE, agent_key.WORKER_ROLE, *verifier, SYNTHESIZER_ROLE]


def driver_for(team: OrchestratedTeam) -> parity.Driver:
    keys = [agent_key.team_role(team.name, role) for role in launched_roles(team)]
    return parity.Driver(
        name=f"orchestrated/{team.name}",
        covers=lambda _cfg: keys,
        run=partial(run_team, team),
    )


def drivers() -> list[parity.Driver]:
    return [driver_for(team) for team in TEAMS]


def register() -> None:
    for driver in drivers():
        parity.register(driver)
