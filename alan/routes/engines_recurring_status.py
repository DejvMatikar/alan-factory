from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from alan import recurring_status as recurring_status_mod
from alan.engine_config import RepoIdentity
from alan.routes._deps import PubSubDep, QueueDep, WorkspaceIndexDep

engines_recurring_status_router = APIRouter(prefix="/api/engines")

_ENGINE_NOT_FOUND_DETAIL = "engine not found"


@engines_recurring_status_router.get(
    "/{owner}/{repo}/recurring-status",
    responses={404: {"description": _ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_recurring_status(
    owner: str,
    repo: str,
    queue: QueueDep,
    workspace_index: WorkspaceIndexDep,
    pubsub: PubSubDep,
) -> recurring_status_mod.RecurringStatus:
    identity = RepoIdentity(owner=owner, repo=repo)
    result = await run_in_threadpool(
        recurring_status_mod.get_recurring_status,
        identity,
        queue=queue,
        workspace_index=workspace_index,
        pubsub=pubsub,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL)
    return result
