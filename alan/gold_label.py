from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from alan import store
from alan.handover import Handover, OpenQuestion
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Workspace

logger = logging.getLogger("alan.gold_label")

GOLD_LABEL_FILE = "gold_labels.jsonl"

_ROW_CAP = 5000

_lock = threading.Lock()


def _validated_path(path: Path, *, root: Path) -> Path | None:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved if resolved.is_file() else None


def _truncate_to_cap(path: Path, *, root: Path) -> None:
    resolved = _validated_path(path, root=root)
    if resolved is None:
        return
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _ROW_CAP:
        return
    try:
        store.write_text_atomic(resolved, "\n".join(lines[-_ROW_CAP:]) + "\n")
    except OSError as exc:
        logger.warning("gold_label: truncate failed for %s: %s", path, exc)


def record_gold_label(
    *, ws: Workspace, blocking: Handover, question: OpenQuestion, answer: str
) -> None:
    entry: dict[str, object] = {
        "task_id": ws.task_id,
        "workspace_id": ws.task_id,
        "project_id": ws.meta.project_id,
        "question_id": question.id,
        "question_class": question.question_class,
        "options": list(question.proposed_answer_space),
        "recommended": question.recommended,
        "chosen": answer,
        "agreed_with_recommendation": (
            question.recommended is not None and answer == question.recommended
        ),
        "time_to_answer_seconds": (datetime.now(UTC) - blocking.timestamp_utc).total_seconds(),
    }
    audit_dir = logs_root().resolve()
    path = audit_dir / GOLD_LABEL_FILE
    try:
        with _lock:
            append_jsonl_audit(path, entry, log_label="gold_label")
            _truncate_to_cap(path, root=audit_dir)
    except Exception:  # noqa: BLE001 - a gold-label write failure must never 5xx /answer
        logger.exception("gold_label: record failed for %s", ws.task_id)


def load_gold_labels(path: Path | None = None) -> list[dict[str, object]]:
    target = path if path is not None else logs_root().resolve() / GOLD_LABEL_FILE
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is not None:
            rows.append(row)
    return rows


def agreement_rate_by_class(rows: list[dict[str, object]]) -> dict[str, float]:
    agreed_counts: dict[str, int] = {}
    total_counts: dict[str, int] = {}
    for row in rows:
        cls = row.get("question_class")
        agreed = row.get("agreed_with_recommendation")
        if (
            not isinstance(cls, str)
            or row.get("recommended") is None
            or not isinstance(agreed, bool)
        ):
            continue
        total_counts[cls] = total_counts.get(cls, 0) + 1
        if agreed:
            agreed_counts[cls] = agreed_counts.get(cls, 0) + 1
    return {cls: agreed_counts.get(cls, 0) / total for cls, total in total_counts.items()}


def latency_stats_by_class(rows: list[dict[str, object]]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        cls = row.get("question_class")
        if not isinstance(cls, str):
            continue
        latency = row.get("time_to_answer_seconds")
        if isinstance(latency, bool) or not isinstance(latency, int | float):
            continue
        buckets.setdefault(cls, []).append(float(latency))
    return {
        cls: {
            "mean_seconds": sum(values) / len(values),
            "p50_seconds": median(values),
            "count": len(values),
        }
        for cls, values in buckets.items()
    }


__all__ = [
    "GOLD_LABEL_FILE",
    "agreement_rate_by_class",
    "latency_stats_by_class",
    "load_gold_labels",
    "record_gold_label",
]
