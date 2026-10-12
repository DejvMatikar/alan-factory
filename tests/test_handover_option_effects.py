from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alan import handover as handover_mod
from alan.handover import (
    PENDING_EXTERNAL_NOTICE,
    Handover,
    OpenQuestion,
    load_handover,
    record_answer,
    save_handover,
)
from alan.workspace import create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _blocking_handover(task_id: str, *, question: OpenQuestion, **overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "escalation",
        "task_id": task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "the situation that prompted the question",
        "open_questions": [question.model_dump()],
    }
    base.update(overrides)
    return Handover.model_validate(base)


def test_option_effects_defaults_empty() -> None:
    q = OpenQuestion(
        id="q1", question="which?", proposed_answer_space=["a", "b"],
        resolver_role="human", blocking=True,
    )
    assert q.option_effects == {}


def test_option_effects_round_trips(ws_root: Path) -> None:
    ws = create("option-effects-rt", "d", root=ws_root)
    h = _blocking_handover(
        ws.task_id,
        question=OpenQuestion(
            id="q1",
            question="do it yourself or waive?",
            proposed_answer_space=["do it yourself", "waive"],
            resolver_role="human",
            blocking=True,
            option_effects={"do it yourself": "pending_external", "waive": "closes"},
        ),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    [q] = loaded.open_questions
    assert q.option_effects == {"do it yourself": "pending_external", "waive": "closes"}


def test_option_effects_rejects_key_outside_answer_space() -> None:
    with pytest.raises(ValidationError, match="option_effects keys must be members"):
        OpenQuestion(
            id="q1",
            question="which?",
            proposed_answer_space=["a", "b"],
            resolver_role="human",
            blocking=True,
            option_effects={"c": "pending_external"},
        )


def test_option_effects_rejects_unknown_effect_value() -> None:
    with pytest.raises(ValidationError):
        OpenQuestion.model_validate({
            "id": "q1",
            "question": "which?",
            "proposed_answer_space": ["a", "b"],
            "resolver_role": "human",
            "blocking": True,
            "option_effects": {"a": "not_a_real_effect"},
        })


def test_v121_payload_without_option_effects_still_parses(ws_root: Path) -> None:
    ws = create("v121-option-effects-compat", "d", root=ws_root)
    payload = {
        "schema_version": "1.21",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "escalation",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.21 payload predating option_effects",
        "open_questions": [
            {
                "id": "q1",
                "question": "waive or resolve by hand?",
                "proposed_answer_space": ["resolve by hand", "waive"],
                "resolver_role": "human",
                "blocking": True,
                "prior_attempts": [],
            },
        ],
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.21"
    [q] = loaded.open_questions
    assert q.option_effects == {}


def test_record_answer_prefix_is_pending_external_notice_verbatim(ws_root: Path) -> None:
    ws = create("record-answer-pending-external", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="resolve outside the fleet or waive?",
        proposed_answer_space=["resolve outside the fleet", "waive"],
        resolver_role="human",
        blocking=True,
        option_effects={"resolve outside the fleet": "pending_external", "waive": "closes"},
    )
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(
        ws, blocking, "q1", "resolve outside the fleet", from_team=blocking.from_team
    )

    assert recorded.full_synthesis == (
        f"{PENDING_EXTERNAL_NOTICE}\n\n(question 'q1', answer: 'resolve outside the fleet')"
        "\n\nthe situation that prompted the question"
    )
    assert "the situation that prompted the question" in recorded.full_synthesis


def test_record_answer_prefix_unchanged_for_closes(ws_root: Path) -> None:
    ws = create("record-answer-closes", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="resolve outside the fleet or waive?",
        proposed_answer_space=["resolve outside the fleet", "waive"],
        resolver_role="human",
        blocking=True,
        option_effects={"resolve outside the fleet": "pending_external", "waive": "closes"},
    )
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, "q1", "waive", from_team=blocking.from_team)

    assert recorded.full_synthesis == (
        "Recorded answer for question 'q1': waive\n\n"
        "the situation that prompted the question"
    )


def test_option_effect_for_returns_none_when_question_id_not_found() -> None:
    assert handover_mod._option_effect_for([], "missing", "x") is None  # noqa: SLF001


def test_record_answer_effect_override_persists_onto_recorded_question(ws_root: Path) -> None:
    ws = create("record-answer-effect-override", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="withdraw or waive?",
        proposed_answer_space=["withdraw", "waive"],
        resolver_role="human",
        blocking=True,
        option_effects={"withdraw": "pending_external", "waive": "closes"},
    )
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(
        ws, blocking, "q1", "withdraw", from_team=blocking.from_team, effect_override="closes"
    )

    [recorded_question] = recorded.open_questions
    assert recorded_question.option_effects["withdraw"] == "closes"
    assert recorded.full_synthesis == (
        "Recorded answer for question 'q1': withdraw\n\n"
        "the situation that prompted the question"
    )


def test_record_answer_prefix_unchanged_for_unclassified_option(ws_root: Path) -> None:
    ws = create("record-answer-unclassified", "d", root=ws_root)
    q = OpenQuestion(
        id="q1", question="which db?", proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="human", blocking=True,
    )
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, "q1", "sqlite", from_team=blocking.from_team)

    assert recorded.full_synthesis == (
        "Recorded answer for question 'q1': sqlite\n\n"
        "the situation that prompted the question"
    )
