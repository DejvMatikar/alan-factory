from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import default_profile, engine_config, server
from alan.engine_config import RepoIdentity

REPO_ROOT = Path(__file__).resolve().parents[1]
_PROFILE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "default_profile_test.yaml"


def _register_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_dir: Path
) -> RepoIdentity:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    resolved = default_profile.resolve(state_dir, _PROFILE_FIXTURE)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    default = engine_config.default_engine_config()
    engine_config.register(default, default=True, validate_env=False)
    engine_config.set_default_ceiling({default.repo: default.posture})
    return default.repo


@pytest.fixture
def owner_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RepoIdentity:
    return _register_default(tmp_path, monkeypatch, tmp_path / "no-profile")


@pytest.fixture
def installed_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RepoIdentity:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    shutil.copy(_PROFILE_FIXTURE, state_dir / default_profile.PROFILE_FILE_NAME)
    return _register_default(tmp_path, monkeypatch, state_dir)


def _listing() -> dict[str, object]:
    with TestClient(server.app) as client:
        listing: dict[str, object] = client.get("/api/engines").json()
    return listing


def test_a_configured_install_lists_its_default_with_a_forge_target(
    owner_default: RepoIdentity,
) -> None:
    listing = _listing()
    engines = listing["engines"]
    assert isinstance(engines, list)
    assert default_profile.resolved().source is default_profile.Source.owner
    assert listing["default_engine_key"] == owner_default.slug
    assert len(engines) == 1
    assert engines[0]["hidden"] is False
    assert engines[0]["forge"]["repo"] == engines[0]["repo"]


def test_an_installed_profile_lists_its_default_with_a_forge_target(
    installed_default: RepoIdentity,
) -> None:
    listing = _listing()
    engines = listing["engines"]
    assert isinstance(engines, list)
    assert default_profile.resolved().source is default_profile.Source.profile
    assert listing["default_engine_key"] == installed_default.slug
    assert len(engines) == 1
    assert engines[0]["hidden"] is False
    assert engines[0]["forge"]["repo"] == engines[0]["repo"]
