from __future__ import annotations

from alan.handover import Handover
from alan.router import RouterDecision
from alan.sticky_instructions import NON_CONSUMING_TEAMS, resolve
from alan.workspace import Workspace

_BRIEF_REFINE_TEAM = "brief_refine"


def apply(ws: Workspace, handover: Handover, decision: RouterDecision) -> RouterDecision:
    if handover.from_team != _BRIEF_REFINE_TEAM:
        return decision
    target = ws.meta.pending_first_hop
    if target is None:
        return decision
    instructions = None
    if target in NON_CONSUMING_TEAMS:
        sticky = resolve(ws, ws.task_id)
        instructions = sticky.text if sticky is not None else None
    return RouterDecision(
        kind="enqueue",
        team=target,
        reason="brief refinement complete",
        instructions=instructions,
    )


__all__ = ["apply"]
