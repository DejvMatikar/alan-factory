from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.clause_withdrawal import CLAUSE_STALL_QUESTION_PREFIX
from alan.handover import Handover, OpenQuestion, save_handover
from alan.teams import _clause_stall
from alan.teams._verification_review_post import (
    _clause_stall_question_id,
    _stall_question_id_for,
)
from alan.workspace import Workspace, create

_CHAIN = "handover_stall-ids-task_root"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return create("stall-ids-task", "d", root=tmp_path / "ws")


def _stall(clause_ids: tuple[str, ...], repeated_ids: tuple[str, ...]) -> _clause_stall.ClauseStall:
    return _clause_stall.ClauseStall(
        clause_ids=clause_ids,
        repeated_ids=repeated_ids,
        clauses=tuple(
            _clause_stall.StalledClause(req_id=rid, text="t", evidence="e") for rid in clause_ids
        ),
        judge_rationale="rationale",
        prior_handover_id="handover_stall-ids-task_000",
    )


def _prior(ws: Workspace, seq: int, *, from_invocation_id: str) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_{seq:03d}",
        handover_chain_id=_CHAIN,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id=from_invocation_id,
        iteration=seq,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="body",
    )


def _answered_amend(ws: Workspace, *, seq: int, question_id: str) -> Handover:
    question = OpenQuestion(
        id=question_id,
        question="stalled",
        proposed_answer_space=["withdraw", "waive"],
        resolver_role="human",
        blocking=True,
        answer="withdraw",
    )
    return Handover(
        handover_id=f"handover_{ws.task_id}_{seq:03d}",
        handover_chain_id=_CHAIN,
        handover_type="revisit_amend",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id=f"inv_amend_{seq:03d}",
        iteration=seq,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="recorded ruling",
        open_questions=[question],
    )


def test_returns_overlap_id_when_prior_is_none(ws: Workspace) -> None:
    stall = _stall(("req-a",), ("req-a",))
    assert _stall_question_id_for(ws, None, stall) == "clause-stall-req-a"


def test_returns_overlap_id_when_unanswered(ws: Workspace) -> None:
    stall = _stall(("req-a",), ("req-a",))
    prior = _prior(ws, 1, from_invocation_id="inv_001")
    assert _stall_question_id_for(ws, prior, stall) == "clause-stall-req-a"


def test_falls_back_to_clause_id_when_overlap_already_answered(ws: Workspace) -> None:
    save_handover(ws, _answered_amend(ws, seq=1, question_id="clause-stall-req-a"))
    stall = _stall(("req-a", "req-b"), ("req-a",))
    prior = _prior(ws, 2, from_invocation_id="inv_002")
    expected = _clause_stall_question_id(("req-a", "req-b"))
    assert _stall_question_id_for(ws, prior, stall) == expected


def test_reuses_the_answered_id_when_the_scope_is_unchanged(ws: Workspace) -> None:
    save_handover(ws, _answered_amend(ws, seq=1, question_id="clause-stall-req-a"))
    stall = _stall(("req-a",), ("req-a",))
    prior = _prior(ws, 2, from_invocation_id="inv_002")
    assert _stall_question_id_for(ws, prior, stall) == "clause-stall-req-a"


def test_falls_back_to_a_fresh_id_when_both_distinct_forms_already_answered(
    ws: Workspace,
) -> None:
    clause_id = _clause_stall_question_id(("req-a", "req-b"))
    save_handover(ws, _answered_amend(ws, seq=1, question_id="clause-stall-req-a"))
    save_handover(ws, _answered_amend(ws, seq=2, question_id=clause_id))
    stall = _stall(("req-a", "req-b"), ("req-a",))
    prior = _prior(ws, 3, from_invocation_id="inv_fresh_1")
    result = _stall_question_id_for(ws, prior, stall)
    assert result.startswith(CLAUSE_STALL_QUESTION_PREFIX)
    assert result not in ("clause-stall-req-a", clause_id)
    assert result.endswith("-inv_fres")


def test_fresh_fallback_ids_differ_across_invocations(ws: Workspace) -> None:
    clause_id = _clause_stall_question_id(("req-a", "req-b"))
    save_handover(ws, _answered_amend(ws, seq=1, question_id="clause-stall-req-a"))
    save_handover(ws, _answered_amend(ws, seq=2, question_id=clause_id))
    stall = _stall(("req-a", "req-b"), ("req-a",))
    first = _stall_question_id_for(ws, _prior(ws, 3, from_invocation_id="inv_aaaaaaaa"), stall)
    second = _stall_question_id_for(ws, _prior(ws, 4, from_invocation_id="inv_bbbbbbbb"), stall)
    assert first != second


def test_fresh_fallback_is_deterministic_for_the_same_invocation(ws: Workspace) -> None:
    clause_id = _clause_stall_question_id(("req-a", "req-b"))
    save_handover(ws, _answered_amend(ws, seq=1, question_id="clause-stall-req-a"))
    save_handover(ws, _answered_amend(ws, seq=2, question_id=clause_id))
    stall = _stall(("req-a", "req-b"), ("req-a",))
    prior = _prior(ws, 3, from_invocation_id="inv_same0001")
    first = _stall_question_id_for(ws, prior, stall)
    second = _stall_question_id_for(ws, prior, stall)
    assert first == second
