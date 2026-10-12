from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ScopeGapKind = Literal[
    "sibling_code",
    "in_flight_pr",
    "prior_workspace",
    "invariant_risk",
    "handover_relative",
    "anti_scope",
]

SuggestedAction = Literal["expand_scope", "out_of_scope", "needs_user_call"]


class ScopeGapFinding(BaseModel):
    kind: ScopeGapKind
    summary: str = Field(min_length=1, max_length=2000)
    references: list[str] = Field(default_factory=list, max_length=10)
    suggested_action: SuggestedAction


class CounterCase(BaseModel):
    against: str = Field(min_length=1, max_length=2000)
    cheapest_80: str = Field(min_length=1, max_length=2000)
    cost_estimate: str = Field(min_length=1, max_length=2000)
