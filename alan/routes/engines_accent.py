from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from alan import engine_config
from alan.engine_config import EngineConfig, EngineNotFoundError, RepoIdentity
from alan.repo_accent import AccentChoice
from alan.routes._deps import GUARDED, PubSubDep, require_control_token
from alan.teams._base import TeamEvent

engines_accent_router = APIRouter(prefix="/api/engines")

_ENGINE_NOT_FOUND_DETAIL = "engine not found"


class AccentUpdate(BaseModel):
    accent: AccentChoice | None


@engines_accent_router.put(
    "/{owner}/{repo}/accent",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": _ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "invalid accent color or intensity"},
    },
)
async def set_engine_accent(
    owner: str, repo: str, body: AccentUpdate, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    try:
        cfg = await run_in_threadpool(
            engine_config.set_accent_and_persist, identity, body.accent
        )
    except EngineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL) from exc
    pubsub.publish_global(TeamEvent("engine_accent_changed", {"slug": identity.slug}))
    return cfg
