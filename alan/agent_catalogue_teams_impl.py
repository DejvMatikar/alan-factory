from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from alan import agent_catalogue_teams_impl_slots as slots
from alan import agent_key
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource
from alan.agent_catalogue_models import FlagOut, FlagSeverity, SessionOut, TurnLimitOut
from alan.agent_catalogue_node import NodeOptions, team_node
from alan.agent_catalogue_teams_impl_common import (
    NO_GUIDANCE_CHECKOUT,
    engine_effort_source,
    engine_model_source,
    prompt_overridden,
    worktree_shared_blocks,
)
from alan.agent_catalogue_teams_impl_user import (
    implementation_fix_user_message,
    implementation_user_message,
)
from alan.agent_catalogue_teams_impl_verification import VERIFICATION_GROUP
from alan.claude_cli import ProcessConfig, ProcessMode, streaming_spec
from alan.claude_cli_grants import ACCEPT_EDITS_MODE
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig, RoleConfig
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS, FLEET_NICE_DELTA
from alan.teams import (
    _pr_body,  # pyright: ignore[reportPrivateUsage]
    _pr_forge,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams import implementation as _impl
from alan.teams import implementation_fix as _fix
from alan.teams._base import WorkerOverrides, render_allowed_tools

_ROLE = "implementer"


@dataclass(frozen=True)
class _WorktreeTeam:
    name: str
    max_turns: int
    default_prompt: str
    compose_generic: Callable[[EngineConfig, Path], str]
    default_role: RoleConfig
    user_message: Callable[[], str]
    when_runs: str
    working_directory: str


_IMPLEMENTATION = _WorktreeTeam(
    name=_impl._TEAM_NAME,  # pyright: ignore[reportPrivateUsage]
    max_turns=_impl._MAX_TURNS,  # pyright: ignore[reportPrivateUsage]
    default_prompt=_impl._SYSTEM_PROMPT,  # pyright: ignore[reportPrivateUsage]
    compose_generic=_impl._compose_generic_system_prompt,  # pyright: ignore[reportPrivateUsage]
    default_role=_impl._DEFAULT_ROLE,  # pyright: ignore[reportPrivateUsage]
    user_message=implementation_user_message,
    when_runs=(
        "Executes the approved plan: reads the planned files, writes code and tests, commits "
        "and stops. The host then pushes the branch and opens the pull request."
    ),
    working_directory=(
        "A checkout leased for this run (a pre-warmed pool slot, or a fresh git worktree of "
        "origin/main); its path is chosen at run time."
    ),
)
_IMPLEMENTATION_FIX = _WorktreeTeam(
    name=_fix._TEAM_NAME,  # pyright: ignore[reportPrivateUsage]
    max_turns=_fix._MAX_TURNS,  # pyright: ignore[reportPrivateUsage]
    default_prompt=_fix._SYSTEM_PROMPT,  # pyright: ignore[reportPrivateUsage]
    compose_generic=_fix._compose_generic_system_prompt,  # pyright: ignore[reportPrivateUsage]
    default_role=_fix._DEFAULT_ROLE,  # pyright: ignore[reportPrivateUsage]
    user_message=implementation_fix_user_message,
    when_runs=(
        "Runs when review of an open pull request asks for changes: addresses each judge "
        "finding, unresolved review thread and failing check as its own commit, then stops. "
        "The host pushes the branch and verification runs again."
    ),
    working_directory=(
        "A checkout leased for this run with the pull request's branch checked out; its "
        "path is chosen at run time."
    ),
)

_BACKSTOP_SESSION = SessionOut(
    behaviour="resumable",
    detail=(
        "Always resumes the implementer node's recorded CLI session, so the model keeps its "
        "context. When the node recorded no session id the backstop is skipped."
    ),
)
_BACKSTOP_TIMEOUTS_DETAIL = (
    f"Fleet idle ceiling {FLEET_IDLE_TIMEOUT_SECONDS:g} s; absolute ceiling from the "
    "service's absolute worker timeout."
)
_BACKSTOP_PROCESS_DETAIL = (
    f"Fleet worker: runs at reduced OS priority (+{FLEET_NICE_DELTA}). Unlike a workflow node "
    "it is never started detached, so it does not survive a service restart."
)
_BACKSTOP_TURN_NOTE = (
    "Fixed ceiling. Adaptive turn budgets and the appended turn-budget note do not apply to "
    "this launch."
)


def _worktree_prompt(cfg: EngineConfig, team: _WorktreeTeam) -> str:
    base = (
        team.default_prompt
        if cfg == _ENGINE_DEFAULT
        else team.compose_generic(cfg, NO_GUIDANCE_CHECKOUT) + slots.REPOSITORY_GUIDANCE
    )
    return _pr_body.render_with_pr_body_instructions(
        cfg,
        team.name,
        _ROLE,
        base,
        environment_block=_impl.environment_block_for(cfg, team.name),
        success_block=_impl.generic_success_block(cfg),
        repo_guidance_block=slots.REPOSITORY_GUIDANCE,
    )


def _worktree_overrides(cfg: EngineConfig, team: _WorktreeTeam) -> WorkerOverrides:
    return WorkerOverrides(
        cwd=None,
        allowed_tools=list(render_allowed_tools(cfg, team.name)),
        permission_mode=ACCEPT_EDITS_MODE,
        max_turns=team.max_turns,
    )


def _worktree_agent(cfg: EngineConfig, team: _WorktreeTeam) -> ResolvedAgent:
    model, effort = _pr_forge.role_model_effort(cfg, _ROLE, team.default_role)
    fallback = f"{team.default_role.model} / {team.default_role.effort or 'no effort'}"
    return team_node(
        team=team.name,
        role=_ROLE,
        system_prompt=_worktree_prompt(cfg, team),
        user_message=team.user_message(),
        model=model,
        effort=effort,
        model_source=engine_model_source(cfg, _ROLE, fallback),
        effort_source=engine_effort_source(cfg, _ROLE, fallback),
        when_runs=team.when_runs,
        working_directory=team.working_directory,
        overrides=_worktree_overrides(cfg, team),
        options=NodeOptions(
            stored_tools=render_allowed_tools(cfg, team.name),
            prompt_overridden=prompt_overridden(cfg, team.name, _ROLE),
            extra_shared_blocks=worktree_shared_blocks(cfg, team.name),
        ),
    )


def _backstop_flag(cfg: EngineConfig) -> FlagOut:
    severity: FlagSeverity = "info" if cfg == _ENGINE_DEFAULT else "warn"
    return FlagOut(
        code="other_mismatch",
        severity=severity,
        title="Ignores the engine's implementer role and prompt",
        detail=(
            "This launch always uses the built-in model "
            f"({_impl._MODEL}) and effort ({_impl._EFFORT}) "  # pyright: ignore[reportPrivateUsage]
            "together with the default engine's implementer system prompt. It never reads "
            "the engine's 'implementer' role, this engine's prompt composition or its prompt "
            "override, so on any other engine it can differ from what the implementer itself "
            "ran with."
        ),
    )


def _backstop_agent(cfg: EngineConfig) -> ResolvedAgent:
    role = cfg.roles.get(_ROLE)
    spec = streaming_spec(
        _impl._SYSTEM_PROMPT,  # pyright: ignore[reportPrivateUsage]
        model=_impl._MODEL,  # pyright: ignore[reportPrivateUsage]
        effort=_impl._EFFORT,  # pyright: ignore[reportPrivateUsage]
        max_turns=_impl._BACKSTOP_MAX_TURNS,  # pyright: ignore[reportPrivateUsage]
        proc_config=ProcessConfig(cwd=None, timeout_seconds=FLEET_IDLE_TIMEOUT_SECONDS),
        resume_session_id=slots.IMPLEMENTER_SESSION,
        allowed_tools=list(render_allowed_tools(cfg, _impl._TEAM_NAME)),  # pyright: ignore[reportPrivateUsage]
        permission_mode=ACCEPT_EDITS_MODE,
        mode=ProcessMode(fleet=True),
        agent=agent_key.IMPLEMENTATION_DRIFT_BACKSTOP,
    )
    builtin = "Built-in constant in the implementation team module, not the engine's role."
    return ResolvedAgent(
        key=agent_key.IMPLEMENTATION_DRIFT_BACKSTOP,
        name="implementation / drift backstop",
        when_runs=(
            "After the implementer finishes with commits, when the drift gate finds a "
            "load-bearing file changed without a documentation update: resumes the "
            "implementer's session once so it adds the missing documentation and commits."
        ),
        scope="repo",
        spec=spec,
        user_message=_impl._BACKSTOP_USER_TEXT,  # pyright: ignore[reportPrivateUsage]
        model_source=ValueSource(kind="builtin", detail=builtin),
        effort_source=ValueSource(kind="builtin", detail=builtin),
        turn_limit=TurnLimitOut(
            max_turns=_impl._BACKSTOP_MAX_TURNS,  # pyright: ignore[reportPrivateUsage]
            adaptive=False,
            note=_BACKSTOP_TURN_NOTE,
        ),
        session=_BACKSTOP_SESSION,
        working_directory=(
            "The same leased checkout the implementer just worked in; its path is chosen at "
            "run time."
        ),
        stored_tools=render_allowed_tools(cfg, _impl._TEAM_NAME),  # pyright: ignore[reportPrivateUsage]
        team=_impl._TEAM_NAME,  # pyright: ignore[reportPrivateUsage]
        role="drift_backstop",
        configured_model=role.model if role is not None else None,
        configured_effort=role.effort if role is not None else None,
        extra_flags=(_backstop_flag(cfg),),
        timeouts_detail=_BACKSTOP_TIMEOUTS_DETAIL,
        process_detail=_BACKSTOP_PROCESS_DETAIL,
    )


def _resolve_implementation(cfg: EngineConfig) -> list[ResolvedAgent]:
    return [_worktree_agent(cfg, _IMPLEMENTATION), _backstop_agent(cfg)]


def _resolve_implementation_fix(cfg: EngineConfig) -> list[ResolvedAgent]:
    return [_worktree_agent(cfg, _IMPLEMENTATION_FIX)]


GROUPS: tuple[ResolverGroup, ...] = (
    ResolverGroup(
        id="team:implementation",
        name="implementation team",
        key=agent_key.team_role(_IMPLEMENTATION.name, _ROLE),
        resolve=_resolve_implementation,
    ),
    ResolverGroup(
        id="team:implementation_fix",
        name="implementation_fix team",
        key=agent_key.team_role(_IMPLEMENTATION_FIX.name, _ROLE),
        resolve=_resolve_implementation_fix,
    ),
    VERIFICATION_GROUP,
)

__all__ = ["GROUPS"]
