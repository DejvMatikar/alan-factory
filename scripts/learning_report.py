"""Offline learning-loop quality report.

Usage:
    uv run python scripts/learning_report.py [--store PATH] [--logs-root DIR] [--git-log FILE]

Reads a trajectory store (default: trajectory_store.jsonl) produced by
derive_trajectories.py and prints two north-star metrics plus Alan's
existing structural merge-survival metric (labeled separately so the two
populations are never conflated).

Metrics printed:
  METRIC first_pass_approval_rate  — fraction of implementation invocations
      whose first (and only) verification verdict was APPROVE. Numerator:
      records with team=implementation and verdict=APPROVE. Denominator:
      records with team=implementation and verdict is not None.

  METRIC mean_fix_cycles_to_merge — mean number of fix rounds per
      merged-and-survived PR. Each PR is counted exactly once (grouped by
      task_id + pr_number); its contribution is the count of implementation_fix
      records in that group. A first-pass PR contributes 0.

  STRUCTURAL merge_survival_rate — fraction of auto-merged PRs (action=="merge_ok"
      in pr_merge_audit.jsonl) that survived the revert window without a
      corresponding revert commit in git history. POPULATION DISCLAIMER: this
      metric covers ALL Alan-initiated merges, NOT just first-pass approvals. It
      is presented separately and should not be conflated with the live
      first-pass approval rate above.

  STRUCTURAL merge_gate_overall_recovery_rate — fraction of blocked merge
      attempts that were mechanically recovered (from merge_gate_audit.jsonl).
      Per-class breakdown follows on subsequent lines.

Pass --behavioral-eval to additionally run the LLM-judged planner behavioral
eval sweep (alan.behavioral_eval) against the committed golden cases under
tests/eval/golden/planner/ — opt-in only, since it is the one LLM call this
script makes; see docs/architecture/eval_scaffolding.md Metric 5.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from alan.learning_metrics import first_pass_approval_rate, mean_fix_cycles_to_merge
from alan.trajectory import TrajectoryRecord

_STORE_DEFAULT = "trajectory_store.jsonl"
_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_NODE_TURNS_FILE = "node_turns_audit.jsonl"
_VERIFICATION_FILE = "verification_audit.jsonl"
_MERGE_FILE = "pr_merge_audit.jsonl"
_MERGE_GATE_FILE = "merge_gate_audit.jsonl"
_METRIC_PREFIX = "METRIC"
_STRUCTURAL_PREFIX = "STRUCTURAL"


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _read_git_log(git_log_path: Path | None) -> str:
    if git_log_path is not None:
        return _read_file(git_log_path)
    try:
        result = subprocess.run(
            ["git", "log", "--format=%x1e%aI%x00%s%x00%b", "--all"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def _load_store(store_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    try:
        text = store_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return records
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            records.append(raw)
    return records


def _store_record_to_typed(raw: dict[str, object]) -> TrajectoryRecord | None:
    task_id = raw.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    team = raw.get("team")
    if not isinstance(team, str) or not team:
        return None
    inv_id_raw = raw.get("inv_id")
    inv_id: str | None = inv_id_raw if isinstance(inv_id_raw, str) else None
    pr_raw = raw.get("pr_number")
    pr_number: int | None = (
        pr_raw if isinstance(pr_raw, int) and not isinstance(pr_raw, bool) else None
    )
    verdict_raw = raw.get("verdict")
    verdict: str | None = verdict_raw if isinstance(verdict_raw, str) else None
    reward_raw = raw.get("reward")
    reward: bool | None = bool(reward_raw) if isinstance(reward_raw, bool) else None
    fc_raw = raw.get("fix_cycles")
    fix_cycles: int = (
        fc_raw if isinstance(fc_raw, int) and not isinstance(fc_raw, bool) else 0
    )
    rv_raw = raw.get("revisits")
    revisits: int = (
        rv_raw if isinstance(rv_raw, int) and not isinstance(rv_raw, bool) else 0
    )
    turns_raw = raw.get("turns")
    turns: int | None = (
        turns_raw if isinstance(turns_raw, int) and not isinstance(turns_raw, bool) else None
    )
    return TrajectoryRecord(
        task_id=task_id, inv_id=inv_id, pr_number=pr_number, team=team,
        verdict=verdict, reward=reward, fix_cycles=fix_cycles,
        revisits=revisits, turns=turns,
    )


def _completed_fraction(
    logs_root: Path,
    git_log_path: Path | None = None,
) -> float | None:
    try:
        from alan.eval_metrics import (
            merge_survival_rate,
            parse_git_log_output,
            parse_merge_audit_rows,
        )
    except ImportError:
        return None
    merge_text = _read_file(logs_root / "pr_merge_audit.jsonl")
    merge_rows = parse_merge_audit_rows(merge_text)
    if not merge_rows:
        return None
    git_log_text = _read_git_log(git_log_path)
    git_records = parse_git_log_output(git_log_text)
    return merge_survival_rate(merge_rows, git_records)


def _merge_gate_recovery_rates(
    logs_root: Path,
) -> dict[str, dict[str, int]] | None:
    text = _read_file(logs_root / _MERGE_GATE_FILE)
    if not text:
        return None
    per_class: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        block_class = str(row.get("block_class", "unknown"))
        counts = per_class.setdefault(block_class, {"recovered": 0, "total": 0})
        counts["total"] += 1
        if row.get("recovered") is True:
            counts["recovered"] += 1
    return per_class if per_class else None


def _print_merge_gate_rates(rates: dict[str, dict[str, int]]) -> None:
    total_all = sum(s["total"] for s in rates.values())
    recovered_all = sum(s["recovered"] for s in rates.values())
    overall = recovered_all / total_all if total_all > 0 else 0.0
    print(
        f"{_STRUCTURAL_PREFIX} merge_gate_overall_recovery_rate {overall:.4f}"
        f" ({recovered_all}/{total_all})"
    )
    for cls, counts in sorted(rates.items()):
        rate = counts["recovered"] / counts["total"] if counts["total"] > 0 else 0.0
        print(
            f"{_STRUCTURAL_PREFIX} merge_gate_recovery:{cls}"
            f" {rate:.4f} ({counts['recovered']}/{counts['total']})"
        )


def _run_behavioral_eval() -> int:
    from alan.behavioral_eval import run_sweep

    return run_sweep()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=None,
        help="Trajectory store JSONL (default: derive inline from audit logs)",
    )
    parser.add_argument(
        "--logs-root",
        default=str(_LOGS_ROOT_DEFAULT),
        help="Directory containing audit JSONL files",
    )
    parser.add_argument(
        "--git-log",
        default=None,
        help="Path to a pre-captured git log file",
    )
    parser.add_argument(
        "--behavioral-eval",
        action="store_true",
        help="Also run the LLM-judged planner behavioral eval sweep",
    )
    args = parser.parse_args(argv)

    logs_root = Path(args.logs_root)
    git_log_path = Path(args.git_log) if args.git_log else None

    if args.store is not None:
        raw_records = _load_store(Path(args.store))
        records = [r for r in (_store_record_to_typed(raw) for raw in raw_records) if r is not None]
    else:
        from alan.trajectory import derive_trajectories

        node_turns_text = _read_file(logs_root / _NODE_TURNS_FILE)
        verification_text = _read_file(logs_root / _VERIFICATION_FILE)
        merge_audit_text = _read_file(logs_root / _MERGE_FILE)
        git_log_text = _read_git_log(git_log_path)
        records = derive_trajectories(
            node_turns_text, verification_text, merge_audit_text, git_log_text
        )

    fpar = first_pass_approval_rate(records)
    mfc = mean_fix_cycles_to_merge(records)

    if fpar is None:
        print(
            f"{_METRIC_PREFIX} first_pass_approval_rate"
            " n/a (no implementation records with verdict)"
        )
    else:
        print(f"{_METRIC_PREFIX} first_pass_approval_rate {fpar:.4f}")

    if mfc is None:
        print(f"{_METRIC_PREFIX} mean_fix_cycles_to_merge n/a (no merged-and-survived PRs)")
    else:
        print(f"{_METRIC_PREFIX} mean_fix_cycles_to_merge {mfc:.4f}")

    cf = _completed_fraction(logs_root, git_log_path)
    if cf is None:
        print(
            f"{_STRUCTURAL_PREFIX} merge_survival_rate n/a "
            "(no merge_ok rows found — POPULATION: all Alan-merged PRs, "
            "NOT first-pass approval; merge survival metric only)"
        )
    else:
        print(
            f"{_STRUCTURAL_PREFIX} merge_survival_rate {cf:.4f} "
            "(POPULATION: all Alan-merged PRs that survived revert window — "
            "merge survival metric, NOT first-pass approval rate)"
        )

    mgr_rates = _merge_gate_recovery_rates(logs_root)
    if mgr_rates is None:
        print(
            f"{_STRUCTURAL_PREFIX} merge_gate_overall_recovery_rate n/a"
            " (no merge_gate_audit.jsonl entries)"
        )
    else:
        _print_merge_gate_rates(mgr_rates)

    if args.behavioral_eval:
        return _run_behavioral_eval()
    return 0


if __name__ == "__main__":
    sys.exit(main())
