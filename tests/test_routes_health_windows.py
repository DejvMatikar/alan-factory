from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.routes.health as health_routes
import alan.unblock_ledger as unblock_ledger_mod
import alan.workspace as workspace_mod
from alan import engine_config as engine_config_mod
from alan import reconciler as _reconciler_mod
from alan import server
from alan.health import HEALTH_META_KEY
from alan.health_repo_metrics import BLOCK_CORRECTION_AUDIT_FILE
from alan.queue import Queue
from alan.roots import logs_root
from alan.workspace import Status, Task, TaskDoDClause, TaskStatus

_DEFAULT_SLUG = "DejvMatikar/alan"
_SPROCKET_SLUG = "sprocket-co/sprocket"
_METRIC_NAMES = (
    "outcome",
    "autonomous",
    "build_success",
    "first_pass_approval",
    "unproven_clauses",
    "format_error",
    "correction_recovery",
    "rescue_success",
    "rescue_load",
)
_LOWER_IS_BETTER = {"format_error", "unproven_clauses"}
_FUNNEL_KEYS = {
    "dispatched",
    "not_crashed",
    "not_needed_human",
    "converged",
    "merged_pr",
    "dod_evidenced",
    "autonomous",
}


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def repos_client(tmp_path: Path) -> Iterator[TestClient]:
    engine_config_mod.register(
        engine_config_mod.default_engine_config(), default=True, validate_env=False
    )
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            yield client
    finally:
        server.app.dependency_overrides.clear()


def _register_sprocket_engine() -> None:
    engine_config_mod.register(
        engine_config_mod.EngineConfig(
            repo=engine_config_mod.RepoIdentity(owner="sprocket-co", repo="sprocket")
        )
    )


def _build_task() -> Task:
    clause = TaskDoDClause(text="feature", evidence="PR #1")
    return Task(id="t1", team="implementation", status=TaskStatus.COMPLETED, dod_clauses=[clause])


def _build_workspace(
    root: Path, title: str, status: Status, engine_key: str | None = None
) -> workspace_mod.Workspace:
    ws = workspace_mod.create(title, "d", root=root, tasks=[_build_task()], engine_key=engine_key)
    workspace_mod.transition(ws, status)
    return ws


def _write_corrections(rows: list[dict[str, Any]]) -> None:
    path = logs_root() / BLOCK_CORRECTION_AUDIT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _correction(task_id: str, attempts: int, outcome: str) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "site": "decisions.yaml",
        "block": "decisions.yaml",
        "role": "judge",
        "outcome": outcome,
        "attempts": attempts,
        "failure_kind": None,
        "task_id": task_id,
    }


def _row(data: dict[str, Any], slug: str) -> dict[str, Any]:
    return next(r for r in data["rows"] if r["repo"] == slug)


def test_health_repos_serves_windows_with_no_triage_state(repos_client: TestClient) -> None:
    res = repos_client.get("/api/health/repos")
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"computed_at", "metrics_computed_at", "unattributed", "fleet", "rows"}
    assert data["computed_at"] is None
    assert datetime.fromisoformat(data["metrics_computed_at"]).tzinfo is not None
    assert data["unattributed"] == 0
    row = _row(data, _DEFAULT_SLUG)
    assert row["outstanding_findings"] == {"value": None, "sample": 0, "low_data": False}
    assert row["in_flight"] == {"value": 0, "sample": 0, "low_data": False}


def test_health_repos_keeps_the_triage_timestamp_when_state_exists(
    repos_client: TestClient,
) -> None:
    computed_at = datetime.now(UTC).isoformat()
    server.app.state.queue.set_meta(
        HEALTH_META_KEY,
        json.dumps({
            "verdict": "healthy",
            "blocked_count": 0,
            "in_flight_count": 0,
            "findings": [],
            "computed_at": computed_at,
        }),
    )
    data = repos_client.get("/api/health/repos").json()
    assert data["computed_at"] == computed_at
    row = _row(data, _DEFAULT_SLUG)
    assert row["outstanding_findings"] == {"value": 0.0, "sample": 0, "low_data": False}


def test_health_repos_rows_and_fleet_carry_all_nine_windowed_metrics(
    repos_client: TestClient,
) -> None:
    data = repos_client.get("/api/health/repos").json()
    row = _row(data, _DEFAULT_SLUG)
    assert set(row) == {"repo", "outstanding_findings", "in_flight", "windows"}
    for scope in (data["fleet"], row["windows"]):
        assert set(scope) == {*_METRIC_NAMES, "funnel"}
        for name in _METRIC_NAMES:
            metric = scope[name]
            assert set(metric) == {"d7", "d30", "verdict", "higher_is_better"}
            assert set(metric["d7"]) == {"value", "sample", "covered_days"}
            assert set(metric["d30"]) == {"value", "sample", "covered_days"}
            assert metric["higher_is_better"] is (name not in _LOWER_IS_BETTER)
        assert set(scope["funnel"]) == {"d7", "d30"}
        assert set(scope["funnel"]["d30"]) == _FUNNEL_KEYS


def test_health_repos_row_has_no_flat_per_row_rate_fields(repos_client: TestClient) -> None:
    row = _row(repos_client.get("/api/health/repos").json(), _DEFAULT_SLUG)
    for retired in (
        "build_success_rate",
        "outcome_rate_30d",
        "autonomous_rate_30d",
        "first_pass_approval_rate",
        "rescue_success_rate",
        "rescue_load_rate",
    ):
        assert retired not in row


def test_health_repos_repository_with_no_activity_has_empty_windows(
    repos_client: TestClient,
) -> None:
    _register_sprocket_engine()
    data = repos_client.get("/api/health/repos").json()
    windows = _row(data, _SPROCKET_SLUG)["windows"]
    for name in _METRIC_NAMES:
        for span in ("d7", "d30"):
            assert windows[name][span]["value"] is None
            assert windows[name][span]["sample"] == 0
        assert windows[name]["verdict"] == "insufficient"
    assert windows["funnel"]["d30"]["dispatched"] == 0


def test_health_repos_counts_finished_builds_in_each_window_and_in_flight_now(
    repos_client: TestClient, tmp_path: Path
) -> None:
    _build_workspace(tmp_path, "done", Status.COMPLETED)
    _build_workspace(tmp_path, "live", Status.ACTIVE)
    data = repos_client.get("/api/health/repos").json()
    row = _row(data, _DEFAULT_SLUG)
    build = row["windows"]["build_success"]
    assert build["d7"]["sample"] == 1
    assert build["d7"]["value"] == pytest.approx(1.0)
    assert build["d30"]["sample"] == 1
    assert build["verdict"] == "insufficient"
    assert row["in_flight"] == {"value": 1, "sample": 1, "low_data": False}
    assert data["fleet"]["build_success"]["d30"]["sample"] == 1


def test_health_repos_attributes_correction_rows_only_to_known_workspaces(
    repos_client: TestClient, tmp_path: Path
) -> None:
    ws = _build_workspace(tmp_path, "done", Status.COMPLETED)
    _write_corrections([
        _correction(ws.task_id, 1, "clean"),
        _correction(ws.task_id, 1, "repaired"),
        _correction("ghost-task", 1, "repaired"),
        _correction("ghost-task", 2, "corrected"),
    ])
    data = repos_client.get("/api/health/repos").json()
    repo_error = _row(data, _DEFAULT_SLUG)["windows"]["format_error"]["d30"]
    assert repo_error["sample"] == 2
    assert repo_error["value"] == pytest.approx(0.5)
    fleet_error = data["fleet"]["format_error"]["d30"]
    assert fleet_error["sample"] == 3
    assert fleet_error["value"] == pytest.approx(2 / 3)
    repo_recovery = _row(data, _DEFAULT_SLUG)["windows"]["correction_recovery"]["d30"]
    assert repo_recovery["sample"] == 0
    assert data["fleet"]["correction_recovery"]["d30"]["sample"] == 1
    assert data["unattributed"] == 2


def test_health_repos_rescue_windows_reflect_a_cleared_engaged_workspace(
    repos_client: TestClient, tmp_path: Path
) -> None:
    ws = workspace_mod.create("Rescued WS", "d", root=tmp_path)
    unblock_ledger_mod.ensure_schema(server.app.state.queue)
    unblock_ledger_mod.record(
        server.app.state.queue,
        unblock_ledger_mod.Entry(
            workspace_key=ws.task_id,
            scope_ref=f"ws:{ws.task_id}",
            shape="crash_park",
            fingerprint="fp-1",
            kind=unblock_ledger_mod.KIND_ATTEMPT,
            action="dispatch:verification",
            outcome=unblock_ledger_mod.OUTCOME_ACTED,
        ),
    )
    workspace_mod.transition(ws, Status.COMPLETED)
    windows = _row(repos_client.get("/api/health/repos").json(), _DEFAULT_SLUG)["windows"]
    for span in ("d7", "d30"):
        assert windows["rescue_success"][span]["value"] == pytest.approx(1.0)
        assert windows["rescue_success"][span]["sample"] == 1
        assert windows["rescue_load"][span]["value"] == pytest.approx(1.0)
        assert windows["rescue_load"][span]["sample"] == 1
    assert windows["rescue_success"]["d30"]["covered_days"] == 30
    assert windows["rescue_success"]["d7"]["covered_days"] == 7


def test_health_repos_reports_a_shorter_covered_period_when_history_is_short(
    repos_client: TestClient, tmp_path: Path
) -> None:
    _build_workspace(tmp_path, "done", Status.COMPLETED)
    windows = _row(repos_client.get("/api/health/repos").json(), _DEFAULT_SLUG)["windows"]
    assert windows["build_success"]["d30"]["covered_days"] < 30
    assert windows["build_success"]["d7"]["covered_days"] < 7


def test_cached_repo_metrics_hit_then_ttl_expiry(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = health_routes._compute_repo_metrics  # noqa: SLF001

    def spy(q: Any, root: Any) -> Any:
        calls.append(1)
        return original(q, root)

    monkeypatch.setattr(health_routes, "_REPO_METRICS_CACHE", {})
    monkeypatch.setattr(health_routes, "_compute_repo_metrics", spy)
    first = health_routes._cached_repo_metrics(queue, ws_root)  # noqa: SLF001
    second = health_routes._cached_repo_metrics(queue, ws_root)  # noqa: SLF001
    assert len(calls) == 1
    assert first is second
    monkeypatch.setattr(health_routes, "_REPO_METRICS_TTL_S", 0.0)
    health_routes._cached_repo_metrics(queue, ws_root)  # noqa: SLF001
    assert len(calls) == 2


def test_compute_repo_metrics_swallows_list_workspaces_exception(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("disk error")

    monkeypatch.setattr(workspace_mod, "list_workspaces", _boom)
    bundle = health_routes._compute_repo_metrics(queue, tmp_path)  # noqa: SLF001
    assert dict(bundle.windows.repos) == {}
    assert dict(bundle.in_flight) == {}
    assert bundle.windows.fleet.outcome.d30.sample == 0
