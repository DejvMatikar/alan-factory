from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from alan import dispatch_provenance as provenance_mod
from alan import retention_registry
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, canonical_scope_key, lookup_by_slug
from alan.queue import JobStatus, Queue
from alan.teams import dep_advisory
from alan.teams.triage import RedMainRun
from alan.workspace import Status, Workspace

logger = logging.getLogger(__name__)

_TEAM_NAME = "dependency_sweep"
_DESCRIPTION = "Event-triggered Python advisory check on red main CI."


@dataclass(frozen=True)
class _Idle:
    workspace: Workspace
    created: bool


def _opted_in(cfg: EngineConfig) -> bool:
    sweep = cfg.dependency_sweep
    return not cfg.hidden and sweep.enabled and sweep.enable_python_vuln_scan


def _find_advisory_workspace(workspaces_root: Path | None, cfg: EngineConfig) -> Workspace | None:
    return next(
        (
            ws
            for ws in workspace_mod.list_workspaces(root=workspaces_root)
            if dep_advisory.is_advisory_workspace(ws)
            and canonical_scope_key(ws.meta.engine_key) == cfg.repo
        ),
        None,
    )


def _advisory_workspace_if_idle(
    queue: Queue, workspaces_root: Path | None, cfg: EngineConfig
) -> _Idle | None:
    ws = _find_advisory_workspace(workspaces_root, cfg)
    if ws is None:
        created = workspace_mod.create(
            dep_advisory.DEP_ADVISORY_TITLE,
            _DESCRIPTION,
            team=_TEAM_NAME,
            root=workspaces_root,
            engine_key=cfg.repo.slug,
            provenance=provenance_mod.DispatchProvenance(
                surface=provenance_mod.DispatchSurface.DEP_ADVISORY_CHECK
            ),
        )
        return _Idle(created, created=True)
    pending = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    running = queue.list_jobs(task_id=ws.task_id, status=JobStatus.RUNNING)
    return None if pending or running else _Idle(ws, created=False)


def _already_checked(queue: Queue, run: RedMainRun) -> bool:
    return queue.get_meta(dep_advisory.checked_run_key(run.slug)) == run.run_id


def _enqueue_check(queue: Queue, ws: Workspace, run: RedMainRun) -> None:
    retention_registry.seed_recurring_workspace(ws)
    if ws.meta.status != Status.ACTIVE:
        ws = workspace_mod.transition(ws, Status.ACTIVE)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team=_TEAM_NAME, tasks=[dep_advisory.ADVISORY_SPEC], source_task_id=ws.task_id
    )
    pending_key = dep_advisory.pending_run_key(run.slug)
    try:
        queue.set_meta(pending_key, run.run_id)
        queue.enqueue(ws.task_id, _TEAM_NAME, triggered_by_invocation_id=inv_id)
    except Exception:
        queue.delete_meta(pending_key)
        shutil.rmtree(workflow_mod.workflow_path(ws, inv_id).parent, ignore_errors=True)
        raise


def _seed_one(queue: Queue, workspaces_root: Path | None, run: RedMainRun) -> None:
    cfg = lookup_by_slug(run.slug)
    if cfg is None or not _opted_in(cfg) or _already_checked(queue, run):
        return
    idle = _advisory_workspace_if_idle(queue, workspaces_root, cfg)
    if idle is None:
        return
    try:
        _enqueue_check(queue, idle.workspace, run)
    except Exception:
        if idle.created:
            dep_advisory.discard_workspace(queue, workspaces_root, idle.workspace)
        raise


def seed_advisory_checks(
    queue: Queue, workspaces_root: Path | None, runs: Sequence[RedMainRun]
) -> None:
    for run in {run.slug: run for run in runs}.values():
        try:
            _seed_one(queue, workspaces_root, run)
        except Exception:  # noqa: BLE001 - one engine's failure must not block another
            logger.exception("dep_advisory_seed: seeding failed for %s", run.slug)
