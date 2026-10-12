from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
from alan import _chat_core, server
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue
from alan.workspace import create

_OPERATIONS = ["Read: a.py", "Bash: ls"]


def _append_assistant_with_operations(
    cfg: _chat_core.ChatCoreConfig[_chat_core.S],
    session: _chat_core.S,
    root: Path,
    operations: list[str],
) -> None:
    _chat_core.append_assistant_and_clear_in_flight(
        cfg,
        session,
        _chat_core.ChatMessage(
            role="assistant",
            content="the reply",
            ts=datetime.now(UTC),
            operations=operations,
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


def test_build_chat_get_session_surfaces_persisted_operations(
    client: TestClient, bc_root: Path
) -> None:
    sid = client.post(
        "/api/build-chat/sessions", json={"engine_key": "DejvMatikar/alan"}
    ).json()["session_id"]
    session = _chat_core.load_session(build_chat_mod.CONFIG, sid, root=bc_root)
    _append_assistant_with_operations(build_chat_mod.CONFIG, session, bc_root, _OPERATIONS)

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["messages"][-1]["operations"] == _OPERATIONS


def test_explore_chat_get_session_surfaces_persisted_operations(
    client: TestClient, ec_root: Path
) -> None:
    sid = client.post(
        "/api/explore-chat/sessions",
        json={"consultant_key": "brainstorming", "engine_key": "DejvMatikar/alan"},
    ).json()["session_id"]
    session = _chat_core.load_session(explore_chat_mod.CONFIG, sid, root=ec_root)
    _append_assistant_with_operations(explore_chat_mod.CONFIG, session, ec_root, _OPERATIONS)

    body = client.get(f"/api/explore-chat/sessions/{sid}").json()
    assert body["messages"][-1]["operations"] == _OPERATIONS


def test_prep_chat_transcript_surfaces_persisted_operations(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    _append_assistant_with_operations(build_chat_mod.CONFIG, session, bc_root, _OPERATIONS)
    ws = create("Archived With Operations", "d", root=ws_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD,
        session.session_id,
        ws.path,
        title="Archived With Operations",
        root=bc_root,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["messages"][-1]["operations"] == _OPERATIONS


def test_prep_chat_transcript_defaults_empty_operations_for_a_pre_change_archive(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    _chat_core.append_assistant_and_clear_in_flight(
        build_chat_mod.CONFIG,
        session,
        _chat_core.ChatMessage(role="assistant", content="legacy reply", ts=datetime.now(UTC)),
        root=bc_root,
    )
    ws = create("Legacy Archived WS", "d", root=ws_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD,
        session.session_id,
        ws.path,
        title="Legacy Archived WS",
        root=bc_root,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["messages"][-1]["operations"] == []
