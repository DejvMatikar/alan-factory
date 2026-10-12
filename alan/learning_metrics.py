from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from alan import store
from alan.trajectory import TrajectoryRecord

_RESTART_RECOVERY_OUTCOMES: tuple[str, ...] = (
    "attached", "attach_lost", "requeued_for_resume", "interrupted_failed",
    "resume_succeeded", "resume_failed", "rerun_never_started", "rerun_cap_exhausted",
)


def first_pass_approval_rate(records: list[TrajectoryRecord]) -> float | None:
    total = 0
    approved = 0
    for rec in records:
        if rec.team != "implementation":
            continue
        if rec.verdict is None:
            continue
        total += 1
        if rec.verdict == "APPROVE":
            approved += 1
    return approved / total if total > 0 else None


def _fix_counts_by_merged_pr(
    records: list[TrajectoryRecord],
) -> dict[tuple[str, int], int]:
    counts: dict[tuple[str, int], int] = {}
    for rec in records:
        if rec.reward is not True or rec.pr_number is None:
            continue
        key = (rec.task_id, rec.pr_number)
        counts.setdefault(key, 0)
        if rec.team == "implementation_fix":
            counts[key] += 1
    return counts


def mean_fix_cycles_per_merged_pr(records: list[TrajectoryRecord]) -> float | None:
    counts = _fix_counts_by_merged_pr(records)
    return sum(counts.values()) / len(counts) if counts else None


def pct_merged_prs_needing_fix(records: list[TrajectoryRecord]) -> float | None:
    counts = _fix_counts_by_merged_pr(records)
    if not counts:
        return None
    needing_fix = sum(1 for c in counts.values() if c >= 1)
    return needing_fix / len(counts)


def mean_fix_cycles_when_fixed(records: list[TrajectoryRecord]) -> float | None:
    fixed = [c for c in _fix_counts_by_merged_pr(records).values() if c >= 1]
    return sum(fixed) / len(fixed) if fixed else None


def mean_fix_cycles_to_merge(records: list[TrajectoryRecord]) -> float | None:
    return mean_fix_cycles_per_merged_pr(records)


def sample_size(records: list[TrajectoryRecord]) -> int:
    return sum(
        1 for rec in records
        if rec.team == "implementation" and rec.verdict is not None
    )


def _normalize_block_class(raw: object) -> str:
    return raw if isinstance(raw, str) and raw else "unknown"


MERGE_GATE_WINDOW_HOURS = 720.0


def _accumulate_merge_gate_row(
    per_class: dict[str, dict[str, int]],
    row: dict[str, object],
    *,
    now: datetime,
    window_hours: float,
) -> None:
    block_class = _normalize_block_class(row.get("block_class"))
    counts = per_class.setdefault(
        block_class, {"recovered": 0, "total": 0, "lifetime_total": 0}
    )
    counts["lifetime_total"] += 1
    if not _row_within_window(row, now=now, window_hours=window_hours):
        return
    counts["total"] += 1
    if row.get("recovered") is True:
        counts["recovered"] += 1


def merge_gate_recovery_rates(
    text: str, *, now: datetime, window_hours: float = MERGE_GATE_WINDOW_HOURS
) -> dict[str, dict[str, int]] | None:
    if not text:
        return None
    per_class: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is None:
            continue
        _accumulate_merge_gate_row(per_class, row, now=now, window_hours=window_hours)
    return per_class if per_class else None


def _empty_restart_counts() -> dict[str, int]:
    return {outcome: 0 for outcome in _RESTART_RECOVERY_OUTCOMES}


def _row_within_window(row: dict[str, object], *, now: datetime, window_hours: float) -> bool:
    raw = row.get("timestamp_utc")
    if not isinstance(raw, str):
        return False
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        return False
    delta = (now - ts).total_seconds()
    return 0 <= delta <= window_hours * 3600


def _matching_restart_outcome(row: dict[str, object]) -> str | None:
    outcome = row.get("outcome")
    if isinstance(outcome, str) and outcome in _RESTART_RECOVERY_OUTCOMES:
        return outcome
    return None


def restart_recovery_counts(
    text: str, *, now: datetime, window_hours: float = 168
) -> dict[str, dict[str, int]] | None:
    if not text:
        return None
    per_team: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is None or not _row_within_window(row, now=now, window_hours=window_hours):
            continue
        outcome = _matching_restart_outcome(row)
        if outcome is None:
            continue
        team = _normalize_block_class(row.get("team"))
        per_team.setdefault(team, _empty_restart_counts())[outcome] += 1
    return per_team if per_team else None


@dataclass(frozen=True)
class LearningMetrics:
    first_pass_approval_rate: float | None
    mean_fix_cycles_to_merge: float | None
    sample_size: int
    mean_fix_cycles_per_merged_pr: float | None = None
    pct_merged_prs_needing_fix: float | None = None
    mean_fix_cycles_when_fixed: float | None = None
    planner_behavioral_score: float | None = None
    planner_behavioral_baseline: float | None = None
    planner_behavioral_regression: bool = False


def learning_metrics_to_dict(metrics: LearningMetrics) -> dict[str, object]:
    return {
        "first_pass_approval_rate": metrics.first_pass_approval_rate,
        "mean_fix_cycles_to_merge": metrics.mean_fix_cycles_to_merge,
        "sample_size": metrics.sample_size,
        "mean_fix_cycles_per_merged_pr": metrics.mean_fix_cycles_per_merged_pr,
        "pct_merged_prs_needing_fix": metrics.pct_merged_prs_needing_fix,
        "mean_fix_cycles_when_fixed": metrics.mean_fix_cycles_when_fixed,
        "planner_behavioral_score": metrics.planner_behavioral_score,
        "planner_behavioral_baseline": metrics.planner_behavioral_baseline,
        "planner_behavioral_regression": metrics.planner_behavioral_regression,
    }


def _coerce_float_or_none(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _coerce_int(val: object) -> int:
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return 0


def _coerce_bool(val: object) -> bool:
    return bool(val) if isinstance(val, bool) else False


def learning_metrics_from_dict(data: object) -> LearningMetrics:
    if not isinstance(data, dict):
        return LearningMetrics(
            first_pass_approval_rate=None,
            mean_fix_cycles_to_merge=None,
            sample_size=0,
            mean_fix_cycles_per_merged_pr=None,
            pct_merged_prs_needing_fix=None,
            mean_fix_cycles_when_fixed=None,
        )
    d = cast("dict[str, object]", data)
    return LearningMetrics(
        first_pass_approval_rate=_coerce_float_or_none(d.get("first_pass_approval_rate")),
        mean_fix_cycles_to_merge=_coerce_float_or_none(d.get("mean_fix_cycles_to_merge")),
        sample_size=_coerce_int(d.get("sample_size")),
        mean_fix_cycles_per_merged_pr=_coerce_float_or_none(
            d.get("mean_fix_cycles_per_merged_pr")
        ),
        pct_merged_prs_needing_fix=_coerce_float_or_none(d.get("pct_merged_prs_needing_fix")),
        mean_fix_cycles_when_fixed=_coerce_float_or_none(d.get("mean_fix_cycles_when_fixed")),
        planner_behavioral_score=_coerce_float_or_none(d.get("planner_behavioral_score")),
        planner_behavioral_baseline=_coerce_float_or_none(d.get("planner_behavioral_baseline")),
        planner_behavioral_regression=_coerce_bool(d.get("planner_behavioral_regression")),
    )


__all__ = [
    "LearningMetrics",
    "MERGE_GATE_WINDOW_HOURS",
    "first_pass_approval_rate",
    "learning_metrics_from_dict",
    "learning_metrics_to_dict",
    "mean_fix_cycles_per_merged_pr",
    "mean_fix_cycles_to_merge",
    "mean_fix_cycles_when_fixed",
    "merge_gate_recovery_rates",
    "pct_merged_prs_needing_fix",
    "restart_recovery_counts",
    "sample_size",
]
