from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.dispatch import owning_task_id
from alan.errors import AppError
from alan.handover import Handover, OpenQuestion, save_handover
from alan.workspace import Task, TaskStatus, create


def _handover(
    task_id: str,
    handover_id: str,
    *,
    blocking_answered: bool | None = None,
) -> Handover:
    open_questions: list[OpenQuestion] = []
    if blocking_answered is not None:
        open_questions = [
            OpenQuestion(
                id=f"{handover_id}-q",
                question="which?",
                proposed_answer_space=["a", "b"],
                resolver_role="human",
                blocking=True,
                answer="a" if blocking_answered else None,
            )
        ]
    return Handover(
        handover_id=handover_id,
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        open_questions=open_questions,
    )


def test_owning_task_id_empty_tasks_passes_through(ws_root: Path) -> None:
    ws = create("Undecomposed", "d", root=ws_root)
    assert owning_task_id(ws, ws.task_id) == ws.task_id


def test_owning_task_id_already_a_member_passes_through(ws_root: Path) -> None:
    ws = create("Member", "d", root=ws_root, tasks=[Task(id="sub-a", status=TaskStatus.PENDING)])
    assert owning_task_id(ws, "sub-a") == "sub-a"


def test_owning_task_id_neither_root_nor_member_passes_through_without_raising(
    ws_root: Path,
) -> None:
    ws = create("No Tasks Bogus Id", "d", root=ws_root)
    assert owning_task_id(ws, "totally-bogus-id") == "totally-bogus-id"


def test_owning_task_id_decomposed_bogus_id_passes_through_without_resolving(
    ws_root: Path,
) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1"),
        Task(id="sub-b", status=TaskStatus.PENDING),
    ]
    ws = create("Decomposed Bogus Id", "d", root=ws_root, tasks=tasks)
    blocking = _handover(ws.task_id, "h1")

    assert owning_task_id(ws, "totally-bogus-id", blocking=blocking) == "totally-bogus-id"


def test_owning_task_id_single_blocking_matched_subtask_resolves(ws_root: Path) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1"),
        Task(id="sub-b", status=TaskStatus.PENDING),
    ]
    ws = create("Single Match", "d", root=ws_root, tasks=tasks)
    blocking = _handover(ws.task_id, "h1")

    assert owning_task_id(ws, ws.task_id, blocking=blocking) == "sub-a"


def test_owning_task_id_two_subtasks_distinct_ids_resolves_true_match(ws_root: Path) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1"),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h2"),
    ]
    ws = create("Two Distinct", "d", root=ws_root, tasks=tasks)
    blocking = _handover(ws.task_id, "h2")

    assert owning_task_id(ws, ws.task_id, blocking=blocking) == "sub-b"


def test_owning_task_id_zero_matches_raises_404_with_pinned_detail(ws_root: Path) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1")]
    ws = create("Zero Matches", "d", root=ws_root, tasks=tasks)
    blocking = _handover(ws.task_id, "h9")

    with pytest.raises(AppError) as exc_info:
        owning_task_id(ws, ws.task_id, blocking=blocking)
    assert exc_info.value.status_code == 404
    assert str(exc_info.value.detail).count(ws.task_id) == 1


def test_owning_task_id_no_blocking_resolves_the_unanswered_candidate(ws_root: Path) -> None:
    tasks = [
        Task(id="sub-answered", status=TaskStatus.PENDING, blocked_handover_id="h-answered"),
        Task(id="sub-unanswered", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-open"),
    ]
    ws = create("Backward Resolve", "d", root=ws_root, tasks=tasks)
    save_handover(ws, _handover(ws.task_id, "h-answered", blocking_answered=True))
    save_handover(ws, _handover(ws.task_id, "h-open", blocking_answered=False))

    assert owning_task_id(ws, ws.task_id) == "sub-unanswered"


def test_owning_task_id_no_blocking_absent_handover_yields_no_candidate(ws_root: Path) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="nonexistent")
    ]
    ws = create("Absent Handover", "d", root=ws_root, tasks=tasks)

    with pytest.raises(AppError) as exc_info:
        owning_task_id(ws, ws.task_id)
    assert exc_info.value.status_code == 404


def test_owning_task_id_no_blocking_two_unanswered_candidates_raises_404(ws_root: Path) -> None:
    tasks = [
        Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-a"),
        Task(id="sub-b", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-b"),
    ]
    ws = create("Two Unanswered", "d", root=ws_root, tasks=tasks)
    save_handover(ws, _handover(ws.task_id, "h-a", blocking_answered=False))
    save_handover(ws, _handover(ws.task_id, "h-b", blocking_answered=False))

    with pytest.raises(AppError) as exc_info:
        owning_task_id(ws, ws.task_id)
    assert exc_info.value.status_code == 404
    assert "blocking question" not in str(exc_info.value.detail)


def test_owning_task_id_zero_matches_with_blocking_names_the_question(ws_root: Path) -> None:
    tasks = [Task(id="sub-a", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h1")]
    ws = create("Zero Matches Named", "d", root=ws_root, tasks=tasks)
    blocking = _handover(ws.task_id, "h9")

    with pytest.raises(AppError) as exc_info:
        owning_task_id(ws, ws.task_id, blocking=blocking)
    assert exc_info.value.status_code == 404
    assert "blocking question" in str(exc_info.value.detail)
