from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from alan import engine_config
from alan.engine_config import EngineConfig, Posture

engine_sources_router = APIRouter(prefix="/api/engines")


class EngineSource(BaseModel):
    slug: str
    in_version_control: bool
    config_path: str | None
    posture_ceiling: Posture
    export_command: str | None


class EngineSourcesResponse(BaseModel):
    sources: list[EngineSource]


def _export_command(slug: str) -> str:
    return f"uv run python scripts/export_engine_configs.py {slug}"


def _config_path(cfg: EngineConfig, *, in_vcs: bool) -> str | None:
    if in_vcs or engine_config.ceiling_source(cfg.repo) == "default":
        return engine_config.ceiling_location(cfg.repo)
    return None


def _engine_source(cfg: EngineConfig) -> EngineSource:
    in_vcs = engine_config.is_file_backed(cfg.repo)
    return EngineSource(
        slug=cfg.repo.slug,
        in_version_control=in_vcs,
        config_path=_config_path(cfg, in_vcs=in_vcs),
        posture_ceiling=engine_config.file_ceiling(cfg.repo),
        export_command=None if in_vcs else _export_command(cfg.repo.slug),
    )


def _build_sources() -> list[EngineSource]:
    return [_engine_source(cfg) for cfg in engine_config.registered_engines()]


@engine_sources_router.get("/sources")
async def list_engine_sources() -> EngineSourcesResponse:
    sources = await run_in_threadpool(_build_sources)
    return EngineSourcesResponse(sources=sources)
