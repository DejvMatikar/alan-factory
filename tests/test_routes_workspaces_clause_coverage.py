from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.app.workspace_creation as creation_mod
from alan import server
from alan.app.workspace_create_models import (
    _SUPPLIED_CLAUSE_ID_DETAIL,
    UNCOVERED_CLAUSES_DETAIL,
)
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Queue
from alan.workspace import _clause_id


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


@pytest.fixture(autouse=True)
def _no_predispatch_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])  # noqa: ARG005


def _req_id(text: str) -> str:
    return _clause_id("req", text)


def _task(task_id: str, dod_clauses: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"id": task_id, "team": None, "dod_clauses": dod_clauses or []}


def _clause(text: str, covers: list[str]) -> dict[str, object]:
    return {"text": text, "covers": covers}


def test_multi_task_dispatch_with_full_clause_coverage_is_created(client: TestClient) -> None:
    req_a_id = _req_id("clause a")
    req_b_id = _req_id("clause b")
    body = {
        "title": "Full Coverage",
        "description": "d",
        "requirements": [{"text": "clause a"}, {"text": "clause b"}],
        "tasks": [
            _task("a", [_clause("done a", [req_a_id])]),
            _task("b", [_clause("done b", [req_b_id])]),
        ],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_multi_task_dispatch_with_uncovered_clause_is_rejected_with_ids(
    client: TestClient,
) -> None:
    req_a_id = _req_id("clause a")
    req_b_id = _req_id("clause b")
    body = {
        "title": "No Coverage",
        "description": "d",
        "requirements": [{"text": "clause a"}, {"text": "clause b"}],
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error"] == UNCOVERED_CLAUSES_DETAIL
    assert detail["uncovered_clause_ids"] == sorted([req_a_id, req_b_id])


def test_partial_clause_coverage_gap_is_rejected_naming_only_the_uncovered_id(
    client: TestClient,
) -> None:
    req_a_id = _req_id("clause a")
    req_b_id = _req_id("clause b")
    body = {
        "title": "Partial Coverage",
        "description": "d",
        "requirements": [{"text": "clause a"}, {"text": "clause b"}],
        "tasks": [
            _task("a", [_clause("done a", [req_a_id])]),
            _task("b"),
        ],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert res.json()["detail"]["uncovered_clause_ids"] == [req_b_id]


def test_rejected_dispatch_creates_no_workspace(client: TestClient) -> None:
    body = {
        "title": "Should Not Exist",
        "description": "d",
        "requirements": [{"text": "clause a"}],
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    listing = client.get("/api/workspaces")
    assert listing.json() == []


def test_single_task_dispatch_with_requirements_and_no_clauses_is_created(
    client: TestClient,
) -> None:
    body = {
        "title": "Single Task",
        "description": "d",
        "requirements": [{"text": "clause a"}],
        "tasks": [_task("a")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_multi_task_dispatch_without_requirements_is_created(client: TestClient) -> None:
    body = {
        "title": "No Requirements",
        "description": "d",
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_overlapping_clause_ownership_is_accepted(client: TestClient) -> None:
    req_id = _req_id("shared clause")
    body = {
        "title": "Overlap",
        "description": "d",
        "requirements": [{"text": "shared clause"}],
        "tasks": [
            _task("a", [_clause("done a", [req_id])]),
            _task("b", [_clause("done b", [req_id])]),
        ],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_test_ref_clause_does_not_trigger_rejection(client: TestClient) -> None:
    body = {
        "title": "Test Ref Bound",
        "description": "d",
        "requirements": [{"text": "clause a", "test_ref": "scn-1"}],
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_caller_chosen_requirement_id_is_rejected_before_workspace_is_created(
    client: TestClient,
) -> None:
    body = {
        "title": "Caller Chosen Id",
        "description": "d",
        "requirements": [{"id": "req-caller-chosen", "text": "clause a"}],
        "tasks": [
            _task("a", [_clause("done a", ["req-caller-chosen"])]),
            _task("b"),
        ],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422
    assert _SUPPLIED_CLAUSE_ID_DETAIL in str(res.json()["detail"])


def test_caller_withdrawn_marker_is_stripped_before_persistence(
    client: TestClient,
) -> None:
    req_a_id = _req_id("clause a")
    body = {
        "title": "Caller Withdrawn",
        "description": "d",
        "requirements": [{"text": "clause a", "withdrawn_by_question_id": "q-1"}],
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert res.json()["detail"]["uncovered_clause_ids"] == [req_a_id]


def test_rejection_fires_with_clause_audit_mode_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "off")
    body = {
        "title": "Audit Off",
        "description": "d",
        "requirements": [{"text": "clause a"}],
        "tasks": [_task("a"), _task("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
