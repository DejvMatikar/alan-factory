from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.routes.health as health_routes
import alan.unblock_activity as activity_mod
import alan.unblock_ledger as ledger
from alan import engine_config as engine_config_mod
from alan import reconciler as _reconciler_mod
from alan import server
from alan.queue import Queue
from alan.rca_store import RcaDraft, save_rca_draft
from alan.workspace import Status, Workspace
from alan.workspace import create as create_ws
from alan.workspace import transition as update_ws_status

PATH = "/api/health/unblocker-activity"
FLEET_KEYS = {
    "scope",
    "window_days",
    "events_total",
    "workspaces_touched",
    "waited_workspaces",
    "diagnosed_workspaces",
    "diagnosable_workspaces",
    "still_stuck_workspaces",
    "gave_up_workspaces",
    "aggregates",
    "ever_recorded",
    "unattributed_events",
}


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture(autouse=True)
def _fresh_activity_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_routes, "_ACTIVITY_CACHE", {})


@pytest.fixture(autouse=True)
def _registered_repos() -> None:
    engine_config_mod.register(
        engine_config_mod.default_engine_config(), default=True, validate_env=False
    )
    for repo in ("alpha", "beta"):
        engine_config_mod.register(
            engine_config_mod.EngineConfig(
                repo=engine_config_mod.RepoIdentity(owner="o", repo=repo)
            ),
            validate_env=False,
        )
    engine_config_mod.register(
        engine_config_mod.EngineConfig(
            repo=engine_config_mod.RepoIdentity(owner="o", repo="secret"), hidden=True
        ),
        validate_env=False,
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(ws_root: Path) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _queue() -> Queue:
    queue: Queue = server.app.state.queue
    ledger.ensure_schema(queue)
    return queue


def _attempt(workspace_key: str, engine_key: str, **overrides: object) -> None:
    fields: dict[str, object] = {
        "engine_key": engine_key,
        "workspace_key": workspace_key,
        "scope_ref": "pr:1",
        "shape": "stale_review",
        "fingerprint": f"fp-{workspace_key}",
        "kind": ledger.KIND_ATTEMPT,
        "action": "dispatch:pr_merge",
        "outcome": ledger.OUTCOME_ACTED,
        "ts": time.time() - 60.0,
    }
    fields.update(overrides)
    ledger.record(_queue(), ledger.Entry(**fields))  # type: ignore[arg-type]


def _workspace(ws_root: Path, title: str, engine_key: str, status: Status) -> Workspace:
    ws = create_ws(title, "d", root=ws_root, engine_key=engine_key)
    update_ws_status(ws, status)
    return ws


def test_fleet_payload_has_only_aggregate_fields(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")

    payload = client.get(PATH).json()

    assert set(payload) == FLEET_KEYS
    assert payload["scope"] == "fleet"
    assert payload["events_total"] == 1


def test_fleet_payload_never_carries_workspace_or_issue_identifiers(client: TestClient) -> None:
    _attempt("ws-secret-key", "o/alpha")
    ledger.record(
        _queue(),
        ledger.Entry(
            workspace_key="ws-secret-key",
            scope_ref="pr:1",
            shape="stale_review",
            fingerprint="fp",
            kind=ledger.KIND_ISSUE_DELIVERED,
            detail={"issue_url": "https://github.com/o/alpha/issues/1"},
        ),
    )

    raw = client.get(PATH).text

    assert "ws-secret-key" not in raw
    assert "issue_url" not in raw
    assert "workspace_key" not in raw
    assert '"events"' not in raw
    assert "o/alpha" not in raw


def test_fleet_aggregates_group_by_shape_with_separate_outcome_tallies(
    client: TestClient,
) -> None:
    _attempt("ws-a", "o/alpha")
    _attempt("ws-b", "o/beta", outcome="failed: KeyError")
    _attempt(
        "ws-c",
        "o/beta",
        kind=ledger.KIND_REPORT,
        shape="needs_human",
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
    )

    aggregates = {a["shape"]: a for a in client.get(PATH).json()["aggregates"]}

    assert aggregates["stale_review"]["attempt_outcomes"] == [
        {"outcome": "acted", "count": 1},
        {"outcome": "failed", "count": 1},
    ]
    assert aggregates["stale_review"]["report_outcomes"] == []
    assert aggregates["needs_human"]["attempt_outcomes"] == []
    assert aggregates["needs_human"]["report_outcomes"] == [{"outcome": "needs_human", "count": 1}]


def test_fleet_view_excludes_hidden_engines(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")
    _attempt("ws-s", "o/secret")

    assert client.get(PATH).json()["events_total"] == 1


def test_scoped_view_serves_a_hidden_engine_the_caller_named(client: TestClient) -> None:
    _attempt("ws-s", "o/secret")

    payload = client.get(PATH, params={"engine": "o/secret"}).json()

    assert payload["scope"] == "repo"
    assert [e["workspace_key"] for e in payload["events"]] == ["ws-s"]


def test_scoped_view_holds_only_the_named_repositorys_events(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")
    _attempt("ws-b", "o/beta")

    alpha = client.get(PATH, params={"engine": "o/alpha"}).json()
    beta = client.get(PATH, params={"engine": "o/beta"}).json()

    assert [e["workspace_key"] for e in alpha["events"]] == ["ws-a"]
    assert [e["workspace_key"] for e in beta["events"]] == ["ws-b"]
    assert alpha["repo"] == "o/alpha"
    assert alpha["events_total"] == beta["events_total"] == 1


def test_scoped_event_carries_the_display_fields(client: TestClient, ws_root: Path) -> None:
    ws = _workspace(ws_root, "Cleared", "o/alpha", Status.COMPLETED)
    _attempt(ws.task_id, "o/alpha", detail={"reason": "review went stale"})
    ledger.record(
        _queue(),
        ledger.Entry(
            workspace_key=ws.task_id,
            scope_ref="pr:1",
            shape="stale_review",
            fingerprint="fp",
            kind=ledger.KIND_ISSUE_DELIVERED,
            detail={"issue_url": "https://github.com/o/alpha/issues/3"},
        ),
    )

    (event,) = client.get(PATH, params={"engine": "o/alpha"}).json()["events"]

    assert event["kind"] == "attempt"
    assert event["shape"] == "stale_review"
    assert event["action"] == "dispatch:pr_merge"
    assert event["outcome"] == "acted"
    assert event["reason"] == "review went stale"
    assert event["clearance"] == "cleared"
    assert event["issue_url"] == "https://github.com/o/alpha/issues/3"
    assert event["attempt"] == {
        "fingerprint_index": 1,
        "fingerprint_ceiling": ledger.ATTEMPT_CEILING_PER_FINGERPRINT,
        "workspace_index": 1,
        "workspace_ceiling": ledger.ATTEMPT_CEILING_PER_WORKSPACE,
    }
    assert event["diagnosis"] is None
    assert len(event["at"]) == len("2026-01-01T00:00Z")
    assert event["at"].endswith("Z")


def test_scoped_report_event_has_no_attempt_index_and_carries_the_attempt_total(
    client: TestClient,
) -> None:
    _attempt("ws-a", "o/alpha", ts=time.time() - 120.0)
    _attempt(
        "ws-a",
        "o/alpha",
        kind=ledger.KIND_REPORT,
        shape="needs_human",
        outcome=ledger.OUTCOME_NEEDS_HUMAN,
        action=None,
        detail={"reason": "no recipe", "authority_holder": "nobody"},
    )

    report = client.get(PATH, params={"engine": "o/alpha"}).json()["events"][0]

    assert report["kind"] == "report"
    assert report["attempt"] is None
    assert report["attempts_total"] == 1
    assert report["authority_holder"] == "nobody"


def test_scoped_event_carries_the_latest_diagnosis_for_its_workspace(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")
    save_rca_draft(
        _queue(),
        "fp-x",
        RcaDraft(
            source="investigator",
            proposed_disposition="root_caused",
            timeline=(),
            falsification_test="rerun",
            recurrence_signature="fd-exhaustion",
            recipe="",
            recurrence_signal="s",
            verified=True,
            workspace_key="ws-a",
            created_at=5.0,
        ),
    )

    payload = client.get(PATH, params={"engine": "o/alpha"}).json()

    assert payload["events"][0]["diagnosis"] == {
        "signature": "fd-exhaustion",
        "proposed_disposition": "root_caused",
        "verified": True,
    }
    assert (payload["diagnosed_workspaces"], payload["diagnosable_workspaces"]) == (1, 1)


def test_unknown_slug_returns_a_well_formed_empty_scoped_payload(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")

    res = client.get(PATH, params={"engine": "nobody/nothing"})

    assert res.status_code == 200
    payload = res.json()
    assert payload["scope"] == "repo"
    assert payload["events"] == []
    assert payload["events_total"] == 0
    assert payload["fleet_ever_recorded"] is True


def test_empty_ledger_reports_never_recorded(client: TestClient) -> None:
    payload = client.get(PATH).json()

    assert payload["ever_recorded"] is False
    assert payload["events_total"] == 0


def test_only_old_history_reports_recorded_with_an_empty_window(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha", ts=time.time() - (ledger.RESCUE_WINDOW_DAYS + 5) * ledger.DAY_S)

    payload = client.get(PATH).json()

    assert payload["ever_recorded"] is True
    assert payload["events_total"] == 0
    assert payload["window_days"] == ledger.RESCUE_WINDOW_DAYS


def test_scoped_event_list_is_capped_while_the_total_is_reported(client: TestClient) -> None:
    now = time.time()
    for i in range(activity_mod.EVENT_LIMIT + 2):
        _attempt(f"ws-{i}", "o/alpha", ts=now - 10.0 - i)

    payload = client.get(PATH, params={"engine": "o/alpha"}).json()

    assert payload["events_total"] == activity_mod.EVENT_LIMIT + 2
    assert len(payload["events"]) == activity_mod.EVENT_LIMIT
    assert payload["events"][0]["workspace_key"] == "ws-0"


def test_cache_does_not_serve_one_repositorys_payload_for_another(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")
    _attempt("ws-b", "o/beta")

    first = client.get(PATH, params={"engine": "o/alpha"}).json()
    second = client.get(PATH, params={"engine": "o/beta"}).json()
    fleet = client.get(PATH).json()

    assert first["events"][0]["workspace_key"] == "ws-a"
    assert second["events"][0]["workspace_key"] == "ws-b"
    assert fleet["scope"] == "fleet"


def test_cache_serves_a_repeat_request_without_recomputing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str | None] = []
    original = health_routes._compute_activity  # noqa: SLF001

    def spy(queue: Any, root: Any, slug: str | None) -> Any:
        calls.append(slug)
        return original(queue, root, slug)

    monkeypatch.setattr(health_routes, "_compute_activity", spy)

    client.get(PATH)
    client.get(PATH)
    client.get(PATH, params={"engine": "o/alpha"})

    assert calls == [None, "o/alpha"]


def test_cache_recomputes_once_the_ttl_has_elapsed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str | None] = []
    original = health_routes._compute_activity  # noqa: SLF001

    def spy(queue: Any, root: Any, slug: str | None) -> Any:
        calls.append(slug)
        return original(queue, root, slug)

    monkeypatch.setattr(health_routes, "_compute_activity", spy)
    monkeypatch.setattr(health_routes, "_ACTIVITY_TTL_S", 0.0)

    client.get(PATH)
    client.get(PATH)

    assert calls == [None, None]


def test_cold_compute_runs_without_holding_the_cache_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    held_during_compute: list[bool] = []
    original = health_routes._compute_activity  # noqa: SLF001

    def spy(queue: Any, root: Any, slug: str | None) -> Any:
        held_during_compute.append(health_routes._ACTIVITY_LOCK.locked())  # noqa: SLF001
        return original(queue, root, slug)

    monkeypatch.setattr(health_routes, "_compute_activity", spy)

    client.get(PATH)

    assert held_during_compute == [False]


def test_cache_drops_expired_entries_when_a_new_key_is_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get(PATH, params={"engine": "o/alpha"})
    client.get(PATH, params={"engine": "o/beta"})
    assert len(health_routes._ACTIVITY_CACHE) == 2  # noqa: SLF001

    monkeypatch.setattr(health_routes, "_ACTIVITY_TTL_S", 0.0)
    client.get(PATH)

    assert len(health_routes._ACTIVITY_CACHE) == 1  # noqa: SLF001


def test_cache_never_holds_more_than_the_cap_of_live_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_routes, "_ACTIVITY_CACHE_MAX", 2)

    for slug in ("o/alpha", "o/beta", "o/gamma", "o/delta"):
        client.get(PATH, params={"engine": slug})

    assert len(health_routes._ACTIVITY_CACHE) == 2  # noqa: SLF001
    assert {key[2] for key in health_routes._ACTIVITY_CACHE} == {"o/gamma", "o/delta"}  # noqa: SLF001


def test_only_get_is_registered_on_the_activity_path() -> None:
    assert set(server.app.openapi()["paths"][PATH]) == {"get"}


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutating_verbs_are_rejected(client: TestClient, method: str) -> None:
    res = getattr(client, method)(PATH)

    assert res.status_code == 405


def test_reading_the_activity_never_writes_to_the_ledger(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")
    before = json.dumps([tuple(r) for r in _queue().run("SELECT * FROM unblock_ledger")])

    client.get(PATH)
    client.get(PATH, params={"engine": "o/alpha"})

    after = json.dumps([tuple(r) for r in _queue().run("SELECT * FROM unblock_ledger")])
    assert before == after
