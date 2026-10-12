from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alan import ci_budget as cb

_START = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)
_STRESS = "Stress changed specs (repeat-each 5, no retries)"


def _stamp(offset_s: float) -> str:
    return (_START + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(
    name: str,
    seconds: float,
    conclusion: str | None = "success",
    steps: tuple[cb.StepTiming, ...] = (),
) -> cb.JobTiming:
    return cb.JobTiming(name, conclusion, _stamp(0), _stamp(seconds), steps)


def test_thresholds_are_pinned() -> None:
    assert cb.WARN_RATIO == 0.10
    assert cb.WARN_MIN_S == 10
    assert cb.FAIL_RATIO == 0.50
    assert cb.FAIL_MIN_S == 20
    assert cb.CONTENTION_MIN_BUDGET_S == 60
    assert cb.CONTENTION_MIN_PEERS == 2
    assert cb.CONTENTION_MEDIAN_RATIO == 1.20
    assert cb.TIMEOUT_MARGIN == 0.95
    assert cb.FAIL_BLOCKS_PR is False
    assert cb.CHECK_JOB_NAME == "Duration budgets (advisory)"
    assert cb.REPORTING_JOB_NAMES == frozenset({"SonarCloud (advisory)", "Coverage PR comment"})
    assert cb.PER_PR_EXCLUDED_JOBS == frozenset(
        {"Duration budgets (advisory)", "SonarCloud (advisory)", "Coverage PR comment"}
    )
    assert cb.PR_ONLY_STEPS == frozenset({_STRESS})
    assert cb.MEASURED_CONCLUSIONS == frozenset({"success", "failure"})


@pytest.mark.parametrize(
    ("budget", "duration", "expected"),
    [
        (100, 100, cb.Status.OK),
        (100, 90, cb.Status.OK),
        (100, 110, cb.Status.OK),
        (100, 110.5, cb.Status.WARN),
        (100, 150, cb.Status.WARN),
        (100, 150.5, cb.Status.FAIL),
        (100, 400, cb.Status.FAIL),
        (20, 29, cb.Status.OK),
        (20, 30, cb.Status.WARN),
        (20, 39, cb.Status.WARN),
        (20, 40, cb.Status.FAIL),
        (300, 330, cb.Status.OK),
        (300, 331, cb.Status.WARN),
        (300, 450, cb.Status.WARN),
        (300, 451, cb.Status.FAIL),
    ],
)
def test_tier_boundaries(budget: float, duration: float, expected: cb.Status) -> None:
    assert cb.tier(duration, budget) is expected


@pytest.mark.parametrize(
    ("budget", "warn_point", "fail_point"),
    [(20, 30, 40), (100, 110, 150), (304, 334.4, 456)],
)
def test_warn_and_fail_points(budget: float, warn_point: float, fail_point: float) -> None:
    assert cb.warn_point_s(budget) == pytest.approx(warn_point)
    assert cb.fail_point_s(budget) == pytest.approx(fail_point)


def test_parse_ts_reads_github_timestamps() -> None:
    assert cb.parse_ts("2026-09-29T10:00:00Z") == _START


def test_span_s_of_a_valid_pair() -> None:
    assert cb.span_s(_stamp(0), _stamp(75)) == 75.0


@pytest.mark.parametrize(
    ("started", "completed"),
    [(None, _stamp(1)), (_stamp(0), None), ("not-a-time", _stamp(1)), (_stamp(0), "nope")],
)
def test_span_s_of_missing_or_invalid_timestamps_is_none(
    started: str | None, completed: str | None
) -> None:
    assert cb.span_s(started, completed) is None


def test_duration_is_completion_minus_start() -> None:
    assert cb.measured_duration_s(_job("a", 140)) == 140.0


def test_failed_jobs_are_measured_too() -> None:
    assert cb.measured_duration_s(_job("a", 90, "failure")) == 90.0


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "timed_out", None])
def test_other_conclusions_are_not_measured(conclusion: str | None) -> None:
    assert cb.measured_duration_s(_job("a", 90, conclusion)) is None


def test_a_job_without_a_valid_span_is_not_measured() -> None:
    job = cb.JobTiming("a", "success", None, _stamp(5))
    assert cb.measured_duration_s(job) is None


def test_pr_only_stress_step_is_subtracted() -> None:
    stress = cb.StepTiming(_STRESS, _stamp(20), _stamp(80))
    other = cb.StepTiming("Run Playwright E2E", _stamp(80), _stamp(150))
    assert cb.measured_duration_s(_job("e2e", 150, steps=(stress, other))) == 90.0


def test_a_stress_step_without_timestamps_subtracts_nothing() -> None:
    stress = cb.StepTiming(_STRESS, None, None)
    assert cb.measured_duration_s(_job("e2e", 150, steps=(stress,))) == 150.0


def test_subtraction_never_goes_negative() -> None:
    stress = cb.StepTiming(_STRESS, _stamp(0), _stamp(500))
    assert cb.measured_duration_s(_job("e2e", 100, steps=(stress,))) == 0.0


def test_peer_ratio_is_the_leave_one_out_median() -> None:
    durations = {"a": 300.0, "b": 100.0, "c": 130.0, "d": 90.0}
    budgets = {"a": 100.0, "b": 100.0, "c": 100.0, "d": 100.0}
    assert cb.peer_median_ratio("a", durations, budgets) == 1.0


def test_peers_below_the_budget_cutoff_are_ignored() -> None:
    durations = {"a": 300.0, "b": 100.0, "c": 130.0, "short": 40.0}
    budgets = {"a": 100.0, "b": 100.0, "c": 100.0, "short": 10.0}
    assert cb.peer_median_ratio("a", durations, budgets) == pytest.approx(1.15)


def test_peers_without_a_budget_are_ignored() -> None:
    durations = {"a": 300.0, "b": 100.0, "unbudgeted": 900.0}
    assert cb.peer_median_ratio("a", durations, {"a": 100.0, "b": 100.0}) is None


def test_fewer_than_two_peers_gives_no_ratio() -> None:
    durations = {"a": 300.0, "b": 100.0}
    assert cb.peer_median_ratio("a", durations, {"a": 100.0, "b": 100.0}) is None


def _run(seconds_by_job: dict[str, float]) -> list[cb.JobTiming]:
    return [_job(name, seconds) for name, seconds in seconds_by_job.items()]


def _statuses(verdicts: list[cb.Verdict]) -> dict[str, cb.Status]:
    return {verdict.name: verdict.status for verdict in verdicts}


_PEER_BUDGETS = {"a": 100.0, "b": 100.0, "c": 100.0, "d": 100.0}


def test_a_single_slow_job_among_normal_peers_fails() -> None:
    verdicts = cb.classify(_run({"a": 200, "b": 100, "c": 105, "d": 95}), _PEER_BUDGETS)
    assert _statuses(verdicts) == {
        "a": cb.Status.FAIL,
        "b": cb.Status.OK,
        "c": cb.Status.OK,
        "d": cb.Status.OK,
    }
    assert cb.has_failure(verdicts)


def test_a_uniformly_slow_run_demotes_the_failure_to_contention() -> None:
    verdicts = cb.classify(_run({"a": 200, "b": 160, "c": 150, "d": 145}), _PEER_BUDGETS)
    by_name = {verdict.name: verdict for verdict in verdicts}
    assert by_name["a"].status is cb.Status.CONTENTION
    assert by_name["a"].peer_ratio == pytest.approx(1.5)
    assert not cb.has_failure(verdicts)


def test_peers_just_under_the_contention_ratio_do_not_demote() -> None:
    verdicts = cb.classify(_run({"a": 200, "b": 119, "c": 119, "d": 119}), _PEER_BUDGETS)
    assert _statuses(verdicts)["a"] is cb.Status.FAIL


def test_peers_exactly_at_the_contention_ratio_demote() -> None:
    verdicts = cb.classify(_run({"a": 200, "b": 120, "c": 120, "d": 120}), _PEER_BUDGETS)
    assert _statuses(verdicts)["a"] is cb.Status.CONTENTION


def test_a_failure_with_too_few_peers_is_never_demoted() -> None:
    verdicts = cb.classify(_run({"a": 200, "b": 300}), {"a": 100.0, "b": 100.0})
    assert _statuses(verdicts)["a"] is cb.Status.FAIL


def test_a_warn_is_reported_as_warn_with_its_figures() -> None:
    verdict = cb.classify(_run({"a": 130}), {"a": 100.0})[0]
    assert verdict.status is cb.Status.WARN
    assert (verdict.duration_s, verdict.budget_s) == (130.0, 100.0)
    assert verdict.over_s == 30.0
    assert verdict.over_pct == pytest.approx(30.0)


def test_a_job_without_a_budget_is_unbudgeted_not_failed() -> None:
    verdict = cb.classify(_run({"new job": 900}), {})[0]
    assert verdict.status is cb.Status.UNBUDGETED
    assert verdict.duration_s == 900.0
    assert verdict.budget_s == 0.0


def test_skipped_and_cancelled_jobs_are_listed_not_measured() -> None:
    jobs = [_job("a", 50, "skipped"), _job("b", 50, "cancelled"), _job("c", 50, None)]
    verdicts = cb.classify(jobs, {"a": 10.0, "b": 10.0, "c": 10.0})
    assert _statuses(verdicts) == {name: cb.Status.NOT_MEASURED for name in "abc"}
    assert [verdict.note for verdict in verdicts] == ["skipped", "cancelled", "in progress"]


def test_the_check_job_itself_is_excluded() -> None:
    verdicts = cb.classify([_job(cb.CHECK_JOB_NAME, 600), _job("a", 100)], {"a": 100.0})
    assert [verdict.name for verdict in verdicts] == ["a"]


@pytest.mark.parametrize("conclusion", ["success", "skipped", None])
def test_reporting_jobs_outside_the_needs_chain_never_get_a_row(conclusion: str | None) -> None:
    reporting = [_job(name, 500, conclusion) for name in sorted(cb.REPORTING_JOB_NAMES)]
    budgets = {name: 10.0 for name in cb.REPORTING_JOB_NAMES} | {"a": 100.0}
    verdicts = cb.classify([*reporting, _job("a", 100)], budgets)
    assert [verdict.name for verdict in verdicts] == ["a"]


def test_reporting_jobs_never_count_as_contention_peers() -> None:
    reporting = [_job(name, 500) for name in sorted(cb.REPORTING_JOB_NAMES)]
    budgets = {name: 100.0 for name in cb.REPORTING_JOB_NAMES} | {"a": 100.0}
    verdicts = cb.classify([_job("a", 200), *reporting], budgets)
    assert _statuses(verdicts) == {"a": cb.Status.FAIL}


def test_unmeasured_jobs_never_count_as_peers() -> None:
    jobs = [_job("a", 200), _job("b", 400, "cancelled"), _job("c", 400, "skipped")]
    verdicts = cb.classify(jobs, {"a": 100.0, "b": 100.0, "c": 100.0})
    assert _statuses(verdicts)["a"] is cb.Status.FAIL


def test_has_failure_is_false_for_an_empty_run() -> None:
    assert not cb.has_failure([])


def test_verdict_without_a_budget_has_no_percentage() -> None:
    assert cb.Verdict("a", cb.Status.UNBUDGETED, duration_s=50.0).over_pct == 0.0


def test_report_defaults_to_an_empty_tests_workflow_report() -> None:
    report = cb.Report()
    assert (report.verdicts, report.notices, dict(report.slowest), report.workflow) == (
        (),
        (),
        {},
        "tests.yml",
    )


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_workspace_path_resolves_a_relative_path_against_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cb.workspace_path("a/../b.json") == tmp_path.resolve() / "b.json"


def test_workspace_path_accepts_an_absolute_path_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cb.workspace_path(str(tmp_path / "x.md")) == tmp_path.resolve() / "x.md"


@pytest.mark.parametrize("value", ["../escape.json", "a/../../escape.json"])
def test_workspace_path_rejects_a_relative_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    with pytest.raises(ValueError, match="resolves outside the workspace"):
        cb.workspace_path(value)


def test_workspace_path_rejects_an_absolute_path_outside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    with pytest.raises(ValueError, match="resolves outside the workspace"):
        cb.workspace_path(str(tmp_path / "escape.json"))


def test_workspace_path_rejects_a_symlink_that_leaves_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(workspace)
    with pytest.raises(ValueError, match="resolves outside the workspace"):
        cb.workspace_path("link/budgets.json")


def test_load_budgets_reads_the_workflow_job_shape(tmp_path: Path) -> None:
    path = _write(tmp_path, {"tests.yml": {"Backend tests + coverage": 304, "job": 12.5}})
    assert cb.load_budgets(path) == {"tests.yml": {"Backend tests + coverage": 304.0, "job": 12.5}}


def test_load_budgets_of_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(cb.BudgetFileError, match="cannot read"):
        cb.load_budgets(tmp_path / "absent.json")


def test_load_budgets_of_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "budgets.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(cb.BudgetFileError, match="cannot read"):
        cb.load_budgets(path)


def test_load_budgets_requires_a_top_level_mapping(tmp_path: Path) -> None:
    with pytest.raises(cb.BudgetFileError, match="mapping of workflow"):
        cb.load_budgets(_write(tmp_path, [1, 2]))


def test_load_budgets_requires_a_mapping_of_jobs(tmp_path: Path) -> None:
    with pytest.raises(cb.BudgetFileError, match="mapping of job name"):
        cb.load_budgets(_write(tmp_path, {"tests.yml": 5}))


@pytest.mark.parametrize("bad", [True, "300", None, [3]])
def test_load_budgets_rejects_non_numbers(tmp_path: Path, bad: object) -> None:
    with pytest.raises(cb.BudgetFileError, match="not a number"):
        cb.load_budgets(_write(tmp_path, {"tests.yml": {"job": bad}}))


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf")])
def test_load_budgets_rejects_non_positive_or_non_finite_numbers(
    tmp_path: Path, bad: float
) -> None:
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps({"tests.yml": {"job": bad}}), encoding="utf-8")
    with pytest.raises(cb.BudgetFileError, match="positive number"):
        cb.load_budgets(path)
