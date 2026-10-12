from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import incident_store as incident_store_mod
from alan.incident import Disposition, Incident, IncidentClass, Suppression
from alan.incident_store import (
    DISPOSITIONS_META_KEY,
    DispositionRecord,
    _is_after,  # pyright: ignore[reportPrivateUsage]
    _is_prunable,  # pyright: ignore[reportPrivateUsage]
    _parse_iso,  # pyright: ignore[reportPrivateUsage]
    ack,
    is_acked,
    is_closed,
    is_suppressed,
    load_dispositions,
    prune_dispositions,
    reconcile_reopen,
    resolve_disposition_ttl_days,
    set_disposition,
    set_suppression,
)
from alan.queue import Queue


def _audit_rows(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "incident_disposition_prune_audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_load_dispositions_empty_when_missing(queue: Queue) -> None:
    assert load_dispositions(queue) == {}


def test_load_dispositions_tolerates_corrupt_json(queue: Queue) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, "{not json")
    assert load_dispositions(queue) == {}


def test_load_dispositions_tolerates_non_dict_json(queue: Queue) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps([1, 2, 3]))
    assert load_dispositions(queue) == {}


def test_load_dispositions_skips_non_dict_record(queue: Queue) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps({"fp1": "not-a-dict", "fp2": {"class": "x"}}))
    records = load_dispositions(queue)
    assert set(records) == {"fp2"}


def test_load_dispositions_tolerates_malformed_suppression(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "suppression": {"reason": "flaky"}}}),
    )
    records = load_dispositions(queue)
    assert records["fp1"].suppression is None


def test_load_dispositions_tolerates_non_dict_suppression(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "suppression": "nope"}}),
    )
    records = load_dispositions(queue)
    assert records["fp1"].suppression is None


def test_load_dispositions_tolerates_unknown_disposition_value(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "disposition": "not_a_real_disposition"}}),
    )
    records = load_dispositions(queue)
    assert records["fp1"].disposition is None


def test_load_dispositions_tolerates_non_integer_occurrence_count(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "occurrence_count": "not-a-number"}}),
    )
    records = load_dispositions(queue)
    assert records["fp1"].occurrence_count == 0


def test_load_dispositions_tolerates_non_integer_recurrence_counter(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "recurrence_counter": ["bad"]}}),
    )
    records = load_dispositions(queue)
    assert records["fp1"].recurrence_counter == 0


def test_set_disposition_creates_new_record_when_none_exists(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = set_disposition(
        queue, "fp1", Disposition.NEEDS_HUMAN, incident_class="escalation", now=now
    )
    assert record.incident_class == "escalation"
    assert record.occurrence_count == 1
    assert is_closed(record) is True
    assert record.fingerprint_at_close == "fp1"
    assert record.occurrence_at_close == 1


def test_set_disposition_none_reopens_existing_record(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    set_disposition(queue, "fp1", Disposition.KNOWN_FLAKE, now=now)
    reopened = set_disposition(queue, "fp1", None, detail="manual reopen", now=now)
    assert is_closed(reopened) is False
    assert reopened.closed_at is None
    assert reopened.fingerprint_at_close is None
    assert reopened.occurrence_at_close is None
    assert reopened.disposition_detail == "manual reopen"


def test_set_suppression_creates_new_record(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    suppression = Suppression(
        reason="flaky", ttl_epoch=now.timestamp() + 100, recurrence_threshold=3
    )
    record = set_suppression(queue, "fp1", suppression, incident_class="job_failure", now=now)
    assert record.suppression == suppression
    assert is_closed(record) is False


def test_ack_creates_new_record_and_preserves_disposition(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = ack(queue, "fp1", incident_class="merge_block", now=now)
    assert record.ack_at == now.isoformat()
    assert is_closed(record) is False

    set_disposition(queue, "fp1", Disposition.MECHANICALLY_RECOVERED, now=now)
    reacked = ack(queue, "fp1", now=now)
    assert reacked.disposition == Disposition.MECHANICALLY_RECOVERED
    assert is_closed(reacked) is True


def test_is_suppressed_false_when_no_suppression(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = ack(queue, "fp1", now=now)
    assert is_suppressed(record, now=now) is False


def test_is_acked_false_when_no_ack_at() -> None:
    record = DispositionRecord(incident_class="failure")
    assert is_acked(record, fresh_last_seen="2026-01-01T00:00:00+00:00") is False


def test_is_acked_true_when_ack_at_set_and_fresh_last_seen_unchanged(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = ack(queue, "fp1", now=now)
    assert is_acked(record, fresh_last_seen=now.isoformat()) is True


def test_is_acked_true_when_fresh_last_seen_is_empty_string(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = ack(queue, "fp1", now=now)
    assert is_acked(record, fresh_last_seen="") is True


def test_is_acked_false_when_fresh_last_seen_postdates_ack_at(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    record = ack(queue, "fp1", now=t1)
    assert is_acked(record, fresh_last_seen=t2.isoformat()) is False


def test_reconcile_reopen_leaves_unmatched_record_untouched(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    incident = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.JOB_FAILURE,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen=t1.isoformat(),
    )
    reconcile_reopen(queue, [incident], now=t1)
    before = load_dispositions(queue)["fp1"]

    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    reconcile_reopen(queue, [], now=t2)
    after = load_dispositions(queue)["fp1"]
    assert after == before


def test_reconcile_reopen_normalizes_empty_fresh_last_seen_before_reopen(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    first = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.JOB_FAILURE,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen=t1.isoformat(),
    )
    reconcile_reopen(queue, [first], now=t1)
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=t2)

    recurred = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.JOB_FAILURE,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen="",
    )
    reconcile_reopen(queue, [recurred], now=t3)
    record = load_dispositions(queue)["fp1"]
    assert is_closed(record) is False
    assert record.diagnosis_falsified is True


def test_parse_iso_valid_timestamp() -> None:
    assert _parse_iso("2026-01-01T00:00:00+00:00") == datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_iso_invalid_timestamp_returns_none() -> None:
    assert _parse_iso("not-a-timestamp") is None


def test_is_after_true_when_both_parse_and_candidate_is_later() -> None:
    assert _is_after("2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00") is True


def test_is_after_false_when_both_parse_and_candidate_is_not_later() -> None:
    assert _is_after("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00") is False


def test_is_after_falls_back_to_string_compare_when_unparseable() -> None:
    assert _is_after("not-a-timestamp", "2026-01-01T00:00:00+00:00") is True


def test_is_after_falls_back_to_string_compare_on_naive_vs_aware_mismatch() -> None:
    assert _is_after("2026-01-02T00:00:00", "2026-01-01T00:00:00+00:00") is True


def test_parse_iso_returns_none_for_non_string_value() -> None:
    assert _parse_iso(123) is None


def test_parse_iso_normalizes_trailing_z_suffix() -> None:
    assert _parse_iso("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


def test_is_after_tolerates_non_string_reference_without_raising() -> None:
    assert _is_after("2026-01-01T00:00:00+00:00", 123) is True


def test_is_acked_tolerates_non_string_ack_at_without_raising(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "job_failure", "ack": {"at": 123}}}),
    )
    record = load_dispositions(queue)["fp1"]
    assert is_acked(record, fresh_last_seen="") is True


def test_disposition_record_auto_disposed_defaults_false() -> None:
    assert DispositionRecord(incident_class="x").auto_disposed is False


def test_load_dispositions_tolerates_missing_auto_disposed_key(queue: Queue) -> None:
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps({"fp1": {"class": "park_stall"}}))
    record = load_dispositions(queue)["fp1"]
    assert record.auto_disposed is False


def test_load_dispositions_treats_non_bool_auto_disposed_as_false(queue: Queue) -> None:
    queue.set_meta(
        DISPOSITIONS_META_KEY,
        json.dumps({"fp1": {"class": "park_stall", "auto_disposed": "false"}}),
    )
    record = load_dispositions(queue)["fp1"]
    assert record.auto_disposed is False


def test_set_disposition_auto_disposed_round_trips(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = set_disposition(
        queue, "fp1", Disposition.MECHANICALLY_RECOVERED, now=now, auto_disposed=True
    )
    assert record.auto_disposed is True
    reloaded = load_dispositions(queue)["fp1"]
    assert reloaded.auto_disposed is True


def test_set_disposition_none_clears_auto_disposed(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    set_disposition(
        queue, "fp1", Disposition.MECHANICALLY_RECOVERED, now=now, auto_disposed=True
    )
    reopened = set_disposition(queue, "fp1", None, now=now)
    assert reopened.auto_disposed is False


def _park_stall_incident(fp: str, last_seen: datetime) -> Incident:
    return Incident(
        fingerprint=fp,
        incident_class=IncidentClass.PARK_STALL,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen=last_seen.isoformat(),
    )


def test_reconcile_reopen_reopens_auto_disposed_non_root_caused_record(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    reconcile_reopen(queue, [_park_stall_incident("fp1", t1)], now=t1)

    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    set_disposition(
        queue, "fp1", Disposition.MECHANICALLY_RECOVERED, now=t2, auto_disposed=True
    )

    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    reconcile_reopen(queue, [_park_stall_incident("fp1", t3)], now=t3)

    reopened = load_dispositions(queue)["fp1"]
    assert reopened.disposition is None
    assert reopened.auto_disposed is False
    assert reopened.diagnosis_falsified is True
    assert reopened.recurrence_counter == 1


def test_reconcile_reopen_does_not_reopen_non_auto_disposed_non_root_caused_record(
    queue: Queue,
) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    reconcile_reopen(queue, [_park_stall_incident("fp1", t1)], now=t1)

    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    set_disposition(queue, "fp1", Disposition.MECHANICALLY_RECOVERED, now=t2)

    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    reconcile_reopen(queue, [_park_stall_incident("fp1", t3)], now=t3)

    still_closed = load_dispositions(queue)["fp1"]
    assert still_closed.disposition == Disposition.MECHANICALLY_RECOVERED
    assert still_closed.diagnosis_falsified is False


def test_reconcile_reopen_calls_on_falsified_hook_when_reopening(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    reconcile_reopen(queue, [_park_stall_incident("fp1", t1)], now=t1)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=t2)
    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    calls: list[DispositionRecord] = []

    reconcile_reopen(
        queue, [_park_stall_incident("fp1", t3)], now=t3, on_falsified=calls.append
    )

    assert len(calls) == 1
    assert calls[0].disposition == Disposition.ROOT_CAUSED
    assert calls[0].fingerprint_at_close == "fp1"


def test_reconcile_reopen_does_not_call_on_falsified_hook_without_reopen(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    calls: list[DispositionRecord] = []

    reconcile_reopen(
        queue, [_park_stall_incident("fp1", t1)], now=t1, on_falsified=calls.append
    )

    assert calls == []


def test_reconcile_reopen_round_trips_disposition_fields(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    suppression = Suppression(
        reason="known", ttl_epoch=now.timestamp() + 500, recurrence_threshold=10
    )
    set_suppression(queue, "fp1", suppression, incident_class="merge_gate", now=now)
    set_disposition(queue, "fp1", Disposition.DUPLICATE_OF, detail="fp0", now=now)

    incident = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.MERGE_GATE,
        scope_ref="pr:1",
        workspace_id=None,
        pr_number=1,
        title="t",
        evidence={},
        last_seen=now.isoformat(),
    )
    reconcile_reopen(queue, [incident], now=now)
    record = load_dispositions(queue)["fp1"]
    assert record.disposition == Disposition.DUPLICATE_OF
    assert record.disposition_detail == "fp0"
    assert record.suppression == suppression


def test_reconcile_reopen_survivor_independent_of_input_order(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)

    def _pair(fp: str) -> tuple[Incident, Incident]:
        older = Incident(
            fingerprint=fp,
            incident_class=IncidentClass.JOB_FAILURE,
            scope_ref="audit:job_failure",
            workspace_id="a",
            pr_number=None,
            title="t",
            evidence={},
            last_seen=t1.isoformat(),
        )
        newer = Incident(
            fingerprint=fp,
            incident_class=IncidentClass.JOB_FAILURE,
            scope_ref="audit:job_failure",
            workspace_id="b",
            pr_number=None,
            title="t",
            evidence={},
            last_seen=t2.isoformat(),
        )
        return older, newer

    older1, newer1 = _pair("fp-ascending")
    older2, newer2 = _pair("fp-descending")

    reconcile_reopen(queue, [older1, newer1], now=t2)
    reconcile_reopen(queue, [newer2, older2], now=t2)

    records = load_dispositions(queue)
    assert records["fp-ascending"].last_seen == t2.isoformat()
    assert records["fp-descending"].last_seen == t2.isoformat()


def test_default_logs_dir_used_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_LOGS_ROOT", raising=False)
    assert incident_store_mod._logs_dir() == Path.home() / ".alan" / "logs"  # noqa: SLF001


def test_resolve_disposition_ttl_days_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", raising=False)
    assert resolve_disposition_ttl_days() is None


def test_resolve_disposition_ttl_days_none_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "0")
    assert resolve_disposition_ttl_days() is None


def test_resolve_disposition_ttl_days_none_when_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "not-a-number")
    assert resolve_disposition_ttl_days() is None


def test_resolve_disposition_ttl_days_valid_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    assert resolve_disposition_ttl_days() == 30


def test_is_prunable_false_when_not_closed() -> None:
    record = DispositionRecord(incident_class="x")
    assert _is_prunable(record, datetime(2026, 1, 1, tzinfo=UTC)) is False


def test_is_prunable_false_when_closed_at_missing() -> None:
    record = DispositionRecord(
        incident_class="x", disposition=Disposition.KNOWN_FLAKE, closed_at=None
    )
    assert _is_prunable(record, datetime(2026, 1, 1, tzinfo=UTC)) is False


def test_is_prunable_false_when_closed_at_unparseable() -> None:
    record = DispositionRecord(
        incident_class="x", disposition=Disposition.KNOWN_FLAKE, closed_at="not-a-timestamp"
    )
    assert _is_prunable(record, datetime(2026, 1, 1, tzinfo=UTC)) is False


def test_is_prunable_false_on_naive_vs_aware_mismatch() -> None:
    record = DispositionRecord(
        incident_class="x",
        disposition=Disposition.KNOWN_FLAKE,
        closed_at=datetime(2026, 1, 1).isoformat(),
    )
    assert _is_prunable(record, datetime(2026, 2, 1, tzinfo=UTC)) is False


def test_is_prunable_false_when_closed_at_equals_cutoff() -> None:
    cutoff = datetime(2026, 2, 1, tzinfo=UTC)
    record = DispositionRecord(
        incident_class="x", disposition=Disposition.KNOWN_FLAKE, closed_at=cutoff.isoformat()
    )
    assert _is_prunable(record, cutoff) is False


def test_is_prunable_true_when_closed_past_cutoff() -> None:
    record = DispositionRecord(
        incident_class="x",
        disposition=Disposition.NEEDS_HUMAN,
        closed_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    )
    assert _is_prunable(record, datetime(2026, 2, 1, tzinfo=UTC)) is True


def test_prune_dispositions_disabled_by_default(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", raising=False)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    old_close = now - timedelta(days=400)
    set_disposition(queue, "fp1", Disposition.KNOWN_FLAKE, now=old_close)

    assert prune_dispositions(queue, now=now) == 0
    assert "fp1" in load_dispositions(queue)


def test_prune_dispositions_removes_closed_past_ttl_including_needs_human(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    old_close = now - timedelta(days=40)
    set_disposition(queue, "fp1", Disposition.NEEDS_HUMAN, now=old_close)

    removed = prune_dispositions(queue, now=now)

    assert removed == 1
    assert "fp1" not in load_dispositions(queue)
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["removed"] == 1
    assert rows[0]["ttl_days"] == 30


def test_prune_dispositions_keeps_open_records(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    old = now - timedelta(days=40)
    ack(queue, "fp1", now=old)

    assert prune_dispositions(queue, now=now) == 0
    assert "fp1" in load_dispositions(queue)


def test_prune_dispositions_keeps_recently_closed_records(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    recent_close = now - timedelta(days=5)
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=recent_close)

    assert prune_dispositions(queue, now=now) == 0
    assert "fp1" in load_dispositions(queue)


def test_prune_dispositions_no_audit_row_when_nothing_removed(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ack(queue, "fp1", now=now)

    assert prune_dispositions(queue, now=now) == 0
    assert _audit_rows(tmp_path) == []


def test_prune_dispositions_leaves_other_records_untouched(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_INCIDENT_DISPOSITION_TTL_DAYS", "30")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    old_close = now - timedelta(days=40)
    recent_close = now - timedelta(days=5)
    set_disposition(queue, "fp_old", Disposition.KNOWN_FLAKE, now=old_close)
    set_disposition(queue, "fp_recent", Disposition.KNOWN_FLAKE, now=recent_close)

    removed = prune_dispositions(queue, now=now)

    assert removed == 1
    records = load_dispositions(queue)
    assert "fp_old" not in records
    assert "fp_recent" in records
