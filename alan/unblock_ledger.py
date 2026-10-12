from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from alan.queue import Queue

ATTEMPT_CEILING_PER_FINGERPRINT = 2
ATTEMPT_CEILING_PER_WORKSPACE = 5
BAR_LOWERING_CEILING = 2
ISSUE_INTENT_MAX_ATTEMPTS = 20
DAY_S = 86400.0
RESCUE_WINDOW_DAYS = 30
RESCUE_LOW_DATA_MIN_SAMPLE = 10
UNBLOCK_WATCH_META_KEY = "unblock_watch_v1"
REPROBE_CAP = 72

KIND_ATTEMPT = "attempt"
KIND_WAIT = "wait"
KIND_REPORT = "report"
KIND_ISSUE_INTENT = "issue_intent"
KIND_ISSUE_DELIVERED = "issue_delivered"
KIND_ISSUE_ATTEMPT = "issue_attempt"

OUTCOME_ACTED = "acted"
OUTCOME_NEEDS_HUMAN = "needs_human"
OUTCOME_NOT_YET = "not_yet"

_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS unblock_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        engine_key TEXT,
        workspace_key TEXT NOT NULL,
        scope_ref TEXT NOT NULL,
        shape TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL,
        action TEXT,
        outcome TEXT,
        head_sha TEXT,
        handover_id TEXT,
        bar_lowering INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        detail TEXT NOT NULL DEFAULT '',
        ref_id INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS unblock_ledger_ws ON unblock_ledger(workspace_key, kind)",
    "CREATE INDEX IF NOT EXISTS unblock_ledger_fp ON unblock_ledger(fingerprint, kind)",
    "CREATE INDEX IF NOT EXISTS unblock_ledger_ref ON unblock_ledger(kind, ref_id)",
)
_INSERT_SQL = (
    "INSERT INTO unblock_ledger (ts, engine_key, workspace_key, scope_ref, shape, fingerprint,"
    " kind, action, outcome, head_sha, handover_id, bar_lowering, cost_usd, detail, ref_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id"
)
_COUNT_FP_SQL = (
    "SELECT COUNT(*) AS n FROM unblock_ledger"
    " WHERE kind = ? AND workspace_key = ? AND fingerprint = ?"
)
_REPORT_HISTORY_SQL = (
    "SELECT COUNT(*) AS n, MIN(ts) AS first_ts FROM unblock_ledger"
    " WHERE kind = ? AND workspace_key = ? AND fingerprint = ?"
)
_COUNT_WS_SQL = "SELECT COUNT(*) AS n FROM unblock_ledger WHERE kind = ? AND workspace_key = ?"
_COUNT_BAR_SQL = (
    "SELECT COUNT(*) AS n FROM unblock_ledger"
    " WHERE kind = ? AND workspace_key = ? AND bar_lowering = 1"
)
_LAST_RECORD_SQL = (
    "SELECT * FROM unblock_ledger WHERE workspace_key = ? AND kind IN (?, ?, ?)"
    " ORDER BY id DESC LIMIT 1"
)
_LAST_DELIVERED_SQL = (
    "SELECT * FROM unblock_ledger WHERE workspace_key = ? AND kind = ?"
    " ORDER BY id DESC LIMIT 1"
)
_PENDING_INTENTS_SQL = (
    "SELECT i.* FROM unblock_ledger i WHERE i.kind = ?"
    " AND NOT EXISTS (SELECT 1 FROM unblock_ledger d WHERE d.kind = ? AND d.ref_id = i.id)"
    " AND (SELECT COUNT(*) FROM unblock_ledger a WHERE a.kind = ? AND a.ref_id = i.id) < ?"
    " ORDER BY i.id LIMIT ?"
)
_COUNT_SINCE_SQL = "SELECT COUNT(*) AS n FROM unblock_ledger WHERE kind = ? AND ts >= ?"
_COUNT_OUTCOME_SINCE_SQL = (
    "SELECT COUNT(*) AS n FROM unblock_ledger WHERE kind = ? AND outcome = ? AND ts >= ?"
)


@dataclass(frozen=True)
class LedgerRow:
    id: int
    ts: float
    engine_key: str | None
    workspace_key: str
    scope_ref: str
    shape: str
    fingerprint: str
    kind: str
    action: str | None
    outcome: str | None
    head_sha: str | None
    handover_id: str | None
    bar_lowering: bool
    cost_usd: float
    detail: dict[str, Any]
    ref_id: int | None


@dataclass(frozen=True)
class WatchEntry:
    next_at: float
    attempts: int
    identity: str
    acted: bool = False
    retried: bool = False
    terminal: bool = False
    park_head: str | None = None

    @property
    def exhausted(self) -> bool:
        return self.terminal and self.attempts >= REPROBE_CAP


class UnblockRecord(BaseModel):
    shape: str
    attempts: int
    ceiling: int
    outcome: str
    summary: str
    issue_url: str | None
    at: float
    action: str | None = None
    reason: str | None = None
    authority_holder: str | None = None
    watch_attempts: int | None = None
    watch_cap: int | None = None
    watch_next_at: float | None = None
    watch_exhausted: bool | None = None


def ensure_schema(queue: Queue) -> None:
    for ddl in _DDL:
        queue.run(ddl)


def _parse_detail(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


def _row_from_sql(row: sqlite3.Row) -> LedgerRow:
    return LedgerRow(
        id=int(row["id"]),
        ts=float(row["ts"]),
        engine_key=row["engine_key"],
        workspace_key=str(row["workspace_key"]),
        scope_ref=str(row["scope_ref"]),
        shape=str(row["shape"]),
        fingerprint=str(row["fingerprint"]),
        kind=str(row["kind"]),
        action=row["action"],
        outcome=row["outcome"],
        head_sha=row["head_sha"],
        handover_id=row["handover_id"],
        bar_lowering=bool(row["bar_lowering"]),
        cost_usd=float(row["cost_usd"]),
        detail=_parse_detail(row["detail"]),
        ref_id=row["ref_id"],
    )


@dataclass(frozen=True)
class Entry:
    workspace_key: str
    scope_ref: str
    shape: str
    fingerprint: str
    kind: str
    engine_key: str | None = None
    action: str | None = None
    outcome: str | None = None
    head_sha: str | None = None
    handover_id: str | None = None
    bar_lowering: bool = False
    cost_usd: float = 0.0
    detail: dict[str, Any] | None = None
    ref_id: int | None = None
    ts: float | None = None


def record(queue: Queue, entry: Entry) -> int:
    rows = queue.run(
        _INSERT_SQL,
        (
            time.time() if entry.ts is None else entry.ts, entry.engine_key, entry.workspace_key,
            entry.scope_ref, entry.shape, entry.fingerprint, entry.kind, entry.action,
            entry.outcome, entry.head_sha, entry.handover_id, int(entry.bar_lowering),
            entry.cost_usd, json.dumps(entry.detail or {}, sort_keys=True), entry.ref_id,
        ),
    )
    return int(rows[0]["id"])


def _count(queue: Queue, sql: str, params: tuple[object, ...]) -> int:
    return int(queue.run(sql, params)[0]["n"])


def attempts_for_fingerprint(queue: Queue, workspace_key: str, fingerprint: str) -> int:
    return _count(queue, _COUNT_FP_SQL, (KIND_ATTEMPT, workspace_key, fingerprint))


@dataclass(frozen=True)
class ReportHistory:
    count: int
    first_ts: float


def report_history(queue: Queue, workspace_key: str, fingerprint: str) -> ReportHistory:
    row = queue.run(_REPORT_HISTORY_SQL, (KIND_REPORT, workspace_key, fingerprint))[0]
    return ReportHistory(count=int(row["n"]), first_ts=float(row["first_ts"] or 0.0))


def park_class(question_id: str | None, invocation_id: str, blocker_name: str | None) -> str:
    if question_id is None:
        return blocker_name or ""
    token = invocation_id[:8]
    if not token or not question_id.endswith(token):
        return question_id
    stem = question_id[: -len(token)]
    return stem[:-1] if stem.endswith(("_", "-")) else stem


def park_key(park_class_name: str, head: str | None) -> str:
    return f"{park_class_name}@{head or ''}"


def attempts_for_workspace(queue: Queue, workspace_key: str) -> int:
    return _count(queue, _COUNT_WS_SQL, (KIND_ATTEMPT, workspace_key))


def bar_lowering_count(queue: Queue, workspace_key: str) -> int:
    return _count(queue, _COUNT_BAR_SQL, (KIND_ATTEMPT, workspace_key))


def pending_issue_intents(queue: Queue, limit: int) -> list[LedgerRow]:
    rows = queue.run(
        _PENDING_INTENTS_SQL,
        (
            KIND_ISSUE_INTENT, KIND_ISSUE_DELIVERED, KIND_ISSUE_ATTEMPT,
            ISSUE_INTENT_MAX_ATTEMPTS, limit,
        ),
    )
    return [_row_from_sql(row) for row in rows]


def counters_24h(queue: Queue, now: float) -> dict[str, int]:
    since = now - DAY_S
    return {
        "unblock_attempts_24h": _count(queue, _COUNT_SINCE_SQL, (KIND_ATTEMPT, since)),
        "unblock_acted_24h": _count(
            queue, _COUNT_OUTCOME_SINCE_SQL, (KIND_ATTEMPT, OUTCOME_ACTED, since)
        ),
        "unblock_reports_24h": _count(queue, _COUNT_SINCE_SQL, (KIND_REPORT, since)),
    }


def stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def repeat_reason(reason: str, history: ReportHistory, head: str | None) -> str:
    if history.count == 0:
        return reason
    at_head = f" at head {head[:8]}" if head is not None else ""
    return (
        f"{reason} · report #{history.count + 1} of this park class{at_head}"
        f", first reported {stamp(history.first_ts)}"
    )


def _summary(row: LedgerRow, attempts: int) -> str:
    reason = str(row.detail.get("reason", ""))
    if row.kind == KIND_ATTEMPT:
        return (
            f"attempt {attempts} of {ATTEMPT_CEILING_PER_WORKSPACE} · {row.shape}"
            f" · {row.action or ''} · {row.outcome or ''} · {stamp(row.ts)}"
        )
    if row.kind == KIND_REPORT:
        return f"needs human · {row.shape} · {reason} · {stamp(row.ts)}"
    return f"waiting · {row.shape} · {reason} · {stamp(row.ts)}"


def _issue_url_for(queue: Queue, workspace_key: str) -> str | None:
    rows = queue.run(_LAST_DELIVERED_SQL, (workspace_key, KIND_ISSUE_DELIVERED))
    if not rows:
        return None
    url = _row_from_sql(rows[0]).detail.get("issue_url")
    return url if isinstance(url, str) else None


def _detail_str(row: LedgerRow, key: str) -> str | None:
    val = row.detail.get(key)
    return val if isinstance(val, str) else None


def _valid_next_at(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and value >= 0


def _valid_attempts(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _valid_park_head(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_entry(raw: object) -> WatchEntry | None:
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, Any], raw)
    next_at, attempts = entry.get("next_at"), entry.get("attempts")
    identity = entry.get("identity")
    if not (_valid_next_at(next_at) and _valid_attempts(attempts) and isinstance(identity, str)):
        return None
    return WatchEntry(
        next_at=float(cast("float", next_at)), attempts=cast("int", attempts), identity=identity,
        acted=entry.get("acted") is True, retried=entry.get("retried") is True,
        terminal=entry.get("terminal") is True, park_head=_valid_park_head(entry.get("park_head")),
    )


def load_watch(queue: Queue) -> dict[str, WatchEntry]:
    raw = queue.get_meta(UNBLOCK_WATCH_META_KEY)
    if raw is None:
        return {}
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    watch: dict[str, WatchEntry] = {}
    for task_id, entry_raw in cast(dict[str, Any], payload).items():
        entry = _parse_entry(entry_raw)
        if entry is not None:
            watch[task_id] = entry
    return watch


def _watch_fields(row: LedgerRow, entry: WatchEntry | None) -> dict[str, Any]:
    if entry is None or row.handover_id != entry.identity:
        return {}
    return {
        "watch_attempts": entry.attempts,
        "watch_cap": REPROBE_CAP,
        "watch_next_at": entry.next_at,
        "watch_exhausted": entry.exhausted,
    }


def _record_from_row(
    row: LedgerRow, attempts: int, issue_url: str | None, entry: WatchEntry | None
) -> UnblockRecord:
    return UnblockRecord(
        shape=row.shape,
        attempts=attempts,
        ceiling=ATTEMPT_CEILING_PER_WORKSPACE,
        outcome=row.outcome or row.kind,
        summary=_summary(row, attempts),
        issue_url=issue_url,
        at=row.ts,
        action=row.action,
        reason=_detail_str(row, "reason"),
        authority_holder=_detail_str(row, "authority_holder"),
        **_watch_fields(row, entry),
    )


def last_record(queue: Queue, workspace_key: str) -> UnblockRecord | None:
    ensure_schema(queue)
    rows = queue.run(_LAST_RECORD_SQL, (workspace_key, KIND_ATTEMPT, KIND_WAIT, KIND_REPORT))
    if not rows:
        return None
    row = _row_from_sql(rows[0])
    attempts = attempts_for_workspace(queue, workspace_key)
    entry = load_watch(queue).get(workspace_key)
    return _record_from_row(row, attempts, _issue_url_for(queue, workspace_key), entry)


def _in_clause(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _batch_last_rows(queue: Queue, workspace_keys: list[str]) -> dict[str, LedgerRow]:
    keys_in = _in_clause(len(workspace_keys))
    sql = (
        "SELECT * FROM unblock_ledger WHERE id IN ("  # noqa: S608  # nosec B608 - placeholders are only "?" tokens; values bound via params
        "SELECT MAX(id) FROM unblock_ledger WHERE workspace_key IN "
        f"({keys_in}) AND kind IN (?, ?, ?) GROUP BY workspace_key)"
    )
    rows = queue.run(sql, (*workspace_keys, KIND_ATTEMPT, KIND_WAIT, KIND_REPORT))
    return {str(row["workspace_key"]): _row_from_sql(row) for row in rows}


def _batch_attempt_counts(queue: Queue, workspace_keys: list[str]) -> dict[str, int]:
    keys_in = _in_clause(len(workspace_keys))
    sql = (
        "SELECT workspace_key, COUNT(*) AS n FROM unblock_ledger "  # noqa: S608  # nosec B608 - placeholders are only "?" tokens; values bound via params
        f"WHERE kind = ? AND workspace_key IN ({keys_in}) GROUP BY workspace_key"
    )
    rows = queue.run(sql, (KIND_ATTEMPT, *workspace_keys))
    return {str(row["workspace_key"]): int(row["n"]) for row in rows}


def issue_urls_for(queue: Queue, workspace_keys: list[str]) -> dict[str, str | None]:
    keys_in = _in_clause(len(workspace_keys))
    sql = (
        "SELECT * FROM unblock_ledger WHERE id IN ("  # noqa: S608  # nosec B608 - placeholders are only "?" tokens; values bound via params
        "SELECT MAX(id) FROM unblock_ledger WHERE workspace_key IN "
        f"({keys_in}) AND kind = ? GROUP BY workspace_key)"
    )
    rows = queue.run(sql, (*workspace_keys, KIND_ISSUE_DELIVERED))
    urls: dict[str, str | None] = {}
    for row in rows:
        parsed = _row_from_sql(row)
        url = parsed.detail.get("issue_url")
        urls[parsed.workspace_key] = url if isinstance(url, str) else None
    return urls


def records_for(queue: Queue, workspace_keys: Iterable[str]) -> dict[str, UnblockRecord]:
    ensure_schema(queue)
    keys = list(dict.fromkeys(workspace_keys))
    if not keys:
        return {}
    last_rows = _batch_last_rows(queue, keys)
    attempt_counts = _batch_attempt_counts(queue, keys)
    issue_urls = issue_urls_for(queue, keys)
    watch = load_watch(queue)
    return {
        key: _record_from_row(row, attempt_counts.get(key, 0), issue_urls.get(key), watch.get(key))
        for key, row in last_rows.items()
    }


def rescue_window_start(now: float, window_days: int = RESCUE_WINDOW_DAYS) -> float:
    return now - window_days * DAY_S


_ACTIVITY_ROWS_SQL = (
    "SELECT * FROM unblock_ledger WHERE kind IN (?, ?, ?) AND ts >= ?"
    " ORDER BY ts DESC, id DESC"
)


def activity_rows_since(queue: Queue, since: float) -> list[LedgerRow]:
    ensure_schema(queue)
    rows = queue.run(_ACTIVITY_ROWS_SQL, (KIND_ATTEMPT, KIND_WAIT, KIND_REPORT, since))
    return [_row_from_sql(row) for row in rows]


@dataclass(frozen=True)
class RescueStats:
    success_rate: float | None
    success_sample: int
    success_low_data: bool
    load_rate: float | None
    load_sample: int
    load_low_data: bool


_ENGAGED_KEYS_SQL = (
    "SELECT DISTINCT workspace_key FROM unblock_ledger WHERE kind IN (?, ?, ?) AND ts >= ?"
)
_REPORTED_KEYS_SQL = "SELECT DISTINCT workspace_key FROM unblock_ledger WHERE kind = ? AND ts >= ?"


def _distinct_keys(queue: Queue, sql: str, params: tuple[object, ...]) -> set[str]:
    return {str(row["workspace_key"]) for row in queue.run(sql, params)}


def rescue_stats(
    queue: Queue,
    workspace_keys: Collection[str],
    stuck_keys: Collection[str],
    now: float,
    window_days: int = RESCUE_WINDOW_DAYS,
) -> RescueStats:
    ensure_schema(queue)
    cutoff = rescue_window_start(now, window_days)
    population = set(workspace_keys)
    engaged = (
        _distinct_keys(queue, _ENGAGED_KEYS_SQL, (KIND_ATTEMPT, KIND_WAIT, KIND_REPORT, cutoff))
        & population
    )
    reported = _distinct_keys(queue, _REPORTED_KEYS_SQL, (KIND_REPORT, cutoff)) & engaged
    stuck_engaged = engaged & set(stuck_keys)
    cleared = engaged - stuck_engaged
    stuck_and_reported = stuck_engaged & reported
    success_sample = len(cleared) + len(stuck_and_reported)
    load_sample = len(population)
    return RescueStats(
        success_rate=len(cleared) / success_sample if success_sample > 0 else None,
        success_sample=success_sample,
        success_low_data=success_sample < RESCUE_LOW_DATA_MIN_SAMPLE,
        load_rate=len(engaged) / load_sample if load_sample > 0 else None,
        load_sample=load_sample,
        load_low_data=load_sample < RESCUE_LOW_DATA_MIN_SAMPLE,
    )


__all__ = [
    "ATTEMPT_CEILING_PER_FINGERPRINT",
    "ATTEMPT_CEILING_PER_WORKSPACE",
    "BAR_LOWERING_CEILING",
    "DAY_S",
    "ISSUE_INTENT_MAX_ATTEMPTS",
    "KIND_ATTEMPT",
    "KIND_ISSUE_ATTEMPT",
    "KIND_ISSUE_DELIVERED",
    "KIND_ISSUE_INTENT",
    "KIND_REPORT",
    "KIND_WAIT",
    "OUTCOME_ACTED",
    "OUTCOME_NEEDS_HUMAN",
    "OUTCOME_NOT_YET",
    "REPROBE_CAP",
    "RESCUE_LOW_DATA_MIN_SAMPLE",
    "RESCUE_WINDOW_DAYS",
    "UNBLOCK_WATCH_META_KEY",
    "Entry",
    "LedgerRow",
    "ReportHistory",
    "RescueStats",
    "UnblockRecord",
    "WatchEntry",
    "activity_rows_since",
    "attempts_for_fingerprint",
    "attempts_for_workspace",
    "bar_lowering_count",
    "counters_24h",
    "ensure_schema",
    "issue_urls_for",
    "last_record",
    "load_watch",
    "park_class",
    "park_key",
    "pending_issue_intents",
    "record",
    "records_for",
    "repeat_reason",
    "report_history",
    "rescue_stats",
    "rescue_window_start",
    "stamp",
]
