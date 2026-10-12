from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import lifecycle
from alan.lifecycle import (
    JOB_TABLE,
    WORKSPACE_TABLE,
    Blocker,
    Effect,
    JobPlan,
    JobStatus,
    JobTransition,
    ParkReason,
    Status,
    stamps_finish,
    transition,
)

_MODULE = Path(lifecycle.__file__)
_PARK_CLEARING = {Status.ACTIVE, Status.COMPLETED}
_FINISHED = (Status.COMPLETED, Status.FAILED, Status.ESCALATED, Status.PARTIAL)
_UNFINISHED = tuple(s for s in Status if s not in _FINISHED)
_HELD = datetime(2026, 1, 1, tzinfo=UTC)
_ENTRIES = [
    (prev, new)
    for new in _FINISHED
    for prev in Status
    if prev is not new and prev is not Status.ARCHIVED
]
_BLOCKER = Blocker(kind="reason", name="n", detail="d")


def test_module_imports_nothing_from_alan() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert [name for name in imported if name.split(".")[0] == "alan"] == []


def test_vocabulary_is_declared_in_the_module_exports() -> None:
    assert {"Status", "JobStatus", "ParkReason", "BlockerKind", "Blocker"} <= set(
        lifecycle.__all__
    )


def test_every_exported_name_exists() -> None:
    assert all(hasattr(lifecycle, name) for name in lifecycle.__all__)


def test_vocabulary_is_shared_with_the_modules_that_used_to_define_it() -> None:
    from alan import park, queue, workspace

    assert workspace.Status is Status
    assert queue.JobStatus is JobStatus
    assert park.ParkReason is ParkReason
    assert park.Blocker is Blocker
    assert park.BlockerKind is lifecycle.BlockerKind


def test_per_task_status_vocabulary_stays_outside_the_lifecycle_module() -> None:
    assert not hasattr(lifecycle, "TaskStatus")
    assert not hasattr(lifecycle, "ProjectStatus")


def test_workspace_table_has_a_row_for_every_status() -> None:
    assert set(WORKSPACE_TABLE) == set(Status)


def test_only_active_and_completed_clear_park_state() -> None:
    clearing_reason = {s for s, row in WORKSPACE_TABLE.items() if row.clears_park_reason}
    clearing_blockers = {s for s, row in WORKSPACE_TABLE.items() if row.clears_blockers}
    assert clearing_reason == _PARK_CLEARING
    assert clearing_blockers == _PARK_CLEARING


@pytest.mark.parametrize("status", list(Status))
def test_workspace_plan_carries_status_and_only_the_rows_clears(status: Status) -> None:
    plan = transition(status)
    expected: dict[str, Any] = {"status": status}
    if status in _PARK_CLEARING:
        expected |= {"park_reason": None, "blockers": []}
    assert plan == expected


@pytest.mark.parametrize("status", list(Status))
def test_workspace_plan_never_stamps_updated_at(status: Status) -> None:
    assert "updated_at" not in transition(status, park_reason=ParkReason.CRASH)


@pytest.mark.parametrize("status", list(Status))
def test_explicit_park_state_wins_over_the_rows_clears(status: Status) -> None:
    plan = transition(status, park_reason=ParkReason.STALL, blockers=[_BLOCKER])
    assert plan["park_reason"] is ParkReason.STALL
    assert plan["blockers"] == [_BLOCKER]


def test_clear_flags_clear_park_state_on_a_non_clearing_status() -> None:
    plan = transition(Status.ESCALATED, clear_park_reason=True, clear_blockers=True)
    assert plan == {"status": Status.ESCALATED, "park_reason": None, "blockers": []}


def test_clear_flag_does_not_override_an_explicit_value() -> None:
    plan = transition(
        Status.AWAITING_INPUT,
        park_reason=ParkReason.QUESTION,
        blockers=[_BLOCKER],
        clear_park_reason=True,
        clear_blockers=True,
    )
    assert plan["park_reason"] is ParkReason.QUESTION
    assert plan["blockers"] == [_BLOCKER]


def test_merge_block_class_omitted_unless_supplied_or_cleared() -> None:
    assert "merge_block_class" not in transition(Status.ACTIVE)


def test_merge_block_class_value_is_carried() -> None:
    assert transition(Status.ACTIVE, merge_block_class="dirty")["merge_block_class"] == "dirty"


def test_clear_merge_block_class_writes_none() -> None:
    plan = transition(Status.FAILED, clear_merge_block_class=True)
    assert plan["merge_block_class"] is None


def test_merge_block_class_value_wins_over_clear() -> None:
    plan = transition(Status.FAILED, merge_block_class="dirty", clear_merge_block_class=True)
    assert plan["merge_block_class"] == "dirty"


def test_cleared_blockers_are_a_fresh_list_per_plan() -> None:
    first = transition(Status.ACTIVE)["blockers"]
    first.append(_BLOCKER)
    assert transition(Status.ACTIVE)["blockers"] == []


@pytest.mark.parametrize("held", [None, _HELD])
@pytest.mark.parametrize(("prev", "new"), _ENTRIES)
def test_stamps_finish_on_entry_into_a_finished_status_from_a_different_one(
    prev: Status, new: Status, held: datetime | None
) -> None:
    assert stamps_finish(prev, new, held) is True


@pytest.mark.parametrize("status", _FINISHED)
def test_stamps_finish_when_a_finished_status_is_rewritten_without_a_stamp(
    status: Status,
) -> None:
    assert stamps_finish(status, status, None) is True


@pytest.mark.parametrize("status", _FINISHED)
def test_stamps_finish_never_restamps_a_rewrite_of_the_same_finished_status(
    status: Status,
) -> None:
    assert stamps_finish(status, status, _HELD) is False


@pytest.mark.parametrize("held", [None, _HELD])
@pytest.mark.parametrize("prev", list(Status))
@pytest.mark.parametrize("new", _UNFINISHED)
def test_stamps_finish_never_fires_for_an_unfinished_target(
    prev: Status, new: Status, held: datetime | None
) -> None:
    assert stamps_finish(prev, new, held) is False


def test_archived_is_not_a_finished_status() -> None:
    assert Status.ARCHIVED in _UNFINISHED
    assert all(not stamps_finish(prev, Status.ARCHIVED, None) for prev in Status)


def test_job_table_has_a_row_for_every_transition() -> None:
    assert set(JOB_TABLE) == set(JobTransition)
    assert len(JOB_TABLE) == 9


_RUNNING_SOURCED = {
    JobTransition.INTERRUPT,
    JobTransition.INTERRUPT_WITH_RESUME,
    JobTransition.INTERRUPT_CLEAR_RESUME,
    JobTransition.REQUEUE_RESUME,
    JobTransition.REQUEUE_PENDING,
    JobTransition.REQUEUE_SERVER_ERROR,
    JobTransition.HOLD_UNTIL_READY,
}


@pytest.mark.parametrize("member", list(JobTransition))
def test_job_row_endpoints_and_miss_message(member: JobTransition) -> None:
    row = JOB_TABLE[member]
    if member in _RUNNING_SOURCED:
        assert row.from_status is JobStatus.RUNNING
        assert row.miss_message == "job {job_id} is not running"
    else:
        assert row.from_status is JobStatus.FAILED
        assert row.miss_message == "job {job_id} is not a failed job"
    assert row.to_status is (
        JobStatus.FAILED if member.name.startswith("INTERRUPT") else JobStatus.PENDING
    )


_COLUMN_EFFECTS = {
    JobTransition.INTERRUPT: "KEEP SET SET KEEP INCREMENT KEEP KEEP KEEP",
    JobTransition.INTERRUPT_WITH_RESUME: "KEEP SET SET SET INCREMENT KEEP KEEP KEEP",
    JobTransition.INTERRUPT_CLEAR_RESUME: "KEEP SET SET NULL INCREMENT KEEP KEEP KEEP",
    JobTransition.REQUEUE_RESUME: "NULL NULL NULL SET INCREMENT KEEP NULL SET",
    JobTransition.REQUEUE_PENDING: "NULL NULL NULL NULL INCREMENT KEEP NULL SET",
    JobTransition.REQUEUE_FAILED_FOR_RESUME: "NULL NULL NULL KEEP INCREMENT INCREMENT NULL SET",
    JobTransition.REQUEUE_FAILED_WITH_RESUME: "NULL NULL NULL SET INCREMENT INCREMENT SET SET",
    JobTransition.REQUEUE_SERVER_ERROR: "NULL NULL NULL NULL KEEP KEEP SET INCREMENT",
    JobTransition.HOLD_UNTIL_READY: "NULL KEEP KEEP KEEP KEEP KEEP SET KEEP",
}
_COLUMN_ORDER = (
    "started_at",
    "finished_at",
    "error",
    "resume_inv_id",
    "interruption_count",
    "requeue_count",
    "eligible_at",
    "server_error_retry_count",
)


@pytest.mark.parametrize("member", list(JobTransition))
def test_job_row_column_effects_match_the_transcribed_sql(member: JobTransition) -> None:
    row = JOB_TABLE[member]
    actual = " ".join(getattr(row, column).name for column in _COLUMN_ORDER)
    assert actual == _COLUMN_EFFECTS[member]


def test_job_row_declares_exactly_the_transcribed_columns() -> None:
    row_fields = {f.name for f in fields(JOB_TABLE[JobTransition.INTERRUPT])}
    assert row_fields == {"from_status", "to_status", "miss_message", *_COLUMN_ORDER}


def test_job_plan_carries_the_row_and_caller_values() -> None:
    plan = transition(
        JobTransition.REQUEUE_FAILED_WITH_RESUME,
        resume_inv_id="r",
        eligible_at=4.5,
        server_error_retry_count=2,
        error="e",
    )
    assert plan == JobPlan(
        JOB_TABLE[JobTransition.REQUEUE_FAILED_WITH_RESUME],
        error="e",
        resume_inv_id="r",
        eligible_at=4.5,
        server_error_retry_count=2,
    )


def test_job_plan_defaults_are_empty() -> None:
    plan = transition(JobTransition.INTERRUPT)
    assert (plan.error, plan.resume_inv_id, plan.eligible_at) == (None, None, None)
    assert plan.server_error_retry_count == 0


def test_rows_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        JOB_TABLE[JobTransition.INTERRUPT].error = Effect.NULL  # type: ignore[misc]


def test_effect_vocabulary_is_four_members() -> None:
    assert {e.name for e in Effect} == {"KEEP", "NULL", "SET", "INCREMENT"}
