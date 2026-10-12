from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import cast

from alan import agent_key, explore_chat_repo
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, runtime_slot
from alan.agent_catalogue_models import AgentTag, SessionOut
from alan.agent_catalogue_standalone_common import (
    CHAT_TIMEOUTS_DETAIL,
    HelperOptions,
    builtin_source,
    chat_turn_spec,
    group,
    helper_agent,
)
from alan.agent_key import AgentKey
from alan.claude_cli import (
    BUILD_SUMMARIZE_SENTINEL,
    CONSULTANTS,
    DISPATCH_PROPOSAL_SENTINEL,
    EXPLORE_SUMMARIZE_SENTINEL,
    ConsultantInfo,
    format_build_system_prompt,
    format_explore_system_prompt,
    run_chat_turn,
)
from alan.engine_config import EngineConfig

_CHAT_SESSION = SessionOut(
    behaviour="per_message",
    detail=(
        "The first message of a chat starts a new CLI session; every later message resumes "
        "it. If the session is lost, the stored id is cleared and the next message starts a "
        "fresh one."
    ),
)

_OWN_CHECKOUT = (
    "The alan service's own working directory: this chat is bound to the install's own "
    "repository, so no per-chat checkout is made."
)

_SHOP_STATS_DESCRIPTION = (
    "Measured shop stats section (first-pass approval rate, fix-cycle metric, fleet health), "
    "appended only when the service has stats to include"
)

_CHAT_MODEL_SOURCE = builtin_source(
    "The default of the chat-turn runner; chats never read an engine role."
)


def _run_chat_defaults() -> tuple[str, int]:
    parameters = inspect.signature(run_chat_turn).parameters
    return cast(str, parameters["model"].default), cast(int, parameters["max_turns"].default)


CHAT_MODEL, CHAT_MAX_TURNS = _run_chat_defaults()


def _repo_label_slot() -> str:
    return runtime_slot(
        "CHAT_REPO_LABEL",
        "The repository the chat is bound to (owner/name), or the install's own repository "
        "when the chat is not bound to another one",
    )


def _working_directory(cfg: EngineConfig) -> str:
    if explore_chat_repo.needs_repo(cfg.repo.slug) is None:
        return _OWN_CHECKOUT
    return (
        f"A per-chat clone of {cfg.repo.slug}, made when the chat's first message is sent; "
        "the chat only reads it."
    )


def _chat_agent(
    cfg: EngineConfig,
    key: AgentKey,
    *,
    name: str,
    when_runs: str,
    system_prompt: str,
    user_message: str,
    tags: tuple[AgentTag, ...] = (),
) -> ResolvedAgent:
    return helper_agent(
        key,
        name=name,
        when_runs=when_runs,
        spec=chat_turn_spec(key, system_prompt, model=CHAT_MODEL, max_turns=CHAT_MAX_TURNS),
        user_message=user_message,
        model_source=_CHAT_MODEL_SOURCE,
        timeouts_detail=CHAT_TIMEOUTS_DETAIL,
        options=HelperOptions(
            session=_CHAT_SESSION,
            working_directory=_working_directory(cfg),
            cwd_is_runtime=True,
            scope="repo",
            tags=tags,
        ),
    )


def _build_chat(cfg: EngineConfig) -> list[ResolvedAgent]:
    system_prompt = format_build_system_prompt(
        "", _repo_label_slot()
    ) + runtime_slot("BUILD_CHAT_SHOP_STATS", _SHOP_STATS_DESCRIPTION)
    message = runtime_slot(
        "BUILD_CHAT_MESSAGE",
        "The message the person types. The Summarise action sends the fixed command "
        f"{BUILD_SUMMARIZE_SENTINEL} and the Propose action sends {DISPATCH_PROPOSAL_SENTINEL}; "
        "when the proposal cannot be parsed, the same session is asked once more with a "
        "correction message quoting the parser error",
    )
    return [
        _chat_agent(
            cfg,
            agent_key.BUILD_CHAT,
            name="Build chat",
            when_runs=(
                "Every time a person sends a message, summarises or asks for a dispatch "
                "proposal in a build chat."
            ),
            system_prompt=system_prompt,
            user_message=message,
        )
    ]


def _explore_resolver(
    consultant: ConsultantInfo,
) -> Callable[[EngineConfig], list[ResolvedAgent]]:
    def resolve(cfg: EngineConfig) -> list[ResolvedAgent]:
        message = runtime_slot(
            "EXPLORE_CHAT_MESSAGE",
            "The message the person types. The Summarise action sends the fixed command "
            f"{EXPLORE_SUMMARIZE_SENTINEL} and the Propose action sends "
            f"{DISPATCH_PROPOSAL_SENTINEL}; when the proposal cannot be parsed, the same "
            "session is asked once more with a correction message quoting the parser error",
        )
        return [
            _chat_agent(
                cfg,
                agent_key.explore_chat(consultant.key),
                name=f"Explore chat / {consultant.label}",
                when_runs=(
                    "Every time a person sends a message, summarises or asks for a dispatch "
                    f"proposal in a {consultant.label} explore chat."
                ),
                system_prompt=format_explore_system_prompt(consultant.key, _repo_label_slot()),
                user_message=message,
                tags=("consultant",),
            )
        ]

    return resolve


GROUPS: tuple[ResolverGroup, ...] = (
    group(agent_key.BUILD_CHAT, "Build chat", _build_chat),
    *(
        group(
            agent_key.explore_chat(consultant.key),
            f"Explore chat / {consultant.label}",
            _explore_resolver(consultant),
        )
        for consultant in CONSULTANTS
    ),
)

__all__ = ["CHAT_MAX_TURNS", "CHAT_MODEL", "GROUPS"]
