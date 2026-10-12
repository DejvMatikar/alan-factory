from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from alan import agent_catalogue, engine_config
from alan.agent_catalogue_models import AgentCatalogue, AgentCatalogueSummary
from alan.engine_config import RepoIdentity

engines_agents_router = APIRouter(prefix="/api/engines")

_ENGINE_NOT_FOUND_DETAIL = "engine not found"


async def _catalogue_for(owner: str, repo: str) -> AgentCatalogue:
    cfg = await run_in_threadpool(
        engine_config.lookup_by_repo, RepoIdentity(owner=owner, repo=repo)
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail=_ENGINE_NOT_FOUND_DETAIL)
    return await run_in_threadpool(agent_catalogue.build_catalogue, cfg)


@engines_agents_router.get(
    "/{owner}/{repo}/agents",
    responses={404: {"description": _ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_agents(owner: str, repo: str) -> AgentCatalogue:
    return await _catalogue_for(owner, repo)


@engines_agents_router.get(
    "/{owner}/{repo}/agents/summary",
    responses={404: {"description": _ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_agents_summary(owner: str, repo: str) -> AgentCatalogueSummary:
    return agent_catalogue.summarize(await _catalogue_for(owner, repo))
