from __future__ import annotations

import contextlib
import inspect
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.retention as retention_mod
import alan.routes.workspaces as workspaces_routes
from alan import server
from alan._chat_core import ChatMessage
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover, HandoverType, save_handover
from alan.queue import Job, Queue
from alan.workflow import WorkflowDoc, WorkflowTask, create_invocation_dir, save_workflow
from alan.workspace import (
    InterventionAction,
    Task,
    TaskDependency,
    TaskDoDClause,
    TaskStatus,
    Workspace,
    create,
    list_workspaces,
    mark_delivered,
    read,
    update_assumptions,
)
from tests.fs_timestamp_support import write_summary_sidecar


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


def test_get_workspace_returns_tasks_field(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Tasks WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert "tasks" in data
    assert isinstance(data["tasks"], list)


def test_get_workspace_returns_multi_tasks_when_present(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Multi Task WS", "d", root=ws_root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["tasks"] = [
        {
            "id": "alpha",
            "depends_on": [],
            "team": "implementation_plan",
            "effort": "medium",
            "status": "ready",
        },
        {
            "id": "beta",
            "depends_on": ["alpha"],
            "team": "implementation",
            "effort": "low",
            "status": "pending",
        },
    ]
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["id"] == "alpha"
    assert data["tasks"][0]["depends_on"] == []
    assert data["tasks"][1]["id"] == "beta"
    assert data["tasks"][1]["depends_on"] == ["alpha"]


def test_get_workspace_surfaces_triage_team(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Triage Sweep", "d", root=ws_root, team="triage")
    res = client.get(f"/api/workspaces/{ws.task_id}?include=triage")
    assert res.status_code == 200
    assert res.json()["team"] == "triage"


def test_get_workspace_team_null_when_meta_team_unset(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Team WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["team"] is None


def test_get_workspace_surfaces_non_triage_team_unchanged(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Implementation WS", "d", root=ws_root, team="implementation")
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["team"] == "implementation"


def _task_body(task_id: str, status: str = "pending") -> dict[str, object]:
    return {
        "id": task_id,
        "effort": "medium",
        "status": status,
        "depends_on": [],
        "team": None,
    }


def test_create_workspace_legacy_returns_root_task_ids(
    client: TestClient,
) -> None:
    res = client.post("/api/workspaces", json={"title": "Legacy", "description": "d"})
    assert res.status_code == 201
    data = res.json()
    assert "root_task_ids" in data
    assert data["root_task_ids"] == [data["task_id"]]


def test_create_workspace_multi_task_persists_dag(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Multi",
        "description": "d",
        "tasks": [_task_body("plan"), _task_body("impl")],
        "dependencies": [{"from_id": "plan", "to_id": "impl"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    data = res.json()
    task_id = data["task_id"]
    assert data["root_task_ids"] == ["plan"]
    ws = read(task_id, root=ws_root)
    assert len(ws.meta.tasks) == 2
    impl = next(t for t in ws.meta.tasks if t.id == "impl")
    assert impl.depends_on == ["plan"]


def test_create_workspace_multi_task_status_overridden_to_pending(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Status Override",
        "description": "d",
        "tasks": [_task_body("t1", "running"), _task_body("t2", "completed")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    for t in ws.meta.tasks:
        assert t.status is TaskStatus.PENDING


def test_create_workspace_multi_task_two_parallel_roots(
    client: TestClient,
) -> None:
    body = {
        "title": "Two Roots",
        "description": "d",
        "tasks": [_task_body("a"), _task_body("b"), _task_body("c")],
        "dependencies": [
            {"from_id": "a", "to_id": "c"},
            {"from_id": "b", "to_id": "c"},
        ],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    data = res.json()
    assert set(data["root_task_ids"]) == {"a", "b"}


def test_create_workspace_422_cycle(client: TestClient) -> None:
    body = {
        "title": "Cycle",
        "description": "d",
        "tasks": [_task_body("a"), _task_body("b")],
        "dependencies": [
            {"from_id": "a", "to_id": "b"},
            {"from_id": "b", "to_id": "a"},
        ],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_422_dangling_from_id(client: TestClient) -> None:
    body = {
        "title": "Dangle From",
        "description": "d",
        "tasks": [_task_body("a")],
        "dependencies": [{"from_id": "x", "to_id": "a"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_422_dangling_to_id(client: TestClient) -> None:
    body = {
        "title": "Dangle To",
        "description": "d",
        "tasks": [_task_body("a")],
        "dependencies": [{"from_id": "a", "to_id": "z"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_422_duplicate_task_id(client: TestClient) -> None:
    body = {
        "title": "Dup",
        "description": "d",
        "tasks": [_task_body("a"), _task_body("a")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_task_depends_on_cleared_by_route(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Smuggle",
        "description": "d",
        "tasks": [{**_task_body("a"), "depends_on": ["b"]}, _task_body("b")],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    for t in ws.meta.tasks:
        assert t.depends_on == []


def test_create_workspace_422_empty_tasks_with_deps(client: TestClient) -> None:
    body = {
        "title": "Empty Tasks With Deps",
        "description": "d",
        "tasks": [],
        "dependencies": [{"from_id": "x", "to_id": "y"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_422_null_task_id(client: TestClient) -> None:
    body = {
        "title": "Null ID",
        "description": "d",
        "tasks": [{"id": None, "effort": "medium", "status": "pending", "depends_on": []}],
        "dependencies": [],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 422


def test_create_workspace_422_empty_deps_with_no_tasks_passes(
    client: TestClient,
) -> None:
    body = {"title": "Empty Deps", "description": "d", "tasks": [], "dependencies": []}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201


def test_create_workspace_frontend_shape_verbatim(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Frontend Shape",
        "description": "d",
        "tasks": [
            {
                "id": "task-a",
                "team": "implementation",
                "budget": "medium",
                "title": "Task A",
                "scope_audit": [],
            },
            {
                "id": "task-b",
                "team": "implementation",
                "budget": "low",
                "title": "Task B",
                "scope_audit": [],
            },
        ],
        "dependencies": [{"from_id": "task-a", "to_id": "task-b"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    data = res.json()
    assert data["root_task_ids"] == ["task-a"]
    ws = read(data["task_id"], root=ws_root)
    assert len(ws.meta.tasks) == 2
    task_b = next(t for t in ws.meta.tasks if t.id == "task-b")
    assert task_b.team == "implementation"
    assert task_b.status is TaskStatus.PENDING
    assert task_b.depends_on == ["task-a"]


def test_dispatch_task_team_legacy_task_id_matches(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    ws = create("Legacy Dispatch", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202


def test_dispatch_task_team_multi_task_per_task_id_matches(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [
        Task(id="plan", status=TaskStatus.PENDING),
        Task(id="impl", status=TaskStatus.PENDING, depends_on=["plan"]),
    ]
    deps = [TaskDependency(from_id="plan", to_id="impl")]
    ws = create("Multi Dispatch", "d", root=ws_root, tasks=tasks, dependencies=deps)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/plan/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202


def test_dispatch_task_team_unknown_task_id_returns_404(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    ws = create("404 Dispatch", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/nonexistent/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404


def test_dispatch_task_team_multi_task_unknown_id_returns_404(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [Task(id="plan", status=TaskStatus.PENDING)]
    ws = create("Multi 404", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/unknown/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 404


def test_dispatch_task_team_uses_shared_assert_team_dispatchable_guard() -> None:
    route_source = inspect.getsource(workspaces_routes.dispatch_task_team)
    assert "perform_task_team_dispatch" in route_source
    impl_source = inspect.getsource(workspaces_routes.perform_task_team_dispatch)
    assert "assert_team_dispatchable" in impl_source


def _make_handover(
    task_id: str, handover_id: str, handover_type: HandoverType = "forward"
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type=handover_type,
        task_id=task_id,
        from_team="research",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="synthesis",
    )


def _last_job(queue: Queue) -> Job:
    jobs = queue.list_jobs()
    assert jobs, "expected at least one enqueued job"
    return jobs[-1]


def test_dispatch_task_team_uses_blocked_handover_when_set(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT),
    ]
    ws = create("Blocked Dispatch", "d", root=ws_root, tasks=tasks)
    blocked = _make_handover(ws.task_id, "blocked-h1", "escalation")
    save_handover(ws, blocked)
    sibling = _make_handover(ws.task_id, "sibling-h2", "terminal")
    save_handover(ws, sibling)
    meta = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    for t in meta["tasks"]:
        if t["id"] == "sub-b":
            t["blocked_handover_id"] = "blocked-h1"
    (ws.path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-b/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    job = _last_job(queue)
    assert job.parent_handover_id == "blocked-h1"


def test_dispatch_task_team_falls_back_to_latest_when_no_blocked_handover(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("No Blocked", "d", root=ws_root, tasks=tasks)
    save_handover(ws, _make_handover(ws.task_id, "latest-h1"))

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    job = _last_job(queue)
    assert job.parent_handover_id == "latest-h1"


def test_dispatch_task_team_missing_blocked_handover_file_falls_back_to_none(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [Task(id="sub-b", status=TaskStatus.AWAITING_INPUT)]
    ws = create("Missing Blocked", "d", root=ws_root, tasks=tasks)
    meta = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    for t in meta["tasks"]:
        if t["id"] == "sub-b":
            t["blocked_handover_id"] = "nonexistent-handover-id"
    (ws.path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-b/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    job = _last_job(queue)
    assert job.parent_handover_id is None


_COUNTER_CASE_BODY = {
    "against": "This locks in scope before the shop has bandwidth to review it.",
    "cheapest_80": "Ship the smallest read-only slice first; defer the rest.",
    "cost_estimate": "insufficient data",
}


def test_dispatch_task_team_with_counter_case_writes_one_waiver_audit_line(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Counter Case Dispatch", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research", "counter_case": _COUNTER_CASE_BODY},
    )
    assert res.status_code == 202
    audit_file = logs_root / "counter_case_waiver_audit.jsonl"
    lines = audit_file.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_id"] == ws.task_id
    assert entry["title"] == "Counter Case Dispatch"
    assert entry["counter_case"] == _COUNTER_CASE_BODY
    assert entry["human_proceeded"] is True


def test_dispatch_task_team_multi_task_records_dispatched_subtask_id(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.PENDING),
    ]
    ws = create("Multi Task Counter Case", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/dispatch",
        json={"team": "research", "counter_case": _COUNTER_CASE_BODY},
    )
    assert res.status_code == 202
    audit_file = logs_root / "counter_case_waiver_audit.jsonl"
    lines = audit_file.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_id"] == "sub-a"
    assert entry["task_id"] != ws.task_id
    assert entry["title"] == "Multi Task Counter Case"


def test_dispatch_task_team_without_counter_case_writes_no_waiver_audit(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Manual Dispatch No Counter Case", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    audit_file = logs_root / "counter_case_waiver_audit.jsonl"
    assert not audit_file.exists()


def test_create_then_dispatch_build_flow_writes_exactly_one_waiver_audit_line(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    create_res = client.post(
        "/api/workspaces", json={"title": "Build Flow Task", "description": "d"}
    )
    assert create_res.status_code == 201
    task_id = create_res.json()["task_id"]
    dispatch_res = client.post(
        f"/api/workspaces/{task_id}/tasks/{task_id}/dispatch",
        json={"team": "implementation_plan", "counter_case": _COUNTER_CASE_BODY},
    )
    assert dispatch_res.status_code == 202
    audit_file = logs_root / "counter_case_waiver_audit.jsonl"
    assert len(audit_file.read_text().splitlines()) == 1


def test_counter_case_waiver_audit_honors_logs_root_override(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import roots

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    logs_root = tmp_path / "override-logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Override Logs Root", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research", "counter_case": _COUNTER_CASE_BODY},
    )
    assert res.status_code == 202
    assert (logs_root / "counter_case_waiver_audit.jsonl").exists()
    assert not any(fake_home.rglob("counter_case_waiver_audit.jsonl"))
    assert roots.logs_root() == logs_root


def test_create_workspace_runs_predispatch_scan_and_stores_findings(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod
    from alan.scope_audit import ScopeGapFinding

    finding = ScopeGapFinding(
        kind="prior_workspace",
        summary="near-duplicate workspace found",
        references=["old-ws"],
        suggested_action="needs_user_call",
    )
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [finding])
    res = client.post("/api/workspaces", json={"title": "New Task", "description": "d"})
    assert res.status_code == 201
    task_id = res.json()["task_id"]

    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert len(detail["scope_audit"]) == 1
    assert detail["scope_audit"][0]["kind"] == "prior_workspace"


def test_get_workspace_returns_empty_scope_audit_by_default(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])
    res = client.post("/api/workspaces", json={"title": "Clean Task", "description": "d"})
    assert res.status_code == 201
    task_id = res.json()["task_id"]

    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["scope_audit"] == []


def test_create_workspace_writes_predispatch_log(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    from alan.app import workspace_creation as creation_mod
    from alan.scope_audit import ScopeGapFinding

    log_path = tmp_path / "predispatch.jsonl"
    monkeypatch.setenv("ALAN_PREDISPATCH_LOG", str(log_path))
    finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="PR overlap",
        references=["PR #5"],
        suggested_action="needs_user_call",
    )
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [finding])
    res = client.post("/api/workspaces", json={"title": "Log Task", "description": "d"})
    assert res.status_code == 201

    assert log_path.exists()
    entry = _json.loads(log_path.read_text().splitlines()[0])
    assert entry["task_id"] == res.json()["task_id"]
    assert entry["title"] == "Log Task"
    assert len(entry["findings"]) == 1
    assert entry["findings"][0]["kind"] == "in_flight_pr"


def test_predispatch_log_path_falls_back_to_logs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    monkeypatch.delenv("ALAN_PREDISPATCH_LOG", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    assert creation_mod._predispatch_log_path() == tmp_path / "logs" / "predispatch_findings.jsonl"


def test_predispatch_log_path_defaults_to_home_logs_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.app import workspace_creation as creation_mod

    monkeypatch.delenv("ALAN_PREDISPATCH_LOG", raising=False)
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    expected = Path.home() / ".alan" / "logs" / "predispatch_findings.jsonl"
    assert creation_mod._predispatch_log_path() == expected


def test_create_workspace_scan_raises_returns_201_with_empty_findings(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    def boom(*a: object, **kw: object) -> list[object]:
        raise RuntimeError("scan boom")

    monkeypatch.setattr(creation_mod, "scan_predispatch", boom)
    res = client.post("/api/workspaces", json={"title": "Boom Task", "description": "d"})
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["scope_audit"] == []


def test_create_workspace_anti_scope_violation_surfaces_finding(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.project_doc import apply_vision_scope_amendment, ensure_doc

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Billing Guard", root=projects_root)
    project, _ = ensure_doc(project, root=projects_root)
    apply_vision_scope_amendment(
        project,
        vision="Keep the app lean",
        scope="Anti-scope: do not touch the billing module.",
        decision_entry="set anti-scope",
        root=projects_root,
    )

    res = client.post(
        "/api/workspaces",
        json={
            "title": "Refactor billing module",
            "description": "clean up the billing module payment flow",
            "project_id": project.id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    kinds = [f["kind"] for f in detail["scope_audit"]]
    assert "anti_scope" in kinds


def test_create_workspace_on_scope_work_no_anti_scope_finding(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.project_doc import apply_vision_scope_amendment, ensure_doc

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Chat Guard", root=projects_root)
    project, _ = ensure_doc(project, root=projects_root)
    apply_vision_scope_amendment(
        project,
        vision="Ship chat improvements",
        scope="In scope: chat UI.\nAnti-scope: do not touch the billing module.",
        decision_entry="set scope",
        root=projects_root,
    )

    res = client.post(
        "/api/workspaces",
        json={
            "title": "Improve chat UI styling",
            "description": "polish the chat composer",
            "project_id": project.id,
        },
    )
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    assert detail["scope_audit"] == []


def test_create_workspace_unknown_project_id_no_anti_scope_error(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])
    res = client.post(
        "/api/workspaces",
        json={"title": "Ghost Project WS", "description": "d", "project_id": "no-such-project"},
    )
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    assert detail["scope_audit"] == []


def test_create_workspace_without_project_id_skips_anti_scope_resolution(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    def boom(*a: object, **kw: object) -> str | None:
        raise AssertionError("read_scope_section must not be called without project_id")

    monkeypatch.setattr(creation_mod, "read_scope_section", boom)
    res = client.post(
        "/api/workspaces", json={"title": "No Project Anti Scope", "description": "d"}
    )
    assert res.status_code == 201


def test_create_workspace_anti_scope_resolution_raises_still_201(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    def boom(*a: object, **kw: object) -> str | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(creation_mod, "read_scope_section", boom)
    monkeypatch.setattr(creation_mod, "scan_predispatch", lambda *a, **kw: [])
    res = client.post(
        "/api/workspaces",
        json={"title": "Boom Anti Scope", "description": "d", "project_id": "some-id"},
    )
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    assert detail["scope_audit"] == []


def test_list_workspaces_project_fields_null_when_no_project_id(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Project WS", "d", root=ws_root)
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["project_id"] is None
    assert found["project_title"] is None


def test_list_workspaces_includes_project_id_and_title_when_project_exists(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Spine", root=projects_root)
    ws = create("Affiliated WS", "d", root=ws_root, project_id=project.id)

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["project_id"] == project.id
    assert found["project_title"] == "Spine"


def test_list_workspaces_project_title_null_when_project_dangling(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    ws = create("Dangling Project WS", "d", root=ws_root, project_id="ghost-project")

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["project_id"] == "ghost-project"
    assert found["project_title"] is None


def test_list_workspaces_archived_bucket_carries_project_title(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.workspace import Status, transition

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Archived Spine", root=projects_root)
    ws = create("Archived Affiliated WS", "d", root=ws_root, project_id=project.id)
    transition(ws, Status.ARCHIVED)

    listing = client.get("/api/workspaces", params={"bucket": "archived"}).json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["project_id"] == project.id
    assert found["project_title"] == "Archived Spine"


def test_list_workspaces_reads_project_json_once_per_distinct_project_id(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Shared", root=projects_root)
    create("WS One", "d", root=ws_root, project_id=project.id)
    create("WS Two", "d", root=ws_root, project_id=project.id)
    create("WS Three", "d", root=ws_root, project_id=project.id)

    real_read = project_mod.read
    calls: list[str] = []

    def _counting_read(project_id: str, *, root: Path | None = None) -> project_mod.Project:
        calls.append(project_id)
        return real_read(project_id, root=root)

    monkeypatch.setattr(project_mod, "read", _counting_read)

    listing = client.get("/api/workspaces").json()
    assert len(listing) == 3
    assert calls.count(project.id) == 1


def test_list_workspaces_task_counts_and_pr_number_match_detail_tasks(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Multi Task Counts",
        "d",
        root=ws_root,
        tasks=[
            Task(
                id="a",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="x", evidence="PR #5")],
            ),
            Task(id="b", status=TaskStatus.FAILED),
            Task(id="c", status=TaskStatus.READY),
        ],
    )

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    tasks = detail["tasks"]

    assert found["tasks_total"] == len(tasks) == 3
    assert found["tasks_done"] == sum(1 for t in tasks if t["status"] == "completed") == 1
    assert found["pr_number"] == 5


def test_list_workspaces_tasks_done_excludes_failed_task(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Failed Only", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.FAILED)])
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["tasks_total"] == 1
    assert found["tasks_done"] == 0


def test_list_workspaces_task_fields_default_when_no_tasks(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Tasks WS", "d", root=ws_root)
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["tasks_total"] == 0
    assert found["tasks_done"] == 0
    assert found["pr_number"] is None


def test_list_workspaces_auto_archive_days_null_when_env_unset(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.delenv("ALAN_AUTO_ARCHIVE_DAYS", raising=False)
    ws = create("Completed No Env WS", "d", root=ws_root)
    transition(ws, Status.COMPLETED)

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["auto_archive_days"] is None


def test_list_workspaces_auto_archive_days_set_for_completed_when_env_set(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("Completed With Env WS", "d", root=ws_root)
    transition(ws, Status.COMPLETED)

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["auto_archive_days"] == 14


def test_list_workspaces_auto_archive_days_null_for_non_completed_when_env_set(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("Active With Env WS", "d", root=ws_root)

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["auto_archive_days"] is None


def test_list_workspaces_auto_archive_days_null_for_invalid_env_value(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "not-a-number")
    ws = create("Completed Invalid Env WS", "d", root=ws_root)
    transition(ws, Status.COMPLETED)

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["auto_archive_days"] is None


def test_list_workspaces_archived_bucket_auto_archive_days_always_null(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    ws = create("Already Archived WS", "d", root=ws_root)
    transition(ws, Status.ARCHIVED)

    listing = client.get("/api/workspaces", params={"bucket": "archived"}).json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["auto_archive_days"] is None


def test_list_workspaces_invalid_auto_archive_env_does_not_log_warning(
    client: TestClient,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "not-a-number")
    ws = create("Completed Invalid Env WS", "d", root=ws_root)
    transition(ws, Status.COMPLETED)

    with caplog.at_level(logging.WARNING, logger="alan.retention"):
        client.get("/api/workspaces")

    assert not any("invalid ALAN_AUTO_ARCHIVE_DAYS" in r.message for r in caplog.records)


def test_list_workspaces_resolves_auto_archive_threshold_once_per_request(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workspace import Status, transition

    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    for i in range(3):
        ws = create(f"Completed {i}", "d", root=ws_root)
        transition(ws, Status.COMPLETED)

    real_resolve = retention_mod.resolve_min_age_days
    calls: list[None] = []

    def _counting_resolve(*, log_invalid: bool = True) -> int | None:
        calls.append(None)
        return real_resolve(log_invalid=log_invalid)

    monkeypatch.setattr(retention_mod, "resolve_min_age_days", _counting_resolve)

    listing = client.get("/api/workspaces").json()
    assert len(listing) == 3
    assert len(calls) == 1


def test_create_workspace_with_requirements_persists(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Req Create",
        "description": "d",
        "requirements": [{"text": "it works", "test_ref": "test_it_works"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["requirements"] is not None
    assert len(detail["requirements"]) == 1
    assert detail["requirements"][0]["text"] == "it works"
    assert detail["requirements"][0]["test_ref"] == "test_it_works"
    assert detail["requirements"][0]["status"] == "PENDING"


def test_create_workspace_requirements_none_by_default(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post("/api/workspaces", json={"title": "No Reqs", "description": "d"})
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["requirements"] is None


def test_create_workspace_requirements_status_sanitized(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Sanitize Reqs",
        "description": "d",
        "requirements": [
            {"text": "r", "test_ref": "t", "status": "PASS", "merged_pr_url": "https://x.com"}
        ],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["requirements"][0]["status"] == "PENDING"
    assert detail["requirements"][0]["merged_pr_url"] is None


def test_create_workspace_unbound_requirement_surfaces_id(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "Unbound Req", "description": "d", "requirements": [{"text": "a clause"}]}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    req = detail["requirements"][0]
    assert req["test_ref"] is None
    assert req["id"].startswith("req-")


def test_workspace_detail_serializes_assumptions(client: TestClient, ws_root: Path) -> None:
    ws = create("Has Assumptions", "d", root=ws_root)
    update_assumptions(ws, ["the API is stable", "the user meant staging"])
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["assumptions"] == ["the API is stable", "the user meant staging"]


def test_create_workspace_with_registered_engine_key_persists(
    client: TestClient, ws_root: Path
) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    body = {"title": "Engine WS", "description": "d", "engine_key": "acme/widget"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key == "acme/widget"


def test_create_workspace_unknown_engine_key_rejected_400(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "Bad Engine WS", "description": "d", "engine_key": "no/such-engine"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert res.json()["detail"] == "unknown engine: 'no/such-engine'"
    assert list_workspaces(root=ws_root) == []


def test_create_workspace_malformed_engine_key_rejected_400(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "Malformed Engine WS", "description": "d", "engine_key": "not-a-slug"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert list_workspaces(root=ws_root) == []


def test_create_workspace_without_engine_key_defaults_to_none(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post("/api/workspaces", json={"title": "No Engine WS", "description": "d"})
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key is None


def test_create_workspace_second_engine_fixture_governs_workspace(
    client: TestClient, ws_root: Path
) -> None:
    from alan.engine_config import load_yaml

    fixtures = Path(__file__).parent / "fixtures"
    secondary = load_yaml(fixtures / "engine_config_secondary.yaml")
    body = {"title": "Secondary WS", "description": "d", "engine_key": secondary.repo.slug}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key == "example-co/secondary"


def test_create_workspace_passes_engine_key_as_scope_to_predispatch_scan(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod
    from alan.engine_config import load_yaml

    fixtures = Path(__file__).parent / "fixtures"
    secondary = load_yaml(fixtures / "engine_config_secondary.yaml")

    captured: dict[str, object] = {}

    def _capture(*a: object, **kw: object) -> list[object]:
        captured.update(kw)
        return []

    monkeypatch.setattr(creation_mod, "scan_predispatch", _capture)
    res = client.post(
        "/api/workspaces",
        json={"title": "Scoped Task", "description": "d", "engine_key": secondary.repo.slug},
    )
    assert res.status_code == 201
    assert captured["scope_engine_key"] == secondary.repo.slug


def test_create_workspace_default_engine_scope_passed_as_none_to_predispatch_scan(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    captured: dict[str, object] = {}

    def _capture(*a: object, **kw: object) -> list[object]:
        captured.update(kw)
        return []

    monkeypatch.setattr(creation_mod, "scan_predispatch", _capture)
    res = client.post(
        "/api/workspaces", json={"title": "Unscoped Task", "description": "d"}
    )
    assert res.status_code == 201
    assert captured["scope_engine_key"] is None


def test_create_workspace_inherits_project_engine_key(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="sprocket")))
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)

    body = {"title": "Inherit WS", "description": "d", "project_id": project.id}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key == "acme/sprocket"


def test_create_workspace_explicit_engine_key_overrides_project(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="sprocket")))
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="other")))
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)

    body = {
        "title": "Override WS",
        "description": "d",
        "project_id": project.id,
        "engine_key": "acme/other",
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key == "acme/other"


def test_create_workspace_explicit_null_engine_key_does_not_inherit_project(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="sprocket")))
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)

    body = {
        "title": "Explicit Null WS",
        "description": "d",
        "project_id": project.id,
        "engine_key": None,
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key is None


def test_create_workspace_project_with_no_engine_key_defaults_to_none(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("No Engine Spine", root=projects_root)

    body = {"title": "No Engine Inherit WS", "description": "d", "project_id": project.id}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key is None


def test_create_workspace_dangling_project_engine_key_rejected_400(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.engine_config import EngineConfig, RepoIdentity, register, unregister_and_persist

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="ghost")))
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Ghost Engine Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/ghost"}, root=projects_root)
    unregister_and_persist(RepoIdentity(owner="acme", repo="ghost"))

    body = {"title": "Dangling WS", "description": "d", "project_id": project.id}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert res.json()["detail"] == "project's bound engine no longer exists"
    assert list_workspaces(root=ws_root) == []


def test_create_workspace_unknown_engine_no_project_id_still_generic_message(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "No Project Bad Engine WS", "description": "d", "engine_key": "no/such"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 400
    assert res.json()["detail"] == "unknown engine: 'no/such'"


def test_create_workspace_accepts_and_persists_roadmap_item_id(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "Roadmap Stamped WS", "description": "d", "roadmap_item_id": "itm_abc123"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.roadmap_item_id == "itm_abc123"


def test_create_workspace_without_roadmap_item_id_defaults_none(
    client: TestClient, ws_root: Path
) -> None:
    body = {"title": "No Roadmap Stamp WS", "description": "d"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.roadmap_item_id is None


def test_create_workspace_missing_project_falls_through_to_default_engine(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    body = {"title": "Ghost Project WS", "description": "d", "project_id": "no-such-project"}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    ws = read(res.json()["task_id"], root=ws_root)
    assert ws.meta.engine_key is None


def test_workspace_create_without_project_id_resolves_no_engine(ws_root: Path) -> None:
    ws = create("Autonomous WS", "d", root=ws_root)
    assert ws.meta.engine_key is None


def test_workspace_inherits_project_engine_end_to_end(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.project as project_mod
    from alan.engine_config import EngineConfig, Posture, RepoIdentity, register

    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="sprocket"), posture=Posture.propose)
    )
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("E2E Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)

    body = {"title": "E2E Inherit WS", "description": "d", "project_id": project.id}
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert ws.meta.engine_key == "acme/sprocket"
    assert ws.meta.engine_snapshot is None

    dispatch_res = client.post(
        f"/api/workspaces/{task_id}/tasks/{task_id}/dispatch",
        json={"team": "research"},
    )
    assert dispatch_res.status_code == 202

    dispatched = read(task_id, root=ws_root)
    assert dispatched.meta.engine_snapshot is not None
    assert dispatched.meta.engine_snapshot.config is not None
    assert dispatched.meta.engine_snapshot.config.repo.slug == "acme/sprocket"


def test_create_workspace_tasks_surface_planning_fields(
    client: TestClient, ws_root: Path
) -> None:
    body = {
        "title": "Multi Plan",
        "description": "d",
        "tasks": [{"id": "a", "team": "implementation"}, {"id": "b", "team": "implementation"}],
        "dependencies": [{"from_id": "a", "to_id": "b"}],
    }
    res = client.post("/api/workspaces", json=body)
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    task = detail["tasks"][0]
    assert task["planning_state"] == "detailed"
    assert task["dod_clauses"] == []
    assert task["parent_task_id"] is None


def test_note_saved_and_returned(client: TestClient, ws_root: Path) -> None:
    ws = create("Note WS", "d", root=ws_root)
    res = client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "my memo"})
    assert res.status_code == 200
    assert res.json() == {"note": "my memo"}


def test_note_persisted_in_detail(client: TestClient, ws_root: Path) -> None:
    ws = create("Note Persist WS", "d", root=ws_root)
    client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "hello"})
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["note"] == "hello"


def test_note_visible_in_listing(client: TestClient, ws_root: Path) -> None:
    ws = create("Note List WS", "d", root=ws_root)
    client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "list memo"})
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["note"] == "list memo"


def test_note_cleared_when_empty_string(client: TestClient, ws_root: Path) -> None:
    ws = create("Note Clear WS", "d", root=ws_root)
    client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "old"})
    res = client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": ""})
    assert res.status_code == 200
    assert res.json()["note"] is None


def test_note_cleared_when_null(client: TestClient, ws_root: Path) -> None:
    ws = create("Note Null WS", "d", root=ws_root)
    client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "old"})
    res = client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": None})
    assert res.status_code == 200
    assert res.json()["note"] is None


def test_note_404_for_unknown_workspace(client: TestClient) -> None:
    res = client.put("/api/workspaces/no-such-ws/note", json={"note": "x"})
    assert res.status_code == 404


def test_note_default_is_none_in_detail(client: TestClient, ws_root: Path) -> None:
    ws = create("No Note WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["note"] is None


def test_note_default_is_none_in_summary(client: TestClient, ws_root: Path) -> None:
    ws = create("No Note Summary WS", "d", root=ws_root)
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["note"] is None


def test_note_whitespace_only_is_cleared(client: TestClient, ws_root: Path) -> None:
    ws = create("Note Whitespace WS", "d", root=ws_root)
    res = client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "   "})
    assert res.status_code == 200
    assert res.json()["note"] is None


def test_note_does_not_bump_updated_at(client: TestClient, ws_root: Path) -> None:
    ws = create("Note Timestamp WS", "d", root=ws_root)
    before = client.get(f"/api/workspaces/{ws.task_id}").json()["updated_at"]
    client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "memo"})
    after = client.get(f"/api/workspaces/{ws.task_id}").json()["updated_at"]
    assert after == before


def test_get_workspace_returns_manager_summary_when_sidecar_present(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Summary WS", "d", root=ws_root)
    write_summary_sidecar(ws, json.dumps({"ok": True, "text": "Everything went fine."}))
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    summary = detail["manager_summary"]
    assert summary["ok"] is True
    assert summary["text"] == "Everything went fine."
    assert summary["run_summary"] is None
    assert summary["written_at"] is not None
    assert summary["stale"] is False


def test_get_workspace_manager_summary_is_none_when_absent(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Summary WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["manager_summary"] is None


def test_get_workspace_manager_summary_degrades_on_invalid_sidecar(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Bad Sidecar WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(json.dumps({"unexpected_key": 123}))
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    summary = detail["manager_summary"]
    assert summary["ok"] is False
    assert summary["text"] is None
    assert summary["run_summary"] is None


def _write_manager_stream_event(
    ws_path: Path, *, cost: float, input_tokens: int, output_tokens: int
) -> None:
    event = {
        "type": "result",
        "total_cost_usd": cost,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    stream = ws_path / "manager_summary_stream.jsonl"
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def test_get_workspace_manager_summary_includes_run_summary_when_stream_present(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Summary With Usage WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(
        json.dumps({"ok": True, "text": "All good."})
    )
    _write_manager_stream_event(ws.path, cost=0.0025, input_tokens=40, output_tokens=15)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()

    run_summary = detail["manager_summary"]["run_summary"]
    assert run_summary is not None
    assert run_summary["model"] == "haiku"
    assert run_summary["total_cost_usd"] == pytest.approx(0.0025)
    assert run_summary["total_input_tokens"] == 40
    assert run_summary["total_output_tokens"] == 15
    assert run_summary["tool_counts"] == []


def test_get_workspace_manager_summary_run_summary_none_when_stream_absent(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Summary No Usage WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(
        json.dumps({"ok": True, "text": "All good."})
    )
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["manager_summary"]["run_summary"] is None


def test_get_workspace_manager_summary_run_summary_present_on_invalid_sidecar_with_usage(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Bad Sidecar With Usage WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(json.dumps({"unexpected_key": 123}))
    _write_manager_stream_event(ws.path, cost=0.001, input_tokens=5, output_tokens=2)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()

    assert detail["manager_summary"]["ok"] is False
    run_summary = detail["manager_summary"]["run_summary"]
    assert run_summary is not None
    assert run_summary["total_cost_usd"] == pytest.approx(0.001)


def test_get_workspace_manager_summary_run_summary_none_on_garbage_stream(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Garbage Stream WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(
        json.dumps({"ok": True, "text": "All good."})
    )
    (ws.path / "manager_summary_stream.jsonl").write_text("not json{{{\n", encoding="utf-8")

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()

    assert detail["manager_summary"]["run_summary"] is None


def test_get_workspace_terminal_outcome_null_for_active(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Active WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["terminal_outcome"] is None


def test_get_workspace_terminal_outcome_for_escalated(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    ws = create("Escalated WS", "d", root=ws_root)
    transition(ws, Status.ESCALATED)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["terminal_outcome"] is not None
    assert detail["terminal_outcome"]["outcome"] == "escalated"
    assert isinstance(detail["terminal_outcome"]["reason"], str)


def test_get_workspace_terminal_outcome_for_failed(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    ws = create("Failed WS", "d", root=ws_root)
    transition(ws, Status.FAILED)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["terminal_outcome"] is not None
    assert detail["terminal_outcome"]["outcome"] == "failed"
    assert isinstance(detail["terminal_outcome"]["reason"], str)


def test_get_workspace_terminal_outcome_degrades_on_exception(
    client: TestClient, ws_root: Path
) -> None:
    from unittest.mock import patch

    from alan.workspace import Status, transition

    ws = create("Broken WS", "d", root=ws_root)
    transition(ws, Status.FAILED)
    with patch(
        "alan.routes._workspace_detail.resolve_terminal_outcome",
        side_effect=RuntimeError("boom"),
    ):
        detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["terminal_outcome"] is None


def test_get_workspace_terminal_outcome_for_partial(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    ws = create("Partial WS", "d", root=ws_root)
    transition(ws, Status.PARTIAL)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["terminal_outcome"] is not None
    assert detail["terminal_outcome"]["outcome"] == "partial"
    assert isinstance(detail["terminal_outcome"]["reason"], str)


def test_get_workspace_park_reason_and_blockers_default_empty(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Fresh WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["park_reason"] is None
    assert detail["blockers"] == []


def test_get_workspace_returns_park_reason_and_blockers_when_present(
    client: TestClient, ws_root: Path
) -> None:
    from alan.park import Blocker, ParkReason
    from alan.workspace import Status, transition

    ws = create("Merge Blocked WS", "d", root=ws_root)
    transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        blockers=[
            Blocker(kind="failing_check", name="Tests", detail="check failing: Tests"),
            Blocker(
                kind="review_decision",
                name="CHANGES_REQUESTED",
                detail="PR review decision: CHANGES_REQUESTED",
            ),
        ],
    )
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["park_reason"] == "merge_block"
    assert detail["blockers"] == [
        {
            "kind": "failing_check",
            "name": "Tests",
            "detail": "check failing: Tests",
            "instruction": None,
            "action_url": None,
        },
        {
            "kind": "review_decision",
            "name": "CHANGES_REQUESTED",
            "detail": "PR review decision: CHANGES_REQUESTED",
            "instruction": None,
            "action_url": None,
        },
    ]


def test_complete_oob_verified_advances(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.routes import workspaces as ws_routes

    monkeypatch.setattr(ws_routes, "verify_pr_merged", lambda _n: True)
    tasks = [
        Task(
            id="sub-a",
            status=TaskStatus.PENDING,
            dod_clauses=[TaskDoDClause(text="x")],
        )
    ]
    ws = create("OOB Verified WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 42},
    )
    assert res.status_code == 200
    data = res.json()
    sub_a = next(t for t in data["tasks"] if t["id"] == "sub-a")
    assert sub_a["status"] == "completed"
    assert sub_a["dod_clauses"][0]["evidence"] == "PR #42"
    assert data["status"] == "completed"


def test_complete_oob_force_completes(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.routes import workspaces as ws_routes

    calls: list[int] = []

    def _record_and_reject(n: int) -> bool:
        calls.append(n)
        return False

    monkeypatch.setattr(ws_routes, "verify_pr_merged", _record_and_reject)
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Force WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 7, "force": True},
    )
    assert res.status_code == 200
    assert calls == []
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.status == TaskStatus.COMPLETED


def test_complete_oob_unverified_no_force_422(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.routes import workspaces as ws_routes

    monkeypatch.setattr(ws_routes, "verify_pr_merged", lambda _n: False)
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Unverified WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 7},
    )
    assert res.status_code == 422
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.status == TaskStatus.PENDING


def test_complete_task_404_unknown_workspace(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces/ghost/tasks/sub-a/complete",
        json={"pr_number": 1, "force": True},
    )
    assert res.status_code == 404


def test_complete_task_404_unknown_subtask(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB 404 Sub WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/ghost-sub/complete",
        json={"pr_number": 1, "force": True},
    )
    assert res.status_code == 404


def test_complete_task_409_when_no_subtasks(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("OOB No Tasks WS", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/complete",
        json={"pr_number": 1, "force": True},
    )
    assert res.status_code == 409


def test_complete_task_409_when_archived(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Archived WS", "d", root=ws_root, tasks=tasks)
    transition(ws, Status.ARCHIVED)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 1, "force": True},
    )
    assert res.status_code == 409


def test_complete_task_409_when_already_terminal(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.COMPLETED)]
    ws = create("OOB Terminal WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 1, "force": True},
    )
    assert res.status_code == 409


def test_complete_task_422_for_non_positive_pr_number(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Bad PR WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 0, "force": True},
    )
    assert res.status_code == 422


def test_complete_task_writes_audit_row_with_force_path(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Audit WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 9, "force": True},
    )
    assert res.status_code == 200
    audit_file = logs_root / "oob_completion_audit.jsonl"
    entry = _json.loads(audit_file.read_text().splitlines()[0])
    assert entry["workspace_id"] == ws.task_id
    assert entry["task_id"] == "sub-a"
    assert entry["pr_number"] == 9
    assert entry["path"] == "force"


def test_complete_task_writes_audit_row_with_verified_path(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    from alan.routes import workspaces as ws_routes

    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    monkeypatch.setattr(ws_routes, "verify_pr_merged", lambda _n: True)
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Audit Verified WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 11},
    )
    assert res.status_code == 200
    audit_file = logs_root / "oob_completion_audit.jsonl"
    entry = _json.loads(audit_file.read_text().splitlines()[0])
    assert entry["path"] == "verified"


def test_complete_task_writes_audit_row_even_when_dispatcher_rejects(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json as _json

    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    tasks = [Task(id="sub-a", status=TaskStatus.COMPLETED)]
    ws = create("OOB Audit Rejected WS", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 13, "force": True},
    )
    assert res.status_code == 409
    audit_file = logs_root / "oob_completion_audit.jsonl"
    entry = _json.loads(audit_file.read_text().splitlines()[0])
    assert entry["workspace_id"] == ws.task_id
    assert entry["task_id"] == "sub-a"
    assert entry["pr_number"] == 13
    assert entry["path"] == "force"


def _control_headers() -> dict[str, str]:
    import os

    from alan import control_plane

    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _scenario_payload(scenario_id: str, when: str) -> dict[str, str]:
    return {
        "id": scenario_id,
        "description": "repaired",
        "given": "the ratchet is wired in",
        "when": when,
        "then": "the command exits 0",
    }


def _put_scenarios(
    client: TestClient,
    ws: Workspace,
    task_id: str,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.put(
            f"/api/workspaces/{ws.task_id}/tasks/{task_id}/scenarios",
            json=body,
            headers=_control_headers() if headers is None else headers,
        ),
    )


def _ws_with_broken_scenario(ws_root: Path, title: str) -> Workspace:
    from alan.workspace import TestScenario

    broken = TestScenario(
        id="timer_lint_rules_enforced",
        description="unsatisfiable by construction",
        given="the config is wired",
        when="npm --prefix alan/web run lint",
        then="vitest reports 18/18 passing",
    )
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING, scenarios=[broken])]
    return create(title, "d", root=ws_root, tasks=tasks)


def test_override_scenarios_replaces_the_stale_scenario(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Repair WS")
    repaired = _scenario_payload(
        "timer_lint_rules_enforced",
        "npm --prefix alan/web run lint && npx vitest run src/test/lintRules.test.ts",
    )
    res = _put_scenarios(client, ws, "sub-a", {"scenarios": [repaired]})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert [s.when for s in sub_a.scenarios] == [repaired["when"]]
    assert sub_a.scenarios_waived is False


def test_override_scenarios_waive_only_leaves_scenarios_intact(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Waive WS")
    res = _put_scenarios(client, ws, "sub-a", {"waived": True})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.scenarios_waived is True
    assert [s.id for s in sub_a.scenarios] == ["timer_lint_rules_enforced"]


def test_override_scenarios_accepts_both_fields_together(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Both WS")
    res = _put_scenarios(
        client,
        ws,
        "sub-a",
        {"scenarios": [_scenario_payload("repaired", "uv run pytest tests/x.py")], "waived": True},
    )
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert [s.id for s in sub_a.scenarios] == ["repaired"]
    assert sub_a.scenarios_waived is True


def test_override_scenarios_clears_scenarios_with_empty_list(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Clear WS")
    res = _put_scenarios(client, ws, "sub-a", {"scenarios": []})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.scenarios == []


def test_override_scenarios_requires_at_least_one_field(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Empty Body WS")
    res = _put_scenarios(client, ws, "sub-a", {})
    assert res.status_code == 422


def test_override_scenarios_without_control_token_returns_401(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario 401 WS")
    res = _put_scenarios(client, ws, "sub-a", {"waived": True}, headers={})
    assert res.status_code == 401


def test_override_scenarios_wrong_control_token_returns_403(
    client: TestClient, ws_root: Path
) -> None:
    from alan import control_plane

    ws = _ws_with_broken_scenario(ws_root, "Scenario 403 WS")
    res = _put_scenarios(
        client,
        ws,
        "sub-a",
        {"waived": True},
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_override_scenarios_404_unknown_workspace(client: TestClient) -> None:
    res = client.put(
        "/api/workspaces/ghost/tasks/sub-a/scenarios",
        json={"waived": True},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_override_scenarios_404_unknown_subtask(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario 404 Sub WS")
    res = _put_scenarios(client, ws, "ghost-sub", {"waived": True})
    assert res.status_code == 404


def test_override_scenarios_409_when_no_subtasks(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Scenario No Tasks WS", "d", root=ws_root)
    res = _put_scenarios(client, ws, ws.task_id, {"waived": True})
    assert res.status_code == 409


def test_override_scenarios_409_when_archived(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    ws = _ws_with_broken_scenario(ws_root, "Scenario Archived WS")
    transition(ws, Status.ARCHIVED)
    res = _put_scenarios(client, ws, "sub-a", {"waived": True})
    assert res.status_code == 409


def test_override_scenarios_writes_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _ws_with_broken_scenario(ws_root, "Scenario Audit WS")
    res = _put_scenarios(
        client,
        ws,
        "sub-a",
        {"scenarios": [_scenario_payload("repaired", "uv run pytest tests/x.py")]},
    )
    assert res.status_code == 200
    entry = json.loads(
        (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()[0]
    )
    assert entry["workspace_id"] == ws.task_id
    assert entry["task_id"] == "sub-a"
    assert entry["scenario_ids"] == ["repaired"]
    assert entry["waived"] is None


def test_override_scenarios_audit_records_null_ids_for_waive_only(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _ws_with_broken_scenario(ws_root, "Scenario Audit Waive WS")
    assert _put_scenarios(client, ws, "sub-a", {"waived": True}).status_code == 200
    entry = json.loads(
        (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()[0]
    )
    assert entry["scenario_ids"] is None
    assert entry["waived"] is True


def test_get_workspace_run_summary_none_when_usage_read_raises(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.routes._workspace_detail as workspace_detail_mod

    ws = create("Usage Boom WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text(
        json.dumps({"ok": True, "text": "done"}), encoding="utf-8"
    )

    def _boom(_ws_path: Path) -> None:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_detail_mod, "read_manager_summary_usage", _boom)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["manager_summary"]["run_summary"] is None


def test_create_workspace_publishes_workspace_created(
    client: TestClient, pubsub: PubSub
) -> None:
    kinds: list[str] = []
    pubsub.subscribe_all(lambda e: kinds.append(e.kind))
    res = client.post("/api/workspaces", json={"title": "Event Test", "description": "d"})
    assert res.status_code == 201
    assert "workspace_created" in kinds


def test_update_note_publishes_note_updated(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("Note Event WS", "d", root=ws_root)
    kinds: list[str] = []
    pubsub.subscribe_all(lambda e: kinds.append(e.kind))
    res = client.put(f"/api/workspaces/{ws.task_id}/note", json={"note": "hello"})
    assert res.status_code == 200
    assert kinds == ["note_updated"]


def test_create_workspace_with_team_dispatches_root_task_atomically(
    client: TestClient, queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Atomic Create Dispatch",
            "description": "d",
            "team": "research",
            "counter_case": _COUNTER_CASE_BODY,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    jobs = queue.list_jobs_for_workspace(task_id)
    assert len(jobs) == 1
    audit_file = logs_root / "counter_case_waiver_audit.jsonl"
    lines = audit_file.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_id"] == task_id
    assert entry["counter_case"] == _COUNTER_CASE_BODY


def test_create_workspace_with_team_no_counter_case_writes_no_waiver(
    client: TestClient, queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    res = client.post(
        "/api/workspaces",
        json={"title": "Atomic No Counter Case", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert len(queue.list_jobs_for_workspace(task_id)) == 1
    assert not (logs_root / "counter_case_waiver_audit.jsonl").exists()


def test_create_workspace_without_team_enqueues_no_job(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces", json={"title": "Two Call Contract WS", "description": "d"}
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert queue.list_jobs_for_workspace(task_id) == []


def test_create_workspace_team_with_nonempty_tasks_rejected_422(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Team Plus Tasks",
            "description": "d",
            "team": "research",
            "tasks": [{"id": "sub-a"}],
        },
    )
    assert res.status_code == 422
    assert list_workspaces(root=ws_root) == []


def test_create_workspace_unknown_team_rolls_back_no_orphan(
    client: TestClient, queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    kinds: list[str] = []
    pubsub.subscribe_all(lambda e: kinds.append(e.kind))
    res = client.post(
        "/api/workspaces",
        json={"title": "Bad Team Atomic", "description": "d", "team": "nonexistent-team"},
    )
    assert res.status_code == 400
    assert list_workspaces(root=ws_root) == []
    assert queue.list_jobs() == []
    assert "workspace_created" not in kinds


def test_create_workspace_internal_team_rolls_back_no_orphan(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Internal Team Atomic", "description": "d", "team": "triage"},
    )
    assert res.status_code == 400
    assert list_workspaces(root=ws_root) == []
    assert queue.list_jobs() == []


def test_create_workspace_dispatch_team_core_failure_rolls_back(
    client: TestClient, queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    def boom(**_kwargs: object) -> None:
        raise RuntimeError("enqueue exploded")

    monkeypatch.setattr(creation_mod, "dispatch_team_core", boom)
    with pytest.raises(RuntimeError):
        client.post(
            "/api/workspaces",
            json={"title": "Boom Atomic", "description": "d", "team": "research"},
        )
    assert list_workspaces(root=ws_root) == []
    assert queue.list_jobs() == []


def test_create_workspace_rollback_delete_failure_preserves_original_error(
    client: TestClient,
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import alan.workspace as workspace_mod
    from alan.app import workspace_creation as creation_mod

    def boom_dispatch(**_kwargs: object) -> None:
        raise RuntimeError("enqueue exploded")

    def boom_delete(*_a: object, **_kw: object) -> None:
        raise OSError("delete also exploded")

    monkeypatch.setattr(creation_mod, "dispatch_team_core", boom_dispatch)
    monkeypatch.setattr(workspace_mod, "delete", boom_delete)
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="enqueue exploded"):
            client.post(
                "/api/workspaces",
                json={"title": "Boom Delete Atomic", "description": "d", "team": "research"},
            )
    assert "rollback delete failed" in caplog.text


def test_create_workspace_scan_predispatch_runs_once_on_team_success(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    calls: list[int] = []

    def counting(*_a: object, **_kw: object) -> list[object]:
        calls.append(1)
        return []

    monkeypatch.setattr(creation_mod, "scan_predispatch", counting)
    res = client.post(
        "/api/workspaces",
        json={"title": "Scan Once Success", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    assert len(calls) == 1


def test_create_workspace_scan_predispatch_runs_once_on_rollback(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.app import workspace_creation as creation_mod

    calls: list[int] = []

    def counting(*_a: object, **_kw: object) -> list[object]:
        calls.append(1)
        return []

    monkeypatch.setattr(creation_mod, "scan_predispatch", counting)
    res = client.post(
        "/api/workspaces",
        json={"title": "Scan Once Rollback", "description": "d", "team": "nonexistent-team"},
    )
    assert res.status_code == 400
    assert len(calls) == 1


def test_create_workspace_omitting_team_matches_legacy_two_call_response_shape(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces", json={"title": "Legacy Shape WS", "description": "d"}
    )
    assert res.status_code == 201
    assert set(res.json().keys()) == {"task_id", "root_task_ids"}


def test_create_workspace_with_team_archives_build_prep_chat(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Archived Build WS",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert not (bc_root / session.session_id).exists()
    assert (ws.path / "prep-chat" / session.session_id / "session.json").is_file()

    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["prep_chat"] == {
        "session_id": session.session_id,
        "kind": "build",
        "consultant_key": None,
    }


def test_create_workspace_with_team_archives_explore_prep_chat(
    client: TestClient, ws_root: Path, ec_root: Path
) -> None:
    session = explore_chat_mod.create_session("architecture", root=ec_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Archived Explore WS",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "explore",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert not (ec_root / session.session_id).exists()

    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["prep_chat"] == {
        "session_id": session.session_id,
        "kind": "explore",
        "consultant_key": "architecture",
    }


def test_create_workspace_without_prep_chat_fields_leaves_none(
    client: TestClient, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "No Prep Chat WS", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    detail = client.get(f"/api/workspaces/{res.json()['task_id']}").json()
    assert detail["prep_chat"] is None


def test_create_workspace_prep_chat_kind_without_session_id_422(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Partial Prep Chat Ref",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
        },
    )
    assert res.status_code == 422


def test_create_workspace_prep_chat_session_id_without_kind_422(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Partial Prep Chat Ref",
            "description": "d",
            "team": "research",
            "prep_chat_session_id": "12345678-1234-1234-1234-1234567890ab",
        },
    )
    assert res.status_code == 422


def test_create_workspace_rollback_leaves_prep_chat_draft_intact(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Rollback Prep Chat WS",
            "description": "d",
            "team": "nonexistent-team",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 400
    assert list_workspaces(root=ws_root) == []
    assert (bc_root / session.session_id / "session.json").is_file()
    loaded = build_chat_mod.load_session(session.session_id, root=bc_root)
    assert loaded.session_id == session.session_id


def test_create_workspace_without_team_archives_prep_chat(
    client: TestClient, ws_root: Path, bc_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.app.workspace_creation.scan_predispatch", lambda *a, **kw: [])
    session = build_chat_mod.create_session(root=bc_root)
    res = client.post(
        "/api/workspaces",
        json={
            "title": "No Team Prep Chat WS",
            "description": "d",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    ws = read(task_id, root=ws_root)
    assert not (bc_root / session.session_id).exists()
    assert (ws.path / "prep-chat" / session.session_id / "session.json").is_file()


def test_create_workspace_archive_failure_logged_dispatch_still_succeeds(
    client: TestClient,
    ws_root: Path,
    bc_root: Path,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = build_chat_mod.create_session(root=bc_root)

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("move exploded")

    monkeypatch.setattr(prep_chat_mod, "archive", boom)
    with caplog.at_level("WARNING"):
        res = client.post(
            "/api/workspaces",
            json={
                "title": "Archive Boom WS",
                "description": "d",
                "team": "research",
                "prep_chat_kind": "build",
                "prep_chat_session_id": session.session_id,
            },
        )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    assert len(queue.list_jobs_for_workspace(task_id)) == 1
    assert "prep-chat archive failed" in caplog.text
    assert (bc_root / session.session_id / "session.json").is_file()
    detail = client.get(f"/api/workspaces/{task_id}").json()
    assert detail["prep_chat"] is None


def test_create_workspace_archive_failure_log_strips_newlines_from_session_id(
    client: TestClient,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    malicious_id = "abc\r\ndef\nFAKE injected log line"

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("move exploded")

    monkeypatch.setattr(prep_chat_mod, "archive", boom)
    with caplog.at_level("WARNING"):
        res = client.post(
            "/api/workspaces",
            json={
                "title": "Archive Boom Injection WS",
                "description": "d",
                "team": "research",
                "prep_chat_kind": "build",
                "prep_chat_session_id": malicious_id,
            },
        )
    assert res.status_code == 201
    assert len(queue.list_jobs_for_workspace(res.json()["task_id"])) == 1
    assert malicious_id not in caplog.text
    assert "abcdefFAKE injected log line" in caplog.text


def test_get_workspace_prep_chat_returns_transcript(
    client: TestClient, ws_root: Path, bc_root: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    build_chat_mod.append_user_message_and_mark_started(
        session,
        ChatMessage(role="user", content="hello there", ts=datetime.now(UTC)),
        root=bc_root,
    )
    res = client.post(
        "/api/workspaces",
        json={
            "title": "Transcript WS",
            "description": "d",
            "team": "research",
            "prep_chat_kind": "build",
            "prep_chat_session_id": session.session_id,
        },
    )
    task_id = res.json()["task_id"]

    transcript = client.get(f"/api/workspaces/{task_id}/prep-chat")
    assert transcript.status_code == 200
    body = transcript.json()
    assert body["session_id"] == session.session_id
    assert body["kind"] == "build"
    assert body["consultant_key"] is None
    assert body["title"] == "Transcript WS"
    assert [m["content"] for m in body["messages"]] == ["hello there"]


def test_get_workspace_prep_chat_404_when_no_archive(client: TestClient, ws_root: Path) -> None:
    ws = create("No Archive WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 404


def test_get_workspace_prep_chat_404_when_marker_missing(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Broken Archive WS", "d", root=ws_root)
    sid = "12345678-1234-1234-1234-1234567890ab"
    (ws.path / "prep-chat" / sid).mkdir(parents=True)
    res = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    assert res.status_code == 404


def test_get_workspace_prep_chat_404_when_workspace_missing(client: TestClient) -> None:
    res = client.get("/api/workspaces/does-not-exist/prep-chat")
    assert res.status_code == 404


def test_list_workspaces_engine_key_null_when_unset(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Engine List WS", "d", root=ws_root)
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["engine_key"] is None


def test_list_workspaces_includes_engine_key(client: TestClient, ws_root: Path) -> None:
    ws = create("Engine List WS", "d", root=ws_root, engine_key="acme/widget")
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["engine_key"] == "acme/widget"


def test_list_workspaces_archived_bucket_carries_engine_key(
    client: TestClient, ws_root: Path
) -> None:
    from alan.workspace import Status, transition

    ws = create("Archived Engine List WS", "d", root=ws_root, engine_key="acme/widget")
    transition(ws, Status.ARCHIVED)

    listing = client.get("/api/workspaces", params={"bucket": "archived"}).json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["engine_key"] == "acme/widget"


def test_get_workspace_engine_key_null_when_unset(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Engine Detail WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["engine_key"] is None
    assert detail["project_id"] is None


def test_get_workspace_includes_engine_key_and_project_id(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Engine Detail WS", "d", root=ws_root, engine_key="acme/widget", project_id="proj-1"
    )
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["engine_key"] == "acme/widget"
    assert detail["project_id"] == "proj-1"


def _write_result_stream(path: Path, *, cost: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


_SESSION_A = "11111111-1111-1111-1111-111111111111"


def test_get_workspace_returns_null_cost_and_empty_breakdown_when_no_spend(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Spend WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["total_cost_usd"] is None
    assert [e["key"] for e in detail["cost_breakdown"]] == [
        "team_runs",
        "status_summary",
        "discussion",
        "collision_check",
        "investigator",
        "preparation",
    ]
    assert all(e["run_summary"] is None for e in detail["cost_breakdown"])


def test_get_workspace_cost_breakdown_reflects_discussion_and_investigator_spend(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Breakdown WS", "d", root=ws_root)
    _write_result_stream(ws.path / "chat" / _SESSION_A / "stream.jsonl", cost=0.03)
    _write_result_stream(ws.path / "chat" / _SESSION_A / "collision_stream.jsonl", cost=0.04)
    _write_result_stream(ws.path / "investigator_stream.jsonl", cost=0.05)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["total_cost_usd"] == pytest.approx(0.12)

    by_key = {e["key"]: e for e in detail["cost_breakdown"]}
    assert by_key["discussion"]["run_summary"]["total_cost_usd"] == pytest.approx(0.03)
    assert by_key["collision_check"]["run_summary"]["total_cost_usd"] == pytest.approx(0.04)
    assert by_key["investigator"]["run_summary"]["total_cost_usd"] == pytest.approx(0.05)
    assert by_key["team_runs"]["run_summary"] is None


def test_get_workspace_cost_breakdown_reflects_preparation_spend(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Preparation Breakdown WS", "d", root=ws_root)
    _write_result_stream(ws.path / "prep-chat" / _SESSION_A / "stream.jsonl", cost=0.06)
    _write_result_stream(ws.path / "prep-chat" / _SESSION_A / "collision_stream.jsonl", cost=0.01)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["total_cost_usd"] == pytest.approx(0.07)

    by_key = {e["key"]: e for e in detail["cost_breakdown"]}
    assert by_key["preparation"]["run_summary"]["total_cost_usd"] == pytest.approx(0.07)
    assert by_key["discussion"]["run_summary"] is None
    assert by_key["collision_check"]["run_summary"] is None


def test_get_workspace_total_cost_matches_list_total_cost_no_drift(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Drift WS", "d", root=ws_root)
    _write_result_stream(ws.path / "manager_summary_stream.jsonl", cost=0.01)
    _write_result_stream(ws.path / "chat" / _SESSION_A / "stream.jsonl", cost=0.02)
    _write_result_stream(ws.path / "chat" / _SESSION_A / "collision_stream.jsonl", cost=0.03)
    _write_result_stream(ws.path / "investigator_stream.jsonl", cost=0.04)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)

    assert detail["total_cost_usd"] == pytest.approx(0.10)
    assert detail["total_cost_usd"] == found["total_cost_usd"]


def test_get_workspace_multiple_chat_sessions_all_contribute(
    client: TestClient, ws_root: Path
) -> None:
    session_b = "22222222-2222-2222-2222-222222222222"
    ws = create("Multi Session Detail WS", "d", root=ws_root)
    _write_result_stream(ws.path / "chat" / _SESSION_A / "stream.jsonl", cost=0.01)
    _write_result_stream(ws.path / "chat" / session_b / "stream.jsonl", cost=0.02)

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    by_key = {e["key"]: e for e in detail["cost_breakdown"]}
    assert by_key["discussion"]["run_summary"]["total_cost_usd"] == pytest.approx(0.03)


def _write_node_workflow(
    ws: Workspace,
    *,
    inv_id: str,
    node_started: datetime,
    node_completed: datetime | None,
    doc_completed: datetime | None,
    status: str = "success",
) -> None:
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status=status,  # type: ignore[arg-type]
        started_at=node_started,
        completed_at=doc_completed,
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="success" if node_completed is not None else "running",
                started_at=node_started,
                completed_at=node_completed,
            )
        ],
    )
    save_workflow(ws, doc)


def test_list_and_detail_active_seconds_null_when_no_work(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Fleet Work WS", "d", root=ws_root)
    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert found["active_seconds"] is None
    assert found["lead_seconds"] is None
    assert detail["active_seconds"] is None
    assert detail["lead_seconds"] is None


def test_list_and_detail_active_seconds_reflect_closed_node(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Closed Node WS", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC) - timedelta(seconds=120)
    completed = started + timedelta(seconds=50)
    _write_node_workflow(
        ws, inv_id=inv_id, node_started=started, node_completed=completed, doc_completed=completed
    )

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert found["active_seconds"] == pytest.approx(50.0)
    assert detail["active_seconds"] == pytest.approx(50.0)
    assert found["active_seconds"] == detail["active_seconds"]


def test_list_active_seconds_advances_for_open_node(client: TestClient, ws_root: Path) -> None:
    ws = create("Open Node WS", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC) - timedelta(seconds=10)
    _write_node_workflow(
        ws,
        inv_id=inv_id,
        node_started=started,
        node_completed=None,
        doc_completed=None,
        status="running",
    )

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    assert found["active_seconds"] is not None
    assert found["active_seconds"] >= 10.0


def test_get_workspace_lead_seconds_null_when_not_delivered(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Not Delivered WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["lead_seconds"] is None


def test_list_and_detail_lead_seconds_reflect_delivered_at(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Delivered Lead WS", "d", root=ws_root)
    ws = mark_delivered(ws, settled_active_seconds=42.0)
    assert ws.meta.delivered_at is not None

    listing = client.get("/api/workspaces").json()
    found = next(w for w in listing if w["task_id"] == ws.task_id)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()

    expected = (ws.meta.delivered_at - ws.meta.created_at).total_seconds()
    assert found["lead_seconds"] == pytest.approx(expected)
    assert detail["lead_seconds"] == pytest.approx(expected)
    assert found["active_seconds"] == pytest.approx(42.0)
    assert detail["active_seconds"] == pytest.approx(42.0)


def test_active_seconds_never_stretches_after_delivery(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Settled No Stretch WS", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    started = datetime.now(UTC) - timedelta(seconds=60)
    completed = started + timedelta(seconds=20)
    _write_node_workflow(
        ws, inv_id=inv_id, node_started=started, node_completed=completed, doc_completed=completed
    )
    ws = mark_delivered(ws, settled_active_seconds=20.0)

    later_start = completed
    later_end = completed + timedelta(seconds=500)
    _write_node_workflow(
        ws,
        inv_id=create_invocation_dir(ws),
        node_started=later_start,
        node_completed=later_end,
        doc_completed=later_end,
    )

    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["active_seconds"] == pytest.approx(20.0)


def test_dispatch_task_team_with_prior_handover_records_redispatch_intervention(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("Redispatch Intervention", "d", root=ws_root, tasks=tasks)
    save_handover(ws, _make_handover(ws.task_id, "prior-h1"))

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.REDISPATCH]


def test_dispatch_task_team_without_prior_work_records_no_intervention(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    ws = create("First Dispatch No Intervention", "d", root=ws_root)

    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_create_workspace_with_team_records_no_intervention(
    client: TestClient, queue: Queue, ws_root: Path
) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Create Dispatch No Intervention", "description": "d", "team": "research"},
    )
    assert res.status_code == 201
    reloaded = read(res.json()["task_id"], root=ws_root)
    assert reloaded.meta.interventions == []


def test_complete_oob_force_records_force_complete_intervention(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Force Intervention", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 7, "force": True},
    )
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.FORCE_COMPLETE]


def test_complete_oob_verified_records_force_complete_intervention(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.routes import workspaces as ws_routes

    monkeypatch.setattr(ws_routes, "verify_pr_merged", lambda _n: True)
    tasks = [Task(id="sub-a", status=TaskStatus.PENDING)]
    ws = create("OOB Verified Intervention", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 42},
    )
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.FORCE_COMPLETE]


def test_complete_task_dispatcher_reject_records_no_intervention(
    client: TestClient, ws_root: Path
) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.COMPLETED)]
    ws = create("OOB Rejected No Intervention", "d", root=ws_root, tasks=tasks)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/sub-a/complete",
        json={"pr_number": 13, "force": True},
    )
    assert res.status_code == 409
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_override_scenarios_records_scenario_override_intervention(
    client: TestClient, ws_root: Path
) -> None:
    ws = _ws_with_broken_scenario(ws_root, "Scenario Intervention WS")
    res = _put_scenarios(client, ws, "sub-a", {"waived": True})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.scenarios_waived is True
    assert [i.action for i in reloaded.meta.interventions] == [
        InterventionAction.SCENARIO_OVERRIDE
    ]
