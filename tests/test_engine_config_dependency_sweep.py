from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from alan.engine_config import (
    DependencySweepConfig,
    EngineConfig,
    RepoIdentity,
    default_engine_config,
    lookup_by_repo,
    register_and_persist,
    update_and_persist,
)
from alan.engine_registry_store import list_engines as _persisted_engines


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def test_dependency_sweep_config_defaults() -> None:
    cfg = DependencySweepConfig()
    assert cfg.enabled is False
    assert cfg.enable_python_vuln_scan is False
    assert cfg.cadence_seconds == 7 * 24 * 3600


def test_dependency_sweep_config_accepts_explicit_values() -> None:
    cfg = DependencySweepConfig(enabled=True, cadence_seconds=3600, enable_python_vuln_scan=True)
    assert cfg.enabled is True
    assert cfg.cadence_seconds == 3600
    assert cfg.enable_python_vuln_scan is True


def test_dependency_sweep_config_rejects_negative_cadence() -> None:
    with pytest.raises(ValidationError):
        DependencySweepConfig(cadence_seconds=-1)


def test_engine_config_dependency_sweep_field_never_none() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert isinstance(cfg.dependency_sweep, DependencySweepConfig)


def test_engine_config_dependency_sweep_roundtrips_through_persistence() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        dependency_sweep=DependencySweepConfig(
            enabled=True, cadence_seconds=86400, enable_python_vuln_scan=False
        ),
    )
    register_and_persist(cfg)
    payloads = _persisted_engines()
    assert payloads[0]["dependency_sweep"] == {
        "enabled": True,
        "cadence_seconds": 86400,
        "enable_python_vuln_scan": False,
    }
    reloaded = lookup_by_repo(cfg.repo)
    assert reloaded == cfg


def test_update_and_persist_can_flip_dependency_sweep_enabled() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    updated = cfg.model_copy(
        update={"dependency_sweep": cfg.dependency_sweep.model_copy(update={"enabled": True})}
    )

    result = update_and_persist(updated, validate_env=False)

    assert result.dependency_sweep.enabled is True
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.dependency_sweep.enabled is True


def test_default_engine_config_dependency_sweep_preserves_weekly_sweep() -> None:
    cfg = default_engine_config()
    assert cfg.dependency_sweep == DependencySweepConfig(
        enabled=True, cadence_seconds=7 * 24 * 3600, enable_python_vuln_scan=True
    )
