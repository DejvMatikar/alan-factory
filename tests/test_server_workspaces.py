from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import reconciler as _reconciler_mod
from alan import server
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dispatcher import PubSub
from alan.handover import Handover, OpenQuestion, save_handover
from alan.lifecycle import ParkReason
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workflow import (
    NodeUsage,
    WorkflowDoc,
    WorkflowGraph,
    create_invocation_dir,
    save_workflow,
    workflow_path,
)
from alan.workspace import Status, Workspace, create

_COUNTER_CASE = {
    "against": "This locks in scope before the shop has bandwidth to review it.",
    "cheapest_80": "Ship the smallest read-only slice first; defer the rest.",
    "cost_estimate": "insufficient data",
}


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
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _write_workflow(ws: Workspace, *, team: str, status: str, inv_id: str | None = None) -> str:
    invocation_id = inv_id if inv_id is not None else create_invocation_dir(ws)
    doc = WorkflowDoc(
        id=invocation_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team=team,
        status=status,  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
    )
    save_workflow(ws, doc)
    return invocation_id


def _save_handover_with_question(
    ws: Workspace, qid: str, *, blocking: bool = True, resolver: str = "human"
) -> Handover:
    h = Handover(
        handover_id=f"handover_{ws.task_id}_one",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        open_questions=[
            OpenQuestion(
                id=qid,
                question="which one?",
                resolver_role=resolver,
                blocking=blocking,
                prior_attempts=[],
            )
        ],
        full_synthesis="needs answer",
    )
    save_handover(ws, h)
    return h


def test_create_workspace_returns_task_id(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "My Task", "description": "do it"},
    )
    assert res.status_code == 201
    assert res.json() == {"task_id": "my-task", "root_task_ids": ["my-task"]}


def test_create_workspace_rejects_blank_title(client: TestClient) -> None:
    res = client.post("/api/workspaces", json={"title": "", "description": "d"})
    assert res.status_code == 422


def test_create_workspace_ignores_auto_dispatch_field(client: TestClient) -> None:
    res = client.post(
        "/api/workspaces",
        json={"title": "Legacy Auto", "description": "d", "auto_dispatch": True},
    )
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    detail = client.get(f"/api/workspaces/{task_id}")
    assert "auto_dispatch" not in detail.json()


def test_list_workspaces_empty(client: TestClient) -> None:
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    assert res.json() == []


def test_list_workspaces_returns_summaries(client: TestClient, ws_root: Path) -> None:
    create("Alpha", "a", root=ws_root)
    create("Bravo", "b", root=ws_root)
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    items = res.json()
    assert [w["task_id"] for w in items] == ["alpha", "bravo"]
    assert all("status" in w for w in items)
    assert all("created_at" in w for w in items)
    assert all("team_runs_total" in w for w in items)
    assert all(w["team_runs_total"] == 0 for w in items)
    assert all("invocations_total" in w for w in items)
    assert all(w["invocations_total"] == 0 for w in items)


def test_list_workspaces_invocations_total_counts_all_statuses(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    create("Active WS", "a", root=ws_root)
    create("Idle WS", "b", root=ws_root)
    pending = queue.enqueue("active-ws", "research")
    queue.enqueue("active-ws", "architecture")
    queue.claim()
    queue.complete(pending.id)
    items = client.get("/api/workspaces").json()
    by_id = {w["task_id"]: w for w in items}
    assert by_id["active-ws"]["invocations_total"] == 2
    assert by_id["idle-ws"]["invocations_total"] == 0


def test_list_workspaces_invocations_total_counts_decomposed_subtask_jobs(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Decomposed WS", "a", root=ws_root)
    queue.enqueue("inj-subtask-1", "implementation", workspace_id=ws.task_id)
    queue.enqueue("inj-subtask-2", "verification", workspace_id=ws.task_id)
    items = client.get("/api/workspaces").json()
    by_id = {w["task_id"]: w for w in items}
    assert by_id[ws.task_id]["invocations_total"] == 2


def test_list_workspaces_includes_latest_team_and_status(client: TestClient, ws_root: Path) -> None:
    ws = create("With Invocation", "i", root=ws_root)
    _write_workflow(ws, team="research", status="running")
    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == ws.task_id)
    assert row["latest_team"] == "research"
    assert row["latest_invocation_status"] == "running"


def test_list_workspaces_latest_invocation_uses_last_lexicographic(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Two Invocations", "t", root=ws_root)
    _write_workflow(ws, team="research", status="success")
    _write_workflow(ws, team="implementation", status="failed")
    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == ws.task_id)
    assert row["latest_team"] == "implementation"
    assert row["latest_invocation_status"] == "failed"


def test_list_workspaces_no_invocations_returns_null_team_and_status(
    client: TestClient, ws_root: Path
) -> None:
    create("Backlog", "b", root=ws_root)
    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == "backlog")
    assert row["latest_team"] is None
    assert row["latest_invocation_status"] is None


def test_list_workspaces_corrupt_workflow_yaml_returns_null(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Corrupt", "c", root=ws_root)
    inv_id = create_invocation_dir(ws)
    workflow_path(ws, inv_id).write_text(": not valid yaml :", encoding="utf-8")
    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == ws.task_id)
    assert row["latest_team"] is None
    assert row["latest_invocation_status"] is None


def test_list_workspaces_total_cost_usd_null_when_no_invocations(
    client: TestClient, ws_root: Path
) -> None:
    create("No Cost", "d", root=ws_root)
    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == "no-cost")
    assert row["total_cost_usd"] is None


def test_list_workspaces_total_cost_usd_sums_across_invocations(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Cost Sum", "d", root=ws_root)

    inv1 = create_invocation_dir(ws)
    g1 = WorkflowGraph(task_id=ws.task_id, inv_id=inv1, team="research")
    g1.start_node("researcher", role="researcher", model="claude-sonnet-5")
    g1.complete_node("researcher", usage=NodeUsage(total_cost_usd=0.001))
    g1.finalize(status="success")
    save_workflow(ws, g1.doc)

    inv2 = create_invocation_dir(ws)
    g2 = WorkflowGraph(task_id=ws.task_id, inv_id=inv2, team="implementation")
    g2.start_node("implementer", role="implementer", model="claude-sonnet-5")
    g2.complete_node("implementer", usage=NodeUsage(total_cost_usd=0.002))
    g2.finalize(status="success")
    save_workflow(ws, g2.doc)

    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == ws.task_id)
    assert row["total_cost_usd"] == pytest.approx(0.003)


def test_list_workspaces_total_cost_usd_skips_null_task_costs(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Partial Cost", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("researcher", role="researcher", model="claude-sonnet-5")
    g.complete_node("researcher", usage=NodeUsage(total_cost_usd=0.005))
    g.start_node("writer", role="writer", model="claude-sonnet-5", depends_on=["researcher"])
    g.complete_node("writer", usage=NodeUsage(total_cost_usd=None))
    g.finalize(status="success")
    save_workflow(ws, g.doc)

    items = client.get("/api/workspaces").json()
    row = next(w for w in items if w["task_id"] == ws.task_id)
    assert row["total_cost_usd"] == pytest.approx(0.005)


def _write_manager_summary_usage(
    ws: Workspace, *, cost: float, input_tokens: int, output_tokens: int
) -> None:
    event = {
        "type": "result",
        "total_cost_usd": cost,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    stream = ws.path / "manager_summary_stream.jsonl"
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def test_list_workspaces_total_cost_usd_folds_in_manager_summary_spend(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Cost Fold", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("researcher", role="researcher", model="claude-sonnet-5")
    g.complete_node("researcher", usage=NodeUsage(total_cost_usd=0.001))
    g.finalize(status="success")
    save_workflow(ws, g.doc)

    before_row = next(w for w in client.get("/api/workspaces").json() if w["task_id"] == ws.task_id)
    assert before_row["total_cost_usd"] == pytest.approx(0.001)

    _write_manager_summary_usage(ws, cost=0.0005, input_tokens=10, output_tokens=5)

    after_row = next(w for w in client.get("/api/workspaces").json() if w["task_id"] == ws.task_id)
    assert after_row["total_cost_usd"] == pytest.approx(0.0015)
    assert after_row["total_cost_usd"] > before_row["total_cost_usd"]


def test_list_workspaces_total_cost_usd_manager_summary_only(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Manager Only Cost", "d", root=ws_root)
    _write_manager_summary_usage(ws, cost=0.002, input_tokens=20, output_tokens=10)

    row = next(w for w in client.get("/api/workspaces").json() if w["task_id"] == ws.task_id)
    assert row["total_cost_usd"] == pytest.approx(0.002)


def test_list_workspaces_total_cost_usd_unaffected_by_garbage_manager_stream(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Garbage Manager Stream", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("researcher", role="researcher", model="claude-sonnet-5")
    g.complete_node("researcher", usage=NodeUsage(total_cost_usd=0.001))
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    (ws.path / "manager_summary_stream.jsonl").write_text("not json{{{\n", encoding="utf-8")

    row = next(w for w in client.get("/api/workspaces").json() if w["task_id"] == ws.task_id)
    assert row["total_cost_usd"] == pytest.approx(0.001)


def test_get_workspace_detail_with_handover(client: TestClient, ws_root: Path) -> None:
    ws = create("Detail Test", "d", root=ws_root)
    _save_handover_with_question(ws, "q1")
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["task_id"] == ws.task_id
    assert body["status"] == "active"
    assert body["latest_handover"]["handover_id"].endswith("_one")


def test_get_workspace_detail_without_handovers(client: TestClient, ws_root: Path) -> None:
    create("No Handovers", "x", root=ws_root)
    res = client.get("/api/workspaces/no-handovers")
    assert res.status_code == 200
    body = res.json()
    assert body["latest_handover"] is None
    assert body["history"] == []


def test_get_workspace_returns_404_when_missing(client: TestClient) -> None:
    res = client.get("/api/workspaces/ghost")
    assert res.status_code == 404


def test_list_workspaces_excludes_triage_by_default(client: TestClient, ws_root: Path) -> None:
    triage_ws = create("Triage Sweep", "t", root=ws_root, team="triage")
    normal_ws = create("Normal Task", "n", root=ws_root)
    default_ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    all_ids = [w["task_id"] for w in client.get("/api/workspaces?include=triage").json()]
    assert triage_ws.task_id not in default_ids
    assert normal_ws.task_id in default_ids
    assert triage_ws.task_id in all_ids
    assert normal_ws.task_id in all_ids


def test_get_workspace_returns_404_for_triage_without_include(
    client: TestClient, ws_root: Path
) -> None:
    triage_ws = create("Triage Sweep", "t", root=ws_root, team="triage")
    assert client.get(f"/api/workspaces/{triage_ws.task_id}").status_code == 404
    assert client.get(f"/api/workspaces/{triage_ws.task_id}?include=triage").status_code == 200


def test_list_workspaces_excludes_dependency_sweep_by_default(
    client: TestClient, ws_root: Path
) -> None:
    dep_ws = create("Dependency Sweep", "d", root=ws_root, team="dependency_sweep")
    normal_ws = create("Normal Task", "n", root=ws_root)
    default_ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    all_ids = [w["task_id"] for w in client.get("/api/workspaces?include=triage").json()]
    assert dep_ws.task_id not in default_ids
    assert normal_ws.task_id in default_ids
    assert dep_ws.task_id in all_ids
    assert normal_ws.task_id in all_ids


def test_get_workspace_returns_404_for_dependency_sweep_without_include(
    client: TestClient, ws_root: Path
) -> None:
    dep_ws = create("Dependency Sweep", "d", root=ws_root, team="dependency_sweep")
    assert client.get(f"/api/workspaces/{dep_ws.task_id}").status_code == 404
    assert client.get(f"/api/workspaces/{dep_ws.task_id}?include=triage").status_code == 200


def test_is_hidden_by_default_triage_always_hidden() -> None:
    from alan.app.workspace_creation import is_hidden_by_default

    assert is_hidden_by_default("triage", "Failure Triage Sweep") is True
    assert is_hidden_by_default("triage", "any title") is True


def test_is_hidden_by_default_dep_sweep_parent_hidden() -> None:
    from alan.app.workspace_creation import is_hidden_by_default
    from alan.reconciler import DEP_SWEEP_TITLE

    assert is_hidden_by_default("dependency_sweep", DEP_SWEEP_TITLE) is True


def test_is_hidden_by_default_dep_sweep_child_visible() -> None:
    from alan.app.workspace_creation import is_hidden_by_default

    assert is_hidden_by_default("dependency_sweep", "dep/123") is False
    assert is_hidden_by_default("dependency_sweep", "dep/42") is False


def test_is_hidden_by_default_normal_workspace_visible() -> None:
    from alan.app.workspace_creation import is_hidden_by_default

    assert is_hidden_by_default(None, "My Feature") is False
    assert is_hidden_by_default("implementation", "My Feature") is False


def test_list_workspaces_dep_child_visible_parent_hidden(client: TestClient, ws_root: Path) -> None:
    from alan.reconciler import DEP_SWEEP_TITLE

    parent_ws = create(DEP_SWEEP_TITLE, "d", root=ws_root, team="dependency_sweep")
    child_ws = create("dep/99", "Dependabot PR #99", root=ws_root, team="dependency_sweep")
    normal_ws = create("Normal Task", "n", root=ws_root)

    default_ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    assert parent_ws.task_id not in default_ids
    assert child_ws.task_id in default_ids
    assert normal_ws.task_id in default_ids

    all_ids = [w["task_id"] for w in client.get("/api/workspaces?include=triage").json()]
    assert parent_ws.task_id in all_ids
    assert child_ws.task_id in all_ids


def test_get_workspace_dep_child_visible_parent_hidden(client: TestClient, ws_root: Path) -> None:
    from alan.reconciler import DEP_SWEEP_TITLE

    parent_ws = create(DEP_SWEEP_TITLE, "d", root=ws_root, team="dependency_sweep")
    child_ws = create("dep/7", "Dependabot PR #7", root=ws_root, team="dependency_sweep")

    assert client.get(f"/api/workspaces/{child_ws.task_id}").status_code == 200
    assert client.get(f"/api/workspaces/{parent_ws.task_id}").status_code == 404
    assert client.get(f"/api/workspaces/{parent_ws.task_id}?include=triage").status_code == 200


def test_list_workspaces_summary_includes_team_field(client: TestClient, ws_root: Path) -> None:
    create("dep/5", "Dependabot PR #5", root=ws_root, team="dependency_sweep")
    create("Normal Task", "n", root=ws_root)

    rows = client.get("/api/workspaces").json()
    dep_row = next(r for r in rows if r["title"] == "dep/5")
    normal_row = next(r for r in rows if r["title"] == "Normal Task")

    assert dep_row["team"] == "dependency_sweep"
    assert normal_row["team"] is None


def test_dispatch_team_enqueues_job(client: TestClient, ws_root: Path, queue: Queue) -> None:
    create("Dispatch", "d", root=ws_root)
    res = client.post("/api/workspaces/dispatch/dispatch", json={"team": "research"})
    assert res.status_code == 202
    payload = res.json()
    assert "job_id" in payload
    assert payload["invocation_id"] == "inv_001"
    jobs = queue.list_jobs(task_id="dispatch")
    assert len(jobs) == 1
    assert jobs[0].team == "research"
    assert jobs[0].triggered_by_invocation_id == "inv_001"
    workflow_path = (
        ws_root / "dispatch" / "tasks" / "dispatch" / "invocations" / "inv_001" / "workflow.yaml"
    )
    assert workflow_path.is_file()


def test_dispatch_unknown_team_returns_400(client: TestClient, ws_root: Path) -> None:
    create("Unknown Team", "d", root=ws_root)
    res = client.post("/api/workspaces/unknown-team/dispatch", json={"team": "ghost"})
    assert res.status_code == 400
    assert "ghost" in res.json()["detail"]


def test_dispatch_decomposer_returns_400(client: TestClient, ws_root: Path) -> None:
    create("Decomposer WS", "d", root=ws_root)
    res = client.post("/api/workspaces/decomposer-ws/dispatch", json={"team": "decomposer"})
    assert res.status_code == 400
    assert "internal" in res.json()["detail"]
    assert "decomposer" in res.json()["detail"]


def test_dispatch_with_instructions_persisted_on_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    create("Instructions WS", "d", root=ws_root)
    res = client.post(
        "/api/workspaces/instructions-ws/dispatch",
        json={
            "team": "research",
            "instructions": "Focus narrowly on alan/foo.py line 42.",
        },
    )
    assert res.status_code == 202
    jobs = queue.list_jobs(task_id="instructions-ws")
    assert len(jobs) == 1
    assert jobs[0].instructions == "Focus narrowly on alan/foo.py line 42."


def test_dispatch_with_blank_instructions_normalised_to_none(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    create("Blank Instr WS", "d", root=ws_root)
    res = client.post(
        "/api/workspaces/blank-instr-ws/dispatch",
        json={"team": "research", "instructions": "   \n  "},
    )
    assert res.status_code == 202
    jobs = queue.list_jobs(task_id="blank-instr-ws")
    assert jobs[0].instructions is None


def test_get_teams_returns_catalog(client: TestClient) -> None:
    res = client.get("/api/teams")
    assert res.status_code == 200
    body = res.json()
    by_name = {t["name"]: t for t in body["teams"]}
    assert by_name["decomposer"]["manually_dispatchable"] is False
    assert by_name["research"]["manually_dispatchable"] is True
    assert by_name["research"]["initial_dispatchable"] is True
    assert by_name["implementation_fix"]["manually_dispatchable"] is True
    assert by_name["implementation_fix"]["initial_dispatchable"] is False
    assert by_name["pr_merge"]["manually_dispatchable"] is True
    assert by_name["pr_merge"]["initial_dispatchable"] is False
    assert by_name["research"]["role_summary"]


def test_dispatch_returns_404_for_missing_workspace(client: TestClient) -> None:
    res = client.post("/api/workspaces/nope/dispatch", json={"team": "research"})
    assert res.status_code == 404


def test_dispatch_attaches_latest_handover_as_parent(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Dispatch Prior", "d", root=ws_root)
    prior = _save_handover_with_question(ws, "q1", resolver="human")
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "implementation"})
    assert res.status_code == 202
    jobs = queue.list_jobs(task_id=ws.task_id)
    assert len(jobs) == 1
    assert jobs[0].parent_handover_id == prior.handover_id


def test_get_workspace_handovers_returns_ordered_list(client: TestClient, ws_root: Path) -> None:
    ws = create("Handover Order", "h", root=ws_root)
    _save_handover_with_question(ws, "q1")
    _save_handover_with_question(ws, "q2")
    res = client.get(f"/api/workspaces/{ws.task_id}/handovers")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    assert items[0]["open_questions"][0]["id"] == "q1"
    assert items[1]["open_questions"][0]["id"] == "q2"


def test_get_workspace_handovers_returns_404_for_unknown(
    client: TestClient,
) -> None:
    res = client.get("/api/workspaces/ghost/handovers")
    assert res.status_code == 404


def test_get_workspace_handovers_returns_empty_list_for_no_handovers(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Handovers WS", "n", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}/handovers")
    assert res.status_code == 200
    assert res.json() == []


def test_events_stream_returns_404_for_missing_workspace(
    client: TestClient,
) -> None:
    res = client.get("/api/workspaces/ghost/events")
    assert res.status_code == 404


def test_delete_workspace_returns_204_when_no_jobs(client: TestClient, ws_root: Path) -> None:
    ws = create("Doomed", "d", root=ws_root)
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert res.content == b""
    assert not ws.path.exists()


def test_delete_workspace_returns_204_with_completed_and_failed_jobs(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Housekeeping", "d", root=ws_root)
    done = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.complete(done.id)
    bad = queue.enqueue(ws.task_id, "implementation")
    queue.claim()
    queue.fail(bad.id, "boom")
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert queue.list_jobs(task_id=ws.task_id) == []
    assert not ws.path.exists()


def test_delete_workspace_returns_404_when_missing(client: TestClient) -> None:
    res = client.delete("/api/workspaces/ghost")
    assert res.status_code == 404


def test_delete_workspace_returns_409_when_running_job_exists(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Active", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 409
    assert "active job" in res.json()["detail"]
    assert ws.path.exists()
    assert len(queue.list_jobs(task_id=ws.task_id)) == 1


def test_delete_workspace_returns_409_when_pending_job_exists(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Pending", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 409
    assert "active job" in res.json()["detail"]
    assert ws.path.exists()


def test_delete_workspace_publishes_workspace_deleted_event(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("Notify", "d", root=ws_root)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert any(
        evt.kind == "workspace_deleted" and evt.payload == {"task_id": ws.task_id}
        for evt in received
    )


def test_dispatch_rejects_archived_workspace(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Archived Dispatch", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "research"})
    assert res.status_code == 409
    assert "archived" in res.json()["detail"]
    assert queue.list_jobs(task_id=ws.task_id) == []
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_dispatch_from_completed_reactivates_workspace(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Completed Redispatch", "d", root=ws_root)
    workspace_mod.transition(ws, Status.COMPLETED)
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "research"})
    assert res.status_code == 202
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    jobs = queue.list_jobs(task_id=ws.task_id)
    assert len(jobs) == 1
    assert jobs[0].team == "research"


def test_dispatch_from_awaiting_input_reactivates_workspace(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("AwaitingInput Redispatch", "d", root=ws_root)
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "research"})
    assert res.status_code == 202
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert queue.list_jobs(task_id=ws.task_id)


def test_dispatch_from_escalated_reactivates_workspace(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Escalated Redispatch", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ESCALATED)
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "research"})
    assert res.status_code == 202
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert queue.list_jobs(task_id=ws.task_id)


def test_dispatch_resets_revisit_chain(client: TestClient, ws_root: Path, queue: Queue) -> None:
    ws = create("Dispatch Reset Revisit", "d", root=ws_root)
    h = _save_handover_with_question(ws, "q1", resolver="research")
    ws = workspace_mod.increment_revisit_count(ws, "verification", h.handover_chain_id)
    ws = workspace_mod.increment_revisit_count(ws, "implementation_fix", h.handover_chain_id)
    res = client.post(f"/api/workspaces/{ws.task_id}/dispatch", json={"team": "research"})
    assert res.status_code == 202
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.revisit_counts == {}


def test_dispatch_ignores_unknown_budget_field(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Budget Field", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/dispatch",
        json={"team": "research", "budget": "high"},
    )
    assert res.status_code == 202
    assert queue.list_jobs(task_id=ws.task_id)


def test_archive_workspace_returns_204_and_persists_status(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("To Archive", "d", root=ws_root)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    assert res.content == b""
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED
    assert any(
        evt.kind == "workspace_archived" and evt.payload == {"task_id": ws.task_id}
        for evt in received
    )


def test_archive_workspace_returns_404_when_missing(
    client: TestClient,
) -> None:
    res = client.post("/api/workspaces/ghost/archive")
    assert res.status_code == 404


def test_archive_workspace_returns_409_when_running_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Archive Active", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 409
    assert res.json()["detail"] == "workspace has active jobs"
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE


def test_archive_workspace_returns_409_when_pending_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Archive Pending", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 409
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE


def test_unarchive_workspace_returns_204_and_persists_status(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("To Unarchive", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 204
    assert res.content == b""
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.UNARCHIVED
    assert any(
        evt.kind == "workspace_unarchived" and evt.payload == {"task_id": ws.task_id}
        for evt in received
    )


def test_unarchive_workspace_returns_404_when_missing(
    client: TestClient,
) -> None:
    res = client.post("/api/workspaces/ghost/unarchive")
    assert res.status_code == 404


def test_unarchive_workspace_returns_204_when_already_active(
    client: TestClient, ws_root: Path, pubsub: PubSub
) -> None:
    ws = create("Already Active", "d", root=ws_root)
    received: list[TeamEvent] = []
    pubsub.subscribe(ws.task_id, received.append)
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 204
    assert res.content == b""
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ACTIVE
    assert not any(evt.kind == "workspace_unarchived" for evt in received)


@pytest.mark.parametrize(
    "status",
    [Status.AWAITING_INPUT, Status.COMPLETED, Status.ESCALATED],
)
def test_unarchive_workspace_returns_409_when_not_archived(
    client: TestClient, ws_root: Path, status: Status
) -> None:
    ws = create("Not Archived", "d", root=ws_root)
    workspace_mod.transition(ws, status)
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 409
    assert res.json()["detail"] == "workspace is not archived"
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is status


def _make_stopped_workspace(ws_root: Path, queue: Queue, title: str) -> "workspace_mod.Workspace":
    ws = create(title, "d", root=ws_root)
    j = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.requeue_pending(j.id)
    queue.pause_task(ws.task_id)
    return ws


def test_archive_workspace_succeeds_when_paused_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _make_stopped_workspace(ws_root, queue, "Archive Paused")
    res = client.post(f"/api/workspaces/{ws.task_id}/archive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.ARCHIVED


def test_delete_workspace_succeeds_when_paused_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _make_stopped_workspace(ws_root, queue, "Delete Paused")
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 204
    assert not ws.path.exists()
    assert queue.list_jobs(task_id=ws.task_id) == []
    assert queue.is_task_paused(ws.task_id) is False


def test_unarchive_workspace_restores_paused_status_when_parked_paused_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _make_stopped_workspace(ws_root, queue, "Unarchive Paused")
    workspace_mod.transition(ws, Status.ARCHIVED)
    res = client.post(f"/api/workspaces/{ws.task_id}/unarchive")
    assert res.status_code == 204
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.PAUSED


def test_delete_workspace_returns_409_when_rmtree_races(
    client: TestClient,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Racy", "d", root=ws_root)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(ws.path)

    monkeypatch.setattr("alan.workspace.shutil.rmtree", boom)
    res = client.delete(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 409
    assert "active job" in res.json()["detail"]
    assert ws.path.exists()


def test_read_history_entries_handles_non_list_root(tmp_path: Path, ws_root: Path) -> None:
    from alan.server import _read_history_entries

    workspace_path = tmp_path / "manual"
    workspace_path.mkdir()
    (workspace_path / "history.json").write_text('{"not": "list"}', encoding="utf-8")
    assert _read_history_entries(workspace_path) == []


def test_read_history_entries_handles_missing_file(tmp_path: Path) -> None:
    from alan.server import _read_history_entries

    assert _read_history_entries(tmp_path) == []


def test_read_history_entries_handles_unparseable_json(tmp_path: Path) -> None:
    from alan.server import _read_history_entries

    (tmp_path / "history.json").write_text("{ not json", encoding="utf-8")
    assert _read_history_entries(tmp_path) == []


def test_lifespan_resolves_paths_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI

    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "envq.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "envw"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _c:
        assert test_app.state.workspaces_root == tmp_path / "envw"
        assert (tmp_path / "envq.db").exists()


def test_lifespan_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fastapi import FastAPI

    from alan import queue as queue_mod

    monkeypatch.delenv("ALAN_QUEUE_DB_PATH", raising=False)
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setattr(queue_mod, "QUEUE_DB", tmp_path / "fallback-q.db")
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "fallback-ws"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _c:
        assert test_app.state.workspaces_root == tmp_path / "fallback-ws"


async def test_event_stream_helper_yields_published_event(pubsub: PubSub) -> None:
    import asyncio

    gen = server._workspace_event_stream("t1", pubsub)

    async def push() -> None:
        await asyncio.sleep(0.01)
        pubsub.publish("t1", TeamEvent("kind1", {"a": "b"}))

    task = asyncio.create_task(push())
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    finally:
        await task
        await gen.aclose()
    assert "event: kind1" in first


async def test_event_stream_helper_terminates_on_sentinel(
    pubsub: PubSub,
) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    gen = server._workspace_event_stream("t1", pubsub)

    async def first_call() -> str:
        return await gen.__anext__()

    task = loop.create_task(first_call())
    await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await gen.aclose()


def test_lifespan_starts_dispatcher_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "live-q.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "live-ws"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "live-engines"))
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )
    monkeypatch.setattr(_reconciler_mod, "seed_dep_sweep", lambda *, queue, workspaces_root: None)
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _c:
        assert test_app.state.dispatcher is not None


def test_lifespan_ignores_a_foreign_engine_record_in_the_session_engines_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import FastAPI

    from alan import engine_config as engine_config_mod
    from alan import roots as roots_mod

    session_engines_root = roots_mod.engines_root()
    session_engines_root.mkdir(parents=True, exist_ok=True)
    foreign = engine_config_mod.EngineConfig(
        repo=engine_config_mod.RepoIdentity(owner="foreign", repo="leaked-engine")
    )
    foreign_record = session_engines_root / "foreign-leftover.json"
    foreign_record.write_text(
        json.dumps(foreign.model_dump(mode="json")), encoding="utf-8"
    )

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "live-q.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "live-ws"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "live-engines"))
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )
    monkeypatch.setattr(_reconciler_mod, "seed_dep_sweep", lambda *, queue, workspaces_root: None)
    try:
        test_app = FastAPI(lifespan=server.lifespan)
        with TestClient(test_app) as _c:
            assert test_app.state.dispatcher is not None
            assert engine_config_mod.lookup_by_slug("foreign/leaked-engine") is None
    finally:
        foreign_record.unlink(missing_ok=True)


def test_dep_functions_read_app_state(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from typing import cast

    queue = Queue(db_path=tmp_path / "dep-q.db")
    pubsub = PubSub()
    ws_root = tmp_path / "dep-ws"
    stub_request = cast(
        "Any",
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(queue=queue, pubsub=pubsub, workspaces_root=ws_root)
            )
        ),
    )
    try:
        assert server._queue_dep(stub_request) is queue
        assert server._pubsub_dep(stub_request) is pubsub
        assert server._workspaces_root_dep(stub_request) == ws_root
    finally:
        queue.close()


async def test_workspace_events_returns_streaming_response(ws_root: Path, pubsub: PubSub) -> None:
    from fastapi.responses import StreamingResponse

    ws = create("Stream OK", "d", root=ws_root)
    response = await server.workspace_events(ws.task_id, workspaces_root=ws_root, pubsub=pubsub)
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"


async def test_workspace_events_with_subtask_id_returns_streaming_response(
    ws_root: Path, pubsub: PubSub
) -> None:
    from fastapi.responses import StreamingResponse

    ws = create("Stream Subtask", "e", root=ws_root)
    response = await server.workspace_events(
        ws.task_id, workspaces_root=ws_root, pubsub=pubsub, subtask_id="sub-1"
    )
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"


def test_get_workspace_artifact_returns_file(client: TestClient, ws_root: Path) -> None:
    ws = create("Artifact WS", "a", root=ws_root)
    artifact = ws.path / ".alan" / "review.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("review content")
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": str(artifact)},
    )
    assert res.status_code == 200
    assert res.content == b"review content"


def test_get_workspace_artifact_returns_seeded_invocation_report(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Report Artifact WS", "d", root=ws_root)
    inv_id = create_invocation_dir(ws)
    workflow_mod.save_invocation_report(
        ws, inv_id, "# Research report\n\nfull synthesis text"
    )
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": f"invocations/{inv_id}/report.md"},
    )
    assert res.status_code == 200
    assert res.content == b"# Research report\n\nfull synthesis text"


def test_get_workspace_artifact_returns_400_on_path_traversal(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Traversal WS", "t", root=ws_root)
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": "/etc/passwd"},
    )
    assert res.status_code == 400
    assert "outside workspace" in res.json()["detail"]


def test_get_workspace_artifact_returns_400_on_relative_path_traversal(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("RelTraversal WS", "r", root=ws_root)
    relative_escape = str(ws.path / "../../etc/passwd")
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": relative_escape},
    )
    assert res.status_code == 400
    assert "outside workspace" in res.json()["detail"]


def test_get_workspace_artifact_accepts_relative_path(client: TestClient, ws_root: Path) -> None:
    ws = create("RelPath WS", "rp", root=ws_root)
    artifact = ws.path / ".alan" / "notes.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("notes")
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": ".alan/notes.md"},
    )
    assert res.status_code == 200
    assert res.content == b"notes"


def test_get_workspace_artifact_returns_404_when_missing(client: TestClient, ws_root: Path) -> None:
    ws = create("Missing WS", "m", root=ws_root)
    res = client.get(
        f"/api/workspaces/{ws.task_id}/artifacts",
        params={"path": str(ws.path / "nonexistent.md")},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "artifact not found"


@pytest.fixture(autouse=True)
def _clear_parse_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_mod, "_WORKFLOW_CACHE", {})
    monkeypatch.setattr(workspace_mod, "_META_CACHE", {})


def test_list_workspaces_parses_each_workflow_at_most_once_across_two_polls(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Multi Inv", "a", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001")
    _write_workflow(ws, team="implementation", status="running", inv_id="inv_002")
    parse_count = 0
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        nonlocal parse_count
        parse_count += 1
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    client.get("/api/workspaces")
    after_first = parse_count
    client.get("/api/workspaces")
    assert parse_count == after_first


def test_list_workspaces_reparses_only_the_changed_workflow(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Changed Inv", "a", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001")
    _write_workflow(ws, team="implementation", status="running", inv_id="inv_002")
    parsed_paths: list[Path] = []
    original = workflow_mod.load_workflow

    def spy(p: Path) -> WorkflowDoc:
        parsed_paths.append(p)
        return original(p)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    client.get("/api/workspaces")
    parsed_paths.clear()
    _write_workflow(ws, team="research", status="failed", inv_id="inv_001")
    client.get("/api/workspaces")
    assert parsed_paths == [workflow_path(ws, "inv_001")]


def test_list_workspaces_response_identical_cold_vs_warm(client: TestClient, ws_root: Path) -> None:
    ws = create("Shape Check", "a", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001")
    cold = client.get("/api/workspaces")
    warm = client.get("/api/workspaces")
    assert cold.json() == warm.json()


def test_list_workspaces_default_excludes_archived(client: TestClient, ws_root: Path) -> None:
    live = create("Keep Me", "a", root=ws_root)
    archived = create("Hide Me", "a", root=ws_root)
    workspace_mod.transition(archived, Status.ARCHIVED)
    ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    assert live.task_id in ids
    assert archived.task_id not in ids


def test_list_workspaces_bucket_archived_returns_only_archived(
    client: TestClient, ws_root: Path
) -> None:
    live = create("Still Live", "a", root=ws_root)
    archived = create("Cold Storage", "a", root=ws_root)
    workspace_mod.transition(archived, Status.ARCHIVED)
    items = client.get("/api/workspaces?bucket=archived").json()
    ids = [w["task_id"] for w in items]
    assert ids == [archived.task_id]
    assert items[0]["status"] == "archived"
    assert live.task_id not in ids


def test_list_workspaces_default_lists_run_and_never_run_together(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    dispatched = create("Has Job", "a", root=ws_root)
    fresh = create("No Job", "a", root=ws_root)
    queue.enqueue(dispatched.task_id, "research")
    ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    assert dispatched.task_id in ids
    assert fresh.task_id in ids


def test_list_workspaces_bucket_invalid_rejected(client: TestClient) -> None:
    assert client.get("/api/workspaces?bucket=bogus").status_code == 422
    assert client.get("/api/workspaces?bucket=active").status_code == 422
    assert client.get("/api/workspaces?bucket=backlog").status_code == 422


def test_list_workspaces_unarchive_returns_row_to_default_listing(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Round Tripper", "a", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    assert [w["task_id"] for w in client.get("/api/workspaces").json()] == []
    assert client.post(f"/api/workspaces/{ws.task_id}/unarchive").status_code == 204
    ids = [w["task_id"] for w in client.get("/api/workspaces").json()]
    assert ids == [ws.task_id]
    assert client.get("/api/workspaces?bucket=archived").json() == []


def test_delete_archived_workspace_removes_blob_row(client: TestClient, ws_root: Path) -> None:
    ws = create("Delete From Archive", "a", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    assert len(client.get("/api/workspaces?bucket=archived").json()) == 1
    assert client.delete(f"/api/workspaces/{ws.task_id}").status_code == 204
    assert client.get("/api/workspaces?bucket=archived").json() == []


def test_admin_reindex_recounts_rows(client: TestClient, ws_root: Path) -> None:
    create("Live Row", "a", root=ws_root)
    archived = create("Cold Row", "a", root=ws_root)
    workspace_mod.transition(archived, Status.ARCHIVED)
    res = client.post("/api/admin/reindex")
    assert res.status_code == 200
    assert res.json() == {"rows": 1, "archived_rows": 1}
