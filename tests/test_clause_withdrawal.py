from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import clause_withdrawal
from alan.handover import ClauseOutcome, Handover, HandoverType, OpenQuestion, save_handover
from alan.workspace import Requirement, Workspace, create

_CHAIN = "handover_withdraw-task_root"
_OTHER_CHAIN = "handover_withdraw-task_other_root"
_CURRENT = clause_withdrawal.OPTION_WITHDRAW_WORDING_CURRENT
_LEGACY = clause_withdrawal.OPTION_WITHDRAW_WORDING_LEGACY
_WAIVE = clause_withdrawal.OPTION_WAIVE_WORDING
_OPERATOR_ONLY_CURRENT = clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_CURRENT
_OPERATOR_ONLY_LEGACY = clause_withdrawal.OPTION_OPERATOR_ONLY_WORDING_LEGACY
_OVERRIDE = clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING
_OPERATOR_ONLY_ID = clause_withdrawal.CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return create("withdraw-task", "d", root=tmp_path / "ws")


def _clause_outcome(
    task_id: str, *, actionable_ids: tuple[str, ...] = ("req-a",),
    operator_only_ids: tuple[str, ...] = (),
    unactionable_ids: tuple[str, ...] = (),
) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id,
        actionable_ids=actionable_ids,
        clause_texts=dict.fromkeys(actionable_ids, "text"),
        evidence=dict.fromkeys(actionable_ids, "evidence"),
        judge_rationale="rationale",
        operator_only_ids=operator_only_ids,
        unactionable_ids=unactionable_ids,
    )


def _handover(
    ws: Workspace,
    *,
    seq: int,
    from_team: str,
    handover_type: HandoverType = "forward",
    chain_id: str = _CHAIN,
    clause_outcome: ClauseOutcome | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_{seq:03d}",
        handover_chain_id=chain_id,
        handover_type=handover_type,
        task_id=ws.task_id,
        from_team=from_team,
        from_invocation_id=f"inv_{seq:03d}",
        to_team_suggestions=[],
        iteration=seq,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="handover body",
        clause_outcome=clause_outcome,
    )


class TestIsClauseStallQuestion:
    def test_true_for_clause_stall_prefix(self) -> None:
        q = OpenQuestion(
            id="clause-stall-req-a", question="q", proposed_answer_space=["a", "b"],
            resolver_role="human", blocking=True,
        )
        assert clause_withdrawal.is_clause_stall_question(q) is True

    def test_false_for_other_prefix(self) -> None:
        q = OpenQuestion(
            id="clause-audit-pr-state", question="q", proposed_answer_space=["a", "b"],
            resolver_role="human", blocking=True,
        )
        assert clause_withdrawal.is_clause_stall_question(q) is False


def _question(question_id: str, options: list[str]) -> OpenQuestion:
    return OpenQuestion(
        id=question_id, question="q", proposed_answer_space=options,
        resolver_role="human", blocking=True,
    )


class TestResolveHumanRulingClauseStall:
    def test_current_withdraw_wording_resolves_to_verification_closes(self) -> None:
        q = _question("clause-stall-req-a", [_CURRENT, _WAIVE])
        result = clause_withdrawal.resolve_human_ruling(q, _CURRENT)
        assert result == clause_withdrawal.Ruling("verification", "closes", "clause")

    def test_legacy_withdraw_wording_still_resolves(self) -> None:
        q = _question("clause-stall-req-a", [_LEGACY, _WAIVE])
        result = clause_withdrawal.resolve_human_ruling(q, _LEGACY)
        assert result == clause_withdrawal.Ruling("verification", "closes", "clause")

    def test_waive_wording_resolves_to_pr_merge_closes(self) -> None:
        q = _question("clause-stall-req-a", [_CURRENT, _WAIVE])
        result = clause_withdrawal.resolve_human_ruling(q, _WAIVE)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "clause")

    def test_non_clause_stall_question_returns_none(self) -> None:
        q = _question("verif-judge-parse", ["retry", "review manually"])
        assert clause_withdrawal.resolve_human_ruling(q, "retry") is None

    def test_unrecognized_answer_on_clause_stall_question_returns_none(self) -> None:
        q = _question("clause-stall-req-a", ["some other option", "another option"])
        assert clause_withdrawal.resolve_human_ruling(q, "some other option") is None


class TestResolveHumanRulingPrStateDoor:
    def test_override_wording_on_pr_state_id_resolves_to_pr_merge_closes_clause_scope(
        self,
    ) -> None:
        q = _question(
            clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
            ["Update the PR's state on GitHub, then re-dispatch verification.", _OVERRIDE],
        )
        result = clause_withdrawal.resolve_human_ruling(q, _OVERRIDE)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "clause")

    def test_non_override_wording_on_pr_state_id_returns_none(self) -> None:
        q = _question(
            clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
            ["Update the PR's state on GitHub, then re-dispatch verification.", _OVERRIDE],
        )
        result = clause_withdrawal.resolve_human_ruling(
            q, "Update the PR's state on GitHub, then re-dispatch verification."
        )
        assert result is None

    def test_is_pr_state_escalation_true_for_pr_state_id(self) -> None:
        q = _question(
            clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
            ["Update the PR's state on GitHub, then re-dispatch verification.", _OVERRIDE],
        )
        assert clause_withdrawal.is_pr_state_escalation(q) is True

    def test_is_pr_state_escalation_false_for_other_id(self) -> None:
        q = _question("clause-stall-req-a", [_CURRENT, _OVERRIDE])
        assert clause_withdrawal.is_pr_state_escalation(q) is False


class TestResolveHumanRulingGenericWaiver:
    def test_generic_verification_escalation_waiver_resolves_to_pr_scope(self) -> None:
        waive_option = "Waive this finding and proceed to merge."
        q = OpenQuestion(
            id="verif-capability-boundary-abc12345",
            question="unresolvable",
            proposed_answer_space=[
                "Resolve this outside the fix-worker loop, then re-dispatch verification.",
                waive_option,
            ],
            question_class="verification_escalation",
            resolver_role="human",
            blocking=True,
            option_resolvers={
                "Resolve this outside the fix-worker loop, then re-dispatch verification.": (
                    "verification"
                ),
                waive_option: "pr_merge",
            },
            option_effects={waive_option: "closes"},
        )
        result = clause_withdrawal.resolve_human_ruling(q, waive_option)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "pr")

    def test_generic_waiver_uses_pending_external_effect_when_declared(self) -> None:
        q = OpenQuestion(
            id="verif-something-else",
            question="q",
            proposed_answer_space=["proceed", "wait"],
            question_class="verification_escalation",
            resolver_role="human",
            blocking=True,
            option_resolvers={"proceed": "pr_merge"},
            option_effects={"proceed": "pending_external"},
        )
        result = clause_withdrawal.resolve_human_ruling(q, "proceed")
        assert result == clause_withdrawal.Ruling("pr_merge", "pending_external", "pr")

    def test_non_verification_escalation_class_returns_none_even_with_pr_merge_resolver(
        self,
    ) -> None:
        q = OpenQuestion(
            id="mb-1", question="unblock?", proposed_answer_space=["proceed", "wait"],
            question_class="merge_block_escalation", resolver_role="human", blocking=True,
            option_resolvers={"proceed": "pr_merge"},
        )
        assert clause_withdrawal.resolve_human_ruling(q, "proceed") is None


class TestLatestVerificationWithClauseOutcome:
    def test_finds_handover_carrying_clause_outcome(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id)
        h = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        save_handover(ws, h)
        found = clause_withdrawal.latest_verification_with_clause_outcome(ws, _CHAIN)
        assert found is not None
        assert found.handover_id == h.handover_id

    def test_skips_verification_handover_with_no_clause_outcome(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id)
        earlier = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        save_handover(ws, earlier)
        later = _handover(ws, seq=2, from_team="verification", clause_outcome=None)
        save_handover(ws, later)
        found = clause_withdrawal.latest_verification_with_clause_outcome(ws, _CHAIN)
        assert found is not None
        assert found.handover_id == earlier.handover_id

    def test_returns_none_at_revisit_amend_boundary(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id)
        save_handover(ws, _handover(ws, seq=1, from_team="verification", clause_outcome=outcome))
        save_handover(
            ws, _handover(ws, seq=2, from_team="verification", handover_type="revisit_amend")
        )
        assert clause_withdrawal.latest_verification_with_clause_outcome(ws, _CHAIN) is None

    def test_ignores_off_chain_handovers(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id)
        save_handover(
            ws,
            _handover(
                ws, seq=1, from_team="verification", chain_id=_OTHER_CHAIN, clause_outcome=outcome
            ),
        )
        assert clause_withdrawal.latest_verification_with_clause_outcome(ws, _CHAIN) is None

    def test_returns_none_when_no_handovers(self, ws: Workspace) -> None:
        assert clause_withdrawal.latest_verification_with_clause_outcome(ws, _CHAIN) is None


class TestClauseIdsForWithdrawal:
    def test_reads_directly_from_escalation_clause_outcome(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation)
        assert ids == ["req-a", "req-b"]

    def test_falls_back_to_chain_walk_when_escalation_has_no_outcome(self, ws: Workspace) -> None:
        outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a",))
        prior = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        save_handover(ws, prior)
        escalation = _handover(ws, seq=2, from_team="verification", clause_outcome=None)
        save_handover(ws, escalation)
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation)
        assert ids == ["req-a"]

    def test_returns_none_when_nothing_recoverable(self, ws: Workspace) -> None:
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=None)
        save_handover(ws, escalation)
        assert clause_withdrawal.clause_ids_for_withdrawal(ws, escalation) is None


class TestWithdraw:
    def test_marks_matching_ids(self, ws: Workspace) -> None:
        req = Requirement(text="must do X")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.withdraw(ws, [req.id], "clause-stall-abc")
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].withdrawn_by_question_id == "clause-stall-abc"

    def test_leaves_non_matching_clauses_untouched(self, ws: Workspace) -> None:
        keep = Requirement(text="must do Y")
        target = Requirement(text="must do X")
        ws = ws.model_copy(
            update={"meta": ws.meta.model_copy(update={"requirements": [keep, target]})}
        )
        updated = clause_withdrawal.withdraw(ws, [target.id], "clause-stall-abc")
        assert updated.meta.requirements is not None
        kept = next(r for r in updated.meta.requirements if r.id == keep.id)
        assert kept.withdrawn_by_question_id is None

    def test_idempotent_on_already_withdrawn_clause(self, ws: Workspace) -> None:
        req = Requirement(text="must do X", withdrawn_by_question_id="clause-stall-first")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.withdraw(ws, [req.id], "clause-stall-second")
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].withdrawn_by_question_id == "clause-stall-first"

    def test_no_op_when_no_ids_match(self, ws: Workspace) -> None:
        req = Requirement(text="must do X")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.withdraw(ws, ["req-does-not-exist"], "clause-stall-abc")
        assert updated.meta.requirements == ws.meta.requirements

    def test_logs_warning_on_unresolvable_ids(
        self, ws: Workspace, caplog: pytest.LogCaptureFixture
    ) -> None:
        req = Requirement(text="must do X")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        with caplog.at_level("WARNING"):
            clause_withdrawal.withdraw(ws, [req.id, "req-ghost"], "clause-stall-abc")
        assert any("req-ghost" in record.getMessage() for record in caplog.records)

    def test_no_requirements_returns_workspace_unchanged(self, ws: Workspace) -> None:
        updated = clause_withdrawal.withdraw(ws, ["anything"], "clause-stall-abc")
        assert updated.meta.requirements == ws.meta.requirements


class TestIsOperatorOnlyEscalation:
    def test_true_for_operator_only_question_id(self) -> None:
        q = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_CURRENT, _OVERRIDE])
        assert clause_withdrawal.is_operator_only_escalation(q) is True

    def test_false_for_clause_stall_question_id(self) -> None:
        q = _question("clause-stall-req-a", [_CURRENT, _WAIVE])
        assert clause_withdrawal.is_operator_only_escalation(q) is False


class TestResolveHumanRulingOperatorOnly:
    def test_current_wording_resolves_to_pr_merge_closes(self) -> None:
        q = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_CURRENT, _OVERRIDE])
        result = clause_withdrawal.resolve_human_ruling(q, _OPERATOR_ONLY_CURRENT)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "clause")

    def test_legacy_wording_still_resolves(self) -> None:
        q = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_LEGACY, _OVERRIDE])
        result = clause_withdrawal.resolve_human_ruling(q, _OPERATOR_ONLY_LEGACY)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "clause")

    def test_override_wording_resolves_to_pr_merge_closes(self) -> None:
        q = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_CURRENT, _OVERRIDE])
        result = clause_withdrawal.resolve_human_ruling(q, _OVERRIDE)
        assert result == clause_withdrawal.Ruling("pr_merge", "closes", "clause")

    def test_unrecognized_answer_on_operator_only_question_returns_none(self) -> None:
        q = _question(_OPERATOR_ONLY_ID, ["some other option", "another option"])
        assert clause_withdrawal.resolve_human_ruling(q, "some other option") is None


class TestClauseIdsForWithdrawalOperatorOnly:
    def test_operator_only_question_reads_operator_only_ids(self, ws: Workspace) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=(), operator_only_ids=("req-op-a", "req-op-b"),
        )
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        question = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_CURRENT, _OVERRIDE])
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
        assert ids == ["req-op-a", "req-op-b"]

    def test_non_operator_only_question_still_reads_actionable_ids(self, ws: Workspace) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=("req-a",), operator_only_ids=("req-op-a",),
        )
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        question = _question("clause-stall-req-a", [_CURRENT, _WAIVE])
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
        assert ids == ["req-a"]

    def test_operator_only_falls_back_to_chain_walk(self, ws: Workspace) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=(), operator_only_ids=("req-op-a",),
        )
        prior = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        save_handover(ws, prior)
        escalation = _handover(ws, seq=2, from_team="verification", clause_outcome=None)
        save_handover(ws, escalation)
        question = _question(_OPERATOR_ONLY_ID, [_OPERATOR_ONLY_CURRENT, _OVERRIDE])
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
        assert ids == ["req-op-a"]


class TestClauseIdsForWithdrawalPrState:
    def test_pr_state_question_reads_unactionable_ids(self, ws: Workspace) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=(), unactionable_ids=("req-pr-a", "req-pr-b"),
        )
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        question = _question(
            clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
            ["Update the PR's state on GitHub, then re-dispatch verification.", _OVERRIDE],
        )
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
        assert ids == ["req-pr-a", "req-pr-b"]

    def test_pr_state_question_ignores_actionable_and_operator_only_buckets(
        self, ws: Workspace
    ) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=("req-a",), operator_only_ids=("req-op-a",),
            unactionable_ids=("req-pr-a",),
        )
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        question = _question(
            clause_withdrawal.CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
            ["Update the PR's state on GitHub, then re-dispatch verification.", _OVERRIDE],
        )
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
        assert ids == ["req-pr-a"]

    def test_no_question_defaults_to_actionable_ids_bucket(self, ws: Workspace) -> None:
        outcome = _clause_outcome(
            ws.task_id, actionable_ids=("req-a",), unactionable_ids=("req-pr-a",),
        )
        escalation = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
        ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation)
        assert ids == ["req-a"]


class TestOperatorWithdrawalSentinel:
    def test_sentinel_is_colon_namespaced(self) -> None:
        assert clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL == "operator:withdrawal"

    def test_sentinel_is_not_a_clause_stall_question(self) -> None:
        q = _question(clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL, [_CURRENT, _WAIVE])
        assert clause_withdrawal.is_clause_stall_question(q) is False

    def test_withdraw_with_sentinel_stamps_the_marker(self, ws: Workspace) -> None:
        req = Requirement(text="must do X")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.withdraw(
            ws, [req.id], clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL
        )
        assert updated.meta.requirements is not None
        assert (
            updated.meta.requirements[0].withdrawn_by_question_id
            == clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL
        )


class TestIsOperatorWithdrawal:
    def test_true_for_sentinel_marker(self) -> None:
        req = Requirement(
            text="must do X",
            withdrawn_by_question_id=clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL,
        )
        assert clause_withdrawal.is_operator_withdrawal(req) is True

    def test_false_for_question_id_marker(self) -> None:
        req = Requirement(text="must do X", withdrawn_by_question_id="clause-stall-abc")
        assert clause_withdrawal.is_operator_withdrawal(req) is False

    def test_false_for_no_marker(self) -> None:
        req = Requirement(text="must do X")
        assert clause_withdrawal.is_operator_withdrawal(req) is False


class TestReinstate:
    def test_returns_the_workspace_unchanged_when_nothing_matches(self, ws: Workspace) -> None:
        req = Requirement(text="must do X")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.reinstate(ws, [req.id])
        assert updated is ws

    def test_reinstate_clears_only_the_operator_sentinel_marker(self, ws: Workspace) -> None:
        req = Requirement(
            text="must do X",
            withdrawn_by_question_id=clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL,
        )
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.reinstate(ws, [req.id])
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].withdrawn_by_question_id is None

    def test_reinstate_leaves_a_question_provenance_marker_in_place(self, ws: Workspace) -> None:
        req = Requirement(text="must do X", withdrawn_by_question_id="clause-stall-abc")
        ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
        updated = clause_withdrawal.reinstate(ws, [req.id])
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].withdrawn_by_question_id == "clause-stall-abc"
