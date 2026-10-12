from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.routes._chat_router as chat_router_mod
from alan import server
from alan.dispatcher import PubSub
from alan.workspace import create

_UNKNOWN_SESSION_ID = "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def client(ec_root: Path, ws_root: Path, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _archive_session(ws_path: Path, ec_root: Path, *, title: str = "WS") -> str:
    session = explore_chat_mod.create_session("research", root=ec_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path, title=title, root=ec_root
    )
    return session.session_id


def test_get_session_redirects_303_to_the_workspace_prep_chat(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.get(f"/api/explore-chat/sessions/{session_id}", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == f"/api/workspaces/{ws.task_id}/prep-chat"


def test_get_session_redirect_resolves_to_the_archived_transcript_when_followed(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.get(f"/api/explore-chat/sessions/{session_id}", follow_redirects=True)
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == session_id
    assert body["kind"] == "explore"
    assert body["consultant_key"] == "research"


def test_get_session_still_404s_for_an_unknown_session_id(client: TestClient) -> None:
    res = client.get(f"/api/explore-chat/sessions/{_UNKNOWN_SESSION_ID}")
    assert res.status_code == 404


def test_get_session_still_404s_for_a_malformed_session_id(client: TestClient) -> None:
    res = client.get("/api/explore-chat/sessions/not-a-uuid")
    assert res.status_code == 404


def test_delete_session_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.delete(f"/api/explore-chat/sessions/{session_id}")
    assert res.status_code == 404


def test_send_message_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.post(
        f"/api/explore-chat/sessions/{session_id}/messages", json={"content": "hi"}
    )
    assert res.status_code == 404


def test_summarize_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.post(f"/api/explore-chat/sessions/{session_id}/summarize")
    assert res.status_code == 404


def test_get_brief_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    res = client.get(f"/api/explore-chat/sessions/{session_id}/brief")
    assert res.status_code == 404


def test_archived_session_not_listed_among_drafts(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, ec_root, title="Archived WS")

    rows = client.get("/api/explore-chat/sessions").json()
    assert session_id not in [r["session_id"] for r in rows]


def test_get_session_reraises_a_non_404_error_raised_by_load_or_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx: object, session_id: str, root: object) -> None:
        raise HTTPException(status_code=500, detail="boom")

    monkeypatch.setattr(chat_router_mod, "load_or_404", boom)
    res = client.get(f"/api/explore-chat/sessions/{_UNKNOWN_SESSION_ID}")
    assert res.status_code == 500
