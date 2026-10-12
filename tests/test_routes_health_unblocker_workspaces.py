from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import alan.routes.health as health_routes
import alan.unblock_activity as activity_mod
import alan.unblock_ledger as ledger
from alan import engine_config as engine_config_mod
from alan import reconciler as _reconciler_mod
from alan import server
from alan.rca_store import RcaDraft, save_rca_draft
from alan.workspace import Status
from tests.test_routes_health_unblocker_activity import (
    FLEET_KEYS,
    PATH,
    _attempt,
    _queue,
    _workspace,
)

SCOPED = {"engine": "o/alpha"}


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


def _report(workspace_key: str, engine_key: str = "o/alpha", **overrides: object) -> None:
    fields: dict[str, object] = {
        "kind": ledger.KIND_REPORT,
        "shape": "needs_human",
        "outcome": ledger.OUTCOME_NEEDS_HUMAN,
        "action": None,
    }
    fields.update(overrides)
    _attempt(workspace_key, engine_key, **fields)


def _rows(client: TestClient) -> dict[str, dict[str, object]]:
    payload = client.get(PATH, params=SCOPED).json()
    return {row["workspace_key"]: row for row in payload["workspaces"]}


def test_fleet_payload_gains_the_two_counts_and_never_a_workspaces_list(
    client: TestClient, ws_root: Path
) -> None:
    stuck = _workspace(ws_root, "Stuck", "o/alpha", Status.FAILED)
    _attempt(stuck.task_id, "o/alpha", ts=time.time() - 120.0)
    _report(stuck.task_id)
    _attempt("ws-b", "o/beta")

    payload = client.get(PATH).json()

    assert set(payload) == FLEET_KEYS
    assert "workspaces" not in payload
    assert payload["workspaces_touched"] == 2
    assert payload["still_stuck_workspaces"] == 1
    assert payload["gave_up_workspaces"] == 1


def test_scoped_payload_carries_the_two_counts_and_the_workspaces_list(
    client: TestClient, ws_root: Path
) -> None:
    stuck = _workspace(ws_root, "Stuck", "o/alpha", Status.FAILED)
    _attempt(stuck.task_id, "o/alpha", ts=time.time() - 120.0)
    _report(stuck.task_id)
    _attempt("ws-b", "o/beta")

    payload = client.get(PATH, params=SCOPED).json()

    assert payload["still_stuck_workspaces"] == 1
    assert payload["gave_up_workspaces"] == 1
    assert [w["workspace_key"] for w in payload["workspaces"]] == [stuck.task_id]


def test_counts_are_zero_when_nothing_is_stuck_or_given_up(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")

    fleet = client.get(PATH).json()
    scoped = client.get(PATH, params=SCOPED).json()

    assert (fleet["still_stuck_workspaces"], fleet["gave_up_workspaces"]) == (0, 0)
    assert (scoped["still_stuck_workspaces"], scoped["gave_up_workspaces"]) == (0, 0)


def test_scoped_workspace_row_carries_every_aggregate_field(
    client: TestClient, ws_root: Path
) -> None:
    ws = _workspace(ws_root, "Stuck", "o/alpha", Status.FAILED)
    now = time.time()
    _attempt(ws.task_id, "o/alpha", ts=now - 300.0, shape="crash_park")
    _attempt(ws.task_id, "o/alpha", ts=now - 200.0, shape="stale_review")
    _report(ws.task_id, ts=now - 100.0, shape="stale_review")
    ledger.record(
        _queue(),
        ledger.Entry(
            workspace_key=ws.task_id,
            scope_ref="pr:1",
            shape="stale_review",
            fingerprint="fp",
            kind=ledger.KIND_ISSUE_DELIVERED,
            detail={"issue_url": "https://github.com/o/alpha/issues/8"},
        ),
    )
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
            workspace_key=ws.task_id,
            created_at=5.0,
        ),
    )

    row = _rows(client)[ws.task_id]

    assert row == {
        "workspace_key": ws.task_id,
        "last_at": ledger.stamp(now - 100.0),
        "shape": "stale_review",
        "events_total": 3,
        "events_listed": 3,
        "attempts_total": 2,
        "attempts_ceiling": ledger.ATTEMPT_CEILING_PER_WORKSPACE,
        "clearance": "still_stuck",
        "gave_up": True,
        "issue_url": "https://github.com/o/alpha/issues/8",
        "diagnosis": {
            "signature": "fd-exhaustion",
            "proposed_disposition": "root_caused",
            "verified": True,
        },
    }


def test_scoped_workspace_row_reports_cleared_and_gone_workspaces(
    client: TestClient, ws_root: Path
) -> None:
    done = _workspace(ws_root, "Done", "o/alpha", Status.COMPLETED)
    _attempt(done.task_id, "o/alpha")
    _attempt("vanished", "o/alpha", fingerprint="fp-2")

    rows = _rows(client)

    assert rows[done.task_id]["clearance"] == "cleared"
    assert rows["vanished"]["clearance"] == "unknown"
    assert rows[done.task_id]["gave_up"] is False
    assert rows[done.task_id]["issue_url"] is None
    assert rows[done.task_id]["diagnosis"] is None


def test_workspace_rows_match_workspaces_touched_when_the_event_list_is_capped(
    client: TestClient,
) -> None:
    now = time.time()
    total = activity_mod.EVENT_LIMIT + 3
    for i in range(total):
        _attempt(f"ws-{i}", "o/alpha", ts=now - 10.0 - i)

    payload = client.get(PATH, params=SCOPED).json()

    assert len(payload["events"]) == activity_mod.EVENT_LIMIT
    assert len(payload["workspaces"]) == payload["workspaces_touched"] == total
    assert payload["workspaces"][0]["workspace_key"] == "ws-0"
    assert payload["workspaces"][-1]["events_listed"] == 0


def test_workspace_row_states_how_many_of_its_events_the_event_list_holds(
    client: TestClient,
) -> None:
    now = time.time()
    for i in range(activity_mod.EVENT_LIMIT):
        _attempt("ws-busy", "o/alpha", ts=now - 10.0 - i)
    _attempt("ws-quiet", "o/alpha", fingerprint="fp-q", ts=now - 5000.0)
    _attempt("ws-busy", "o/alpha", ts=now - 4000.0)

    rows = _rows(client)

    assert rows["ws-busy"]["events_total"] == activity_mod.EVENT_LIMIT + 1
    assert rows["ws-busy"]["events_listed"] == activity_mod.EVENT_LIMIT
    assert rows["ws-quiet"]["events_total"] == 1
    assert rows["ws-quiet"]["events_listed"] == 0


def test_attempts_total_is_served_unclamped_against_its_ceiling(client: TestClient) -> None:
    now = time.time()
    for i in range(ledger.ATTEMPT_CEILING_PER_WORKSPACE + 2):
        _attempt("ws-a", "o/alpha", ts=now - 100.0 + i)

    row = _rows(client)["ws-a"]

    assert row["attempts_total"] == ledger.ATTEMPT_CEILING_PER_WORKSPACE + 2
    assert row["attempts_ceiling"] == ledger.ATTEMPT_CEILING_PER_WORKSPACE


def test_workspace_rows_come_newest_activity_first(client: TestClient) -> None:
    now = time.time()
    _attempt("ws-old", "o/alpha", ts=now - 300.0)
    _attempt("ws-new", "o/alpha", ts=now - 100.0)

    payload = client.get(PATH, params=SCOPED).json()

    assert [w["workspace_key"] for w in payload["workspaces"]] == ["ws-new", "ws-old"]


def test_unknown_slug_serves_an_empty_workspaces_list_with_zero_counts(
    client: TestClient,
) -> None:
    _attempt("ws-a", "o/alpha")

    payload = client.get(PATH, params={"engine": "nobody/nothing"}).json()

    assert payload["workspaces"] == []
    assert payload["still_stuck_workspaces"] == 0
    assert payload["gave_up_workspaces"] == 0


def test_the_workspaces_ride_the_existing_endpoint_and_cache_keys(client: TestClient) -> None:
    _attempt("ws-a", "o/alpha")

    client.get(PATH)
    client.get(PATH, params=SCOPED)
    client.get(PATH, params=SCOPED)

    assert len(health_routes._ACTIVITY_CACHE) == 2  # noqa: SLF001
    assert {key[2] for key in health_routes._ACTIVITY_CACHE} == {None, "o/alpha"}  # noqa: SLF001
