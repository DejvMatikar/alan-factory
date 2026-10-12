from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource
from alan.agent_catalogue_models import AgentScope, AgentTag, SessionOut, TurnLimitOut
from alan.agent_catalogue_node import SESSION_PLACEHOLDER
from alan.agent_key import AgentKey
from alan.claude_cli import (
    _DEFAULT_CHAT_ALLOWED_TOOLS,  # pyright: ignore[reportPrivateUsage]
    ProcessConfig,
    _cli_timeout_seconds,  # pyright: ignore[reportPrivateUsage]
    chat_launch_spec,
    streaming_spec,
)
from alan.engine_config import EngineConfig
from alan.launch_spec import LaunchSpec, effort_for_model

HAIKU = "haiku"
SONNET = "sonnet"

SERVICE_WORKING_DIRECTORY = (
    "The alan service's own working directory; the subprocess is started without a "
    "directory of its own."
)

EFFORT_FROM_MODEL = ValueSource(
    kind="builtin",
    detail=(
        "Derived from the model name when the process starts: alan passes an effort only "
        "for opus and sonnet, and nothing for any other model."
    ),
)

NO_SESSION = SessionOut(behaviour="none", detail="A single run; no session is kept or resumed.")

ALLOWLISTED_ENVIRONMENT_NOTE = (
    "Names and categories only; values are never shown. Only the variables listed here are "
    "passed on; nothing else is inherited from the alan service process."
)

CHAT_TIMEOUTS_DETAIL = (
    "Idle ceiling from the chat-turn timeout; absolute ceiling from the service's absolute "
    "worker timeout."
)
STREAMED_TIMEOUTS_DETAIL = (
    "Idle ceiling from the one-shot CLI timeout; absolute ceiling from the service's absolute "
    "worker timeout."
)
ONE_SHOT_TIMEOUTS_DETAIL = (
    "The run is stopped after the one-shot CLI timeout; there is no separate absolute ceiling."
)


def builtin_source(detail: str) -> ValueSource:
    return ValueSource(kind="builtin", detail=detail)


def streamed_helper_spec(system_prompt: str, *, model: str, agent: AgentKey) -> LaunchSpec:
    return streaming_spec(
        system_prompt,
        model=model,
        max_turns=3,
        proc_config=ProcessConfig(timeout_seconds=_cli_timeout_seconds()),
        deny_all_tools=True,
        agent=agent,
    )


def chat_turn_spec(
    key: AgentKey, system_prompt: str, *, model: str, max_turns: int
) -> LaunchSpec:
    return chat_launch_spec(
        model=model,
        session_id=SESSION_PLACEHOLDER,
        resume=False,
        max_turns=max_turns,
        allowed_tools=list(_DEFAULT_CHAT_ALLOWED_TOOLS),
        system_prompt=system_prompt,
        effort=effort_for_model(model),
        cwd=None,
        agent=key,
    )


@dataclass(frozen=True)
class HelperOptions:
    effort_source: ValueSource = EFFORT_FROM_MODEL
    session: SessionOut = NO_SESSION
    working_directory: str = SERVICE_WORKING_DIRECTORY
    cwd_is_runtime: bool = False
    scope: AgentScope = "machine"
    tags: tuple[AgentTag, ...] = ()
    team: str | None = None
    role: str | None = None
    configured_model: str | None = None
    configured_effort: str | None = None


NO_OPTIONS = HelperOptions()


def helper_agent(
    key: AgentKey,
    *,
    name: str,
    when_runs: str,
    spec: LaunchSpec,
    user_message: str,
    model_source: ValueSource,
    timeouts_detail: str,
    options: HelperOptions = NO_OPTIONS,
) -> ResolvedAgent:
    return ResolvedAgent(
        key=key,
        name=name,
        when_runs=when_runs,
        scope=options.scope,
        spec=spec,
        user_message=user_message,
        model_source=model_source,
        effort_source=options.effort_source,
        turn_limit=TurnLimitOut(max_turns=spec.max_turns),
        session=options.session,
        working_directory=options.working_directory,
        tags=options.tags,
        team=options.team,
        role=options.role,
        configured_model=options.configured_model,
        configured_effort=options.configured_effort,
        cwd_is_runtime=options.cwd_is_runtime,
        timeouts_detail=timeouts_detail,
    )


def group(
    key: AgentKey, name: str, resolve: Callable[[EngineConfig], Sequence[ResolvedAgent]]
) -> ResolverGroup:
    return ResolverGroup(id=key.id, name=name, key=key, resolve=resolve)


__all__ = [
    "ALLOWLISTED_ENVIRONMENT_NOTE",
    "CHAT_TIMEOUTS_DETAIL",
    "EFFORT_FROM_MODEL",
    "HAIKU",
    "NO_OPTIONS",
    "NO_SESSION",
    "ONE_SHOT_TIMEOUTS_DETAIL",
    "SERVICE_WORKING_DIRECTORY",
    "SONNET",
    "STREAMED_TIMEOUTS_DETAIL",
    "HelperOptions",
    "builtin_source",
    "chat_turn_spec",
    "group",
    "helper_agent",
    "streamed_helper_spec",
]
