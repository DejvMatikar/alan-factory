from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from alan import reconciler as _reconciler_mod
from alan import server
from alan.digest import DigestDoc, RecoveryTrendEntry, ResidueEntry, digest_to_dict
from alan.digest_sweep import DIGEST_LAST_DATE_META_KEY, DIGEST_LATEST_META_KEY
from alan.routes import health as health_mod

_NOW = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture(autouse=True)
def _push_digest_deadline_far_future(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        health_mod, "resolve_digest_deadline", lambda now: now + timedelta(days=3650)
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _digest(
    recovery_trend: tuple[RecoveryTrendEntry, ...] = (),
    residue: tuple[ResidueEntry, ...] = (),
) -> DigestDoc:
    return DigestDoc(
        since="2026-07-04T09:00:00+00:00",
        generated_at=_NOW.isoformat(),
        opened=(),
        closed=(),
        recovery_trend=recovery_trend,
        suppressions=(),
        residue=residue,
        cost_note="note",
        fingerprints=frozenset(),
    )


def _seed_digest(doc: DigestDoc) -> None:
    server.app.state.queue.set_meta(DIGEST_LATEST_META_KEY, json.dumps(digest_to_dict(doc)))


def test_recovery_is_null_when_digest_never_produced(client: TestClient) -> None:
    res = client.get("/api/health/digest")
    assert res.status_code == 200
    data = res.json()
    assert data["recovery"] is None
    assert data["residue"] is None


def test_recovery_is_null_when_stored_digest_is_unreadable(client: TestClient) -> None:
    server.app.state.queue.set_meta(DIGEST_LATEST_META_KEY, "not json{{{")
    res = client.get("/api/health/digest")
    assert res.json()["recovery"] is None
    assert res.json()["residue"] is None


def test_recovery_totals_sum_across_incident_classes(client: TestClient) -> None:
    _seed_digest(
        _digest(
            recovery_trend=(
                RecoveryTrendEntry(
                    incident_class="self_heal", current_count=3, previous_count=1, direction="up"
                ),
                RecoveryTrendEntry(
                    incident_class="merge_gate",
                    current_count=1,
                    previous_count=1,
                    direction="same",
                ),
            )
        )
    )
    res = client.get("/api/health/digest")
    recovery = res.json()["recovery"]
    assert recovery == {"current_count": 4, "previous_count": 2, "direction": "up"}


def test_recovery_zero_is_measured_not_null_when_no_recovery_events(client: TestClient) -> None:
    _seed_digest(_digest(recovery_trend=()))
    res = client.get("/api/health/digest")
    recovery = res.json()["recovery"]
    assert recovery == {"current_count": 0, "previous_count": 0, "direction": "same"}


def test_per_class_recovery_trend_array_does_not_cross_api_boundary(client: TestClient) -> None:
    _seed_digest(
        _digest(
            recovery_trend=(
                RecoveryTrendEntry(
                    incident_class="self_heal", current_count=1, previous_count=0, direction="up"
                ),
            )
        )
    )
    res = client.get("/api/health/digest")
    assert "recovery_trend" not in res.json()
    assert set(res.json()["recovery"]) == {"current_count", "previous_count", "direction"}


def test_residue_served_verbatim_with_only_fingerprint_and_title(client: TestClient) -> None:
    _seed_digest(
        _digest(residue=(ResidueEntry(fingerprint="fp-1", title="Unclassified anomaly: ws-1"),))
    )
    res = client.get("/api/health/digest")
    residue = res.json()["residue"]
    assert residue == [{"fingerprint": "fp-1", "title": "Unclassified anomaly: ws-1"}]
    assert set(residue[0]) == {"fingerprint", "title"}


def test_residue_is_empty_list_when_digest_has_no_residue(client: TestClient) -> None:
    _seed_digest(_digest(residue=()))
    res = client.get("/api/health/digest")
    assert res.json()["residue"] == []


def test_digest_overdue_true_when_past_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health_mod, "resolve_digest_deadline", lambda now: now - timedelta(hours=1)
    )
    res = client.get("/api/health/digest")
    assert res.json()["digest_overdue"] is True


def test_digest_overdue_false_when_today_already_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health_mod, "resolve_digest_deadline", lambda now: now - timedelta(hours=1)
    )
    today_key = (datetime.now(UTC) - timedelta(hours=1)).date().isoformat()
    server.app.state.queue.set_meta(DIGEST_LAST_DATE_META_KEY, today_key)
    res = client.get("/api/health/digest")
    assert res.json()["digest_overdue"] is False


def test_digest_overdue_false_before_deadline(client: TestClient) -> None:
    res = client.get("/api/health/digest")
    assert res.json()["digest_overdue"] is False
