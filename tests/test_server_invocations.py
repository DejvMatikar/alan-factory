from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workflow as workflow_mod
from alan.dispatcher import PubSub
from alan.queue import Queue
from alan.workflow import SCHEMA_VERSION, WorkflowGraph, save_workflow
from alan.workspace import create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


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
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _seed_workflow(ws_root: Path, *, task_id: str = "task") -> tuple[str, str]:
    ws = create(task_id, "desc", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("orchestrator", role="orchestrator", model="opus")
    g.complete_node("orchestrator", dispatched=["alpha"])
    g.start_node("alpha", role="alpha", model="sonnet", depends_on=["orchestrator"])
    g.complete_node("alpha")
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    stream = workflow_mod.stream_path(ws, inv_id, "alpha")
    stream.write_text(
        json.dumps({"type": "system"}) + "\n"
        + json.dumps({"type": "result", "result": "ok"}) + "\n",
        encoding="utf-8",
    )
    return ws.task_id, inv_id


def test_list_invocations_404_for_unknown_workspace(client: TestClient) -> None:
    res = client.get("/api/workspaces/nope/invocations")
    assert res.status_code == 404


def test_list_invocations_empty(client: TestClient, ws_root: Path) -> None:
    create("solo", "", root=ws_root)
    res = client.get("/api/workspaces/solo/invocations")
    assert res.status_code == 200
    assert res.json() == []


def test_list_invocations_returns_ids(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(f"/api/workspaces/{task_id}/invocations")
    assert res.status_code == 200
    assert res.json() == [inv_id]


def test_get_invocation_returns_workflow_doc(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(f"/api/workspaces/{task_id}/invocations/{inv_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["id"] == inv_id
    assert body["status"] == "success"
    assert {t["id"] for t in body["tasks"]} == {"orchestrator", "alpha"}


def test_get_invocation_unknown_workspace_404(client: TestClient) -> None:
    res = client.get("/api/workspaces/nope/invocations/inv_001")
    assert res.status_code == 404


def test_get_invocation_unknown_inv_404(client: TestClient, ws_root: Path) -> None:
    create("ws", "", root=ws_root)
    res = client.get("/api/workspaces/ws/invocations/inv_999")
    assert res.status_code == 404


def test_get_invocation_500_on_corrupt_yaml(
    client: TestClient, ws_root: Path
) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    bad = _inv_dir(ws_root, task_id) / inv_id / "workflow.yaml"
    bad.write_text("- not\n- a\n- map\n", encoding="utf-8")
    res = client.get(f"/api/workspaces/{task_id}/invocations/{inv_id}")
    assert res.status_code == 500


def test_get_agent_stream_returns_events(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/stream"
    )
    assert res.status_code == 200
    body = res.json()
    assert [e["type"] for e in body] == ["system", "result"]


def test_get_agent_stream_unknown_workspace_404(client: TestClient) -> None:
    res = client.get(
        "/api/workspaces/nope/invocations/inv_001/agents/x/stream"
    )
    assert res.status_code == 404


def test_get_agent_stream_unknown_role_404(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/missing/stream"
    )
    assert res.status_code == 404


def test_get_invocation_rejects_malformed_inv_id(
    client: TestClient, ws_root: Path
) -> None:
    create("ws", "", root=ws_root)
    res = client.get("/api/workspaces/ws/invocations/inv_1")
    assert res.status_code == 404
    assert "invalid invocation id" in res.json()["detail"]


def test_get_agent_stream_rejects_malformed_role(
    client: TestClient, ws_root: Path
) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/1bad/stream"
    )
    assert res.status_code == 404
    assert "invalid node id" in res.json()["detail"]


def test_get_agent_stream_rejects_malformed_inv_id(
    client: TestClient, ws_root: Path
) -> None:
    create("ws", "", root=ws_root)
    res = client.get("/api/workspaces/ws/invocations/inv_xxx/agents/x/stream")
    assert res.status_code == 404
    assert "invalid invocation id" in res.json()["detail"]


def _inv_dir(ws_root: Path, task_id: str) -> Path:
    return ws_root / task_id / "tasks" / task_id / "invocations"


def _write_result(ws_root: Path, task_id: str, inv_id: str, node_id: str) -> None:
    agents_dir = _inv_dir(ws_root, task_id) / inv_id / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{node_id}.result.json").write_text(
        json.dumps({
            "role": node_id,
            "model": "sonnet",
            "result_text": "output text",
            "completed_at": "2026-05-19T10:00:00+00:00",
        }),
        encoding="utf-8",
    )


def _write_config(ws_root: Path, task_id: str, inv_id: str, node_id: str) -> None:
    agents_dir = _inv_dir(ws_root, task_id) / inv_id / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{node_id}.config.json").write_text(
        json.dumps({
            "system_prompt": "you are research",
            "user_text": "do the task",
            "allowed_tools": ["Read"],
            "max_turns": 20,
            "permission_mode": "auto",
            "model": "sonnet",
            "turns_source": "static",
            "derived_max_turns": 20,
        }),
        encoding="utf-8",
    )


def test_get_agent_result_returns_doc(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    _write_result(ws_root, task_id, inv_id, "alpha")
    res = client.get(f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/result")
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "alpha"
    assert body["result_text"] == "output text"


def test_get_agent_result_unknown_workspace_404(client: TestClient) -> None:
    res = client.get("/api/workspaces/nope/invocations/inv_001/agents/x/result")
    assert res.status_code == 404


def test_get_agent_result_absent_404(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/result"
    )
    assert res.status_code == 404
    assert "no result" in res.json()["detail"]


def test_get_agent_result_rejects_malformed_node_id(
    client: TestClient, ws_root: Path
) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/1bad/result"
    )
    assert res.status_code == 404
    assert "invalid node id" in res.json()["detail"]


def test_get_agent_result_rejects_malformed_inv_id(
    client: TestClient, ws_root: Path
) -> None:
    create("ws2", "", root=ws_root)
    res = client.get("/api/workspaces/ws2/invocations/inv_xxx/agents/x/result")
    assert res.status_code == 404
    assert "invalid invocation id" in res.json()["detail"]


def test_get_agent_config_returns_doc(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    _write_config(ws_root, task_id, inv_id, "alpha")
    res = client.get(f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/config")
    assert res.status_code == 200
    body = res.json()
    assert body["system_prompt"] == "you are research"
    assert body["allowed_tools"] == ["Read"]
    assert body["max_turns"] == 20
    assert body["turns_source"] == "static"
    assert body["derived_max_turns"] == 20


def test_get_agent_config_unknown_workspace_404(client: TestClient) -> None:
    res = client.get("/api/workspaces/nope/invocations/inv_001/agents/x/config")
    assert res.status_code == 404


def test_get_agent_config_absent_404(client: TestClient, ws_root: Path) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/config"
    )
    assert res.status_code == 404
    assert "no config" in res.json()["detail"]


def test_get_agent_config_rejects_malformed_node_id(
    client: TestClient, ws_root: Path
) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/1bad/config"
    )
    assert res.status_code == 404
    assert "invalid node id" in res.json()["detail"]


def test_get_agent_config_rejects_malformed_inv_id(
    client: TestClient, ws_root: Path
) -> None:
    create("ws3", "", root=ws_root)
    res = client.get("/api/workspaces/ws3/invocations/inv_xxx/agents/x/config")
    assert res.status_code == 404
    assert "invalid invocation id" in res.json()["detail"]


def test_get_agent_stream_skips_blank_and_bad_lines(
    client: TestClient, ws_root: Path
) -> None:
    task_id, inv_id = _seed_workflow(ws_root)
    stream = _inv_dir(ws_root, task_id) / inv_id / "agents" / "alpha.stream.jsonl"
    stream.write_text(
        "\n"
        + "not json\n"
        + json.dumps(["array"]) + "\n"
        + json.dumps({"type": "result", "result": "ok"}) + "\n",
        encoding="utf-8",
    )
    res = client.get(
        f"/api/workspaces/{task_id}/invocations/{inv_id}/agents/alpha/stream"
    )
    assert res.status_code == 200
    body = res.json()
    assert body == [{"type": "result", "result": "ok"}]
