from __future__ import annotations

from alan import claude_cli
from alan.agent_catalogue_helpers import ResolvedAgent, ValueSource, runtime_slot
from alan.agent_catalogue_models import (
    AgentScope,
    AgentTag,
    FlagOut,
    SessionOut,
    TurnLimitOut,
)
from alan.agent_key import AgentKey, AgentKind


def demo_slot() -> str:
    return runtime_slot("DEMO_SLOT", "Value inserted by the demo agent at run time")


def make_resolved(
    name: str = "demo",
    *,
    kind: AgentKind = "background_helper",
    model: str = "sonnet",
    effort: str | None = None,
    deny_all_tools: bool = False,
    model_source: ValueSource | None = None,
    effort_source: ValueSource | None = None,
    role: str | None = None,
    engine_role_hint: str | None = None,
    configured_model: str | None = None,
    configured_effort: str | None = None,
    tags: tuple[AgentTag, ...] = (),
    scope: AgentScope = "repo",
    extra_flags: tuple[FlagOut, ...] = (),
    stored_tools: tuple[str, ...] = (),
    prompt_overridden: bool = False,
) -> ResolvedAgent:
    key = AgentKey(kind, name)
    spec = claude_cli.streaming_spec(
        f"You are {name}. {demo_slot()}",
        model=model,
        effort=effort,
        max_turns=7,
        allowed_tools=["Read", "Bash(uv run:*)"],
        deny_all_tools=deny_all_tools,
        agent=key,
    )
    return ResolvedAgent(
        key=key,
        name=name,
        when_runs="when the demo runs",
        scope=scope,
        spec=spec,
        user_message=f"Task: {demo_slot()}",
        model_source=model_source or ValueSource("builtin", "built in"),
        effort_source=effort_source or ValueSource("builtin", "derived from the model"),
        turn_limit=TurnLimitOut(max_turns=7),
        session=SessionOut(behaviour="new", detail="each run starts fresh"),
        working_directory="the service working directory",
        stored_tools=stored_tools,
        tags=tags,
        team=None,
        role=role,
        prompt_overridden=prompt_overridden,
        configured_model=configured_model,
        configured_effort=configured_effort,
        engine_role_hint=engine_role_hint,
        extra_flags=extra_flags,
    )

