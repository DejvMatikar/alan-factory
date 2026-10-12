from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from alan.scope_audit import CounterCase, ScopeGapFinding
from alan.yaml_normalize import FreeTextList

LEGACY_ACTION_TO_TEAM: dict[str, str] = {
    "implement": "implementation_plan",
    "research": "research",
    "brainstorm": "brainstorming",
    "architecture": "architecture",
    "maintenance": "maintenance",
    "verification": "verification",
    "scout": "opportunity_scout",
}


NEW_WORKSPACE_TEAM = "implementation_plan"

ALLOWED_NEW_WORKSPACE_TEAMS_CONTEXT_KEY = "allowed_new_workspace_teams"


def _resolve_allowed_new_workspace_teams(
    context: Mapping[str, Any] | None,
) -> frozenset[str]:
    if context is None:
        return frozenset({NEW_WORKSPACE_TEAM})
    allowed = context.get(ALLOWED_NEW_WORKSPACE_TEAMS_CONTEXT_KEY)
    if not allowed:
        return frozenset({NEW_WORKSPACE_TEAM})
    return frozenset(allowed)


def _empty_scope_audit() -> list[ScopeGapFinding]:
    return []


def _empty_acceptance_clauses() -> list[str]:
    return []


def _map_legacy_dispatch_action(typed: dict[str, Any]) -> dict[str, Any]:
    legacy = typed.get("action")
    if not isinstance(legacy, str):
        return typed
    mapped = LEGACY_ACTION_TO_TEAM.get(legacy, legacy)
    return {**{k: v for k, v in typed.items() if k != "action"}, "team": mapped}


class DispatchProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    team: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1, max_length=4000)
    instructions: str | None = Field(default=None, max_length=40000)
    scope_audit: list[ScopeGapFinding] = Field(default_factory=_empty_scope_audit, max_length=20)
    counter_case: CounterCase
    title: str | None = Field(default=None, max_length=50)
    acceptance_clauses: FreeTextList = Field(default_factory=_empty_acceptance_clauses)
    recorded_clauses: FreeTextList = Field(default_factory=_empty_acceptance_clauses)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_action(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        typed = cast(dict[str, Any], data)
        if "team" in typed or "action" not in typed:
            return typed
        return _map_legacy_dispatch_action(typed)

    @model_validator(mode="after")
    def _validate_new_workspace_shape(self, info: ValidationInfo) -> "DispatchProposal":
        if self.title is not None:
            if not self.title.strip():
                raise ValueError("title must not be blank when present")
            allowed_teams = _resolve_allowed_new_workspace_teams(info.context)
            if self.team not in allowed_teams:
                raise ValueError(
                    "a proposal carrying a title (new-workspace dispatch) must "
                    f"target a team in {sorted(allowed_teams)!r}, got {self.team!r}"
                )
        elif self.acceptance_clauses or self.recorded_clauses:
            raise ValueError(
                "acceptance_clauses and recorded_clauses require a title (new-workspace proposal)"
            )
        return self
