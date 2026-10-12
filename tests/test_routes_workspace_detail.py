from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server, unblock_ledger
from alan.deploy_verify import GateRecord
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import Handover, save_handover
from alan.incident import STUCK_WS_STATUSES
from alan.queue import Queue
from alan.routes._workspace_detail import (
    WorkspaceDetail,
    detail_timing,
    has_resumable_failed_job,
    latest_pr_number,
    manager_summary_stale,
    resolve_pending_deploy_gate,
)
from alan.workspace import (
    Requirement,
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    create,
    mark_delivered,
    transition,
)
from tests.fs_timestamp_support import write_summary_sidecar


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def test_resolve_pending_deploy_gate_returns_none_with_no_jobs(queue: Queue) -> None:
    assert resolve_pending_deploy_gate(queue, "no-such-task") is None


def test_resolve_pending_deploy_gate_returns_none_for_job_with_no_instructions(
    queue: Queue,
) -> None:
    queue.enqueue("t1", "deploy_verify")
    assert resolve_pending_deploy_gate(queue, "t1") is None


def test_resolve_pending_deploy_gate_returns_none_for_ceiling_path(queue: Queue) -> None:
    record = GateRecord(merge_sha="a" * 40, path="ceiling", deferred_at=time.time())
    queue.enqueue(
        "t1", "deploy_verify", eligible_at=time.time() + 600.0, instructions=record.to_json()
    )
    assert resolve_pending_deploy_gate(queue, "t1") is None


def test_resolve_pending_deploy_gate_returns_none_for_restart_free_path(queue: Queue) -> None:
    record = GateRecord(merge_sha="a" * 40, path="restart_free", deferred_at=None)
    queue.enqueue("t1", "deploy_verify", instructions=record.to_json())
    assert resolve_pending_deploy_gate(queue, "t1") is None


def test_resolve_pending_deploy_gate_returns_payload_for_awaiting_restart(
    queue: Queue,
) -> None:
    deferred_at = time.time() - 45.0
    record = GateRecord(
        merge_sha="a" * 40,
        path="awaiting_restart",
        deferred_at=deferred_at,
        deadline=time.time() + 5400.0,
        race_deadline=None,
    )
    queue.enqueue(
        "t1", "deploy_verify", eligible_at=time.time() + 5400.0, instructions=record.to_json()
    )
    gate = resolve_pending_deploy_gate(queue, "t1")
    assert gate is not None
    assert gate.path == "awaiting_restart"
    assert gate.deadline == record.deadline
    assert gate.merged_at is None
    assert gate.waited_s == pytest.approx(45.0, abs=2.0)


def test_resolve_pending_deploy_gate_returns_payload_for_awaiting_live(
    queue: Queue,
) -> None:
    merged_at = time.time() - 100.0
    record = GateRecord(
        merge_sha="a" * 40,
        path="awaiting_live",
        deferred_at=time.time() - 10.0,
        deadline=time.time() + 5400.0,
        race_deadline=None,
        merged_at=merged_at,
    )
    queue.enqueue(
        "t1", "deploy_verify", eligible_at=time.time() + 5400.0, instructions=record.to_json()
    )
    gate = resolve_pending_deploy_gate(queue, "t1")
    assert gate is not None
    assert gate.path == "awaiting_live"
    assert gate.merged_at == merged_at


def test_resolve_pending_deploy_gate_ignores_other_teams_jobs(queue: Queue) -> None:
    record = GateRecord(merge_sha="a" * 40, path="awaiting_restart", deferred_at=time.time())
    queue.enqueue(
        "t1", "implementation", eligible_at=time.time() + 5400.0, instructions=record.to_json()
    )
    assert resolve_pending_deploy_gate(queue, "t1") is None


def test_resolve_pending_deploy_gate_skips_earlier_non_gate_job_for_later_awaiting_job(
    queue: Queue,
) -> None:
    fresh_record = GateRecord(merge_sha="a" * 40, path="restart_free", deferred_at=None)
    queue.enqueue("t1", "deploy_verify", instructions=fresh_record.to_json())
    awaiting_record = GateRecord(
        merge_sha="b" * 40,
        path="awaiting_restart",
        deferred_at=time.time() - 10.0,
        deadline=time.time() + 5400.0,
        race_deadline=None,
    )
    queue.enqueue(
        "t1",
        "deploy_verify",
        eligible_at=time.time() + 5400.0,
        instructions=awaiting_record.to_json(),
    )
    gate = resolve_pending_deploy_gate(queue, "t1")
    assert gate is not None
    assert gate.path == "awaiting_restart"
    assert gate.deadline == awaiting_record.deadline


def test_detail_timing_returns_none_active_seconds_when_no_row(ws_root: Path) -> None:
    ws = create("Detail Timing WS", "d", root=ws_root)
    active_seconds, _lead = detail_timing(ws, None)
    assert active_seconds is None


def test_has_resumable_failed_job_false_with_no_jobs(queue: Queue) -> None:
    assert has_resumable_failed_job(queue, "no-such-task") is False


def test_has_resumable_failed_job_false_without_resume_inv_id(queue: Queue) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.fail(job.id, "boom")
    assert has_resumable_failed_job(queue, "t1") is False


def test_has_resumable_failed_job_true_for_resume_capable_team_with_resume_inv_id(
    queue: Queue,
) -> None:
    job = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "interrupted", resume_inv_id="inv-1")
    assert has_resumable_failed_job(queue, "t1") is True


def test_has_resumable_failed_job_false_for_non_resume_capable_team_even_with_resume_inv_id(
    queue: Queue,
) -> None:
    job = queue.enqueue("t1", "deploy_verify")
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "interrupted", resume_inv_id="inv-1")
    assert has_resumable_failed_job(queue, "t1") is False


def test_has_resumable_failed_job_uses_most_recent_failed_job(queue: Queue) -> None:
    job1 = queue.enqueue("t1", "implementation")
    queue.claim()
    queue.mark_interrupted_and_fail(job1.id, "interrupted", resume_inv_id="inv-1")
    job2 = queue.enqueue("t1", "deploy_verify")
    queue.claim()
    queue.fail(job2.id, "boom")
    assert has_resumable_failed_job(queue, "t1") is False


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
    tmp_path: Path,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (
        lambda: tmp_path / "bc"
    )
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (
        lambda: tmp_path / "ec"
    )
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_get_workspace_pending_deploy_gate_null_for_freshly_queued_job(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Fresh Deploy Verify", "d", root=ws_root)
    queue.enqueue(ws.task_id, "deploy_verify")
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["pending_deploy_gate"] is None


def test_get_workspace_pending_deploy_gate_populated_for_awaiting_live(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Waiting Deploy Verify", "d", root=ws_root)
    merged_at = time.time() - 60.0
    record = GateRecord(
        merge_sha="a" * 40,
        path="awaiting_live",
        deferred_at=time.time() - 5.0,
        deadline=time.time() + 5400.0,
        race_deadline=None,
        merged_at=merged_at,
    )
    queue.enqueue(
        ws.task_id,
        "deploy_verify",
        eligible_at=time.time() + 5400.0,
        instructions=record.to_json(),
    )
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    gate = res.json()["pending_deploy_gate"]
    assert gate is not None
    assert gate["path"] == "awaiting_live"
    assert gate["merged_at"] == merged_at


def test_get_workspace_pending_deploy_gate_null_with_no_jobs(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Jobs WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["pending_deploy_gate"] is None


def test_get_workspace_live_at_null_before_confirmation(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Not Yet Live WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["live_at"] is None


def test_get_workspace_live_at_surfaces_confirmed_timestamp(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Live WS", "d", root=ws_root)
    live_at = datetime.now(UTC)
    mark_delivered(ws, settled_active_seconds=12.0, live_at=live_at)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["live_at"] is not None


def _detail(
    *, requirements: list[Requirement] | None = None, tasks: list[Task] | None = None
) -> WorkspaceDetail:
    now = datetime.now(UTC)
    return WorkspaceDetail(
        task_id="t1",
        title="T",
        description="d",
        status=Status.ACTIVE,
        revisit_counts={},
        created_at=now,
        updated_at=now,
        history=[],
        latest_handover=None,
        requirements=requirements,
        tasks=tasks or [],
    )


def test_workspace_detail_unproven_clause_count_zero_without_requirements() -> None:
    detail = _detail()
    assert detail.unproven_clause_count == 0
    assert detail.unproven_clause_ids == []


def test_workspace_detail_unproven_clause_ids_sorted_and_counted() -> None:
    a = Requirement(text="a clause")
    b = Requirement(text="b clause")
    detail = _detail(requirements=[a, b])
    assert detail.unproven_clause_count == 2
    assert detail.unproven_clause_ids == sorted([a.id, b.id])


def test_workspace_detail_unproven_clause_count_excludes_withdrawn_clause() -> None:
    live = Requirement(text="a clause")
    withdrawn = Requirement(text="b clause", withdrawn_by_question_id="clause-stall-x")
    detail = _detail(requirements=[live, withdrawn])
    assert detail.unproven_clause_count == 1
    assert detail.unproven_clause_ids == [live.id]


def test_get_workspace_unproven_clause_count_zero_without_requirements(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Reqs WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["unproven_clause_count"] == 0
    assert data["unproven_clause_ids"] == []


def test_get_workspace_unproven_clause_count_counts_unbound_uncovered_clause(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Unproven Reqs WS", "d", root=ws_root, requirements=[Requirement(text="clause a")]
    )
    assert ws.meta.requirements is not None
    clause_id = ws.meta.requirements[0].id
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["unproven_clause_count"] == 1
    assert data["unproven_clause_ids"] == [clause_id]


def test_get_workspace_unproven_clause_count_excludes_covered_clause(
    client: TestClient, ws_root: Path
) -> None:
    clause = Requirement(text="clause a")
    task = Task(
        id="t1",
        status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="deliver", covers=[clause.id], evidence="PR #1")],
    )
    ws = create("Covered Reqs WS", "d", root=ws_root, requirements=[clause], tasks=[task])
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["unproven_clause_count"] == 0
    assert data["unproven_clause_ids"] == []


def test_get_workspace_unproven_clause_count_excludes_test_ref_bound_clause(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Bound Reqs WS",
        "d",
        root=ws_root,
        requirements=[Requirement(text="clause a", test_ref="t1")],
    )
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["unproven_clause_count"] == 0
    assert data["unproven_clause_ids"] == []


def test_get_workspace_unblock_is_null_without_ledger_rows(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Never Unblocked WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["unblock"] is None


def test_get_workspace_unblock_surfaces_the_last_ledger_record(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = create("Unblocked WS", "d", root=ws_root)
    transition(ws, Status.AWAITING_INPUT)
    unblock_ledger.ensure_schema(queue)
    unblock_ledger.record(
        queue,
        unblock_ledger.Entry(
            workspace_key=ws.task_id, scope_ref=f"ws:{ws.task_id}", shape="stale_review",
            fingerprint="fp", kind=unblock_ledger.KIND_ATTEMPT, action="dispatch:pr_merge",
            outcome=unblock_ledger.OUTCOME_ACTED, ts=1_000_000.0,
        ),
    )
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    record = res.json()["unblock"]
    assert record["shape"] == "stale_review"
    assert record["attempts"] == 1
    assert record["ceiling"] == unblock_ledger.ATTEMPT_CEILING_PER_WORKSPACE
    assert record["outcome"] == "acted"
    assert record["summary"].startswith("attempt 1 of 5 · stale_review · dispatch:pr_merge · acted")
    assert record["issue_url"] is None


def _make_handover(
    task_id: str,
    handover_id: str,
    *,
    pr_number: int | None = None,
    timestamp: datetime | None = None,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type="forward",
        task_id=task_id,
        from_team="research",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=timestamp or datetime.now(UTC),
        full_synthesis="synthesis",
        pr_number=pr_number,
    )


def test_latest_pr_number_returns_none_for_empty_list() -> None:
    assert latest_pr_number([]) is None


def test_latest_pr_number_returns_none_when_no_handover_carries_one() -> None:
    handovers = [_make_handover("t1", "h1"), _make_handover("t1", "h2")]
    assert latest_pr_number(handovers) is None


def test_latest_pr_number_returns_most_recent_non_null() -> None:
    handovers = [
        _make_handover("t1", "h1", pr_number=1),
        _make_handover("t1", "h2", pr_number=None),
        _make_handover("t1", "h3", pr_number=3),
    ]
    assert latest_pr_number(handovers) == 3


def test_manager_summary_is_stale_false_when_written_at_none() -> None:
    assert manager_summary_stale(None, datetime.now(UTC), []) is False


def test_manager_summary_is_stale_false_when_nothing_newer() -> None:
    written_at = datetime.now(UTC)
    ws_updated_at = written_at - timedelta(minutes=5)
    handover = _make_handover("t1", "h1", timestamp=written_at - timedelta(minutes=10))
    assert manager_summary_stale(written_at, ws_updated_at, [handover]) is False


def test_manager_summary_is_stale_true_when_workspace_resumed_past_it() -> None:
    written_at = datetime.now(UTC)
    ws_updated_at = written_at + timedelta(minutes=5)
    assert manager_summary_stale(written_at, ws_updated_at, []) is True


def test_manager_summary_is_stale_true_when_newer_handover_exists() -> None:
    written_at = datetime.now(UTC)
    ws_updated_at = written_at - timedelta(minutes=5)
    handover = _make_handover("t1", "h1", timestamp=written_at + timedelta(minutes=1))
    assert manager_summary_stale(written_at, ws_updated_at, [handover]) is True


def test_get_workspace_pr_number_null_without_pr_bearing_handover(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No PR WS", "d", root=ws_root)
    save_handover(ws, _make_handover(ws.task_id, "h1"))
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["pr_number"] is None


def test_get_workspace_pr_number_surfaces_latest_handovers_pr(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("PR WS", "d", root=ws_root)
    save_handover(ws, _make_handover(ws.task_id, "h1", pr_number=7))
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["pr_number"] == 7


def test_get_workspace_pr_number_uses_latest_of_several_handovers(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Multi PR WS", "d", root=ws_root)
    save_handover(ws, _make_handover(ws.task_id, "h1", pr_number=1))
    save_handover(ws, _make_handover(ws.task_id, "h2", pr_number=2))
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["pr_number"] == 2


def test_get_workspace_manager_summary_is_stale_false_when_fresh(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Summary WS", "d", root=ws_root)
    write_summary_sidecar(ws, '{"ok": true, "text": "done"}')
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    summary = res.json()["manager_summary"]
    assert summary["written_at"] is not None
    assert summary["stale"] is False


def test_get_workspace_manager_summary_is_stale_true_after_resume(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Stale Summary WS", "d", root=ws_root)
    (ws.path / "manager_summary.json").write_text('{"ok": true, "text": "done"}')
    time.sleep(0.1)
    transition(ws, Status.PAUSED)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["manager_summary"]["stale"] is True


def test_get_workspace_manager_summary_null_without_sidecar(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("No Summary WS", "d", root=ws_root)
    res = client.get(f"/api/workspaces/{ws.task_id}")
    assert res.status_code == 200
    assert res.json()["manager_summary"] is None


@pytest.mark.parametrize("status", sorted(STUCK_WS_STATUSES, key=str))
def test_workspace_detail_operator_link_is_true_for_each_stuck_status(status: Status) -> None:
    detail = _detail().model_copy(update={"status": status})
    assert detail.operator_link is True


@pytest.mark.parametrize("status", sorted(set(Status) - STUCK_WS_STATUSES, key=str))
def test_workspace_detail_operator_link_is_false_for_every_other_status(status: Status) -> None:
    detail = _detail().model_copy(update={"status": status})
    assert detail.operator_link is False


def test_workspace_detail_operator_link_is_serialised_as_a_computed_field() -> None:
    stuck = _detail().model_copy(update={"status": Status.ESCALATED})
    assert _detail().model_dump()["operator_link"] is False
    assert stuck.model_dump()["operator_link"] is True


def test_get_workspace_operator_link_follows_the_stuck_definition(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Operator Link WS", "d", root=ws_root)
    assert client.get(f"/api/workspaces/{ws.task_id}").json()["operator_link"] is False
    transition(ws, Status.AWAITING_INPUT)
    assert client.get(f"/api/workspaces/{ws.task_id}").json()["operator_link"] is True
