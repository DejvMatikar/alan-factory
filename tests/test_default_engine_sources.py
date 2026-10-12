from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import config_files as config_files_mod
from alan import default_profile, engine_config, engine_registry_store, server
from alan.engine_config import EngineConfig, Posture, PostureCeilingError, RepoIdentity


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))


@pytest.fixture
def profile_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    state = tmp_path / "profile"
    state.mkdir()
    (state / "default_profile.yaml").write_text("owner: acme\nrepo: alpha\nposture: propose\n")
    resolved = default_profile.resolve(state, tmp_path / "no-owner.yaml")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)
    saved = engine_config.default_engine_config().model_copy(update={"posture": Posture.observe})
    engine_registry_store.write_engine(saved.repo.slug, saved.model_dump(mode="json"))
    return saved


def _sources() -> list[dict[str, Any]]:
    with TestClient(server.app) as client:
        sources: list[dict[str, Any]] = client.get("/api/engines/sources").json()["sources"]
    return sources


def test_a_profile_resolved_default_reports_the_profile_as_its_config_path(
    profile_default: EngineConfig,
) -> None:
    assert _sources() == [
        {
            "slug": "acme/alpha",
            "in_version_control": False,
            "config_path": "engines/default_profile.yaml",
            "posture_ceiling": "propose",
            "export_command": "uv run python scripts/export_engine_configs.py acme/alpha",
        }
    ]
    assert profile_default.repo == RepoIdentity(owner="acme", repo="alpha")


def test_a_file_backed_default_reports_exactly_what_it_reported_before(
    profile_default: EngineConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked = profile_default.model_copy(update={"posture": Posture.propose})
    monkeypatch.setattr(
        config_files_mod, "load_tracked_configs", lambda: ({tracked.repo: tracked}, [])
    )
    assert _sources() == [
        {
            "slug": "acme/alpha",
            "in_version_control": True,
            "config_path": "config/engines.d/alpha.yaml",
            "posture_ceiling": "propose",
            "export_command": None,
        }
    ]


def test_a_connected_engine_reports_no_config_path_beside_a_profile_default(
    profile_default: EngineConfig,
) -> None:
    connected = EngineConfig(repo=RepoIdentity(owner="acme", repo="gadget"))
    engine_registry_store.write_connected("acme/gadget", connected.model_dump(mode="json"))
    by_slug = {source["slug"]: source for source in _sources()}
    assert by_slug["acme/gadget"]["config_path"] is None
    assert by_slug["acme/gadget"]["in_version_control"] is False
    assert by_slug["acme/alpha"]["config_path"] == "engines/default_profile.yaml"
    assert profile_default.repo.repo == "alpha"


def test_the_ceiling_refusal_names_the_profile_on_a_profile_resolved_install(
    profile_default: EngineConfig,
) -> None:
    with TestClient(server.app):
        with pytest.raises(PostureCeilingError) as refusal:
            engine_config.set_posture_and_persist(
                profile_default.repo, Posture.autonomous, source="api"
            )
    assert str(refusal.value) == (
        "posture 'autonomous' exceeds the reviewed ceiling 'propose' for 'acme/alpha'; "
        "raise it in engines/default_profile.yaml"
    )


def test_the_ceiling_refusal_names_the_tracked_file_for_a_file_backed_default(
    profile_default: EngineConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracked = profile_default.model_copy(update={"posture": Posture.propose})
    monkeypatch.setattr(
        config_files_mod, "load_tracked_configs", lambda: ({tracked.repo: tracked}, [])
    )
    with TestClient(server.app):
        with pytest.raises(PostureCeilingError) as refusal:
            engine_config.set_posture_and_persist(
                profile_default.repo, Posture.autonomous, source="api"
            )
    assert str(refusal.value) == (
        "posture 'autonomous' exceeds the reviewed ceiling 'propose' for 'acme/alpha'; "
        "raise it in config/engines.d/alpha.yaml"
    )
