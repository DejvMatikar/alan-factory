from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StrictInt

from alan import engine_config
from alan.engine_config import RepoIdentity
from alan.lane_limit import LaneStatus, lane_status, set_limit
from alan.routes._deps import GUARDED, QueueDep, WorkspacesRootDep, require_control_token

engines_lane_router = APIRouter(prefix="/api/engines")

_ENGINE_NOT_FOUND_DETAIL = "engine not found"


class LaneLimitUpdate(BaseModel):
    limit: Annotated[StrictInt, Field(ge=1)] | None


async def _require_engine(owner: str, repo: str) -> RepoIdentity:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await run_in_threadpool(engine_config.lookup_by_repo, identity)
    if cfg is None:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL)
    return identity


@engines_lane_router.get(
    "/{owner}/{repo}/lane",
    responses={404: {"description": _ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_lane(
    owner: str, repo: str, queue: QueueDep, workspaces_root: WorkspacesRootDep
) -> LaneStatus:
    identity = await _require_engine(owner, repo)
    return await run_in_threadpool(lane_status, queue, workspaces_root, identity)


@engines_lane_router.put(
    "/{owner}/{repo}/lane/limit",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": _ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "limit must be a whole number >= 1, or null to clear"},
    },
)
async def set_engine_lane_limit(
    owner: str,
    repo: str,
    body: LaneLimitUpdate,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
) -> LaneStatus:
    identity = await _require_engine(owner, repo)
    await run_in_threadpool(set_limit, queue, identity, body.limit, source="api")
    return await run_in_threadpool(lane_status, queue, workspaces_root, identity)
