from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import workspace as workspace_mod
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


def _create_engine(client, owner="acme", repo="widget"):
    return client.post(
        "/api/engines",
        json={"repo": {"owner": owner, "repo": repo}},
        headers=_control_headers(),
    )


def _set_limit(client, body, *, headers=None, repo="widget"):
    return client.put(
        f"/api/engines/acme/{repo}/lane/limit",
        json=body,
        headers=headers if headers is not None else _control_headers(),
    )


def _subscribe(client: TestClient) -> list[TeamEvent]:
    events: list[TeamEvent] = []
    server.app.state.pubsub.subscribe_all(events.append)
    return events


def test_get_lane_requires_no_token(client: TestClient) -> None:
    _create_engine(client)
    res = client.get("/api/engines/acme/widget/lane", headers={})
    assert res.status_code == 200


def test_get_lane_missing_engine_returns_404(client: TestClient) -> None:
    res = client.get("/api/engines/acme/widget/lane")
    assert res.status_code == 404


def test_get_lane_defaults_to_no_limit_and_zero_seats(client: TestClient) -> None:
    _create_engine(client)
    res = client.get("/api/engines/acme/widget/lane")
    assert res.status_code == 200
    body = res.json()
    assert body["limit"] is None
    assert body["seats"] == 0
    assert body["holders"] == []


def test_get_lane_counts_live_workspace_on_the_engine(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    ws = workspace_mod.create(
        "widget work", "d", root=workspaces_root, engine_key="acme/widget"
    )
    res = client.get("/api/engines/acme/widget/lane")
    body = res.json()
    assert body["seats"] == 1
    assert body["holders"] == [
        {"task_id": ws.task_id, "title": ws.meta.title, "status": ws.meta.status.value}
    ]


def test_set_lane_limit_requires_control_token(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {"limit": 2}, headers={})
    assert res.status_code == 401


def test_set_lane_limit_wrong_control_token_returns_403(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(
        client, {"limit": 2}, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong"}
    )
    assert res.status_code == 403


def test_set_lane_limit_missing_engine_returns_404(client: TestClient) -> None:
    res = _set_limit(client, {"limit": 2})
    assert res.status_code == 404


def test_set_lane_limit_rejects_zero(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {"limit": 0})
    assert res.status_code == 422


def test_set_lane_limit_rejects_negative(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {"limit": -1})
    assert res.status_code == 422


def test_set_lane_limit_rejects_non_integer(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {"limit": 1.5})
    assert res.status_code == 422


def test_set_lane_limit_rejects_missing_key(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {})
    assert res.status_code == 422


def test_set_lane_limit_valid_value_persists(client: TestClient) -> None:
    _create_engine(client)
    res = _set_limit(client, {"limit": 3})
    assert res.status_code == 200
    assert res.json()["limit"] == 3
    follow_up = client.get("/api/engines/acme/widget/lane")
    assert follow_up.json()["limit"] == 3


def test_set_lane_limit_null_clears(client: TestClient) -> None:
    _create_engine(client)
    _set_limit(client, {"limit": 3})
    res = _set_limit(client, {"limit": None})
    assert res.status_code == 200
    assert res.json()["limit"] is None


def test_set_lane_limit_never_changes_engine_config(client: TestClient) -> None:
    _create_engine(client)
    before = client.get("/api/engines/acme/widget").json()
    _set_limit(client, {"limit": 4})
    after = client.get("/api/engines/acme/widget").json()
    assert before == after


def test_set_lane_limit_never_writes_workspace_meta(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    ws = workspace_mod.create(
        "widget work", "d", root=workspaces_root, engine_key="acme/widget"
    )
    before = (ws.path / "meta.json").read_text(encoding="utf-8")
    _set_limit(client, {"limit": 1})
    after = (ws.path / "meta.json").read_text(encoding="utf-8")
    assert before == after


def test_set_lane_limit_emits_no_events(client: TestClient) -> None:
    _create_engine(client)
    events = _subscribe(client)
    _set_limit(client, {"limit": 2})
    assert events == []
