from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

WARN_RATIO = 0.10
WARN_MIN_S = 10
FAIL_RATIO = 0.50
FAIL_MIN_S = 20
CONTENTION_MIN_BUDGET_S = 60
CONTENTION_MIN_PEERS = 2
CONTENTION_MEDIAN_RATIO = 1.20
TIMEOUT_MARGIN = 0.95
FAIL_BLOCKS_PR = False

CHECK_JOB_NAME = "Duration budgets (advisory)"
REPORTING_JOB_NAMES = frozenset({"SonarCloud (advisory)", "Coverage PR comment"})
PER_PR_EXCLUDED_JOBS = frozenset({CHECK_JOB_NAME}) | REPORTING_JOB_NAMES
PR_ONLY_STEPS = frozenset({"Stress changed specs (repeat-each 5, no retries)"})
MEASURED_CONCLUSIONS = frozenset({"success", "failure"})

Budgets = dict[str, dict[str, float]]
Slowest = dict[str, tuple[tuple[str, float], ...]]


class BudgetFileError(Exception):
    pass


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    CONTENTION = "contention"
    UNBUDGETED = "unbudgeted"
    NOT_MEASURED = "not-measured"


@dataclass(frozen=True)
class StepTiming:
    name: str
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class JobTiming:
    name: str
    conclusion: str | None
    started_at: str | None
    completed_at: str | None
    steps: tuple[StepTiming, ...] = ()


@dataclass(frozen=True)
class Verdict:
    name: str
    status: Status
    duration_s: float = 0.0
    budget_s: float = 0.0
    peer_ratio: float | None = None
    note: str = ""

    @property
    def over_s(self) -> float:
        return self.duration_s - self.budget_s

    @property
    def over_pct(self) -> float:
        if self.budget_s <= 0:
            return 0.0
        return 100.0 * self.over_s / self.budget_s


@dataclass(frozen=True)
class Report:
    verdicts: tuple[Verdict, ...] = ()
    notices: tuple[str, ...] = ()
    slowest: Slowest = field(default_factory=Slowest)
    workflow: str = "tests.yml"


def _seconds(workflow: str, job: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BudgetFileError(f"{workflow}: budget for {job!r} is not a number")
    if not math.isfinite(value) or value <= 0:
        raise BudgetFileError(f"{workflow}: budget for {job!r} must be a positive number")
    return float(value)


def _job_budgets(workflow: str, jobs: object) -> dict[str, float]:
    if not isinstance(jobs, dict):
        raise BudgetFileError(f"{workflow}: expected a mapping of job name to seconds")
    return {
        str(job): _seconds(workflow, str(job), value)
        for job, value in cast("dict[object, object]", jobs).items()
    }


def workspace_path(value: str) -> Path:
    root = Path.cwd().resolve()
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{value!r} resolves outside the workspace {root}")
    return resolved


def load_budgets(path: Path) -> Budgets:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BudgetFileError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BudgetFileError(f"{path}: expected a mapping of workflow file to jobs")
    return {
        str(workflow): _job_budgets(str(workflow), jobs)
        for workflow, jobs in cast("dict[object, object]", raw).items()
    }


def fail_point_s(budget_s: float) -> float:
    return budget_s + max(FAIL_RATIO * budget_s, FAIL_MIN_S)


def warn_point_s(budget_s: float) -> float:
    return budget_s + max(WARN_RATIO * budget_s, WARN_MIN_S)


def tier(duration_s: float, budget_s: float) -> Status:
    over = duration_s - budget_s
    if over > FAIL_RATIO * budget_s and over >= FAIL_MIN_S:
        return Status.FAIL
    if over > WARN_RATIO * budget_s and over >= WARN_MIN_S:
        return Status.WARN
    return Status.OK


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def span_s(started_at: str | None, completed_at: str | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    try:
        return (parse_ts(completed_at) - parse_ts(started_at)).total_seconds()
    except ValueError:
        return None


def measured_duration_s(job: JobTiming) -> float | None:
    if job.conclusion not in MEASURED_CONCLUSIONS:
        return None
    total = span_s(job.started_at, job.completed_at)
    if total is None:
        return None
    excluded = sum(
        span_s(step.started_at, step.completed_at) or 0.0
        for step in job.steps
        if step.name in PR_ONLY_STEPS
    )
    return max(total - excluded, 0.0)


def peer_median_ratio(
    name: str, durations: Mapping[str, float], budgets: Mapping[str, float]
) -> float | None:
    ratios = [
        duration / budgets[peer]
        for peer, duration in durations.items()
        if peer != name and budgets.get(peer, 0.0) >= CONTENTION_MIN_BUDGET_S
    ]
    if len(ratios) < CONTENTION_MIN_PEERS:
        return None
    return statistics.median(ratios)


def _judge(
    name: str, duration: float, durations: Mapping[str, float], budgets: Mapping[str, float]
) -> Verdict:
    budget = budgets.get(name)
    if budget is None:
        return Verdict(name, Status.UNBUDGETED, duration_s=duration)
    status = tier(duration, budget)
    if status is not Status.FAIL:
        return Verdict(name, status, duration, budget)
    ratio = peer_median_ratio(name, durations, budgets)
    if ratio is not None and ratio >= CONTENTION_MEDIAN_RATIO:
        return Verdict(name, Status.CONTENTION, duration, budget, ratio)
    return Verdict(name, Status.FAIL, duration, budget, ratio)


def _durations(jobs: Sequence[JobTiming]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for job in jobs:
        duration = measured_duration_s(job)
        if duration is not None:
            durations[job.name] = duration
    return durations


def classify(jobs: Sequence[JobTiming], budgets: Mapping[str, float]) -> list[Verdict]:
    relevant = [job for job in jobs if job.name not in PER_PR_EXCLUDED_JOBS]
    durations = _durations(relevant)
    return [
        _judge(job.name, durations[job.name], durations, budgets)
        if job.name in durations
        else Verdict(job.name, Status.NOT_MEASURED, note=job.conclusion or "in progress")
        for job in relevant
    ]


def has_failure(verdicts: Sequence[Verdict]) -> bool:
    return any(verdict.status is Status.FAIL for verdict in verdicts)
