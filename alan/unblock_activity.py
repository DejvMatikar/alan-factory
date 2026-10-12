from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import alan.engine_config as engine_config_mod
import alan.unblock_ledger as ledger_mod
from alan.incident import stuck_workspace_keys
from alan.queue import Queue
from alan.rca_store import RcaDraft, load_rca_drafts
from alan.workspace import Workspace

EVENT_LIMIT = 50
ISSUE_URL_LOOKUP_CHUNK = 500

_EVER_RECORDED_SQL = (
    "SELECT EXISTS(SELECT 1 FROM unblock_ledger WHERE kind IN (?, ?, ?)) AS present"
)
_RECORDED_ORIGINS_SQL = (
    "SELECT DISTINCT workspace_key, engine_key FROM unblock_ledger WHERE kind IN (?, ?, ?)"
)
_ATTEMPT_INDEX_SQL = (
    "SELECT id, workspace_key,"
    " ROW_NUMBER() OVER (PARTITION BY workspace_key, fingerprint ORDER BY ts, id)"
    " AS fingerprint_index,"
    " ROW_NUMBER() OVER (PARTITION BY workspace_key ORDER BY ts, id) AS workspace_index"
    " FROM unblock_ledger WHERE kind = ?"
)


class Clearance(StrEnum):
    CLEARED = "cleared"
    STILL_STUCK = "still_stuck"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AttemptIndex:
    fingerprint_index: int
    fingerprint_ceiling: int
    workspace_index: int
    workspace_ceiling: int


@dataclass(frozen=True)
class AttemptHistory:
    by_row: Mapping[int, AttemptIndex]
    totals: Mapping[str, int]


@dataclass(frozen=True)
class DiagnosisSummary:
    signature: str
    proposed_disposition: str | None
    verified: bool


@dataclass(frozen=True)
class ActivityEvent:
    id: int
    at: float
    workspace_key: str
    repo: str | None
    shape: str
    kind: str
    action: str | None
    outcome: str
    reason: str | None
    authority_holder: str | None
    clearance: Clearance
    attempt: AttemptIndex | None
    attempts_total: int | None
    issue_url: str | None = None
    diagnosis: DiagnosisSummary | None = None


@dataclass(frozen=True)
class OutcomeCount:
    outcome: str
    count: int


@dataclass(frozen=True)
class ShapeAggregate:
    shape: str
    total: int
    workspaces: int
    cleared_workspaces: int
    still_stuck_workspaces: int
    gone_workspaces: int
    retried_workspaces: int
    attempt_outcomes: tuple[OutcomeCount, ...]
    report_outcomes: tuple[OutcomeCount, ...]


@dataclass(frozen=True)
class ActivitySummary:
    events_total: int
    workspaces_touched: int
    waited_workspaces: int
    diagnosed_workspaces: int
    diagnosable_workspaces: int
    still_stuck_workspaces: int
    gave_up_workspaces: int
    aggregates: tuple[ShapeAggregate, ...]


@dataclass(frozen=True)
class WorkspaceActivity:
    workspace_key: str
    last_at: float
    shape: str
    events_total: int
    events_listed: int
    attempts_total: int
    attempts_ceiling: int
    clearance: Clearance
    gave_up: bool
    issue_url: str | None
    diagnosis: DiagnosisSummary | None


@dataclass(frozen=True)
class FleetActivity:
    ever_recorded: bool
    unattributed_events: int
    summary: ActivitySummary


@dataclass(frozen=True)
class RepoActivity:
    slug: str
    fleet_ever_recorded: bool
    events: tuple[ActivityEvent, ...]
    workspaces: tuple[WorkspaceActivity, ...]
    summary: ActivitySummary


@dataclass(frozen=True)
class _Lookups:
    ws_by_key: Mapping[str, Workspace]
    stuck_keys: frozenset[str]
    history: AttemptHistory
    diagnoses: Mapping[str, DiagnosisSummary]


@dataclass(frozen=True)
class _Window:
    events: tuple[ActivityEvent, ...]
    waited: Mapping[str, str | None]
    attempt_totals: Mapping[str, int]


def ever_recorded(queue: Queue) -> bool:
    ledger_mod.ensure_schema(queue)
    rows = queue.run(
        _EVER_RECORDED_SQL,
        (ledger_mod.KIND_ATTEMPT, ledger_mod.KIND_WAIT, ledger_mod.KIND_REPORT),
    )
    return bool(rows[0]["present"])


def _attempt_history(queue: Queue) -> AttemptHistory:
    rows = queue.run(_ATTEMPT_INDEX_SQL, (ledger_mod.KIND_ATTEMPT,))
    by_row = {
        int(row["id"]): AttemptIndex(
            fingerprint_index=int(row["fingerprint_index"]),
            fingerprint_ceiling=ledger_mod.ATTEMPT_CEILING_PER_FINGERPRINT,
            workspace_index=int(row["workspace_index"]),
            workspace_ceiling=ledger_mod.ATTEMPT_CEILING_PER_WORKSPACE,
        )
        for row in rows
    }
    totals: dict[str, int] = {}
    for row in rows:
        key = str(row["workspace_key"])
        totals[key] = max(totals.get(key, 0), int(row["workspace_index"]))
    return AttemptHistory(by_row=by_row, totals=totals)


def _slug_of(
    engine_key: str | None, workspace_key: str, ws_by_key: Mapping[str, Workspace]
) -> str | None:
    if engine_key is not None:
        return engine_config_mod.canonical_scope_key(engine_key).slug
    ws = ws_by_key.get(workspace_key)
    if ws is None:
        return None
    return engine_config_mod.canonical_scope_key(ws.meta.engine_key).slug


def _repo_slug_for(
    row: ledger_mod.LedgerRow, ws_by_key: Mapping[str, Workspace]
) -> str | None:
    return _slug_of(row.engine_key, row.workspace_key, ws_by_key)


def _clearance(
    workspace_key: str, ws_by_key: Mapping[str, Workspace], stuck_keys: Collection[str]
) -> Clearance:
    if workspace_key not in ws_by_key:
        return Clearance.UNKNOWN
    if workspace_key in stuck_keys:
        return Clearance.STILL_STUCK
    return Clearance.CLEARED


def _diagnosis_summary(draft: RcaDraft) -> DiagnosisSummary:
    return DiagnosisSummary(
        signature=draft.recurrence_signature,
        proposed_disposition=draft.proposed_disposition,
        verified=draft.verified,
    )


def _diagnoses_by_workspace(queue: Queue) -> dict[str, DiagnosisSummary]:
    drafts = sorted(load_rca_drafts(queue).values(), key=lambda d: d.created_at or 0.0)
    return {
        d.workspace_key: _diagnosis_summary(d) for d in drafts if d.workspace_key is not None
    }


def _outcome_label(row: ledger_mod.LedgerRow) -> str:
    return (row.outcome or row.kind).split(":", 1)[0].strip()


def _detail_text(row: ledger_mod.LedgerRow, key: str) -> str | None:
    value = row.detail.get(key)
    return value if isinstance(value, str) and value else None


def _event_from_row(row: ledger_mod.LedgerRow, lookups: _Lookups) -> ActivityEvent:
    is_attempt = row.kind == ledger_mod.KIND_ATTEMPT
    return ActivityEvent(
        id=row.id,
        at=row.ts,
        workspace_key=row.workspace_key,
        repo=_repo_slug_for(row, lookups.ws_by_key),
        shape=row.shape,
        kind=row.kind,
        action=row.action,
        outcome=_outcome_label(row),
        reason=_detail_text(row, "reason"),
        authority_holder=_detail_text(row, "authority_holder"),
        clearance=_clearance(row.workspace_key, lookups.ws_by_key, lookups.stuck_keys),
        attempt=lookups.history.by_row.get(row.id) if is_attempt else None,
        attempts_total=None if is_attempt else lookups.history.totals.get(row.workspace_key, 0),
        diagnosis=lookups.diagnoses.get(row.workspace_key),
    )


def _lookups_for(queue: Queue, all_ws: Sequence[Workspace]) -> _Lookups:
    return _Lookups(
        ws_by_key={ws.task_id: ws for ws in all_ws},
        stuck_keys=stuck_workspace_keys((ws.task_id, ws.meta.status) for ws in all_ws),
        history=_attempt_history(queue),
        diagnoses=_diagnoses_by_workspace(queue),
    )


def _load_window(queue: Queue, all_ws: Sequence[Workspace], now: float) -> _Window:
    rows = ledger_mod.activity_rows_since(queue, ledger_mod.rescue_window_start(now))
    lookups = _lookups_for(queue, all_ws)
    events = tuple(_event_from_row(r, lookups) for r in rows if r.kind != ledger_mod.KIND_WAIT)
    engaged = {e.workspace_key for e in events}
    waited = {
        r.workspace_key: _repo_slug_for(r, lookups.ws_by_key)
        for r in rows
        if r.kind == ledger_mod.KIND_WAIT and r.workspace_key not in engaged
    }
    return _Window(events=events, waited=waited, attempt_totals=lookups.history.totals)


def _outcome_counts(outcomes: Iterable[str]) -> tuple[OutcomeCount, ...]:
    ranked = sorted(Counter(outcomes).items(), key=lambda item: (-item[1], item[0]))
    return tuple(OutcomeCount(outcome=outcome, count=count) for outcome, count in ranked)


def _is_retry(event: ActivityEvent) -> bool:
    return event.attempt is not None and event.attempt.fingerprint_index > 1


def _outcomes_of_kind(events: Iterable[ActivityEvent], kind: str) -> tuple[OutcomeCount, ...]:
    return _outcome_counts(e.outcome for e in events if e.kind == kind)


def _shape_aggregate(shape: str, events: Sequence[ActivityEvent]) -> ShapeAggregate:
    per_workspace = {e.workspace_key: e.clearance for e in events}
    clearances = Counter(per_workspace.values())
    return ShapeAggregate(
        shape=shape,
        total=len(events),
        workspaces=len(per_workspace),
        cleared_workspaces=clearances[Clearance.CLEARED],
        still_stuck_workspaces=clearances[Clearance.STILL_STUCK],
        gone_workspaces=clearances[Clearance.UNKNOWN],
        retried_workspaces=len({e.workspace_key for e in events if _is_retry(e)}),
        attempt_outcomes=_outcomes_of_kind(events, ledger_mod.KIND_ATTEMPT),
        report_outcomes=_outcomes_of_kind(events, ledger_mod.KIND_REPORT),
    )


def _grouped(
    events: Iterable[ActivityEvent], key: Callable[[ActivityEvent], str]
) -> dict[str, list[ActivityEvent]]:
    groups: dict[str, list[ActivityEvent]] = {}
    for event in events:
        groups.setdefault(key(event), []).append(event)
    return groups


def _aggregate(events: Sequence[ActivityEvent]) -> tuple[ShapeAggregate, ...]:
    by_shape = _grouped(events, lambda e: e.shape)
    aggregates = [_shape_aggregate(shape, group) for shape, group in by_shape.items()]
    return tuple(sorted(aggregates, key=lambda agg: (-agg.total, agg.shape)))


def _still_stuck_keys(events: Iterable[ActivityEvent]) -> set[str]:
    return {e.workspace_key for e in events if e.clearance is Clearance.STILL_STUCK}


def _gave_up_keys(events: Iterable[ActivityEvent]) -> set[str]:
    return {e.workspace_key for e in events if e.kind == ledger_mod.KIND_REPORT}


def _summarize(events: Sequence[ActivityEvent], waited_workspaces: int) -> ActivitySummary:
    workspaces = {e.workspace_key for e in events}
    diagnosed = {e.workspace_key for e in events if e.diagnosis is not None}
    return ActivitySummary(
        events_total=len(events),
        workspaces_touched=len(workspaces),
        waited_workspaces=waited_workspaces,
        diagnosed_workspaces=len(diagnosed),
        diagnosable_workspaces=len(workspaces),
        still_stuck_workspaces=len(_still_stuck_keys(events)),
        gave_up_workspaces=len(_gave_up_keys(events)),
        aggregates=_aggregate(events),
    )


def _issue_urls(queue: Queue, events: Sequence[ActivityEvent]) -> Mapping[str, str | None]:
    keys = list(dict.fromkeys(e.workspace_key for e in events))
    urls: dict[str, str | None] = {}
    for start in range(0, len(keys), ISSUE_URL_LOOKUP_CHUNK):
        urls.update(
            ledger_mod.issue_urls_for(queue, keys[start : start + ISSUE_URL_LOOKUP_CHUNK])
        )
    return urls


def _with_issue_urls(
    events: Iterable[ActivityEvent], urls: Mapping[str, str | None]
) -> tuple[ActivityEvent, ...]:
    return tuple(replace(e, issue_url=urls.get(e.workspace_key)) for e in events)


def _workspace_activity(
    events: Sequence[ActivityEvent],
    listed: int,
    attempts_total: int,
    urls: Mapping[str, str | None],
) -> WorkspaceActivity:
    latest = events[0]
    return WorkspaceActivity(
        workspace_key=latest.workspace_key,
        last_at=latest.at,
        shape=latest.shape,
        events_total=len(events),
        events_listed=listed,
        attempts_total=attempts_total,
        attempts_ceiling=ledger_mod.ATTEMPT_CEILING_PER_WORKSPACE,
        clearance=latest.clearance,
        gave_up=any(e.kind == ledger_mod.KIND_REPORT for e in events),
        issue_url=urls.get(latest.workspace_key),
        diagnosis=latest.diagnosis,
    )


def _workspaces(
    events: Sequence[ActivityEvent],
    listed: Sequence[ActivityEvent],
    attempt_totals: Mapping[str, int],
    urls: Mapping[str, str | None],
) -> tuple[WorkspaceActivity, ...]:
    listed_counts = Counter(e.workspace_key for e in listed)
    return tuple(
        _workspace_activity(group, listed_counts[key], attempt_totals.get(key, 0), urls)
        for key, group in _grouped(events, lambda e: e.workspace_key).items()
    )


def _is_visible(repo: str | None, visible_slugs: Collection[str]) -> bool:
    return repo is None or repo in visible_slugs


def _ever_recorded_visible(
    queue: Queue, all_ws: Sequence[Workspace], visible_slugs: Collection[str]
) -> bool:
    ledger_mod.ensure_schema(queue)
    ws_by_key = {ws.task_id: ws for ws in all_ws}
    rows = queue.run(
        _RECORDED_ORIGINS_SQL,
        (ledger_mod.KIND_ATTEMPT, ledger_mod.KIND_WAIT, ledger_mod.KIND_REPORT),
    )
    slugs = (
        _slug_of(row["engine_key"], str(row["workspace_key"]), ws_by_key) for row in rows
    )
    return any(_is_visible(slug, visible_slugs) for slug in slugs)


def collect_fleet(
    queue: Queue,
    all_ws: Sequence[Workspace],
    *,
    now: float,
    visible_slugs: Collection[str],
) -> FleetActivity:
    window = _load_window(queue, all_ws, now)
    events = tuple(e for e in window.events if _is_visible(e.repo, visible_slugs))
    waited = sum(1 for repo in window.waited.values() if _is_visible(repo, visible_slugs))
    return FleetActivity(
        ever_recorded=_ever_recorded_visible(queue, all_ws, visible_slugs),
        unattributed_events=sum(1 for e in events if e.repo is None),
        summary=_summarize(events, waited),
    )


def _is_repo(repo: str | None, wanted_casefolded: str) -> bool:
    return repo is not None and repo.casefold() == wanted_casefolded


def collect_repo(
    queue: Queue,
    all_ws: Sequence[Workspace],
    *,
    now: float,
    slug: str,
    limit: int = EVENT_LIMIT,
) -> RepoActivity:
    window = _load_window(queue, all_ws, now)
    wanted = slug.casefold()
    events = tuple(e for e in window.events if _is_repo(e.repo, wanted))
    waited = sum(1 for repo in window.waited.values() if _is_repo(repo, wanted))
    listed = events[:limit]
    urls = _issue_urls(queue, events)
    return RepoActivity(
        slug=slug,
        fleet_ever_recorded=ever_recorded(queue),
        events=_with_issue_urls(listed, urls),
        workspaces=_workspaces(events, listed, window.attempt_totals, urls),
        summary=_summarize(events, waited),
    )


__all__ = [
    "EVENT_LIMIT",
    "ISSUE_URL_LOOKUP_CHUNK",
    "ActivityEvent",
    "ActivitySummary",
    "AttemptHistory",
    "AttemptIndex",
    "Clearance",
    "DiagnosisSummary",
    "FleetActivity",
    "OutcomeCount",
    "RepoActivity",
    "ShapeAggregate",
    "WorkspaceActivity",
    "collect_fleet",
    "collect_repo",
    "ever_recorded",
]
