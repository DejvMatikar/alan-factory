from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import engine_config as engine_config_mod
from alan.engine_config import EngineConfig, RepoIdentity

_UNKNOWN_ENGINE = "/api/engines/ghost/nowhere"
_KNOWN_ENGINE = "/api/engines/acme/widget"
_KNOWN_IDENTITY = RepoIdentity(owner="acme", repo="widget")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path / "workspaces"
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: None
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _register_known_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_config_mod,
        "lookup_by_repo",
        lambda identity: EngineConfig(repo=identity) if identity == _KNOWN_IDENTITY else None,
    )
    monkeypatch.setattr(engine_config_mod, "get_registered", lambda: None)


def test_repo_status_of_an_unknown_engine_is_404_engine_not_found(client: TestClient) -> None:
    res = client.get(f"{_UNKNOWN_ENGINE}/status")
    assert res.status_code == 404
    assert res.json() == {"detail": "engine not found"}


def test_update_of_an_unregistered_engine_returns_the_store_error_message_as_the_404_body(
    client: TestClient,
) -> None:
    res = client.patch(
        _UNKNOWN_ENGINE,
        json={"repo": {"owner": "ghost", "repo": "nowhere"}},
        headers=_control_headers(),
    )
    assert res.status_code == 404
    assert res.json() == {"detail": "Engine for 'ghost/nowhere' is not registered"}


def test_posture_change_of_an_unregistered_engine_is_404_engine_not_found(
    client: TestClient,
) -> None:
    res = client.post(
        f"{_UNKNOWN_ENGINE}/posture",
        json={"posture": "propose", "confirm": True},
        headers=_control_headers(),
    )
    assert res.status_code == 404
    assert res.json() == {"detail": "engine not found"}


def test_rollback_to_a_missing_history_version_is_404_no_such_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_known_engine(monkeypatch)
    res = client.post(
        f"{_KNOWN_ENGINE}/prompt-overrides/rollback",
        json={"team": "implementation", "role": "implementer", "version": 99},
        headers=_control_headers(),
    )
    assert res.status_code == 404
    assert res.json() == {"detail": "no such prompt override history version"}


def test_delete_that_loses_the_unregister_race_is_404_engine_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_known_engine(monkeypatch)
    monkeypatch.setattr(
        engine_config_mod, "unregister_and_persist", lambda _id: False
    )
    res = client.delete(_KNOWN_ENGINE, headers=_control_headers())
    assert res.status_code == 404
    assert res.json() == {"detail": "engine not found"}


def test_pool_state_without_a_dispatcher_is_404_no_pool_active(client: TestClient) -> None:
    res = client.get(f"{_KNOWN_ENGINE}/pool")
    assert res.status_code == 404
    assert res.json() == {"detail": "no pool active for this engine"}


def test_pool_state_for_an_engine_without_a_pool_is_404_no_pool_active(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_known_engine(monkeypatch)
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: SimpleNamespace(
        pool_states=lambda: {}
    )
    res = client.get(f"{_KNOWN_ENGINE}/pool")
    assert res.status_code == 404
    assert res.json() == {"detail": "no pool active for this engine"}
