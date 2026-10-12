from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan.teams._base import TeamEvent


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    engines_root: Path, secrets_root: Path, workspaces_root: Path
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _minimal_body(owner: str = "acme", repo: str = "widget") -> dict[str, object]:
    return {"repo": {"owner": owner, "repo": repo}}


def _create(client, body=None, **kwargs):
    return client.post(
        "/api/engines", json=body or _minimal_body(**kwargs), headers=_control_headers()
    )


def _subscribe(client: TestClient) -> list[TeamEvent]:
    events: list[TeamEvent] = []
    server.app.state.pubsub.subscribe_all(events.append)
    return events


def _set_accent(client, body=None, headers=None):
    return client.put(
        "/api/engines/acme/widget/accent",
        json=body if body is not None else {"accent": {"color": "iris", "intensity": "bold"}},
        headers=headers if headers is not None else _control_headers(),
    )


def test_set_accent_valid_returns_200_with_updated_config(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client)
    assert res.status_code == 200
    assert res.json()["accent"] == {"color": "iris", "intensity": "bold"}


def test_set_accent_persists_change(client: TestClient) -> None:
    _create(client)
    _set_accent(client, body={"accent": {"color": "rose", "intensity": "soft"}})
    res = client.get("/api/engines/acme/widget")
    assert res.json()["accent"] == {"color": "rose", "intensity": "soft"}


def test_set_accent_persists_a_newly_widened_palette_color(client: TestClient) -> None:
    _create(client)
    _set_accent(client, body={"accent": {"color": "slate", "intensity": "bold"}})
    res = client.get("/api/engines/acme/widget")
    assert res.json()["accent"] == {"color": "slate", "intensity": "bold"}


def test_set_accent_explicit_no_accent_persists(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, body={"accent": {"color": None}})
    assert res.status_code == 200
    assert res.json()["accent"] == {"color": None, "intensity": "standard"}


def test_set_accent_null_resets_to_automatic(client: TestClient) -> None:
    _create(client)
    _set_accent(client, body={"accent": {"color": "crimson", "intensity": "bold"}})
    res = _set_accent(client, body={"accent": None})
    assert res.status_code == 200
    assert res.json()["accent"] is None
    follow_up = client.get("/api/engines/acme/widget")
    assert follow_up.json()["accent"] is None


def test_set_accent_missing_accent_field_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, body={})
    assert res.status_code == 422


def test_set_accent_missing_accent_field_leaves_stored_config_unchanged(
    client: TestClient,
) -> None:
    _create(client)
    _set_accent(client, body={"accent": {"color": "violet", "intensity": "standard"}})
    res = _set_accent(client, body={})
    assert res.status_code == 422
    follow_up = client.get("/api/engines/acme/widget")
    assert follow_up.json()["accent"] == {"color": "violet", "intensity": "standard"}


def test_set_accent_unknown_color_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, body={"accent": {"color": "lavender"}})
    assert res.status_code == 422


def test_set_accent_unknown_intensity_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, body={"accent": {"color": "iris", "intensity": "loud"}})
    assert res.status_code == 422


def test_set_accent_bounded_integer_intensity_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, body={"accent": {"color": "iris", "intensity": 2}})
    assert res.status_code == 422


def test_set_accent_invalid_value_leaves_stored_config_unchanged(client: TestClient) -> None:
    _create(client)
    _set_accent(client, body={"accent": {"color": "violet", "intensity": "standard"}})
    res = _set_accent(client, body={"accent": {"color": "lavender"}})
    assert res.status_code == 422
    follow_up = client.get("/api/engines/acme/widget")
    assert follow_up.json()["accent"] == {"color": "violet", "intensity": "standard"}


def test_set_accent_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, headers={})
    assert res.status_code == 401


def test_set_accent_without_control_token_writes_nothing(client: TestClient) -> None:
    _create(client)
    _set_accent(client, headers={})
    res = client.get("/api/engines/acme/widget")
    assert res.json()["accent"] is None


def test_set_accent_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _set_accent(client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"})
    assert res.status_code == 403


def test_set_accent_missing_engine_returns_404(client: TestClient) -> None:
    res = _set_accent(client)
    assert res.status_code == 404


def test_set_accent_emits_engine_accent_changed_event(client: TestClient) -> None:
    _create(client)
    events = _subscribe(client)
    res = _set_accent(client)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_accent_changed"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}
