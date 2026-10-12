from __future__ import annotations

from datetime import UTC, datetime

from alan import workflow as workflow_mod
from alan.handover import Decision, Handover
from alan.teams._base import EmitFn, Team
from alan.workspace import Workspace


def make_handover(
    task_id: str,
    suffix: str,
    *,
    from_invocation_id: str = "inv_001",
    to_team: str = "implementation_plan",
    iteration: int = 1,
    full_synthesis: str = "",
    decision_statement: str | None = None,
) -> Handover:
    decisions = (
        [
            Decision(
                id="d1",
                statement=decision_statement,
                rationale="r",
                confidence=0.9,
                reversibility="high",
            )
        ]
        if decision_statement is not None
        else []
    )
    return Handover(
        handover_id=f"handover_{task_id}_{suffix}",
        handover_chain_id=f"handover_{task_id}_chain",
        handover_type="forward",
        task_id=task_id,
        from_team="brainstorming",
        from_invocation_id=from_invocation_id,
        to_team_suggestions=[to_team],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=full_synthesis,
        decisions=decisions,
    )


def seed_and_run(
    team: Team,
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    task_id: str | None = None,
    instructions: str | None = None,
) -> Handover:
    specs = team.plan_workflow(workspace, prior)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team=team.name, tasks=specs
    )
    return team.run(
        workspace, prior, emit, inv_id=inv_id, task_id=task_id, instructions=instructions
    )
