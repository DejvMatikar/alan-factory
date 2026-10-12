from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar

from alan import engine_config as engine_config_mod
from alan import store
from alan import unblock_ledger as unblock_ledger_mod
from alan import workspace as workspace_mod
from alan.acceptance import live_clauses, unproven_clause_ids
from alan.finisher_rate import (
    FINISHER_EPOCH,
    FunnelCounts,
    aggregate,
    build_indices,
    filter_metas,
)
from alan.health_repo_metrics import (
    AUDIT_TAIL_BYTES,
    BLOCK_CORRECTION_AUDIT_FILE,
    LOW_DATA_MIN_SAMPLE,
    classify_build_workspaces,
    engine_keys_by_task,
    is_build_success,
    read_audit_tail,
)
from alan.incident import stuck_workspace_keys
from alan.learning_metrics import first_pass_approval_rate, sample_size
from alan.queue import Queue
from alan.trajectory import TrajectoryRecord, derive_trajectories

SHORT_WINDOW_DAYS = 7
LONG_WINDOW_DAYS = 30
SAME_BAND = 0.05

_BAND_TOLERANCE = 1e-9
_DAY = timedelta(days=1)
_ITEM = TypeVar("_ITEM")

_NODE_TURNS_FILE = "node_turns_audit.jsonl"
_VERIFICATION_FILE = "verification_audit.jsonl"
_MERGE_AUDIT_FILE = "pr_merge_audit.jsonl"
_JOB_FAILURE_FILE = "job_failure_audit.jsonl"
_SPIRAL_AUDIT_FILE = "spiral_audit.jsonl"

Reading = tuple[float | None, int]


class Verdict(StrEnum):
    BETTER = "better"
    WORSE = "worse"
    SAME = "same"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Window:
    value: float | None
    sample: int
    covered_days: int


@dataclass(frozen=True)
class MetricVerdict:
    verdict: Verdict
    higher_is_better: bool


@dataclass(frozen=True)
class MetricWindows:
    d7: Window
    d30: Window
    verdict: MetricVerdict


@dataclass(frozen=True)
class FunnelWindows:
    d7: FunnelCounts
    d30: FunnelCounts


@dataclass(frozen=True)
class ScopeWindows:
    outcome: MetricWindows
    autonomous: MetricWindows
    build_success: MetricWindows
    first_pass_approval: MetricWindows
    unproven_clauses: MetricWindows
    format_error: MetricWindows
    correction_recovery: MetricWindows
    rescue_success: MetricWindows
    rescue_load: MetricWindows
    funnel: FunnelWindows


@dataclass(frozen=True)
class HealthWindows:
    computed_at: datetime
    fleet: ScopeWindows
    repos: Mapping[str, ScopeWindows]
    unattributed: int


class CorrectionRow(Protocol):
    @property  # pragma: no cover
    def ts(self) -> datetime | None: ...

    @property  # pragma: no cover
    def task_id(self) -> str | None: ...

    @property  # pragma: no cover
    def attempts(self) -> int: ...

    @property  # pragma: no cover
    def outcome(self) -> str: ...


@dataclass(frozen=True)
class SourceReach:
    funnel: datetime | None
    first_pass: datetime | None
    corrections: datetime | None


@dataclass(frozen=True)
class WindowSources:
    queue: Queue
    workspaces: Sequence[workspace_mod.Workspace]
    job_failure_text: str
    merge_audit_text: str
    spiral_audit_text: str
    trajectories: Sequence[TrajectoryRecord]
    corrections: Sequence[CorrectionRow]
    reach: SourceReach


@dataclass(frozen=True)
class _Entry:
    slug: str
    workspace: workspace_mod.Workspace
    meta: dict[str, Any]
    finished: workspace_mod.Meta | None


@dataclass(frozen=True)
class _Scope:
    entries: Sequence[_Entry]
    trajectories: Sequence[TrajectoryRecord]
    corrections: Sequence[CorrectionRow]


@dataclass(frozen=True)
class _Context:
    now: datetime
    queue: Queue
    failure: dict[str, list[str]]
    merge: set[str]
    spiral: dict[str, list[str]]
    reach: SourceReach
    terminal_reach: datetime


def _utc(moment: datetime) -> datetime:
    return workspace_mod.as_utc(moment)


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return _utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _since(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _ratio(numerator: int, sample: int) -> Reading:
    return (numerator / sample if sample > 0 else None), sample


def _covered_days(now: datetime, days: int, reach: datetime | None) -> int:
    start = _since(now, days)
    effective = start if reach is None else max(start, reach)
    return max(0, (now - effective) // _DAY)


def _window(now: datetime, days: int, reach: datetime | None, reading: Reading) -> Window:
    value, sample = reading
    return Window(value=value, sample=sample, covered_days=_covered_days(now, days, reach))


def _comparable_delta(d7: Window, d30: Window) -> float | None:
    if d7.value is None or d30.value is None:
        return None
    if min(d7.sample, d30.sample) < LOW_DATA_MIN_SAMPLE:
        return None
    if d30.covered_days <= SHORT_WINDOW_DAYS:
        return None
    return d7.value - d30.value


def _verdict(d7: Window, d30: Window, higher_is_better: bool) -> Verdict:
    delta = _comparable_delta(d7, d30)
    if delta is None:
        return Verdict.INSUFFICIENT
    if abs(delta) <= SAME_BAND + _BAND_TOLERANCE:
        return Verdict.SAME
    return Verdict.BETTER if (delta > 0) == higher_is_better else Verdict.WORSE


def compare_windows(d7: Window, d30: Window, *, higher_is_better: bool) -> MetricVerdict:
    return MetricVerdict(_verdict(d7, d30, higher_is_better), higher_is_better)


def _metric(
    ctx: _Context,
    reach: datetime | None,
    readings: tuple[Reading, Reading],
    higher_is_better: bool,
) -> MetricWindows:
    d7 = _window(ctx.now, SHORT_WINDOW_DAYS, reach, readings[0])
    d30 = _window(ctx.now, LONG_WINDOW_DAYS, reach, readings[1])
    verdict = compare_windows(d7, d30, higher_is_better=higher_is_better)
    return MetricWindows(d7=d7, d30=d30, verdict=verdict)


def _measured(
    ctx: _Context,
    reach: datetime | None,
    measure: Callable[[datetime], Reading],
    higher_is_better: bool,
) -> MetricWindows:
    readings = (
        measure(_since(ctx.now, SHORT_WINDOW_DAYS)),
        measure(_since(ctx.now, LONG_WINDOW_DAYS)),
    )
    return _metric(ctx, reach, readings, higher_is_better)


def _funnel_counts(scope: _Scope, ctx: _Context, days: int) -> FunnelCounts:
    cutoff = max(_since(ctx.now, days), FINISHER_EPOCH)
    metas = filter_metas([e.meta for e in scope.entries], cutoff)
    counts, _leak = aggregate(metas, ctx.failure, ctx.spiral, ctx.merge)
    return counts


def _funnel_metric(
    ctx: _Context,
    counts: tuple[FunnelCounts, FunnelCounts],
    numerator: Callable[[FunnelCounts], int],
    higher_is_better: bool,
) -> MetricWindows:
    readings = (
        _ratio(numerator(counts[0]), counts[0].dispatched),
        _ratio(numerator(counts[1]), counts[1].dispatched),
    )
    return _metric(ctx, ctx.reach.funnel, readings, higher_is_better)


def _finished_since(scope: _Scope, cutoff: datetime) -> list[workspace_mod.Meta]:
    return [
        e.finished
        for e in scope.entries
        if e.finished is not None
        and e.finished.terminal_at is not None
        and _utc(e.finished.terminal_at) >= cutoff
    ]


def _build_success_reading(scope: _Scope, cutoff: datetime) -> Reading:
    finished = _finished_since(scope, cutoff)
    return _ratio(sum(1 for meta in finished if is_build_success(meta)), len(finished))


def _unproven_reading(scope: _Scope, cutoff: datetime) -> Reading:
    live = unproven = 0
    for meta in _finished_since(scope, cutoff):
        live += len(live_clauses(meta.requirements or []))
        unproven += len(unproven_clause_ids(meta.requirements, meta.tasks))
    return _ratio(unproven, live)


def _first_pass_reading(scope: _Scope, cutoff: datetime) -> Reading:
    inside: list[TrajectoryRecord] = []
    for record in scope.trajectories:
        verified_at = _parse_ts(record.verified_at)
        if verified_at is not None and verified_at >= cutoff:
            inside.append(record)
    return first_pass_approval_rate(inside), sample_size(inside)


def _rows_since(rows: Sequence[CorrectionRow], cutoff: datetime) -> list[CorrectionRow]:
    return [r for r in rows if r.ts is not None and _utc(r.ts) >= cutoff]


def _format_error_reading(scope: _Scope, cutoff: datetime) -> Reading:
    first = [r for r in _rows_since(scope.corrections, cutoff) if r.attempts == 1]
    return _ratio(sum(1 for r in first if r.outcome != "clean"), len(first))


def _recovery_reading(scope: _Scope, cutoff: datetime) -> Reading:
    second = [r for r in _rows_since(scope.corrections, cutoff) if r.attempts == 2]
    return _ratio(sum(1 for r in second if r.outcome == "corrected"), len(second))


def _rescue(scope: _Scope, ctx: _Context, days: int) -> unblock_ledger_mod.RescueStats:
    pairs = [(e.workspace.task_id, e.workspace.meta.status) for e in scope.entries]
    return unblock_ledger_mod.rescue_stats(
        ctx.queue,
        [key for key, _status in pairs],
        stuck_workspace_keys(pairs),
        ctx.now.timestamp(),
        days,
    )


def _rescue_metric(
    ctx: _Context,
    stats: tuple[unblock_ledger_mod.RescueStats, unblock_ledger_mod.RescueStats],
    reading: Callable[[unblock_ledger_mod.RescueStats], Reading],
) -> MetricWindows:
    return _metric(ctx, None, (reading(stats[0]), reading(stats[1])), True)


def _success_reading(stats: unblock_ledger_mod.RescueStats) -> Reading:
    return stats.success_rate, stats.success_sample


def _load_reading(stats: unblock_ledger_mod.RescueStats) -> Reading:
    return stats.load_rate, stats.load_sample


def _scope_windows(scope: _Scope, ctx: _Context) -> ScopeWindows:
    counts = (
        _funnel_counts(scope, ctx, SHORT_WINDOW_DAYS),
        _funnel_counts(scope, ctx, LONG_WINDOW_DAYS),
    )
    stats = (
        _rescue(scope, ctx, SHORT_WINDOW_DAYS),
        _rescue(scope, ctx, LONG_WINDOW_DAYS),
    )
    reach = ctx.reach
    return ScopeWindows(
        outcome=_funnel_metric(ctx, counts, lambda c: c.dod_evidenced, True),
        autonomous=_funnel_metric(ctx, counts, lambda c: c.autonomous, True),
        build_success=_measured(
            ctx, ctx.terminal_reach, lambda cut: _build_success_reading(scope, cut), True
        ),
        first_pass_approval=_measured(
            ctx, reach.first_pass, lambda cut: _first_pass_reading(scope, cut), True
        ),
        unproven_clauses=_measured(
            ctx, ctx.terminal_reach, lambda cut: _unproven_reading(scope, cut), False
        ),
        format_error=_measured(
            ctx, reach.corrections, lambda cut: _format_error_reading(scope, cut), False
        ),
        correction_recovery=_measured(
            ctx, reach.corrections, lambda cut: _recovery_reading(scope, cut), True
        ),
        rescue_success=_rescue_metric(ctx, stats, _success_reading),
        rescue_load=_rescue_metric(ctx, stats, _load_reading),
        funnel=FunnelWindows(d7=counts[0], d30=counts[1]),
    )


def _slug(engine_key: str | None) -> str:
    return engine_config_mod.canonical_scope_key(engine_key).slug


def _entry(ws: workspace_mod.Workspace) -> _Entry:
    terminal, _in_flight = classify_build_workspaces([ws])
    finished = terminal[0] if terminal and terminal[0].terminal_at is not None else None
    return _Entry(
        slug=_slug(ws.meta.engine_key),
        workspace=ws,
        meta=ws.meta.model_dump(mode="json"),
        finished=finished,
    )


def _grouped(
    items: Iterable[_ITEM], slug_of: Callable[[_ITEM], str | None]
) -> dict[str, list[_ITEM]]:
    groups: dict[str, list[_ITEM]] = {}
    for item in items:
        slug = slug_of(item)
        if slug is not None:
            groups.setdefault(slug, []).append(item)
    return groups


def _terminal_reach(workspaces: Sequence[workspace_mod.Workspace], now: datetime) -> datetime:
    stamps = [_utc(ws.meta.terminal_at) for ws in workspaces if ws.meta.terminal_at is not None]
    return min(stamps, default=now)


def _context(sources: WindowSources, now: datetime) -> _Context:
    failure, merge, spiral = build_indices(
        sources.job_failure_text, sources.merge_audit_text, sources.spiral_audit_text
    )
    return _Context(
        now=now,
        queue=sources.queue,
        failure=failure,
        merge=merge,
        spiral=spiral,
        reach=sources.reach,
        terminal_reach=_terminal_reach(sources.workspaces, now),
    )


def _unattributed(
    rows: Sequence[CorrectionRow], task_slug: Mapping[str | None, str], cutoff: datetime
) -> int:
    counted = (r for r in _rows_since(rows, cutoff) if r.attempts in (1, 2))
    return sum(1 for r in counted if r.task_id not in task_slug)


def compute_health_windows(sources: WindowSources, *, now: datetime) -> HealthWindows:
    ctx = _context(sources, now)
    entries = [_entry(ws) for ws in sources.workspaces]
    task_slug: dict[str | None, str] = {e.workspace.task_id: e.slug for e in entries}
    engine_keys = engine_keys_by_task(list(sources.workspaces))
    by_entries = _grouped(entries, lambda e: e.slug)
    by_records = _grouped(sources.trajectories, lambda r: _slug(engine_keys.get(r.task_id)))
    by_rows = _grouped(sources.corrections, lambda r: task_slug.get(r.task_id))
    fleet = _Scope(entries, sources.trajectories, sources.corrections)
    repos = {
        slug: _scope_windows(
            _Scope(
                by_entries.get(slug, []), by_records.get(slug, []), by_rows.get(slug, [])
            ),
            ctx,
        )
        for slug in sorted(by_entries.keys() | by_records.keys())
    }
    return HealthWindows(
        computed_at=now,
        fleet=_scope_windows(fleet, ctx),
        repos=repos,
        unattributed=_unattributed(
            sources.corrections, task_slug, _since(now, LONG_WINDOW_DAYS)
        ),
    )


@dataclass(frozen=True)
class _Tail:
    text: str
    reach: datetime | None


def _is_truncated(path: Path) -> bool:
    try:
        return path.stat().st_size > AUDIT_TAIL_BYTES
    except OSError:
        return False


def _earliest_timestamp(text: str) -> datetime | None:
    stamps = (_parse_ts(row.get("timestamp_utc")) for row in store.parse_jsonl_rows(text))
    return min((stamp for stamp in stamps if stamp is not None), default=None)


def _read_tail(path: Path) -> _Tail:
    text = read_audit_tail(path)
    return _Tail(text, _earliest_timestamp(text) if _is_truncated(path) else None)


def _latest(*reaches: datetime | None) -> datetime | None:
    return max((reach for reach in reaches if reach is not None), default=None)


def _corrections_reach(audit_dir: Path, rows: Sequence[CorrectionRow]) -> datetime | None:
    if not _is_truncated(audit_dir / BLOCK_CORRECTION_AUDIT_FILE):
        return None
    return min((_utc(r.ts) for r in rows if r.ts is not None), default=None)


def read_window_sources(
    queue: Queue,
    audit_dir: Path,
    workspaces: Sequence[workspace_mod.Workspace],
    corrections: Sequence[CorrectionRow],
) -> WindowSources:
    job = _read_tail(audit_dir / _JOB_FAILURE_FILE)
    merge = _read_tail(audit_dir / _MERGE_AUDIT_FILE)
    spiral = _read_tail(audit_dir / _SPIRAL_AUDIT_FILE)
    node_turns = _read_tail(audit_dir / _NODE_TURNS_FILE)
    verification = _read_tail(audit_dir / _VERIFICATION_FILE)
    reach = SourceReach(
        funnel=_latest(FINISHER_EPOCH, job.reach, merge.reach, spiral.reach),
        first_pass=_latest(node_turns.reach, verification.reach),
        corrections=_corrections_reach(audit_dir, corrections),
    )
    return WindowSources(
        queue=queue,
        workspaces=workspaces,
        job_failure_text=job.text,
        merge_audit_text=merge.text,
        spiral_audit_text=spiral.text,
        trajectories=derive_trajectories(node_turns.text, verification.text, "", ""),
        corrections=corrections,
        reach=reach,
    )


__all__ = [
    "LONG_WINDOW_DAYS",
    "SAME_BAND",
    "SHORT_WINDOW_DAYS",
    "CorrectionRow",
    "FunnelWindows",
    "HealthWindows",
    "MetricVerdict",
    "MetricWindows",
    "ScopeWindows",
    "SourceReach",
    "Verdict",
    "Window",
    "WindowSources",
    "compare_windows",
    "compute_health_windows",
    "read_window_sources",
]
