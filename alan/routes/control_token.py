from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from alan import roots
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.control_token_store import (
    ControlTokenStatusState,
    current_status,
    extend_token,
    revoke_token,
    rotate_token,
)
from alan.routes._deps import GUARDED_EXPIRABLE, require_control_token

control_token_router = APIRouter(prefix="/api/control-token")


class ControlTokenStatusResponse(BaseModel):
    state: ControlTokenStatusState
    deadline: str | None


def _status_response() -> ControlTokenStatusResponse:
    status = current_status()
    return ControlTokenStatusResponse(state=status.state, deadline=status.deadline)


@control_token_router.get("/status")
async def get_control_token_status() -> ControlTokenStatusResponse:
    return _status_response()


@control_token_router.post(
    "/extend", dependencies=[Depends(require_control_token)], responses=GUARDED_EXPIRABLE
)
async def extend_control_token_endpoint() -> ControlTokenStatusResponse:
    await run_in_threadpool(extend_token, roots.state_root())
    return _status_response()


@control_token_router.post(
    "/rotate", dependencies=[Depends(require_control_token)], responses=GUARDED_EXPIRABLE
)
async def rotate_control_token_endpoint() -> ControlTokenStatusResponse:
    token, _meta = await run_in_threadpool(rotate_token, roots.state_root())
    os.environ[CONTROL_TOKEN_ENV] = token
    return _status_response()


@control_token_router.post(
    "/revoke", dependencies=[Depends(require_control_token)], responses=GUARDED_EXPIRABLE
)
async def revoke_control_token_endpoint() -> ControlTokenStatusResponse:
    await run_in_threadpool(revoke_token, roots.state_root())
    os.environ.pop(CONTROL_TOKEN_ENV, None)
    return _status_response()
