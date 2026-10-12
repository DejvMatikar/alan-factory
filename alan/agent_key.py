from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

AgentKind = Literal["team_role", "chat", "background_helper", "operator_launched"]

AGENT_KINDS: Final[tuple[AgentKind, ...]] = (
    "team_role",
    "chat",
    "background_helper",
    "operator_launched",
)

ORCHESTRATED_TEAMS: Final[frozenset[str]] = frozenset(
    {"research", "brainstorming", "opportunity_scout", "ui_review"}
)
ORCHESTRATED_FIXED_ROLES: Final[frozenset[str]] = frozenset(
    {"orchestrator", "synthesizer", "verifier"}
)
WORKER_ROLE: Final = "worker"

_EXPLORE_PREFIX: Final = "explore/"
_LEGACY_PLANNING_PREFIX: Final = "legacy_planning/"
_DRIVER_LOOP_PREFIX: Final = "driver_loop/"
_OPERATOR_PREFIX: Final = "operator/"


@dataclass(frozen=True, order=True)
class AgentKey:
    kind: AgentKind
    name: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}"


def canonical_team_role(team: str, role: str) -> str:
    if team in ORCHESTRATED_TEAMS and role not in ORCHESTRATED_FIXED_ROLES:
        return WORKER_ROLE
    return role


def team_role(team: str, role: str) -> AgentKey:
    return AgentKey("team_role", f"{team}/{canonical_team_role(team, role)}")


def explore_chat(consultant_key: str) -> AgentKey:
    return AgentKey("chat", f"{_EXPLORE_PREFIX}{consultant_key}")


def legacy_planning(role: str) -> AgentKey:
    return AgentKey("team_role", f"{_LEGACY_PLANNING_PREFIX}{role}")


def driver_loop(name: str) -> AgentKey:
    return AgentKey("operator_launched", f"{_DRIVER_LOOP_PREFIX}{name}")


def operator_session(scope: str) -> AgentKey:
    return AgentKey("operator_launched", f"{_OPERATOR_PREFIX}{scope}")


BUILD_CHAT: Final = AgentKey("chat", "build")
COLLISION: Final = AgentKey("background_helper", "collision")
MANAGER_SUMMARY: Final = AgentKey("background_helper", "manager_summary")
INVESTIGATOR: Final = AgentKey("background_helper", "investigator")
HANDOVER_COMPACTOR: Final = AgentKey("background_helper", "handover_compactor")
ENGINE_CONFIG_PROPOSAL: Final = AgentKey("background_helper", "engine_config_proposal")
JUDGE_EVAL: Final = AgentKey("background_helper", "judge_eval")
PLANNER_GRADER_EVAL: Final = AgentKey("background_helper", "planner_grader_eval")
IMPLEMENTATION_DRIFT_BACKSTOP: Final = AgentKey(
    "team_role", "implementation/drift_backstop"
)

STATIC_KEYS: Final[tuple[AgentKey, ...]] = (
    BUILD_CHAT,
    COLLISION,
    MANAGER_SUMMARY,
    INVESTIGATOR,
    HANDOVER_COMPACTOR,
    ENGINE_CONFIG_PROPOSAL,
    JUDGE_EVAL,
    PLANNER_GRADER_EVAL,
    IMPLEMENTATION_DRIFT_BACKSTOP,
)

__all__ = [
    "AGENT_KINDS",
    "BUILD_CHAT",
    "COLLISION",
    "ENGINE_CONFIG_PROPOSAL",
    "HANDOVER_COMPACTOR",
    "IMPLEMENTATION_DRIFT_BACKSTOP",
    "INVESTIGATOR",
    "JUDGE_EVAL",
    "MANAGER_SUMMARY",
    "ORCHESTRATED_FIXED_ROLES",
    "ORCHESTRATED_TEAMS",
    "PLANNER_GRADER_EVAL",
    "STATIC_KEYS",
    "WORKER_ROLE",
    "AgentKey",
    "AgentKind",
    "canonical_team_role",
    "driver_loop",
    "explore_chat",
    "legacy_planning",
    "operator_session",
    "team_role",
]
