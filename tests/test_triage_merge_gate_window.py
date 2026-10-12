from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from alan.learning_metrics import MERGE_GATE_WINDOW_HOURS
from alan.queue import Queue
from alan.teams.triage import (
    _MERGE_GATE_AUDIT_FILE,
    _MERGE_GATE_RECOVERY_META_KEY,
    _compute_merge_gate_recovery_safe,
)

_NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def _write_audit_row(audit_dir: Path, *, block_class: str, recovered: bool, ts: datetime) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    row = {"block_class": block_class, "recovered": recovered, "timestamp_utc": ts.isoformat()}
    (audit_dir / _MERGE_GATE_AUDIT_FILE).write_text(json.dumps(row) + "\n", encoding="utf-8")


def _blob(queue: Queue) -> dict[str, object]:
    raw = queue.get_meta(_MERGE_GATE_RECOVERY_META_KEY)
    assert raw is not None
    result: dict[str, object] = json.loads(raw)
    return result


def test_compute_merge_gate_recovery_safe_stamps_window_hours(
    queue: Queue, tmp_path: Path
) -> None:
    _write_audit_row(tmp_path, block_class="genuine", recovered=True, ts=_NOW)
    _compute_merge_gate_recovery_safe(queue, tmp_path, _NOW)
    blob = _blob(queue)
    assert blob["window_hours"] == MERGE_GATE_WINDOW_HOURS


def test_compute_merge_gate_recovery_safe_reflects_passed_now(
    queue: Queue, tmp_path: Path
) -> None:
    _write_audit_row(tmp_path, block_class="genuine", recovered=True, ts=_NOW)
    _compute_merge_gate_recovery_safe(queue, tmp_path, _NOW)
    blob = _blob(queue)
    assert blob["computed_at"] == _NOW.isoformat()
    assert blob["rates"] == {"genuine": {"recovered": 1, "total": 1, "lifetime_total": 1}}


def test_compute_merge_gate_recovery_safe_ceiling_excludes_fixture_cluster_dates(
    queue: Queue, tmp_path: Path
) -> None:
    fixture_ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    _write_audit_row(tmp_path, block_class="genuine", recovered=True, ts=fixture_ts)
    _compute_merge_gate_recovery_safe(queue, tmp_path, _NOW)
    blob = _blob(queue)
    assert blob["rates"] == {"genuine": {"recovered": 0, "total": 0, "lifetime_total": 1}}


def test_compute_merge_gate_recovery_safe_ceiling_excludes_fixture_cluster_start_date(
    queue: Queue, tmp_path: Path
) -> None:
    fixture_ts = datetime(2026, 6, 30, 0, 0, 0, tzinfo=UTC)
    _write_audit_row(tmp_path, block_class="genuine", recovered=True, ts=fixture_ts)
    _compute_merge_gate_recovery_safe(queue, tmp_path, _NOW)
    blob = _blob(queue)
    assert blob["rates"] == {"genuine": {"recovered": 0, "total": 0, "lifetime_total": 1}}


def test_compute_merge_gate_recovery_safe_ceiling_excludes_fixture_cluster_end_date(
    queue: Queue, tmp_path: Path
) -> None:
    fixture_ts = datetime(2026, 7, 2, 23, 59, 59, tzinfo=UTC)
    _write_audit_row(tmp_path, block_class="genuine", recovered=True, ts=fixture_ts)
    _compute_merge_gate_recovery_safe(queue, tmp_path, _NOW)
    blob = _blob(queue)
    assert blob["rates"] == {"genuine": {"recovered": 0, "total": 0, "lifetime_total": 1}}


def test_merge_gate_window_hours_stays_below_fifty_nine_days() -> None:
    assert MERGE_GATE_WINDOW_HOURS < 59 * 24
