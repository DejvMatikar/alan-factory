from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AliasPath,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StrictBool,
    TypeAdapter,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
)

from alan.incident import Disposition, Incident, Suppression, dedupe_by_fingerprint
from alan.queue import Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit

logger = logging.getLogger("alan.incident_store")

DISPOSITIONS_META_KEY = "incident_dispositions_v1"

_TTL_DAYS_ENV = "ALAN_INCIDENT_DISPOSITION_TTL_DAYS"
_AUDIT_FILE = "incident_disposition_prune_audit.jsonl"


def _logs_dir() -> Path:
    return logs_root()


def _default_on_error(default: object) -> WrapValidator:
    def fallback(value: object, handler: ValidatorFunctionWrapHandler) -> object:
        try:
            return handler(value)
        except ValidationError:
            return default

    return WrapValidator(fallback)


def _safe_int(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _text_or_none(raw: Any) -> str | None:
    return None if raw is None else str(raw)


def _nest_ack(ack_at: str | None) -> dict[str, str] | None:
    return None if ack_at is None else {"at": ack_at}


_Text = Annotated[str, BeforeValidator(str)]
_OptionalText = Annotated[str | None, BeforeValidator(_text_or_none)]
_Count = Annotated[int, PlainValidator(_safe_int)]


class DispositionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, validate_by_name=True, validate_by_alias=True)

    incident_class: _Text = Field(default="", validation_alias="class", serialization_alias="class")
    disposition: Annotated[Disposition | None, _default_on_error(None)] = None
    disposition_detail: _OptionalText = None
    suppression: Annotated[Suppression | None, _default_on_error(None)] = None
    ack_at: Annotated[_OptionalText, PlainSerializer(_nest_ack)] = Field(
        default=None, validation_alias=AliasPath("ack", "at"), serialization_alias="ack"
    )
    opened_at: _Text = ""
    closed_at: _OptionalText = None
    fingerprint_at_close: _OptionalText = None
    occurrence_at_close: Annotated[int | None, _default_on_error(None)] = None
    occurrence_count: _Count = 0
    last_seen: _Text = ""
    recurrence_counter: _Count = 0
    diagnosis_falsified: Annotated[bool, PlainValidator(bool)] = False
    auto_disposed: Annotated[StrictBool, _default_on_error(False)] = False


_RECORDS_ADAPTER = TypeAdapter(dict[str, Any])


def load_dispositions(queue: Queue) -> dict[str, DispositionRecord]:
    raw = queue.get_meta(DISPOSITIONS_META_KEY)
    if raw is None:
        return {}
    try:
        loaded = _RECORDS_ADAPTER.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError):
        return {}
    result: dict[str, DispositionRecord] = {}
    for fingerprint, rec in loaded.items():
        try:
            result[fingerprint] = DispositionRecord.model_validate(rec, by_name=False)
        except ValidationError:
            continue
    return result


def _save_dispositions(queue: Queue, records: dict[str, DispositionRecord]) -> None:
    blob = {fp: r.model_dump(by_alias=True) for fp, r in records.items()}
    queue.set_meta(DISPOSITIONS_META_KEY, json.dumps(blob))


def _replace(record: DispositionRecord, **changes: Any) -> DispositionRecord:
    return DispositionRecord(**{**vars(record), **changes})


def _existing_or_new(
    records: dict[str, DispositionRecord], fingerprint: str, incident_class: str, now_iso: str
) -> DispositionRecord:
    existing = records.get(fingerprint)
    if existing is not None:
        return existing
    return DispositionRecord(
        incident_class=incident_class, opened_at=now_iso, last_seen=now_iso, occurrence_count=1
    )


def set_disposition(
    queue: Queue,
    fingerprint: str,
    disposition: Disposition | None,
    *,
    incident_class: str = "",
    detail: str | None = None,
    now: datetime,
    auto_disposed: bool = False,
) -> DispositionRecord:
    records = load_dispositions(queue)
    now_iso = now.isoformat()
    base = _existing_or_new(records, fingerprint, incident_class, now_iso)
    if disposition is not None:
        updated = _replace(
            base,
            disposition=disposition,
            disposition_detail=detail,
            closed_at=now_iso,
            fingerprint_at_close=fingerprint,
            occurrence_at_close=base.occurrence_count,
            auto_disposed=auto_disposed,
        )
    else:
        updated = _replace(
            base,
            disposition=None,
            disposition_detail=detail,
            closed_at=None,
            fingerprint_at_close=None,
            occurrence_at_close=None,
            auto_disposed=False,
        )
    records[fingerprint] = updated
    _save_dispositions(queue, records)
    return updated


def set_suppression(
    queue: Queue,
    fingerprint: str,
    suppression: Suppression,
    *,
    incident_class: str = "",
    now: datetime,
) -> DispositionRecord:
    records = load_dispositions(queue)
    now_iso = now.isoformat()
    base = _existing_or_new(records, fingerprint, incident_class, now_iso)
    updated = _replace(base, suppression=suppression)
    records[fingerprint] = updated
    _save_dispositions(queue, records)
    return updated


def ack(
    queue: Queue,
    fingerprint: str,
    *,
    incident_class: str = "",
    now: datetime,
) -> DispositionRecord:
    records = load_dispositions(queue)
    now_iso = now.isoformat()
    base = _existing_or_new(records, fingerprint, incident_class, now_iso)
    updated = _replace(base, ack_at=now_iso)
    records[fingerprint] = updated
    _save_dispositions(queue, records)
    return updated


def is_closed(record: DispositionRecord) -> bool:
    return record.disposition is not None


def is_suppressed(record: DispositionRecord, *, now: datetime) -> bool:
    s = record.suppression
    if s is None:
        return False
    if now.timestamp() >= s.ttl_epoch:
        return False
    return record.occurrence_count < s.recurrence_threshold


def is_acked(record: DispositionRecord, *, fresh_last_seen: str) -> bool:
    if record.ack_at is None:
        return False
    return not _is_after(fresh_last_seen, record.ack_at)


def _bump_for_fresh(record: DispositionRecord, fresh: Incident, now_iso: str) -> DispositionRecord:
    return _replace(
        record, occurrence_count=record.occurrence_count + 1, last_seen=fresh.last_seen or now_iso
    )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _is_after(candidate: Any, reference: Any) -> bool:
    parsed_candidate = _parse_iso(candidate)
    parsed_reference = _parse_iso(reference)
    if parsed_candidate is not None and parsed_reference is not None:
        try:
            return parsed_candidate > parsed_reference
        except TypeError:
            pass
    return str(candidate) > str(reference)


def _maybe_reopen(record: DispositionRecord, fresh: Incident | None) -> DispositionRecord:
    eligible = record.disposition == Disposition.ROOT_CAUSED or record.auto_disposed
    if not eligible or record.closed_at is None:
        return record
    if fresh is None or not _is_after(record.last_seen, record.closed_at):
        return record
    return _replace(
        record,
        disposition=None,
        disposition_detail=None,
        closed_at=None,
        fingerprint_at_close=None,
        occurrence_at_close=None,
        auto_disposed=False,
        diagnosis_falsified=True,
        recurrence_counter=record.recurrence_counter + 1,
    )


def _maybe_unsuppress(record: DispositionRecord, now: datetime) -> DispositionRecord:
    if record.suppression is None:
        return record
    if is_suppressed(record, now=now):
        return record
    return _replace(record, suppression=None)


def _reconcile_one(
    record: DispositionRecord,
    fresh: Incident | None,
    now: datetime,
    now_iso: str,
    on_falsified: Callable[[DispositionRecord], None] | None,
) -> DispositionRecord:
    next_record = record
    if fresh is not None:
        next_record = _bump_for_fresh(next_record, fresh, now_iso)
    reopened = _maybe_reopen(next_record, fresh)
    if reopened is not next_record:
        if on_falsified is not None:
            on_falsified(next_record)
        next_record = reopened
    return _maybe_unsuppress(next_record, now)


def _seed_new_records(
    updated: dict[str, DispositionRecord], fresh_by_fp: dict[str, Incident], now_iso: str
) -> None:
    for fp, fresh in fresh_by_fp.items():
        if fp not in updated:
            updated[fp] = DispositionRecord(
                incident_class=fresh.incident_class.value,
                opened_at=now_iso,
                last_seen=fresh.last_seen or now_iso,
                occurrence_count=1,
            )


def reconcile_reopen(
    queue: Queue,
    fresh_incidents: list[Incident],
    *,
    now: datetime,
    on_falsified: Callable[[DispositionRecord], None] | None = None,
) -> dict[str, DispositionRecord]:
    records = load_dispositions(queue)
    now_iso = now.isoformat()
    fresh_by_fp = {i.fingerprint: i for i in dedupe_by_fingerprint(fresh_incidents)}
    updated = {
        fp: _reconcile_one(record, fresh_by_fp.get(fp), now, now_iso, on_falsified)
        for fp, record in records.items()
    }
    _seed_new_records(updated, fresh_by_fp, now_iso)
    _save_dispositions(queue, updated)
    return updated


def resolve_disposition_ttl_days() -> int | None:
    raw = os.environ.get(_TTL_DAYS_ENV, "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        logger.warning(
            "incident_store: invalid %s=%r; disposition prune disabled", _TTL_DAYS_ENV, raw
        )
        return None
    return days if days > 0 else None


def _is_prunable(record: DispositionRecord, cutoff: datetime) -> bool:
    if not is_closed(record):
        return False
    closed_at = _parse_iso(record.closed_at)
    if closed_at is None:
        return False
    try:
        return closed_at < cutoff
    except TypeError:
        return False


def _audit_prune(removed: int, ttl_days: int, now: datetime) -> None:
    append_jsonl_audit(
        _logs_dir() / _AUDIT_FILE,
        {"removed": removed, "ttl_days": ttl_days, "pruned_at": now.isoformat()},
        log_label="incident_store",
    )


def prune_dispositions(queue: Queue, *, now: datetime) -> int:
    ttl_days = resolve_disposition_ttl_days()
    if ttl_days is None:
        return 0
    cutoff = now - timedelta(days=ttl_days)
    records = load_dispositions(queue)
    kept = {fp: r for fp, r in records.items() if not _is_prunable(r, cutoff)}
    removed = len(records) - len(kept)
    if removed == 0:
        return 0
    _save_dispositions(queue, kept)
    _audit_prune(removed, ttl_days, now)
    logger.info(
        "incident_store: pruned %d disposition record(s) past %d-day TTL", removed, ttl_days
    )
    return removed


__all__ = [
    "DISPOSITIONS_META_KEY",
    "DispositionRecord",
    "ack",
    "is_acked",
    "is_closed",
    "is_suppressed",
    "load_dispositions",
    "prune_dispositions",
    "reconcile_reopen",
    "resolve_disposition_ttl_days",
    "set_disposition",
    "set_suppression",
]
