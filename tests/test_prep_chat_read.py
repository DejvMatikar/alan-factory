from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.prep_chat as prep_chat_mod
from alan import server
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue
from alan.workspace import create


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


def _archive_build_session(ws_path: Path, bc_root: Path, *, title: str = "WS") -> str:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title=title, root=bc_root
    )
    return session.session_id


def test_prep_chat_transcript_carries_the_brief_when_one_was_saved(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    build_chat_mod.save_brief(session.session_id, "Distilled brief", root=bc_root)
    ws = create("Briefed WS", "d", root=ws_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD,
        session.session_id,
        ws.path,
        title="Briefed WS",
        root=bc_root,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    body = res.json()
    assert body["brief"]["content"] == "Distilled brief"
    assert body["brief"]["updated_at"] is not None


def test_prep_chat_transcript_brief_is_absent_when_none_was_ever_saved(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Unbriefed WS", "d", root=ws_root)
    session_id = _archive_build_session(ws.path, bc_root, title="Unbriefed WS")

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == session_id
    assert body["brief"] is None


def test_prep_chat_transcript_brief_absent_for_an_archive_that_predates_the_brief_field(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Legacy Archive WS", "d", root=ws_root)
    _archive_build_session(ws.path, bc_root, title="Legacy Archive WS")
    archived_dir = next((ws.path / "prep-chat").iterdir())
    assert not (archived_dir / "brief.json").exists()

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["brief"] is None


def test_prep_chat_transcript_exposes_the_archived_session_engine_key(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root, engine_key="acme/widget")
    ws = create("Scoped WS", "d", root=ws_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD,
        session.session_id,
        ws.path,
        title="Scoped WS",
        root=bc_root,
    )

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["engine_key"] == "acme/widget"


def test_prep_chat_transcript_engine_key_is_none_for_alans_own_repo_session(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Unscoped WS", "d", root=ws_root)
    _archive_build_session(ws.path, bc_root, title="Unscoped WS")

    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 200
    assert res.json()["engine_key"] is None
