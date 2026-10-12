"""Per-class merge-recovery outcome report.

Usage:
    uv run python scripts/merge_recovery_report.py [--logs-root DIR]

Reads impl_audit.jsonl (produced by alan.merge_recovery._audit_recovery) and
prints a per-class breakdown of recovery attempts: how many were attempted,
how many succeeded, and the recovery rate for each MergeBlockClass value.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_AUDIT_FILE = "impl_audit.jsonl"


def _load_recovery_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / _AUDIT_FILE
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("action") != "merge_recovery_attempt":
            continue
        rows.append(entry)  # type: ignore[arg-type]
    return rows


def _report(rows: list[dict[str, object]]) -> None:
    attempted: dict[str, int] = defaultdict(int)
    recovered: dict[str, int] = defaultdict(int)
    for row in rows:
        cls = str(row.get("block_class") or "unknown")
        attempted[cls] += 1
        if row.get("recovered") is True:
            recovered[cls] += 1

    if not attempted:
        print("No merge_recovery_attempt rows found in audit log.")
        return

    all_classes = sorted(attempted)
    total_attempted = sum(attempted.values())
    total_recovered = sum(recovered.values())

    print(f"{'block_class':<30} {'attempts':>8} {'recovered':>10} {'rate':>8}")
    print("-" * 60)
    for cls in all_classes:
        att = attempted[cls]
        rec = recovered[cls]
        rate = rec / att if att else 0.0
        print(f"{cls:<30} {att:>8} {rec:>10} {rate:>7.1%}")
    print("-" * 60)
    overall = total_recovered / total_attempted if total_attempted else 0.0
    print(f"{'TOTAL':<30} {total_attempted:>8} {total_recovered:>10} {overall:>7.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs-root",
        type=Path,
        default=_LOGS_ROOT_DEFAULT,
        help="Directory containing impl_audit.jsonl (default: ~/.alan/logs)",
    )
    args = parser.parse_args()
    rows = _load_recovery_rows(args.logs_root)
    _report(rows)


if __name__ == "__main__":
    sys.exit(main())
