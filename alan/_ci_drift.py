from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

WINDOW = 7
MIN_SAMPLES = 4
SLOW_RATIO = 1.10
SLACK_RATIO = 0.80
SLACK_MIN_S = 20
BUCKET_PCT = 5
LABEL_MAX = 50
DIGEST_CHARS = 6

_PR_REFERENCE = re.compile(r"\(#(\d+)\)")


class Direction(StrEnum):
    SLOW = "slow"
    SLACK = "slack"


_LABEL_PREFIX = {Direction.SLOW: "ci-slow:", Direction.SLACK: "ci-budget-slack:"}


@dataclass(frozen=True)
class RunSample:
    run_id: int
    date: str
    pr: int | None
    durations: Mapping[str, float]


@dataclass(frozen=True)
class DriftFinding:
    workflow: str
    job: str
    direction: Direction
    budget_s: float
    after_s: float
    before_s: float | None
    samples: int
    window: tuple[RunSample, ...]
    proposed_s: int


def pr_number(commit_message: str) -> int | None:
    subject = commit_message.split("\n", 1)[0]
    matches = _PR_REFERENCE.findall(subject)
    return int(matches[-1]) if matches else None


def proposed_budget_s(median_s: float) -> int:
    return max(1, round(median_s))


def crossing_index(values: Sequence[float], crossed: Callable[[float], bool]) -> int | None:
    for index in range(MIN_SAMPLES - 1, len(values)):
        if crossed(statistics.median(values[max(0, index - WINDOW + 1) : index + 1])):
            return index
    return None


def jump_window(runs: Sequence[RunSample], index: int | None) -> tuple[RunSample, ...]:
    if index is None:
        return ()
    return tuple(runs[max(0, index - WINDOW // 2) : index + 1])


def _direction(after_s: float, budget_s: float) -> Direction | None:
    if after_s > SLOW_RATIO * budget_s:
        return Direction.SLOW
    if after_s < SLACK_RATIO * budget_s and budget_s - after_s >= SLACK_MIN_S:
        return Direction.SLACK
    return None


def _crossed(direction: Direction, budget_s: float) -> Callable[[float], bool]:
    if direction is Direction.SLOW:
        return lambda median: median > SLOW_RATIO * budget_s
    return lambda median: median < SLACK_RATIO * budget_s


def _finding(
    workflow: str, job: str, budget_s: float, runs: Sequence[RunSample]
) -> DriftFinding | None:
    values = [run.durations[job] for run in runs]
    if len(values) < MIN_SAMPLES:
        return None
    recent = values[-WINDOW:]
    after_s = statistics.median(recent)
    direction = _direction(after_s, budget_s)
    if direction is None:
        return None
    prior = values[-2 * WINDOW : -WINDOW]
    before_s = statistics.median(prior) if len(prior) >= MIN_SAMPLES else None
    window = jump_window(runs, crossing_index(values, _crossed(direction, budget_s)))
    return DriftFinding(
        workflow,
        job,
        direction,
        budget_s,
        after_s,
        before_s,
        len(recent),
        window,
        proposed_budget_s(after_s),
    )


def drift_findings(
    workflow: str, samples: Sequence[RunSample], budgets: Mapping[str, float]
) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    for job, budget_s in sorted(budgets.items()):
        runs = [run for run in samples if job in run.durations]
        finding = _finding(workflow, job, budget_s, runs)
        if finding is not None:
            findings.append(finding)
    return findings


def finding_label(workflow: str, job: str, direction: Direction) -> str:
    prefix = _LABEL_PREFIX[direction]
    digest = hashlib.sha256(f"{workflow}/{job}".encode()).hexdigest()[:DIGEST_CHARS]
    room = LABEL_MAX - len(prefix) - len(digest) - 1
    slug = re.sub(r"[^a-z0-9]+", "-", job.lower()).strip("-")[:room].rstrip("-")
    return f"{prefix}{slug}-{digest}"


def dedup_marker(direction: Direction, budget_s: float, median_s: float) -> str:
    bucket = int(100 * median_s / budget_s) // BUCKET_PCT * BUCKET_PCT
    return f"{direction}:{round(budget_s)}:{bucket}"
