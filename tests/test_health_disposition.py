from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from alan import reconciler as _reconciler_mod
from alan import server
from alan.incident import Disposition
from alan.incident_store import load_dispositions


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def test_post_disposition_persists_and_returns_closed_at(client: TestClient) -> None:
    res = client.post(
        "/api/health/incidents/fp-1/disposition", json={"disposition": "root_caused"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data == {
        "fingerprint": "fp-1",
        "disposition": "root_caused",
        "closed_at": data["closed_at"],
    }
    assert data["closed_at"] != ""

    record = load_dispositions(server.app.state.queue)["fp-1"]
    assert record.disposition == Disposition.ROOT_CAUSED


def test_post_disposition_creates_a_record_for_an_unseen_fingerprint(client: TestClient) -> None:
    assert "fp-new" not in load_dispositions(server.app.state.queue)
    res = client.post(
        "/api/health/incidents/fp-new/disposition", json={"disposition": "known_flake"}
    )
    assert res.status_code == 200
    assert "fp-new" in load_dispositions(server.app.state.queue)


def test_post_disposition_rejects_unknown_value(client: TestClient) -> None:
    res = client.post(
        "/api/health/incidents/fp-1/disposition", json={"disposition": "not_a_real_value"}
    )
    assert res.status_code == 422
    assert "fp-1" not in load_dispositions(server.app.state.queue)


def test_post_disposition_missing_body_field_is_422(client: TestClient) -> None:
    res = client.post("/api/health/incidents/fp-1/disposition", json={})
    assert res.status_code == 422


def test_post_disposition_last_write_wins(client: TestClient) -> None:
    first = client.post(
        "/api/health/incidents/fp-1/disposition", json={"disposition": "root_caused"}
    )
    assert first.status_code == 200
    second = client.post(
        "/api/health/incidents/fp-1/disposition", json={"disposition": "known_flake"}
    )
    assert second.status_code == 200

    record = load_dispositions(server.app.state.queue)["fp-1"]
    assert record.disposition == Disposition.KNOWN_FLAKE


@pytest.mark.parametrize(
    "value",
    [
        "root_caused",
        "mechanically_recovered",
        "known_flake",
        "transient_external",
        "by_design",
        "duplicate_of",
        "needs_human",
    ],
)
def test_post_disposition_accepts_every_disposition_value(client: TestClient, value: str) -> None:
    res = client.post(f"/api/health/incidents/fp-{value}/disposition", json={"disposition": value})
    assert res.status_code == 200
    assert res.json()["disposition"] == value
