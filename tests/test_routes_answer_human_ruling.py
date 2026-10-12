from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import clause_withdrawal
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import ClauseOutcome, Handover, OpenQuestion
from alan.routes import answer as answer_mod
from alan.teams import _pr_state_paths, _verification_capability
from alan.workspace import Requirement, Workspace, create

_CAPABILITY_WAIVE_OPTION = "Waive this finding and proceed to merge."
_CAPABILITY_RESOLVE_OPTION = (
    "Resolve this outside the fix-worker loop, then re-dispatch verification."
)
_WITHDRAW = clause_withdrawal.OPTION_WITHDRAW_WORDING_CURRENT
_WAIVE = clause_withdrawal.OPTION_WAIVE_WORDING


@pytest.fixture(autouse=True)
def _isolated_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _capability_question() -> OpenQuestion:
    return OpenQuestion(
        id="verif-capability-boundary-abc12345",
        question="unresolvable",
        proposed_answer_space=[_CAPABILITY_RESOLVE_OPTION, _CAPABILITY_WAIVE_OPTION],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={
            _CAPABILITY_RESOLVE_OPTION: "verification", _CAPABILITY_WAIVE_OPTION: "pr_merge",
        },
        option_effects={_CAPABILITY_WAIVE_OPTION: "closes"},
    )


def _clause_stall_question() -> OpenQuestion:
    return OpenQuestion(
        id="clause-stall-req-a",
        question="stalled",
        proposed_answer_space=[_WITHDRAW, _WAIVE],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={_WITHDRAW: "verification", _WAIVE: "pr_merge"},
    )


def _blocking(
    ws: Workspace, *, pr_number: int | None, clause_outcome: ClauseOutcome | None
) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_esc",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        pr_number=pr_number,
        clause_outcome=clause_outcome,
    )


def _clause_outcome(ws: Workspace, req_id: str) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(req_id,),
        clause_texts={req_id: "text"},
        evidence={req_id: "evidence"},
        judge_rationale="rationale",
    )


def _waiver_key(question: OpenQuestion) -> str:
    return _verification_capability.capability_boundary_waiver_key_for_question(question)


class TestApplyHumanRulingPrScope:
    def test_records_capability_waiver_when_pr_number_present(self, ws_root: Path) -> None:
        ws = create("human-ruling-pr", "d", root=ws_root)
        blocking = _blocking(ws, pr_number=7, clause_outcome=None)
        question = _capability_question()
        result = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_CAPABILITY_WAIVE_OPTION, blocking=blocking,
        )
        assert result == ws
        assert _pr_state_paths.capability_boundary_waiver_recorded(
            7, _waiver_key(question), DEFAULT_FORGE_TARGET
        ) is True

    def test_no_op_when_pr_number_is_none(self, ws_root: Path) -> None:
        ws = create("human-ruling-pr-none", "d", root=ws_root)
        blocking = _blocking(ws, pr_number=None, clause_outcome=None)
        question = _capability_question()
        result = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_CAPABILITY_WAIVE_OPTION, blocking=blocking,
        )
        assert result == ws
        assert _pr_state_paths.capability_boundary_waiver_recorded(
            7, _waiver_key(question), DEFAULT_FORGE_TARGET
        ) is False

    def test_replay_is_idempotent(self, ws_root: Path) -> None:
        ws = create("human-ruling-pr-replay", "d", root=ws_root)
        blocking = _blocking(ws, pr_number=9, clause_outcome=None)
        question = _capability_question()
        answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_CAPABILITY_WAIVE_OPTION, blocking=blocking,
        )
        result = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_CAPABILITY_WAIVE_OPTION, blocking=blocking,
        )
        assert result == ws
        assert _pr_state_paths.capability_boundary_waiver_recorded(
            9, _waiver_key(question), DEFAULT_FORGE_TARGET
        ) is True


class TestApplyHumanRulingClauseScope:
    def test_withdraw_marks_the_clause(self, ws_root: Path) -> None:
        req = Requirement(text="must be idempotent")
        ws = create("human-ruling-clause", "d", root=ws_root, requirements=[req])
        blocking = _blocking(ws, pr_number=42, clause_outcome=_clause_outcome(ws, req.id))
        question = _clause_stall_question()
        updated = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_WITHDRAW, blocking=blocking,
        )
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].withdrawn_by_question_id == question.id

    def test_withdraw_with_unrecoverable_clause_ids_raises_422_and_writes_nothing(
        self, ws_root: Path
    ) -> None:
        req = Requirement(text="must be idempotent")
        ws = create("human-ruling-clause-422", "d", root=ws_root, requirements=[req])
        blocking = _blocking(ws, pr_number=42, clause_outcome=None)
        question = _clause_stall_question()
        with pytest.raises(answer_mod.AnswerError) as excinfo:
            answer_mod._apply_human_ruling(  # noqa: SLF001
                ws=ws, question=question, answer=_WITHDRAW, blocking=blocking,
            )
        assert excinfo.value.status_code == 422
        assert ws.meta.requirements is not None
        assert ws.meta.requirements[0].withdrawn_by_question_id is None

    def test_waive_with_unrecoverable_clause_ids_proceeds_and_withdraws_nothing(
        self, ws_root: Path
    ) -> None:
        req = Requirement(text="must be idempotent")
        ws = create("human-ruling-clause-waive", "d", root=ws_root, requirements=[req])
        blocking = _blocking(ws, pr_number=42, clause_outcome=None)
        question = _clause_stall_question()
        result = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_WAIVE, blocking=blocking,
        )
        assert result == ws
        assert ws.meta.requirements is not None
        assert ws.meta.requirements[0].withdrawn_by_question_id is None

    def test_replay_of_an_already_withdrawn_clause_is_idempotent(self, ws_root: Path) -> None:
        req = Requirement(text="must be idempotent")
        ws = create("human-ruling-clause-replay", "d", root=ws_root, requirements=[req])
        blocking = _blocking(ws, pr_number=42, clause_outcome=_clause_outcome(ws, req.id))
        question = _clause_stall_question()
        once = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer=_WITHDRAW, blocking=blocking,
        )
        twice = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=once, question=question, answer=_WITHDRAW, blocking=blocking,
        )
        assert twice.meta.requirements is not None
        assert twice.meta.requirements[0].withdrawn_by_question_id == question.id

    def test_no_ruling_returns_workspace_unchanged(self, ws_root: Path) -> None:
        ws = create("human-ruling-none", "d", root=ws_root)
        blocking = _blocking(ws, pr_number=42, clause_outcome=None)
        question = OpenQuestion(
            id="mb-1", question="unblock?", proposed_answer_space=["proceed", "wait"],
            question_class="merge_block_escalation", resolver_role="human", blocking=True,
        )
        result = answer_mod._apply_human_ruling(  # noqa: SLF001
            ws=ws, question=question, answer="wait", blocking=blocking,
        )
        assert result == ws
