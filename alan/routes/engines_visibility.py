from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from alan import engine_config
from alan.engine_config import EngineConfig, RepoIdentity
from alan.repo_quiesce import QuiesceReport, quiesce_repo
from alan.routes._deps import (
    GUARDED,
    BuildChatLocksDep,
    BuildChatSessionsRootDep,
    ExploreChatLocksDep,
    ExploreChatSessionsRootDep,
    OptionalDispatcherDep,
    PubSubDep,
    QueueDep,
    WorkspacesRootDep,
    require_control_token,
)
from alan.teams._base import TeamEvent

engines_visibility_router = APIRouter(prefix="/api/engines")

_ENGINE_NOT_FOUND_DETAIL = "engine not found"
_ENGINE_IS_DEFAULT_DETAIL = "cannot hide the default engine"


@engines_visibility_router.post(
    "/{owner}/{repo}/hide",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": _ENGINE_NOT_FOUND_DETAIL},
        409: {"description": _ENGINE_IS_DEFAULT_DETAIL},
    },
)
async def hide_engine(
    owner: str,
    repo: str,
    dispatcher: OptionalDispatcherDep,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
    build_chat_root: BuildChatSessionsRootDep,
    build_chat_locks: BuildChatLocksDep,
    explore_chat_root: ExploreChatSessionsRootDep,
    explore_chat_locks: ExploreChatLocksDep,
    pubsub: PubSubDep,
) -> QuiesceReport:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await run_in_threadpool(engine_config.lookup_by_repo, identity)
    if cfg is None:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL)
    default_cfg = await run_in_threadpool(engine_config.get_registered)
    if default_cfg is not None and default_cfg.repo == identity:
        raise HTTPException(status_code=409, detail=_ENGINE_IS_DEFAULT_DETAIL)
    report = await run_in_threadpool(
        quiesce_repo,
        identity,
        dispatcher=dispatcher,
        queue=queue,
        workspaces_root=workspaces_root,
        build_chat_root=build_chat_root,
        build_chat_locks=build_chat_locks,
        explore_chat_root=explore_chat_root,
        explore_chat_locks=explore_chat_locks,
    )
    pubsub.publish_global(TeamEvent("engine_hidden", {"slug": identity.slug}))
    return report


@engines_visibility_router.post(
    "/{owner}/{repo}/unhide",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": _ENGINE_NOT_FOUND_DETAIL},
    },
)
async def unhide_engine(
    owner: str, repo: str, dispatcher: OptionalDispatcherDep, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg_before = await run_in_threadpool(engine_config.lookup_by_repo, identity)
    if cfg_before is None:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL)
    was_hidden = cfg_before.hidden
    cfg = await run_in_threadpool(engine_config.unhide_and_persist, identity)
    if was_hidden and dispatcher is not None:
        await run_in_threadpool(dispatcher.register_pool, cfg)
    pubsub.publish_global(TeamEvent("engine_unhidden", {"slug": identity.slug}))
    return cfg
