from __future__ import annotations

from datetime import UTC, datetime

from alan.digest import (
    COST_NOTE,
    AuditWindowCounts,
    DigestDoc,
    OpenedEntry,
    RecoveryTrendEntry,
    ResidueEntry,
    bucket_audit_rows,
    digest_from_dict,
    digest_to_dict,
    recovery_totals,
    render_digest,
)
from alan.incident import AuditRow, Disposition, Incident, IncidentClass, Suppression
from alan.incident_store import DispositionRecord

_SINCE = "2026-07-04T09:00:00+00:00"
_NOW = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()


def _record(**overrides: object) -> DispositionRecord:
    base: dict[str, object] = {
        "incident_class": IncidentClass.TERMINAL_FAILED.value,
        "opened_at": "",
        "last_seen": "",
    }
    base.update(overrides)
    return DispositionRecord(**base)  # type: ignore[arg-type]


def _incident(fingerprint: str, incident_class: IncidentClass, title: str = "t") -> Incident:
    return Incident(
        fingerprint=fingerprint,
        incident_class=incident_class,
        scope_ref=f"ws:{fingerprint}",
        workspace_id=fingerprint,
        pr_number=None,
        title=title,
        evidence={},
        last_seen=_NOW_ISO,
    )


def _audit_row(incident_class: IncidentClass, ts: str, **extra: object) -> AuditRow:
    return AuditRow(incident_class=incident_class, raw={"timestamp_utc": ts, **extra})


def test_render_digest_counts_opened_within_window() -> None:
    dispositions = {
        "fp1": _record(
            opened_at="2026-07-04T10:00:00+00:00", last_seen="2026-07-04T10:00:00+00:00"
        ),
        "fp2": _record(
            incident_class=IncidentClass.ESCALATION.value,
            opened_at="2026-07-03T10:00:00+00:00",
            last_seen="2026-07-03T10:00:00+00:00",
        ),
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.opened == (
        OpenedEntry(incident_class=IncidentClass.TERMINAL_FAILED.value, count=1),
    )


def test_render_digest_counts_closed_by_disposition_within_window() -> None:
    dispositions = {
        "fp1": _record(
            opened_at="2026-07-01T00:00:00+00:00",
            closed_at="2026-07-04T12:00:00+00:00",
            disposition=Disposition.ROOT_CAUSED,
            last_seen="2026-07-04T12:00:00+00:00",
        ),
        "fp2": _record(
            opened_at="2026-07-01T00:00:00+00:00",
            closed_at="2026-07-03T00:00:00+00:00",
            disposition=Disposition.KNOWN_FLAKE,
            last_seen="2026-07-03T00:00:00+00:00",
        ),
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert len(doc.closed) == 1
    assert doc.closed[0].disposition == Disposition.ROOT_CAUSED.value
    assert doc.closed[0].count == 1


def test_render_digest_ignores_open_record_with_no_disposition_for_closed_bucket() -> None:
    dispositions = {"fp1": _record(closed_at=None, opened_at="2026-07-04T10:00:00+00:00")}
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.closed == ()


def test_render_digest_excludes_opened_after_now_clock_skew() -> None:
    dispositions = {
        "fp1": _record(
            opened_at="2026-07-05T09:30:00+00:00", last_seen="2026-07-05T09:30:00+00:00"
        )
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.opened == ()


def test_render_digest_excludes_closed_after_now_clock_skew() -> None:
    dispositions = {
        "fp1": _record(
            opened_at="2026-07-01T00:00:00+00:00",
            closed_at="2026-07-05T09:30:00+00:00",
            disposition=Disposition.ROOT_CAUSED,
        )
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.closed == ()


def test_render_digest_reports_in_force_suppression_with_remaining_seconds() -> None:
    suppression = Suppression(
        reason="known flake", ttl_epoch=_NOW.timestamp() + 3600, recurrence_threshold=100
    )
    dispositions = {
        "fp1": _record(
            suppression=suppression,
            occurrence_count=1,
            opened_at="2026-07-04T10:00:00+00:00",
        )
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert len(doc.suppressions) == 1
    entry = doc.suppressions[0]
    assert entry.fingerprint == "fp1"
    assert entry.reason == "known flake"
    assert entry.remaining_seconds == 3600.0


def test_render_digest_excludes_expired_suppression() -> None:
    suppression = Suppression(
        reason="known flake", ttl_epoch=_NOW.timestamp() - 10, recurrence_threshold=100
    )
    dispositions = {"fp1": _record(suppression=suppression, opened_at="2026-07-04T10:00:00+00:00")}
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.suppressions == ()


def test_render_digest_excludes_suppression_past_recurrence_threshold() -> None:
    suppression = Suppression(
        reason="known flake", ttl_epoch=_NOW.timestamp() + 3600, recurrence_threshold=1
    )
    dispositions = {
        "fp1": _record(
            suppression=suppression, occurrence_count=5, opened_at="2026-07-04T10:00:00+00:00"
        )
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.suppressions == ()


def test_render_digest_reports_open_residue_incident() -> None:
    incidents = [_incident("fp-residue", IncidentClass.RESIDUE, title="Unclassified anomaly")]
    doc = render_digest({}, incidents, AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.residue == (ResidueEntry(fingerprint="fp-residue", title="Unclassified anomaly"),)


def test_render_digest_excludes_closed_residue_incident() -> None:
    incidents = [_incident("fp-residue", IncidentClass.RESIDUE)]
    dispositions = {
        "fp-residue": _record(
            incident_class=IncidentClass.RESIDUE.value,
            disposition=Disposition.BY_DESIGN,
            opened_at="2026-07-01T00:00:00+00:00",
            closed_at="2026-07-01T00:00:00+00:00",
        )
    }
    doc = render_digest(
        dispositions, incidents, AuditWindowCounts(current={}, previous={}), _SINCE, _NOW
    )
    assert doc.residue == ()


def test_render_digest_ignores_non_residue_incidents_for_residue_bucket() -> None:
    incidents = [_incident("fp-fail", IncidentClass.TERMINAL_FAILED)]
    doc = render_digest({}, incidents, AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.residue == ()


def test_render_digest_reports_residue_with_open_unsuppressed_disposition_record() -> None:
    incidents = [_incident("fp-residue", IncidentClass.RESIDUE)]
    dispositions = {
        "fp-residue": _record(
            incident_class=IncidentClass.RESIDUE.value, opened_at="2026-07-01T00:00:00+00:00"
        )
    }
    doc = render_digest(
        dispositions, incidents, AuditWindowCounts(current={}, previous={}), _SINCE, _NOW
    )
    assert len(doc.residue) == 1
    assert doc.residue[0].fingerprint == "fp-residue"


def test_conservation_touched_records_are_subset_of_fingerprints() -> None:
    dispositions = {
        "touched-opened": _record(opened_at="2026-07-04T10:00:00+00:00"),
        "touched-closed": _record(
            opened_at="2026-07-01T00:00:00+00:00",
            closed_at="2026-07-04T11:00:00+00:00",
            disposition=Disposition.ROOT_CAUSED,
        ),
        "touched-last-seen": _record(
            opened_at="2026-07-01T00:00:00+00:00", last_seen="2026-07-04T12:00:00+00:00"
        ),
        "touched-ack": _record(
            opened_at="2026-07-01T00:00:00+00:00", ack_at="2026-07-04T13:00:00+00:00"
        ),
        "untouched": _record(
            opened_at="2026-07-01T00:00:00+00:00", last_seen="2026-07-01T00:00:00+00:00"
        ),
    }
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)

    def touched_in_window(rec: DispositionRecord) -> bool:
        timestamps = [t for t in (rec.opened_at, rec.closed_at, rec.last_seen, rec.ack_at) if t]
        return bool(timestamps) and _SINCE <= max(timestamps) <= _NOW_ISO

    expected = {fp for fp, rec in dispositions.items() if touched_in_window(rec)}
    assert expected == {"touched-opened", "touched-closed", "touched-last-seen", "touched-ack"}
    assert expected <= doc.fingerprints
    assert doc.fingerprints == expected


def test_bucket_audit_rows_counts_self_heal_rows_as_recovery() -> None:
    rows = [
        _audit_row(IncidentClass.SELF_HEAL, "2026-07-04T10:00:00+00:00"),
        _audit_row(IncidentClass.SELF_HEAL, "2026-07-04T11:00:00+00:00"),
    ]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {"self_heal": 2}
    assert counts.previous == {}


def test_bucket_audit_rows_counts_only_recovered_merge_gate_rows() -> None:
    rows = [
        _audit_row(IncidentClass.MERGE_GATE, "2026-07-04T10:00:00+00:00", recovered=True),
        _audit_row(IncidentClass.MERGE_GATE, "2026-07-04T10:30:00+00:00", recovered=False),
    ]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {"merge_gate": 1}


def test_bucket_audit_rows_excludes_job_failure_rows() -> None:
    rows = [_audit_row(IncidentClass.JOB_FAILURE, "2026-07-04T10:00:00+00:00")]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {}
    assert counts.previous == {}


def test_bucket_audit_rows_splits_current_vs_previous_window() -> None:
    rows = [
        _audit_row(IncidentClass.SELF_HEAL, "2026-07-03T10:00:00+00:00"),
        _audit_row(IncidentClass.SELF_HEAL, "2026-07-03T12:00:00+00:00"),
        _audit_row(IncidentClass.SELF_HEAL, "2026-07-05T08:00:00+00:00"),
    ]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {"self_heal": 1}
    assert counts.previous == {"self_heal": 2}


def test_bucket_audit_rows_ignores_row_older_than_prior_window() -> None:
    rows = [_audit_row(IncidentClass.SELF_HEAL, "2026-07-01T00:00:00+00:00")]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {}
    assert counts.previous == {}


def test_bucket_audit_rows_skips_row_without_timestamp() -> None:
    rows = [AuditRow(incident_class=IncidentClass.SELF_HEAL, raw={})]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    assert counts.current == {}
    assert counts.previous == {}


def test_bucket_audit_rows_counts_unaffected_by_incident_window() -> None:
    since = "2025-01-01T00:00:00+00:00"
    prior_since = "2024-01-01T00:00:00+00:00"
    old_row = _audit_row(IncidentClass.SELF_HEAL, "2025-06-01T00:00:00+00:00")
    counts = bucket_audit_rows([old_row], since=since, prior_since=prior_since)
    assert counts.current == {"self_heal": 1}
    assert counts.previous == {}


def test_trend_invariant_to_occurrence_count_only_reflects_audit_rows() -> None:
    dispositions = {
        "fp1": _record(occurrence_count=999, opened_at="2026-07-04T10:00:00+00:00"),
    }
    rows = [_audit_row(IncidentClass.SELF_HEAL, "2026-07-05T08:00:00+00:00")]
    counts = bucket_audit_rows(rows, since=_SINCE, prior_since="2026-07-03T09:00:00+00:00")
    doc = render_digest(dispositions, [], counts, _SINCE, _NOW)
    assert len(doc.recovery_trend) == 1
    assert doc.recovery_trend[0].current_count == 1
    assert doc.recovery_trend[0].incident_class == "self_heal"


def test_recovery_trend_direction_up_down_same() -> None:
    counts = AuditWindowCounts(
        current={"self_heal": 3, "merge_gate": 1, "job_failure_unused": 2, "residue": 0},
        previous={"self_heal": 1, "merge_gate": 1, "residue": 4},
    )
    doc = render_digest({}, [], counts, _SINCE, _NOW)
    by_class = {e.incident_class: e for e in doc.recovery_trend}
    assert by_class["self_heal"].direction == "up"
    assert by_class["merge_gate"].direction == "same"
    assert by_class["job_failure_unused"].direction == "up"
    assert by_class["residue"].direction == "down"


def test_digest_to_dict_from_dict_round_trip() -> None:
    dispositions = {
        "fp1": _record(
            opened_at="2026-07-04T10:00:00+00:00",
            closed_at="2026-07-04T11:00:00+00:00",
            disposition=Disposition.ROOT_CAUSED,
            suppression=Suppression(
                reason="flaky", ttl_epoch=_NOW.timestamp() + 100, recurrence_threshold=5
            ),
        ),
    }
    incidents = [_incident("fp-residue", IncidentClass.RESIDUE)]
    counts = AuditWindowCounts(current={"self_heal": 2}, previous={"self_heal": 1})
    doc = render_digest(dispositions, incidents, counts, _SINCE, _NOW)

    round_tripped = digest_from_dict(digest_to_dict(doc))

    assert round_tripped == doc


def test_digest_from_dict_defaults_missing_fields() -> None:
    doc = digest_from_dict({})
    assert doc.since == ""
    assert doc.generated_at == ""
    assert doc.opened == ()
    assert doc.cost_note == COST_NOTE
    assert doc.fingerprints == frozenset()


def test_digest_from_dict_ignores_malformed_inner_fields() -> None:
    doc = digest_from_dict(
        {
            "opened": "not-a-list",
            "recovery_trend": [1, 2],
            "fingerprints": ["fp1", 42, None],
        }
    )
    assert doc.opened == ()
    assert doc.recovery_trend == ()
    assert doc.fingerprints == {"fp1"}


def test_digest_from_dict_direction_defaults_to_same_for_unknown_value() -> None:
    raw = {
        "recovery_trend": [
            {
                "incident_class": "self_heal",
                "current_count": 1,
                "previous_count": 1,
                "direction": "sideways",
            }
        ]
    }
    doc = digest_from_dict(raw)
    assert doc is not None
    assert doc.recovery_trend[0].direction == "same"


def test_touched_in_window_false_when_record_has_no_timestamps_at_all() -> None:
    dispositions = {"fp-blank": _record()}
    doc = render_digest(dispositions, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.fingerprints == frozenset()


def test_digest_doc_is_a_dataclass_instance() -> None:
    doc = render_digest({}, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert isinstance(doc, DigestDoc)
    assert doc.cost_note
    assert doc.since == _SINCE
    assert doc.generated_at == _NOW_ISO


def test_render_digest_defaults_shop_stats_to_empty_string() -> None:
    doc = render_digest({}, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW)
    assert doc.shop_stats == ""


def test_render_digest_carries_shop_stats_block_through_as_data() -> None:
    block = "Learning metrics: 62.0% first-pass approval"
    doc = render_digest(
        {}, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW, shop_stats=block
    )
    assert doc.shop_stats == block


def test_digest_to_dict_includes_shop_stats() -> None:
    doc = render_digest(
        {}, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW, shop_stats="stats"
    )
    assert digest_to_dict(doc)["shop_stats"] == "stats"


def test_digest_from_dict_reads_shop_stats() -> None:
    doc = digest_from_dict({"shop_stats": "stats"})
    assert doc.shop_stats == "stats"


def test_digest_from_dict_defaults_shop_stats_to_empty_when_missing() -> None:
    doc = digest_from_dict({})
    assert doc.shop_stats == ""


def test_digest_shop_stats_round_trips_through_to_dict_and_from_dict() -> None:
    doc = render_digest(
        {}, [], AuditWindowCounts(current={}, previous={}), _SINCE, _NOW, shop_stats="round trip"
    )
    assert digest_from_dict(digest_to_dict(doc)) == doc


def test_recovery_totals_sums_current_and_previous_across_classes() -> None:
    entries = (
        RecoveryTrendEntry(
            incident_class="self_heal", current_count=3, previous_count=1, direction="up"
        ),
        RecoveryTrendEntry(
            incident_class="merge_gate", current_count=2, previous_count=2, direction="same"
        ),
    )
    totals = recovery_totals(entries)
    assert totals.current_count == 5
    assert totals.previous_count == 3


def test_recovery_totals_direction_up_when_current_exceeds_previous() -> None:
    entries = (
        RecoveryTrendEntry(
            incident_class="self_heal", current_count=3, previous_count=1, direction="up"
        ),
    )
    assert recovery_totals(entries).direction == "up"


def test_recovery_totals_direction_down_when_current_below_previous() -> None:
    entries = (
        RecoveryTrendEntry(
            incident_class="self_heal", current_count=1, previous_count=3, direction="down"
        ),
    )
    assert recovery_totals(entries).direction == "down"


def test_recovery_totals_direction_same_when_totals_are_equal() -> None:
    entries = (
        RecoveryTrendEntry(
            incident_class="self_heal", current_count=3, previous_count=1, direction="up"
        ),
        RecoveryTrendEntry(
            incident_class="merge_gate", current_count=1, previous_count=3, direction="down"
        ),
    )
    assert recovery_totals(entries).direction == "same"


def test_recovery_totals_direction_same_when_no_entries() -> None:
    totals = recovery_totals(())
    assert totals.current_count == 0
    assert totals.previous_count == 0
    assert totals.direction == "same"
