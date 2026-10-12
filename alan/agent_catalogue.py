from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Sequence

from alan import (
    agent_catalogue_teams,
    agent_catalogue_teams_impl,
    agent_catalogue_teams_orchestrated,
)
from alan.agent_catalogue_flags import agent_flags, unread_role_flags
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, error_entry, to_entry
from alan.agent_catalogue_models import (
    AgentCatalogue,
    AgentCatalogueSummary,
    AgentEntry,
    CatalogueNotes,
    CountOut,
    FlagOut,
    InheritedItem,
)
from alan.agent_catalogue_standalone import GROUPS as _STANDALONE_GROUPS
from alan.engine_config import EngineConfig

logger = logging.getLogger("alan.agent_catalogue")

APPENDED_PROMPT_NOTE = (
    "The text shown for each agent is appended to Claude Code's own built-in system prompt. "
    "That built-in prompt is not reproduced here."
)

INHERITED_ITEMS: tuple[InheritedItem, ...] = (
    InheritedItem(
        name="Claude Code user-level settings",
        note="Settings from the Claude Code installation on this machine; contents not shown.",
    ),
    InheritedItem(
        name="Repository guidance file",
        note=(
            "The guidance file Claude Code loads from the agent's working directory; "
            "contents not shown."
        ),
    ),
    InheritedItem(
        name="User-level permissions",
        note="Permission rules configured in Claude Code on this machine; contents not shown.",
    ),
    InheritedItem(
        name="Hooks",
        note="User-level hooks configured in Claude Code on this machine; contents not shown.",
    ),
    InheritedItem(
        name="MCP servers",
        note="MCP servers configured in Claude Code on this machine; contents not shown.",
    ),
    InheritedItem(
        name="Plugins",
        note="Plugins installed in Claude Code on this machine; contents not shown.",
    ),
)

NOTES = CatalogueNotes(appended_prompt=APPENDED_PROMPT_NOTE, inherited=list(INHERITED_ITEMS))


def resolver_groups() -> tuple[ResolverGroup, ...]:
    return (
        *agent_catalogue_teams.GROUPS,
        *agent_catalogue_teams_orchestrated.GROUPS,
        *agent_catalogue_teams_impl.GROUPS,
        *_STANDALONE_GROUPS,
    )


def _failed(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _resolve_group(
    group: ResolverGroup, cfg: EngineConfig
) -> tuple[list[ResolvedAgent], list[AgentEntry]]:
    try:
        return list(group.resolve(cfg)), []
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent catalogue resolver %s failed", group.id)
        return [], [error_entry(group.key, group.name, _failed(exc))]


def resolve_all(
    cfg: EngineConfig, groups: Iterable[ResolverGroup] | None = None
) -> tuple[list[ResolvedAgent], list[AgentEntry]]:
    resolved: list[ResolvedAgent] = []
    errors: list[AgentEntry] = []
    for group in groups if groups is not None else resolver_groups():
        agents, failures = _resolve_group(group, cfg)
        resolved.extend(agents)
        errors.extend(failures)
    return resolved, errors


def _entry_for(agent: ResolvedAgent, cfg: EngineConfig) -> AgentEntry:
    try:
        return to_entry(agent, agent_flags(agent, cfg))
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent catalogue entry %s failed", agent.key.id)
        return error_entry(agent.key, agent.name, _failed(exc))


def _unread_flags(resolved: Sequence[ResolvedAgent], cfg: EngineConfig) -> list[FlagOut]:
    try:
        return unread_role_flags(resolved, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent catalogue unread-role check failed")
        return [
            FlagOut(
                code="other_mismatch",
                severity="warn",
                title="Could not check for unread stored engine roles",
                detail=_failed(exc),
            )
        ]


def build_catalogue(cfg: EngineConfig) -> AgentCatalogue:
    resolved, errors = resolve_all(cfg)
    entries = [_entry_for(agent, cfg) for agent in resolved]
    return AgentCatalogue(
        engine=cfg.repo.slug,
        notes=NOTES,
        entries=[*entries, *errors],
        flags=_unread_flags(resolved, cfg),
    )


def _counts(names: Sequence[str]) -> list[CountOut]:
    return [CountOut(name=name, count=count) for name, count in sorted(Counter(names).items())]


def _severity_count(flags: Sequence[FlagOut], severity: str) -> int:
    return sum(1 for flag in flags if flag.severity == severity)


def _working_entries(catalogue: AgentCatalogue) -> list[AgentEntry]:
    return [entry for entry in catalogue.entries if entry.error is None]


def _all_flags(catalogue: AgentCatalogue, working: Sequence[AgentEntry]) -> list[FlagOut]:
    flags = list(catalogue.flags)
    for entry in working:
        flags.extend(entry.flags)
    return flags


def summarize(catalogue: AgentCatalogue) -> AgentCatalogueSummary:
    working = _working_entries(catalogue)
    flags = _all_flags(catalogue, working)
    return AgentCatalogueSummary(
        engine=catalogue.engine,
        total=len(catalogue.entries),
        by_model=_counts([entry.model.display for entry in working]),
        by_effort=_counts([entry.effort.display for entry in working]),
        by_kind=_counts([entry.kind for entry in working]),
        warnings=_severity_count(flags, "warn"),
        infos=_severity_count(flags, "info"),
        errors=len(catalogue.entries) - len(working),
    )


__all__ = [
    "APPENDED_PROMPT_NOTE",
    "INHERITED_ITEMS",
    "NOTES",
    "build_catalogue",
    "resolve_all",
    "resolver_groups",
    "summarize",
]
