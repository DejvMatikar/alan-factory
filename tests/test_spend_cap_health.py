from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import reconciler as _reconciler_mod
from alan.health import (
    _STALE_SECONDS,
    HEALTH_META_KEY,
    SPEND_CAP_CAUSE,
    SPEND_CAP_FIX,
    SpendCapStop,
    load_spend_cap_stop,
)
from alan.queue import SPEND_CAP_STOP_META_KEY, Queue
from alan.usage_limit import SPEND_CAP_HOLD_EPOCH

_REGAIN = "2026-10-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def q(tmp_path: Path) -> Iterator[Queue]:
    instance = Queue(db_path=tmp_path / "unit.db")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _health_state_json(computed_at: datetime) -> str:
    return json.dumps({
        "verdict": "healthy",
        "blocked_count": 0,
        "in_flight_count": 0,
        "findings": [],
        "computed_at": computed_at.isoformat(),
    })


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def test_load_spend_cap_stop_is_absent_without_a_marker(q: Queue) -> None:
    assert load_spend_cap_stop(q) is None


def test_load_spend_cap_stop_reports_alan_wording_and_the_regain_date(q: Queue) -> None:
    q.stop_until_resume(_REGAIN)
    assert load_spend_cap_stop(q) == SpendCapStop(
        cause=SPEND_CAP_CAUSE, fix=SPEND_CAP_FIX, regain_at=_REGAIN
    )


def test_load_spend_cap_stop_omits_the_regain_date_when_none_was_stated(q: Queue) -> None:
    q.stop_until_resume(None)
    stop = load_spend_cap_stop(q)
    assert stop is not None
    assert stop.regain_at is None


def test_spend_cap_wording_names_the_cause_and_the_fix() -> None:
    assert "spend limit" in SPEND_CAP_CAUSE
    assert "Raise the spend limit" in SPEND_CAP_FIX
    assert "wait until access returns" in SPEND_CAP_FIX
    assert "Resume" in SPEND_CAP_FIX


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="empty-string"),
        pytest.param("not json", id="not-json"),
        pytest.param("[]", id="json-array"),
        pytest.param('"text"', id="json-string"),
        pytest.param('{"regain_at": 5, "stopped_at": 1.0}', id="numeric-regain"),
        pytest.param('{"regain_at": "banana", "stopped_at": 1.0}', id="unparseable-regain"),
        pytest.param(
            '{"regain_at": "2026-10-01T00:00:00", "stopped_at": 1.0}',
            id="regain-without-timezone",
        ),
        pytest.param("{}", id="empty-object"),
        pytest.param('{"regain_at": null}', id="null-regain-without-stopped-at"),
        pytest.param(
            '{"regain_at": "2026-10-01T00:00:00+00:00"}', id="regain-without-stopped-at"
        ),
        pytest.param('{"stopped_at": 1.0}', id="stopped-at-without-regain-key"),
        pytest.param('{"regain_at": null, "stopped_at": null}', id="null-stopped-at"),
        pytest.param('{"regain_at": null, "stopped_at": "1.0"}', id="string-stopped-at"),
        pytest.param('{"regain_at": null, "stopped_at": true}', id="boolean-stopped-at"),
        pytest.param('{"regain_at": null, "stopped_at": NaN}', id="nan-stopped-at"),
        pytest.param('{"regain_at": null, "stopped_at": Infinity}', id="infinite-stopped-at"),
    ],
)
def test_malformed_marker_reads_as_absent_while_the_hold_stays(q: Queue, raw: str) -> None:
    q.stop_until_resume(_REGAIN)
    q.set_meta(SPEND_CAP_STOP_META_KEY, raw)
    assert load_spend_cap_stop(q) is None
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_health_reports_the_stop_when_no_health_sweep_has_ever_run(
    client: TestClient,
) -> None:
    server.app.state.queue.stop_until_resume(_REGAIN)
    data = client.get("/api/health").json()
    assert data["health_state"] is None
    assert data["last_computed_at"] is None
    assert data["spend_cap_stop"] == {
        "cause": SPEND_CAP_CAUSE,
        "fix": SPEND_CAP_FIX,
        "regain_at": _REGAIN,
    }
    assert data["queue_paused"] is True


def test_health_reports_the_stop_when_the_health_sweep_has_gone_stale(
    client: TestClient,
) -> None:
    old = datetime.now(UTC) - timedelta(seconds=_STALE_SECONDS + 60)
    server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json(old))
    server.app.state.queue.stop_until_resume(_REGAIN)
    data = client.get("/api/health").json()
    assert data["health_state"] is None
    assert data["spend_cap_stop"]["regain_at"] == _REGAIN


def test_health_reports_the_stop_alongside_a_fresh_health_state(client: TestClient) -> None:
    server.app.state.queue.set_meta(HEALTH_META_KEY, _health_state_json(datetime.now(UTC)))
    server.app.state.queue.stop_until_resume(_REGAIN)
    data = client.get("/api/health").json()
    assert data["health_state"]["verdict"] == "healthy"
    assert data["spend_cap_stop"]["cause"] == SPEND_CAP_CAUSE


def test_health_reports_the_stop_on_every_request(client: TestClient) -> None:
    server.app.state.queue.stop_until_resume(None)
    first = client.get("/api/health").json()["spend_cap_stop"]
    second = client.get("/api/health").json()["spend_cap_stop"]
    assert first == second
    assert first["regain_at"] is None


def test_health_omits_the_stop_when_the_fleet_is_not_stopped(client: TestClient) -> None:
    assert client.get("/api/health").json()["spend_cap_stop"] is None


def test_health_omits_the_stop_for_an_ordinary_pause(client: TestClient) -> None:
    server.app.state.queue.pause_until(time.time() + 3600.0)
    data = client.get("/api/health").json()
    assert data["queue_paused"] is True
    assert data["spend_cap_stop"] is None


def test_health_with_a_malformed_marker_stays_held_and_reports_no_stop(
    client: TestClient,
) -> None:
    server.app.state.queue.stop_until_resume(_REGAIN)
    server.app.state.queue.set_meta(SPEND_CAP_STOP_META_KEY, "garbage")
    data = client.get("/api/health").json()
    assert data["queue_paused"] is True
    assert data["paused_until"] == SPEND_CAP_HOLD_EPOCH
    assert data["spend_cap_stop"] is None


def test_health_serialises_the_hold_as_finite_json(client: TestClient) -> None:
    server.app.state.queue.stop_until_resume(_REGAIN)
    text = client.get("/api/health").text

    def reject_constant(name: str) -> object:
        raise AssertionError(f"non-finite JSON constant {name}")

    data = json.loads(text, parse_constant=reject_constant)
    assert datetime.fromtimestamp(data["paused_until"], tz=UTC).year == 2100


def test_one_resume_clears_the_hold_the_marker_and_the_reported_stop(
    client: TestClient,
) -> None:
    queue = server.app.state.queue
    queue.stop_until_resume(_REGAIN)
    assert client.get("/api/health").json()["spend_cap_stop"] is not None
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.json() == {"ok": True, "queue_paused": False, "paused_until": None}
    data = client.get("/api/health").json()
    assert data["queue_paused"] is False
    assert data["paused_until"] is None
    assert data["spend_cap_stop"] is None
    assert queue.get_meta(SPEND_CAP_STOP_META_KEY) is None


def test_generated_openapi_schema_marks_the_stop_field_optional(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "spend_cap_stop" not in schema["HealthResponse"].get("required", [])
    assert set(schema["SpendCapStop"]["required"]) == {"cause", "fix"}
