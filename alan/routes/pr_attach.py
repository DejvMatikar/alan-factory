from __future__ import annotations

import threading
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import alan.workspace as workspace_mod
from alan._dispatcher_base import forge_target_for_ws
from alan.dispatch import prior_handover_for_task
from alan.handover import record_pr_attachment
from alan.queue import JobStatus, Queue
from alan.reconciler import (
    _is_subtask_live,  # pyright: ignore[reportPrivateUsage]
)
from alan.roots import logs_root
from alan.routes import _deps
from alan.routes._deps import QueueDep, WorkspacesRootDep, read_workspace_or_404
from alan.teams._audit import append_jsonl_audit
from alan.teams._forge_errors import ForgeRateLimited, ForgeTransientError
from alan.teams._pr_branch import resolve_existing_branch
from alan.teams.triage_pregate import (
    _pr_matches_task,  # pyright: ignore[reportPrivateUsage]
)

pr_attach_router = APIRouter(prefix="/api/workspaces/{workspace_id}/tasks/{task_id}")

_lock = threading.Lock()
_AUDIT_FILE = "pr_attach_audit.jsonl"

_WORKSPACE_ARCHIVED_DETAIL = "workspace is archived"
_NO_PRIOR_HANDOVER_DETAIL = "no prior handover to amend"
_IN_FLIGHT_DETAIL_PREFIX = "work already in flight"

_ATTACH_RESPONSES: dict[int | str, dict[str, str]] = {
    **_deps.GUARDED,
    404: {"description": "workspace not found, task not found, or PR not found"},
    409: {
        "description": (
            f"{_WORKSPACE_ARCHIVED_DETAIL}, {_NO_PRIOR_HANDOVER_DETAIL}, "
            "or work in flight for the subtask"
        )
    },
    422: {"description": "PR head ref does not match the workspace's branch prefix"},
    503: {"description": "could not confirm the PR — forge unreachable"},
}


class AttachPrBody(BaseModel):
    pr_number: int = Field(ge=1)


class AttachOutcome(str, Enum):
    ATTACHED = "attached"
    ALREADY_ATTACHED = "already_attached"
    NO_SUBTASK_PIN = "no_subtask_pin"


class AttachPrResult(BaseModel):
    handover_id: str
    pr_number: int
    outcome: AttachOutcome


def _logs_dir() -> Path:
    return logs_root()


def _append_attach_audit(
    workspace_id: str, task_id: str, pr_number: int, *, path: str
) -> None:
    append_jsonl_audit(
        _logs_dir() / _AUDIT_FILE,
        {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "pr_number": pr_number,
            "path": path,
        },
        log_label="pr_attach",
    )


def _refuse(
    workspace_id: str,
    task_id: str,
    pr_number: int,
    *,
    path: str,
    status_code: int,
    detail: str,
) -> HTTPException:
    _append_attach_audit(workspace_id, task_id, pr_number, path=path)
    return HTTPException(status_code=status_code, detail=detail)


def _in_flight_detail(live_key: str, workspace_id: str, queue: Queue) -> str:
    jobs = [
        *queue.list_jobs(task_id=live_key, workspace_id=workspace_id, status=JobStatus.PENDING),
        *queue.list_jobs(task_id=live_key, workspace_id=workspace_id, status=JobStatus.RUNNING),
    ]
    named = ", ".join(f"job {j.id} ({j.team}, {j.status.value})" for j in jobs[:5])
    return f"{_IN_FLIGHT_DETAIL_PREFIX} for task {live_key!r}: {named}"


def _resolve_head_ref_or_refuse(
    ws: workspace_mod.Workspace, task_id: str, pr_number: int
) -> str:
    target = forge_target_for_ws(ws)
    try:
        ref = resolve_existing_branch(pr_number, ws.task_id, forge_target=target)
    except (ForgeTransientError, ForgeRateLimited) as exc:
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="forge_unreachable", status_code=503,
            detail=f"could not confirm PR #{pr_number} — forge unreachable",
        ) from exc
    if ref is None:
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="pr_not_found", status_code=404,
            detail=f"PR #{pr_number} not found in {target.repo.slug}",
        )
    if not _pr_matches_task(ref, ws.task_id):
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="prefix_mismatch", status_code=422,
            detail=(
                f"PR #{pr_number} head ref {ref!r} does not match expected "
                f"prefix teams/{ws.task_id}"
            ),
        )
    return ref


def _task_id_known(ws: workspace_mod.Workspace, task_id: str) -> bool:
    if not ws.meta.tasks:
        return True
    return any(t.id == task_id for t in ws.meta.tasks)


def _reject_if_unattachable(
    ws: workspace_mod.Workspace, task_id: str, pr_number: int
) -> None:
    if not _task_id_known(ws, task_id):
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="unknown_task", status_code=404,
            detail=f"task {task_id!r} not found in workspace {ws.task_id!r}",
        )
    if ws.meta.status == workspace_mod.Status.ARCHIVED:
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="archived", status_code=409, detail=_WORKSPACE_ARCHIVED_DETAIL,
        )


def _point_subtask_at(
    ws: workspace_mod.Workspace,
    task_id: str,
    subtask: workspace_mod.Task | None,
    handover_id: str,
) -> bool:
    if subtask is None:
        return False
    workspace_mod.update_task_status(
        ws, task_id, subtask.status, blocked_handover_id=handover_id,
    )
    return True


def _reject_if_in_flight(
    ws: workspace_mod.Workspace,
    task_id: str,
    subtask: workspace_mod.Task | None,
    pr_number: int,
    queue: Queue,
) -> None:
    live_key = subtask.id if subtask is not None else ws.workspace_id
    if _is_subtask_live(live_key, ws.workspace_id, queue):
        raise _refuse(
            ws.task_id, task_id, pr_number,
            path="in_flight", status_code=409,
            detail=_in_flight_detail(live_key, ws.workspace_id, queue),
        )


def _attach(
    ws: workspace_mod.Workspace,
    task_id: str,
    pr_number: int,
    workspaces_root: Path,
    queue: Queue,
) -> AttachPrResult:
    _reject_if_unattachable(ws, task_id, pr_number)
    # The whole read-check-write sequence is one critical section, now opened
    # by the liveness gate: reloading from disk only after acquiring the lock
    # is what lets the gate, the idempotency check, and the pin-repoint all
    # observe an attach a concurrent request just committed, instead of two
    # racing requests each reading the same stale, PR-less prior and each
    # appending their own amendment. Success here is a statement about state
    # at the moment the call returns — nothing in flight for this subtask —
    # never a promise about what the next dispatch will do.
    with _lock:
        ws = workspace_mod.read(ws.task_id, root=workspaces_root)
        subtask = next((t for t in ws.meta.tasks if t.id == task_id), None)
        _reject_if_in_flight(ws, task_id, subtask, pr_number, queue)
        prior = prior_handover_for_task(ws, task_id)
        if prior is None:
            raise _refuse(
                ws.task_id, task_id, pr_number,
                path="no_prior_handover", status_code=409, detail=_NO_PRIOR_HANDOVER_DETAIL,
            )
        if prior.pr_number == pr_number:
            pinned = _point_subtask_at(ws, task_id, subtask, prior.handover_id)
            _append_attach_audit(ws.task_id, task_id, pr_number, path="noop")
            return AttachPrResult(
                handover_id=prior.handover_id,
                pr_number=pr_number,
                outcome=(
                    AttachOutcome.ALREADY_ATTACHED if pinned else AttachOutcome.NO_SUBTASK_PIN
                ),
            )
        _resolve_head_ref_or_refuse(ws, task_id, pr_number)
        new_handover = record_pr_attachment(ws, prior, pr_number)
        pinned = _point_subtask_at(ws, task_id, subtask, new_handover.handover_id)
    _append_attach_audit(ws.task_id, task_id, pr_number, path="attached")
    workspace_mod.record_intervention(ws, workspace_mod.InterventionAction.ATTACH_PR)
    return AttachPrResult(
        handover_id=new_handover.handover_id,
        pr_number=pr_number,
        outcome=AttachOutcome.ATTACHED if pinned else AttachOutcome.NO_SUBTASK_PIN,
    )


@pr_attach_router.post(
    "/attach-pr",
    responses=_ATTACH_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def attach_pr(
    workspace_id: str,
    task_id: str,
    body: AttachPrBody,
    workspaces_root: WorkspacesRootDep,
    queue: QueueDep,
) -> AttachPrResult:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    return await run_in_threadpool(
        _attach, ws, task_id, body.pr_number, workspaces_root, queue
    )


__all__ = ["AttachOutcome", "AttachPrBody", "AttachPrResult", "attach_pr", "pr_attach_router"]
