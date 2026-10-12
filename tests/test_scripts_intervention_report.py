from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import intervention_report as report  # type: ignore[import-not-found]  # noqa: E402

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "intervention_report"

WINDOW_START = date(2026, 5, 15)
WINDOW_END = date(2026, 7, 3)


def _git_log_text(records: list[tuple[str, str, str]]) -> str:
    return "".join(f"\x1e{ts}\x00{subject}\x00{body}" for ts, subject, body in records)


def _jsonl(rows: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def test_merged_pr_numbers_from_git_log_extracts_in_window() -> None:
    text = _git_log_text(
        [
            ("2026-05-20T10:00:00+00:00", "feat: add thing (#101)", ""),
            ("2026-06-01T10:00:00+00:00", "fix: bug (#102)", ""),
            ("2026-05-10T10:00:00+00:00", "chore: before window (#103)", ""),
            ("2026-07-04T10:00:00+00:00", "chore: after window (#104)", ""),
            ("2026-06-05T10:00:00+00:00", "chore: no pr number here", ""),
        ]
    )
    numbers = report.merged_pr_numbers_from_git_log(text, WINDOW_START, WINDOW_END)
    assert numbers == frozenset({101, 102})


def test_merged_pr_numbers_from_git_log_normalizes_to_utc_at_boundary() -> None:
    text = _git_log_text([("2026-07-04T01:30:00+02:00", "docs: late-night merge (#200)", "")])
    numbers = report.merged_pr_numbers_from_git_log(text, WINDOW_START, WINDOW_END)
    assert numbers == frozenset({200})


def test_merged_pr_numbers_from_git_log_skips_unparseable_timestamp() -> None:
    text = "\x1enot-a-date\x00subject (#1)\x00"
    assert (
        report.merged_pr_numbers_from_git_log(text, WINDOW_START, WINDOW_END) == frozenset()
    )


def test_merged_pr_numbers_from_git_log_empty_text() -> None:
    assert report.merged_pr_numbers_from_git_log("", WINDOW_START, WINDOW_END) == frozenset()


def test_merged_pr_numbers_from_json_filters_window() -> None:
    data = [
        {"number": 1, "merged_at": "2026-05-20T10:00:00+00:00"},
        {"number": 2, "merged_at": "2026-05-01T10:00:00+00:00"},
    ]
    numbers = report.merged_pr_numbers_from_json(json.dumps(data), WINDOW_START, WINDOW_END)
    assert numbers == frozenset({1})


def test_merged_pr_numbers_from_json_ignores_malformed_entries() -> None:
    data = [
        "not-a-dict",
        {"number": "not-an-int", "merged_at": "2026-05-20T10:00:00+00:00"},
        {"number": True, "merged_at": "2026-05-20T10:00:00+00:00"},
        {"number": 5, "merged_at": 12345},
        {"number": 6, "merged_at": "not-a-date"},
        {"number": 7},
    ]
    numbers = report.merged_pr_numbers_from_json(json.dumps(data), WINDOW_START, WINDOW_END)
    assert numbers == frozenset()


def test_merged_pr_numbers_from_json_invalid_json_returns_empty() -> None:
    assert report.merged_pr_numbers_from_json("not json", WINDOW_START, WINDOW_END) == frozenset()


def test_merged_pr_numbers_from_json_non_list_returns_empty() -> None:
    assert (
        report.merged_pr_numbers_from_json(json.dumps({"a": 1}), WINDOW_START, WINDOW_END)
        == frozenset()
    )


def test_merged_pr_numbers_from_json_accepts_gh_camel_case_merged_at() -> None:
    data = [{"number": 42, "mergedAt": "2026-05-20T10:00:00Z"}]
    numbers = report.merged_pr_numbers_from_json(json.dumps(data), WINDOW_START, WINDOW_END)
    assert numbers == frozenset({42})


def test_merged_pr_numbers_from_json_reads_committed_fixture() -> None:
    text = (_FIXTURES_DIR / "merged_prs.json").read_text(encoding="utf-8")
    numbers = report.merged_pr_numbers_from_json(text, WINDOW_START, WINDOW_END)
    assert numbers == frozenset({501, 502, 503, 504})


def test_alan_merged_pr_numbers_excludes_fixture_and_window() -> None:
    text = _jsonl(
        [
            {
                "timestamp_utc": "2026-05-18T19:16:51+00:00",
                "task_id": "pr-merge-task",
                "action": "merge_ok",
                "pr_number": 42,
            },
            {
                "timestamp_utc": "2026-05-20T10:00:00+00:00",
                "task_id": "real-task",
                "action": "merge_ok",
                "pr_number": 501,
            },
            {
                "timestamp_utc": "2026-05-01T10:00:00+00:00",
                "task_id": "real-task",
                "action": "merge_ok",
                "pr_number": 999,
            },
            {
                "timestamp_utc": "2026-05-21T10:00:00+00:00",
                "task_id": "real-task",
                "action": "merge_failed",
                "pr_number": 502,
            },
        ]
    )
    numbers = report.alan_merged_pr_numbers(text, WINDOW_START, WINDOW_END)
    assert numbers == frozenset({501})


def test_alan_merged_pr_numbers_empty_text() -> None:
    assert report.alan_merged_pr_numbers("", WINDOW_START, WINDOW_END) == frozenset()


def test_alan_merged_pr_numbers_skips_bad_timestamp() -> None:
    text = _jsonl([{"task_id": "x", "action": "merge_ok", "pr_number": 1}])
    assert report.alan_merged_pr_numbers(text, WINDOW_START, WINDOW_END) == frozenset()


def test_parse_spiral_chain_resets_filters_kind_and_window() -> None:
    text = _jsonl(
        [
            {
                "timestamp_utc": "2026-06-01T10:00:00+00:00",
                "kind": "chain_reset",
                "task_id": "real-task-one",
                "handover_chain_id": "h1",
            },
            {
                "timestamp_utc": "2026-06-02T10:00:00+00:00",
                "kind": "chain_decay",
                "task_id": "real-task-one",
                "handover_chain_id": "h1",
            },
            {
                "timestamp_utc": "2026-05-01T10:00:00+00:00",
                "kind": "chain_reset",
                "task_id": "real-task-two",
                "handover_chain_id": "h2",
            },
        ]
    )
    rows = report.parse_spiral_chain_resets(text, WINDOW_START, WINDOW_END)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "real-task-one"


def test_parse_spiral_chain_resets_excludes_known_fixture_task_ids() -> None:
    text = _jsonl(
        [
            {
                "timestamp_utc": "2026-06-01T10:00:00+00:00",
                "kind": "chain_reset",
                "task_id": "verify-task",
                "handover_chain_id": "h1",
            },
            {
                "timestamp_utc": "2026-06-02T10:00:00+00:00",
                "kind": "chain_reset",
                "task_id": "pr-merge-task",
                "handover_chain_id": "h2",
            },
            {
                "timestamp_utc": "2026-06-03T10:00:00+00:00",
                "kind": "chain_reset",
                "task_id": "dep-450",
                "handover_chain_id": "h3",
            },
        ]
    )
    rows = report.parse_spiral_chain_resets(text, WINDOW_START, WINDOW_END)
    assert [r["task_id"] for r in rows] == ["dep-450"]


def test_parse_spiral_chain_resets_skips_bad_json_and_bad_timestamp() -> None:
    text = "not-json\n" + _jsonl([{"kind": "chain_reset", "task_id": "t1"}])
    assert report.parse_spiral_chain_resets(text, WINDOW_START, WINDOW_END) == []


def test_chain_reset_incident_count_dedupes_by_task_and_chain() -> None:
    rows: list[dict[str, object]] = [
        {"task_id": "t1", "handover_chain_id": "h1"},
        {"task_id": "t1", "handover_chain_id": "h1"},
        {"task_id": "t1", "handover_chain_id": "h2"},
        {"task_id": "t2"},
    ]
    assert report.chain_reset_incident_count(rows) == 3


def test_chain_reset_incident_count_skips_rows_missing_task_id() -> None:
    rows: list[dict[str, object]] = [{"handover_chain_id": "h1"}]
    assert report.chain_reset_incident_count(rows) == 0


def test_chain_reset_incident_count_empty() -> None:
    assert report.chain_reset_incident_count([]) == 0


def test_alan_review_dismissed_count_excludes_fixture_and_window() -> None:
    text = _jsonl(
        [
            {
                "timestamp_utc": "2026-05-20T10:00:00+00:00",
                "task_id": "verify-task",
                "action": "review_dismissed",
            },
            {
                "timestamp_utc": "2026-05-21T10:00:00+00:00",
                "task_id": "real-task",
                "action": "review_dismissed",
            },
            {
                "timestamp_utc": "2026-05-01T10:00:00+00:00",
                "task_id": "real-task",
                "action": "review_dismissed",
            },
            {
                "timestamp_utc": "2026-05-22T10:00:00+00:00",
                "task_id": "real-task",
                "action": "review_ok",
            },
        ]
    )
    assert report.alan_review_dismissed_count_in_window(text, WINDOW_START, WINDOW_END) == 1


def test_alan_review_dismissed_count_bad_timestamp_skipped() -> None:
    text = _jsonl([{"task_id": "x", "action": "review_dismissed"}])
    assert report.alan_review_dismissed_count_in_window(text, WINDOW_START, WINDOW_END) == 0


def test_gross_count_from_json_counts_in_window() -> None:
    data = [
        {"created_at": "2026-05-20T10:00:00+00:00"},
        {"created_at": "2026-05-01T10:00:00+00:00"},
    ]
    n = report._gross_count_from_json(  # noqa: SLF001
        json.dumps(data), WINDOW_START, WINDOW_END, ("created_at",)
    )
    assert n == 1


def test_gross_count_from_json_empty_text_returns_none() -> None:
    assert (
        report._gross_count_from_json("", WINDOW_START, WINDOW_END, ("created_at",)) is None  # noqa: SLF001
    )


def test_gross_count_from_json_invalid_json_returns_none() -> None:
    assert (
        report._gross_count_from_json("nope", WINDOW_START, WINDOW_END, ("created_at",))  # noqa: SLF001
        is None
    )


def test_gross_count_from_json_non_list_returns_none() -> None:
    assert (
        report._gross_count_from_json(  # noqa: SLF001
            json.dumps({}), WINDOW_START, WINDOW_END, ("created_at",)
        )
        is None
    )


def test_gross_count_from_json_skips_malformed_entries() -> None:
    data = ["not-a-dict", {"created_at": 123}, {"created_at": "bad"}, {"other": "field"}]
    n = report._gross_count_from_json(  # noqa: SLF001
        json.dumps(data), WINDOW_START, WINDOW_END, ("created_at",)
    )
    assert n == 0


def test_gross_count_from_json_reads_committed_rerun_fixture() -> None:
    text = (_FIXTURES_DIR / "reruns.json").read_text(encoding="utf-8")
    n = report._gross_count_from_json(text, WINDOW_START, WINDOW_END, ("created_at",))  # noqa: SLF001
    assert n == 2


def test_gross_count_from_json_reads_committed_dismissal_fixture() -> None:
    text = (_FIXTURES_DIR / "dismissals.json").read_text(encoding="utf-8")
    n = report._gross_count_from_json(text, WINDOW_START, WINDOW_END, ("dismissed_at",))  # noqa: SLF001
    assert n == 3


def test_gross_count_from_json_accepts_gh_camel_case_created_at() -> None:
    data = [{"createdAt": "2026-05-20T10:00:00Z"}]
    n = report._gross_count_from_json(  # noqa: SLF001
        json.dumps(data), WINDOW_START, WINDOW_END, report._CREATED_AT_FIELDS  # noqa: SLF001
    )
    assert n == 1


def test_gross_count_from_json_accepts_gh_camel_case_dismissed_at() -> None:
    data = [{"dismissedAt": "2026-05-20T10:00:00Z"}]
    n = report._gross_count_from_json(  # noqa: SLF001
        json.dumps(data), WINDOW_START, WINDOW_END, report._DISMISSED_AT_FIELDS  # noqa: SLF001
    )
    assert n == 1


def test_gross_count_from_json_prefers_first_matching_field() -> None:
    data = [
        {"created_at": "2026-05-20T10:00:00+00:00", "createdAt": "2026-01-01T00:00:00Z"},
    ]
    n = report._gross_count_from_json(  # noqa: SLF001
        json.dumps(data), WINDOW_START, WINDOW_END, report._CREATED_AT_FIELDS  # noqa: SLF001
    )
    assert n == 1


def test_compute_baseline_set_difference_manual_merges() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset({1, 2, 3, 4}),
        alan_merged_pr_numbers=frozenset({1, 2}),
        chain_reset_rows=[
            {"task_id": "t1", "handover_chain_id": "h1"},
            {"task_id": "t1", "handover_chain_id": "h1"},
        ],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=None,
        review_dismissal_gross=None,
        alan_review_dismissed_count=0,
    )
    assert baseline.merged_pr_count == 4
    assert baseline.manual_merge_count == 2
    assert baseline.chain_reset_raw_count == 2
    assert baseline.chain_reset_incident_count == 1
    assert baseline.tier1_raw_total == 4
    assert baseline.tier1_incident_total == 3
    assert baseline.headline_ratio == pytest.approx(1.0)
    assert baseline.ci_rerun_gross is None
    assert baseline.review_dismissal_estimate is None


def test_compute_baseline_tier3_never_fabricates_count() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset({1, 2}),
        alan_merged_pr_numbers=frozenset({1}),
        chain_reset_rows=[{"task_id": "t1"}],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=5,
        review_dismissal_gross=10,
        alan_review_dismissed_count=3,
    )
    assert baseline.tier3_state_surgery_raw_count is None
    assert baseline.tier3_state_surgery_dedup_incidents is None
    assert baseline.tier3_parallel_session_raw_count is None
    assert baseline.tier3_parallel_session_dedup_incidents is None


def test_compute_baseline_empty_denominator_returns_none() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset(),
        alan_merged_pr_numbers=frozenset(),
        chain_reset_rows=[],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=None,
        review_dismissal_gross=None,
        alan_review_dismissed_count=0,
    )
    assert baseline.merged_pr_count == 0
    assert baseline.headline_ratio is None


def test_compute_baseline_tier2_dismissal_netting() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset({1}),
        alan_merged_pr_numbers=frozenset({1}),
        chain_reset_rows=[],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=5,
        review_dismissal_gross=41,
        alan_review_dismissed_count=29,
    )
    assert baseline.ci_rerun_gross == 5
    assert baseline.review_dismissal_gross == 41
    assert baseline.alan_review_dismissed_count == 29
    assert baseline.review_dismissal_estimate == 12


def test_compute_baseline_headline_ratio_tier1_only() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset({1, 2, 3, 4}),
        alan_merged_pr_numbers=frozenset({1}),
        chain_reset_rows=[{"task_id": "t1"}, {"task_id": "t2"}],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=50,
        review_dismissal_gross=80,
        alan_review_dismissed_count=20,
    )
    assert baseline.tier1_raw_total == 5
    assert baseline.headline_ratio == pytest.approx(5 / 4)


def test_compute_baseline_review_dismissal_estimate_floors_at_zero() -> None:
    baseline = report.compute_baseline(
        merged_pr_numbers=frozenset({1}),
        alan_merged_pr_numbers=frozenset({1}),
        chain_reset_rows=[],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=None,
        review_dismissal_gross=2,
        alan_review_dismissed_count=9,
    )
    assert baseline.review_dismissal_estimate == 0


def test_compute_baseline_caveats_contain_required_strings() -> None:
    report.compute_baseline(
        merged_pr_numbers=frozenset({1}),
        alan_merged_pr_numbers=frozenset(),
        chain_reset_rows=[],
        window_start="2026-05-15",
        window_end="2026-07-03",
        ci_rerun_gross=None,
        review_dismissal_gross=None,
        alan_review_dismissed_count=0,
    )
    assert any("proxy-inclusive FLOOR" in c for c in report.CAVEATS)
    assert any("2026-07-03" in c and "roadmap-driver" in c for c in report.CAVEATS)
    assert any("job_failure_audit.jsonl" in c and "truncated" in c for c in report.CAVEATS)


def _write_logs(tmp_path: Path) -> Path:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "pr_merge_audit.jsonl").write_text(
        _jsonl(
            [
                {
                    "timestamp_utc": "2026-05-20T10:00:00+00:00",
                    "task_id": "real",
                    "action": "merge_ok",
                    "pr_number": 501,
                },
                {
                    "timestamp_utc": "2026-05-25T10:00:00+00:00",
                    "task_id": "real",
                    "action": "merge_ok",
                    "pr_number": 502,
                },
            ]
        )
    )
    (logs_dir / "spiral_audit.jsonl").write_text(
        _jsonl(
            [
                {
                    "timestamp_utc": "2026-06-01T10:00:00+00:00",
                    "kind": "chain_reset",
                    "task_id": "real-task-one",
                    "handover_chain_id": "h1",
                },
            ]
        )
    )
    (logs_dir / "verification_audit.jsonl").write_text(
        _jsonl(
            [
                {
                    "timestamp_utc": "2026-05-22T10:00:00+00:00",
                    "task_id": "real",
                    "action": "review_dismissed",
                },
            ]
        )
    )
    return logs_dir


def test_main_smoke_end_to_end_with_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs_dir = _write_logs(tmp_path)
    rc = report.main(
        [
            "--logs-root",
            str(logs_dir),
            "--merged-prs-json",
            str(_FIXTURES_DIR / "merged_prs.json"),
            "--rerun-json",
            str(_FIXTURES_DIR / "reruns.json"),
            "--dismissal-json",
            str(_FIXTURES_DIR / "dismissals.json"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "merged_pr_count 4" in out
    assert "tier1_manual_merge_count 2" in out
    assert "tier1_chain_reset_count 1 (raw) / 1 (incident-deduped" in out
    assert "tier1_raw_total 3" in out
    assert "headline_interventions_per_merged_pr 0.7500 (3/4)" in out
    assert "tier2_ci_rerun_gross 2 (gross-only" in out
    assert "tier2_review_dismissal_estimate 2 (gross 3 minus alan's own 1 auto-dismissals)" in out
    for caveat in report.CAVEATS:
        assert caveat in out


def test_main_accepts_gh_camel_case_rerun_and_dismissal_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs_dir = _write_logs(tmp_path)
    rerun_file = tmp_path / "reruns_camel.json"
    rerun_file.write_text(json.dumps([{"createdAt": "2026-05-20T10:00:00Z"}]))
    dismissal_file = tmp_path / "dismissals_camel.json"
    dismissal_file.write_text(json.dumps([{"dismissedAt": "2026-05-20T10:00:00Z"}]))
    git_log_file = tmp_path / "git_log.txt"
    git_log_file.write_text("\x1e2026-05-20T10:00:00+00:00\x00feat: x (#501)\x00")
    rc = report.main(
        [
            "--logs-root",
            str(logs_dir),
            "--git-log",
            str(git_log_file),
            "--rerun-json",
            str(rerun_file),
            "--dismissal-json",
            str(dismissal_file),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "tier2_ci_rerun_gross 1 (gross-only" in out
    assert "tier2_review_dismissal_estimate 0 (gross 1 minus alan's own 1 auto-dismissals)" in out


def test_main_defaults_to_git_log_when_no_json_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs_dir = _write_logs(tmp_path)
    git_log_file = tmp_path / "git_log.txt"
    git_log_file.write_text(
        "\x1e2026-05-20T10:00:00+00:00\x00feat: x (#501)\x00"
        "\x1e2026-05-25T10:00:00+00:00\x00feat: y (#502)\x00"
    )
    rc = report.main(["--logs-root", str(logs_dir), "--git-log", str(git_log_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "merged_pr_count 2" in out
    assert "tier1_manual_merge_count 0" in out


def test_main_no_rerun_or_dismissal_json_reports_unmeasured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs_dir = _write_logs(tmp_path)
    git_log_file = tmp_path / "git_log.txt"
    git_log_file.write_text("\x1e2026-05-20T10:00:00+00:00\x00feat: x (#501)\x00")
    rc = report.main(["--logs-root", str(logs_dir), "--git-log", str(git_log_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tier2_ci_rerun_gross n/a — not captured this pass" in out
    assert "tier2_review_dismissal_estimate n/a" in out
    assert "tier3_state_surgery n/a" in out
    assert "tier3_parallel_human_session n/a" in out
