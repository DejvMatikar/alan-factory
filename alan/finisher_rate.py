"""Finisher-rate funnel metric — survivorship-correct measure of build-pipeline completion.

Pure module (no teams imports). Mirrors alan/trajectory.py: inputs are text /
pre-parsed data; file I/O lives in scripts/finisher_diagnosis.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeAlias, cast

from alan import store

_DictAny: TypeAlias = dict[str, Any]

_BUILD_TEAMS: frozenset[str] = frozenset({
    "implementation", "implementation_plan", "implementation_fix",
    "verification", "pr_merge", "decomposer",
})
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed", "failed", "awaiting_input", "escalated", "archived",
})
_NEEDS_HUMAN_STATUSES: frozenset[str] = frozenset({"awaiting_input", "escalated"})
_BOOKKEEPING_AUDIT_KINDS: frozenset[str] = frozenset({"chain_decay", "chain_reset"})

# Build-scoped terminal set for the archive-neutral build_success_rate metric
# (alan/routes/infra.py). Distinct from _TERMINAL_STATUSES above: "awaiting_input"
# is excluded (still live, human can unblock it) and "partial" is included (a
# recoverable-terminal outcome per docs/architecture/partial_workspace_status.md).
_BUILD_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed", "failed", "escalated", "partial", "archived",
})

FINISHER_FLOOR: float = 0.5
FINISHER_EPOCH: datetime = datetime(2026, 6, 17, tzinfo=UTC)
_WINDOW_DAYS_DEFAULT: int = 30
_MIN_SAMPLE_DEFAULT: int = 10


@dataclass(frozen=True)
class FunnelCounts:
    dispatched: int
    not_crashed: int
    # Counts tasks that cleared the crash check but not necessarily human-free overall;
    # a not_converged/escalated task contributes here under single-stage attribution.
    not_needed_human: int
    converged: int
    merged_pr: int
    dod_evidenced: int
    autonomous: int = 0


@dataclass(frozen=True)
class LeakReasons:
    crash_reasons: dict[str, int]
    convergence_kinds: dict[str, int]


@dataclass(frozen=True)
class FinisherRate:
    rate: float | None
    counts: FunnelCounts
    leak_reasons: LeakReasons
    low_data: bool
    window_days: int
    sample_size: int
    floor_alert: bool
    autonomous_rate: float | None = None


def parse_revisit_key(key: str) -> tuple[str, str, str, str]:
    parts = key.split("|")
    if len(parts) == 4:
        return (parts[0], parts[1], parts[2], parts[3])
    if len(parts) == 2:
        return ("", "", parts[0], parts[1])
    return ("", "", key, "")


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    return store.parse_jsonl_rows(text)


def _build_failure_index(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for row in rows:
        ws_id = str(row.get("workspace_id") or row.get("task_id") or "")
        if not ws_id:
            continue
        error = str(row.get("error") or "unknown")
        index.setdefault(ws_id, []).append(error)
    return index


def _build_merge_set(rows: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for row in rows:
        if row.get("action") != "merge_ok":
            continue
        task_id = str(row.get("task_id") or "")
        if task_id:
            result.add(task_id)
    return result


def _build_spiral_index(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for row in rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        kind = str(row.get("kind") or "unclassified")
        if kind in _BOOKKEEPING_AUDIT_KINDS:
            continue
        index.setdefault(task_id, []).append(kind)
    return index


def _has_build_revisit(meta: dict[str, Any]) -> bool:
    revisits: Any = meta.get("revisit_counts") or {}
    if not isinstance(revisits, dict):
        return False
    return any(
        parse_revisit_key(str(k))[2] in _BUILD_TEAMS
        for k in cast(_DictAny, revisits)
    )


def _is_build_pipeline(meta: dict[str, Any]) -> bool:
    tasks: Any = meta.get("tasks") or []
    if any(
        isinstance(t, dict)
        and str(cast(_DictAny, t).get("team") or "") in _BUILD_TEAMS
        for t in tasks
    ):
        return True
    return _has_build_revisit(meta)


def _clause_missing_evidence(clause: Any) -> bool:
    return isinstance(clause, dict) and cast(_DictAny, clause).get("evidence") is None


def _task_has_gap(task: Any) -> bool:
    if not isinstance(task, dict):
        return False
    clauses: Any = cast(_DictAny, task).get("dod_clauses") or []
    return any(_clause_missing_evidence(c) for c in clauses)


def _has_dod_evidence(meta: dict[str, Any]) -> bool:
    tasks: Any = meta.get("tasks") or []
    return not any(_task_has_gap(t) for t in tasks)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _parse_created_at(meta: dict[str, Any]) -> datetime | None:
    ts_raw = meta.get("created_at")
    if not isinstance(ts_raw, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(ts_raw))
    except ValueError:
        return None


def _in_window(meta: dict[str, Any], cutoff: datetime) -> bool:
    created = _parse_created_at(meta)
    return created is not None and created >= cutoff


def _is_intervened(meta: dict[str, Any]) -> bool:
    interventions: list[Any] | None = meta.get("interventions")
    return isinstance(interventions, list) and bool(interventions)


def _is_autonomous(stage: str, meta: dict[str, Any]) -> bool:
    return (
        stage == "success"
        and not _is_intervened(meta)
        and str(meta.get("status") or "") not in _NEEDS_HUMAN_STATUSES
    )


def _classify_stage(
    ws_id: str,
    meta: dict[str, Any],
    failure_index: dict[str, list[str]],
    spiral_index: dict[str, list[str]],
    merge_set: set[str],
) -> str:
    if ws_id in merge_set:
        return "success" if _has_dod_evidence(meta) else "not_evidenced"
    if ws_id in failure_index:
        return "crashed"
    if ws_id in spiral_index:
        return "not_converged"
    if str(meta.get("status") or "") in _NEEDS_HUMAN_STATUSES:
        return "needed_human"
    return "not_merged"


def _track_crash(
    ws_id: str, failure_index: dict[str, list[str]], reasons: dict[str, int]
) -> None:
    for reason in failure_index.get(ws_id, [])[:1]:
        reasons[reason] = reasons.get(reason, 0) + 1


def _track_convergence(
    ws_id: str, spiral_index: dict[str, list[str]], kinds: dict[str, int]
) -> None:
    for kind in spiral_index.get(ws_id, [])[:1]:
        kinds[kind] = kinds.get(kind, 0) + 1


def _tally_stage(
    stage: str,
    ws_id: str,
    failure_index: dict[str, list[str]],
    spiral_index: dict[str, list[str]],
    crash_reasons: dict[str, int],
    convergence_kinds: dict[str, int],
) -> tuple[int, int, int, int, int]:
    if stage == "crashed":
        _track_crash(ws_id, failure_index, crash_reasons)
        return 0, 0, 0, 0, 0
    if stage == "not_converged":
        _track_convergence(ws_id, spiral_index, convergence_kinds)
        return 1, 1, 0, 0, 0
    if stage == "needed_human":
        return 1, 0, 0, 0, 0
    if stage == "not_merged":
        return 1, 1, 1, 0, 0
    if stage == "not_evidenced":
        return 1, 1, 1, 1, 0
    return 1, 1, 1, 1, 1


def aggregate(
    metas: list[dict[str, Any]],
    failure_index: dict[str, list[str]],
    spiral_index: dict[str, list[str]],
    merge_set: set[str],
) -> tuple[FunnelCounts, LeakReasons]:
    dispatched = not_crashed = not_needed_human = converged = merged_pr = dod_evidenced = 0
    autonomous = 0
    crash_reasons: dict[str, int] = {}
    convergence_kinds: dict[str, int] = {}
    for meta in metas:
        dispatched += 1
        ws_id = str(meta.get("task_id") or "")
        stage = _classify_stage(ws_id, meta, failure_index, spiral_index, merge_set)
        nc, nnh, cv, mp, de = _tally_stage(
            stage, ws_id, failure_index, spiral_index, crash_reasons, convergence_kinds
        )
        not_crashed += nc
        not_needed_human += nnh
        converged += cv
        merged_pr += mp
        dod_evidenced += de
        autonomous += 1 if _is_autonomous(stage, meta) else 0
    counts = FunnelCounts(
        dispatched=dispatched,
        not_crashed=not_crashed,
        not_needed_human=not_needed_human,
        converged=converged,
        merged_pr=merged_pr,
        dod_evidenced=dod_evidenced,
        autonomous=autonomous,
    )
    reasons = LeakReasons(crash_reasons=crash_reasons, convergence_kinds=convergence_kinds)
    return counts, reasons


def filter_metas(
    meta_list: list[dict[str, Any]], cutoff: datetime
) -> list[dict[str, Any]]:
    return [
        m for m in meta_list
        if _is_build_pipeline(m)
        and str(m.get("status") or "") in _TERMINAL_STATUSES
        and _in_window(m, cutoff)
    ]


def window_cutoff(window_days: int, epoch: datetime | None) -> datetime:
    rolling_cutoff = datetime.now(UTC) - timedelta(days=window_days)
    return rolling_cutoff if epoch is None else max(rolling_cutoff, _as_utc(epoch))


def build_indices(
    job_failure_text: str, merge_audit_text: str, spiral_audit_text: str
) -> tuple[dict[str, list[str]], set[str], dict[str, list[str]]]:
    failure_index = _build_failure_index(_parse_jsonl(job_failure_text))
    merge_set = _build_merge_set(_parse_jsonl(merge_audit_text))
    spiral_index = _build_spiral_index(_parse_jsonl(spiral_audit_text))
    return failure_index, merge_set, spiral_index


def _rate_or_none(numerator: int, sample: int, low_data: bool) -> float | None:
    return numerator / sample if (sample > 0 and not low_data) else None


def compute_finisher_rate(
    job_failure_text: str,
    merge_audit_text: str,
    spiral_audit_text: str,
    workspace_meta_list: list[dict[str, Any]],
    *,
    window_days: int = _WINDOW_DAYS_DEFAULT,
    min_sample: int = _MIN_SAMPLE_DEFAULT,
    epoch: datetime | None = None,
) -> FinisherRate:
    cutoff = window_cutoff(window_days, epoch)
    failure_index, merge_set, spiral_index = build_indices(
        job_failure_text, merge_audit_text, spiral_audit_text
    )
    metas = filter_metas(workspace_meta_list, cutoff)
    counts, leak_reasons = aggregate(metas, failure_index, spiral_index, merge_set)

    sample = counts.dispatched
    low_data = sample < min_sample
    rate = _rate_or_none(counts.dod_evidenced, sample, low_data)
    floor_alert = rate is not None and rate < FINISHER_FLOOR

    return FinisherRate(
        rate=rate,
        counts=counts,
        leak_reasons=leak_reasons,
        low_data=low_data,
        window_days=window_days,
        sample_size=sample,
        floor_alert=floor_alert,
        autonomous_rate=_rate_or_none(counts.autonomous, sample, low_data),
    )


def _counts_to_dict(counts: FunnelCounts) -> dict[str, int]:
    return {
        "dispatched": counts.dispatched,
        "not_crashed": counts.not_crashed,
        "not_needed_human": counts.not_needed_human,
        "converged": counts.converged,
        "merged_pr": counts.merged_pr,
        "dod_evidenced": counts.dod_evidenced,
        "autonomous": counts.autonomous,
    }


def fr_to_dict(fr: FinisherRate) -> dict[str, Any]:
    return {
        "rate": fr.rate,
        "autonomous_rate": fr.autonomous_rate,
        "counts": _counts_to_dict(fr.counts),
        "leak_reasons": {
            "crash_reasons": fr.leak_reasons.crash_reasons,
            "convergence_kinds": fr.leak_reasons.convergence_kinds,
        },
        "low_data": fr.low_data,
        "window_days": fr.window_days,
        "sample_size": fr.sample_size,
        "floor_alert": fr.floor_alert,
    }


def _int_field(d: dict[str, Any], key: str) -> int:
    val: Any = d.get(key)
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    return 0


def _counts_from_dict(d: dict[str, Any]) -> FunnelCounts:
    return FunnelCounts(
        dispatched=_int_field(d, "dispatched"),
        not_crashed=_int_field(d, "not_crashed"),
        not_needed_human=_int_field(d, "not_needed_human"),
        converged=_int_field(d, "converged"),
        merged_pr=_int_field(d, "merged_pr"),
        dod_evidenced=_int_field(d, "dod_evidenced"),
        autonomous=_int_field(d, "autonomous"),
    )


def _leak_from_dict(d: dict[str, Any]) -> LeakReasons:
    crash_raw: Any = d.get("crash_reasons")
    conv_raw: Any = d.get("convergence_kinds")
    crash_reasons: dict[str, int] = (
        {str(k): int(v) for k, v in cast(_DictAny, crash_raw).items()}
        if isinstance(crash_raw, dict)
        else {}
    )
    convergence_kinds: dict[str, int] = (
        {str(k): int(v) for k, v in cast(_DictAny, conv_raw).items()}
        if isinstance(conv_raw, dict)
        else {}
    )
    return LeakReasons(crash_reasons=crash_reasons, convergence_kinds=convergence_kinds)


def _rate_field(d: dict[str, Any], key: str) -> float | None:
    raw: Any = d.get(key)
    return float(raw) if isinstance(raw, (int, float)) else None


def fr_from_dict(d: dict[str, Any]) -> FinisherRate:
    counts_raw: Any = d.get("counts") or {}
    leak_raw: Any = d.get("leak_reasons") or {}
    counts = _counts_from_dict(cast(_DictAny, counts_raw))
    leak_reasons = _leak_from_dict(cast(_DictAny, leak_raw))
    return FinisherRate(
        rate=_rate_field(d, "rate"),
        counts=counts,
        leak_reasons=leak_reasons,
        low_data=bool(d.get("low_data")),
        window_days=_int_field(d, "window_days") or _WINDOW_DAYS_DEFAULT,
        sample_size=_int_field(d, "sample_size"),
        floor_alert=bool(d.get("floor_alert")),
        autonomous_rate=_rate_field(d, "autonomous_rate"),
    )


__all__ = [
    "FINISHER_FLOOR",
    "FinisherRate",
    "FunnelCounts",
    "LeakReasons",
    "aggregate",
    "build_indices",
    "compute_finisher_rate",
    "filter_metas",
    "fr_from_dict",
    "fr_to_dict",
    "parse_revisit_key",
    "window_cutoff",
]
