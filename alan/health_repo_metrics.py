from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from alan import engine_config as engine_config_mod
from alan import workspace as workspace_mod
from alan.acceptance import all_clauses_satisfied, unproven_clause_ids
from alan.finisher_rate import (
    _BUILD_TERMINAL_STATUSES,  # pyright: ignore[reportPrivateUsage]
    _is_build_pipeline,  # pyright: ignore[reportPrivateUsage]
)

AUDIT_TAIL_BYTES = 256 * 1024
LOW_DATA_MIN_SAMPLE = 10

_IN_FLIGHT_BUILD_STATUSES: frozenset[str] = frozenset({"active", "paused", "awaiting_input"})
_WINDOW_DAYS = 30


def read_audit_tail(path: Path, tail_bytes: int = AUDIT_TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        if size <= tail_bytes:
            return path.read_text(encoding="utf-8")
        with path.open("rb") as fh:
            fh.seek(size - tail_bytes)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def task_completed_without_gap(task: workspace_mod.Task) -> bool:
    if task.status != workspace_mod.TaskStatus.COMPLETED:
        return False
    if not task.dod_clauses:
        return True
    return all(clause.evidence is not None for clause in task.dod_clauses)


def is_build_success(meta: workspace_mod.Meta) -> bool:
    if not meta.tasks:
        return False
    if not all(task_completed_without_gap(t) for t in meta.tasks):
        return False
    return all_clauses_satisfied(meta)


def classify_build_workspaces(
    all_ws: list[workspace_mod.Workspace],
) -> tuple[list[workspace_mod.Meta], int]:
    terminal_metas: list[workspace_mod.Meta] = []
    in_flight = 0
    for ws in all_ws:
        meta_dict = ws.meta.model_dump(mode="json")
        if not _is_build_pipeline(meta_dict):
            continue
        status = str(ws.meta.status)
        if status in _BUILD_TERMINAL_STATUSES:
            terminal_metas.append(ws.meta)
        if status in _IN_FLIGHT_BUILD_STATUSES:
            in_flight += 1
    return terminal_metas, in_flight


def unproven_clause_count(all_ws: list[workspace_mod.Workspace]) -> int:
    terminal_metas, _in_flight = classify_build_workspaces(all_ws)
    return sum(
        len(unproven_clause_ids(meta.requirements, meta.tasks)) for meta in terminal_metas
    )


def in_flight_by_repo(all_ws: list[workspace_mod.Workspace]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for ws in all_ws:
        _terminal, in_flight = classify_build_workspaces([ws])
        counts[engine_config_mod.canonical_scope_key(ws.meta.engine_key).slug] += in_flight
    return dict(counts)


def engine_keys_by_task(all_ws: list[workspace_mod.Workspace]) -> dict[str, str | None]:
    return {ws.task_id: ws.meta.engine_key for ws in all_ws}


BLOCK_CORRECTION_AUDIT_FILE = "block_correction_audit.jsonl"


@dataclass(frozen=True)
class BlockCorrectionRow:
    site: str
    block: str
    role: str
    outcome: str
    attempts: int
    failure_kind: str | None = None
    task_id: str | None = None
    ts: datetime | None = None


def _bc_str_fields(d: dict[str, Any]) -> tuple[str, str, str, str] | None:
    site, block, role, outcome = d.get("site"), d.get("block"), d.get("role"), d.get("outcome")
    if not all(isinstance(v, str) and v for v in (site, block, role, outcome)):
        return None
    return cast(str, site), cast(str, block), cast(str, role), cast(str, outcome)


def _bc_attempts(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return None
    return value


def _bc_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return workspace_mod.as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _bc_row_from_dict(d: dict[str, Any]) -> BlockCorrectionRow | None:
    fields = _bc_str_fields(d)
    attempts = _bc_attempts(d.get("attempts"))
    if fields is None or attempts is None:
        return None
    site, block, role, outcome = fields
    failure_kind = d.get("failure_kind")
    task_id = d.get("task_id")
    return BlockCorrectionRow(
        site=site,
        block=block,
        role=role,
        outcome=outcome,
        attempts=attempts,
        failure_kind=failure_kind if isinstance(failure_kind, str) else None,
        task_id=task_id if isinstance(task_id, str) else None,
        ts=_bc_ts(d.get("timestamp_utc")),
    )


def _bc_row_from_line(line: str) -> BlockCorrectionRow | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _bc_row_from_dict(cast(dict[str, Any], parsed))


def read_block_correction_rows(audit_dir: Path) -> list[BlockCorrectionRow]:
    text = read_audit_tail(audit_dir / BLOCK_CORRECTION_AUDIT_FILE)
    candidates = (_bc_row_from_line(line) for line in text.splitlines())
    return [row for row in candidates if row is not None]


@dataclass(frozen=True)
class FormatCorrectionRates:
    format_error_rate: float | None
    format_error_sample: int
    format_error_low_data: bool
    recovery_rate: float | None
    recovery_sample: int
    recovery_low_data: bool


def compute_format_error_rate(rows: list[BlockCorrectionRow]) -> tuple[float | None, int, bool]:
    attempt1 = [r for r in rows if r.attempts == 1]
    sample = len(attempt1)
    if sample == 0:
        return None, 0, True
    errors = sum(1 for r in attempt1 if r.outcome != "clean")
    return errors / sample, sample, False


def compute_recovery_rate(rows: list[BlockCorrectionRow]) -> tuple[float | None, int, bool]:
    attempt2 = [r for r in rows if r.attempts == 2]
    sample = len(attempt2)
    if sample == 0:
        return None, 0, True
    recovered = sum(1 for r in attempt2 if r.outcome == "corrected")
    return recovered / sample, sample, False


def compute_format_correction_rates(rows: list[BlockCorrectionRow]) -> FormatCorrectionRates:
    fmt_rate, fmt_sample, fmt_low = compute_format_error_rate(rows)
    rec_rate, rec_sample, rec_low = compute_recovery_rate(rows)
    return FormatCorrectionRates(
        format_error_rate=fmt_rate,
        format_error_sample=fmt_sample,
        format_error_low_data=fmt_low,
        recovery_rate=rec_rate,
        recovery_sample=rec_sample,
        recovery_low_data=rec_low,
    )


def format_correction_rates_from_audit_dir(audit_dir: Path) -> FormatCorrectionRates:
    return compute_format_correction_rates(read_block_correction_rows(audit_dir))


__all__ = [
    "AUDIT_TAIL_BYTES",
    "BLOCK_CORRECTION_AUDIT_FILE",
    "LOW_DATA_MIN_SAMPLE",
    "BlockCorrectionRow",
    "FormatCorrectionRates",
    "classify_build_workspaces",
    "compute_format_correction_rates",
    "compute_format_error_rate",
    "compute_recovery_rate",
    "engine_keys_by_task",
    "format_correction_rates_from_audit_dir",
    "in_flight_by_repo",
    "is_build_success",
    "read_audit_tail",
    "read_block_correction_rows",
    "task_completed_without_gap",
    "unproven_clause_count",
]
