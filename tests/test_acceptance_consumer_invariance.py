from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import acceptance, health_repo_metrics, workspace_dag
from alan.workspace import Meta, Requirement, Status, Task, TaskDoDClause, TaskStatus, Workspace


def _task(covers: list[str] | None = None) -> Task:
    return Task(
        id="t1",
        status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="deliver", covers=covers or [], evidence="PR #1")],
    )


@dataclass(frozen=True)
class _Case:
    name: str
    requirements: list[Requirement] | None
    tasks: list[Task]
    expected_unproven_count: int
    expected_status: Status
    expected_build_success: bool


def _fully_unproven() -> _Case:
    clause = Requirement(text="unbound and uncovered")
    return _Case(
        name="fully_unproven",
        requirements=[clause],
        tasks=[_task()],
        expected_unproven_count=1,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _partial() -> _Case:
    proven = Requirement(text="bound", test_ref="t1", status="PASS")
    unproven = Requirement(text="unbound and uncovered")
    return _Case(
        name="partial",
        requirements=[proven, unproven],
        tasks=[_task()],
        expected_unproven_count=1,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _fully_covered() -> _Case:
    clause = Requirement(text="covered")
    return _Case(
        name="fully_covered",
        requirements=[clause],
        tasks=[_task(covers=[clause.id])],
        expected_unproven_count=0,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _all_bound() -> _Case:
    clause = Requirement(text="bound", test_ref="t1", status="PASS")
    return _Case(
        name="all_bound",
        requirements=[clause],
        tasks=[_task()],
        expected_unproven_count=0,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _none_requirements() -> _Case:
    return _Case(
        name="none",
        requirements=None,
        tasks=[_task()],
        expected_unproven_count=0,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _empty_requirements() -> _Case:
    return _Case(
        name="empty",
        requirements=[],
        tasks=[_task()],
        expected_unproven_count=0,
        expected_status=Status.FAILED,
        expected_build_success=False,
    )


def _fully_withdrawn() -> _Case:
    clause = Requirement(text="withdrawn and uncovered", withdrawn_by_question_id="clause-stall-x")
    return _Case(
        name="fully_withdrawn",
        requirements=[clause],
        tasks=[_task()],
        expected_unproven_count=0,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


def _withdrawn_failing_test_bound() -> _Case:
    clause = Requirement(
        text="withdrawn but failing", test_ref="t1", status="FAIL",
        withdrawn_by_question_id="clause-stall-x",
    )
    return _Case(
        name="withdrawn_failing_test_bound",
        requirements=[clause],
        tasks=[_task()],
        expected_unproven_count=0,
        expected_status=Status.COMPLETED,
        expected_build_success=True,
    )


CASES: list[_Case] = [
    _fully_unproven(),
    _partial(),
    _fully_covered(),
    _all_bound(),
    _none_requirements(),
    _empty_requirements(),
    _fully_withdrawn(),
    _withdrawn_failing_test_bound(),
]


def _meta_for(case: _Case) -> Meta:
    now = datetime.now(UTC)
    return Meta(
        task_id="ws-1",
        title="T",
        description="d",
        status=Status.ACTIVE,
        requirements=case.requirements,
        tasks=case.tasks,
        created_at=now,
        updated_at=now,
    )


def _ws_for(meta: Meta) -> Workspace:
    return Workspace(task_id=meta.task_id, path=Path("unused"), meta=meta)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_unproven_count_matches_expected_per_fixture(case: _Case) -> None:
    unproven = acceptance.unproven_clause_ids(case.requirements, case.tasks)
    assert len(unproven) == case.expected_unproven_count


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_workspace_terminal_status_decision_matches_pre_existing_behavior(
    case: _Case,
) -> None:
    ws = _ws_for(_meta_for(case))
    assert workspace_dag.workspace_terminal_status(ws) == case.expected_status


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_is_build_success_decision_matches_pre_existing_behavior(case: _Case) -> None:
    meta = _meta_for(case)
    assert health_repo_metrics.is_build_success(meta) == case.expected_build_success


def test_unproven_count_varies_while_completed_decision_does_not() -> None:
    completed_cases = [c for c in CASES if c.expected_status is Status.COMPLETED]
    counts = {c.expected_unproven_count for c in completed_cases}
    assert len(counts) > 1
    assert all(c.expected_build_success is True for c in completed_cases)


def test_equal_unproven_count_does_not_force_equal_decision() -> None:
    zero_count_cases = [c for c in CASES if c.expected_unproven_count == 0]
    statuses = {c.expected_status for c in zero_count_cases}
    assert len(statuses) > 1
