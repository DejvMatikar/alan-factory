from __future__ import annotations

import re
from collections.abc import Sequence, Sized
from datetime import datetime, timedelta

from alan import store

MERGE_SURVIVAL_WINDOW: timedelta = timedelta(days=14)

_PR_RE: re.Pattern[str] = re.compile(r"\(#(\d+)\)")
_REVERT_PREFIX = "revert"
_GIT_LOG_SEP = "\x1e"
_GIT_FIELD_SEP = "\x00"
_MERGE_OK_ACTION = "merge_ok"


def extract_pr_number(subject: str) -> int | None:
    m = _PR_RE.search(subject)
    return int(m.group(1)) if m else None


def revert_targets_pr(subject: str, body: str, pr_number: int) -> bool:
    if not subject.lower().startswith(_REVERT_PREFIX):
        return False
    pattern = re.compile(rf"#{pr_number}(?!\d)")
    return bool(pattern.search(subject) or pattern.search(body))


def merge_survived(merge_ts: datetime, revert_ts: datetime | None) -> bool:
    if revert_ts is None:
        return True
    return (revert_ts - merge_ts) > MERGE_SURVIVAL_WINDOW


def gate_declared(requirements: Sized | None) -> bool:
    return requirements is not None and len(requirements) > 0


def _is_merge_ok_row(row: dict[str, object]) -> bool:
    return row.get("action") == _MERGE_OK_ACTION and isinstance(
        row.get("pr_number"), int
    )


def parse_merge_audit_rows(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is not None and _is_merge_ok_row(row):
            rows.append(row)
    return rows


def _parse_git_record(record: str) -> tuple[str, str, str] | None:
    fields = record.split(_GIT_FIELD_SEP, 2)
    if len(fields) < 2:
        return None
    ts = fields[0].strip()
    subject = fields[1].strip()
    if not ts or not subject:
        return None
    body = fields[2].strip() if len(fields) > 2 else ""
    return (ts, subject, body)


def parse_git_log_output(text: str) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    for raw in text.split(_GIT_LOG_SEP):
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = _parse_git_record(stripped)
        if parsed is not None:
            records.append(parsed)
    return records


def _parse_merge_ts(row: dict[str, object]) -> datetime | None:
    ts_raw = row.get("timestamp_utc")
    if not isinstance(ts_raw, str):
        return None
    try:
        return datetime.fromisoformat(ts_raw)
    except ValueError:
        return None


def _find_revert_ts(
    pr_number: int,
    git_records: list[tuple[str, str, str]],
) -> datetime | None:
    for ts_str, subject, body in git_records:
        if not revert_targets_pr(subject, body, pr_number):
            continue
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            continue
    return None


def merge_survival_rate(
    audit_rows: list[dict[str, object]],
    git_records: list[tuple[str, str, str]],
) -> float | None:
    survived = 0
    total = 0
    for row in audit_rows:
        merge_ts = _parse_merge_ts(row)
        if merge_ts is None:
            continue
        pr_raw = row.get("pr_number")
        if not isinstance(pr_raw, int):
            continue
        total += 1
        revert_ts = _find_revert_ts(pr_raw, git_records)
        if merge_survived(merge_ts, revert_ts):
            survived += 1
    return survived / total if total > 0 else None


def requirements_coverage_rate(
    requirements_list: Sequence[Sized | None],
) -> float:
    if not requirements_list:
        return 0.0
    covered = sum(1 for r in requirements_list if gate_declared(r))
    return covered / len(requirements_list)
