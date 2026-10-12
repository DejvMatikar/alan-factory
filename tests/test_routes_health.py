from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alan import engine_config as engine_config_mod
from alan import reconciler as _reconciler_mod
from alan import server
from alan.health import (
    HEALTH_META_KEY,
    save_health_state,
)


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _health_state_json(
    verdict: str = "degraded",
    findings: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps({
        "verdict": verdict,
        "blocked_count": len(findings or []),
        "in_flight_count": 0,
        "findings": findings or [],
        "computed_at": datetime.now(UTC).isoformat(),
    })


def test_health_endpoint_includes_health_state_when_fresh(client: TestClient) -> None:
    server.app.state.queue.set_meta(
        HEALTH_META_KEY, _health_state_json("healthy", [])
    )
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["health_state"] is not None
    assert data["health_state"]["verdict"] == "healthy"


def test_health_endpoint_findings_response_includes_fields(client: TestClient) -> None:
    finding = {
        "finding_id": "task-abc",
        "kind": "build_task",
        "severity": "critical",
        "description": "desc",
        "workspace_id": "task-abc",
        "pr_number": None,
        "blocker_flags": ["pr_checks_failing"],
        "action": "dispatch_remediation",
        "in_flight": True,
    }
    server.app.state.queue.set_meta(
        HEALTH_META_KEY, _health_state_json("degraded", [finding])
    )
    res = client.get("/api/health")
    assert res.status_code == 200
    findings = res.json()["health_state"]["findings"]
    assert len(findings) == 1
    assert findings[0]["finding_id"] == "task-abc"
    assert findings[0]["in_flight"] is True


def test_health_endpoint_health_state_null_when_stale(client: TestClient) -> None:
    from datetime import timedelta

    from alan.health import _STALE_SECONDS

    old_ts = (datetime.now(UTC) - timedelta(seconds=_STALE_SECONDS + 60)).isoformat()
    stale_state = json.dumps({
        "verdict": "healthy",
        "blocked_count": 0,
        "in_flight_count": 0,
        "findings": [],
        "computed_at": old_ts,
    })
    server.app.state.queue.set_meta(HEALTH_META_KEY, stale_state)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["health_state"] is None


def test_health_endpoint_health_state_null_when_missing(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["health_state"] is None


def test_health_endpoint_counts_derived_from_findings_not_persisted_counts(
    client: TestClient,
) -> None:
    finding = {
        "finding_id": "task-abc",
        "kind": "build_task",
        "severity": "critical",
        "description": "desc",
        "workspace_id": "task-abc",
        "pr_number": None,
        "blocker_flags": ["pr_checks_failing"],
        "action": "dispatch_remediation",
        "in_flight": True,
    }
    corrupt_state = json.dumps({
        "verdict": "degraded",
        "blocked_count": 5,
        "in_flight_count": 5,
        "findings": [finding],
        "computed_at": datetime.now(UTC).isoformat(),
    })
    server.app.state.queue.set_meta(HEALTH_META_KEY, corrupt_state)
    res = client.get("/api/health")
    assert res.status_code == 200
    health_state = res.json()["health_state"]
    assert health_state["blocked_count"] == 1
    assert health_state["in_flight_count"] == 1
    assert len(health_state["findings"]) == 1


def test_web_dist_dir_is_pinned_where_the_ui_cannot_mount() -> None:
    pinned = os.environ["ALAN_WEB_DIST_DIR"].strip()
    assert pinned
    assert not (Path(pinned) / "index.html").is_file()


def _has_route_for_path(app: FastAPI, path: str) -> bool:
    return any(getattr(route, "path", None) == path for route in app.routes)


def test_dismiss_endpoint_absent_from_route_table(client: TestClient) -> None:
    assert not _has_route_for_path(server.app, "/api/health/dismiss")


def test_clear_endpoint_absent_from_route_table(client: TestClient) -> None:
    assert not _has_route_for_path(server.app, "/api/health/clear")


def test_dismiss_and_clear_endpoints_absent_even_with_spa_mounted(tmp_path: Path) -> None:
    from alan.routes.infra import mount_ui

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html></html>")

    probe = FastAPI()
    assert mount_ui(probe, dist_dir) is True
    assert not _has_route_for_path(probe, "/api/health/dismiss")
    assert not _has_route_for_path(probe, "/api/health/clear")


def test_health_response_omits_dismissed_resolved_resolvable_fields(
    client: TestClient,
) -> None:
    from alan.health import compute_health_state
    from alan.teams.triage_pregate import ClassifiedSurface

    surface = ClassifiedSurface(
        surface_id="task-ghost",
        kind="build_task",
        is_blocked=False,
        blocker_reason="ws_stuck",
        obs={},
        resolvable=True,
    )
    state = compute_health_state([surface], datetime.now(UTC))
    save_health_state(server.app.state.queue, state)

    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "dismissed_findings" not in data
    assert "resolved_findings" not in data
    assert "resolvable_findings" not in (data["health_state"] or {})


def test_complete_workspace_not_found_404(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    res = client.post("/api/health/complete", json={"finding_id": "does-not-exist"})
    assert res.status_code == 404
    assert res.json()["detail"] == "workspace_not_found"


def test_complete_toctou_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-toctou", "d", root=ws_root, team="implementation")
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [])
    monkeypatch.setattr(health_routes, "is_merged_ghost", lambda _ws, _q, _idx: False)
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 409
    assert res.json()["detail"] == "no_longer_finished"


def test_complete_happy_path_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod
    from alan.handover import Handover, save_handover

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-happy", "d", root=ws_root, team="implementation")
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    ws = ws_mod.read(ws.task_id, root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=datetime.now(UTC),
        full_synthesis="stub", pr_number=42,
    )
    save_handover(ws, h)
    merged_pr = {
        "number": 42, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [merged_pr])
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 200
    assert res.json()["task_id"] == ws.task_id
    updated = ws_mod.read(ws.task_id, root=ws_root)
    assert updated.meta.status == ws_mod.Status.COMPLETED


def test_complete_records_health_complete_intervention(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod
    from alan.handover import Handover, save_handover

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-intervention", "d", root=ws_root, team="implementation")
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    ws = ws_mod.read(ws.task_id, root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=datetime.now(UTC),
        full_synthesis="stub", pr_number=42,
    )
    save_handover(ws, h)
    merged_pr = {
        "number": 42, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [merged_pr])
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 200
    updated = ws_mod.read(ws.task_id, root=ws_root)
    assert updated.meta.status == ws_mod.Status.COMPLETED
    assert [i.action for i in updated.meta.interventions] == [
        ws_mod.InterventionAction.HEALTH_COMPLETE
    ]


def test_complete_toctou_409_records_no_intervention(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-toctou-none", "d", root=ws_root, team="implementation")
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [])
    monkeypatch.setattr(health_routes, "is_merged_ghost", lambda _ws, _q, _idx: False)
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 409
    reloaded = ws_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_complete_stamps_pr_evidence_on_dod_clauses(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod
    from alan.handover import Handover, save_handover
    from alan.workspace import Task, TaskDoDClause, TaskStatus

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-ev", "d", root=ws_root, team="implementation")
    task = Task(
        id="t1",
        status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="Finish feature")],
    )
    ws = ws_mod.inject_tasks(ws, [task], [])
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    ws = ws_mod.read(ws.task_id, root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=datetime.now(UTC),
        full_synthesis="stub", pr_number=55,
    )
    save_handover(ws, h)
    merged_pr = {
        "number": 55, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [merged_pr])
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 200
    updated = ws_mod.read(ws.task_id, root=ws_root)
    evidence = updated.meta.tasks[0].dod_clauses[0].evidence
    assert evidence is not None
    assert "PR #55" in evidence


def test_complete_no_dod_clauses_skips_stamp(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod
    from alan.handover import Handover, save_handover
    from alan.workspace import Task, TaskStatus

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-nodod", "d", root=ws_root, team="implementation")
    task = Task(id="t1", status=TaskStatus.PENDING, dod_clauses=[])
    ws = ws_mod.inject_tasks(ws, [task], [])
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)
    ws = ws_mod.read(ws.task_id, root=ws_root)
    h = Handover(
        handover_id="h1", handover_chain_id="h1", handover_type="forward",
        task_id=ws.task_id, from_team="implementation", from_invocation_id="inv_1",
        to_team_suggestions=[], iteration=1, timestamp_utc=datetime.now(UTC),
        full_synthesis="stub", pr_number=60,
    )
    save_handover(ws, h)
    merged_pr = {
        "number": 60, "state": "MERGED",
        "headRefName": "teams/" + ws.task_id,
        "reviewDecision": "APPROVED", "mergeStateStatus": "MERGED",
        "statusCheckRollup": [],
    }
    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [merged_pr])
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 200
    updated = ws_mod.read(ws.task_id, root=ws_root)
    assert updated.meta.tasks[0].dod_clauses == []


def test_complete_list_handovers_raises_uses_merged_evidence(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    ws_root: Path,
) -> None:
    import alan.routes.health as health_routes
    import alan.workspace as ws_mod

    monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
    ws = ws_mod.create("ghost-noho", "d", root=ws_root, team="implementation")
    ws_mod.transition(ws, ws_mod.Status.ESCALATED)

    monkeypatch.setattr(health_routes, "_fetch_raw_prs", lambda: [])
    monkeypatch.setattr(health_routes, "is_merged_ghost", lambda _ws, _q, _idx: True)

    def _raises(_ws: Any) -> Any:
        raise RuntimeError("no handovers")

    monkeypatch.setattr(health_routes, "list_handovers", _raises)
    res = client.post("/api/health/complete", json={"finding_id": ws.task_id})
    assert res.status_code == 200
    updated = ws_mod.read(ws.task_id, root=ws_root)
    assert updated.meta.status == ws_mod.Status.COMPLETED


def test_health_endpoint_includes_finisher_rate_when_present(
    client: TestClient,
) -> None:
    from alan.finisher_rate import FinisherRate, FunnelCounts, LeakReasons
    from alan.health import save_finisher_rate
    fr = FinisherRate(
        rate=0.6,
        counts=FunnelCounts(10, 9, 8, 7, 6, 6, autonomous=5),
        leak_reasons=LeakReasons(crash_reasons={}, convergence_kinds={}),
        low_data=False,
        window_days=30,
        sample_size=10,
        floor_alert=False,
        autonomous_rate=0.5,
    )
    save_finisher_rate(server.app.state.queue, fr)
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "finisher_rate" in data
    assert data["finisher_rate"] is not None
    assert data["finisher_rate"]["sample_size"] == 10
    assert data["finisher_rate"]["window_days"] == 30
    assert data["finisher_rate"]["autonomous_rate"] == pytest.approx(0.5)
    assert data["finisher_rate"]["counts"]["autonomous"] == 5


def test_health_endpoint_finisher_rate_null_when_missing(
    client: TestClient,
) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "finisher_rate" in data
    assert data["finisher_rate"] is None


def _sweep_complete_row(
    *, timestamp_utc: str, surface_count: int, blocked_count: int
) -> str:
    return json.dumps({
        "action": "sweep_complete",
        "timestamp_utc": timestamp_utc,
        "task_id": "t",
        "inv_id": "inv_001",
        "surface_count": surface_count,
        "blocked_count": blocked_count,
    })


def test_health_endpoint_sweeper_all_none_when_no_audit_file(
    client: TestClient,
) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["sweeper"] == {
        "timestamp_utc": None,
        "surface_count": None,
        "blocked_count": None,
        "recent_actions": [],
        "next_eligible_utc": None,
    }


def test_health_endpoint_sweeper_populated_from_latest_sweep_complete_row(
    tmp_path: Path, client: TestClient
) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "triage_audit.jsonl").write_text(
        _sweep_complete_row(
            timestamp_utc="2026-07-11T09:00:00+00:00", surface_count=4, blocked_count=1
        )
        + "\n",
        encoding="utf-8",
    )
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["sweeper"] == {
        "timestamp_utc": "2026-07-11T09:00:00+00:00",
        "surface_count": 4,
        "blocked_count": 1,
        "recent_actions": [
            {"action": "sweep_complete", "timestamp_utc": "2026-07-11T09:00:00+00:00"}
        ],
        "next_eligible_utc": None,
    }


def test_health_endpoint_sweeper_includes_next_eligible_utc_from_queue_meta(
    client: TestClient,
) -> None:
    from alan.health import SWEEP_CADENCE_SECONDS
    from alan.teams.triage import _CADENCE_META_KEY

    last_scheduled = 1752221999.0
    server.app.state.queue.set_meta(_CADENCE_META_KEY, str(last_scheduled))
    res = client.get("/api/health")
    assert res.status_code == 200
    expected = datetime.fromtimestamp(
        last_scheduled + SWEEP_CADENCE_SECONDS, tz=UTC
    ).isoformat()
    assert res.json()["sweeper"]["next_eligible_utc"] == expected


def test_health_endpoint_sweeper_next_eligible_utc_none_when_meta_malformed(
    client: TestClient,
) -> None:
    from alan.teams.triage import _CADENCE_META_KEY

    server.app.state.queue.set_meta(_CADENCE_META_KEY, "not-a-number")
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["sweeper"]["next_eligible_utc"] is None


def test_health_endpoint_includes_served_sha(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    import alan.routes.infra as infra_routes

    monkeypatch.setattr(infra_routes, "served_sha", lambda: "deadbeef")
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["served_sha"] == "deadbeef"


def test_health_endpoint_served_sha_none_when_unresolvable(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    import alan.routes.infra as infra_routes

    monkeypatch.setattr(infra_routes, "served_sha", lambda: None)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["served_sha"] is None


def _worker_pool_finding(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "finding_id": "worker_pool",
        "kind": "worker_pool",
        "severity": "critical",
        "description": "d",
        "workspace_id": None,
        "pr_number": None,
        "blocker_flags": [],
        "action": "deep_link",
    }
    base.update(overrides)
    return base


def test_health_endpoint_finding_includes_observed_at_when_present(client: TestClient) -> None:
    finding = _worker_pool_finding(observed_at="2026-08-30T00:00:00+00:00")
    server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json("degraded", [finding]))
    res = client.get("/api/health")
    assert res.status_code == 200
    findings = res.json()["health_state"]["findings"]
    assert findings[0]["observed_at"] == "2026-08-30T00:00:00+00:00"


def test_health_endpoint_finding_observed_at_null_when_absent(client: TestClient) -> None:
    finding = _worker_pool_finding()
    server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json("degraded", [finding]))
    res = client.get("/api/health")
    assert res.status_code == 200
    findings = res.json()["health_state"]["findings"]
    assert findings[0]["observed_at"] is None


def test_suppress_worker_pool_noop_when_dispatcher_is_none() -> None:
    from alan.health import HealthFinding
    from alan.routes.infra import _suppress_worker_pool

    finding = HealthFinding(
        finding_id="worker_pool", kind="worker_pool", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="deep_link",
    )
    assert _suppress_worker_pool([finding], dispatcher=None) == [finding]


def test_suppress_worker_pool_keeps_finding_when_no_live_workers(tmp_path: Path) -> None:
    from alan.dispatcher import Dispatcher
    from alan.health import HealthFinding
    from alan.queue import Queue
    from alan.routes.infra import _suppress_worker_pool

    q = Queue(db_path=tmp_path / "sup1.db")
    d = Dispatcher(q, {}, workspaces_root=tmp_path / "ws", concurrency=1)
    finding = HealthFinding(
        finding_id="worker_pool", kind="worker_pool", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="deep_link",
    )
    try:
        assert _suppress_worker_pool([finding], dispatcher=d) == [finding]
    finally:
        q.close()


def test_suppress_worker_pool_drops_finding_when_dispatcher_has_live_workers(
    tmp_path: Path,
) -> None:
    from alan.dispatcher import Dispatcher
    from alan.health import HealthFinding
    from alan.queue import Queue
    from alan.routes.infra import _suppress_worker_pool

    q = Queue(db_path=tmp_path / "sup2.db")
    d = Dispatcher(q, {}, workspaces_root=tmp_path / "ws", concurrency=1, poll_interval=0.01)
    other_finding = HealthFinding(
        finding_id="main_ci", kind="main_ci", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="dispatch_remediation",
    )
    worker_finding = HealthFinding(
        finding_id="worker_pool", kind="worker_pool", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="deep_link",
    )
    d.start()
    try:
        result = _suppress_worker_pool([other_finding, worker_finding], dispatcher=d)
        assert result == [other_finding]
    finally:
        d.stop(timeout=3.0)
        q.close()


def test_suppress_worker_pool_keeps_finding_on_partial_deficit(tmp_path: Path) -> None:
    from alan.dispatcher import Dispatcher
    from alan.health import HealthFinding
    from alan.queue import Queue
    from alan.routes.infra import _suppress_worker_pool

    q = Queue(db_path=tmp_path / "sup3.db")
    d = Dispatcher(q, {}, workspaces_root=tmp_path / "ws", concurrency=2, poll_interval=0.01)
    finding = HealthFinding(
        finding_id="worker_pool", kind="worker_pool", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="deep_link",
    )
    d.start()
    try:
        d._alive_worker_count = 1  # noqa: SLF001
        assert _suppress_worker_pool([finding], dispatcher=d) == [finding]
    finally:
        d.stop(timeout=3.0)
        q.close()


def test_prepare_health_state_verdict_reflects_post_suppression_findings() -> None:
    from alan.health import HealthFinding, HealthState
    from alan.routes.infra import _prepare_health_state

    finding = HealthFinding(
        finding_id="worker_pool", kind="worker_pool", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="deep_link",
    )
    state = HealthState(
        verdict="degraded", blocked_count=1, in_flight_count=0,
        findings=[finding], computed_at=datetime.now(UTC).isoformat(),
    )
    result = _prepare_health_state(state, [])
    assert result is not None
    assert result.verdict == "healthy"
    assert result.blocked_count == 0


def test_prepare_health_state_verdict_stays_degraded_with_findings() -> None:
    from alan.health import HealthFinding, HealthState
    from alan.routes.infra import _prepare_health_state

    finding = HealthFinding(
        finding_id="main_ci", kind="main_ci", severity="critical",
        description="d", workspace_id=None, pr_number=None, blocker_flags=[],
        action="dispatch_remediation",
    )
    state = HealthState(
        verdict="degraded", blocked_count=1, in_flight_count=0,
        findings=[finding], computed_at=datetime.now(UTC).isoformat(),
    )
    result = _prepare_health_state(state, [finding])
    assert result is not None
    assert result.verdict == "degraded"


def test_health_endpoint_includes_all_expected_keys(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    for key in (
        "ok",
        "queue_paused",
        "paused_until",
        "health_state",
        "last_computed_at",
        "dispatch_loops_alive",
        "dispatch_loops_configured",
        "finisher_rate",
        "quality_metrics",
        "digest_overdue",
        "sweeper",
        "served_sha",
    ):
        assert key in data


def _register_default_engine() -> None:
    engine_config_mod.register(
        engine_config_mod.default_engine_config(), default=True, validate_env=False
    )


def _repo_finding(
    finding_id: str, *, severity: str = "high", engine_key: str | None = None
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "kind": "build_task",
        "severity": severity,
        "description": "d",
        "workspace_id": None,
        "pr_number": None,
        "blocker_flags": [],
        "action": "dispatch_remediation",
        "engine_key": engine_key,
    }


def test_health_repos_includes_default_repo_with_weighted_findings(tmp_path: Path) -> None:
    _register_default_engine()
    findings = [
        _repo_finding("f1", severity="critical"),
        _repo_finding("f2", severity="medium"),
    ]
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            server.app.state.queue.set_meta(
                HEALTH_META_KEY, _health_state_json("degraded", findings)
            )
            res = client.get("/api/health/repos")
    finally:
        server.app.dependency_overrides.clear()
    assert res.status_code == 200
    data = res.json()
    assert data["computed_at"] is not None
    row = next(r for r in data["rows"] if r["repo"] == "DejvMatikar/alan")
    assert row["outstanding_findings"]["value"] == pytest.approx(4.0)
    assert row["outstanding_findings"]["sample"] == 2


def test_health_repos_hidden_repo_omitted(tmp_path: Path) -> None:
    _register_default_engine()
    engine_config_mod.register(
        engine_config_mod.EngineConfig(
            repo=engine_config_mod.RepoIdentity(owner="sprocket-co", repo="sprocket"),
            hidden=True,
        )
    )
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json("healthy", []))
            res = client.get("/api/health/repos")
    finally:
        server.app.dependency_overrides.clear()
    slugs = {r["repo"] for r in res.json()["rows"]}
    assert "sprocket-co/sprocket" not in slugs


def test_health_repos_includes_visible_registered_repo(tmp_path: Path) -> None:
    _register_default_engine()
    engine_config_mod.register(
        engine_config_mod.EngineConfig(
            repo=engine_config_mod.RepoIdentity(owner="sprocket-co", repo="sprocket")
        )
    )
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json("healthy", []))
            res = client.get("/api/health/repos")
    finally:
        server.app.dependency_overrides.clear()
    slugs = {r["repo"] for r in res.json()["rows"]}
    assert "sprocket-co/sprocket" in slugs
