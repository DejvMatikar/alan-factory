from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.responses import Response

from alan import onboarding
from alan.project import Project
from alan.routes._deps import GUARDED, DispatcherDep, require_control_token

onboarding_router = APIRouter(
    prefix="/api/onboarding", dependencies=[Depends(require_control_token)]
)


class OnboardingCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    owner: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1, max_length=200)
    template: str = Field(min_length=1, max_length=100)
    token: str = Field(min_length=1, max_length=4000)


@onboarding_router.post(
    "",
    status_code=201,
    responses={
        **GUARDED,
        409: {"description": "repo already onboarded"},
        422: {"description": "unknown template or preliminary checks failed"},
        503: {"description": "dispatcher disabled"},
    },
)
async def create_onboarding_attempt(
    body: OnboardingCreateBody, dispatcher: DispatcherDep
) -> Project:
    try:
        return await run_in_threadpool(
            onboarding.create_attempt,
            dispatcher=dispatcher,
            title=body.title,
            owner=body.owner,
            repo=body.repo,
            template=body.template,
            token=body.token,
        )
    except onboarding.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@onboarding_router.delete(
    "/{project_id}",
    status_code=204,
    responses={
        **GUARDED,
        404: {"description": "unknown onboarding attempt"},
        409: {"description": "project is not a provisioning attempt"},
        503: {"description": "dispatcher disabled"},
    },
)
async def delete_onboarding_attempt(project_id: str, dispatcher: DispatcherDep) -> Response:
    try:
        await run_in_threadpool(onboarding.rollback_attempt, dispatcher, project_id)
    except onboarding.OnboardingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return Response(status_code=204)
