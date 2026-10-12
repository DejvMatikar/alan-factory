from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from alan.agent_key import AgentKind

AgentScope = Literal["repo", "machine", "default_engine"]
AgentTag = Literal["eval", "driver_loop", "legacy", "consultant", "template"]
ValueSourceKind = Literal["engine_role", "builtin", "cli_default"]
FlagSeverity = Literal["info", "warn"]
FlagCode = Literal[
    "unread_stored_role",
    "ignores_engine_roles",
    "ignores_engine_effort",
    "legacy_planning_endpoint",
    "model_mismatch",
    "effort_mismatch",
    "other_mismatch",
]
SegmentKind = Literal["text", "placeholder"]
SessionBehaviour = Literal["new", "resumable", "per_message", "interactive", "none"]
CLI_DEFAULT_LABEL = "CLI default"


class CatalogueModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PromptSegmentOut(CatalogueModel):
    kind: SegmentKind
    label: str
    text: str = ""
    description: str | None = None
    shared: bool = False


class ValueOut(CatalogueModel):
    value: str | None
    display: str
    source: ValueSourceKind
    source_detail: str | None = None


class TurnLimitOut(CatalogueModel):
    max_turns: int | None
    adaptive: bool = False
    note: str | None = None


class ToolsOut(CatalogueModel):
    stored: list[str] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)


class SessionOut(CatalogueModel):
    behaviour: SessionBehaviour
    detail: str


class TimeoutsOut(CatalogueModel):
    idle_seconds: float | None = None
    absolute_seconds: float | None = None
    detail: str | None = None


class EnvEntryOut(CatalogueModel):
    name: str
    category: str


class ProcessOut(CatalogueModel):
    priority_delta: int | None = None
    detached: bool = False
    detail: str | None = None


class ConditionalDirOut(CatalogueModel):
    label: str
    condition: str


class FlagOut(CatalogueModel):
    code: FlagCode
    severity: FlagSeverity
    title: str
    detail: str


class AgentEntry(CatalogueModel):
    id: str
    name: str
    kind: AgentKind
    tags: list[AgentTag] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    scope: AgentScope
    team: str | None = None
    role: str | None = None
    when_runs: str
    model: ValueOut
    effort: ValueOut
    prompt: list[PromptSegmentOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    prompt_overridden: bool = False
    user_message: list[PromptSegmentOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    turn_limit: TurnLimitOut
    permission_mode: str | None = None
    tools: ToolsOut
    add_dirs: list[str] = Field(default_factory=list)
    conditional_add_dirs: list[ConditionalDirOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    session: SessionOut
    working_directory: str
    timeouts: TimeoutsOut
    environment: list[EnvEntryOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    environment_note: str | None = None
    process: ProcessOut
    flags: list[FlagOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    error: str | None = None


class InheritedItem(CatalogueModel):
    name: str
    note: str


class CatalogueNotes(CatalogueModel):
    appended_prompt: str
    inherited: list[InheritedItem]


class AgentCatalogue(CatalogueModel):
    engine: str
    notes: CatalogueNotes
    entries: list[AgentEntry]
    flags: list[FlagOut] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


class CountOut(CatalogueModel):
    name: str
    count: int


class AgentCatalogueSummary(CatalogueModel):
    engine: str
    total: int
    by_model: list[CountOut]
    by_effort: list[CountOut]
    by_kind: list[CountOut]
    warnings: int
    infos: int
    errors: int


__all__ = [
    "CLI_DEFAULT_LABEL",
    "AgentCatalogue",
    "AgentCatalogueSummary",
    "AgentEntry",
    "AgentScope",
    "AgentTag",
    "CatalogueNotes",
    "ConditionalDirOut",
    "CountOut",
    "EnvEntryOut",
    "FlagCode",
    "FlagOut",
    "FlagSeverity",
    "InheritedItem",
    "PromptSegmentOut",
    "ProcessOut",
    "SegmentKind",
    "SessionBehaviour",
    "SessionOut",
    "TimeoutsOut",
    "ToolsOut",
    "TurnLimitOut",
    "ValueOut",
    "ValueSourceKind",
]
