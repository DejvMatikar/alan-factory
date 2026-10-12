from __future__ import annotations

from collections.abc import Sequence

from alan.agent_catalogue_helpers import ResolvedAgent
from alan.agent_catalogue_models import FlagCode, FlagOut, FlagSeverity
from alan.engine_config import EngineConfig, RoleConfig

_NO_VALUE = "CLI default"


def _shown(value: str | None) -> str:
    return value if value is not None else _NO_VALUE


def _flag(code: FlagCode, severity: FlagSeverity, title: str, detail: str) -> FlagOut:
    return FlagOut(code=code, severity=severity, title=title, detail=detail)


def _hinted_role(resolved: ResolvedAgent, cfg: EngineConfig) -> tuple[str, RoleConfig] | None:
    name = resolved.engine_role_hint or resolved.role
    if name is None:
        return None
    role = cfg.roles.get(name)
    return None if role is None else (name, role)


def _severity(configured: str | None, effective: str | None) -> FlagSeverity:
    return "info" if configured == effective else "warn"


def _ignores_roles(resolved: ResolvedAgent, cfg: EngineConfig) -> list[FlagOut]:
    hinted = _hinted_role(resolved, cfg)
    if hinted is None or resolved.model_source.kind == "engine_role":
        return []
    name, role = hinted
    effective = resolved.spec.model
    return [
        _flag(
            "ignores_engine_roles",
            _severity(role.model, effective),
            "Ignores the engine's role settings",
            f"Engine role '{name}' is set to model {role.model}, but this agent starts with "
            f"{_shown(effective)} regardless of that setting.",
        )
    ]


def _ignores_effort(resolved: ResolvedAgent, cfg: EngineConfig) -> list[FlagOut]:
    hinted = _hinted_role(resolved, cfg)
    if hinted is None or resolved.effort_source.kind == "engine_role":
        return []
    name, role = hinted
    if role.effort is None:
        return []
    effective = resolved.spec.effort
    return [
        _flag(
            "ignores_engine_effort",
            _severity(role.effort, effective),
            "Ignores the engine's effort",
            f"Engine role '{name}' is set to effort {role.effort}, but this agent starts with "
            f"effort {_shown(effective)} regardless of that setting.",
        )
    ]


def _legacy(resolved: ResolvedAgent) -> list[FlagOut]:
    if "legacy" not in resolved.tags:
        return []
    return [
        _flag(
            "legacy_planning_endpoint",
            "warn",
            "Legacy standalone planning endpoint",
            "Nothing in the app calls this endpoint; it runs every role on the one-shot "
            "default model regardless of the role's configured model. Removal is a separate "
            "follow-up.",
        )
    ]


def _configured_mismatches(resolved: ResolvedAgent) -> list[FlagOut]:
    flags: list[FlagOut] = []
    spec = resolved.spec
    if resolved.configured_model is not None and resolved.configured_model != spec.model:
        flags.append(
            _flag(
                "model_mismatch",
                "warn",
                "Configured model differs from the launched model",
                f"Configured: {resolved.configured_model}. Launched with: {_shown(spec.model)}.",
            )
        )
    if resolved.configured_effort is not None and resolved.configured_effort != spec.effort:
        flags.append(
            _flag(
                "effort_mismatch",
                "warn",
                "Configured effort differs from the launched effort",
                f"Configured: {resolved.configured_effort}. "
                f"Launched with: {_shown(spec.effort)}.",
            )
        )
    return flags


def agent_flags(resolved: ResolvedAgent, cfg: EngineConfig) -> list[FlagOut]:
    return [
        *_ignores_roles(resolved, cfg),
        *_ignores_effort(resolved, cfg),
        *_legacy(resolved),
        *_configured_mismatches(resolved),
        *resolved.extra_flags,
    ]


def _read_role_names(resolved: Sequence[ResolvedAgent]) -> set[str]:
    names: set[str] = set()
    for agent in resolved:
        for source in (agent.model_source, agent.effort_source):
            if source.kind == "engine_role" and source.engine_role is not None:
                names.add(source.engine_role)
    return names


def unread_role_flags(resolved: Sequence[ResolvedAgent], cfg: EngineConfig) -> list[FlagOut]:
    read = _read_role_names(resolved)
    return [
        _flag(
            "unread_stored_role",
            "warn",
            f"Stored engine role '{name}' is not read by anything",
            f"The engine stores role '{name}' (model {role.model}, effort "
            f"{_shown(role.effort)}), but no agent reads it when it starts.",
        )
        for name, role in sorted(cfg.roles.items())
        if name not in read
    ]


__all__ = ["agent_flags", "unread_role_flags"]
