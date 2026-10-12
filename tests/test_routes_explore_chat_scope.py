from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.explore_chat as explore_chat_mod
from alan import server
from alan.dispatcher import PubSub
from alan.engine_config import EngineConfig, RepoIdentity, register

_ACME_ENGINE_KEY = "acme/widget"
_OTHER_ENGINE_KEY = "acme/other"


def _register_engine(slug: str) -> None:
    owner, repo = slug.split("/")
    register(EngineConfig(repo=RepoIdentity(owner=owner, repo=repo)), validate_env=False)


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def ec_locks() -> explore_chat_mod.ExploreChatSessionLocks:
    return explore_chat_mod.ExploreChatSessionLocks()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def client(ec_root, ws_root, ec_locks, pubsub):
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._explore_chat_locks_dep] = lambda: ec_locks
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_list_sessions_no_engine_key_param_is_unfiltered(
    client: TestClient, ec_root: Path
) -> None:
    a = explore_chat_mod.create_session(
        "brainstorming", root=ec_root, engine_key=_ACME_ENGINE_KEY
    )
    b = explore_chat_mod.create_session("brainstorming", root=ec_root)

    rows = client.get("/api/explore-chat/sessions").json()

    assert {r["session_id"] for r in rows} == {a.session_id, b.session_id}


def test_list_sessions_filters_by_engine_key(client: TestClient, ec_root: Path) -> None:
    _register_engine(_ACME_ENGINE_KEY)
    _register_engine(_OTHER_ENGINE_KEY)
    matching = explore_chat_mod.create_session(
        "brainstorming", root=ec_root, engine_key=_ACME_ENGINE_KEY
    )
    explore_chat_mod.create_session(
        "brainstorming", root=ec_root, engine_key=_OTHER_ENGINE_KEY
    )

    rows = client.get(
        "/api/explore-chat/sessions", params={"engine_key": _ACME_ENGINE_KEY}
    ).json()

    assert [r["session_id"] for r in rows] == [matching.session_id]


def test_list_sessions_unknown_engine_key_returns_empty(
    client: TestClient, ec_root: Path
) -> None:
    explore_chat_mod.create_session("brainstorming", root=ec_root)

    resp = client.get("/api/explore-chat/sessions", params={"engine_key": "ghost/repo"})

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_combines_engine_key_and_project_id(
    client: TestClient, ec_root: Path
) -> None:
    _register_engine(_ACME_ENGINE_KEY)
    matching = explore_chat_mod.create_session(
        "brainstorming", root=ec_root, engine_key=_ACME_ENGINE_KEY, project_id="proj-a"
    )
    explore_chat_mod.create_session(
        "brainstorming", root=ec_root, engine_key=_ACME_ENGINE_KEY, project_id="proj-b"
    )
    explore_chat_mod.create_session(
        "brainstorming", root=ec_root, project_id="proj-a"
    )

    rows = client.get(
        "/api/explore-chat/sessions",
        params={"engine_key": _ACME_ENGINE_KEY, "project_id": "proj-a"},
    ).json()

    assert [r["session_id"] for r in rows] == [matching.session_id]


def test_list_sessions_engine_key_bounds_rejected(client: TestClient) -> None:
    assert (
        client.get("/api/explore-chat/sessions", params={"engine_key": ""}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/explore-chat/sessions", params={"engine_key": "x" * 201}
        ).status_code
        == 422
    )
