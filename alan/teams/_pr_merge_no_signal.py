from __future__ import annotations

from typing import Any

from alan.engine_config import ForgeTarget
from alan.teams import _pr_ready
from alan.workspace import Workspace

CHANGES_REQUESTED_BLOCKER = "changes_requested"
BLOCKED_NO_SIGNAL_TIMEOUT_RECIPE = (
    "This PR reads mergeStateStatus=BLOCKED with no failing checks, no pending "
    "checks and no outstanding CHANGES_REQUESTED review, so nothing in this diff "
    "explains the block. Check whether a required status context never reported, "
    "a review conversation is unresolved, or a branch-protection rule is "
    "unsatisfied — none of those live in the diff — then re-dispatch pr_merge."
)


def settled_review_block(readiness: _pr_ready.ReadinessResult) -> bool:
    return (
        isinstance(readiness, _pr_ready.PrReadiness)
        and not readiness.failing_checks
        and not readiness.pending_checks
        and readiness.review_decision == "CHANGES_REQUESTED"
    )


def _timeout_state_line(readiness: _pr_ready.PrReadiness) -> str:
    if settled_review_block(readiness):
        return (
            "Recorded state at give-up: no failing and no pending checks, but "
            "review decision CHANGES_REQUESTED stands at head "
            f"{readiness.head_sha[:12] or 'unknown'} — that change-request "
            "review, not the checks, is what blocks the merge. Dismiss it on "
            "GitHub if it is stale, or address the requested changes and "
            "re-request review, then re-dispatch pr_merge."
        )
    return (
        f"Recorded state at give-up: {len(readiness.failing_checks)} failing "
        f"check(s), {len(readiness.pending_checks)} pending check(s), review "
        f"decision {readiness.review_decision or 'none'}."
    )


def timeout_ctx(
    workspace: Workspace, pr_number: int, elapsed: float, polls: int,
    *, forge_target: ForgeTarget,
) -> dict[str, Any]:
    readiness = _pr_ready.readiness_or_unavailable(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    ctx: dict[str, Any] = {
        "elapsed": round(elapsed, 1),
        "polls": polls,
        "blocked_no_signal": False,
    }
    if isinstance(readiness, _pr_ready.PrReadiness):
        ctx["blocked_no_signal"] = _pr_ready.is_blocked_without_signal(readiness)
        ctx["timeout_state"] = _timeout_state_line(readiness)
    if settled_review_block(readiness):
        ctx["timeout_blocker"] = CHANGES_REQUESTED_BLOCKER
    return ctx


def timeout_recipe(ctx: dict[str, Any], default: str) -> str:
    if ctx.get("blocked_no_signal"):
        return BLOCKED_NO_SIGNAL_TIMEOUT_RECIPE
    timeout_state = ctx.get("timeout_state")
    return str(timeout_state) if timeout_state else default

