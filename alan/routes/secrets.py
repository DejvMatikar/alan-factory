from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.responses import Response

from alan import secrets_store
from alan.routes._deps import GUARDED, PubSubDep, require_control_token
from alan.teams._base import TeamEvent

secrets_router = APIRouter(prefix="/api/secrets")

_SECRET_NOT_FOUND_DETAIL = "secret not found"  # noqa: S105  # nosec B105


class SecretCreateBody(BaseModel):
    engine: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=20000)
    expires_at: str | None = Field(default=None, max_length=32)


class SecretDeleteBody(BaseModel):
    engine: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=100)


class SecretRecordResponse(BaseModel):
    name: str
    fingerprint: str
    last_verified: str | None = None
    expires_at: str | None = None
    expiry_warned_at: str | None = None


class SecretListResponse(BaseModel):
    secrets: list[SecretRecordResponse]


def _to_response(meta: secrets_store.SecretRecordMeta) -> SecretRecordResponse:
    return SecretRecordResponse(
        name=meta.name,
        fingerprint=meta.fingerprint,
        last_verified=meta.last_verified,
        expires_at=meta.expires_at,
        expiry_warned_at=meta.expiry_warned_at,
    )


@secrets_router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        422: {"description": "engine/purpose/value is blank, or expires_at is not a valid date"},
    },
)
async def create_secret(body: SecretCreateBody, pubsub: PubSubDep) -> SecretRecordResponse:
    try:
        meta = await run_in_threadpool(
            secrets_store.write_secret,
            body.engine,
            body.purpose,
            body.value,
            expires_at=body.expires_at,
        )
    except secrets_store.SecretStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pubsub.publish_global(
        TeamEvent("secret_written", {"engine": body.engine, "purpose": body.purpose})
    )
    return _to_response(meta)


@secrets_router.get("")
async def list_secrets_endpoint() -> SecretListResponse:
    metas = await run_in_threadpool(secrets_store.list_secrets)
    return SecretListResponse(secrets=[_to_response(m) for m in metas])


@secrets_router.delete(
    "",
    status_code=204,
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": _SECRET_NOT_FOUND_DETAIL},
        422: {"description": "engine or purpose is blank"},
    },
)
async def delete_secret_endpoint(body: SecretDeleteBody) -> Response:
    try:
        deleted = await run_in_threadpool(
            secrets_store.delete_secret, body.engine, body.purpose
        )
    except secrets_store.SecretStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_SECRET_NOT_FOUND_DETAIL)
    return Response(status_code=204)
