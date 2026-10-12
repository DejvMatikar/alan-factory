from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan.finisher_rate import FINISHER_EPOCH
from alan.health_repo_metrics import (
    BLOCK_CORRECTION_AUDIT_FILE,
    read_audit_tail,
)
from alan.health_windows import (
    HealthWindows,
    SourceReach,
    Verdict,
    Window,
    WindowSources,
    compare_windows,
    compute_health_windows,
    read_window_sources,
)
from alan.queue import Queue
from alan.workspace import (
    Intervention,
    Meta,
    Requirement,
    Status,
    Task,
    TaskStatus,
    Workspace,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
NO_REACH = SourceReach(funnel=None, first_pass=None, corrections=None)
METRIC_NAMES = (
    "outcome",
    "autonomous",
    "build_success",
    "first_pass_approval",
    "unproven_clauses",
    "format_error",
    "correction_recovery",
    "rescue_success",
    "rescue_load",
)
LOWER_IS_BETTER = frozenset({"format_error", "unproven_clauses"})


@dataclass(frozen=True)
class Row:
    ts: datetime | None
    task_id: str | None
    attempts: int
    outcome: str


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def build_task(status: TaskStatus = TaskStatus.COMPLETED, *, team: str = "implementation") -> Task:
    return Task(id="t1", team=team, status=status)


def make_ws(
    task_id: str,
    *,
    status: Status = Status.COMPLETED,
    terminal_days_ago: float | None = 1.0,
    created_days_ago: float = 40.0,
    engine_key: str | None = None,
    tasks: list[Task] | None = None,
    requirements: list[Requirement] | None = None,
    interventions: list[Intervention] | None = None,
) -> Workspace:
    meta = Meta(
        task_id=task_id,
        title=task_id,
        description="d",
        status=status,
        tasks=[build_task()] if tasks is None else tasks,
        requirements=requirements,
        interventions=interventions or [],
        engine_key=engine_key,
        created_at=ago(created_days_ago),
        updated_at=NOW,
        terminal_at=None if terminal_days_ago is None else ago(terminal_days_ago),
    )
    return Workspace(task_id=task_id, path=Path("/nonexistent") / task_id, meta=meta)


def make_sources(queue: Queue, **overrides: Any) -> WindowSources:
    fields: dict[str, Any] = {
        "queue": queue,
        "workspaces": [],
        "job_failure_text": "",
        "merge_audit_text": "",
        "spiral_audit_text": "",
        "trajectories": [],
        "corrections": [],
        "reach": NO_REACH,
    }
    fields.update(overrides)
    return WindowSources(**fields)


def compute(queue: Queue, *, now: datetime = NOW, **overrides: Any) -> HealthWindows:
    return compute_health_windows(make_sources(queue, **overrides), now=now)


def first_attempt_rows(
    *, when: datetime, errors: int, total: int, task_id: str | None = None
) -> list[Row]:
    return [
        Row(when, task_id, 1, "exhausted" if index < errors else "clean")
        for index in range(total)
    ]


def _window(value: float | None, *, sample: int = 20, covered: int = 30) -> Window:
    return Window(value=value, sample=sample, covered_days=covered)


@pytest.mark.parametrize(
    ("d7", "d30", "higher_is_better", "expected"),
    [
        (0.80, 0.70, True, Verdict.BETTER),
        (0.60, 0.70, True, Verdict.WORSE),
        (0.80, 0.70, False, Verdict.WORSE),
        (0.60, 0.70, False, Verdict.BETTER),
        (0.70, 0.70, True, Verdict.SAME),
        (0.70, 0.70, False, Verdict.SAME),
    ],
)
def test_verdict_follows_the_sign_of_the_difference_and_the_metric_polarity(
    d7: float, d30: float, higher_is_better: bool, expected: Verdict
) -> None:
    verdict = compare_windows(
        _window(d7), _window(d30), higher_is_better=higher_is_better
    )
    assert verdict.verdict is expected
    assert verdict.higher_is_better is higher_is_better


@pytest.mark.parametrize(
    ("d7", "d30", "higher_is_better", "expected"),
    [
        (0.75, 0.70, True, Verdict.SAME),
        (0.65, 0.70, True, Verdict.SAME),
        (0.75, 0.70, False, Verdict.SAME),
        (0.65, 0.70, False, Verdict.SAME),
        (0.751, 0.70, True, Verdict.BETTER),
        (0.649, 0.70, True, Verdict.WORSE),
        (0.751, 0.70, False, Verdict.WORSE),
        (0.649, 0.70, False, Verdict.BETTER),
        (0.15, 0.10, False, Verdict.SAME),
        (0.10, 0.15, True, Verdict.SAME),
    ],
)
def test_a_difference_of_exactly_five_points_either_way_reads_as_the_same(
    d7: float, d30: float, higher_is_better: bool, expected: Verdict
) -> None:
    verdict = compare_windows(
        _window(d7), _window(d30), higher_is_better=higher_is_better
    )
    assert verdict.verdict is expected


@pytest.mark.parametrize(
    ("d7", "d30"),
    [
        (_window(0.9, sample=9), _window(0.5)),
        (_window(0.9), _window(0.5, sample=9)),
        (_window(0.9, sample=0), _window(0.5, sample=0)),
        (_window(None, sample=0), _window(0.5)),
        (_window(0.9), _window(None, sample=0)),
        (_window(None, sample=0), _window(None, sample=0)),
    ],
)
def test_a_window_under_ten_samples_or_without_a_value_has_no_verdict(
    d7: Window, d30: Window
) -> None:
    verdict = compare_windows(d7, d30, higher_is_better=True)
    assert verdict.verdict is Verdict.INSUFFICIENT
    assert verdict.higher_is_better is True


def test_exactly_ten_samples_in_both_windows_is_enough_to_compare() -> None:
    verdict = compare_windows(
        _window(0.9, sample=10), _window(0.5, sample=10), higher_is_better=True
    )
    assert verdict.verdict is Verdict.BETTER


@pytest.mark.parametrize(("covered", "expected"), [(7, Verdict.INSUFFICIENT), (8, Verdict.BETTER)])
def test_a_thirty_day_window_covering_seven_days_or_fewer_has_no_verdict(
    covered: int, expected: Verdict
) -> None:
    verdict = compare_windows(
        _window(0.9, covered=7), _window(0.5, covered=covered), higher_is_better=True
    )
    assert verdict.verdict is expected


def test_only_format_error_and_unproven_clauses_are_lower_is_better(queue: Queue) -> None:
    fleet = compute(queue).fleet
    polarity = {
        name: getattr(fleet, name).verdict.higher_is_better for name in METRIC_NAMES
    }
    assert {name for name, higher in polarity.items() if not higher} == LOWER_IS_BETTER


def test_every_metric_has_no_verdict_and_no_value_when_nothing_was_recorded(
    queue: Queue,
) -> None:
    fleet = compute(queue).fleet
    for name in METRIC_NAMES:
        metric = getattr(fleet, name)
        assert metric.verdict.verdict is Verdict.INSUFFICIENT
        assert (metric.d7.value, metric.d7.sample) == (None, 0)
        assert (metric.d30.value, metric.d30.sample) == (None, 0)


def test_computed_at_is_the_supplied_now_and_no_repositories_exist_without_workspaces(
    queue: Queue,
) -> None:
    result = compute(queue)
    assert result.computed_at == NOW
    assert dict(result.repos) == {}
    assert result.unattributed == 0


@pytest.mark.parametrize(
    ("reach_days_ago", "d7", "d30"),
    [
        (None, 7, 30),
        (60.0, 7, 30),
        (30.0, 7, 30),
        (12.5, 7, 12),
        (7.5, 7, 7),
        (7.0, 7, 7),
        (3.2, 3, 3),
        (0.0, 0, 0),
        (-1.0, 0, 0),
    ],
)
def test_covered_days_is_the_whole_days_between_the_reach_and_now_within_each_window(
    queue: Queue, reach_days_ago: float | None, d7: int, d30: int
) -> None:
    reach = None if reach_days_ago is None else ago(reach_days_ago)
    metric = compute(queue, reach=SourceReach(None, None, reach)).fleet.format_error
    assert (metric.d7.covered_days, metric.d30.covered_days) == (d7, d30)


def test_each_metric_is_bounded_by_the_reach_of_its_own_source(queue: Queue) -> None:
    reach = SourceReach(funnel=ago(10), first_pass=ago(5), corrections=ago(3))
    fleet = compute(
        queue,
        reach=reach,
        workspaces=[make_ws("old", terminal_days_ago=20), make_ws("new", terminal_days_ago=2)],
    ).fleet
    covered = {name: getattr(fleet, name).d30.covered_days for name in METRIC_NAMES}
    assert covered == {
        "outcome": 10,
        "autonomous": 10,
        "build_success": 20,
        "first_pass_approval": 5,
        "unproven_clauses": 20,
        "format_error": 3,
        "correction_recovery": 3,
        "rescue_success": 30,
        "rescue_load": 30,
    }


def test_terminal_metrics_have_no_coverage_when_no_workspace_carries_a_terminal_stamp(
    queue: Queue,
) -> None:
    fleet = compute(
        queue, workspaces=[make_ws("undated", terminal_days_ago=None)]
    ).fleet
    assert fleet.build_success.d30.covered_days == 0
    assert fleet.unproven_clauses.d7.covered_days == 0


def _improving_rows() -> list[Row]:
    return first_attempt_rows(when=ago(1), errors=0, total=12) + first_attempt_rows(
        when=ago(20), errors=12, total=12
    )


def test_a_short_reach_removes_the_verdict_even_with_plenty_of_samples(queue: Queue) -> None:
    metric = compute(
        queue, corrections=_improving_rows(), reach=SourceReach(None, None, ago(7))
    ).fleet.format_error
    assert (metric.d7.value, metric.d30.value) == (0.0, 0.5)
    assert metric.verdict.verdict is Verdict.INSUFFICIENT


def test_a_full_reach_gives_a_verdict_from_the_same_rows(queue: Queue) -> None:
    metric = compute(queue, corrections=_improving_rows()).fleet.format_error
    assert (metric.d7.value, metric.d30.value) == (0.0, 0.5)
    assert metric.verdict.verdict is Verdict.BETTER
    assert metric.verdict.higher_is_better is False


def write_oversized_audit(path: Path, *, first_days_ago: float, **fields: Any) -> None:
    lines = [
        json.dumps(
            {
                **fields,
                "seq": index,
                "pad": "x" * 60,
                "timestamp_utc": (ago(first_days_ago) + timedelta(minutes=index)).isoformat(),
            }
        )
        for index in range(4000)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def earliest_kept_stamp(path: Path) -> datetime:
    stamps: list[datetime] = []
    for line in read_audit_tail(path).splitlines():
        try:
            stamps.append(datetime.fromisoformat(json.loads(line)["timestamp_utc"]))
        except ValueError:
            continue
    return min(stamps)


def test_reading_an_empty_audit_directory_keeps_the_funnel_epoch_as_its_only_reach(
    queue: Queue, tmp_path: Path
) -> None:
    sources = read_window_sources(queue, tmp_path, [], [])
    assert sources.reach == SourceReach(funnel=FINISHER_EPOCH, first_pass=None, corrections=None)
    assert sources.trajectories == []
    assert (sources.job_failure_text, sources.merge_audit_text, sources.spiral_audit_text) == (
        "",
        "",
        "",
    )


def test_a_complete_small_audit_file_loses_nothing_so_it_bounds_no_reach(
    queue: Queue, tmp_path: Path
) -> None:
    (tmp_path / "pr_merge_audit.jsonl").write_text(
        json.dumps({"timestamp_utc": ago(2).isoformat(), "action": "merge_ok", "task_id": "a"})
        + "\n",
        encoding="utf-8",
    )
    sources = read_window_sources(queue, tmp_path, [], [])
    assert sources.reach.funnel == FINISHER_EPOCH
    assert '"task_id": "a"' in sources.merge_audit_text


def test_a_truncated_merge_audit_tail_bounds_the_funnel_reach_at_its_oldest_kept_row(
    queue: Queue, tmp_path: Path
) -> None:
    path = tmp_path / "pr_merge_audit.jsonl"
    write_oversized_audit(path, first_days_ago=60, action="merge_ok", task_id="a")
    sources = read_window_sources(queue, tmp_path, [], [])
    assert sources.reach.funnel == earliest_kept_stamp(path)
    assert sources.reach.funnel is not None
    assert sources.reach.funnel > ago(60)


def test_a_truncated_tail_that_predates_the_funnel_epoch_leaves_the_epoch_as_the_reach(
    queue: Queue, tmp_path: Path
) -> None:
    write_oversized_audit(
        tmp_path / "spiral_audit.jsonl", first_days_ago=400, task_id="a", kind="loop"
    )
    sources = read_window_sources(queue, tmp_path, [], [])
    assert sources.reach.funnel == FINISHER_EPOCH


def test_first_pass_reach_is_the_latest_oldest_kept_row_of_its_truncated_sources(
    queue: Queue, tmp_path: Path
) -> None:
    turns = tmp_path / "node_turns_audit.jsonl"
    verification = tmp_path / "verification_audit.jsonl"
    write_oversized_audit(
        turns, first_days_ago=60, task_id="a", inv_id="inv_1", team="implementation"
    )
    write_oversized_audit(verification, first_days_ago=50, task_id="a", action="review_ok")
    sources = read_window_sources(queue, tmp_path, [], [])
    assert sources.reach.first_pass == max(
        earliest_kept_stamp(turns), earliest_kept_stamp(verification)
    )
    assert sources.reach.first_pass == earliest_kept_stamp(verification)


def test_a_truncated_source_with_no_dated_rows_bounds_no_reach(
    queue: Queue, tmp_path: Path
) -> None:
    (tmp_path / "node_turns_audit.jsonl").write_text("x" * 300_000, encoding="utf-8")
    assert read_window_sources(queue, tmp_path, [], []).reach.first_pass is None


def test_corrections_reach_is_the_oldest_dated_row_when_the_audit_file_was_truncated(
    queue: Queue, tmp_path: Path
) -> None:
    write_oversized_audit(tmp_path / BLOCK_CORRECTION_AUDIT_FILE, first_days_ago=60)
    rows = [Row(ago(20), None, 1, "clean"), Row(ago(9), None, 1, "clean"), Row(None, None, 1, "x")]
    assert read_window_sources(queue, tmp_path, [], rows).reach.corrections == ago(20)


def test_corrections_reach_is_unbounded_when_no_row_is_dated(
    queue: Queue, tmp_path: Path
) -> None:
    write_oversized_audit(tmp_path / BLOCK_CORRECTION_AUDIT_FILE, first_days_ago=60)
    rows = [Row(None, None, 1, "clean")]
    assert read_window_sources(queue, tmp_path, [], rows).reach.corrections is None


def test_corrections_reach_is_unbounded_when_the_audit_file_is_complete(
    queue: Queue, tmp_path: Path
) -> None:
    (tmp_path / BLOCK_CORRECTION_AUDIT_FILE).write_text("{}\n", encoding="utf-8")
    rows = [Row(ago(20), None, 1, "clean")]
    assert read_window_sources(queue, tmp_path, [], rows).reach.corrections is None


def test_read_sources_carry_the_workspaces_the_queue_and_the_corrections_through(
    queue: Queue, tmp_path: Path
) -> None:
    workspaces = [make_ws("a")]
    rows = [Row(ago(1), None, 1, "clean")]
    sources = read_window_sources(queue, tmp_path, workspaces, rows)
    assert sources.queue is queue
    assert list(sources.workspaces) == workspaces
    assert list(sources.corrections) == rows
