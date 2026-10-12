from __future__ import annotations

import functools
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, computed_field, model_validator
from starlette.responses import RedirectResponse, Response

import alan.fleet_timing as fleet_timing_mod
import alan.prep_chat as prep_chat_mod
import alan.project as project_mod
import alan.retention as retention_mod
import alan.unblock_ledger as unblock_ledger_mod
import alan.workflow as workflow_mod
import alan.workspace as workspace_mod
import alan.workspace_cost as workspace_cost_mod
from alan._dispatcher_base import (
    OutOfBandCompletionError,
    enqueue_with_pending_workflow,
    forge_target_for_ws,
)
from alan._dispatcher_retire import (
    WORKSPACE_DELETE_BLOCKED_DETAIL,
    archive_or_refuse,
    restore_from_archive,
    retire_workspace,
)
from alan.app.workspace_create_models import (
    CreateWorkspaceBody,
    CreateWorkspaceResponse,
    archive_prep_chat_best_effort,
    reject_uncovered_clauses,
    reject_unseedable_teams,
)
from alan.app.workspace_creation import (
    append_predispatch_log,
    build_create_fn,
    is_hidden_by_default,
    maybe_log_counter_case_waiver,
    normalize_instructions,
    refine_and_dispatch_root_task,
    resolve_dispatchable_team,
    resolve_engine_key,
    scan_scope_findings,
    task_title_for_audit,
)
from alan.archive_guard import ArchiveRequest
from alan.delivery import WorkspaceDelivery, fetch_pr_delivery
from alan.dispatch import (
    assert_team_dispatchable,
    dispatch_team_core,
    owning_task_id,
    prior_handover_for_task,
)
from alan.events import PubSub
from alan.handover import Handover
from alan.handover import list_handovers as _list_handovers
from alan.incident import STUCK_WS_STATUSES, stuck_workspace_keys
from alan.queue import JobStatus, Queue
from alan.roots import logs_root
from alan.routes._brief_refine_source import write_brief_refine_source
from alan.routes._deps import (
    GUARDED,
    SSE_HEADERS,
    SSE_MEDIA,
    BuildChatSessionsRootDep,
    DispatcherDep,
    ExploreChatSessionsRootDep,
    OptionalDispatcherDep,
    PubSubDep,
    QueueDep,
    WorkspaceIndexDep,
    WorkspacesRootDep,
    read_history_entries,
    read_stream_events,
    read_workspace_or_404,
    require_control_token,
    workspace_event_stream,
)
from alan.routes._prep_chat_read import prep_chat_read_router
from alan.routes._workspace_detail import (
    PendingDeployGate,
    WorkspaceDetail,
    detail_timing,
    fetch_manager_summary,
    fetch_terminal_outcome,
    has_resumable_failed_job,
    latest_pr_number,
    resolve_pending_deploy_gate,
)
from alan.routes._workspaces_lane import create_within_lane_limit
from alan.routes._workspaces_merge_gate import (
    apply_merge_gate_to_manual_dispatch,
    create_dispatch_skip_decision,
)
from alan.scope_audit import CounterCase
from alan.teams import _clause_audit
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import TeamEvent
from alan.teams.triage_pregate import verify_pr_merged
from alan.workflow import WorkflowDoc, WorkflowError, WorkflowStatus
from alan.workspace import Status, TestScenario
from alan.workspace_index import WorkspaceRow

logger = logging.getLogger("alan.server")

_OOB_COMPLETION_AUDIT_FILE = "oob_completion_audit.jsonl"
_SCENARIO_OVERRIDE_AUDIT_FILE = "scenario_override_audit.jsonl"


_WORKSPACE_NOT_FOUND_DESC = "workspace not found"
_WORKSPACE_ARCHIVED_DETAIL = "workspace is archived"
_NOT_FOUND: dict[int | str, dict[str, str]] = {404: {"description": _WORKSPACE_NOT_FOUND_DESC}}
_NOT_FOUND_OR_BAD_TEAM: dict[int | str, dict[str, str]] = {
    400: {"description": "unknown or non-dispatchable team"},
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": _WORKSPACE_ARCHIVED_DETAIL},
}
_CREATE_WORKSPACE_RESPONSES: dict[int | str, dict[str, str]] = {
    400: {
        "description": (
            "unknown engine, unknown team, internal-only team, or acceptance "
            "clauses uncovered by task dod_clauses"
        )
    },
    409: {"description": "engine lane full"},
    422: {"description": "team not admitted at the workspace's pinned posture"},
}
_NOT_FOUND_OR_BAD_STATE: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": "workspace not completed or no PR-bearing handover"},
}
_ARCHIVE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": "workspace has active jobs, or has incomplete subtasks"},
}
_WORKSPACE_NOT_ARCHIVED_DETAIL = "workspace is not archived"
_UNARCHIVE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": _WORKSPACE_NOT_ARCHIVED_DETAIL},
}
_INVOCATION_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "workspace or invocation not found"},
}
_STREAM_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "workspace, invocation, or agent stream not found"},
}
_DELETE_WORKSPACE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": WORKSPACE_DELETE_BLOCKED_DETAIL},
}
_STOP_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": "workspace is not active or has no running job"},
}
_RESUME_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    409: {"description": "workspace is not resumable"},
}
_NODE_DOC_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "workspace, invocation, or agent doc not found"},
}


class WorkspaceSummary(BaseModel):
    task_id: str
    title: str
    status: Status
    team_runs_total: int
    invocations_total: int
    total_cost_usd: float | None = None
    updated_at: datetime
    created_at: datetime
    latest_team: str | None = None
    latest_invocation_status: WorkflowStatus | None = None
    note: str | None = None
    team: str | None = None
    project_id: str | None = None
    project_title: str | None = None
    engine_key: str | None = None
    auto_archive_days: int | None = None
    tasks_total: int = 0
    tasks_done: int = 0
    pr_number: int | None = None
    active_seconds: float | None = None
    lead_seconds: float | None = None
    unblock: unblock_ledger_mod.UnblockRecord | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def operator_link(self) -> bool:
        return self.status in STUCK_WS_STATUSES


class NoteBody(BaseModel):
    note: str | None = Field(default=None, max_length=5000)


class NoteResponse(BaseModel):
    note: str | None


class StopResponse(BaseModel):
    job_id: int


class ResumeResponse(BaseModel):
    queued_jobs: int


class DispatchBody(BaseModel):
    team: str = Field(min_length=1, max_length=64)
    instructions: str | None = Field(default=None, max_length=40000)
    counter_case: CounterCase | None = None


class DispatchResponse(BaseModel):
    job_id: int
    invocation_id: str


class CompleteOutOfBandBody(BaseModel):
    pr_number: int = Field(gt=0)
    force: bool = False


class TaskScenariosBody(BaseModel):
    scenarios: list[TestScenario] | None = Field(default=None, max_length=64)
    waived: bool | None = None

    @model_validator(mode="after")
    def _validate_at_least_one_set(self) -> "TaskScenariosBody":
        if self.scenarios is None and self.waived is None:
            raise ValueError("scenarios or waived is required")
        return self


def _summary_from_row(
    row: WorkspaceRow,
    invocations_total: int,
    project_title: str | None,
    configured_auto_archive_days: int | None,
    now: datetime,
    unblock: unblock_ledger_mod.UnblockRecord | None,
) -> WorkspaceSummary:
    auto_archive_days = (
        configured_auto_archive_days
        if configured_auto_archive_days is not None
        and retention_mod.is_archivable_status(row.status)
        else None
    )
    return WorkspaceSummary(
        task_id=row.task_id,
        title=row.title,
        status=row.status,
        team_runs_total=row.team_runs_total,
        invocations_total=invocations_total,
        total_cost_usd=row.total_cost_usd,
        updated_at=row.updated_at,
        created_at=row.created_at,
        latest_team=row.latest_team,
        latest_invocation_status=row.latest_invocation_status,
        note=row.note,
        team=row.team,
        project_id=row.project_id,
        project_title=project_title,
        engine_key=row.engine_key,
        auto_archive_days=auto_archive_days,
        tasks_total=row.tasks_total,
        tasks_done=row.tasks_done,
        pr_number=row.pr_number,
        active_seconds=row.live_active_seconds(now),
        lead_seconds=fleet_timing_mod.lead_seconds(
            created_at=row.created_at, delivered_at=row.delivered_at
        ),
        unblock=unblock,
    )


def _resolve_project_titles(rows: list[WorkspaceRow]) -> dict[str, str | None]:
    """Route-layer join: at most one `project.json` read per distinct project id.

    `project_cache` is scoped to this one call, so N rows sharing a `project_id`
    still pay for a single `project_mod.cached_read`. A dangling id (project
    deleted/malformed) resolves to a present `task_id` key with a `None` title —
    the row still surfaces its `project_id`, just with no title to link.
    """
    project_cache: dict[str, project_mod.Project | None] = {}
    titles: dict[str, str | None] = {}
    for row in rows:
        if row.project_id is None:
            continue
        project = project_mod.cached_read(row.project_id, project_cache)
        titles[row.task_id] = project.title if project is not None else None
    return titles


def _safe_workflow_path(ws: workspace_mod.Workspace, inv_id: str) -> Path:
    try:
        return workflow_mod.workflow_path(ws, inv_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _safe_stream_path(
    ws: workspace_mod.Workspace, inv_id: str, role: str
) -> Path:
    try:
        return workflow_mod.stream_path(ws, inv_id, role)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _safe_result_path(
    ws: workspace_mod.Workspace, inv_id: str, node_id: str
) -> Path:
    try:
        return workflow_mod.result_path(ws, inv_id, node_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _safe_config_path(
    ws: workspace_mod.Workspace, inv_id: str, node_id: str
) -> Path:
    try:
        return workflow_mod.config_path(ws, inv_id, node_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


workspaces_router = APIRouter(prefix="/api/workspaces")
workspaces_router.include_router(prep_chat_read_router)
tasks_router = APIRouter(prefix="/api/workspaces/{workspace_id}/tasks/{task_id}")

_COMPLETE_TASK_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": "workspace or subtask not found"},
    409: {
        "description": (
            "workspace has no subtasks, is archived, subtask is already "
            "terminal, or a pending test_ref requirement cannot be resolved"
        )
    },
    422: {"description": "PR not verified merged; retry with force=true to override"},
}


_TASK_SCENARIOS_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED,
    404: {"description": "workspace or subtask not found"},
    409: {"description": "workspace has no subtasks or is archived"},
    422: {"description": "neither scenarios nor waived was supplied"},
}


def _reject_no_subtasks_or_archived(ws: workspace_mod.Workspace) -> None:
    if not ws.meta.tasks or ws.meta.status == Status.ARCHIVED:
        raise HTTPException(
            status_code=409, detail="workspace has no subtasks or is archived"
        )


def _reject_unknown_task(
    ws: workspace_mod.Workspace, workspace_id: str, task_id: str
) -> None:
    if task_id not in {t.id for t in ws.meta.tasks}:
        raise HTTPException(
            status_code=404,
            detail=f"task {task_id!r} not found in workspace {workspace_id!r}",
        )


def _append_scenario_override_audit(
    workspace_id: str, task_id: str, *, scenario_ids: list[str] | None, waived: bool | None
) -> None:
    append_jsonl_audit(
        logs_root() / _SCENARIO_OVERRIDE_AUDIT_FILE,
        {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "scenario_ids": scenario_ids,
            "waived": waived,
        },
        log_label="scenario_override",
    )


def _append_oob_completion_audit(
    workspace_id: str, task_id: str, pr_number: int, *, path: str
) -> None:
    append_jsonl_audit(
        logs_root() / _OOB_COMPLETION_AUDIT_FILE,
        {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "pr_number": pr_number,
            "path": path,
        },
        log_label="oob_completion",
    )


async def perform_task_team_dispatch(
    workspace_id: str,
    task_id: str,
    body: DispatchBody,
    queue: Queue,
    pubsub: PubSub,
    workspaces_root: Path,
    *,
    record_redispatch: bool,
) -> DispatchResponse:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    team = resolve_dispatchable_team(body.team)
    task_id = await run_in_threadpool(owning_task_id, ws, task_id)
    await run_in_threadpool(assert_team_dispatchable, ws, task_id, body.team)
    instructions = normalize_instructions(body.instructions)
    prior = await run_in_threadpool(prior_handover_for_task, ws, task_id)
    if record_redispatch and prior is not None:
        ws = await run_in_threadpool(
            workspace_mod.record_intervention,
            ws,
            workspace_mod.InterventionAction.REDISPATCH,
        )
    merge_gate_decision, instructions = await apply_merge_gate_to_manual_dispatch(
        ws, prior, instructions
    )
    outcome = await run_in_threadpool(
        dispatch_team_core,
        workspace=ws,
        task_id=task_id,
        team=team,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=enqueue_with_pending_workflow,
        instructions=instructions,
        merge_gate=merge_gate_decision,
    )
    await maybe_log_counter_case_waiver(
        body.counter_case, task_id, task_title_for_audit(ws, task_id)
    )
    return DispatchResponse(job_id=outcome.job.id, invocation_id=outcome.inv_id)


@tasks_router.post("/dispatch", status_code=202, responses=_NOT_FOUND_OR_BAD_TEAM)
async def dispatch_task_team(
    workspace_id: str,
    task_id: str,
    body: DispatchBody,
    queue: QueueDep,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
) -> DispatchResponse:
    return await perform_task_team_dispatch(
        workspace_id,
        task_id,
        body,
        queue,
        pubsub,
        workspaces_root,
        record_redispatch=True,
    )


@tasks_router.post("/complete", responses=_COMPLETE_TASK_RESPONSES)
async def complete_out_of_band_task(
    workspace_id: str,
    task_id: str,
    body: CompleteOutOfBandBody,
    queue: QueueDep,
    dispatcher: DispatcherDep,
    workspaces_root: WorkspacesRootDep,
    index: WorkspaceIndexDep,
) -> WorkspaceDetail:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    _reject_no_subtasks_or_archived(ws)
    _reject_unknown_task(ws, workspace_id, task_id)
    if body.force:
        path = "force"
    else:
        verified = await run_in_threadpool(verify_pr_merged, body.pr_number)
        if not verified:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"PR #{body.pr_number} is not verified merged; "
                    "retry with force=true to override"
                ),
            )
        path = "verified"
    await run_in_threadpool(
        _append_oob_completion_audit, workspace_id, task_id, body.pr_number, path=path
    )
    try:
        await run_in_threadpool(
            dispatcher.complete_out_of_band_task, workspace_id, task_id, body.pr_number
        )
    except OutOfBandCompletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await run_in_threadpool(
        workspace_mod.record_intervention, ws, workspace_mod.InterventionAction.FORCE_COMPLETE
    )
    return await get_workspace(
        task_id=workspace_id, queue=queue, workspaces_root=workspaces_root, index=index
    )


@tasks_router.put(
    "/scenarios",
    responses=_TASK_SCENARIOS_RESPONSES,
    dependencies=[Depends(require_control_token)],
)
async def override_task_scenarios(
    workspace_id: str,
    task_id: str,
    body: TaskScenariosBody,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
    index: WorkspaceIndexDep,
) -> WorkspaceDetail:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    _reject_no_subtasks_or_archived(ws)
    _reject_unknown_task(ws, workspace_id, task_id)
    await run_in_threadpool(
        _append_scenario_override_audit,
        workspace_id,
        task_id,
        scenario_ids=None if body.scenarios is None else [s.id for s in body.scenarios],
        waived=body.waived,
    )
    await run_in_threadpool(
        workspace_mod.update_task_scenarios,
        ws,
        task_id,
        scenarios=body.scenarios,
        waived=body.waived,
    )
    await run_in_threadpool(
        workspace_mod.record_intervention, ws, workspace_mod.InterventionAction.SCENARIO_OVERRIDE
    )
    return await get_workspace(
        task_id=workspace_id, queue=queue, workspaces_root=workspaces_root, index=index
    )


@workspaces_router.post("", status_code=201, responses=_CREATE_WORKSPACE_RESPONSES)
async def create_workspace(
    body: CreateWorkspaceBody,
    queue: QueueDep,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
    build_chat_root: BuildChatSessionsRootDep,
    explore_chat_root: ExploreChatSessionsRootDep,
) -> CreateWorkspaceResponse:
    engine_key = await run_in_threadpool(
        resolve_engine_key,
        body.engine_key,
        body.project_id,
        engine_key_provided="engine_key" in body.model_fields_set,
    )
    tasks_to_create = [t.to_task() for t in body.tasks]
    reject_uncovered_clauses(body, tasks_to_create)
    reject_unseedable_teams(body, engine_key)
    surface = prep_chat_mod.dispatch_surface(body.prep_chat_kind)
    scope_findings = await scan_scope_findings(body, workspaces_root, engine_key)
    create_fn = build_create_fn(
        body, workspaces_root, tasks_to_create, scope_findings, engine_key, surface
    )
    ws = await create_within_lane_limit(
        queue, root=workspaces_root, engine_key=engine_key, create=create_fn
    )
    await run_in_threadpool(append_predispatch_log, ws.task_id, body.title, scope_findings)
    await run_in_threadpool(_clause_audit.append_route_audit_row, ws)
    if body.team is not None:
        await refine_and_dispatch_root_task(
            ws, body.team, body, queue, pubsub, workspaces_root,
            functools.partial(
                write_brief_refine_source, body.prep_chat_kind, body.prep_chat_session_id,
                build_root=build_chat_root, explore_root=explore_chat_root,
            ),
            create_dispatch_skip_decision(),
        )
    await archive_prep_chat_best_effort(body, ws, build_chat_root, explore_chat_root)
    pubsub.publish(ws.task_id, TeamEvent("workspace_created", {"task_id": ws.task_id}))
    root_task_ids = [t.id for t in ws.meta.tasks if not t.depends_on] or [ws.task_id]
    return CreateWorkspaceResponse(task_id=ws.task_id, root_task_ids=root_task_ids)


async def _unblock_by_key(
    queue: Queue, rows: list[WorkspaceRow]
) -> dict[str, unblock_ledger_mod.UnblockRecord]:
    stuck_keys = stuck_workspace_keys((r.task_id, r.status) for r in rows)
    if not stuck_keys:
        return {}
    return await run_in_threadpool(unblock_ledger_mod.records_for, queue, stuck_keys)


@workspaces_router.get("")
async def list_workspaces_endpoint(
    queue: QueueDep,
    index: WorkspaceIndexDep,
    include: Annotated[str | None, Query()] = None,
    bucket: Annotated[str | None, Query(pattern="^archived$")] = None,
) -> list[WorkspaceSummary]:
    if bucket == "archived":
        rows = await run_in_threadpool(index.archived_rows)
    else:
        rows = await run_in_threadpool(index.rows)
    if include != "triage":
        rows = [r for r in rows if not is_hidden_by_default(r.team, r.title)]
    counts = await run_in_threadpool(
        queue.count_jobs_by_workspace, workspace_ids=[r.task_id for r in rows]
    )
    titles = await run_in_threadpool(_resolve_project_titles, rows)
    configured_auto_archive_days = retention_mod.resolve_min_age_days(log_invalid=False)
    now = datetime.now(UTC)
    unblock_by_key = await _unblock_by_key(queue, rows)
    return [
        _summary_from_row(
            r,
            counts.get(r.task_id, 0),
            titles.get(r.task_id),
            configured_auto_archive_days,
            now,
            unblock_by_key.get(r.task_id),
        )
        for r in rows
    ]


async def _detail_unblock(
    queue: Queue, ws: workspace_mod.Workspace, task_id: str
) -> unblock_ledger_mod.UnblockRecord | None:
    if not stuck_workspace_keys([(ws.task_id, ws.meta.status)]):
        return None
    return await run_in_threadpool(unblock_ledger_mod.last_record, queue, task_id)


async def _fetch_queue_derived(
    queue: Queue, task_id: str
) -> tuple[bool, bool, int, PendingDeployGate | None]:
    running = await run_in_threadpool(
        queue.list_jobs_for_workspace, task_id, status=JobStatus.RUNNING
    )
    resumable_failed = await run_in_threadpool(has_resumable_failed_job, queue, task_id)
    requeue_count = await run_in_threadpool(queue.requeue_count_for_task, task_id)
    pending_deploy_gate = await run_in_threadpool(resolve_pending_deploy_gate, queue, task_id)
    return bool(running), resumable_failed, requeue_count, pending_deploy_gate


@workspaces_router.get("/{task_id}", responses=_NOT_FOUND)
async def get_workspace(
    task_id: str,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
    index: WorkspaceIndexDep,
    include: Annotated[str | None, Query()] = None,
) -> WorkspaceDetail:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    if is_hidden_by_default(ws.meta.team, ws.meta.title) and include != "triage":
        raise HTTPException(status_code=404, detail=_WORKSPACE_NOT_FOUND_DESC)
    handovers = await run_in_threadpool(_list_handovers, ws)
    latest = handovers[-1] if handovers else None
    history = await run_in_threadpool(read_history_entries, ws.path)
    has_running, has_resumable_failed, requeue_count, pending_deploy_gate = (
        await _fetch_queue_derived(queue, task_id)
    )
    summary = await fetch_manager_summary(ws.path, ws.meta.updated_at, handovers)
    outcome = await fetch_terminal_outcome(ws, handovers, queue)
    row = await run_in_threadpool(index.refresh_one, task_id)
    cost_breakdown = (
        workspace_cost_mod.synthesize_cost_breakdown(row.cost_totals) if row is not None else []
    )
    active_seconds, lead = detail_timing(ws, row)
    prep_chat_ref = await run_in_threadpool(prep_chat_mod.load_ref, ws.path)
    unblock = await _detail_unblock(queue, ws, task_id)
    return WorkspaceDetail(
        task_id=ws.task_id,
        title=ws.meta.title,
        description=ws.meta.description,
        status=ws.meta.status,
        revisit_counts=dict(ws.meta.revisit_counts),
        prep_chat=prep_chat_ref,
        created_at=ws.meta.created_at,
        updated_at=ws.meta.updated_at,
        history=history,
        latest_handover=latest,
        has_running_job=has_running,
        has_resumable_failed_job=has_resumable_failed,
        requeue_count=requeue_count,
        tasks=list(ws.meta.tasks),
        scope_audit=list(ws.meta.scope_audit),
        requirements=ws.meta.requirements,
        assumptions=ws.meta.assumptions,
        note=ws.meta.note,
        manager_summary=summary,
        terminal_outcome=outcome,
        park_reason=ws.meta.park_reason,
        blockers=list(ws.meta.blockers),
        team=ws.meta.team,
        project_id=ws.meta.project_id,
        engine_key=ws.meta.engine_key,
        total_cost_usd=row.total_cost_usd if row is not None else None,
        cost_breakdown=cost_breakdown,
        active_seconds=active_seconds,
        lead_seconds=lead,
        pending_deploy_gate=pending_deploy_gate,
        live_at=ws.meta.live_at,
        unblock=unblock,
        pr_number=latest_pr_number(handovers),
    )


@workspaces_router.put("/{task_id}/note", responses=_NOT_FOUND)
async def update_workspace_note(
    task_id: str,
    body: NoteBody,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
) -> NoteResponse:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    ws = await run_in_threadpool(workspace_mod.update_note, ws, body.note)
    pubsub.publish(task_id, TeamEvent("note_updated", {"task_id": task_id}))
    return NoteResponse(note=ws.meta.note)


@workspaces_router.get("/{task_id}/handovers", responses=_NOT_FOUND)
async def get_workspace_handovers(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> list[Handover]:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    return await run_in_threadpool(_list_handovers, ws)


@workspaces_router.get("/{task_id}/delivery", responses=_NOT_FOUND_OR_BAD_STATE)
async def get_workspace_delivery(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> WorkspaceDelivery:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    if ws.meta.status != Status.COMPLETED:
        raise HTTPException(status_code=409, detail="workspace not completed")
    handovers = await run_in_threadpool(_list_handovers, ws)
    pr_number = latest_pr_number(handovers)
    if pr_number is None:
        raise HTTPException(status_code=409, detail="no PR associated with workspace")
    target = forge_target_for_ws(ws)
    return await run_in_threadpool(fetch_pr_delivery, pr_number, target)


@workspaces_router.get(
    "/{task_id}/artifacts",
    responses={
        400: {"description": "path is outside workspace"},
        404: {"description": _WORKSPACE_NOT_FOUND_DESC},
    },
)
async def get_workspace_artifact(
    task_id: str,
    path: str,
    workspaces_root: WorkspacesRootDep,
) -> FileResponse:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    ws_root = ws.path.resolve()
    resolved = (ws_root / path).resolve()
    if not resolved.is_relative_to(ws_root):
        raise HTTPException(status_code=400, detail="path is outside workspace")
    if not await run_in_threadpool(resolved.is_file):
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(resolved)


@workspaces_router.post("/{task_id}/dispatch", status_code=308)
async def dispatch_team(task_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/api/workspaces/{task_id}/tasks/{task_id}/dispatch",
        status_code=308,
    )


@workspaces_router.post(
    "/{task_id}/stop",
    status_code=202,
    responses=_STOP_RESPONSES,
)
async def stop_workspace(
    task_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    dispatcher: DispatcherDep,
    workspaces_root: WorkspacesRootDep,
) -> StopResponse:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    if ws.meta.status in {Status.ARCHIVED, Status.COMPLETED, Status.PAUSED}:
        raise HTTPException(
            status_code=409,
            detail=f"workspace is {ws.meta.status.value}; cannot stop",
        )
    running = await run_in_threadpool(
        queue.list_jobs_for_workspace, task_id, status=JobStatus.RUNNING
    )
    if not running:
        raise HTTPException(status_code=404, detail="no running job to stop")
    interrupted = await run_in_threadpool(dispatcher.interrupt_task, task_id)
    if not interrupted:
        raise HTTPException(status_code=404, detail="no running job to stop")
    await run_in_threadpool(workspace_mod.transition, ws, Status.PAUSED)
    pubsub.publish(task_id, TeamEvent("workspace_paused", {"task_id": task_id}))
    return StopResponse(job_id=running[0].id)


@workspaces_router.post(
    "/{task_id}/resume",
    status_code=202,
    responses=_RESUME_RESPONSES,
)
async def resume_workspace(
    task_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    dispatcher: DispatcherDep,
    workspaces_root: WorkspacesRootDep,
) -> ResumeResponse:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    can_resume_failed = False
    if ws.meta.status == Status.AWAITING_INPUT:
        can_resume_failed = await run_in_threadpool(has_resumable_failed_job, queue, task_id)
    if ws.meta.status != Status.PAUSED and not can_resume_failed:
        raise HTTPException(
            status_code=409,
            detail=f"workspace is {ws.meta.status.value}; cannot resume",
        )
    await run_in_threadpool(dispatcher.resume_task, task_id)
    ws = await run_in_threadpool(
        workspace_mod.record_intervention, ws, workspace_mod.InterventionAction.RESUME
    )
    await run_in_threadpool(workspace_mod.transition, ws, Status.ACTIVE)
    queued = await run_in_threadpool(
        queue.list_jobs_for_workspace, task_id, status=JobStatus.PENDING
    )
    pubsub.publish(task_id, TeamEvent("workspace_resumed", {"task_id": task_id}))
    return ResumeResponse(queued_jobs=len(queued))


@workspaces_router.post(
    "/{task_id}/archive",
    status_code=204,
    responses=_ARCHIVE_RESPONSES,
)
async def archive_workspace(
    task_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
    dispatcher: OptionalDispatcherDep,
    body: ArchiveRequest | None = None,
) -> Response:
    await read_workspace_or_404(task_id, workspaces_root)
    outcome = await run_in_threadpool(
        archive_or_refuse, dispatcher, task_id, queue, workspaces_root, body
    )
    if not outcome.archived:
        raise HTTPException(status_code=409, detail=outcome.detail)
    pubsub.publish(task_id, TeamEvent("workspace_archived", {"task_id": task_id}))
    return Response(status_code=204)


@workspaces_router.post(
    "/{task_id}/unarchive",
    status_code=204,
    responses=_UNARCHIVE_RESPONSES,
)
async def unarchive_workspace(
    task_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    index: WorkspaceIndexDep,
    workspaces_root: WorkspacesRootDep,
) -> Response:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    if ws.meta.status == Status.ACTIVE:
        return Response(status_code=204)
    if ws.meta.status != Status.ARCHIVED:
        raise HTTPException(status_code=409, detail=_WORKSPACE_NOT_ARCHIVED_DETAIL)
    await run_in_threadpool(restore_from_archive, ws, queue)
    await run_in_threadpool(index.invalidate, task_id)
    pubsub.publish(task_id, TeamEvent("workspace_unarchived", {"task_id": task_id}))
    return Response(status_code=204)


@workspaces_router.delete(
    "/{task_id}",
    status_code=204,
    responses=_DELETE_WORKSPACE_RESPONSES,
)
async def delete_workspace(
    task_id: str,
    queue: QueueDep,
    pubsub: PubSubDep,
    index: WorkspaceIndexDep,
    workspaces_root: WorkspacesRootDep,
    dispatcher: OptionalDispatcherDep,
) -> Response:
    await read_workspace_or_404(task_id, workspaces_root)
    result = await run_in_threadpool(retire_workspace, dispatcher, task_id, queue, workspaces_root)
    if not result.deleted:
        raise HTTPException(status_code=409, detail=result.blocked_reason)
    await run_in_threadpool(index.invalidate, task_id)
    pubsub.publish(task_id, TeamEvent("workspace_deleted", {"task_id": task_id}))
    return Response(status_code=204)


@workspaces_router.get("/{task_id}/invocations", responses=_NOT_FOUND)
async def list_invocations_endpoint(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> list[str]:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    return await run_in_threadpool(workflow_mod.list_invocations, ws)


@workspaces_router.get(
    "/{task_id}/invocations/{inv_id}",
    responses=_INVOCATION_NOT_FOUND,
)
async def get_invocation(
    task_id: str,
    inv_id: str,
    workspaces_root: WorkspacesRootDep,
) -> WorkflowDoc:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    path = _safe_workflow_path(ws, inv_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown invocation: {inv_id}")
    try:
        return await run_in_threadpool(workflow_mod.load_workflow, path)
    except WorkflowError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@workspaces_router.get(
    "/{task_id}/invocations/{inv_id}/agents/{role}/stream",
    responses=_STREAM_NOT_FOUND,
)
async def get_agent_stream(
    task_id: str,
    inv_id: str,
    role: str,
    workspaces_root: WorkspacesRootDep,
) -> list[dict[str, Any]]:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    path = _safe_stream_path(ws, inv_id, role)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no stream for agent {role!r} in {inv_id}",
        )
    return await run_in_threadpool(read_stream_events, path)


@workspaces_router.get(
    "/{task_id}/invocations/{inv_id}/agents/{node_id}/result",
    responses=_NODE_DOC_NOT_FOUND,
)
async def get_agent_result(
    task_id: str,
    inv_id: str,
    node_id: str,
    workspaces_root: WorkspacesRootDep,
) -> workflow_mod.NodeResultDoc:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    path = _safe_result_path(ws, inv_id, node_id)
    doc = await run_in_threadpool(workflow_mod.read_node_result_doc, path)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"no result for agent {node_id!r} in {inv_id}",
        )
    return doc


@workspaces_router.get(
    "/{task_id}/invocations/{inv_id}/agents/{node_id}/config",
    responses=_NODE_DOC_NOT_FOUND,
)
async def get_agent_config(
    task_id: str,
    inv_id: str,
    node_id: str,
    workspaces_root: WorkspacesRootDep,
) -> workflow_mod.NodeConfigDoc:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    path = _safe_config_path(ws, inv_id, node_id)
    doc = await run_in_threadpool(workflow_mod.read_node_config, path)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"no config for agent {node_id!r} in {inv_id}",
        )
    return doc


@workspaces_router.get("/{task_id}/events", responses=_NOT_FOUND)
async def workspace_events(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
    pubsub: PubSubDep,
    subtask_id: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    await read_workspace_or_404(task_id, workspaces_root)
    return StreamingResponse(
        workspace_event_stream(task_id, pubsub, filter_task_id=subtask_id),
        media_type=SSE_MEDIA,
        headers=SSE_HEADERS,
    )
