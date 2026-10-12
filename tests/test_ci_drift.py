from __future__ import annotations

import pytest

from alan import _ci_drift as cd


def _runs(values: list[float], job: str = "job", first_pr: int = 100) -> list[cd.RunSample]:
    return [
        cd.RunSample(index, f"2026-09-{20 + index:02d}T04:00:00Z", first_pr + index, {job: value})
        for index, value in enumerate(values)
    ]


def test_drift_constants_are_pinned() -> None:
    assert cd.WINDOW == 7
    assert cd.MIN_SAMPLES == 4
    assert cd.SLOW_RATIO == 1.10
    assert cd.SLACK_RATIO == 0.80
    assert cd.SLACK_MIN_S == 20
    assert cd.BUCKET_PCT == 5
    assert cd.LABEL_MAX == 50


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Apply, restart and verify a self-update (#1847)", 1847),
        ("Apply, restart and verify a self-update (#1847)\n\nbody mentions (#12)", 1847),
        ("Merge (#5) then fix (#9)", 9),
        ("direct push without a number", None),
        ("", None),
        ("subject\n\n* inner (#77)", None),
    ],
)
def test_pr_number_reads_the_squash_subject(message: str, expected: int | None) -> None:
    assert cd.pr_number(message) == expected


@pytest.mark.parametrize(("median", "proposed"), [(372.4, 372), (372.6, 373), (0.2, 1)])
def test_proposed_budget_is_the_rounded_median_and_at_least_one(
    median: float, proposed: int
) -> None:
    assert cd.proposed_budget_s(median) == proposed


def test_crossing_index_needs_the_minimum_samples_before_it_can_fire() -> None:
    assert cd.crossing_index([500, 500, 500], lambda median: median > 1) is None


def test_crossing_index_is_the_first_rolling_median_over_the_line() -> None:
    values = [100.0] * 7 + [150.0] * 7
    assert cd.crossing_index(values, lambda median: median > 110) == 10


def test_crossing_index_is_none_when_the_median_never_crosses() -> None:
    assert cd.crossing_index([100.0] * 10, lambda median: median > 110) is None


def test_a_lone_spike_does_not_move_the_rolling_median() -> None:
    values = [100.0] * 5 + [300.0] + [100.0] * 5
    assert cd.crossing_index(values, lambda median: median > 110) is None


def test_jump_window_is_the_runs_leading_up_to_the_crossing() -> None:
    window = cd.jump_window(_runs([1.0] * 14), 10)
    assert [run.pr for run in window] == [107, 108, 109, 110]


def test_jump_window_clamps_at_the_start_of_the_history() -> None:
    assert [run.run_id for run in cd.jump_window(_runs([1.0] * 6), 3)] == [0, 1, 2, 3]


def test_jump_window_of_no_crossing_is_empty() -> None:
    assert cd.jump_window(_runs([1.0] * 3), None) == ()


def test_a_sustained_slowdown_files_a_slow_finding_with_before_and_after() -> None:
    samples = _runs([100.0] * 7 + [150.0] * 7)
    [finding] = cd.drift_findings("tests.yml", samples, {"job": 100.0})
    assert finding.direction is cd.Direction.SLOW
    assert (finding.before_s, finding.after_s, finding.budget_s) == (100.0, 150.0, 100.0)
    assert finding.proposed_s == 150
    assert finding.samples == 7
    assert [run.pr for run in finding.window] == [107, 108, 109, 110]


def test_a_median_within_ten_percent_of_budget_is_quiet() -> None:
    assert cd.drift_findings("tests.yml", _runs([109.0] * 7), {"job": 100.0}) == []


def test_a_sustained_slowdown_just_over_ten_percent_fires() -> None:
    [finding] = cd.drift_findings("tests.yml", _runs([111.0] * 7), {"job": 100.0})
    assert finding.direction is cd.Direction.SLOW


def test_a_median_far_under_budget_files_a_slack_finding() -> None:
    [finding] = cd.drift_findings("tests.yml", _runs([100.0] * 7 + [60.0] * 7), {"job": 100.0})
    assert finding.direction is cd.Direction.SLACK
    assert finding.proposed_s == 60
    assert [run.pr for run in finding.window] == [107, 108, 109, 110]


def test_a_median_only_slightly_under_budget_is_quiet() -> None:
    assert cd.drift_findings("tests.yml", _runs([85.0] * 7), {"job": 100.0}) == []


def test_slack_needs_at_least_twenty_seconds_under_budget() -> None:
    assert cd.drift_findings("tests.yml", _runs([20.0] * 7), {"job": 30.0}) == []


def test_slack_fires_when_twenty_seconds_under_and_below_eighty_percent() -> None:
    [finding] = cd.drift_findings("tests.yml", _runs([60.0] * 7), {"job": 80.0})
    assert finding.direction is cd.Direction.SLACK


def test_fewer_than_four_samples_never_fire() -> None:
    assert cd.drift_findings("tests.yml", _runs([900.0] * 3), {"job": 100.0}) == []


def test_exactly_four_samples_can_fire_with_no_before_median() -> None:
    [finding] = cd.drift_findings("tests.yml", _runs([200.0] * 4), {"job": 100.0})
    assert finding.before_s is None
    assert finding.samples == 4


def test_before_median_needs_four_earlier_runs() -> None:
    [finding] = cd.drift_findings("tests.yml", _runs([100.0] * 3 + [200.0] * 7), {"job": 100.0})
    assert finding.before_s is None


def test_jobs_absent_from_the_runs_are_skipped() -> None:
    assert cd.drift_findings("tests.yml", _runs([900.0] * 7, job="other"), {"job": 100.0}) == []


def test_findings_come_back_sorted_by_job_name() -> None:
    samples = [cd.RunSample(i, "2026-09-20", i, {"b": 300.0, "a": 300.0}) for i in range(7)]
    findings = cd.drift_findings("tests.yml", samples, {"b": 100.0, "a": 100.0})
    assert [finding.job for finding in findings] == ["a", "b"]


def test_a_run_with_a_missing_pr_still_forms_part_of_the_window() -> None:
    samples = [cd.RunSample(i, "", None, {"job": 300.0}) for i in range(7)]
    [finding] = cd.drift_findings("nightly.yml", samples, {"job": 100.0})
    assert all(run.pr is None for run in finding.window)


@pytest.mark.parametrize("direction", list(cd.Direction))
def test_labels_fit_the_github_label_limit(direction: cd.Direction) -> None:
    label = cd.finding_label(
        "tests.yml", "Frontend lint (eslint + tsc + knip + dep-cruiser)", direction
    )
    assert len(label) <= cd.LABEL_MAX
    assert label.startswith("ci-slow:" if direction is cd.Direction.SLOW else "ci-budget-slack:")


def test_label_is_stable_and_carries_a_readable_slug() -> None:
    first = cd.finding_label("tests.yml", "Backend tests + coverage", cd.Direction.SLOW)
    assert first == cd.finding_label("tests.yml", "Backend tests + coverage", cd.Direction.SLOW)
    assert first.startswith("ci-slow:backend-tests-coverage-")


def test_label_differs_per_workflow_and_per_direction() -> None:
    slow = cd.finding_label("tests.yml", "deploy", cd.Direction.SLOW)
    assert slow != cd.finding_label("ci-deploy.yml", "deploy", cd.Direction.SLOW)
    assert slow != cd.finding_label("tests.yml", "deploy", cd.Direction.SLACK)


def test_label_slug_never_ends_with_a_dangling_dash_after_truncation() -> None:
    label = cd.finding_label("tests.yml", "a" * 34 + " b" * 10, cd.Direction.SLOW)
    assert "--" not in label
    assert len(label) <= cd.LABEL_MAX


def test_marker_is_stable_within_a_five_percent_bucket() -> None:
    assert cd.dedup_marker(cd.Direction.SLOW, 100.0, 126.0) == cd.dedup_marker(
        cd.Direction.SLOW, 100.0, 129.0
    )
    assert cd.dedup_marker(cd.Direction.SLOW, 100.0, 126.0) == "slow:100:125"


def test_marker_changes_across_buckets_budgets_and_directions() -> None:
    base = cd.dedup_marker(cd.Direction.SLOW, 100.0, 126.0)
    assert base != cd.dedup_marker(cd.Direction.SLOW, 100.0, 131.0)
    assert base != cd.dedup_marker(cd.Direction.SLOW, 110.0, 126.0)
    assert base != cd.dedup_marker(cd.Direction.SLACK, 100.0, 126.0)
