from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator

import alan.project as project_mod
import alan.project_amendment as project_amendment_mod
import alan.project_auto_continue as project_auto_continue_mod
import alan.project_budget as project_budget_mod
import alan.project_doc as project_doc_mod
import alan.project_roadmap as project_roadmap_mod
import alan.workspace as workspace_mod
import alan.workspace_index as workspace_index_mod
from alan import engine_config, engine_preflight
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.engine_config import forge_target_for_workspace
from alan.proposal_items import ProposalItem
from alan.routes._deps import PubSubDep, QueueDep, WorkspaceIndexDep, WorkspacesRootDep

projects_router = APIRouter(prefix="/api/projects")

_PROJECT_NOT_FOUND_DETAIL = "project not found"
_PROJECT_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": _PROJECT_NOT_FOUND_DETAIL},
}
_PROJECT_ARCHIVED_DETAIL = "project is archived"
_PROJECT_NOT_FOUND_OR_ARCHIVED: dict[int | str, dict[str, str]] = {
    404: {"description": _PROJECT_NOT_FOUND_DETAIL},
    409: {"description": _PROJECT_ARCHIVED_DETAIL},
}
_PROJECT_NOT_READY_DETAIL = "project_not_ready"
_PROJECT_ALREADY_COMPLETED_DETAIL = "project_already_completed"
_PROJECT_NOT_FOUND_OR_NOT_COMPLETABLE: dict[int | str, dict[str, str]] = {
    404: {"description": _PROJECT_NOT_FOUND_DETAIL},
    409: {"description": f"{_PROJECT_NOT_READY_DETAIL} or {_PROJECT_ALREADY_COMPLETED_DETAIL}"},
}
_PATCH_STATUS_COMPLETED_DETAIL = (
    "status=completed is only reachable via POST /api/projects/{id}/complete"
)
_PROJECT_HAS_WORKSPACES_DETAIL = "project_has_workspaces"
_DELETE_PROJECT_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _PROJECT_NOT_FOUND_DETAIL},
    409: {"description": _PROJECT_HAS_WORKSPACES_DETAIL},
}
_TERMINAL_WORKSPACE_STATUSES = frozenset(
    {workspace_mod.Status.COMPLETED, workspace_mod.Status.ARCHIVED}
)
_PROJECT_HAS_LIVE_WORKSPACES_DETAIL = "project_has_live_workspaces"
_ENGINE_PREFLIGHT_NOT_PASSING_DETAIL = "engine preflight not passing"


class CreateProjectBody(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    doc_ref: str | None = Field(default=None, max_length=2000)
    intended_engine_key: str | None = Field(default=None, min_length=1, max_length=200)


class PatchProjectBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    status: project_mod.ProjectStatus | None = None
    doc_ref: str | None = Field(default=None, max_length=2000)
    autonomy: project_mod.AutonomyMode | None = None
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    engine_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _reject_non_finite_budget(cls, v: Any) -> Any:
        # Raise HTTPException (not ValueError) here: a pydantic ValidationError
        # echoes the raw rejected value back as `input`, and Starlette's
        # JSONResponse renders with allow_nan=False, so a `nan`/`inf` input
        # would crash 422-response rendering into an unhandled 500.
        if isinstance(v, float) and not math.isfinite(v):
            raise HTTPException(status_code=422, detail="budget_usd must be a finite number")
        return v

    @model_validator(mode="after")
    def _reject_explicit_null_for_required_fields(self) -> "PatchProjectBody":
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        if "autonomy" in self.model_fields_set and self.autonomy is None:
            raise ValueError("autonomy cannot be null")
        return self


class ProjectLedgerEntry(BaseModel):
    task_id: str
    title: str
    status: workspace_mod.Status
    pr_number: int | None = None


RoadmapDeliveryStatus = Literal["not_started", "in_flight", "delivered"]


class RoadmapItemView(BaseModel):
    id: str
    title: str
    text: str
    order: int
    status: RoadmapDeliveryStatus
    merged_prs: list[int] = Field(default_factory=list[int])


class ProjectListEntry(BaseModel):
    id: str
    title: str
    status: project_mod.ProjectStatus
    doc_ref: str | None
    autonomy: project_mod.AutonomyMode
    budget_usd: float | None
    engine_key: str | None = None
    intended_engine_key: str | None = None
    created_at: datetime
    updated_at: datetime
    workspace_count: int = 0
    total_spend_usd: float = 0.0
    roadmap_items_total: int = 0
    roadmap_items_delivered: int = 0


class ProjectBudgetView(BaseModel):
    state: project_budget_mod.BudgetState
    burn_usd: float
    budget_usd: float | None
    remaining_usd: float | None


class ProjectDetail(BaseModel):
    id: str
    title: str
    status: project_mod.ProjectStatus
    doc_ref: str | None
    autonomy: project_mod.AutonomyMode
    budget_usd: float | None
    engine_key: str | None = None
    intended_engine_key: str | None = None
    created_at: datetime
    updated_at: datetime
    workspaces: list[ProjectLedgerEntry] = Field(default_factory=list[ProjectLedgerEntry])
    ready_to_complete: bool = False
    roadmap: list[RoadmapItemView] = Field(default_factory=list[RoadmapItemView])
    budget: ProjectBudgetView | None = None


class CompleteRequest(BaseModel):
    force: bool = False
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _reject_forced_without_reason(self) -> "CompleteRequest":
        if self.force and not self.reason.strip():
            raise ValueError("reason is required when force=true")
        return self


class ProjectDocResponse(BaseModel):
    vision: str
    scope: str
    decision_log: str
    discovered_facts: str


class ProposeAmendmentBody(BaseModel):
    vision: str = Field(min_length=1, max_length=20000)
    scope: str = Field(min_length=1, max_length=20000)
    decision_entry: str = Field(min_length=1, max_length=20000)


class ProposeAmendmentResponse(BaseModel):
    fingerprint: str
    project_id: str
    proposed_at: str


class RoadmapImportBody(BaseModel):
    markdown: str = Field(min_length=1, max_length=200000)


class RoadmapPreviewItem(BaseModel):
    title: str
    text: str


class RoadmapPreviewResponse(BaseModel):
    items: list[RoadmapPreviewItem]


class RoadmapImportResponse(BaseModel):
    items: list[project_roadmap_mod.RoadmapItem]


class RoadmapReorderBody(BaseModel):
    ordered_ids: list[str] = Field(min_length=1, max_length=1000)


class RoadmapEditBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    text: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def _require_title_or_text(self) -> "RoadmapEditBody":
        if self.title is None and self.text is None:
            raise ValueError("title or text is required")
        return self


def _read_project_or_404(project_id: str) -> project_mod.Project:
    try:
        return project_mod.read(project_id)
    except project_mod.ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _latest_pr_number(ws: workspace_mod.Workspace) -> int | None:
    numbers = workspace_mod.pr_numbers_from_tasks(ws.meta.tasks)
    return numbers[-1] if numbers else None


def _project_workspaces(project_id: str, workspaces_root: Path) -> list[workspace_mod.Workspace]:
    return [
        ws
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.project_id == project_id
    ]


def _blocking_workspace_ids_for_project(project_id: str, workspaces_root: Path) -> list[str]:
    return [
        ws.task_id
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.status not in _TERMINAL_WORKSPACE_STATUSES and ws.meta.project_id == project_id
    ]


def _engine_preflight_passing(engine_key: str) -> bool:
    cfg = engine_config.lookup_by_slug(engine_key)
    if cfg is None:
        return False
    result = engine_preflight.load_last_preflight(forge_target_for_workspace(engine_key, cfg))
    return result is not None and result.overall == "pass"


def _project_ledger(project_id: str, workspaces_root: Path) -> list[ProjectLedgerEntry]:
    matching = _project_workspaces(project_id, workspaces_root)
    matching.sort(key=lambda ws: ws.meta.created_at)
    return [
        ProjectLedgerEntry(
            task_id=ws.task_id,
            title=ws.meta.title,
            status=ws.meta.status,
            pr_number=_latest_pr_number(ws),
        )
        for ws in matching
    ]


def _project_readiness(ledger: list[ProjectLedgerEntry]) -> bool:
    return all(entry.status in _TERMINAL_WORKSPACE_STATUSES for entry in ledger)


def _stamped_workspaces_by_item(
    project_id: str, workspaces_root: Path
) -> dict[str, list[workspace_mod.Workspace]]:
    by_item: dict[str, list[workspace_mod.Workspace]] = {}
    for ws in _project_workspaces(project_id, workspaces_root):
        if ws.meta.roadmap_item_id is not None:
            by_item.setdefault(ws.meta.roadmap_item_id, []).append(ws)
    return by_item


def _roadmap_merged_prs(stamped: list[workspace_mod.Workspace]) -> list[int]:
    return sorted(
        {n for ws in stamped for n in workspace_mod.pr_numbers_from_tasks(ws.meta.tasks)}
    )


def _roadmap_is_delivered(stamped: list[workspace_mod.Workspace]) -> bool:
    return any(
        ws.meta.status in _TERMINAL_WORKSPACE_STATUSES
        and bool(workspace_mod.pr_numbers_from_tasks(ws.meta.tasks))
        for ws in stamped
    )


def _roadmap_is_in_flight(stamped: list[workspace_mod.Workspace]) -> bool:
    return any(ws.meta.status not in _TERMINAL_WORKSPACE_STATUSES for ws in stamped)


def _roadmap_item_status(
    stamped: list[workspace_mod.Workspace],
) -> tuple[RoadmapDeliveryStatus, list[int]]:
    merged_prs = _roadmap_merged_prs(stamped)
    if _roadmap_is_delivered(stamped):
        return "delivered", merged_prs
    if _roadmap_is_in_flight(stamped):
        return "in_flight", merged_prs
    return "not_started", merged_prs


def _project_budget_view(
    project_id: str, index: workspace_index_mod.WorkspaceIndex
) -> ProjectBudgetView | None:
    burn = project_budget_mod.resolve_project_burn(project_id, index)
    if burn is None:
        return None
    return ProjectBudgetView(
        state=project_budget_mod.budget_state(burn),
        burn_usd=burn.burn_usd,
        budget_usd=burn.budget_usd,
        remaining_usd=burn.remaining_usd,
    )


def _roadmap_items(project_id: str, workspaces_root: Path) -> list[RoadmapItemView]:
    items = project_roadmap_mod.read_items(project_id)
    if not items:
        return []
    by_item = _stamped_workspaces_by_item(project_id, workspaces_root)
    views: list[RoadmapItemView] = []
    for order, item in enumerate(items):
        status, merged_prs = _roadmap_item_status(by_item.get(item.id, []))
        views.append(
            RoadmapItemView(
                id=item.id,
                title=item.title,
                text=item.text,
                order=order,
                status=status,
                merged_prs=merged_prs,
            )
        )
    return views


@projects_router.post("", status_code=201)
async def create_project(body: CreateProjectBody) -> project_mod.Project:
    return await run_in_threadpool(
        project_mod.create,
        body.title,
        doc_ref=body.doc_ref,
        intended_engine_key=body.intended_engine_key,
    )


_ZERO_AGGREGATE = workspace_index_mod.ProjectAggregate(workspace_count=0, total_spend_usd=0.0)


def _roadmap_totals(project_id: str, delivered_ids: set[str]) -> tuple[int, int]:
    items = project_roadmap_mod.read_items(project_id)
    delivered = sum(1 for item in items if item.id in delivered_ids)
    return len(items), delivered


def _project_list_entry(
    project: project_mod.Project,
    aggregates: dict[str, workspace_index_mod.ProjectAggregate],
    delivered_by_project: dict[str, set[str]],
) -> ProjectListEntry:
    agg = aggregates.get(project.id, _ZERO_AGGREGATE)
    total, delivered = _roadmap_totals(project.id, delivered_by_project.get(project.id, set()))
    return ProjectListEntry(
        **project.model_dump(),
        workspace_count=agg.workspace_count,
        total_spend_usd=agg.total_spend_usd,
        roadmap_items_total=total,
        roadmap_items_delivered=delivered,
    )


def _project_list_entries(
    projects: list[project_mod.Project],
    aggregates: dict[str, workspace_index_mod.ProjectAggregate],
    delivered_by_project: dict[str, set[str]],
) -> list[ProjectListEntry]:
    return [_project_list_entry(p, aggregates, delivered_by_project) for p in projects]


@projects_router.get("")
async def list_projects_endpoint(
    index: WorkspaceIndexDep,
    include: Annotated[str | None, Query()] = None,
) -> list[ProjectListEntry]:
    projects = await run_in_threadpool(project_mod.list_projects)
    if include != "archived":
        projects = [p for p in projects if p.status != project_mod.ProjectStatus.ARCHIVED]
    if not projects:
        return []
    rows = await run_in_threadpool(index.rows)
    archived = await run_in_threadpool(index.archived_snapshot)
    aggregates = workspace_index_mod.project_aggregates(rows, archived)
    delivered_by_project = workspace_index_mod.delivered_roadmap_item_ids(rows, archived)
    return await run_in_threadpool(
        _project_list_entries, projects, aggregates, delivered_by_project
    )


@projects_router.get("/{project_id}", responses=_PROJECT_NOT_FOUND)
async def get_project(
    project_id: str,
    workspaces_root: WorkspacesRootDep,
    index: WorkspaceIndexDep,
) -> ProjectDetail:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    ledger = await run_in_threadpool(_project_ledger, project_id, workspaces_root)
    roadmap = await run_in_threadpool(_roadmap_items, project_id, workspaces_root)
    budget = await run_in_threadpool(_project_budget_view, project_id, index)
    return ProjectDetail(
        **project.model_dump(),
        workspaces=ledger,
        ready_to_complete=_project_readiness(ledger),
        roadmap=roadmap,
        budget=budget,
    )


_PATCH_PROJECT_RESPONSES: dict[int | str, dict[str, str]] = {
    **_PROJECT_NOT_FOUND,
    400: {"description": "unknown engine, or status=completed via PATCH"},
    409: {"description": _PROJECT_HAS_LIVE_WORKSPACES_DETAIL},
    422: {"description": _ENGINE_PREFLIGHT_NOT_PASSING_DETAIL},
}


async def _check_engine_rebind_gates(
    project: project_mod.Project, body: PatchProjectBody, workspaces_root: Path
) -> None:
    if "engine_key" not in body.model_fields_set:
        return
    if body.engine_key is None or body.engine_key == project.engine_key:
        return
    if engine_config.lookup_by_slug(body.engine_key) is None:
        raise HTTPException(status_code=400, detail=f"unknown engine: {body.engine_key!r}")
    blocking = await run_in_threadpool(
        _blocking_workspace_ids_for_project, project.id, workspaces_root
    )
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": _PROJECT_HAS_LIVE_WORKSPACES_DETAIL,
                "blocking_workspace_ids": blocking,
            },
        )
    if not await run_in_threadpool(_engine_preflight_passing, body.engine_key):
        raise HTTPException(status_code=422, detail=_ENGINE_PREFLIGHT_NOT_PASSING_DETAIL)


@projects_router.patch("/{project_id}", responses=_PATCH_PROJECT_RESPONSES)
async def patch_project(
    project_id: str, body: PatchProjectBody, workspaces_root: WorkspacesRootDep
) -> project_mod.Project:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if body.status == project_mod.ProjectStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=_PATCH_STATUS_COMPLETED_DETAIL)
    if project.status == project_mod.ProjectStatus.COMPLETED and body.status is not None:
        raise HTTPException(status_code=409, detail=_PROJECT_ALREADY_COMPLETED_DETAIL)
    await _check_engine_rebind_gates(project, body, workspaces_root)
    updates: dict[str, Any] = body.model_dump(exclude_unset=True)
    if not updates:
        return project
    return await run_in_threadpool(project_mod.update, project, updates)


@projects_router.post("/{project_id}/complete", responses=_PROJECT_NOT_FOUND_OR_NOT_COMPLETABLE)
async def complete_project(
    project_id: str,
    workspaces_root: WorkspacesRootDep,
    body: CompleteRequest | None = None,
) -> project_mod.Project:
    body = body if body is not None else CompleteRequest()
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=_PROJECT_ALREADY_COMPLETED_DETAIL)
    ledger = await run_in_threadpool(_project_ledger, project_id, workspaces_root)
    if not body.force and not _project_readiness(ledger):
        raise HTTPException(status_code=409, detail=_PROJECT_NOT_READY_DETAIL)
    completed = await run_in_threadpool(
        project_mod.update, project, {"status": project_mod.ProjectStatus.COMPLETED}
    )
    outcomes = [
        project_doc_mod.LedgerOutcome(
            task_id=entry.task_id,
            title=entry.title,
            status=str(entry.status),
            pr_number=entry.pr_number,
        )
        for entry in ledger
    ]
    await run_in_threadpool(
        project_doc_mod.append_completion_summary,
        completed,
        ledger=outcomes,
        force=body.force,
        reason=body.reason,
        completed_at=completed.updated_at,
    )
    return completed


def _read_project_doc(project: project_mod.Project) -> project_doc_mod.ProjectDocSections:
    project, path = project_doc_mod.ensure_doc(project)
    return project_doc_mod.parse_anchors(path.read_text(encoding="utf-8"))


@projects_router.get("/{project_id}/doc", responses=_PROJECT_NOT_FOUND)
async def get_project_doc(project_id: str) -> ProjectDocResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    sections = await run_in_threadpool(_read_project_doc, project)
    return ProjectDocResponse(
        vision=sections.vision,
        scope=sections.scope,
        decision_log=sections.decision_log,
        discovered_facts=sections.discovered_facts,
    )


@projects_router.delete(
    "/{project_id}", status_code=204, responses=_DELETE_PROJECT_RESPONSES
)
async def delete_project(
    project_id: str,
    workspaces_root: WorkspacesRootDep,
    queue: QueueDep,
) -> Response:
    await run_in_threadpool(_read_project_or_404, project_id)
    matching = await run_in_threadpool(_project_workspaces, project_id, workspaces_root)
    if matching:
        raise HTTPException(status_code=409, detail=_PROJECT_HAS_WORKSPACES_DETAIL)
    await run_in_threadpool(
        project_amendment_mod.delete_pending_amendments_for_project, queue, project_id
    )
    try:
        await run_in_threadpool(project_mod.delete, project_id)
    except project_mod.ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@projects_router.post(
    "/{project_id}/amendments", status_code=201, responses=_PROJECT_NOT_FOUND_OR_ARCHIVED
)
async def propose_project_amendment(
    project_id: str, body: ProposeAmendmentBody, queue: QueueDep
) -> ProposeAmendmentResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=_PROJECT_ARCHIVED_DETAIL)
    fingerprint, record = await run_in_threadpool(
        project_amendment_mod.propose_amendment,
        queue,
        project_id,
        vision=body.vision,
        scope=body.scope,
        decision_entry=body.decision_entry,
        now=datetime.now(UTC),
    )
    return ProposeAmendmentResponse(
        fingerprint=fingerprint, project_id=project_id, proposed_at=record.proposed_at
    )


_ROADMAP_ALREADY_IMPORTED_DETAIL = "roadmap already imported"
_ROADMAP_IMPORT_RESPONSES: dict[int | str, dict[str, str]] = {
    **_PROJECT_NOT_FOUND,
    409: {"description": _ROADMAP_ALREADY_IMPORTED_DETAIL},
}


@projects_router.post("/{project_id}/roadmap/preview", responses=_PROJECT_NOT_FOUND)
async def preview_roadmap(project_id: str, body: RoadmapImportBody) -> RoadmapPreviewResponse:
    await run_in_threadpool(_read_project_or_404, project_id)
    parsed = await run_in_threadpool(project_roadmap_mod.parse_roadmap_markdown, body.markdown)
    return RoadmapPreviewResponse(
        items=[RoadmapPreviewItem(title=p.title, text=p.text) for p in parsed]
    )


@projects_router.post(
    "/{project_id}/roadmap/import", status_code=201, responses=_ROADMAP_IMPORT_RESPONSES
)
async def import_roadmap(project_id: str, body: RoadmapImportBody) -> RoadmapImportResponse:
    await run_in_threadpool(_read_project_or_404, project_id)
    try:
        items = await run_in_threadpool(
            project_roadmap_mod.import_roadmap, project_id, body.markdown
        )
    except project_roadmap_mod.RoadmapAlreadyImportedError as exc:
        raise HTTPException(status_code=409, detail=_ROADMAP_ALREADY_IMPORTED_DETAIL) from exc
    return RoadmapImportResponse(items=items)


@projects_router.post(
    "/{project_id}/roadmap/reorder", status_code=201, responses=_PROJECT_NOT_FOUND_OR_ARCHIVED
)
async def propose_roadmap_reorder(
    project_id: str, body: RoadmapReorderBody, queue: QueueDep
) -> ProposeAmendmentResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=_PROJECT_ARCHIVED_DETAIL)
    fingerprint, record = await run_in_threadpool(
        project_amendment_mod.propose_amendment,
        queue,
        project_id,
        kind="roadmap_reorder",
        payload={"ordered_ids": body.ordered_ids},
        now=datetime.now(UTC),
    )
    return ProposeAmendmentResponse(
        fingerprint=fingerprint, project_id=project_id, proposed_at=record.proposed_at
    )


class RoadmapNextItemProposalResponse(BaseModel):
    proposal: ProposalItem | None
    budget_state: project_budget_mod.BudgetState


@projects_router.get(
    "/{project_id}/roadmap/next-item-proposal", responses=_PROJECT_NOT_FOUND_OR_ARCHIVED
)
async def get_roadmap_next_item_proposal(
    project_id: str, workspaces_root: WorkspacesRootDep, index: WorkspaceIndexDep
) -> RoadmapNextItemProposalResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=_PROJECT_ARCHIVED_DETAIL)
    proposal = await run_in_threadpool(
        project_auto_continue_mod.propose_next_item_proposal_item,
        project,
        workspaces_root=workspaces_root,
        workspace_index=index,
    )
    state = await run_in_threadpool(project_budget_mod.resolve_budget_state, project_id, index)
    return RoadmapNextItemProposalResponse(proposal=proposal, budget_state=state)


class RoadmapAdvanceResponse(BaseModel):
    task_id: str
    item_id: str
    item_title: str


_NO_NEXT_ROADMAP_ITEM_DETAIL = "no un-attempted roadmap item to advance to"
_BUDGET_PAUSED_DETAIL = "project budget is exhausted"
_ROADMAP_ADVANCE_RESPONSES: dict[int | str, dict[str, str]] = {
    **_PROJECT_NOT_FOUND,
    409: {
        "description": (
            f"{_PROJECT_ARCHIVED_DETAIL} or {_NO_NEXT_ROADMAP_ITEM_DETAIL} or "
            f"{_BUDGET_PAUSED_DETAIL}"
        )
    },
}


@projects_router.post(
    "/{project_id}/roadmap/advance", status_code=201, responses=_ROADMAP_ADVANCE_RESPONSES
)
async def advance_project_roadmap(
    project_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
    index: WorkspaceIndexDep,
) -> RoadmapAdvanceResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=_PROJECT_ARCHIVED_DETAIL)
    if await run_in_threadpool(project_budget_mod.budget_exhausted, project_id, index):
        raise HTTPException(status_code=409, detail=_BUDGET_PAUSED_DETAIL)
    outcome = await run_in_threadpool(
        project_auto_continue_mod.advance_project,
        project_id,
        workspaces_root=workspaces_root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=enqueue_with_pending_workflow,
    )
    if outcome is None:
        raise HTTPException(status_code=409, detail=_NO_NEXT_ROADMAP_ITEM_DETAIL)
    return RoadmapAdvanceResponse(
        task_id=outcome.workspace.task_id,
        item_id=outcome.item.id,
        item_title=outcome.item.title,
    )


def _roadmap_edit_payload(item_id: str, body: RoadmapEditBody) -> dict[str, Any]:
    payload: dict[str, Any] = {"item_id": item_id}
    if body.title is not None:
        payload["title"] = body.title
    if body.text is not None:
        payload["text"] = body.text
    return payload


@projects_router.post(
    "/{project_id}/roadmap/items/{item_id}",
    status_code=201,
    responses=_PROJECT_NOT_FOUND_OR_ARCHIVED,
)
async def propose_roadmap_edit(
    project_id: str, item_id: str, body: RoadmapEditBody, queue: QueueDep
) -> ProposeAmendmentResponse:
    project = await run_in_threadpool(_read_project_or_404, project_id)
    if project.status == project_mod.ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail=_PROJECT_ARCHIVED_DETAIL)
    fingerprint, record = await run_in_threadpool(
        project_amendment_mod.propose_amendment,
        queue,
        project_id,
        kind="roadmap_edit",
        payload=_roadmap_edit_payload(item_id, body),
        now=datetime.now(UTC),
    )
    return ProposeAmendmentResponse(
        fingerprint=fingerprint, project_id=project_id, proposed_at=record.proposed_at
    )
