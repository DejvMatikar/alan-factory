from __future__ import annotations

import logging
from pathlib import Path

from alan import lifecycle
from alan import park as park_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.events import PubSub
from alan.queue import JobStatus, Queue
from alan.teams._base import TeamEvent
from alan.workspace import Status, Workspace

logger = logging.getLogger("alan.reconciler")

_ORPHAN_STATUSES: frozenset[Status] = frozenset({Status.AWAITING_INPUT, Status.ACTIVE})
RESTART_PARK_MESSAGE = (
    "A service restart interrupted this run and automatic recovery could not "
    "resume, re-derive, or re-run it. Re-dispatch this workspace from its "
    "own page."
)


def _latest_invocation_id(ws: Workspace) -> str | None:
    inv_ids = workflow_mod.list_invocations(ws)
    return inv_ids[-1] if inv_ids else None


def _load_workflow_doc(ws: Workspace, inv_id: str) -> workflow_mod.WorkflowDoc | None:
    try:
        return workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    except (workflow_mod.WorkflowError, OSError):
        return None


def _orphan_eligible(ws: Workspace) -> bool:
    return ws.meta.status in _ORPHAN_STATUSES and not lifecycle.held_by_unarchive(
        ws.meta.status, ws.meta.park_reason
    )


def _orphaned_inv_id(ws: Workspace) -> str | None:
    if not _orphan_eligible(ws):
        return None
    inv_id = _latest_invocation_id(ws)
    if inv_id is None:
        return None
    doc = _load_workflow_doc(ws, inv_id)
    if doc is None:
        return None
    if any(task.status == "interrupted" for task in doc.tasks):
        return inv_id
    return None


def find_orphaned_interrupted_workspaces(
    *, workspaces_root: Path | None
) -> list[tuple[Workspace, str]]:
    result: list[tuple[Workspace, str]] = []
    for ws in workspace_mod.list_workspaces(root=workspaces_root):
        inv_id = _orphaned_inv_id(ws)
        if inv_id is not None:
            result.append((ws, inv_id))
    return result


def _has_live_job(queue: Queue, ws: Workspace) -> bool:
    return bool(queue.list_jobs_for_workspace(ws.workspace_id, status=JobStatus.RUNNING))


def _park_orphan(ws: Workspace) -> None:
    workspace_mod.transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=park_mod.ParkReason.CRASH,
        blockers=park_mod.synthetic_blocker(RESTART_PARK_MESSAGE),
    )


def _publish_orphan_parked(pubsub: PubSub, ws: Workspace) -> None:
    try:
        pubsub.publish(
            ws.workspace_id,
            TeamEvent("workspace_interrupted", {"task_id": ws.task_id, "inv_id": ""}),
        )
    except Exception:  # noqa: BLE001 - subscriber faults must not stall the sweep
        logger.exception(
            "orphan reconcile: publish workspace_interrupted failed for %s", ws.task_id
        )


def reconcile_orphaned_workflows(
    *, queue: Queue, workspaces_root: Path | None, pubsub: PubSub
) -> list[str]:
    parked: list[str] = []
    for ws, _inv_id in find_orphaned_interrupted_workspaces(workspaces_root=workspaces_root):
        if _has_live_job(queue, ws):
            continue
        try:
            _park_orphan(ws)
        except Exception:  # noqa: BLE001 - one bad workspace must not abort the sweep
            logger.exception("orphan reconcile: failed to park workspace %s", ws.task_id)
            continue
        _publish_orphan_parked(pubsub, ws)
        parked.append(ws.task_id)
    return parked


__all__ = [
    "RESTART_PARK_MESSAGE",
    "find_orphaned_interrupted_workspaces",
    "reconcile_orphaned_workflows",
]
