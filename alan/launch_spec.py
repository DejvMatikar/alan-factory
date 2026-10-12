from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal, cast

from alan.agent_key import AgentKey
from alan.claude_cli_grants import (
    ACCEPT_EDITS_MODE,
    disallowed_tools_args,
    narrowed_tools,
)

CLAUDE_EXECUTABLE: Final = "claude"
APPEND_SYSTEM_PROMPT_FLAG: Final = "--append-system-prompt"

Layout = Literal["agent", "chat", "driver_loop", "operator"]
SessionMode = Literal["new", "resumed", "none"]
EnvCategory = Literal[
    "fixed_override",
    "sandbox_redirect",
    "cache_redirect",
    "secret_removed",
    "fleet_ceiling",
    "allowlisted",
]

LABEL_SYSTEM_PROMPT: Final = "system_prompt"
LABEL_TURN_BUDGET: Final = "turn_budget"
LABEL_HEADLESS: Final = "headless_execution"
LABEL_PROJECT_DOC: Final = "project_doc"
LABEL_NO_TOOLS: Final = "no_tools"

TURN_BUDGET_PREFIX: Final = "\n\nTurn budget for this run: "
_TURN_BUDGET_TAIL_RE: Final = re.compile(
    rf"(?P<tail>{re.escape(TURN_BUDGET_PREFIX)}\d+)\Z"
)

_OPUS_EFFORT: Final = "xhigh"
_SONNET_EFFORT: Final = "high"
_RULE_RE: Final = re.compile(r"(?:[^\s(]|\([^)]*\))+")


@dataclass(frozen=True)
class PromptSegment:
    label: str
    text: str
    shared: bool = False


@dataclass(frozen=True)
class EnvEntry:
    name: str
    category: EnvCategory


@dataclass(frozen=True)
class ProcessSpec:
    cwd: str | None = None
    idle_timeout_seconds: float | None = None
    abs_timeout_seconds: float | None = None
    fleet: bool = False
    detached: bool = False
    priority_delta: int | None = None
    env: tuple[EnvEntry, ...] = ()


@dataclass(frozen=True)
class LaunchSpec:
    layout: Layout
    agent: AgentKey | None = None
    model: str | None = None
    effort: str | None = None
    output_format: str | None = None
    max_turns: int | None = None
    permission_mode: str | None = None
    prompt: tuple[PromptSegment, ...] = ()
    prompt_arg: str | None = None
    requested_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    add_dirs: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    deny_all_tools: bool = False
    tool_gate: tuple[str, ...] = ()
    session_mode: SessionMode = "none"
    session_id: str | None = None
    extra: tuple[str, ...] = ()
    process: ProcessSpec = ProcessSpec()


def with_process(spec: LaunchSpec, process: ProcessSpec) -> LaunchSpec:
    return cast(LaunchSpec, replace(spec, process=process))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def with_add_dirs(spec: LaunchSpec, add_dirs: Sequence[str]) -> LaunchSpec:
    return cast(LaunchSpec, replace(spec, add_dirs=tuple(add_dirs)))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def effort_for_model(model: str) -> str | None:
    if model == "opus":
        return _OPUS_EFFORT
    if model == "sonnet":
        return _SONNET_EFFORT
    return None


def resolve_effort(effort: str | None, model: str) -> str | None:
    return effort if effort is not None else effort_for_model(model)


def appended_prompt(spec: LaunchSpec) -> str | None:
    if not spec.prompt:
        return None
    return "".join(segment.text for segment in spec.prompt)


def effective_allowed_tools(
    allowed_tools: Sequence[str] | None, permission_mode: str
) -> tuple[str, ...]:
    tools = list(allowed_tools or [])
    if permission_mode == ACCEPT_EDITS_MODE:
        return tuple(tools)
    return tuple(narrowed_tools(tools))


def flat_denied_tools(denied: Sequence[str]) -> tuple[str, ...]:
    return tuple(rule for token in denied for rule in _RULE_RE.findall(token))


def split_turn_budget(system_prompt: str) -> tuple[PromptSegment, ...]:
    match = _TURN_BUDGET_TAIL_RE.search(system_prompt)
    if match is None:
        return (PromptSegment(LABEL_SYSTEM_PROMPT, system_prompt),)
    head = system_prompt[: match.start()]
    return (
        PromptSegment(LABEL_SYSTEM_PROMPT, head),
        PromptSegment(LABEL_TURN_BUDGET, match.group("tail")),
    )


def _session(session_id: str | None, resume_session_id: str | None) -> tuple[
    SessionMode, str | None
]:
    if resume_session_id is not None:
        return "resumed", resume_session_id
    if session_id is not None:
        return "new", session_id
    return "none", None


def agent_spec(
    prompt: Sequence[PromptSegment],
    *,
    output_format: str,
    max_turns: int,
    model: str = "sonnet",
    effort: str | None = None,
    extra: Sequence[str] | None = None,
    allowed_tools: Sequence[str] | None = None,
    add_dirs: Sequence[str] | None = None,
    permission_mode: str = "auto",
    deny_all_tools: bool = False,
    session_id: str | None = None,
    resume_session_id: str | None = None,
    agent: AgentKey | None = None,
) -> LaunchSpec:
    mode, sid = _session(session_id, resume_session_id)
    return LaunchSpec(
        layout="agent",
        agent=agent,
        model=model,
        effort=effort,
        output_format=output_format,
        max_turns=max_turns,
        permission_mode=permission_mode,
        prompt=tuple(prompt),
        requested_tools=tuple(allowed_tools or ()),
        allowed_tools=(
            () if deny_all_tools else effective_allowed_tools(allowed_tools, permission_mode)
        ),
        add_dirs=() if deny_all_tools else tuple(add_dirs or ()),
        denied=tuple(
            disallowed_tools_args(
                deny_all_tools=deny_all_tools, permission_mode=permission_mode
            )[1:]
        ),
        deny_all_tools=deny_all_tools,
        session_mode=mode,
        session_id=sid,
        extra=tuple(extra or ()),
    )


def chat_spec(
    *,
    model: str,
    session_id: str,
    resume: bool,
    max_turns: int,
    allowed_tools: Sequence[str],
    system_prompt: str,
    effort: str | None = None,
    agent: AgentKey | None = None,
) -> LaunchSpec:
    return LaunchSpec(
        layout="chat",
        agent=agent,
        model=model,
        effort=effort,
        output_format="stream-json",
        max_turns=max_turns,
        permission_mode="auto",
        prompt=(PromptSegment(LABEL_SYSTEM_PROMPT, system_prompt),),
        requested_tools=tuple(allowed_tools),
        allowed_tools=effective_allowed_tools(allowed_tools, "auto"),
        denied=tuple(disallowed_tools_args(deny_all_tools=False, permission_mode="auto")[1:]),
        session_mode="resumed" if resume else "new",
        session_id=session_id,
    )


def _grant_tokens(spec: LaunchSpec) -> list[str]:
    tokens: list[str] = []
    if spec.allowed_tools:
        tokens.extend(["--allowedTools", *spec.allowed_tools])
    if spec.add_dirs:
        tokens.extend(["--add-dir", *spec.add_dirs])
    return tokens


def _session_tokens(spec: LaunchSpec) -> list[str]:
    if spec.session_mode == "resumed":
        return ["--resume", str(spec.session_id)]
    if spec.session_mode == "new":
        return ["--session-id", str(spec.session_id)]
    return []


def _effort_tokens(spec: LaunchSpec) -> list[str]:
    return [] if spec.effort is None else ["--effort", spec.effort]


def _agent_argv(spec: LaunchSpec) -> list[str]:
    return [
        CLAUDE_EXECUTABLE,
        "-p",
        "--model",
        str(spec.model),
        "--output-format",
        str(spec.output_format),
        "--max-turns",
        str(spec.max_turns),
        "--permission-mode",
        str(spec.permission_mode),
        APPEND_SYSTEM_PROMPT_FLAG,
        str(appended_prompt(spec)),
        *_effort_tokens(spec),
        *_grant_tokens(spec),
        "--disallowedTools",
        *spec.denied,
        *_session_tokens(spec),
        *spec.extra,
    ]


def _chat_argv(spec: LaunchSpec) -> list[str]:
    return [
        CLAUDE_EXECUTABLE,
        "-p",
        "--model",
        str(spec.model),
        "--output-format",
        str(spec.output_format),
        "--verbose",
        "--max-turns",
        str(spec.max_turns),
        "--permission-mode",
        str(spec.permission_mode),
        *_grant_tokens(spec),
        "--disallowedTools",
        *spec.denied,
        *_effort_tokens(spec),
        *_session_tokens(spec),
        APPEND_SYSTEM_PROMPT_FLAG,
        str(appended_prompt(spec)),
    ]


def _driver_loop_argv(spec: LaunchSpec) -> list[str]:
    argv = [
        CLAUDE_EXECUTABLE,
        "-p",
        str(spec.prompt_arg),
        "--permission-mode",
        str(spec.permission_mode),
        "--tools",
        *spec.tool_gate,
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--output-format",
        str(spec.output_format),
        "--verbose",
    ]
    if spec.allowed_tools:
        argv.extend(["--allowedTools", *spec.allowed_tools])
    argv.extend(["--disallowedTools", *spec.denied])
    if spec.add_dirs:
        argv.extend(["--add-dir", *spec.add_dirs])
    if spec.model:
        argv.extend(["--model", spec.model])
    return argv


def _operator_argv(spec: LaunchSpec) -> list[str]:
    return [
        CLAUDE_EXECUTABLE,
        *([spec.prompt_arg] if spec.prompt_arg is not None else []),
        *(["--add-dir", *spec.add_dirs] if spec.add_dirs else []),
        "--disallowedTools",
        *spec.denied,
    ]


_ARGV_BUILDERS = {
    "agent": _agent_argv,
    "chat": _chat_argv,
    "driver_loop": _driver_loop_argv,
    "operator": _operator_argv,
}


def to_argv(spec: LaunchSpec) -> list[str]:
    return _ARGV_BUILDERS[spec.layout](spec)


__all__ = [
    "APPEND_SYSTEM_PROMPT_FLAG",
    "CLAUDE_EXECUTABLE",
    "LABEL_HEADLESS",
    "LABEL_NO_TOOLS",
    "LABEL_PROJECT_DOC",
    "LABEL_SYSTEM_PROMPT",
    "LABEL_TURN_BUDGET",
    "TURN_BUDGET_PREFIX",
    "EnvCategory",
    "EnvEntry",
    "LaunchSpec",
    "Layout",
    "ProcessSpec",
    "PromptSegment",
    "SessionMode",
    "agent_spec",
    "appended_prompt",
    "chat_spec",
    "effective_allowed_tools",
    "effort_for_model",
    "flat_denied_tools",
    "resolve_effort",
    "split_turn_budget",
    "to_argv",
    "with_add_dirs",
    "with_process",
]
