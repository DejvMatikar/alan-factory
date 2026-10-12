from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.compression import load_cross_team_prior_outputs
from alan.handover import (
    PENDING_EXTERNAL_NOTICE,
    Handover,
    OpenQuestion,
    record_answer,
    save_handover,
)
from alan.teams._base import build_prior_context_user_text
from alan.workspace import create

_DECISION_DIRECTIVE = "The human has already decided this — do not re-ask or re-litigate it."

_VERIFICATION_OPTION = "Complete the out-of-fleet step yourself, then re-dispatch verification."
_WAIVE_OPTION = "Waive this finding and proceed to merge."
_UNRESOLVABLE_FINDING = (
    "Verifier declared this finding unresolvable by any fleet role: needs vault access."
)


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
        "full_synthesis": _UNRESOLVABLE_FINDING,
        "open_questions": [question.model_dump()],
    }
    base.update(overrides)
    return Handover.model_validate(base)


def _capability_boundary_question() -> OpenQuestion:
    return OpenQuestion(
        id="verif-capability-boundary-inv_z",
        question=_UNRESOLVABLE_FINDING,
        proposed_answer_space=[_VERIFICATION_OPTION, _WAIVE_OPTION],
        recommended=_VERIFICATION_OPTION,
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={_VERIFICATION_OPTION: "verification", _WAIVE_OPTION: "pr_merge"},
        option_effects={_VERIFICATION_OPTION: "pending_external", _WAIVE_OPTION: "closes"},
    )


def test_pending_external_answer_text_asserts_check_not_accept(ws_root: Path) -> None:
    ws = create("pending-ext-prior-context", "d", root=ws_root)
    q = _capability_boundary_question()
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, q.id, _VERIFICATION_OPTION, from_team=blocking.from_team)

    text = build_prior_context_user_text("verification", ws, recorded)

    for fragment in (
        "A human undertook to perform this step outside the fleet",
        "It has not been observed to have landed",
        "Check whether it has",
        "this remains blocked on that human",
        "not accepted work",
        "not to be re-derived as a fresh discovery",
    ):
        assert fragment in text

    assert "Recorded answer for question" not in text
    assert _DECISION_DIRECTIVE not in text


def test_pending_external_answer_text_preserves_original_finding(ws_root: Path) -> None:
    ws = create("pending-ext-preserves-finding", "d", root=ws_root)
    q = _capability_boundary_question()
    blocking = _blocking_handover(ws.task_id, question=q)
    save_handover(ws, blocking)

    recorded = record_answer(ws, blocking, q.id, _VERIFICATION_OPTION, from_team=blocking.from_team)

    text = build_prior_context_user_text("verification", ws, recorded)

    assert _UNRESOLVABLE_FINDING in text


def test_pending_external_answer_notice_survives_question_bullet_rendering(ws_root: Path) -> None:
    ws = create("pending-ext-cross-team", "d", root=ws_root)
    q = _capability_boundary_question()
    blocking = _blocking_handover(
        ws.task_id, question=q, from_team="verification", to_team_suggestions=["verification"],
    )
    save_handover(ws, blocking)
    record_answer(ws, blocking, q.id, _VERIFICATION_OPTION, from_team=blocking.from_team)

    out = load_cross_team_prior_outputs(ws, "verification")

    assert PENDING_EXTERNAL_NOTICE in out
    assert "[ANSWERED:" not in out
