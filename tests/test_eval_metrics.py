from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alan.eval_metrics import (
    MERGE_SURVIVAL_WINDOW,
    _find_revert_ts,
    _is_merge_ok_row,
    _parse_git_record,
    _parse_merge_ts,
    extract_pr_number,
    gate_declared,
    merge_survival_rate,
    merge_survived,
    parse_git_log_output,
    parse_merge_audit_rows,
    requirements_coverage_rate,
    revert_targets_pr,
)


def test_extract_pr_number_found() -> None:
    assert extract_pr_number("Fix bug (#42)") == 42


def test_extract_pr_number_not_found() -> None:
    assert extract_pr_number("Fix bug without PR") is None


def test_revert_targets_pr_not_revert() -> None:
    assert revert_targets_pr("Fix something (#5)", "", 5) is False


def test_revert_targets_pr_no_id_match() -> None:
    assert revert_targets_pr("Revert old thing", "", 5) is False


def test_revert_targets_pr_id_in_subject() -> None:
    assert revert_targets_pr('Revert "Fix bug (#5)"', "", 5) is True


def test_revert_targets_pr_id_in_body() -> None:
    assert revert_targets_pr("Revert something", "This reverts #5.", 5) is True


def test_revert_targets_pr_false_positive_adjacent_digit() -> None:
    assert revert_targets_pr('Revert "Fix (#51)"', "reverts #51", 5) is False


def test_merge_survived_no_revert() -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    assert merge_survived(ts, None) is True


def test_merge_survived_after_window() -> None:
    merge_ts = datetime(2026, 1, 1, tzinfo=UTC)
    revert_ts = merge_ts + MERGE_SURVIVAL_WINDOW + timedelta(seconds=1)
    assert merge_survived(merge_ts, revert_ts) is True


def test_merge_survived_at_boundary() -> None:
    merge_ts = datetime(2026, 1, 1, tzinfo=UTC)
    revert_ts = merge_ts + MERGE_SURVIVAL_WINDOW
    assert merge_survived(merge_ts, revert_ts) is False


def test_merge_survived_within_window() -> None:
    merge_ts = datetime(2026, 1, 1, tzinfo=UTC)
    revert_ts = merge_ts + timedelta(days=1)
    assert merge_survived(merge_ts, revert_ts) is False


def test_gate_declared_none() -> None:
    assert gate_declared(None) is False


def test_gate_declared_empty() -> None:
    assert gate_declared([]) is False


def test_gate_declared_nonempty() -> None:
    assert gate_declared(["req"]) is True


def test_is_merge_ok_row_wrong_action() -> None:
    assert _is_merge_ok_row({"action": "merge_failed", "pr_number": 1}) is False


def test_is_merge_ok_row_pr_not_int() -> None:
    assert _is_merge_ok_row({"action": "merge_ok", "pr_number": "1"}) is False


def test_is_merge_ok_row_valid() -> None:
    assert _is_merge_ok_row({"action": "merge_ok", "pr_number": 5}) is True


def test_parse_merge_audit_rows_empty() -> None:
    assert parse_merge_audit_rows("") == []


def test_parse_merge_audit_rows_mixed() -> None:
    text = (
        "\n"
        "{invalid json}\n"
        '{"action": "merge_failed", "pr_number": 1}\n'
        '{"action": "merge_ok", "pr_number": 42, "timestamp_utc": "2026-01-01T00:00:00+00:00"}\n'
    )
    rows = parse_merge_audit_rows(text)
    assert len(rows) == 1
    assert rows[0]["pr_number"] == 42


def test_parse_git_record_too_few_fields() -> None:
    assert _parse_git_record("just a string") is None


def test_parse_git_record_empty_ts() -> None:
    assert _parse_git_record("\x00subject") is None


def test_parse_git_record_empty_subject() -> None:
    assert _parse_git_record("2026-01-01T00:00:00+00:00\x00") is None


def test_parse_git_record_two_fields() -> None:
    ts = "2026-01-01T00:00:00+00:00"
    result = _parse_git_record(f"{ts}\x00Fix bug (#1)")
    assert result == (ts, "Fix bug (#1)", "")


def test_parse_git_record_three_fields() -> None:
    ts = "2026-01-01T00:00:00+00:00"
    result = _parse_git_record(f"{ts}\x00Fix bug (#1)\x00Some body text")
    assert result == (ts, "Fix bug (#1)", "Some body text")


def test_parse_git_log_output_empty() -> None:
    assert parse_git_log_output("") == []


def test_parse_git_log_output_records() -> None:
    ts = "2026-01-01T00:00:00+00:00"
    text = f"{ts}\x00Fix bug (#1)\x00\x1e\x1e{ts}\x00Add feature (#2)\x00body\x1e"
    records = parse_git_log_output(text)
    assert len(records) == 2
    assert records[0] == (ts, "Fix bug (#1)", "")
    assert records[1] == (ts, "Add feature (#2)", "body")


def test_parse_git_log_output_skips_invalid() -> None:
    ts = "2026-01-01T00:00:00+00:00"
    text = f"bad record\x1e{ts}\x00Good commit (#3)\x00\x1e"
    records = parse_git_log_output(text)
    assert len(records) == 1


def test_parse_merge_ts_missing_key() -> None:
    assert _parse_merge_ts({}) is None


def test_parse_merge_ts_non_string() -> None:
    assert _parse_merge_ts({"timestamp_utc": 123}) is None


def test_parse_merge_ts_invalid_dt() -> None:
    assert _parse_merge_ts({"timestamp_utc": "not-a-date"}) is None


def test_parse_merge_ts_valid() -> None:
    result = _parse_merge_ts({"timestamp_utc": "2026-01-01T00:00:00+00:00"})
    assert result is not None
    assert result.year == 2026


def test_find_revert_ts_empty_records() -> None:
    assert _find_revert_ts(5, []) is None


def test_find_revert_ts_no_match() -> None:
    ts = "2026-01-01T00:00:00+00:00"
    records = [(ts, "Fix bug (#1)", "")]
    assert _find_revert_ts(1, records) is None


def test_find_revert_ts_found() -> None:
    ts = "2026-01-15T00:00:00+00:00"
    records = [(ts, 'Revert "Fix bug (#1)"', "")]
    result = _find_revert_ts(1, records)
    assert result is not None
    assert result.day == 15


def test_find_revert_ts_invalid_timestamp() -> None:
    records = [("bad-ts", 'Revert "Fix (#1)"', "")]
    assert _find_revert_ts(1, records) is None


def test_merge_survival_rate_empty_rows() -> None:
    assert merge_survival_rate([], []) is None


def test_merge_survival_rate_no_timestamp() -> None:
    rows: list[dict[str, object]] = [{"action": "merge_ok", "pr_number": 1}]
    assert merge_survival_rate(rows, []) is None


def test_merge_survival_rate_non_int_pr() -> None:
    rows: list[dict[str, object]] = [
        {
            "action": "merge_ok",
            "pr_number": "not-an-int",
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        }
    ]
    assert merge_survival_rate(rows, []) is None


def test_merge_survival_rate_all_survived() -> None:
    rows: list[dict[str, object]] = [
        {
            "action": "merge_ok",
            "pr_number": 1,
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        }
    ]
    rate = merge_survival_rate(rows, [])
    assert rate == 1.0


def test_merge_survival_rate_some_reverted() -> None:
    merge_ts_str = "2026-01-01T00:00:00+00:00"
    revert_ts_str = "2026-01-03T00:00:00+00:00"
    rows: list[dict[str, object]] = [
        {"action": "merge_ok", "pr_number": 1, "timestamp_utc": merge_ts_str},
        {"action": "merge_ok", "pr_number": 2, "timestamp_utc": merge_ts_str},
    ]
    records = [(revert_ts_str, 'Revert "PR (#1)"', "")]
    rate = merge_survival_rate(rows, records)
    assert rate == 0.5


def test_requirements_coverage_rate_empty() -> None:
    assert requirements_coverage_rate([]) == 0.0


def test_requirements_coverage_rate_all_none() -> None:
    rate = requirements_coverage_rate([None, None])
    assert rate == 0.0


def test_requirements_coverage_rate_mixed() -> None:
    rate = requirements_coverage_rate([None, [], ["req1"], ["req1", "req2"]])
    assert rate == 0.5
