from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from alan import sticky_instructions
from alan import workspace as workspace_mod
from alan.app.workspace_creation import normalize_instructions
from alan.dispatch import owning_task_id
from alan.dispatch_instructions import (
    DispatchInstructionsReadback,
    resolve_dispatch_instructions,
)
from alan.errors import AppError
from alan.roots import logs_root
from alan.routes._deps import (
    GUARDED,
    QueueDep,
    WorkspacesRootDep,
    read_workspace_or_404,
    require_control_token,
)
from alan.teams._audit import append_jsonl_audit

dispatch_instructions_router = APIRouter(prefix="/api/workspaces")

_AUDIT_FILE = "sticky_instruction_write_audit.jsonl"
_BLANK_TEXT_DETAIL = "instruction text is empty after trimming whitespace"
_TOO_LONG_DETAIL = (
    f"instruction text is longer than {sticky_instructions.MAX_COMPOSED_CHARS} characters "
    "after trimming whitespace"
)

_WRITE_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED,
    404: {"description": "workspace not found, or no single owning subtask on a decomposed one"},
    409: {"description": "workspace already holds the maximum number of sticky instructions"},
    422: {"description": "text is empty after trimming, or longer than the composition ceiling"},
}

IN_FLIGHT_NOTICE = (
    "A pass that is already running keeps the instructions it was enqueued with. The text "
    "reaches a later dispatch only if that dispatch is enqueued from a workspace read that "
    "follows this write, is routed by the dispatcher or triggered by answering a question, "
    "goes to a team that consumes instructions (not pr_merge, triage, dependency_sweep or "
    "deploy_verify), and is composed with generated instructions that leave room under the "
    "composition ceiling. A manual dispatch carries only the instructions supplied for it."
)

_WRITE_DESCRIPTION = (
    "Stores binding instruction text on the workspace's owning task without dispatching. "
    f"{IN_FLIGHT_NOTICE} The text is delivered in the `## User dispatch instructions` section, "
    "exactly as if it had been typed at dispatch time. "
    "The composition ceiling applies to the text after leading and trailing whitespace is "
    "trimmed. Writing the same text again leaves the stored state unchanged. The workspace "
    "metadata read-modify-write is not atomic: a write racing an in-flight dispatcher cycle can "
    "be overwritten by that cycle."
)


def _readback_key(ws: workspace_mod.Workspace, task_id: str) -> str:
    try:
        return owning_task_id(ws, task_id)
    except AppError:
        return task_id


@dispatch_instructions_router.get("/{task_id}/dispatch-instructions")
async def get_dispatch_instructions(
    task_id: str,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
) -> dict[str, DispatchInstructionsReadback]:
    try:
        ws = await run_in_threadpool(workspace_mod.read, task_id, root=workspaces_root)
    except workspace_mod.WorkspaceError:
        return {}
    return await run_in_threadpool(resolve_dispatch_instructions, queue, ws)


class StickyInstructionOut(BaseModel):
    text: str | None = None
    origin: Literal["dispatch", "generated"] | None = None


class StickyInstructionBody(BaseModel):
    text: str = Field(min_length=1)


class StickyInstructionWritten(BaseModel):
    task_id: str
    text: str
    origin: Literal["dispatch"]
    notice: str


def _validated_text(raw: str) -> str:
    text = normalize_instructions(raw)
    if text is None:
        raise HTTPException(status_code=422, detail=_BLANK_TEXT_DETAIL)
    if len(text) > sticky_instructions.MAX_COMPOSED_CHARS:
        raise HTTPException(status_code=422, detail=_TOO_LONG_DETAIL)
    return text


def _audit_write(workspace_id: str, task_id: str, text: str) -> None:
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE,
        {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "origin": "dispatch",
            "text": text,
            "length": len(text),
        },
        log_label="sticky_instruction_write",
    )


def _is_unchanged(ws: workspace_mod.Workspace, task_id: str, text: str) -> bool:
    stored = ws.meta.dispatch_instructions.get(task_id)
    return stored == workspace_mod.StickyInstruction(text=text, origin="dispatch")


async def _store(ws: workspace_mod.Workspace, task_id: str, text: str) -> None:
    try:
        await run_in_threadpool(
            workspace_mod.set_sticky_instructions, ws, task_id, text, origin="dispatch"
        )
    except workspace_mod.WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@dispatch_instructions_router.get("/{task_id}/sticky-instructions")
async def get_sticky_instructions(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> StickyInstructionOut:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    key = await run_in_threadpool(_readback_key, ws, task_id)
    sticky = ws.meta.dispatch_instructions.get(key)
    if sticky is None:
        return StickyInstructionOut()
    return StickyInstructionOut(text=sticky.text, origin=sticky.origin)


@dispatch_instructions_router.put(
    "/{task_id}/sticky-instructions",
    responses=_WRITE_RESPONSES,
    dependencies=[Depends(require_control_token)],
    description=_WRITE_DESCRIPTION,
)
async def put_sticky_instructions(
    task_id: str,
    body: StickyInstructionBody,
    workspaces_root: WorkspacesRootDep,
) -> StickyInstructionWritten:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    text = _validated_text(body.text)
    target = await run_in_threadpool(owning_task_id, ws, task_id)
    if not _is_unchanged(ws, target, text):
        await _store(ws, target, text)
    await run_in_threadpool(_audit_write, task_id, target, text)
    return StickyInstructionWritten(
        task_id=target, text=text, origin="dispatch", notice=IN_FLIGHT_NOTICE
    )


@dispatch_instructions_router.delete("/{task_id}/sticky-instructions", status_code=204)
async def delete_sticky_instructions(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> Response:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    key = await run_in_threadpool(_readback_key, ws, task_id)
    await run_in_threadpool(workspace_mod.clear_sticky_instructions, ws, key)
    return Response(status_code=204)
