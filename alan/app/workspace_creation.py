from __future__ import annotations

import functools
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

import alan.dispatch_provenance as provenance_mod
import alan.merge_gate as merge_gate_mod
import alan.project as project_mod
import alan.workflow as workflow_mod
import alan.workspace as workspace_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.app.workspace_create_models import CreateWorkspaceBody
from alan.dispatch import assert_team_dispatchable, dispatch_team_core
from alan.engine_config import EngineConfig, default_engine_config, get_registered
from alan.engine_config import lookup_by_slug as _lookup_engine_by_slug
from alan.engine_preflight import assert_engine_ready
from alan.errors import AppError
from alan.events import PubSub
from alan.predispatch_scan import scan_predispatch
from alan.project_doc import read_scope_section
from alan.queue import Queue
from alan.reconciler import DEP_SWEEP_TITLE as _DEP_SWEEP_TITLE
from alan.roots import logs_root
from alan.scope_audit import CounterCase, ScopeGapFinding
from alan.teams import MANUALLY_DISPATCHABLE_TEAMS, TEAMS, Team
from alan.teams._audit import append_jsonl_audit
from alan.teams.dep_advisory import DEP_ADVISORY_TITLE
from alan.workspace import Task

logger = logging.getLogger("alan.server")

_PREDISPATCH_LOG_ENV = "ALAN_PREDISPATCH_LOG"
_PREDISPATCH_LOG_FILE = "predispatch_findings.jsonl"
_COUNTER_CASE_WAIVER_AUDIT_FILE = "counter_case_waiver_audit.jsonl"
_INTERNAL_TEAM_DETAIL = (
    "team {name!r} is internal; it is auto-routed by the dispatcher "
    "(e.g. decomposer fires after implementation_plan, dependency_sweep runs on "
    "its own weekly cadence) and cannot be dispatched manually"
)


def _predispatch_log_path() -> Path:
    override = os.environ.get(_PREDISPATCH_LOG_ENV, "").strip()
    return Path(override) if override else logs_root() / _PREDISPATCH_LOG_FILE


def append_predispatch_log(task_id: str, title: str, findings: list[ScopeGapFinding]) -> None:
    entry: dict[str, object] = {
        "task_id": task_id,
        "title": title,
        "findings": [f.model_dump() for f in findings],
    }
    append_jsonl_audit(_predispatch_log_path(), entry, log_label="predispatch")


def _append_counter_case_waiver_audit(
    task_id: str, title: str, counter_case: CounterCase
) -> None:
    entry: dict[str, object] = {
        "task_id": task_id,
        "title": title,
        "counter_case": counter_case.model_dump(),
        "human_proceeded": True,
    }
    append_jsonl_audit(
        logs_root() / _COUNTER_CASE_WAIVER_AUDIT_FILE, entry, log_label="counter_case_waiver"
    )


async def maybe_log_counter_case_waiver(
    counter_case: CounterCase | None, task_id: str, title: str
) -> None:
    if counter_case is None:
        return
    await run_in_threadpool(_append_counter_case_waiver_audit, task_id, title, counter_case)


def is_hidden_by_default(team: str | None, title: str) -> bool:
    if team == "triage":
        return True
    if team == "dependency_sweep":
        return title in {_DEP_SWEEP_TITLE, DEP_ADVISORY_TITLE}
    return False


def resolve_dispatchable_team(name: str) -> Team:
    team = TEAMS.get(name)
    if team is None:
        raise AppError(status_code=400, detail=f"unknown team: {name}")
    if name not in MANUALLY_DISPATCHABLE_TEAMS:
        raise AppError(
            status_code=400, detail=_INTERNAL_TEAM_DETAIL.format(name=name)
        )
    return team


def _project_engine_key(project_id: str) -> tuple[str | None, bool]:
    project = project_mod.cached_read(project_id, {})
    if project is None:
        return None, False
    return project.engine_key, True


def _bound_engine(engine_key: str, *, from_project: bool) -> EngineConfig:
    cfg = _lookup_engine_by_slug(engine_key)
    if cfg is None:
        detail = (
            "project's bound engine no longer exists"
            if from_project
            else f"unknown engine: {engine_key!r}"
        )
        raise AppError(status_code=400, detail=detail)
    return cfg


def resolve_engine_key(
    engine_key: str | None, project_id: str | None, *, engine_key_provided: bool
) -> str | None:
    from_project = False
    if engine_key is None and not engine_key_provided and project_id is not None:
        engine_key, from_project = _project_engine_key(project_id)
    if engine_key is None:
        assert_engine_ready(get_registered() or default_engine_config())
        return None
    assert_engine_ready(_bound_engine(engine_key, from_project=from_project))
    return engine_key


def normalize_instructions(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned if cleaned else None


def task_title_for_audit(ws: workspace_mod.Workspace, task_id: str) -> str:
    subtask = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if subtask is not None and subtask.title:
        return subtask.title
    return ws.meta.title


def build_create_fn(
    body: CreateWorkspaceBody,
    workspaces_root: Path,
    tasks_to_create: list[Task],
    scope_findings: list[ScopeGapFinding],
    engine_key: str | None,
    surface: provenance_mod.DispatchSurface,
) -> functools.partial[workspace_mod.Workspace]:
    return functools.partial(
        workspace_mod.create,
        body.title,
        body.description,
        root=workspaces_root,
        tasks=tasks_to_create,
        dependencies=body.dependencies,
        scope_audit=scope_findings,
        requirements=body.requirements,
        engine_key=engine_key,
        project_id=body.project_id,
        roadmap_item_id=body.roadmap_item_id,
        provenance=provenance_mod.DispatchProvenance.for_request(
            surface, body.dispatch_actor, body.prep_chat_session_id
        ),
        pending_first_hop=body.team,
    )


async def _resolve_create_anti_scope(project_id: str | None) -> str | None:
    if not project_id:
        return None
    try:
        return await run_in_threadpool(read_scope_section, project_id)
    except Exception:  # noqa: BLE001 - anti-scope resolution must never block workspace creation
        logger.warning("create_workspace: anti-scope resolution failed", exc_info=True)
        return None


async def scan_scope_findings(
    body: CreateWorkspaceBody, workspaces_root: Path, engine_key: str | None
) -> list[ScopeGapFinding]:
    anti_scope = await _resolve_create_anti_scope(body.project_id)
    try:
        return await run_in_threadpool(
            scan_predispatch,
            body.title,
            body.description,
            workspaces_root=workspaces_root,
            anti_scope=anti_scope,
            scope_engine_key=engine_key,
        )
    except Exception:  # noqa: BLE001 - scan failure must never block workspace creation
        logger.warning("create_workspace: predispatch scan failed", exc_info=True)
        return []


def should_refine(ws: workspace_mod.Workspace) -> bool:
    return not workflow_mod.list_invocations(ws)


async def dispatch_root_task_or_rollback(
    ws: workspace_mod.Workspace,
    team_name: str,
    instructions: str | None,
    counter_case: CounterCase | None,
    queue: Queue,
    pubsub: PubSub,
    workspaces_root: Path,
    *,
    merge_gate: merge_gate_mod.MergeGateDecision,
    dispatch_as: str | None = None,
) -> None:
    try:
        requested_team = resolve_dispatchable_team(team_name)
        await run_in_threadpool(assert_team_dispatchable, ws, ws.task_id, team_name)
        team = TEAMS[dispatch_as] if dispatch_as is not None else requested_team
        await run_in_threadpool(
            dispatch_team_core,
            workspace=ws,
            task_id=ws.task_id,
            team=team,
            queue=queue,
            pubsub=pubsub,
            enqueue_workflow=enqueue_with_pending_workflow,
            instructions=instructions,
            merge_gate=merge_gate,
        )
    except Exception:  # noqa: BLE001 - any failure here must roll back the create
        try:
            await run_in_threadpool(workspace_mod.delete, ws.task_id, root=workspaces_root)
        except Exception:  # noqa: BLE001 - rollback is best-effort; never mask the original error
            logger.warning(
                "create_workspace: rollback delete failed for %s", ws.task_id, exc_info=True
            )
        raise
    await maybe_log_counter_case_waiver(
        counter_case, ws.task_id, task_title_for_audit(ws, ws.task_id)
    )


async def refine_and_dispatch_root_task(
    ws: workspace_mod.Workspace,
    team: str,
    body: CreateWorkspaceBody,
    queue: Queue,
    pubsub: PubSub,
    workspaces_root: Path,
    write_refine_source: Callable[[workspace_mod.Workspace], Awaitable[None]],
    merge_gate: merge_gate_mod.MergeGateDecision,
) -> None:
    cleaned_instructions = normalize_instructions(body.instructions)
    refine = should_refine(ws)
    if refine and cleaned_instructions:
        ws = await run_in_threadpool(
            workspace_mod.set_sticky_instructions,
            ws, ws.task_id, cleaned_instructions, origin="dispatch",
        )
    if refine:
        await write_refine_source(ws)
    await dispatch_root_task_or_rollback(
        ws, team, None if refine else cleaned_instructions, body.counter_case,
        queue, pubsub, workspaces_root,
        merge_gate=merge_gate, dispatch_as="brief_refine" if refine else None,
    )
