from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.handover import Handover, OpenQuestion
from alan.park import Blocker
from alan.teams._base import Team
from alan.terminal_outcome import (
    _FALLBACK_ESCALATED,  # pyright: ignore[reportPrivateUsage]
    _first_line,  # pyright: ignore[reportPrivateUsage]
)

TEAM_TRANSITION_POLICY: dict[str, frozenset[str]] = {
    team: frozenset(targets)
    for team, targets in _ENGINE_DEFAULT.transition_graph.items()
}

DecisionKind = Literal["enqueue", "await_human", "escalate", "done"]


@dataclass(frozen=True)
class RouterDecision:
    kind: DecisionKind
    team: str | None = None
    reason: str | None = None
    block_class: str | None = None
    blockers: tuple[Blocker, ...] = field(default_factory=tuple)
    eligible_at: float | None = None
    instructions: str | None = None


_CREDENTIAL_PARK_REASON = "credential authentication failure — rotate and re-dispatch"
_CREDENTIAL_PARK_BLOCK_CLASS = "credential"


def _credential_park_decision(handover: Handover) -> RouterDecision:
    """A genuine credential-auth failure (author push, fix-cycle re-push,
    merge, or reviewer review-post) parks the workspace under a distinct
    ParkReason.CREDENTIAL rather than falling into a generic escalation or
    question park — `full_synthesis` already carries the purpose- and
    forge-aware rotate-then-re-dispatch recipe from whichever call site
    classified the failure."""
    detail = (handover.full_synthesis or "").strip() or _CREDENTIAL_PARK_REASON
    blocker = Blocker(
        kind="reason",
        name="credential_blocker",
        detail=detail[:500],
        instruction=detail,
    )
    return RouterDecision(
        kind="await_human",
        reason=_CREDENTIAL_PARK_REASON,
        block_class=_CREDENTIAL_PARK_BLOCK_CLASS,
        blockers=(blocker,),
    )


def _escalation_decision(handover: Handover) -> RouterDecision:
    cause = _first_line(handover.full_synthesis) or _FALLBACK_ESCALATED
    blocker = Blocker(kind="reason", name="escalation", detail=cause)
    return RouterDecision(
        kind="escalate",
        reason=f"escalation handover from {handover.from_team}: {cause}",
        blockers=(blocker,),
    )


def select_next_team(
    handover: Handover,
    teams: dict[str, Team],
    transition_policy: dict[str, frozenset[str]] | None = None,
) -> RouterDecision:
    if handover.credential_failure == "expired_token":
        return _credential_park_decision(handover)
    if handover.handover_type == "escalation":
        return _escalation_decision(handover)
    if handover.handover_type == "terminal":
        return RouterDecision(kind="done", reason="terminal handover")
    policy = transition_policy if transition_policy is not None else TEAM_TRANSITION_POLICY
    blocking_decision = _resolve_blocking(handover, teams, policy)
    if blocking_decision is not None:
        return blocking_decision
    return _resolve_forward(handover, teams, policy)


_HUMAN = "human"


def _resolve_blocking(
    handover: Handover,
    teams: dict[str, Team],
    policy: dict[str, frozenset[str]],
) -> RouterDecision | None:
    blocked = [q for q in handover.open_questions if q.blocking]
    if not blocked:
        return None
    return _route_blocking_question(handover, teams, blocked[0], policy)


def _blocker_for_question(question: OpenQuestion) -> tuple[Blocker, ...]:
    return (Blocker(kind="reason", name=question.id, detail=question.question[:500]),)


def _route_blocking_question(
    handover: Handover,
    teams: dict[str, Team],
    question: OpenQuestion,
    policy: dict[str, frozenset[str]],
) -> RouterDecision:
    if question.resolver_role == _HUMAN:
        return RouterDecision(
            kind="await_human",
            reason=f"blocking question {question.id}",
            blockers=_blocker_for_question(question),
        )
    if _can_route_to_resolver(handover, teams, question, policy):
        return RouterDecision(
            kind="enqueue",
            team=question.resolver_role,
            reason=f"blocking question {question.id} routed to resolver",
        )
    return RouterDecision(
        kind="await_human",
        reason=f"unresolvable blocking question {question.id}",
        blockers=_blocker_for_question(question),
    )


def _can_route_to_resolver(
    handover: Handover,
    teams: dict[str, Team],
    question: OpenQuestion,
    policy: dict[str, frozenset[str]],
) -> bool:
    if question.resolver_role not in teams:
        return False
    allowed = policy.get(handover.from_team, frozenset())
    return question.resolver_role in allowed


def _resolve_forward(
    handover: Handover,
    teams: dict[str, Team],
    policy: dict[str, frozenset[str]],
) -> RouterDecision:
    if handover.from_team not in policy:
        return RouterDecision(
            kind="escalate",
            reason=f"unknown from_team: {handover.from_team}",
        )
    allowed = policy[handover.from_team]
    target = _pick_forward_target(handover, teams, allowed)
    if target is not None:
        return RouterDecision(
            kind="enqueue", team=target, reason="forward via suggestions"
        )
    if not allowed:
        return RouterDecision(kind="done", reason="terminal team")
    return RouterDecision(kind="await_human", reason="no allowed suggestion")


def _pick_forward_target(
    handover: Handover, teams: dict[str, Team], allowed: frozenset[str]
) -> str | None:
    next_hop = handover.next_hop
    if next_hop is not None and next_hop in allowed and next_hop in teams:
        return next_hop
    for suggestion in handover.to_team_suggestions:
        if suggestion in allowed and suggestion in teams:
            return suggestion
    return None
