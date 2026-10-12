from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from alan import engine_config, engine_registry_store
from alan.engine_config import EngineConfig, RepoIdentity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Saved:
    config: EngineConfig
    marked_at: str | None


def _saved_records() -> list[_Saved]:
    configs = [
        EngineConfig.model_validate(payload) for payload in engine_registry_store.list_engines()
    ]
    return [
        _Saved(config, engine_registry_store.default_origin_marked_at(config.repo.slug))
        for config in configs
    ]


def _connected(saved: list[_Saved], default_repo: RepoIdentity) -> list[EngineConfig]:
    return [s.config for s in saved if s.marked_at is None and s.config.repo != default_repo]


def _own_record(saved: list[_Saved], default_repo: RepoIdentity) -> EngineConfig | None:
    return next((s.config for s in saved if s.config.repo == default_repo), None)


def _newest_leftover(saved: list[_Saved], default_repo: RepoIdentity) -> EngineConfig | None:
    marked = [
        (s.marked_at, s.config.repo.slug, s.config)
        for s in saved
        if s.marked_at is not None and s.config.repo != default_repo
    ]
    if not marked:
        return None
    return max(marked, key=lambda entry: (entry[0], entry[1]))[2]


def _settings_source(saved: list[_Saved], default_repo: RepoIdentity) -> EngineConfig | None:
    return _own_record(saved, default_repo) or _newest_leftover(saved, default_repo)


def _register_connected(connected: list[EngineConfig]) -> None:
    for config in connected:
        engine_config.register_replacing(config)
    if connected:
        logger.info(
            "loaded %d persisted engine config(s) from registry store: %s",
            len(connected),
            ", ".join(config.repo.slug for config in connected),
        )


def merge_saved_engines() -> None:
    default = engine_config.default_engine_config()
    saved = _saved_records()
    _register_connected(_connected(saved, default.repo))
    source = _settings_source(saved, default.repo)
    if source is not None:
        merged = engine_config.apply_stored_projection(default, source)
        engine_config.register_replacing(merged, default=True)


def publish_default_ceiling() -> None:
    default = engine_config.default_engine_config()
    if not engine_config.is_file_backed(default.repo):
        engine_config.set_default_ceiling({default.repo: default.posture})


def load_engines(load_file_backed: Callable[[], None]) -> None:
    merge_saved_engines()
    load_file_backed()
    publish_default_ceiling()
