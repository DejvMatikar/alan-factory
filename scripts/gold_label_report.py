"""Offline gold-label quality report.

Usage:
    uv run python scripts/gold_label_report.py [--logs-root DIR]

Reads gold_labels.jsonl (written live by alan.gold_label.record_gold_label at
the /answer endpoint, see docs/architecture/eval_scaffolding.md) and prints
per-question-class agreement rate (chosen == recommended) and answer-latency
stats. All math lives in alan.gold_label; this script only loads rows and
prints them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alan.gold_label import (
    GOLD_LABEL_FILE,
    agreement_rate_by_class,
    latency_stats_by_class,
    load_gold_labels,
)

_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_METRIC_PREFIX = "METRIC"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs-root",
        default=str(_LOGS_ROOT_DEFAULT),
        help="Directory containing gold_labels.jsonl",
    )
    args = parser.parse_args(argv)

    logs_root = Path(args.logs_root)
    rows = load_gold_labels(logs_root / GOLD_LABEL_FILE)

    if not rows:
        print(f"{_METRIC_PREFIX} gold_label_agreement_rate n/a (no gold-label rows found)")
        return 0

    agreement = agreement_rate_by_class(rows)
    for cls in sorted(agreement):
        print(f"{_METRIC_PREFIX} gold_label_agreement_rate:{cls} {agreement[cls]:.4f}")

    latency = latency_stats_by_class(rows)
    for cls in sorted(latency):
        stats = latency[cls]
        print(
            f"{_METRIC_PREFIX} gold_label_answer_latency_seconds:{cls} "
            f"mean={stats['mean_seconds']:.2f} p50={stats['p50_seconds']:.2f} n={stats['count']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
