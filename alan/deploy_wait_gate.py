from __future__ import annotations

import time

from alan.deploy_verify import GatePath, GateRecord
from alan.handover import DeployWaitSignal, Handover
from alan.router import RouterDecision

_DEPLOY_VERIFY_TEAM = "deploy_verify"


def apply(handover: Handover, decision: RouterDecision) -> RouterDecision:
    wait = _active_wait(handover, decision)
    if wait is None:
        return decision
    return _reenqueue_decision(wait)


def _active_wait(handover: Handover, decision: RouterDecision) -> DeployWaitSignal | None:
    if decision.kind != "done" or handover.from_team != _DEPLOY_VERIFY_TEAM:
        return None
    return handover.deploy_wait


def _reenqueue_decision(wait: DeployWaitSignal) -> RouterDecision:
    is_race_wait = wait.race_deadline is not None
    eligible_at = wait.race_deadline if is_race_wait else wait.deadline
    path: GatePath = "awaiting_restart" if is_race_wait else "awaiting_live"
    record = GateRecord(
        merge_sha=wait.merge_sha,
        path=path,
        deferred_at=time.time(),
        deadline=None if is_race_wait else wait.deadline,
        race_deadline=wait.race_deadline,
        merged_at=None if is_race_wait else wait.merged_at,
        repo=None if is_race_wait else wait.repo,
    )
    return RouterDecision(
        kind="enqueue",
        team=_DEPLOY_VERIFY_TEAM,
        reason=wait.reason,
        eligible_at=eligible_at,
        instructions=record.to_json(),
    )


__all__ = ["apply"]
