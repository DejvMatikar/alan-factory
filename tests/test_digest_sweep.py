from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import digest_sweep as digest_sweep_mod
from alan import workflow as workflow_mod
from alan.digest_sweep import (
    DIGEST_LAST_DATE_META_KEY,
    DIGEST_LATEST_META_KEY,
    load_latest_digest,
    reconcile_incidents_sweep,
)
from alan.health import HealthState
from alan.incident import Disposition, Incident, IncidentClass, incident_fingerprint
from alan.incident import derive_incidents as real_derive_incidents
from alan.incident_store import load_dispositions, set_disposition
from alan.incident_store import reconcile_reopen as real_reconcile_reopen
from alan.queue import Queue
from alan.teams.triage import _TEAM_NAME, ROLE_SPECS, ReconcileIncidentsFn, TriageTeam
from alan.workspace import Status, transition
from alan.workspace import create as create_workspace

_EMPTY_HEALTH = HealthState(
    verdict="healthy",
    blocked_count=0,
    in_flight_count=0,
    findings=[],
    computed_at="2026-01-01T00:00:00+00:00",
)


def _stuck_ws(ws_root: Path) -> tuple[str, str]:
    ws = create_workspace("Sweep Target", "d", root=ws_root)
    ws = transition(ws, Status.FAILED)
    fp = incident_fingerprint(IncidentClass.TERMINAL_FAILED, f"ws:{ws.task_id}", None)
    return ws.task_id, fp


def _noop_emit(event: object) -> None:
    del event


def test_matches_reconcile_incidents_fn_type() -> None:
    fn: ReconcileIncidentsFn = reconcile_incidents_sweep
    assert fn is reconcile_incidents_sweep


def test_sweep_upserts_fresh_incident(queue: Queue, ws_root: Path) -> None:
    _task_id, fp = _stuck_ws(ws_root)
    now = datetime.now(UTC)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, now)

    records = load_dispositions(queue)
    assert fp in records
    record = records[fp]
    assert record.disposition is None
    assert record.occurrence_count == 1
    assert record.incident_class == IncidentClass.TERMINAL_FAILED.value


def test_sweep_advances_occurrence_while_last_seen_tracks_incident_state(
    queue: Queue, ws_root: Path
) -> None:
    _task_id, fp = _stuck_ws(ws_root)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))
    first = load_dispositions(queue)[fp]

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))
    second = load_dispositions(queue)[fp]

    assert second.occurrence_count == first.occurrence_count + 1
    assert second.last_seen == first.last_seen


def test_sweep_reopens_recurring_root_caused_incident(queue: Queue, ws_root: Path) -> None:
    _task_id, fp = _stuck_ws(ws_root)
    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))
    ws_updated_at = load_dispositions(queue)[fp].last_seen

    stale_close = datetime.fromisoformat(ws_updated_at) - timedelta(days=1)
    set_disposition(
        queue,
        fp,
        Disposition.ROOT_CAUSED,
        incident_class=IncidentClass.TERMINAL_FAILED.value,
        now=stale_close,
    )
    closed = load_dispositions(queue)[fp]
    assert closed.disposition == Disposition.ROOT_CAUSED
    assert closed.diagnosis_falsified is False

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))

    reopened = load_dispositions(queue)[fp]
    assert reopened.disposition is None
    assert reopened.diagnosis_falsified is True
    assert reopened.recurrence_counter == 1


def test_wired_through_triage_team_reconciles_and_survives_failure(
    queue: Queue, ws_root: Path
) -> None:
    _task_id, fp = _stuck_ws(ws_root)
    ws = create_workspace("Sweep Runner", "d", root=ws_root, team=_TEAM_NAME)
    inv_id = workflow_mod.seed_pending_workflow(ws, team=_TEAM_NAME, tasks=list(ROLE_SPECS))
    team = TriageTeam(
        queue=queue,
        workspaces_root=ws_root,
        reconcile_incidents=reconcile_incidents_sweep,
    )

    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == _TEAM_NAME
    assert fp in load_dispositions(queue)


def test_wired_through_triage_team_swallows_internal_failure(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[None] = []

    def _boom(*_args: object, **_kwargs: object) -> list[object]:
        calls.append(None)
        raise RuntimeError("boom")

    monkeypatch.setattr(digest_sweep_mod, "gather_audit_rows", _boom)

    ws = create_workspace("Sweep Runner Boom", "d", root=ws_root, team=_TEAM_NAME)
    inv_id = workflow_mod.seed_pending_workflow(ws, team=_TEAM_NAME, tasks=list(ROLE_SPECS))
    team = TriageTeam(
        queue=queue,
        workspaces_root=ws_root,
        reconcile_incidents=reconcile_incidents_sweep,
    )

    handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.from_team == _TEAM_NAME
    assert calls, "reconcile_incidents_sweep's failing path was never exercised"


def test_sweep_passes_fresh_incidents_straight_to_reconcile_reopen_with_no_callback(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stuck_ws(ws_root)
    calls: list[dict[str, Any]] = []

    def _spy(queue_arg: Queue, incidents: list[Incident], **kwargs: Any) -> object:
        calls.append(kwargs)
        return real_reconcile_reopen(queue_arg, incidents, **kwargs)

    monkeypatch.setattr(digest_sweep_mod, "reconcile_reopen", _spy)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))

    assert len(calls) == 1
    assert "on_falsified" not in calls[0]


def test_sweep_prunes_disposition_closed_past_ttl(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime.now(UTC)
    old_close = now - timedelta(days=40)
    set_disposition(queue, "stale-fp", Disposition.KNOWN_FLAKE, now=old_close)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, now)

    assert "stale-fp" not in load_dispositions(queue)


def test_sweep_does_not_prune_when_ttl_unset(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", raising=False)
    now = datetime.now(UTC)
    old_close = now - timedelta(days=400)
    set_disposition(queue, "stale-fp", Disposition.KNOWN_FLAKE, now=old_close)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, now)

    assert "stale-fp" in load_dispositions(queue)


def test_load_latest_digest_none_when_missing(queue: Queue) -> None:
    assert load_latest_digest(queue) is None


def test_load_latest_digest_none_on_invalid_json(queue: Queue) -> None:
    queue.set_meta(DIGEST_LATEST_META_KEY, "not json{{{")
    assert load_latest_digest(queue) is None


def test_load_latest_digest_none_when_not_a_dict(queue: Queue) -> None:
    queue.set_meta(DIGEST_LATEST_META_KEY, json.dumps([1, 2, 3]))
    assert load_latest_digest(queue) is None


def test_prior_window_start_falls_back_to_now_on_unparseable_since() -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    result = digest_sweep_mod._prior_window_start("not-a-timestamp", now)  # noqa: SLF001
    assert result == (now - timedelta(days=1)).isoformat()


def test_prior_window_start_uses_fallback_window_when_since_not_before_now() -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    since = now.isoformat()
    result = digest_sweep_mod._prior_window_start(since, now)  # noqa: SLF001
    assert result == (now - timedelta(days=1)).isoformat()


def test_prior_window_start_falls_back_to_now_on_naive_since_without_crashing() -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    naive_since = datetime(2026, 7, 4, 9, 30).isoformat()
    result = digest_sweep_mod._prior_window_start(naive_since, now)  # noqa: SLF001
    assert result == (now - timedelta(days=1)).isoformat()


def _pin_deadline(monkeypatch: pytest.MonkeyPatch, deadline: datetime) -> None:
    monkeypatch.setattr(digest_sweep_mod, "resolve_digest_deadline", lambda now: deadline)


_DAY1_DEADLINE = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
_DAY1_MORNING = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
_DAY1_LATER = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)
_DAY2_DEADLINE = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_DAY2_MORNING = datetime(2026, 7, 6, 9, 30, tzinfo=UTC)


def test_digest_not_emitted_when_not_overdue(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 5, 8, 0, tzinfo=UTC)
    _pin_deadline(monkeypatch, datetime(2026, 7, 5, 9, 0, tzinfo=UTC))

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, now)

    assert queue.get_meta(DIGEST_LATEST_META_KEY) is None
    assert queue.get_meta(DIGEST_LAST_DATE_META_KEY) is None


def test_digest_emitted_when_overdue(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    _pin_deadline(monkeypatch, deadline)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, now)

    doc = load_latest_digest(queue)
    assert doc is not None
    assert doc.generated_at == now.isoformat()
    assert queue.get_meta(DIGEST_LAST_DATE_META_KEY) == "2026-07-05"


def test_digest_idempotent_same_day_produces_byte_identical_digest(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)
    first_raw = queue.get_meta(DIGEST_LATEST_META_KEY)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_LATER)
    second_raw = queue.get_meta(DIGEST_LATEST_META_KEY)

    assert first_raw == second_raw


def test_digest_regenerates_on_the_following_day(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)
    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)
    assert queue.get_meta(DIGEST_LAST_DATE_META_KEY) == "2026-07-05"

    _pin_deadline(monkeypatch, _DAY2_DEADLINE)
    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY2_MORNING)
    assert queue.get_meta(DIGEST_LAST_DATE_META_KEY) == "2026-07-06"


def test_digest_since_uses_previous_digests_generated_at(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)
    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)
    first_generated_at = load_latest_digest(queue).generated_at  # type: ignore[union-attr]

    _pin_deadline(monkeypatch, _DAY2_DEADLINE)
    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY2_MORNING)

    second = load_latest_digest(queue)
    assert second is not None
    assert second.since == first_generated_at


def test_digest_includes_overdue_incident_in_dispositions(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    fp = incident_fingerprint(IncidentClass.DIGEST_OVERDUE, "system:digest", None)
    assert fp in load_dispositions(queue)


def test_digest_carries_measured_shop_stats_block(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    doc = load_latest_digest(queue)
    assert doc is not None
    assert doc.shop_stats != ""
    assert "not enough data yet" in doc.shop_stats
    assert "None" not in doc.shop_stats


def test_sweep_digest_excludes_disposition_pruned_same_tick(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    old_close = _DAY1_MORNING - timedelta(days=40)
    set_disposition(queue, "stale-fp", Disposition.KNOWN_FLAKE, now=old_close)
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    assert "stale-fp" not in load_dispositions(queue)
    doc = load_latest_digest(queue)
    assert doc is not None
    assert "stale-fp" not in doc.fingerprints


def test_sweep_digest_still_emitted_when_prune_dispositions_raises(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(digest_sweep_mod, "prune_dispositions", _boom)
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    with caplog.at_level("ERROR", logger="alan.digest_sweep"):
        reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    assert load_latest_digest(queue) is not None
    assert any("prune_dispositions failed" in r.message for r in caplog.records)


def test_outer_ring_degrades_when_webhook_unset(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("ALAN_DIGEST_WEBHOOK_URL", raising=False)
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    with caplog.at_level("INFO", logger="alan.digest_sweep"):
        reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    assert load_latest_digest(queue) is not None
    assert any("webhook unset" in r.message for r in caplog.records)


def test_outer_ring_degrades_when_webhook_post_fails(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_DIGEST_WEBHOOK_URL", "http://example.invalid/digest")
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    with caplog.at_level("WARNING", logger="alan.digest_sweep"):
        reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    assert load_latest_digest(queue) is not None
    assert any("webhook push failed" in r.message for r in caplog.records)


def test_outer_ring_posts_digest_payload_when_webhook_set(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_DIGEST_WEBHOOK_URL", "http://example.invalid/digest")
    _pin_deadline(monkeypatch, _DAY1_DEADLINE)
    captured: list[bytes | None] = []

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def _fake_urlopen(request: object, timeout: float) -> _FakeResponse:
        del timeout
        captured.append(request.data)  # type: ignore[attr-defined]
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, _DAY1_MORNING)

    assert len(captured) == 1
    payload = json.loads(captured[0])  # type: ignore[arg-type]
    assert "cost_note" in payload


def test_reconcile_incidents_sweep_builds_engine_key_by_workspace(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_a = create_workspace(
        "Sweep Engine A", "d", root=ws_root, engine_key="sprocket-co/sprocket"
    )
    ws_b = create_workspace("Sweep Engine B", "d", root=ws_root)

    captured: dict[str, object] = {}

    def _capture(**kwargs: Any) -> list[Incident]:
        captured.update(kwargs)
        return real_derive_incidents(**kwargs)

    monkeypatch.setattr(digest_sweep_mod, "derive_incidents", _capture)

    reconcile_incidents_sweep(queue, ws_root, _EMPTY_HEALTH, datetime.now(UTC))

    engine_map = captured["engine_key_by_workspace"]
    assert engine_map[ws_a.task_id] == "sprocket-co/sprocket"  # type: ignore[index]
    assert engine_map[ws_b.task_id] is None  # type: ignore[index]
