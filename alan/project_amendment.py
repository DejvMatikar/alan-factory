from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import alan.project as project_mod
from alan.errors import AppError
from alan.project import Project, ProjectStatus
from alan.queue import Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit

PENDING_AMENDMENTS_META_KEY = "project_pending_amendments_v1"
_AMENDMENT_AUDIT_FILE = "project_amendment_audit.jsonl"
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")

AmendmentKind = Literal["vision_scope", "roadmap_reorder", "roadmap_edit"]
VISION_SCOPE_KIND: AmendmentKind = "vision_scope"
_AMENDMENT_KINDS: frozenset[str] = frozenset(
    {"vision_scope", "roadmap_reorder", "roadmap_edit"}
)

_lock = threading.Lock()


@dataclass(frozen=True)
class PendingAmendment:
    project_id: str
    kind: AmendmentKind
    vision: str
    scope: str
    decision_entry: str
    payload: dict[str, Any]
    proposed_by: str
    proposed_at: str


def amendment_fingerprint(
    project_id: str,
    vision: str,
    scope: str,
    decision_entry: str,
    *,
    kind: AmendmentKind = VISION_SCOPE_KIND,
    payload: dict[str, Any] | None = None,
) -> str:
    raw_parts: list[Any] = [project_id, vision, scope, decision_entry]
    if kind != VISION_SCOPE_KIND:
        raw_parts.extend([kind, payload or {}])
    raw = json.dumps(raw_parts, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def require_valid_fingerprint(fingerprint: str) -> None:
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise AppError(
            status_code=422,
            detail="fingerprint must be a 16-character lowercase hex string",
        )


def _str_or_default(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _kind_or_default(v: Any) -> AmendmentKind:
    if isinstance(v, str) and v in _AMENDMENT_KINDS:
        return cast(AmendmentKind, v)
    return VISION_SCOPE_KIND


def _payload_or_default(v: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", v) if isinstance(v, dict) else {}


def _record_to_dict(r: PendingAmendment) -> dict[str, Any]:
    return {
        "project_id": r.project_id,
        "kind": r.kind,
        "vision": r.vision,
        "scope": r.scope,
        "decision_entry": r.decision_entry,
        "payload": r.payload,
        "proposed_by": r.proposed_by,
        "proposed_at": r.proposed_at,
    }


def _record_from_dict(d: dict[str, Any]) -> PendingAmendment | None:
    project_id = d.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    return PendingAmendment(
        project_id=project_id,
        kind=_kind_or_default(d.get("kind")),
        vision=_str_or_default(d.get("vision")),
        scope=_str_or_default(d.get("scope")),
        decision_entry=_str_or_default(d.get("decision_entry")),
        payload=_payload_or_default(d.get("payload")),
        proposed_by=_str_or_default(d.get("proposed_by")) or "chat",
        proposed_at=_str_or_default(d.get("proposed_at")),
    )


def load_pending_amendments(queue: Queue) -> dict[str, PendingAmendment]:
    raw = queue.get_meta(PENDING_AMENDMENTS_META_KEY)
    if raw is None:
        return {}
    try:
        loaded: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    result: dict[str, PendingAmendment] = {}
    for fingerprint, rec in cast(dict[str, Any], loaded).items():
        if not isinstance(rec, dict):
            continue
        parsed = _record_from_dict(cast(dict[str, Any], rec))
        if parsed is not None:
            result[fingerprint] = parsed
    return result


def _save_pending_amendments(queue: Queue, records: dict[str, PendingAmendment]) -> None:
    blob = {fp: _record_to_dict(r) for fp, r in records.items()}
    queue.set_meta(PENDING_AMENDMENTS_META_KEY, json.dumps(blob))


def propose_amendment(
    queue: Queue,
    project_id: str,
    *,
    vision: str = "",
    scope: str = "",
    decision_entry: str = "",
    kind: AmendmentKind = VISION_SCOPE_KIND,
    payload: dict[str, Any] | None = None,
    proposed_by: str = "chat",
    now: datetime,
) -> tuple[str, PendingAmendment]:
    resolved_payload = payload or {}
    fingerprint = amendment_fingerprint(
        project_id, vision, scope, decision_entry, kind=kind, payload=resolved_payload
    )
    record = PendingAmendment(
        project_id=project_id,
        kind=kind,
        vision=vision,
        scope=scope,
        decision_entry=decision_entry,
        payload=resolved_payload,
        proposed_by=proposed_by,
        proposed_at=now.isoformat(),
    )
    with _lock:
        records = load_pending_amendments(queue)
        records[fingerprint] = record
        _save_pending_amendments(queue, records)
    return fingerprint, record


def get_pending_amendment(queue: Queue, fingerprint: str) -> PendingAmendment | None:
    return load_pending_amendments(queue).get(fingerprint)


def claim_pending_amendment(queue: Queue, fingerprint: str) -> PendingAmendment | None:
    """Atomically pop `fingerprint`'s record so at most one caller can ever apply it."""
    with _lock:
        records = load_pending_amendments(queue)
        record = records.pop(fingerprint, None)
        if record is not None:
            _save_pending_amendments(queue, records)
        return record


def delete_pending_amendment(queue: Queue, fingerprint: str) -> None:
    claim_pending_amendment(queue, fingerprint)


def delete_pending_amendments_for_project(queue: Queue, project_id: str) -> None:
    with _lock:
        records = load_pending_amendments(queue)
        remaining = {fp: rec for fp, rec in records.items() if rec.project_id != project_id}
        if len(remaining) != len(records):
            _save_pending_amendments(queue, remaining)


def resolve_pending_amendment(
    queue: Queue, fingerprint: str, *, root: Path | None = None
) -> tuple[PendingAmendment, Project] | None:
    """Re-derive a pending amendment's project from durable state.

    Refuses (returns None) a fingerprint that was never proposed or has
    already been applied (record gone), a project that no longer resolves
    (stale), or a project that has been archived (closed) — never trusts a
    cached view of any of the three.
    """
    record = get_pending_amendment(queue, fingerprint)
    if record is None:
        return None
    try:
        project = project_mod.read(record.project_id, root=root)
    except project_mod.ProjectError:
        return None
    if project.status == ProjectStatus.ARCHIVED:
        return None
    return record, project


def record_amendment_approval(fingerprint: str, record: PendingAmendment, *, now: datetime) -> None:
    append_jsonl_audit(
        logs_root() / _AMENDMENT_AUDIT_FILE,
        {
            "fingerprint": fingerprint,
            "project_id": record.project_id,
            "kind": record.kind,
            "vision": record.vision,
            "scope": record.scope,
            "decision_entry": record.decision_entry,
            "payload": record.payload,
            "approved_at": now.isoformat(),
        },
        log_label="project_amendment",
    )


__all__ = [
    "PENDING_AMENDMENTS_META_KEY",
    "VISION_SCOPE_KIND",
    "AmendmentKind",
    "PendingAmendment",
    "amendment_fingerprint",
    "claim_pending_amendment",
    "delete_pending_amendment",
    "delete_pending_amendments_for_project",
    "get_pending_amendment",
    "load_pending_amendments",
    "propose_amendment",
    "record_amendment_approval",
    "require_valid_fingerprint",
    "resolve_pending_amendment",
]
