from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from alan.engine_config import EngineConfig, RepoIdentity
from alan.engine_config import (
    _parse_engine_yaml as _parse_engine_yaml,  # pyright: ignore[reportPrivateUsage]
)

_ENGINE_FILE_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


def engines_config_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "engines.d"


@dataclass(frozen=True)
class QuarantinedEngineFile:
    path: Path
    error: str


def _tracked_yaml_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix in _ENGINE_FILE_SUFFIXES
    )


def resolve_quarantined_identity(
    path: Path, candidates: Iterable[RepoIdentity]
) -> RepoIdentity | None:
    for candidate in candidates:
        if candidate.repo == path.stem:
            return candidate
    return None


def load_tracked_configs(
    directory: Path | None = None,
) -> tuple[dict[RepoIdentity, EngineConfig], list[QuarantinedEngineFile]]:
    target = directory if directory is not None else engines_config_dir()
    configs: dict[RepoIdentity, EngineConfig] = {}
    quarantined: list[QuarantinedEngineFile] = []
    for path in _tracked_yaml_paths(target):
        try:
            config = _parse_engine_yaml(path)
        except Exception as exc:  # noqa: BLE001 - fail-soft per file; one bad file must not block the rest
            quarantined.append(QuarantinedEngineFile(path=path, error=str(exc)))
            continue
        if config.repo in configs:
            quarantined.append(
                QuarantinedEngineFile(
                    path=path, error=f"duplicate repo identity {config.repo.slug!r}"
                )
            )
            continue
        configs[config.repo] = config
    return configs, quarantined
