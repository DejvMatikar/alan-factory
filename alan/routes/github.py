from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from alan.github_repos import GithubReposResult, probe_repos
from alan.routes._deps import GUARDED, require_control_token

github_router = APIRouter(prefix="/api/github")


class GithubReposBody(BaseModel):
    token: str = Field(min_length=1, max_length=4000)


@github_router.post(
    "/repos",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        422: {"description": "token is blank or too long"},
    },
)
async def list_github_repos(body: GithubReposBody) -> GithubReposResult:
    return await run_in_threadpool(probe_repos, body.token)
