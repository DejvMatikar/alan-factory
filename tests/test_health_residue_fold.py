from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from alan import reconciler as _reconciler_mod
from alan import server
from alan.digest import DigestDoc, ResidueEntry, digest_to_dict
from alan.digest_sweep import DIGEST_LATEST_META_KEY
from alan.health import HealthFinding, HealthState, save_health_state
from alan.queue import Queue
from alan.rca_store import RcaDraft, save_rca_draft


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _seed_healthy_state(queue: Queue) -> None:
    save_health_state(
        queue,
        HealthState(
            verdict="healthy",
            blocked_count=0,
            in_flight_count=0,
            findings=[],
            computed_at=datetime.now(UTC).isoformat(),
        ),
    )


def _seed_state_with_one_stored_finding(queue: Queue) -> None:
    finding = HealthFinding(
        finding_id="stored-1",
        kind="build_task",
        severity="high",
        description="stored finding",
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
    )
    save_health_state(
        queue,
        HealthState(
            verdict="degraded",
            blocked_count=1,
            in_flight_count=0,
            findings=[finding],
            computed_at=datetime.now(UTC).isoformat(),
        ),
    )


def _seed_digest_residue(
    queue: Queue, *, fingerprint: str = "fp-residue", title: str = "Unclassified anomaly"
) -> None:
    doc = DigestDoc(
        since="2026-01-01T00:00:00+00:00",
        generated_at=datetime.now(UTC).isoformat(),
        opened=(),
        closed=(),
        recovery_trend=(),
        suppressions=(),
        residue=(ResidueEntry(fingerprint=fingerprint, title=title),),
        cost_note="note",
        fingerprints=frozenset(),
    )
    queue.set_meta(DIGEST_LATEST_META_KEY, json.dumps(digest_to_dict(doc)))


def _seed_pending_rca(queue: Queue, fingerprint: str = "fp-rca") -> None:
    save_rca_draft(
        queue,
        fingerprint,
        RcaDraft(
            source="investigator",
            proposed_disposition="root_caused",
            timeline=(),
            falsification_test="rerun and confirm",
            recurrence_signature="sig",
            recipe="recipe",
            recurrence_signal="signal",
        ),
    )


def test_digest_residue_no_longer_surfaces_as_an_unclassified_anomaly_finding(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    _seed_healthy_state(queue)
    _seed_digest_residue(queue)

    data = client.get("/api/health").json()

    assert data["health_state"]["findings"] == []
    assert data["health_state"]["blocked_count"] == 0
    assert data["health_state"]["verdict"] == "healthy"


def test_pending_rca_draft_no_longer_surfaces_as_a_pending_disposition_finding(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    _seed_healthy_state(queue)
    _seed_pending_rca(queue)

    data = client.get("/api/health").json()

    assert data["health_state"]["findings"] == []
    assert data["health_state"]["blocked_count"] == 0


def test_blocked_count_drops_by_exactly_the_two_removed_synthetic_findings(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    _seed_state_with_one_stored_finding(queue)
    _seed_digest_residue(queue)
    _seed_pending_rca(queue)

    data = client.get("/api/health").json()

    previous_blocked_count = 1 + 2
    removed_findings = 2
    assert data["health_state"]["blocked_count"] == previous_blocked_count - removed_findings
    assert [f["finding_id"] for f in data["health_state"]["findings"]] == ["stored-1"]


def test_no_emitted_finding_carries_a_removed_kind(client: TestClient) -> None:
    queue = server.app.state.queue
    _seed_state_with_one_stored_finding(queue)
    _seed_digest_residue(queue)
    _seed_pending_rca(queue)

    data = client.get("/api/health").json()

    kinds = {f["kind"] for f in data["health_state"]["findings"]}
    assert kinds.isdisjoint({"unclassified_anomaly", "pending_disposition"})


def test_disposition_route_still_records_a_disposition_without_a_ui_caller(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    _seed_healthy_state(queue)

    resp = client.post(
        "/api/health/incidents/fp-rca/disposition", json={"disposition": "root_caused"}
    )

    assert resp.status_code == 200
    assert resp.json()["disposition"] == "root_caused"


def test_synthetic_findings_absent_entirely_when_health_state_is_stale(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    _seed_digest_residue(queue)

    data = client.get("/api/health").json()

    assert data["health_state"] is None
