from __future__ import annotations

from alan.handover import Handover, OpenQuestion
from alan.teams._pr_handover import HandoverContext, escalation_skeleton
from alan.workspace import Workspace


def spiral_escalation_handover(
    workspace: Workspace, prior: Handover, spiral_reason: str
) -> Handover:
    ctx = HandoverContext.build(
        workspace,
        prior,
        prior.from_invocation_id,
        team_name=prior.from_team,
        default_forbidden=tuple(prior.forbidden_actions),
        next_team=tuple(prior.to_team_suggestions),
    )
    skeleton = escalation_skeleton(ctx, spiral_reason, prior.pr_number)
    return skeleton.model_copy(
        update={
            "decisions": prior.decisions,
            "artifacts_produced": prior.artifacts_produced,
        }
    )


FIX_CYCLE_OVERRIDE_OPTION = (
    "Grant three more implementation_fix cycles for this pull request and "
    "re-dispatch implementation_fix now."
)
FIX_CYCLE_HAND_RESOLVE_OPTION = (
    "Resolve the blocker by hand outside the fleet, then re-dispatch "
    "verification to confirm the pull request is clear."
)


def fix_cycle_ceiling_question(inv_id: str, count: int, last_finding: str) -> OpenQuestion:
    return OpenQuestion(
        id=f"fix_cycle_ceiling_{inv_id[:8]}",
        question=(
            f"implementation_fix has been dispatched {count} times against this "
            "pull request with no merge — the fix-cycle ceiling is reached. "
            f"Last verifier finding: {last_finding}"
        ),
        proposed_answer_space=[FIX_CYCLE_OVERRIDE_OPTION, FIX_CYCLE_HAND_RESOLVE_OPTION],
        recommended=FIX_CYCLE_HAND_RESOLVE_OPTION,
        question_class="fix_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={
            FIX_CYCLE_OVERRIDE_OPTION: "implementation_fix",
            FIX_CYCLE_HAND_RESOLVE_OPTION: "verification",
        },
    )


def fix_cycle_ceiling_handover(
    workspace: Workspace, prior: Handover, count: int, last_finding: str
) -> Handover:
    ctx = HandoverContext.build(
        workspace,
        prior,
        prior.from_invocation_id,
        team_name=prior.from_team,
        default_forbidden=tuple(prior.forbidden_actions),
        next_team=tuple(prior.to_team_suggestions),
    )
    summary = (
        f"fix-cycle ceiling reached ({count} implementation_fix cycles) — "
        f"last verifier finding: {last_finding}"
    )
    skeleton = escalation_skeleton(ctx, summary, prior.pr_number)
    question = fix_cycle_ceiling_question(ctx.inv_id, count, last_finding)
    return skeleton.model_copy(update={"open_questions": [question]})
