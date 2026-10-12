from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan.dispatcher import PubSub
from alan.queue import Queue

_AUDIT_FILE = "verification_audit.jsonl"


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _route_audit_rows(audit_root: Path) -> list[dict[str, object]]:
    log_path = audit_root / _AUDIT_FILE
    if not log_path.exists():
        return []
    entries = [json.loads(line) for line in log_path.read_text().splitlines()]
    return [e for e in entries if e.get("action") == "clause_audit_route_unaudited"]


def test_roadmap_item_id_with_absent_requirements_audits(
    client: TestClient, audit_root: Path
) -> None:
    body = {"title": "Roadmap No Reqs", "description": "d", "roadmap_item_id": "H8.1b"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    rows = _route_audit_rows(audit_root)
    assert len(rows) == 1
    assert rows[0]["roadmap_item_id"] == "H8.1b"
    assert rows[0]["task_id"] == res.json()["task_id"]


def test_roadmap_item_id_with_empty_requirements_audits(
    client: TestClient, audit_root: Path
) -> None:
    body = {
        "title": "Roadmap Empty Reqs", "description": "d",
        "roadmap_item_id": "H8.1b", "requirements": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    assert len(_route_audit_rows(audit_root)) == 1


def test_roadmap_item_id_with_requirements_is_noop(
    client: TestClient, audit_root: Path
) -> None:
    body = {
        "title": "Roadmap With Reqs", "description": "d",
        "roadmap_item_id": "H8.1b",
        "requirements": [{"text": "deliver the outcome"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    assert _route_audit_rows(audit_root) == []


def test_no_roadmap_item_id_is_noop_regardless_of_requirements(
    client: TestClient, audit_root: Path
) -> None:
    res = client.post("/api/workspaces", json={"title": "No Roadmap", "description": "d"})
    assert res.status_code == 201
    assert _route_audit_rows(audit_root) == []
