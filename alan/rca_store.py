from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, cast

from alan.queue import Queue

RCA_DRAFTS_META_KEY = "rca_drafts_v1"

_VALID_CITATION_KINDS = frozenset({"audit_row", "log_line", "replay_command"})

_lock = threading.Lock()


class RcaSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Citation:
    kind: str
    file: str | None = None
    json_path: str | None = None
    grep_pattern: str | None = None
    argv: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_CITATION_KINDS:
            raise RcaSchemaError(f"citation kind {self.kind!r} is not a recognized citation kind")
        if not _citation_has_locator(self):
            raise RcaSchemaError(f"citation of kind {self.kind!r} is missing a concrete locator")


def _citation_has_locator(c: Citation) -> bool:
    if c.kind == "audit_row":
        return bool(c.file) and bool(c.json_path)
    if c.kind == "log_line":
        return bool(c.file) and bool(c.grep_pattern)
    return bool(c.argv)


@dataclass(frozen=True)
class TimelineEntry:
    description: str
    citation: Citation


@dataclass(frozen=True)
class RcaDraft:
    source: str
    proposed_disposition: str | None
    timeline: tuple[TimelineEntry, ...]
    falsification_test: str
    recurrence_signature: str
    recipe: str
    recurrence_signal: str
    verified: bool = False
    workspace_key: str | None = None
    created_at: float | None = None

    def __post_init__(self) -> None:
        if not self.falsification_test:
            raise RcaSchemaError("RcaDraft requires a non-empty falsification_test")


def _opt_str(v: Any) -> str | None:
    return str(v) if v is not None else None


def _str_or_default(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _citation_to_dict(c: Citation) -> dict[str, Any]:
    return {
        "kind": c.kind,
        "file": c.file,
        "json_path": c.json_path,
        "grep_pattern": c.grep_pattern,
        "argv": list(c.argv) if c.argv is not None else None,
    }


def _citation_from_dict(d: Any) -> Citation | None:
    if not isinstance(d, dict):
        return None
    data = cast(dict[str, Any], d)
    kind = data.get("kind")
    if kind not in _VALID_CITATION_KINDS:
        return None
    argv_raw = data.get("argv")
    argv = (
        tuple(str(a) for a in cast("list[object]", argv_raw))
        if isinstance(argv_raw, list)
        else None
    )
    try:
        return Citation(
            kind=str(kind),
            file=_opt_str(data.get("file")),
            json_path=_opt_str(data.get("json_path")),
            grep_pattern=_opt_str(data.get("grep_pattern")),
            argv=argv,
        )
    except RcaSchemaError:
        return None


def _timeline_entry_to_dict(e: TimelineEntry) -> dict[str, Any]:
    return {"description": e.description, "citation": _citation_to_dict(e.citation)}


def _timeline_entry_from_dict(d: Any) -> TimelineEntry | None:
    if not isinstance(d, dict):
        return None
    data = cast(dict[str, Any], d)
    citation = _citation_from_dict(data.get("citation"))
    if citation is None:
        return None
    return TimelineEntry(description=_str_or_default(data.get("description")), citation=citation)


def _timeline_from_list(raw: Any) -> tuple[TimelineEntry, ...]:
    if not isinstance(raw, list):
        return ()
    entries = (_timeline_entry_from_dict(item) for item in cast("list[object]", raw))
    return tuple(e for e in entries if e is not None)


def _draft_to_dict(r: RcaDraft) -> dict[str, Any]:
    return {
        "source": r.source,
        "proposed_disposition": r.proposed_disposition,
        "timeline": [_timeline_entry_to_dict(e) for e in r.timeline],
        "falsification_test": r.falsification_test,
        "recurrence_signature": r.recurrence_signature,
        "recipe": r.recipe,
        "recurrence_signal": r.recurrence_signal,
        "verified": r.verified,
        "workspace_key": r.workspace_key,
        "created_at": r.created_at,
    }


def _created_at_or_none(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _record_from_dict(d: dict[str, Any]) -> RcaDraft | None:
    falsification_test = d.get("falsification_test")
    if not isinstance(falsification_test, str) or not falsification_test:
        return None
    source = d.get("source")
    proposed_disposition = d.get("proposed_disposition")
    workspace_key = d.get("workspace_key")
    return RcaDraft(
        source=str(source) if isinstance(source, str) else "investigator",
        proposed_disposition=(
            str(proposed_disposition) if isinstance(proposed_disposition, str) else None
        ),
        timeline=_timeline_from_list(d.get("timeline")),
        falsification_test=falsification_test,
        recurrence_signature=_str_or_default(d.get("recurrence_signature")),
        recipe=_str_or_default(d.get("recipe")),
        recurrence_signal=_str_or_default(d.get("recurrence_signal")),
        verified=d.get("verified") is True,
        workspace_key=workspace_key if isinstance(workspace_key, str) else None,
        created_at=_created_at_or_none(d.get("created_at")),
    )


def _insert_valid_draft(result: dict[str, RcaDraft], fingerprint: str, rec: Any) -> None:
    if not isinstance(rec, dict):
        return
    draft = _record_from_dict(cast(dict[str, Any], rec))
    if draft is not None:
        result[fingerprint] = draft


def load_rca_drafts(queue: Queue) -> dict[str, RcaDraft]:
    raw = queue.get_meta(RCA_DRAFTS_META_KEY)
    if raw is None:
        return {}
    try:
        loaded: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    result: dict[str, RcaDraft] = {}
    for fingerprint, rec in cast(dict[str, Any], loaded).items():
        _insert_valid_draft(result, fingerprint, rec)
    return result


def _save_rca_drafts(queue: Queue, records: dict[str, RcaDraft]) -> None:
    blob = {fp: _draft_to_dict(r) for fp, r in records.items()}
    queue.set_meta(RCA_DRAFTS_META_KEY, json.dumps(blob))


def save_rca_draft(queue: Queue, fingerprint: str, draft: RcaDraft) -> RcaDraft:
    with _lock:
        records = load_rca_drafts(queue)
        records[fingerprint] = draft
        _save_rca_drafts(queue, records)
    return draft


def get_rca_draft(queue: Queue, fingerprint: str) -> RcaDraft | None:
    return load_rca_drafts(queue).get(fingerprint)


__all__ = [
    "Citation",
    "RCA_DRAFTS_META_KEY",
    "RcaDraft",
    "RcaSchemaError",
    "TimelineEntry",
    "get_rca_draft",
    "load_rca_drafts",
    "save_rca_draft",
]
