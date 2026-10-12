from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from alan.incident import AuditRow, Incident, IncidentClass
from alan.incident_store import DispositionRecord

TrendDirection = Literal["up", "down", "same"]

COST_NOTE = (
    "Computed deterministically from disposition and audit-log state on the existing "
    "triage sweep — no LLM tokens spent producing this digest."
)

_RECOVERY_CLASSES: frozenset[IncidentClass] = frozenset(
    {IncidentClass.SELF_HEAL, IncidentClass.MERGE_GATE}
)


@dataclass(frozen=True)
class AuditWindowCounts:
    current: dict[str, int]
    previous: dict[str, int]


@dataclass(frozen=True)
class OpenedEntry:
    incident_class: str
    count: int


@dataclass(frozen=True)
class ClosedEntry:
    disposition: str
    count: int


@dataclass(frozen=True)
class RecoveryTrendEntry:
    incident_class: str
    current_count: int
    previous_count: int
    direction: TrendDirection


@dataclass(frozen=True)
class RecoveryTotals:
    current_count: int
    previous_count: int
    direction: TrendDirection


@dataclass(frozen=True)
class SuppressionEntry:
    fingerprint: str
    incident_class: str
    reason: str
    remaining_seconds: float


@dataclass(frozen=True)
class ResidueEntry:
    fingerprint: str
    title: str


@dataclass(frozen=True)
class DigestDoc:
    since: str
    generated_at: str
    opened: tuple[OpenedEntry, ...]
    closed: tuple[ClosedEntry, ...]
    recovery_trend: tuple[RecoveryTrendEntry, ...]
    suppressions: tuple[SuppressionEntry, ...]
    residue: tuple[ResidueEntry, ...]
    cost_note: str
    fingerprints: frozenset[str]
    shop_stats: str = ""


def _is_recovery_row(row: AuditRow) -> bool:
    if row.incident_class == IncidentClass.SELF_HEAL:
        return True
    if row.incident_class == IncidentClass.MERGE_GATE:
        return row.raw.get("recovered") is True
    return False


def _row_timestamp(row: AuditRow) -> str:
    val = row.raw.get("timestamp_utc")
    return val if isinstance(val, str) else ""


def bucket_audit_rows(
    rows: Sequence[AuditRow], *, since: str, prior_since: str
) -> AuditWindowCounts:
    current: dict[str, int] = {}
    previous: dict[str, int] = {}
    for row in rows:
        if not _is_recovery_row(row):
            continue
        ts = _row_timestamp(row)
        if not ts:
            continue
        cls = row.incident_class.value
        if ts >= since:
            current[cls] = current.get(cls, 0) + 1
        elif ts >= prior_since:
            previous[cls] = previous.get(cls, 0) + 1
    return AuditWindowCounts(current=current, previous=previous)


def _opened_entries(
    dispositions: dict[str, DispositionRecord], since: str, now_iso: str
) -> tuple[OpenedEntry, ...]:
    counts: dict[str, int] = {}
    for record in dispositions.values():
        if record.opened_at and since <= record.opened_at <= now_iso:
            counts[record.incident_class] = counts.get(record.incident_class, 0) + 1
    return tuple(OpenedEntry(incident_class=cls, count=n) for cls, n in sorted(counts.items()))


def _closed_entries(
    dispositions: dict[str, DispositionRecord], since: str, now_iso: str
) -> tuple[ClosedEntry, ...]:
    counts: dict[str, int] = {}
    for record in dispositions.values():
        if (
            record.closed_at
            and since <= record.closed_at <= now_iso
            and record.disposition is not None
        ):
            key = record.disposition.value
            counts[key] = counts.get(key, 0) + 1
    return tuple(ClosedEntry(disposition=d, count=n) for d, n in sorted(counts.items()))


def _trend_direction(current: int, previous: int) -> TrendDirection:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "same"


def _recovery_trend(audit_counts: AuditWindowCounts) -> tuple[RecoveryTrendEntry, ...]:
    classes = sorted(set(audit_counts.current) | set(audit_counts.previous))
    entries: list[RecoveryTrendEntry] = []
    for cls in classes:
        current = audit_counts.current.get(cls, 0)
        previous = audit_counts.previous.get(cls, 0)
        entries.append(
            RecoveryTrendEntry(
                incident_class=cls,
                current_count=current,
                previous_count=previous,
                direction=_trend_direction(current, previous),
            )
        )
    return tuple(entries)


def recovery_totals(recovery_trend: Sequence[RecoveryTrendEntry]) -> RecoveryTotals:
    current = sum(e.current_count for e in recovery_trend)
    previous = sum(e.previous_count for e in recovery_trend)
    return RecoveryTotals(
        current_count=current,
        previous_count=previous,
        direction=_trend_direction(current, previous),
    )


def _is_in_force(record: DispositionRecord, now: datetime) -> bool:
    s = record.suppression
    if s is None:
        return False
    if now.timestamp() >= s.ttl_epoch:
        return False
    return record.occurrence_count < s.recurrence_threshold


def _suppression_entries(
    dispositions: dict[str, DispositionRecord], now: datetime
) -> tuple[SuppressionEntry, ...]:
    entries: list[SuppressionEntry] = []
    for fingerprint, record in sorted(dispositions.items()):
        s = record.suppression
        if s is None or not _is_in_force(record, now):
            continue
        entries.append(
            SuppressionEntry(
                fingerprint=fingerprint,
                incident_class=record.incident_class,
                reason=s.reason,
                remaining_seconds=max(0.0, s.ttl_epoch - now.timestamp()),
            )
        )
    return tuple(entries)


def _residue_entries(
    incidents: list[Incident], dispositions: dict[str, DispositionRecord], now: datetime
) -> tuple[ResidueEntry, ...]:
    entries: list[ResidueEntry] = []
    for incident in incidents:
        if incident.incident_class != IncidentClass.RESIDUE:
            continue
        record = dispositions.get(incident.fingerprint)
        if record is not None and (record.disposition is not None or _is_in_force(record, now)):
            continue
        entries.append(ResidueEntry(fingerprint=incident.fingerprint, title=incident.title))
    return tuple(entries)


def _touched_in_window(record: DispositionRecord, since: str, now_iso: str) -> bool:
    timestamps = [
        t for t in (record.opened_at, record.closed_at, record.last_seen, record.ack_at) if t
    ]
    if not timestamps:
        return False
    return since <= max(timestamps) <= now_iso


def render_digest(
    dispositions: dict[str, DispositionRecord],
    incidents: list[Incident],
    audit_counts: AuditWindowCounts,
    since: str,
    now: datetime,
    shop_stats: str = "",
) -> DigestDoc:
    now_iso = now.isoformat()
    fingerprints = frozenset(
        fp for fp, record in dispositions.items() if _touched_in_window(record, since, now_iso)
    )
    return DigestDoc(
        since=since,
        generated_at=now_iso,
        opened=_opened_entries(dispositions, since, now_iso),
        closed=_closed_entries(dispositions, since, now_iso),
        recovery_trend=_recovery_trend(audit_counts),
        suppressions=_suppression_entries(dispositions, now),
        residue=_residue_entries(incidents, dispositions, now),
        cost_note=COST_NOTE,
        fingerprints=fingerprints,
        shop_stats=shop_stats,
    )


def digest_to_dict(doc: DigestDoc) -> dict[str, Any]:
    return {
        "since": doc.since,
        "generated_at": doc.generated_at,
        "opened": [{"incident_class": e.incident_class, "count": e.count} for e in doc.opened],
        "closed": [{"disposition": e.disposition, "count": e.count} for e in doc.closed],
        "recovery_trend": [
            {
                "incident_class": e.incident_class,
                "current_count": e.current_count,
                "previous_count": e.previous_count,
                "direction": e.direction,
            }
            for e in doc.recovery_trend
        ],
        "suppressions": [
            {
                "fingerprint": e.fingerprint,
                "incident_class": e.incident_class,
                "reason": e.reason,
                "remaining_seconds": e.remaining_seconds,
            }
            for e in doc.suppressions
        ],
        "residue": [{"fingerprint": e.fingerprint, "title": e.title} for e in doc.residue],
        "cost_note": doc.cost_note,
        "fingerprints": sorted(doc.fingerprints),
        "shop_stats": doc.shop_stats,
    }


def _str_field(d: dict[str, Any], key: str) -> str:
    val: Any = d.get(key)
    return val if isinstance(val, str) else ""


def _int_field(d: dict[str, Any], key: str) -> int:
    val: Any = d.get(key)
    return val if isinstance(val, int) else 0


def _float_field(d: dict[str, Any], key: str) -> float:
    val: Any = d.get(key)
    return float(val) if isinstance(val, (int, float)) else 0.0


def _direction_field(d: dict[str, Any]) -> TrendDirection:
    val: Any = d.get("direction")
    return val if val in ("up", "down", "same") else "same"


def _as_list(raw: Any) -> list[object]:
    if not isinstance(raw, list):
        return []
    return cast("list[object]", raw)


def _parse_dict_list(raw: Any) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", item) for item in _as_list(raw) if isinstance(item, dict)]


def _parse_opened(raw: Any) -> tuple[OpenedEntry, ...]:
    return tuple(
        OpenedEntry(incident_class=_str_field(e, "incident_class"), count=_int_field(e, "count"))
        for e in _parse_dict_list(raw)
    )


def _parse_closed(raw: Any) -> tuple[ClosedEntry, ...]:
    return tuple(
        ClosedEntry(disposition=_str_field(e, "disposition"), count=_int_field(e, "count"))
        for e in _parse_dict_list(raw)
    )


def _parse_recovery_trend(raw: Any) -> tuple[RecoveryTrendEntry, ...]:
    return tuple(
        RecoveryTrendEntry(
            incident_class=_str_field(e, "incident_class"),
            current_count=_int_field(e, "current_count"),
            previous_count=_int_field(e, "previous_count"),
            direction=_direction_field(e),
        )
        for e in _parse_dict_list(raw)
    )


def _parse_suppressions(raw: Any) -> tuple[SuppressionEntry, ...]:
    return tuple(
        SuppressionEntry(
            fingerprint=_str_field(e, "fingerprint"),
            incident_class=_str_field(e, "incident_class"),
            reason=_str_field(e, "reason"),
            remaining_seconds=_float_field(e, "remaining_seconds"),
        )
        for e in _parse_dict_list(raw)
    )


def _parse_residue(raw: Any) -> tuple[ResidueEntry, ...]:
    return tuple(
        ResidueEntry(fingerprint=_str_field(e, "fingerprint"), title=_str_field(e, "title"))
        for e in _parse_dict_list(raw)
    )


def _parse_fingerprints(raw: Any) -> frozenset[str]:
    return frozenset(item for item in _as_list(raw) if isinstance(item, str))


def digest_from_dict(d: dict[str, Any]) -> DigestDoc:
    return DigestDoc(
        since=_str_field(d, "since"),
        generated_at=_str_field(d, "generated_at"),
        opened=_parse_opened(d.get("opened")),
        closed=_parse_closed(d.get("closed")),
        recovery_trend=_parse_recovery_trend(d.get("recovery_trend")),
        suppressions=_parse_suppressions(d.get("suppressions")),
        residue=_parse_residue(d.get("residue")),
        cost_note=_str_field(d, "cost_note") or COST_NOTE,
        fingerprints=_parse_fingerprints(d.get("fingerprints")),
        shop_stats=_str_field(d, "shop_stats"),
    )


__all__ = [
    "COST_NOTE",
    "AuditWindowCounts",
    "ClosedEntry",
    "DigestDoc",
    "OpenedEntry",
    "RecoveryTotals",
    "RecoveryTrendEntry",
    "ResidueEntry",
    "SuppressionEntry",
    "bucket_audit_rows",
    "digest_from_dict",
    "digest_to_dict",
    "recovery_totals",
    "render_digest",
]
