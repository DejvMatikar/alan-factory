from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from alan.engine_config import EngineConfig
from alan.engine_presets import build_config, get_preset, list_presets

engine_presets_router = APIRouter(prefix="/api/engine-presets")

_PRESET_NOT_FOUND_DETAIL = "preset not found"


class EnginePresetInfo(BaseModel):
    name: str
    config: EngineConfig


class EnginePresetListResponse(BaseModel):
    presets: list[EnginePresetInfo]


@engine_presets_router.get("")
async def list_engine_presets() -> EnginePresetListResponse:
    return EnginePresetListResponse(
        presets=[EnginePresetInfo(name=name, config=cfg) for name, cfg in list_presets()]
    )


@engine_presets_router.get(
    "/{name}",
    responses={404: {"description": _PRESET_NOT_FOUND_DETAIL}},
)
async def get_engine_preset(name: str) -> EnginePresetInfo:
    cfg = get_preset(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=_PRESET_NOT_FOUND_DETAIL)
    return EnginePresetInfo(name=name, config=cfg)


@engine_presets_router.get(
    "/{name}/config",
    responses={404: {"description": _PRESET_NOT_FOUND_DETAIL}},
)
async def get_engine_preset_config(
    name: str,
    owner: Annotated[str, Query(min_length=1)],
    repo: Annotated[str, Query(min_length=1)],
) -> EngineConfig:
    cfg = build_config(name, owner, repo)
    if cfg is None:
        raise HTTPException(status_code=404, detail=_PRESET_NOT_FOUND_DETAIL)
    return cfg
