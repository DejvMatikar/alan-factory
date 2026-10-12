from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from alan import agent_key
from alan.agent_catalogue_helpers import ResolvedAgent, ValueSource, runtime_slot
from alan.agent_catalogue_models import (
    AgentScope,
    AgentTag,
    FlagOut,
    SessionOut,
    TurnLimitOut,
)
from alan.agent_key import AgentKey
from alan.claude_cli import ProcessConfig, ProcessMode, streaming_spec
from alan.detached_mode import DIAL_ENV, resolve_detached_mode
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS, FLEET_NICE_DELTA
from alan.launch_spec import TURN_BUDGET_PREFIX, LaunchSpec
from alan.teams import _base
from alan.teams._base import MAX_TURNS, WorkerOverrides

SESSION_PLACEHOLDER = "generated-at-run-time"
_ADAPTIVE_NOTE = (
    "The static ceiling is the upper bound. With adaptive turns enabled the applied "
    "ceiling is lowered from this role's recent turn history and never raised above it."
)
_FLEET_PROCESS_DETAIL = (
    f"Fleet worker: runs at reduced OS priority (+{FLEET_NICE_DELTA}); starts in its own "
    f"process session when detached mode is on ({DIAL_ENV}) so it survives a service restart."
)
_TIMEOUTS_DETAIL = (
    f"Fleet idle ceiling {FLEET_IDLE_TIMEOUT_SECONDS:g} s; absolute ceiling from the "
    "service's absolute worker timeout."
)
_NODE_SESSION = SessionOut(
    behaviour="resumable",
    detail=(
        "Starts a new CLI session with a generated id; the id is recorded so an interrupted "
        "node resumes the same session after a restart."
    ),
)


def turn_budget_slot() -> str:
    return runtime_slot(
        "TURN_BUDGET",
        "Turn ceiling applied to this run: the static cap, or lower when adaptive turns apply",
    )


def turn_limit(max_turns: int) -> TurnLimitOut:
    adaptive = _base._adaptive_turns_enabled()  # pyright: ignore[reportPrivateUsage]
    return TurnLimitOut(
        max_turns=max_turns,
        adaptive=adaptive,
        note=_ADAPTIVE_NOTE,
    )


def _node_spec(
    key: AgentKey,
    system_prompt: str,
    model: str,
    effort: str | None,
    max_turns: int,
    opts: WorkerOverrides,
) -> LaunchSpec:
    return streaming_spec(
        f"{system_prompt}{TURN_BUDGET_PREFIX}{turn_budget_slot()}",
        model=model,
        effort=effort,
        max_turns=max_turns,
        proc_config=ProcessConfig(cwd=opts.cwd, timeout_seconds=FLEET_IDLE_TIMEOUT_SECONDS),
        session_id=SESSION_PLACEHOLDER,
        mode=ProcessMode(fleet=True, journal_stream=resolve_detached_mode()),
        agent=key,
        **_base._overrides_to_kwargs(opts),  # pyright: ignore[reportPrivateUsage]
    )


@dataclass(frozen=True)
class NodeOptions:
    stored_tools: Sequence[str] = ()
    tags: tuple[AgentTag, ...] = ()
    scope: AgentScope = "repo"
    prompt_overridden: bool = False
    configured_model: str | None = None
    configured_effort: str | None = None
    engine_role_hint: str | None = None
    extra_flags: tuple[FlagOut, ...] = ()
    extra_shared_blocks: tuple[tuple[str, str], ...] = ()


def team_node(
    *,
    team: str,
    role: str,
    system_prompt: str,
    user_message: str,
    model: str,
    effort: str | None,
    model_source: ValueSource,
    effort_source: ValueSource,
    when_runs: str,
    working_directory: str,
    overrides: WorkerOverrides | None,
    options: NodeOptions,
) -> ResolvedAgent:
    opts = overrides or WorkerOverrides()
    max_turns = opts.max_turns if opts.max_turns is not None else MAX_TURNS
    key = agent_key.team_role(team, role)
    spec = _node_spec(key, system_prompt, model, effort, max_turns, opts)
    return ResolvedAgent(
        key=key,
        name=f"{team} / {role}",
        when_runs=when_runs,
        scope=options.scope,
        spec=spec,
        user_message=user_message,
        model_source=model_source,
        effort_source=effort_source,
        turn_limit=turn_limit(max_turns),
        session=_NODE_SESSION,
        working_directory=working_directory,
        stored_tools=tuple(options.stored_tools),
        tags=options.tags,
        team=team,
        role=role,
        prompt_overridden=options.prompt_overridden,
        configured_model=options.configured_model,
        configured_effort=options.configured_effort,
        engine_role_hint=options.engine_role_hint,
        extra_flags=options.extra_flags,
        extra_shared_blocks=options.extra_shared_blocks,
        cwd_is_runtime=opts.cwd is None,
        timeouts_detail=_TIMEOUTS_DETAIL,
        process_detail=_FLEET_PROCESS_DETAIL,
    )


__all__ = [
    "SESSION_PLACEHOLDER",
    "NodeOptions",
    "team_node",
    "turn_budget_slot",
    "turn_limit",
]
