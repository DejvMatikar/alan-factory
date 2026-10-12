from __future__ import annotations

from typing import Literal

from alan.project import Project
from alan.proposal_items import burn_for_resolved_project
from alan.proposal_items import resolve_project_burn as _resolve_project_burn_and_autonomy
from alan.workspace_index import ProjectBurn, WorkspaceIndex

BudgetState = Literal["normal", "approaching", "paused"]

WARN_FRACTION = 0.8


def budget_state(burn: ProjectBurn | None) -> BudgetState:
    if burn is None or burn.budget_usd is None:
        return "normal"
    if burn.remaining_usd is not None and burn.remaining_usd <= 0:
        return "paused"
    if burn.burn_usd >= WARN_FRACTION * burn.budget_usd:
        return "approaching"
    return "normal"


def resolve_project_burn(
    project_id: str | None, workspace_index: WorkspaceIndex | None
) -> ProjectBurn | None:
    if workspace_index is None:
        return None
    burn, _autonomy = _resolve_project_burn_and_autonomy(
        project_id, workspace_index.rows(), workspace_index.archived_snapshot(), {}
    )
    return burn


def resolve_burn_for_project(
    project: Project, workspace_index: WorkspaceIndex | None
) -> ProjectBurn | None:
    if workspace_index is None:
        return None
    return burn_for_resolved_project(
        project, workspace_index.rows(), workspace_index.archived_snapshot()
    )


def resolve_budget_state(
    project_id: str | None, workspace_index: WorkspaceIndex | None
) -> BudgetState:
    return budget_state(resolve_project_burn(project_id, workspace_index))


def budget_exhausted(project_id: str | None, workspace_index: WorkspaceIndex | None) -> bool:
    return resolve_budget_state(project_id, workspace_index) == "paused"


__all__ = [
    "WARN_FRACTION",
    "BudgetState",
    "budget_exhausted",
    "budget_state",
    "resolve_budget_state",
    "resolve_burn_for_project",
    "resolve_project_burn",
]
