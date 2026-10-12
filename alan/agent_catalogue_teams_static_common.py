from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alan import agent_key
from alan.agent_catalogue_helpers import (
    ROLE_OUTPUT_NONCE,
    ResolvedAgent,
    ResolverGroup,
    ValueSource,
    runtime_slot,
    with_flags_and_dirs,
)
from alan.agent_catalogue_models import ConditionalDirOut, FlagOut
from alan.agent_catalogue_node import NodeOptions, team_node
from alan.engine_config import EngineConfig
from alan.launch_spec import effort_for_model
from alan.lifecycle import Status
from alan.teams._base import render_allowed_tools
from alan.teams._static_pipeline import (
    Role,
    StaticPipelineTeam,
    _coverage_block,  # pyright: ignore[reportPrivateUsage]
    assemble_role_system_prompt,
    build_role_user_text,
)
from alan.workspace import EngineSnapshot, Meta, Workspace

TASK_TITLE_SLOT = runtime_slot("PIPELINE_TASK_TITLE", "Title of the workspace's task")
TASK_DESCRIPTION_SLOT = runtime_slot(
    "PIPELINE_TASK_DESCRIPTION", "Description of the workspace's task"
)
DISPATCH_INSTRUCTIONS_SLOT = runtime_slot(
    "PIPELINE_DISPATCH_INSTRUCTIONS",
    "The operator's free-form dispatch instructions, under the heading "
    "'## User dispatch instructions'; left out when none were given",
)
PRIOR_HANDOVER_SLOT = runtime_slot(
    "PIPELINE_PRIOR_HANDOVER",
    "The previous team's handover (its synthesis and, from the second iteration on, earlier "
    "cross-team context); left out when this team runs first",
)
DEFAULT_BASE_TAIL = DISPATCH_INSTRUCTIONS_SLOT + PRIOR_HANDOVER_SLOT

WORKING_DIRECTORY = (
    "The alan service's own working directory (the install checkout); this role sets no "
    "checkout override."
)
MODEL_DETAIL = (
    "Fixed in the team's built-in role table; this repository's engine roles are not consulted."
)
EFFORT_DETAIL = (
    "alan passes no explicit effort for this role. For opus the effort is derived from the "
    f"model as {effort_for_model('opus')}, for sonnet as {effort_for_model('sonnet')}; any "
    "other model leaves the CLI default."
)

_BUILTIN_MODEL = ValueSource(kind="builtin", detail=MODEL_DETAIL)
_DERIVED_EFFORT = ValueSource(kind="builtin", detail=EFFORT_DETAIL)
_TEMPLATE_TASK_ID = "template"
_TEMPLATE_PATH = Path("/nonexistent/alan-agent-catalogue-template")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RoleDetails:
    when_runs: str
    base_tail: str = DEFAULT_BASE_TAIL
    user_prefix: str = ""
    claimed_grants: tuple[str, ...] = ()
    conditional_dirs: tuple[ConditionalDirOut, ...] = ()
    flags: tuple[FlagOut, ...] = ()


def template_workspace(cfg: EngineConfig) -> Workspace:
    meta = Meta(
        task_id=_TEMPLATE_TASK_ID,
        title=TASK_TITLE_SLOT,
        description=TASK_DESCRIPTION_SLOT,
        status=Status.ACTIVE,
        created_at=_EPOCH,
        updated_at=_EPOCH,
        engine_snapshot=EngineSnapshot(
            posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
        ),
    )
    return Workspace(task_id=_TEMPLATE_TASK_ID, path=_TEMPLATE_PATH, meta=meta)


def _prior_output_slots(team: StaticPipelineTeam, role: Role) -> list[tuple[str, str]]:
    stages = team._effective_stages()  # pyright: ignore[reportPrivateUsage]
    position = [role.name in stage for stage in stages].index(True)
    earlier = [name for stage in stages[:position] for name in stage]
    return [
        (
            name,
            runtime_slot(
                f"{team.name}_{name}_OUTPUT".upper(),
                f"Full output of the {name} role in the {team.name} team, produced earlier in "
                "this pipeline",
            ),
        )
        for name in earlier
    ]


def _user_message(
    team: StaticPipelineTeam, role: Role, workspace: Workspace, details: RoleDetails
) -> str:
    base = (
        team._build_base_user_text(workspace, None)  # pyright: ignore[reportPrivateUsage]
        + details.base_tail
    )
    text = build_role_user_text(
        base,
        _prior_output_slots(team, role),
        _coverage_block(role.name, team.roles),
        nonce=ROLE_OUTPUT_NONCE,
    )
    return details.user_prefix + text


def _claimed_grant_flags(
    team: StaticPipelineTeam, details: RoleDetails, effective: tuple[str, ...]
) -> tuple[FlagOut, ...]:
    return tuple(
        FlagOut(
            code="other_mismatch",
            severity="warn",
            title="Prompt relies on a tool the engine does not grant",
            detail=(
                f"This role's prompt says `{grant}` is pre-approved in its tool allowlist, but "
                f"the effective tool list for the {team.name} team does not contain it."
            ),
        )
        for grant in details.claimed_grants
        if grant not in effective
    )


def static_role_agent(
    team: StaticPipelineTeam,
    role: Role,
    cfg: EngineConfig,
    workspace: Workspace,
    details: RoleDetails,
    extra_shared_blocks: tuple[tuple[str, str], ...] = (),
) -> ResolvedAgent:
    agent = team_node(
        team=team.name,
        role=role.name,
        system_prompt=assemble_role_system_prompt(
            team._role_system_prompt(role, workspace),  # pyright: ignore[reportPrivateUsage]
            terse=team._role_terse(role.name),  # pyright: ignore[reportPrivateUsage]
        ),
        user_message=_user_message(team, role, workspace, details),
        model=role.model,
        effort=None,
        model_source=_BUILTIN_MODEL,
        effort_source=_DERIVED_EFFORT,
        when_runs=details.when_runs,
        working_directory=WORKING_DIRECTORY,
        overrides=team._role_overrides(role.name, workspace),  # pyright: ignore[reportPrivateUsage]
        options=NodeOptions(
            stored_tools=render_allowed_tools(cfg, team.name),
            extra_shared_blocks=extra_shared_blocks,
        ),
    )
    return with_flags_and_dirs(
        agent,
        (*details.flags, *_claimed_grant_flags(team, details, agent.spec.allowed_tools)),
        details.conditional_dirs,
    )


def static_team_group(
    team: StaticPipelineTeam,
    details: Mapping[str, RoleDetails],
    extra_shared_blocks: tuple[tuple[str, str], ...] = (),
) -> ResolverGroup:
    def resolve(cfg: EngineConfig) -> list[ResolvedAgent]:
        workspace = template_workspace(cfg)
        return [
            static_role_agent(
                team, role, cfg, workspace, details[role.name], extra_shared_blocks
            )
            for role in team.roles
        ]

    return ResolverGroup(
        id=f"static_pipeline:{team.name}",
        name=f"{team.name} team",
        key=agent_key.team_role(team.name, team.roles[0].name),
        resolve=resolve,
    )


__all__ = [
    "DEFAULT_BASE_TAIL",
    "DISPATCH_INSTRUCTIONS_SLOT",
    "EFFORT_DETAIL",
    "MODEL_DETAIL",
    "PRIOR_HANDOVER_SLOT",
    "TASK_DESCRIPTION_SLOT",
    "TASK_TITLE_SLOT",
    "WORKING_DIRECTORY",
    "RoleDetails",
    "static_role_agent",
    "static_team_group",
    "template_workspace",
]
