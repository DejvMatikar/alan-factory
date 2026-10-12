from __future__ import annotations

import pytest

from alan import _ci_budget_render as render
from alan import ci_budget as cb
from alan._ci_drift import Direction, DriftFinding, RunSample
from alan.ci_budget import Report, Status, Verdict

_FAIL = Verdict("Backend tests + coverage", Status.FAIL, 472.0, 304.0, 1.02)
_WARN = Verdict("Python lint (pyright strict)", Status.WARN, 40.0, 30.0)
_CONTENTION = Verdict("Frontend tests", Status.CONTENTION, 130.0, 60.0, 1.5)
_UNBUDGETED = Verdict("Brand new job", Status.UNBUDGETED, duration_s=88.0)
_OK = Verdict("Detect changed paths", Status.OK, 12.0, 12.0)
_SKIPPED = Verdict("Coverage PR comment", Status.NOT_MEASURED, note="skipped")


def _finding(direction: Direction = Direction.SLOW, **overrides: object) -> DriftFinding:
    fields: dict[str, object] = {
        "workflow": "tests.yml",
        "job": "Backend tests + coverage",
        "direction": direction,
        "budget_s": 140.0,
        "after_s": 210.0,
        "before_s": 141.0,
        "samples": 7,
        "window": (
            RunSample(1, "2026-09-27T04:03:28Z", 1845, {}),
            RunSample(2, "2026-09-27T06:11:23Z", 1847, {}),
        ),
        "proposed_s": 210,
    }
    fields.update(overrides)
    return DriftFinding(**fields)  # type: ignore[arg-type]


def test_annotation_escapes_percent_and_newlines() -> None:
    line = render.annotation("error", "50% slower\r\nsecond line")
    assert line == "::error title=CI duration budget::50%25 slower%0D%0Asecond line"


@pytest.fixture
def blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb, "FAIL_BLOCKS_PR", True)


def test_fail_message_carries_the_advisory_note_while_the_check_is_advisory() -> None:
    assert render.fail_message(_FAIL, None).endswith(render.ADVISORY_NOTE)


@pytest.mark.usefixtures("blocking")
def test_fail_message_drops_the_advisory_note_once_the_check_blocks() -> None:
    assert render.ADVISORY_NOTE not in render.fail_message(_FAIL, None)


def test_an_advisory_fail_is_annotated_as_a_warning() -> None:
    assert render.annotation_lines(Report((_FAIL,)))[0].startswith("::warning")


def test_fail_message_names_job_budget_measured_and_difference() -> None:
    text = render.fail_message(_FAIL, None)
    assert "Backend tests + coverage" in text
    assert "measured 472s against a budget of 304s (+168s (+55%))" in text
    assert "fail point of 456s" in text


def test_fail_message_tells_the_fixer_not_to_raise_the_budget_or_rerun() -> None:
    text = render.fail_message(_FAIL, None)
    assert "Do not raise the budget in this PR" in text
    assert ".github/ci-duration-budgets.json" in text
    assert "A rerun will not change this verdict" in text


def test_fail_message_for_a_non_test_job_has_no_slowest_section() -> None:
    assert "Slowest test files" not in render.fail_message(_FAIL, None)
    assert "no test files" not in render.fail_message(_FAIL, None)


def test_fail_message_lists_the_slowest_changed_test_files() -> None:
    text = render.fail_message(_FAIL, [("tests/test_big.py", 42.25), ("tests/test_small.py", 3.0)])
    assert "Slowest test files this PR added or changed" in text
    assert "`tests/test_big.py` (42.2s)" in text
    assert "`tests/test_small.py` (3.0s)" in text


def test_fail_message_says_so_when_no_changed_test_file_has_timings() -> None:
    assert "added or changed no test files with recorded timings" in render.fail_message(_FAIL, [])


def test_warn_message_states_both_thresholds_in_seconds() -> None:
    text = render.warn_message(_WARN)
    assert "measured 40s against a budget of 30s" in text
    assert "Warns from 40s" in text
    assert "fails from 50s" in text


def test_contention_message_blames_the_host() -> None:
    text = render.contention_message(_CONTENTION)
    assert "host contention is the likely cause" in text
    assert "median 1.50x budget" in text
    assert "Not failing" in text


def test_contention_message_without_a_peer_ratio_still_renders() -> None:
    assert "median 0.00x budget" in render.contention_message(
        Verdict("x", Status.CONTENTION, 130.0, 60.0)
    )


def test_unbudgeted_message_asks_for_a_budget_under_the_workflow() -> None:
    text = render.unbudgeted_message(_UNBUDGETED, "tests.yml")
    assert "no duration budget is committed" in text
    assert 'under "tests.yml" in .github/ci-duration-budgets.json' in text


def _annotated(report: Report) -> list[str]:
    return render.annotation_lines(report)


@pytest.mark.usefixtures("blocking")
def test_annotations_list_errors_before_warnings_then_notices() -> None:
    report = Report(
        (_WARN, _CONTENTION, _UNBUDGETED, _OK, _SKIPPED, _FAIL),
        ("Jobs API partly unavailable",),
        {"Backend tests + coverage": (("tests/test_big.py", 42.0),)},
    )
    lines = _annotated(report)
    assert [line.split(" ", 1)[0] for line in lines] == [
        "::error",
        "::warning",
        "::warning",
        "::warning",
        "::warning",
    ]
    assert "tests/test_big.py" in lines[0]
    assert "Jobs API partly unavailable" in lines[-1]


def test_annotations_are_capped() -> None:
    warnings = tuple(Verdict(f"job {i}", Status.UNBUDGETED, duration_s=5.0) for i in range(30))
    assert len(_annotated(Report(warnings))) == render.MAX_ANNOTATIONS


def test_a_clean_report_has_no_annotations() -> None:
    assert _annotated(Report((_OK, _SKIPPED))) == []


def test_summary_has_a_row_per_job_with_budget_measured_and_difference() -> None:
    summary = render.summary_markdown(Report((_FAIL, _UNBUDGETED, _OK, _SKIPPED)))
    assert "| Job | Budget | Measured | Over budget | Verdict |" in summary
    assert "| Backend tests + coverage | 304s | 472s | +168s (+55%) | fail |" in summary
    assert "| Brand new job | none | 88s | - | unbudgeted |" in summary
    assert "| Detect changed paths | 12s | 12s | +0s (+0%) | ok |" in summary
    assert "| Coverage PR comment | none | not measured (skipped) | - | not-measured |" in summary


def test_summary_escapes_pipes_in_job_names() -> None:
    summary = render.summary_markdown(Report((Verdict("a | b", Status.OK, 1.0, 1.0),)))
    assert "| a \\| b |" in summary


@pytest.mark.usefixtures("blocking")
def test_summary_carries_details_notices_and_the_thresholds_line() -> None:
    summary = render.summary_markdown(Report((_FAIL,), ("budget file unusable",)))
    assert "- budget file unusable" in summary
    assert "- **error**: Backend tests + coverage: measured 472s" in summary
    assert "Warns above +10% and at least 10s over budget" in summary
    assert "fails above +50% and at least 20s over budget" in summary
    assert "docs/architecture/ci_duration_budgets.md" in summary


def test_summary_of_a_report_without_verdicts_has_no_table() -> None:
    summary = render.summary_markdown(Report(notices=("nothing was checked",)))
    assert "| Job |" not in summary
    assert "- nothing was checked" in summary


def test_comment_body_starts_with_the_sticky_marker_and_links_the_run() -> None:
    body = render.comment_body(Report((_WARN,)), "https://example/run/1")
    assert body.startswith(render.COMMENT_MARKER)
    assert "[Run details](https://example/run/1)" in body


def test_resolved_comment_keeps_the_marker() -> None:
    body = render.resolved_comment_body("https://example/run/2")
    assert body.startswith(render.COMMENT_MARKER)
    assert "within budget" in body
    assert "https://example/run/2" in body


def test_slow_issue_title_and_body_carry_the_medians_window_and_proposal() -> None:
    finding = _finding()
    assert render.drift_issue_title(finding) == (
        'CI job "Backend tests + coverage" drifted above its duration budget: 210s median vs 140s'
    )
    body = render.drift_issue_body(finding)
    assert "Workflow `tests.yml`, job **Backend tests + coverage**." in body
    assert "| Committed budget | 140s |" in body
    assert "| Median of the last 7 successful main runs | 210s |" in body
    assert "| Median of the 7 runs before those | 141s |" in body
    assert "| Proposed budget | 210s |" in body
    assert (
        "rose above 1.10x the budget within these merges: #1845 (2026-09-27), #1847 (2026-09-27)"
        in body
    )
    assert "raise the budget to 210s in its own PR" in body
    assert "a raise is a human decision" in body
    assert "docs/architecture/ci_duration_budgets.md" in body


def test_slack_issue_proposes_a_lower_budget() -> None:
    finding = _finding(Direction.SLACK, after_s=90.0, proposed_s=90)
    assert render.drift_issue_title(finding) == (
        'CI job "Backend tests + coverage" runs well under its duration budget: 90s median vs 140s'
    )
    body = render.drift_issue_body(finding)
    assert "fell below 0.80x the budget" in body
    assert "Lower the budget to 90s in .github/ci-duration-budgets.json" in body
    assert "Lowering a budget is always allowed" in body


def test_issue_body_without_earlier_runs_or_a_window_says_so() -> None:
    body = render.drift_issue_body(_finding(before_s=None, window=()))
    assert "n/a (fewer than 4 earlier runs)" in body
    assert "within these merges: unavailable." in body


def test_window_entries_without_a_pr_or_a_date_fall_back_to_the_run() -> None:
    window = (RunSample(77, "", None, {}), RunSample(78, "2026-09-28T01:00:00Z", None, {}))
    body = render.drift_issue_body(_finding(window=window))
    assert "run 77, run 78 (2026-09-28)" in body


@pytest.mark.parametrize("status", list(Status))
def test_every_status_renders_in_the_summary(status: Status) -> None:
    verdict = Verdict("job", status, 50.0, 30.0, 1.0, "note")
    assert "| job |" in render.summary_markdown(Report((verdict,)))
