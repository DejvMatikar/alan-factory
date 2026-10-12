"""One-shot historical finisher-rate diagnosis.

Reads audit logs and workspace metas, then emits a ranked diagnosis report
to stdout showing where build-pipeline tasks fail to finish.

Usage:
    uv run python scripts/finisher_diagnosis.py [--window-days N] [--min-sample N]
    uv run python scripts/finisher_diagnosis.py --logs-root ~/.alan/logs \\
        --workspaces-root ~/.alan/workspaces
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from alan.finisher_rate import compute_finisher_rate, fr_to_dict


def _logs_root() -> Path:
    return Path.home() / ".alan" / "logs"


def _workspaces_root() -> Path:
    return Path.home() / ".alan" / "workspaces"


def _read_log(root: Path, name: str) -> str:
    path = root / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _load_meta(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _collect_metas(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for meta_path in root.glob("*/meta.json"):
        meta = _load_meta(meta_path)
        if meta is not None:
            result.append(meta)
    return result


def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "n/a"
    return f"{100 * num / denom:.1f}%"


def _top_n(d: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _format_funnel(d: dict[str, Any]) -> str:
    counts = d.get("counts") or {}
    dispatched = int(counts.get("dispatched") or 0)
    not_crashed = int(counts.get("not_crashed") or 0)
    not_needed_human = int(counts.get("not_needed_human") or 0)
    converged = int(counts.get("converged") or 0)
    merged_pr = int(counts.get("merged_pr") or 0)
    dod_evidenced = int(counts.get("dod_evidenced") or 0)

    rows = [
        ("Stage", "Count", "Pass-through"),
        ("---", "---", "---"),
        ("Dispatched (denominator)", str(dispatched), ""),
        ("Did not crash", str(not_crashed), _pct(not_crashed, dispatched)),
        ("Did not need human", str(not_needed_human), _pct(not_needed_human, not_crashed)),
        ("Converged within budget", str(converged), _pct(converged, not_needed_human)),
        ("Opened and merged PR", str(merged_pr), _pct(merged_pr, converged)),
        ("DoD evidenced (finisher)", str(dod_evidenced), _pct(dod_evidenced, merged_pr)),
    ]
    col_widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = []
    for row in rows:
        parts = [row[i].ljust(col_widths[i]) for i in range(3)]
        lines.append("| " + " | ".join(parts) + " |")
    return "\n".join(lines)


def _format_leak_reasons(d: dict[str, Any]) -> str:
    leak = d.get("leak_reasons") or {}
    crash = dict(leak.get("crash_reasons") or {})
    convergence = dict(leak.get("convergence_kinds") or {})
    lines = ["### Top crash reasons", ""]
    if crash:
        for reason, count in _top_n(crash):
            lines.append(f"- `{reason}` × {count}")
    else:
        lines.append("*(none recorded)*")
    lines += ["", "### Top convergence failure kinds", ""]
    if convergence:
        for kind, count in _top_n(convergence):
            lines.append(f"- `{kind}` × {count}")
    else:
        lines += ["*(none recorded)*"]
    return "\n".join(lines)


def _format_header(d: dict[str, Any], window_days: int) -> str:
    rate = d.get("rate")
    sample = int(d.get("sample_size") or 0)
    low_data = bool(d.get("low_data"))
    floor_alert = bool(d.get("floor_alert"))
    rate_str = f"{rate:.1%}" if isinstance(rate, float) else "n/a (low data)"
    alert = " ⚠ BELOW FLOOR" if floor_alert else ""
    lines = [
        f"# Finisher-rate diagnosis — {window_days}-day window",
        "",
        f"**Finisher rate:** {rate_str}{alert}",
        f"**Sample size:** {sample} terminal build-pipeline workspaces",
    ]
    if low_data:
        lines.append("**Note:** Low-data flag set — fewer than min_sample workspaces in window.")
    return "\n".join(lines)


def _format_low_data_warning(d: dict[str, Any]) -> str:
    if not d.get("low_data"):
        return ""
    return (
        "\n> **Low-data warning:** Fewer than the minimum sample size of workspaces "
        "were found in the window. The funnel counts are shown but no rate is computed.\n"
    )


def _build_report(d: dict[str, Any], window_days: int) -> str:
    parts = [
        _format_header(d, window_days),
        _format_low_data_warning(d),
        "",
        "## Funnel",
        "",
        _format_funnel(d),
        "",
        "## Leak analysis",
        "",
        _format_leak_reasons(d),
    ]
    return "\n".join(p for p in parts if p is not None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--min-sample", type=int, default=10)
    parser.add_argument("--logs-root", type=Path, default=None)
    parser.add_argument("--workspaces-root", type=Path, default=None)
    args = parser.parse_args()

    logs = args.logs_root or _logs_root()
    ws_root = args.workspaces_root or _workspaces_root()

    failure_text = _read_log(logs, "job_failure_audit.jsonl")
    merge_text = _read_log(logs, "pr_merge_audit.jsonl")
    spiral_text = _read_log(logs, "spiral_audit.jsonl")
    metas = _collect_metas(ws_root)

    fr = compute_finisher_rate(
        failure_text, merge_text, spiral_text, metas,
        window_days=args.window_days,
        min_sample=args.min_sample,
    )
    d = fr_to_dict(fr)
    print(_build_report(d, args.window_days), file=sys.stdout)


if __name__ == "__main__":
    main()
