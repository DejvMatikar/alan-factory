from __future__ import annotations

from typing import Any, cast

from alan.health import (
    HealthState,
    load_health_state,
    load_learning_metrics,
    load_merge_gate_recovery,
    load_restart_recovery,
)
from alan.queue import Queue

_NOT_ENOUGH_DATA = "not enough data yet"


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_of(computed_at: object) -> str:
    return computed_at if isinstance(computed_at, str) and computed_at else "unknown"


def _pct(value: object) -> str:
    num = _as_float(value)
    return f"{num * 100:.1f}%" if num is not None else _NOT_ENOUGH_DATA


def _cycles(value: object) -> str:
    num = _as_float(value)
    return f"{num:.2f}" if num is not None else _NOT_ENOUGH_DATA


def _sample_size(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _learning_metrics_lines(data: dict[str, Any] | None) -> list[str]:
    if not data:
        return [
            f"Learning metrics: {_NOT_ENOUGH_DATA} "
            "(triage sweep hasn't computed fix-cycle metrics)."
        ]
    return [
        f"Learning metrics (as of {_as_of(data.get('computed_at'))}, "
        f"sample={_sample_size(data.get('sample_size'))}):",
        f"- First-pass approval rate: {_pct(data.get('first_pass_approval_rate'))}",
        "- Mean fix cycles per merged PR: "
        f"{_cycles(data.get('mean_fix_cycles_per_merged_pr'))}",
        "- Merged PRs needing a fix: "
        f"{_pct(data.get('pct_merged_prs_needing_fix'))} "
        f"(mean {_cycles(data.get('mean_fix_cycles_when_fixed'))} cycles when a fix was needed)",
    ]


def _ci_deploy_status(state: HealthState, kind: str) -> str:
    for finding in state.findings:
        if finding.kind == kind:
            return finding.description
    return "green"


def _health_lines(state: HealthState | None) -> list[str]:
    if state is None:
        return [f"Fleet health / CI / deploy: {_NOT_ENOUGH_DATA} (triage sweep hasn't run yet)."]
    return [
        f"Fleet health / CI / deploy (as of {_as_of(state.computed_at)}):",
        f"- Verdict: {state.verdict} — {state.blocked_count} blocked surface(s), "
        f"{state.in_flight_count} in flight",
        f"- Main CI: {_ci_deploy_status(state, 'main_ci')}",
        f"- Deploy: {_ci_deploy_status(state, 'deploy')}",
    ]


def _gate_counts(value: object) -> tuple[int, int]:
    if not isinstance(value, dict):
        return 0, 0
    counts = cast(dict[str, Any], value)
    total = counts.get("total")
    recovered = counts.get("recovered")
    total_n = total if isinstance(total, int) and not isinstance(total, bool) else 0
    recovered_n = (
        recovered if isinstance(recovered, int) and not isinstance(recovered, bool) else 0
    )
    return recovered_n, total_n


def _rate(recovered: int, total: int) -> str:
    return f"{recovered / total * 100:.1f}%" if total > 0 else _NOT_ENOUGH_DATA


_MIN_RATE_SAMPLES = 5


def _window_label(window_hours: object) -> str:
    hours = _as_float(window_hours)
    return f"last {int(hours / 24)}d" if hours is not None and hours > 0 else "all history"


def _lifetime_total(value: object, total: int) -> int:
    if not isinstance(value, dict):
        return total
    raw = cast(dict[str, Any], value).get("lifetime_total")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else total


def _class_line(window_label: str, block_class: str, raw_counts: object) -> str:
    recovered, total = _gate_counts(raw_counts)
    if total == 0:
        lifetime_n = _lifetime_total(raw_counts, total)
        return f"  - {block_class}: no measurement in {window_label} (lifetime {lifetime_n})"
    if total < _MIN_RATE_SAMPLES:
        return f"  - {block_class}: {recovered}/{total} (below minimum sample size)"
    return f"  - {block_class}: {_rate(recovered, total)} ({recovered}/{total})"


def _merge_gate_lines(data: dict[str, Any] | None) -> list[str]:
    if not isinstance(data, dict):
        return [f"Merge-gate recovery: {_NOT_ENOUGH_DATA} (no gate blocks recorded yet)."]
    raw_rates = data.get("rates")
    if not isinstance(raw_rates, dict) or not raw_rates:
        return [f"Merge-gate recovery: {_NOT_ENOUGH_DATA} (no gate blocks recorded yet)."]
    rates = cast(dict[str, Any], raw_rates)
    window_label = _window_label(data.get("window_hours"))
    class_lines: list[str] = []
    total_recovered = 0
    total_all = 0
    for block_class in sorted(rates):
        recovered, total = _gate_counts(rates[block_class])
        total_recovered += recovered
        total_all += total
        class_lines.append(_class_line(window_label, block_class, rates[block_class]))
    lines = [
        f"Merge-gate recovery ({window_label}, as of {_as_of(data.get('computed_at'))}):",
        f"- Overall: {_rate(total_recovered, total_all)} recovered "
        f"({total_recovered}/{total_all})",
    ]
    lines.extend(class_lines)
    return lines


def _restart_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _restart_recovery_lines(data: dict[str, Any] | None) -> list[str]:
    empty = [f"Restart recovery: {_NOT_ENOUGH_DATA} (no restarts recorded yet)."]
    if not isinstance(data, dict):
        return empty
    raw_counts = data.get("counts")
    if not isinstance(raw_counts, dict) or not raw_counts:
        return empty
    per_team = cast(dict[str, Any], raw_counts)
    lines = [
        "Restart recovery (last 7d, as of "
        f"{_as_of(data.get('computed_at'))}; excludes rederive-path jobs):",
    ]
    total_requeued = 0
    for team in sorted(per_team):
        counts = per_team[team]
        if not isinstance(counts, dict):
            continue
        counts = cast(dict[str, Any], counts)
        total_requeued += _restart_count(counts.get("requeued_for_resume"))
        lines.append(
            f"  - {team}: attached={_restart_count(counts.get('attached'))} "
            f"attach_lost={_restart_count(counts.get('attach_lost'))} "
            f"interrupted_failed={_restart_count(counts.get('interrupted_failed'))} "
            f"resume_succeeded={_restart_count(counts.get('resume_succeeded'))} "
            f"resume_failed={_restart_count(counts.get('resume_failed'))}"
        )
    lines.append(f"- resume queued (decision, not an outcome): {total_requeued}")
    return lines


def assemble_shop_stats_block(queue: Queue) -> str:
    lines: list[str] = []
    lines.extend(_learning_metrics_lines(load_learning_metrics(queue)))
    lines.extend(_health_lines(load_health_state(queue)))
    lines.extend(_merge_gate_lines(load_merge_gate_recovery(queue)))
    lines.extend(_restart_recovery_lines(load_restart_recovery(queue)))
    return "\n".join(lines)


__all__ = ["assemble_shop_stats_block"]
