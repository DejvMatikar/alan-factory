from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from alan import project as project_mod
from alan import project_roadmap as project_roadmap_mod
from alan.handover import Handover
from alan.incident import IncidentClass
from alan.project import Project
from alan.teams import MANUALLY_DISPATCHABLE_TEAMS
from alan.workspace_index import ProjectBurn, WorkspaceRow, project_burn_usd

_EXCERPT_MAX = 500

InboxItemType = Literal[
    "clarification", "decision", "unblock", "failure", "awaiting_harvest", "digest", "fyi"
]
ActionKind = Literal["chat_dispatch", "direct_dispatch", "answer_accept", "link"]


class InboxAction(BaseModel):
    kind: ActionKind
    label: str
    team: str | None = None
    instructions: str | None = None
    url: str | None = None


class ProposalItem(BaseModel):
    type: InboxItemType
    fingerprint: str | None
    workspace_id: str | None
    task_id: str | None
    project_id: str | None = None
    engine_key: str | None = None
    title: str
    prepared_context: str
    recommendation: str
    instruction: str | None = None
    action_url: str | None = None
    actions: list[InboxAction]
    updated_at: str
    burn: ProjectBurn | None = None
    autonomy: project_mod.AutonomyMode | None = None
    incident_class: IncidentClass | None = None
    question_id: str | None = None
    handover_id: str | None = None
    recommended: str | None = None
    question: str | None = None
    proposed_answer_space: list[str] = Field(default_factory=list)
    pending_external: bool = False


def _excerpt(text: str, limit: int = _EXCERPT_MAX) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "…"


def propose_team(latest: Handover | None) -> str | None:
    """Deterministic next-hop resolution: `next_hop`, else `to_team_suggestions` in order.

    Reused verbatim by `alan.auto_continue` — the same zero-model-call routing
    data the dispatcher's own `alan.router.select_next_team` already reads,
    read-only, so "no dispatch without a resolvable, static-allowlisted team"
    stays provable for both the human-approved propose-mode flow and the
    unattended auto-continue flow.
    """
    if latest is None:
        return None
    candidates: list[str] = [latest.next_hop] if latest.next_hop else []
    candidates.extend(latest.to_team_suggestions)
    for team in candidates:
        if team in MANUALLY_DISPATCHABLE_TEAMS:
            return team
    return None


def burn_for_resolved_project(
    project: Project, rows: list[WorkspaceRow], archived: list[WorkspaceRow]
) -> ProjectBurn:
    burn_usd = project_burn_usd(rows, archived, project.id)
    remaining = None if project.budget_usd is None else project.budget_usd - burn_usd
    return ProjectBurn(burn_usd=burn_usd, budget_usd=project.budget_usd, remaining_usd=remaining)


def resolve_project_burn(
    project_id: str | None,
    rows: list[WorkspaceRow],
    archived: list[WorkspaceRow],
    project_cache: dict[str, Project | None],
) -> tuple[ProjectBurn | None, project_mod.AutonomyMode | None]:
    """Mirror `_resolve_project_doc_path` (alan/dispatcher.py): fail open, never raise.

    Also surfaces the project's persisted autonomy dial so the decision-presentation
    path demonstrably reads it; no auto_continue action is taken here (deferred to P7.1).
    `project_cache` is shared across one build so workspaces sharing a project
    don't each pay for a fresh `project_mod.read`.
    """
    if project_id is None:
        return None, None
    project = project_mod.cached_read(project_id, project_cache)
    if project is None:
        return None, None
    return burn_for_resolved_project(project, rows, archived), project.autonomy


_NEXT_ITEM_ADVANCE_URL_TEMPLATE = "/api/projects/{project_id}/roadmap/advance"


def next_item_proposal_item(
    project: Project, item: project_roadmap_mod.RoadmapItem
) -> ProposalItem:
    """Propose-mode "next: item N" card — pull-computed, no persisted state.

    Dispatches via `direct_dispatch` (one-tap start) rather than `link`
    (navigate-then-decide elsewhere): the action's `url` points at the roadmap
    advance endpoint, which runs the same atomic check-and-dispatch
    `alan.project_auto_continue.advance_project` uses for the unattended seam.
    Recomputing this from current state on every read is what makes it
    idempotent without a stored pending record.
    """
    return ProposalItem(
        type="decision",
        fingerprint=None,
        workspace_id=None,
        task_id=None,
        project_id=project.id,
        engine_key=project.engine_key,
        title=f"Next: {item.title}",
        prepared_context=_excerpt(item.text),
        recommendation=f"All prior roadmap items have delivered — start {item.title!r} next.",
        actions=[
            InboxAction(
                kind="direct_dispatch",
                label=f"Start: {item.title}",
                url=_NEXT_ITEM_ADVANCE_URL_TEMPLATE.format(project_id=project.id),
            )
        ],
        updated_at=datetime.now(UTC).isoformat(),
        autonomy=project.autonomy,
    )


__all__ = [
    "ActionKind",
    "InboxAction",
    "InboxItemType",
    "ProposalItem",
    "burn_for_resolved_project",
    "next_item_proposal_item",
    "propose_team",
    "resolve_project_burn",
]
