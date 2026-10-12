from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import (
    driver_loop_runs,
    gold_label,
    incident,
    learning_metrics,
    run_summary,
    scenario_binding,
)
from alan.teams import (
    _copilot_resolution_block,
    _pr_audit_log,
    _pr_post,
    _scenario_rebind_block,
    triage,
)


def test_triage_read_sweeper_status_skips_malformed_rows(tmp_path: Path) -> None:
    audit_file = tmp_path / "triage_audit.jsonl"
    valid_row = {
        "action": "sweep_complete",
        "task_id": "t1",
        "inv_id": "inv_001",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "surface_count": 3,
        "blocked_count": 1,
    }
    audit_file.write_text("{not valid json}\n" + json.dumps(valid_row) + "\n")
    status = triage.read_sweeper_status(tmp_path)
    assert status["surface_count"] == 3
    assert status["blocked_count"] == 1


def test_incident_gather_audit_rows_skips_malformed_rows(tmp_path: Path) -> None:
    (tmp_path / "job_failure_audit.jsonl").write_text(
        json.dumps({"task_id": "t1", "error": "boom"}) + "\n" + "{bad json}\n"
    )
    rows = incident.gather_audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0].raw["task_id"] == "t1"


def test_learning_metrics_merge_gate_recovery_rates_skips_malformed_rows() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    text = "{bad json}\n" + json.dumps(
        {
            "block_class": "flaky_test",
            "recovered": True,
            "timestamp_utc": now.isoformat(),
        }
    )
    rates = learning_metrics.merge_gate_recovery_rates(text, now=now)
    assert rates is not None
    assert rates["flaky_test"]["recovered"] == 1


def test_gold_label_load_gold_labels_skips_malformed_rows(tmp_path: Path) -> None:
    path = tmp_path / "gold_labels.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"question_class": "x"}) + "\n")
    rows = gold_label.load_gold_labels(path)
    assert len(rows) == 1
    assert rows[0]["question_class"] == "x"


def test_copilot_resolution_block_refused_rows_skips_malformed_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "copilot_resolution_refused",
        "pr_number": 1,
        "thread_id": "T1",
        "repo": "acme/widget",
        "reason": "already_handled",
    }
    (logs / _pr_audit_log.COPILOT_AUDIT_FILE).write_text(
        "{bad json}\n" + json.dumps(row) + "\n"
    )
    rows = _copilot_resolution_block._refused_rows(1, "acme/widget")  # noqa: SLF001
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "T1"


def test_scenario_rebind_block_rejected_rows_skips_malformed_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    row = {
        "action": "fix_rebind_rejected",
        "workspace_id": "ws-1",
        "scenario_id": "s1",
        "when": "curl http://example.com",
        "reason": "disallowed",
        "rule": _scenario_rebind_block.RULE_ALLOWLIST,
    }
    (logs / scenario_binding.AUDIT_FILE).write_text("{bad json}\n" + json.dumps(row) + "\n")
    rows = _scenario_rebind_block._rejected_rows("ws-1")  # noqa: SLF001
    assert len(rows) == 1
    assert rows[0]["scenario_id"] == "s1"


def test_run_summary_read_stream_events_missing_file_returns_empty(tmp_path: Path) -> None:
    assert run_summary._read_stream_events(tmp_path / "missing.jsonl") == []  # noqa: SLF001


def test_run_summary_read_stream_events_multi_line_file_preserves_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(
        json.dumps({"type": "system"}) + "\n"
        + "{bad json}\n"
        + json.dumps({"type": "result"}) + "\n"
    )
    events = run_summary._read_stream_events(path)  # noqa: SLF001
    assert [e["type"] for e in events] == ["system", "result"]


def test_driver_loop_runs_parse_events_pairs_unstripped_line() -> None:
    raw_line = "  " + json.dumps({"type": "system"}) + "  "
    events = driver_loop_runs._parse_events(raw_line + "\n")  # noqa: SLF001
    assert len(events) == 1
    line, event = events[0]
    assert line == raw_line
    assert event["type"] == "system"


def test_pr_post_parse_review_objects_returns_tuple_not_list() -> None:
    stdout = json.dumps({"id": 1}) + "\n" + json.dumps({"id": 2})
    result = _pr_post.parse_review_objects(stdout)
    assert isinstance(result, tuple)
    assert result == ({"id": 1}, {"id": 2})
