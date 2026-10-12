from __future__ import annotations

from alan.router import RouterDecision
from alan.teams import deploy_verify


def deploy_gate_decision(pr_number: int) -> RouterDecision:
    reason, eligible_at, instructions = deploy_verify.restart_gate_terms(pr_number)
    return RouterDecision(
        kind="enqueue",
        team="deploy_verify",
        reason=reason,
        eligible_at=eligible_at,
        instructions=instructions,
    )


__all__ = ["deploy_gate_decision"]
