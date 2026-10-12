from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class DispatchSurface(StrEnum):
    UNRECORDED = "unrecorded"
    API_DIRECT = "api_direct"
    BUILD_CHAT = "build_chat"
    EXPLORE_CHAT = "explore_chat"
    TRIAGE_SWEEP = "triage_sweep"
    DEP_SWEEP_PARENT = "dep_sweep_parent"
    DEP_SWEEP_PR = "dep_sweep_pr"
    DEP_ADVISORY_CHECK = "dep_advisory_check"
    DEP_ADVISORY_FIX = "dep_advisory_fix"
    PROJECT_AUTO_CONTINUE = "project_auto_continue"


class DispatchProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    surface: DispatchSurface = DispatchSurface.UNRECORDED
    actor: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )
    prep_chat_session_id: str | None = None

    @classmethod
    def for_request(
        cls, surface: DispatchSurface, actor: str | None, prep_chat_session_id: str | None
    ) -> DispatchProvenance:
        return cls(surface=surface, actor=actor, prep_chat_session_id=prep_chat_session_id)


UNRECORDED: Final[DispatchProvenance] = DispatchProvenance(surface=DispatchSurface.UNRECORDED)
