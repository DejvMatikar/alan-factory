from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alan.handover import Handover, OpenQuestion, is_answer_recording


def _handover(handover_type: str, open_questions: list[OpenQuestion]) -> Handover:
    return Handover(
        handover_id="handover_t1_001",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="t1",
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="body",
        open_questions=open_questions,
    )


def _question(answer: str | None) -> OpenQuestion:
    return OpenQuestion(
        id="q1", question="q", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True, answer=answer,
    )


@pytest.mark.parametrize(
    ("handover_type", "answer", "expected"),
    [
        ("revisit_amend", "a", True),
        ("revisit_amend", None, False),
        ("forward", "a", False),
        ("escalation", "a", False),
    ],
)
def test_is_answer_recording(handover_type: str, answer: str | None, expected: bool) -> None:
    handover = _handover(handover_type, [_question(answer)])
    assert is_answer_recording(handover) is expected


def test_is_answer_recording_false_with_no_open_questions() -> None:
    handover = _handover("revisit_amend", [])
    assert is_answer_recording(handover) is False


def test_is_answer_recording_true_when_any_of_several_questions_is_answered() -> None:
    handover = _handover("revisit_amend", [_question(None), _question("a")])
    assert is_answer_recording(handover) is True
