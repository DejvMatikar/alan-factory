from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.lane_limit as lane_limit_mod
from alan import server
from alan.dispatcher import Dispatcher, PubSub
from alan.engine_config import canonical_scope_key
from alan.queue import Queue
from alan.workspace import create, list_workspaces


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


def _predispatch_log(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "predispatch_findings.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _clause_audit_log(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "verification_audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _raise_seat_holders(*_args: object, **_kwargs: object) -> list[object]:
    raise AssertionError("seat_holders must not be called when no limit is set")


def test_create_without_limit_never_counts_seats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lane_limit_mod, "seat_holders", _raise_seat_holders)
    res = client.post("/api/workspaces", json={"title": "Free Lane", "description": "d"})
    assert res.status_code == 201


def test_create_below_limit_returns_201(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    lane_limit_mod.set_limit(queue, canonical_scope_key(None), 2, source="api")
    create("existing", "d", root=ws_root)
    res = client.post("/api/workspaces", json={"title": "Below Limit", "description": "d"})
    assert res.status_code == 201


def test_create_at_limit_returns_409_lane_full_and_writes_nothing(
    client: TestClient, queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    lane_limit_mod.set_limit(queue, canonical_scope_key(None), 1, source="api")
    create("existing", "d", root=ws_root)
    predispatch_before = len(_predispatch_log(tmp_path))
    clause_audit_before = len(_clause_audit_log(tmp_path))
    jobs_before = len(queue.list_jobs())

    res = client.post("/api/workspaces", json={"title": "At Limit", "description": "d"})

    assert res.status_code == 409
    assert res.json()["detail"].startswith("lane full:")
    titles = {ws.meta.title for ws in list_workspaces(root=ws_root)}
    assert titles == {"existing"}
    assert len(_predispatch_log(tmp_path)) == predispatch_before
    assert len(_clause_audit_log(tmp_path)) == clause_audit_before
    assert len(queue.list_jobs()) == jobs_before


def test_refused_create_leaves_prep_chat_session_unarchived(
    client: TestClient, queue: Queue, ws_root: Path, bc_root: Path
) -> None:
    lane_limit_mod.set_limit(queue, canonical_scope_key(None), 1, source="api")
    create("existing", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Refused With Chat",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 409
    assert (bc_root / session.session_id / "session.json").is_file()
    loaded = build_chat_mod.load_session(session.session_id, root=bc_root)
    assert loaded.session_id == session.session_id


def test_create_workspace_lowering_limit_below_current_count_only_blocks_new_creates(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    create("existing-a", "d", root=ws_root)
    create("existing-b", "d", root=ws_root)
    lane_limit_mod.set_limit(queue, canonical_scope_key(None), 1, source="api")
    res = client.post("/api/workspaces", json={"title": "Over Limit", "description": "d"})
    assert res.status_code == 409
    assert len(list_workspaces(root=ws_root)) == 2
