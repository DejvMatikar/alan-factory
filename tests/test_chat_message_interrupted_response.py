from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
from alan import _chat_core, server
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue
from alan.routes._deps import ChatMessageOut
from alan.workspace import create


def _append_assistant(
    cfg: _chat_core.ChatCoreConfig[_chat_core.S],
    session: _chat_core.S,
    root: Path,
    *,
    interrupted: bool,
) -> None:
    _chat_core.append_assistant_and_clear_in_flight(
        cfg,
        session,
        _chat_core.ChatMessage(
            role="assistant",
            content="the reply",
            ts=datetime.now(UTC),
            interrupted=interrupted,
        ),
        root=root,
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path,
    bc_root: Path,
    ec_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _build_session_id(client: TestClient) -> str:
    res = client.post("/api/build-chat/sessions", json={"engine_key": "DejvMatikar/alan"})
    return str(res.json()["session_id"])


def _explore_session_id(client: TestClient) -> str:
    res = client.post(
        "/api/explore-chat/sessions",
        json={"consultant_key": "brainstorming", "engine_key": "DejvMatikar/alan"},
    )
    return str(res.json()["session_id"])


@pytest.mark.parametrize("interrupted", [True, False])
def test_build_chat_get_session_surfaces_the_persisted_interrupted_flag(
    client: TestClient, bc_root: Path, interrupted: bool
) -> None:
    sid = _build_session_id(client)
    session = _chat_core.load_session(build_chat_mod.CONFIG, sid, root=bc_root)
    _append_assistant(build_chat_mod.CONFIG, session, bc_root, interrupted=interrupted)

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["messages"][-1]["interrupted"] is interrupted


@pytest.mark.parametrize("interrupted", [True, False])
def test_explore_chat_get_session_surfaces_the_persisted_interrupted_flag(
    client: TestClient, ec_root: Path, interrupted: bool
) -> None:
    sid = _explore_session_id(client)
    session = _chat_core.load_session(explore_chat_mod.CONFIG, sid, root=ec_root)
    _append_assistant(explore_chat_mod.CONFIG, session, ec_root, interrupted=interrupted)

    body = client.get(f"/api/explore-chat/sessions/{sid}").json()
    assert body["messages"][-1]["interrupted"] is interrupted


@pytest.mark.parametrize("interrupted", [True, False])
def test_prep_chat_transcript_surfaces_the_persisted_interrupted_flag(
    client: TestClient, ws_root: Path, bc_root: Path, interrupted: bool
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    _append_assistant(build_chat_mod.CONFIG, session, bc_root, interrupted=interrupted)
    ws = create("Archived With Interrupted Turn", "d", root=ws_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD,
        session.session_id,
        ws.path,
        title="Archived With Interrupted Turn",
        root=bc_root,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["messages"][-1]["interrupted"] is interrupted


def test_user_message_is_reported_as_not_interrupted(client: TestClient, bc_root: Path) -> None:
    sid = _build_session_id(client)
    session = _chat_core.load_session(build_chat_mod.CONFIG, sid, root=bc_root)
    _chat_core.append_user_message_and_mark_started(
        build_chat_mod.CONFIG,
        session,
        _chat_core.ChatMessage(role="user", content="hello", ts=datetime.now(UTC)),
        root=bc_root,
    )

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["messages"][-1]["interrupted"] is False


def test_chat_message_schema_declares_interrupted_as_a_non_nullable_boolean_defaulting_false() -> (
    None
):
    prop = server.app.openapi()["components"]["schemas"]["ChatMessageOut"]["properties"][
        "interrupted"
    ]
    assert prop["type"] == "boolean"
    assert prop["default"] is False


def test_chat_message_schema_declares_role_as_the_two_member_union() -> None:
    schema = server.app.openapi()["components"]["schemas"]["ChatMessageOut"]
    assert schema["properties"]["role"]["enum"] == ["user", "assistant"]


def test_chat_message_response_rejects_a_role_outside_the_union() -> None:
    with pytest.raises(ValidationError):
        ChatMessageOut.model_validate({"role": "system", "content": "x", "ts": datetime.now(UTC)})
