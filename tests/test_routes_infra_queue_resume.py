from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, gold_label, server
from alan import workspace as workspace_mod
from alan.queue import FORGE_DEGRADED_META_KEY
from alan.roots import logs_root
from alan.teams._base import TeamEvent


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _audit_rows() -> list[dict[str, object]]:
    path = logs_root() / "queue_pause_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_resume_queue_requires_control_token(client: TestClient) -> None:
    res = client.post("/api/queue/resume")
    assert res.status_code == 401


def test_resume_queue_wrong_token_403(client: TestClient) -> None:
    res = client.post(
        "/api/queue/resume", headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_resume_queue_auth_failure_never_clears_pause(client: TestClient) -> None:
    server.app.state.queue.pause_until(time.time() + 3600.0)
    res = client.post("/api/queue/resume")
    assert res.status_code == 401
    assert server.app.state.queue.paused_until() is not None


def test_resume_queue_clears_an_active_pause(client: TestClient) -> None:
    server.app.state.queue.pause_until(time.time() + 3600.0)
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200
    body = res.json()
    assert body == {"ok": True, "queue_paused": False, "paused_until": None}
    assert server.app.state.queue.paused_until() is None


def test_resume_queue_when_not_paused_is_a_harmless_noop(client: TestClient) -> None:
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200
    body = res.json()
    assert body == {"ok": True, "queue_paused": False, "paused_until": None}


def test_resume_queue_writes_audit_row_with_prior_paused_until(client: TestClient) -> None:
    future = time.time() + 3600.0
    server.app.state.queue.pause_until(future)
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200
    rows = _audit_rows()
    assert len(rows) == 1
    prior = rows[0]["prior_paused_until"]
    assert isinstance(prior, (int, float))
    assert abs(float(prior) - future) < 1.0
    assert rows[0]["cleared_at"]


def test_resume_queue_writes_audit_row_with_null_prior_when_not_paused(
    client: TestClient,
) -> None:
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200
    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0]["prior_paused_until"] is None


def test_resume_queue_publishes_queue_resumed_exactly_once(client: TestClient) -> None:
    server.app.state.queue.pause_until(time.time() + 3600.0)
    events: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(events.append)
    try:
        client.post("/api/queue/resume", headers=_control_headers())
    finally:
        unsubscribe()
    matches = [e for e in events if e.kind == "queue_resumed"]
    assert len(matches) == 1
    assert matches[0].payload == {}


def test_resume_queue_does_not_publish_on_auth_failure(client: TestClient) -> None:
    events: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(events.append)
    try:
        client.post("/api/queue/resume")
    finally:
        unsubscribe()
    assert not any(e.kind == "queue_resumed" for e in events)


def test_resume_queue_never_records_intervention_or_gold_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom_intervention(*_a: object, **_kw: object) -> object:
        raise AssertionError("record_intervention must not be called")

    def _boom_gold_label(*_a: object, **_kw: object) -> object:
        raise AssertionError("record_gold_label must not be called")

    monkeypatch.setattr(workspace_mod, "record_intervention", _boom_intervention)
    monkeypatch.setattr(gold_label, "record_gold_label", _boom_gold_label)
    server.app.state.queue.pause_until(time.time() + 3600.0)
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200


def test_resume_queue_leaves_forge_degraded_meta_untouched(client: TestClient) -> None:
    blob = json.dumps({"reset_at": time.time() + 900.0, "detail": "github rate limited"})
    server.app.state.queue.set_meta(FORGE_DEGRADED_META_KEY, blob)
    server.app.state.queue.pause_until(time.time() + 3600.0)
    res = client.post("/api/queue/resume", headers=_control_headers())
    assert res.status_code == 200
    assert server.app.state.queue.get_meta(FORGE_DEGRADED_META_KEY) == blob
