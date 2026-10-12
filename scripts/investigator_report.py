"""Offline investigator-ritual quality report.

Usage:
    uv run python scripts/investigator_report.py [--queue-db PATH] [--logs-root DIR]

Reads the investigator's RCA drafts (``rca_drafts_v1``) and the human-confirmed
incident dispositions (``incident_dispositions_v1``) from the shared queue sqlite
db and prints two north-star metrics. LLM-free — this is pure aggregation over
already-persisted state, no model call is ever made.

Metrics printed:
  METRIC disposition_agreement_rate — of the fingerprints where the investigator
      proposed a disposition AND a human later confirmed one via
      POST /api/health/incidents/{fingerprint}/disposition, the fraction where the two
      agree. Degrades to n/a when no such fingerprint exists yet.

  METRIC recurrence_after_disposition — of the fingerprints that were ever
      dispositioned (closed, or closed-then-reopened), the fraction that
      recurred (alan.incident_store.reconcile_reopen bumped recurrence_counter
      above zero). Degrades to n/a when nothing has ever been dispositioned.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alan.incident_store import DispositionRecord, load_dispositions
from alan.queue import Queue
from alan.rca_store import RcaDraft, load_rca_drafts

_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_QUEUE_DB_ENV = "ALAN_QUEUE_DB_PATH"
_METRIC_PREFIX = "METRIC"


def _disposition_agreement_rate(
    drafts: dict[str, RcaDraft], dispositions: dict[str, DispositionRecord]
) -> tuple[float | None, int, int]:
    total = 0
    matched = 0
    for fingerprint, draft in drafts.items():
        if draft.source != "investigator" or draft.proposed_disposition is None:
            continue
        record = dispositions.get(fingerprint)
        if record is None or record.disposition is None:
            continue
        total += 1
        if draft.proposed_disposition == record.disposition.value:
            matched += 1
    rate = matched / total if total > 0 else None
    return rate, matched, total


def _was_ever_dispositioned(record: DispositionRecord) -> bool:
    return record.disposition is not None or record.recurrence_counter > 0


def _recurrence_after_disposition_rate(
    dispositions: dict[str, DispositionRecord],
) -> tuple[float | None, int, int]:
    ever_dispositioned = 0
    recurred = 0
    for record in dispositions.values():
        if not _was_ever_dispositioned(record):
            continue
        ever_dispositioned += 1
        if record.recurrence_counter > 0:
            recurred += 1
    rate = recurred / ever_dispositioned if ever_dispositioned > 0 else None
    return rate, recurred, ever_dispositioned


def _resolve_queue_db(cli_value: str | None, logs_root: str) -> Path:
    if cli_value:
        return Path(cli_value)
    override = os.environ.get(_QUEUE_DB_ENV, "").strip()
    if override:
        return Path(override)
    return Path(logs_root).parent / "queue.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs-root",
        default=str(_LOGS_ROOT_DEFAULT),
        help="Directory containing audit JSONL files (used to derive the default "
        "queue db path when --queue-db is not given: <logs-root>/../queue.db)",
    )
    parser.add_argument(
        "--queue-db",
        default=None,
        help="Path to the queue sqlite db (default: ALAN_QUEUE_DB_PATH env, or "
        "<logs-root>/../queue.db)",
    )
    args = parser.parse_args(argv)

    queue = Queue(db_path=_resolve_queue_db(args.queue_db, args.logs_root))
    try:
        drafts = load_rca_drafts(queue)
        dispositions = load_dispositions(queue)
    finally:
        queue.close()

    agreement_rate, matched, total = _disposition_agreement_rate(drafts, dispositions)
    if agreement_rate is None:
        print(
            f"{_METRIC_PREFIX} disposition_agreement_rate n/a (no fingerprint has both an "
            "investigator-proposed disposition and a human-confirmed disposition)"
        )
    else:
        print(
            f"{_METRIC_PREFIX} disposition_agreement_rate {agreement_rate:.4f} "
            f"({matched}/{total})"
        )

    recurrence_rate, recurred, ever_dispositioned = _recurrence_after_disposition_rate(
        dispositions
    )
    if recurrence_rate is None:
        print(
            f"{_METRIC_PREFIX} recurrence_after_disposition n/a "
            "(no incident has ever been dispositioned)"
        )
    else:
        print(
            f"{_METRIC_PREFIX} recurrence_after_disposition {recurrence_rate:.4f} "
            f"({recurred}/{ever_dispositioned})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
