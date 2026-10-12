from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel

from alan.queue import Job, Queue
from alan.sticky_instructions import NON_CONSUMING_TEAMS, InstructionOrigin
from alan.workflow import list_invocations
from alan.workspace import Workspace

DispatchInstructionsStatus = Literal["attached", "none", "not_delivered", "unknown"]


class DispatchInstructionsReadback(BaseModel):
    status: DispatchInstructionsStatus
    text: str | None = None
    origin: InstructionOrigin | None = None


def _jobs_by_invocation(queue: Queue, task_id: str) -> dict[str, Job]:
    by_invocation: dict[str, Job] = {}
    for job in queue.list_jobs_for_workspace(task_id):
        if job.triggered_by_invocation_id is not None:
            by_invocation[job.triggered_by_invocation_id] = job
    return by_invocation


def _origin(job: Job) -> InstructionOrigin | None:
    if job.instructions_origin is None:
        return None
    return cast(InstructionOrigin, job.instructions_origin)


def _readback_for_job(job: Job) -> DispatchInstructionsReadback:
    if not job.instructions:
        return DispatchInstructionsReadback(status="none")
    if job.team in NON_CONSUMING_TEAMS:
        return DispatchInstructionsReadback(
            status="not_delivered", text=job.instructions, origin=_origin(job)
        )
    return DispatchInstructionsReadback(
        status="attached", text=job.instructions, origin=_origin(job)
    )


def resolve_dispatch_instructions(
    queue: Queue, workspace: Workspace
) -> dict[str, DispatchInstructionsReadback]:
    by_invocation = _jobs_by_invocation(queue, workspace.task_id)
    result: dict[str, DispatchInstructionsReadback] = {}
    for inv_id in list_invocations(workspace):
        job = by_invocation.get(inv_id)
        result[inv_id] = (
            _readback_for_job(job)
            if job is not None
            else DispatchInstructionsReadback(status="unknown")
        )
    return result
