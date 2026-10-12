from __future__ import annotations

from fastapi.concurrency import run_in_threadpool

import alan.merge_gate as merge_gate_mod
import alan.workspace as workspace_mod
from alan.handover import Handover
from alan.merge_recovery import MergeBlockClass
from alan.park import blockers_from_readiness
from alan.teams._pr_ready import PrReadiness

_MANUAL_DISPATCH_SKIP_REASON = (
    "no prior handover, or the prior handover carries no pull-request number to gate against"
)
_CREATE_DISPATCH_SKIP_REASON = (
    "a freshly created workspace has no prior handover and no pull-request number yet"
)


def _attach_block_class_to_instructions(
    instructions: str | None,
    block_class: MergeBlockClass,
    readiness: PrReadiness,
) -> str:
    blockers = blockers_from_readiness(
        failing_checks=readiness.failing_checks,
        pending_checks=readiness.pending_checks,
        failed_reasons=readiness.failed_reasons,
        review_decision=readiness.review_decision,
        merge_state_status=readiness.merge_state_status,
        quarantined_checks=readiness.quarantined_checks,
    )
    brief = merge_gate_mod.render_remediation_instructions(
        block_class, "; ".join(readiness.failed_reasons), tuple(blockers),
    )
    return f"{instructions}\n\n{brief}" if instructions else brief


async def apply_merge_gate_to_manual_dispatch(
    ws: workspace_mod.Workspace, prior: Handover | None, instructions: str | None,
) -> tuple[merge_gate_mod.MergeGateDecision, str | None]:
    if prior is None or prior.pr_number is None:
        return merge_gate_mod.MergeGateDecision.skipped(_MANUAL_DISPATCH_SKIP_REASON), instructions
    attempt = await run_in_threadpool(
        merge_gate_mod.attempt_recovery, ws, prior.pr_number, recovered_outcome="dispatch",
    )
    match attempt:
        case merge_gate_mod.MergeGateBlocked():
            await run_in_threadpool(
                merge_gate_mod.record_merge_gate_audit,
                ws.task_id, prior.pr_number, attempt.block_class, False, "dispatch",
                merge_gate_mod.reconcile_outcome_str(attempt.reconcile),
            )
            instructions = _attach_block_class_to_instructions(
                instructions, attempt.block_class, attempt.readiness
            )
        case (
            merge_gate_mod.MergeGateNotAttempted()
            | merge_gate_mod.MergeGateAttemptFailed()
            | merge_gate_mod.MergeGateRecovered()
        ):  # pragma: no branch
            pass
    return merge_gate_mod.MergeGateDecision.attempted(attempt), instructions


def create_dispatch_skip_decision() -> merge_gate_mod.MergeGateDecision:
    return merge_gate_mod.MergeGateDecision.skipped(_CREATE_DISPATCH_SKIP_REASON)
