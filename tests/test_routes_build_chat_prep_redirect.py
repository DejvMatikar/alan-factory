from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.routes._chat_router as chat_router_mod
from alan import server
from alan.dispatcher import PubSub
from alan.workspace import create

_ENGINE_KEY = "DejvMatikar/alan"
_BODY = {"engine_key": _ENGINE_KEY}
_UNKNOWN_SESSION_ID = "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def client(bc_root: Path, ws_root: Path, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _archive_session(ws_path: Path, bc_root: Path, *, title: str = "WS") -> str:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title=title, root=bc_root
    )
    return session.session_id


def test_get_session_redirects_303_to_the_workspace_prep_chat(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.get(f"/api/build-chat/sessions/{session_id}", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == f"/api/workspaces/{ws.task_id}/prep-chat"


def test_get_session_redirect_resolves_to_the_archived_transcript_when_followed(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.get(f"/api/build-chat/sessions/{session_id}", follow_redirects=True)
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == session_id
    assert body["kind"] == "build"


def test_get_session_still_404s_for_an_unknown_session_id(client: TestClient) -> None:
    res = client.get(f"/api/build-chat/sessions/{_UNKNOWN_SESSION_ID}")
    assert res.status_code == 404


def test_get_session_still_404s_for_a_malformed_session_id(client: TestClient) -> None:
    res = client.get("/api/build-chat/sessions/not-a-uuid")
    assert res.status_code == 404


def test_delete_session_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.delete(f"/api/build-chat/sessions/{session_id}")
    assert res.status_code == 404


def test_send_message_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.post(
        f"/api/build-chat/sessions/{session_id}/messages", json={"content": "hi"}
    )
    assert res.status_code == 404


def test_summarize_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.post(f"/api/build-chat/sessions/{session_id}/summarize")
    assert res.status_code == 404


def test_get_brief_still_404s_for_an_archived_session_id(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    res = client.get(f"/api/build-chat/sessions/{session_id}/brief")
    assert res.status_code == 404


def test_archived_session_not_listed_among_drafts(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root, title="Archived WS")

    rows = client.get("/api/build-chat/sessions").json()
    assert session_id not in [r["session_id"] for r in rows]


def test_get_session_reraises_a_non_404_error_raised_by_load_or_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx: object, session_id: str, root: object) -> None:
        raise HTTPException(status_code=500, detail="boom")

    monkeypatch.setattr(chat_router_mod, "load_or_404", boom)
    res = client.get(f"/api/build-chat/sessions/{_UNKNOWN_SESSION_ID}")
    assert res.status_code == 500
