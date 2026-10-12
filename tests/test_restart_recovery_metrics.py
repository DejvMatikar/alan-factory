from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan.learning_metrics import restart_recovery_counts
from alan.queue import Queue


def _meta_blob(queue: Queue, key: str) -> dict[str, object]:
    raw = queue.get_meta(key)
    assert raw is not None
    return json.loads(raw)  # type: ignore[no-any-return]


def _row(outcome: str, *, team: str | None, ts: datetime) -> str:
    entry: dict[str, object] = {"outcome": outcome, "timestamp_utc": ts.isoformat()}
    if team is not None:
        entry["team"] = team
    return json.dumps(entry)


def test_restart_recovery_counts_empty_input_returns_none() -> None:
    assert restart_recovery_counts("", now=datetime.now(UTC)) is None


def test_restart_recovery_counts_groups_by_team_and_tallies_outcomes() -> None:
    now = datetime.now(UTC)
    text = "\n".join([
        _row("attached", team="implementation", ts=now),
        _row("attached", team="implementation", ts=now),
        _row("attach_lost", team="implementation", ts=now),
        _row("requeued_for_resume", team="verification", ts=now),
        _row("interrupted_failed", team="verification", ts=now),
    ])
    counts = restart_recovery_counts(text, now=now)
    assert counts is not None
    assert counts["implementation"]["attached"] == 2
    assert counts["implementation"]["attach_lost"] == 1
    assert counts["implementation"]["requeued_for_resume"] == 0
    assert counts["verification"]["requeued_for_resume"] == 1
    assert counts["verification"]["interrupted_failed"] == 1


def test_restart_recovery_counts_missing_team_buckets_as_unknown() -> None:
    now = datetime.now(UTC)
    counts = restart_recovery_counts(_row("attached", team=None, ts=now), now=now)
    assert counts is not None
    assert counts["unknown"]["attached"] == 1


def test_restart_recovery_counts_excludes_rows_outside_window() -> None:
    now = datetime.now(UTC)
    stale = now - timedelta(hours=200)
    text = "\n".join([
        _row("attached", team="implementation", ts=now),
        _row("attached", team="implementation", ts=stale),
    ])
    counts = restart_recovery_counts(text, now=now, window_hours=168)
    assert counts is not None
    assert counts["implementation"]["attached"] == 1


def test_restart_recovery_counts_ignores_unknown_outcome_and_malformed_lines() -> None:
    now = datetime.now(UTC)
    text = "\n".join([
        _row("some_future_outcome", team="implementation", ts=now),
        "{not valid json",
        _row("attached", team="implementation", ts=now),
    ])
    counts = restart_recovery_counts(text, now=now)
    assert counts is not None
    assert counts["implementation"]["attached"] == 1
    assert "some_future_outcome" not in counts["implementation"]


def test_restart_recovery_counts_missing_timestamp_row_excluded() -> None:
    now = datetime.now(UTC)
    text = json.dumps({"outcome": "attached", "team": "implementation"})
    assert restart_recovery_counts(text, now=now) is None


def test_restart_recovery_counts_malformed_timestamp_row_excluded() -> None:
    now = datetime.now(UTC)
    text = json.dumps(
        {"outcome": "attached", "team": "implementation", "timestamp_utc": "not-a-timestamp"}
    )
    assert restart_recovery_counts(text, now=now) is None


def test_restart_recovery_counts_naive_timestamp_row_excluded() -> None:
    now = datetime.now(UTC)
    text = json.dumps(
        {
            "outcome": "attached", "team": "implementation",
            "timestamp_utc": now.replace(tzinfo=None).isoformat(),
        }
    )
    assert restart_recovery_counts(text, now=now) is None


def test_restart_recovery_counts_all_rows_stale_returns_none() -> None:
    now = datetime.now(UTC)
    stale = now - timedelta(hours=200)
    counts = restart_recovery_counts(_row("attached", team="implementation", ts=stale), now=now)
    assert counts is None


def test_restart_recovery_counts_future_timestamp_row_excluded() -> None:
    now = datetime.now(UTC)
    future = now + timedelta(hours=1)
    counts = restart_recovery_counts(_row("attached", team="implementation", ts=future), now=now)
    assert counts is None


@pytest.fixture
def queue(tmp_path: Path):  # type: ignore[no-untyped-def]
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def test_compute_restart_recovery_safe_persists_blob(queue: Queue, tmp_path: Path) -> None:
    from alan.teams.triage import _compute_restart_recovery_safe

    now = datetime.now(UTC)
    (tmp_path / "resume_audit.jsonl").write_text(
        _row("attached", team="implementation", ts=now) + "\n", encoding="utf-8"
    )
    _compute_restart_recovery_safe(queue, tmp_path, now)
    raw = queue.get_meta("restart_recovery_v1")
    assert raw is not None
    blob = json.loads(raw)
    assert blob["counts"]["implementation"]["attached"] == 1
    assert blob["computed_at"] == now.isoformat()
    assert blob["window_hours"] == 168.0


def test_compute_restart_recovery_safe_no_audit_file_saves_none_counts(
    queue: Queue, tmp_path: Path
) -> None:
    from alan.teams.triage import _compute_restart_recovery_safe

    now = datetime.now(UTC)
    _compute_restart_recovery_safe(queue, tmp_path, now)
    blob = _meta_blob(queue, "restart_recovery_v1")
    assert blob["counts"] is None


def test_compute_restart_recovery_safe_swallows_exception(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.triage import _compute_restart_recovery_safe

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("reduce failed")

    monkeypatch.setattr("alan.teams.triage.restart_recovery_counts", _boom)
    _compute_restart_recovery_safe(queue, tmp_path, datetime.now(UTC))
    assert queue.get_meta("restart_recovery_v1") is None


def test_save_meta_safe_regression_for_merge_gate_path(
    queue: Queue, tmp_path: Path
) -> None:
    from alan.teams.triage import _compute_merge_gate_recovery_safe

    now = datetime.now(UTC)
    row = {"block_class": "genuine", "recovered": True, "timestamp_utc": now.isoformat()}
    (tmp_path / "merge_gate_audit.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    _compute_merge_gate_recovery_safe(queue, tmp_path, now)
    blob = _meta_blob(queue, "merge_gate_recovery_v1")
    assert blob["rates"] == {"genuine": {"recovered": 1, "total": 1, "lifetime_total": 1}}
    assert blob["computed_at"] == now.isoformat()


def test_load_restart_recovery_missing_returns_none(queue: Queue) -> None:
    from alan.health import load_restart_recovery

    assert load_restart_recovery(queue) is None


def test_load_restart_recovery_corrupt_json_returns_none(queue: Queue) -> None:
    from alan.health import load_restart_recovery

    queue.set_meta("restart_recovery_v1", "{not valid json")
    assert load_restart_recovery(queue) is None


def test_load_restart_recovery_non_dict_json_returns_none(queue: Queue) -> None:
    from alan.health import load_restart_recovery

    queue.set_meta("restart_recovery_v1", json.dumps([1, 2, 3]))
    assert load_restart_recovery(queue) is None


def test_load_restart_recovery_happy_path(queue: Queue) -> None:
    from alan.health import load_restart_recovery

    payload = {"counts": {"implementation": {"attached": 1}}, "computed_at": "x"}
    queue.set_meta("restart_recovery_v1", json.dumps(payload))
    assert load_restart_recovery(queue) == payload


def test_restart_recovery_lines_no_data_reads_not_enough_data() -> None:
    from alan.shop_stats import _restart_recovery_lines

    lines = _restart_recovery_lines(None)
    assert any("not enough data yet" in line for line in lines)


def test_restart_recovery_lines_missing_counts_key_reads_not_enough_data() -> None:
    from alan.shop_stats import _restart_recovery_lines

    lines = _restart_recovery_lines({"computed_at": "x"})
    assert any("not enough data yet" in line for line in lines)


def test_restart_recovery_lines_empty_counts_dict_reads_not_enough_data() -> None:
    from alan.shop_stats import _restart_recovery_lines

    lines = _restart_recovery_lines({"computed_at": "x", "counts": {}})
    assert any("not enough data yet" in line for line in lines)


def test_restart_recovery_lines_skips_non_dict_team_entry() -> None:
    from alan.shop_stats import _restart_recovery_lines

    data = {
        "computed_at": "x",
        "counts": {
            "implementation": {"attached": 1, "attach_lost": 0,
                                "requeued_for_resume": 0, "interrupted_failed": 0},
            "corrupt": "not-a-dict",
        },
    }
    lines = _restart_recovery_lines(data)
    assert not any("corrupt" in line for line in lines)
    assert any("implementation" in line for line in lines)


def test_restart_recovery_lines_renders_header_window_and_rederive_note() -> None:
    from alan.shop_stats import _restart_recovery_lines

    data = {
        "computed_at": "2026-08-15T00:00:00+00:00",
        "counts": {"implementation": {"attached": 2, "attach_lost": 1, "interrupted_failed": 0}},
    }
    lines = _restart_recovery_lines(data)
    assert "last 7d" in lines[0]
    assert "2026-08-15T00:00:00+00:00" in lines[0]
    assert "excludes rederive-path jobs" in lines[0]


def test_restart_recovery_lines_per_team_row_and_separated_resume_queued_line() -> None:
    from alan.shop_stats import _restart_recovery_lines

    data = {
        "computed_at": "x",
        "counts": {
            "implementation": {
                "attached": 2, "attach_lost": 1, "requeued_for_resume": 3,
                "interrupted_failed": 0,
            },
            "verification": {
                "attached": 0, "attach_lost": 0, "requeued_for_resume": 1,
                "interrupted_failed": 1,
            },
        },
    }
    lines = _restart_recovery_lines(data)
    impl_line = next(line for line in lines if "implementation" in line)
    assert "attached=2" in impl_line
    assert "attach_lost=1" in impl_line
    assert "interrupted_failed=0" in impl_line
    assert "requeued" not in impl_line.split(":")[0]
    decision_line = lines[-1]
    assert decision_line.startswith("- resume queued (decision, not an outcome):")
    assert decision_line.endswith("4")


def test_assemble_shop_stats_block_includes_restart_recovery(queue: Queue) -> None:
    from alan.shop_stats import assemble_shop_stats_block

    now = datetime.now(UTC)
    queue.set_meta(
        "restart_recovery_v1",
        json.dumps({
            "computed_at": now.isoformat(),
            "counts": {"implementation": {"attached": 1, "attach_lost": 0,
                                           "requeued_for_resume": 0, "interrupted_failed": 0}},
        }),
    )
    block = assemble_shop_stats_block(queue)
    assert "Restart recovery" in block
    assert "implementation: attached=1" in block


def test_restart_recovery_outcomes_includes_resume_succeeded_and_failed() -> None:
    from alan.learning_metrics import _RESTART_RECOVERY_OUTCOMES  # noqa: SLF001

    assert "resume_succeeded" in _RESTART_RECOVERY_OUTCOMES
    assert "resume_failed" in _RESTART_RECOVERY_OUTCOMES


def test_restart_recovery_counts_tallies_resume_succeeded_and_failed() -> None:
    now = datetime.now(UTC)
    text = "\n".join([
        _row("resume_succeeded", team="verification", ts=now),
        _row("resume_succeeded", team="verification", ts=now),
        _row("resume_failed", team="verification", ts=now),
    ])
    counts = restart_recovery_counts(text, now=now)
    assert counts is not None
    assert counts["verification"]["resume_succeeded"] == 2
    assert counts["verification"]["resume_failed"] == 1


def test_restart_recovery_lines_render_resume_succeeded_and_failed_counts() -> None:
    from alan.shop_stats import _restart_recovery_lines

    data = {
        "computed_at": "x",
        "counts": {
            "verification": {
                "attached": 0, "attach_lost": 0, "requeued_for_resume": 2,
                "interrupted_failed": 0, "resume_succeeded": 1, "resume_failed": 1,
            },
        },
    }
    lines = _restart_recovery_lines(data)
    verif_line = next(line for line in lines if "verification" in line)
    assert "resume_succeeded=1" in verif_line
    assert "resume_failed=1" in verif_line
