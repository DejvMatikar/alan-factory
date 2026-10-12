from __future__ import annotations

import json
import string
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import clause_withdrawal
from alan.clause_withdrawal import OPTION_WAIVE_WORDING, OPTION_WITHDRAW_WORDING_CURRENT
from alan.handover import (
    ClauseOutcome,
    Handover,
    HandoverType,
    OpenQuestion,
    list_handovers,
    save_handover,
)
from alan.teams import _clause_stall
from alan.teams._verification_capability import NO_FLEET_ROLE_PHRASE
from alan.teams._verification_review_post import (
    _CLAUSE_STALL_QUESTION_TEMPLATE,
    _clause_stall_question_id,
    _emit_clause_stall_escalation,
)
from alan.workspace import Requirement, Workspace, create

_CHAIN = "handover_stall-task_root"
_OTHER_CHAIN = "handover_stall-task_other_root"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return create("stall-task", "d", root=tmp_path / "ws")


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


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


def _clause_outcome(
    task_id: str,
    *,
    actionable_ids: tuple[str, ...] = ("req-a",),
    clause_text: str = "the endpoint must be idempotent",
    evidence: str = "POST /x is not idempotent",
    judge_rationale: str = "the diff never guards against a repeat call",
) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id,
        actionable_ids=actionable_ids,
        clause_texts=dict.fromkeys(actionable_ids, clause_text),
        evidence=dict.fromkeys(actionable_ids, evidence),
        judge_rationale=judge_rationale,
    )


def _audit_rows(audit_root: Path) -> list[dict[str, object]]:
    path = audit_root / "verification_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_reinstatement_does_not_change_a_recorded_verification_clause_outcome(
    ws: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="the endpoint must be idempotent")
    ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
    outcome = _clause_outcome(ws.task_id, actionable_ids=(req.id,))
    handover = _handover(ws, seq=1, from_team="verification", clause_outcome=outcome)
    save_handover(ws, handover)

    ws = clause_withdrawal.withdraw(
        ws, [req.id], clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL
    )
    ws = clause_withdrawal.reinstate(ws, [req.id])

    [reloaded] = [h for h in list_handovers(ws) if h.handover_id == handover.handover_id]
    assert reloaded.clause_outcome == outcome


def test_the_next_stall_detection_after_a_reinstatement_reraises_the_same_stall(
    ws: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="the endpoint must be idempotent")
    ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"requirements": [req]})})
    prior_outcome = _clause_outcome(ws.task_id, actionable_ids=(req.id,))
    save_handover(
        ws, _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome)
    )

    ws = clause_withdrawal.withdraw(
        ws, [req.id], clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL
    )
    ws = clause_withdrawal.reinstate(ws, [req.id])

    incoming = _handover(ws, seq=2, from_team="implementation_fix")
    current_outcome = _clause_outcome(ws.task_id, actionable_ids=(req.id,))

    stall = _clause_stall.detect(ws, incoming, current_outcome)

    assert stall is not None
    assert stall.clause_ids == (req.id,)
    assert stall.repeated_ids == (req.id,)


class TestLatestVerificationSinceIntervention:
    def test_returns_none_when_no_handovers(self, ws: Workspace) -> None:
        assert (
            _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
                ws, _CHAIN, ws.task_id
            )
            is None
        )

    def test_finds_verification_handover(self, ws: Workspace) -> None:
        h = _handover(ws, seq=1, from_team="verification")
        save_handover(ws, h)
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is not None
        assert found.handover_id == h.handover_id

    def test_skips_off_chain_handover(self, ws: Workspace) -> None:
        save_handover(
            ws, _handover(ws, seq=1, from_team="verification", chain_id=_OTHER_CHAIN)
        )
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is None

    def test_returns_none_on_revisit_amend_before_verification(
        self, ws: Workspace
    ) -> None:
        save_handover(ws, _handover(ws, seq=1, from_team="verification"))
        save_handover(
            ws,
            _handover(
                ws, seq=2, from_team="verification", handover_type="revisit_amend"
            ),
        )
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is None

    def test_skips_non_verification_handover_between(self, ws: Workspace) -> None:
        first = _handover(ws, seq=1, from_team="verification")
        save_handover(ws, first)
        save_handover(ws, _handover(ws, seq=2, from_team="implementation_fix"))
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is not None
        assert found.handover_id == first.handover_id

    def _answer_recording_revisit_amend(self, ws: Workspace, *, seq: int) -> Handover:
        answered_question = OpenQuestion(
            id="clause-stall-req-a",
            question="stalled",
            proposed_answer_space=["a", "b"],
            resolver_role="human",
            blocking=True,
            answer="a",
        )
        return Handover(
            handover_id=f"handover_{ws.task_id}_{seq:03d}",
            handover_chain_id=_CHAIN,
            handover_type="revisit_amend",
            task_id=ws.task_id,
            from_team="verification",
            from_invocation_id=f"inv_{seq:03d}",
            to_team_suggestions=[],
            iteration=seq,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="recorded answer",
            open_questions=[answered_question],
        )

    def test_continues_past_an_answer_recording_revisit_amend(self, ws: Workspace) -> None:
        first = _handover(ws, seq=1, from_team="verification")
        save_handover(ws, first)
        save_handover(ws, self._answer_recording_revisit_amend(ws, seq=2))
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is not None
        assert found.handover_id == first.handover_id

    def test_still_returns_none_on_a_content_revisit_amend_with_no_answered_question(
        self, ws: Workspace
    ) -> None:
        save_handover(ws, _handover(ws, seq=1, from_team="verification"))
        save_handover(
            ws,
            _handover(
                ws, seq=2, from_team="verification", handover_type="revisit_amend"
            ),
        )
        found = _clause_stall._latest_verification_since_intervention(  # noqa: SLF001
            ws, _CHAIN, ws.task_id
        )
        assert found is None


class TestDetect:
    def test_hit_returns_stall_and_appends_audit_row(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id)
        prior = _handover(
            ws, seq=1, from_team="verification", clause_outcome=prior_outcome
        )
        save_handover(ws, prior)
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id)

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a",)
        assert stall.repeated_ids == ("req-a",)
        assert stall.clauses[0].req_id == "req-a"
        assert stall.clauses[0].text == "the endpoint must be idempotent"
        assert stall.clauses[0].evidence == "POST /x is not idempotent"
        assert stall.judge_rationale == "the diff never guards against a repeat call"
        assert stall.prior_handover_id == prior.handover_id
        rows = _audit_rows(audit_root)
        assert any(
            r["action"] == "clause_stall_detected"
            and r["task_id"] == ws.task_id
            and r["audited_task_id"] == ws.task_id
            and r["req_id"] == "req-a"
            and r["req_ids"] == ["req-a"]
            and r["repeated_req_ids"] == ["req-a"]
            and r["prior_req_ids"] == ["req-a"]
            and r["prior_handover_id"] == prior.handover_id
            for r in rows
        )

    def test_returns_none_when_current_is_none(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        incoming = _handover(ws, seq=1, from_team="implementation_fix")
        assert _clause_stall.detect(ws, incoming, None) is None
        assert _audit_rows(audit_root) == []

    def test_detect_returns_none_on_empty_actionable_set(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        incoming = _handover(ws, seq=1, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=())
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_incoming_is_none(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, None, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_no_prior_verification_handover(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        incoming = _handover(ws, seq=1, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_prior_clause_outcome_is_none(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        save_handover(ws, _handover(ws, seq=1, from_team="verification"))
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_task_id_mismatched(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome("sibling-subtask")
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_actionable_sets_are_disjoint(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-b",))
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a",))
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_off_chain(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id)
        save_handover(
            ws,
            _handover(
                ws, seq=1, from_team="verification", chain_id=_OTHER_CHAIN,
                clause_outcome=prior_outcome,
            ),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix", chain_id=_CHAIN)
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_returns_none_when_revisit_amend_precedes(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id)
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        save_handover(
            ws,
            _handover(
                ws, seq=2, from_team="verification", handover_type="revisit_amend"
            ),
        )
        incoming = _handover(ws, seq=3, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id)
        assert _clause_stall.detect(ws, incoming, current) is None
        assert _audit_rows(audit_root) == []

    def test_still_fires_with_intervening_implementation_fix_handover(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id)
        prior = _handover(
            ws, seq=1, from_team="verification", clause_outcome=prior_outcome
        )
        save_handover(ws, prior)
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        save_handover(ws, incoming)
        current = _clause_outcome(ws.task_id)

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.prior_handover_id == prior.handover_id

    def test_truncates_long_clause_text_and_evidence_to_2000_chars(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        long_text = "x" * 3000
        prior_outcome = _clause_outcome(
            ws.task_id, clause_text=long_text, evidence=long_text,
        )
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, clause_text=long_text, evidence=long_text)

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert len(stall.clauses[0].text) == 2000
        assert len(stall.clauses[0].evidence) == 2000

    def test_detect_fires_on_repeated_group_of_four_clauses(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        ids = ("req-a", "req-b", "req-c", "req-d")
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=ids)
        prior = _handover(
            ws, seq=1, from_team="verification", clause_outcome=prior_outcome
        )
        save_handover(ws, prior)
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=ids)

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ids
        assert stall.repeated_ids == ids
        assert {c.req_id for c in stall.clauses} == set(ids)

    def test_detect_still_fires_on_repeated_single_clause(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a",))
        prior = _handover(
            ws, seq=1, from_team="verification", clause_outcome=prior_outcome
        )
        save_handover(ws, prior)
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a",))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a",)
        assert stall.repeated_ids == ("req-a",)

    def test_detect_fires_on_shrinking_set_because_the_survivor_repeated(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a",))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a",)
        assert stall.repeated_ids == ("req-a",)

    def test_detect_fires_on_growing_set_and_keys_identity_on_the_overlap(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a",))
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a", "req-b")
        assert stall.repeated_ids == ("req-a",)
        assert {c.req_id for c in stall.clauses} == {"req-a", "req-b"}

    def test_detect_ignores_ids_only_the_prior_run_flagged(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-c"))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a", "req-c")
        assert stall.repeated_ids == ("req-a",)

    def test_repeated_ids_are_sorted_and_deduplicated(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(
            ws.task_id, actionable_ids=("req-b", "req-a", "req-b"),
        )
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-b", "req-a"))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.repeated_ids == ("req-a", "req-b")

    def test_audit_row_records_the_prior_run_set_that_produced_the_overlap(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-c"))

        _clause_stall.detect(ws, incoming, current)

        row = next(
            r for r in _audit_rows(audit_root) if r["action"] == "clause_stall_detected"
        )
        assert row["prior_req_ids"] == ["req-a", "req-b"]
        assert row["req_ids"] == ["req-a", "req-c"]
        assert row["repeated_req_ids"] == ["req-a"]

    def test_detect_is_order_insensitive_for_the_same_set(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=("req-b", "req-a"))
        prior = _handover(
            ws, seq=1, from_team="verification", clause_outcome=prior_outcome
        )
        save_handover(ws, prior)
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=("req-a", "req-b"))

        stall = _clause_stall.detect(ws, incoming, current)

        assert stall is not None
        assert stall.clause_ids == ("req-a", "req-b")
        assert stall.repeated_ids == ("req-a", "req-b")

    def test_stall_audit_row_names_every_clause(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        ids = ("req-a", "req-b", "req-c")
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=ids)
        save_handover(
            ws,
            _handover(ws, seq=1, from_team="verification", clause_outcome=prior_outcome),
        )
        incoming = _handover(ws, seq=2, from_team="implementation_fix")
        current = _clause_outcome(ws.task_id, actionable_ids=ids)

        _clause_stall.detect(ws, incoming, current)

        rows = _audit_rows(audit_root)
        row = next(r for r in rows if r["action"] == "clause_stall_detected")
        assert row["req_ids"] == list(ids)
        assert row["req_id"] == "req-a"
        assert row["repeated_req_ids"] == list(ids)


class TestClauseStallQuestionId:
    def test_stall_question_id_preserved_for_single_clause(self) -> None:
        assert _clause_stall_question_id(("req-a",)) == "clause-stall-req-a"

    def test_stall_question_id_is_bounded_for_a_large_group(self) -> None:
        ids = tuple(f"req-{i}" for i in range(30))
        question_id = _clause_stall_question_id(ids)
        assert question_id.startswith("clause-stall-")
        assert len(question_id) <= 200
        assert question_id == _clause_stall_question_id(tuple(reversed(ids)))


class TestQuestionIdStabilityAcrossAFlappingAuditor:
    def _stall_for_pair(
        self, ws: Workspace, seq: int, prior_ids: tuple[str, ...], current_ids: tuple[str, ...]
    ) -> _clause_stall.ClauseStall:
        prior_outcome = _clause_outcome(ws.task_id, actionable_ids=prior_ids)
        save_handover(
            ws,
            _handover(
                ws, seq=seq, from_team="verification", clause_outcome=prior_outcome
            ),
        )
        incoming = _handover(ws, seq=seq + 1, from_team="implementation_fix")
        stall = _clause_stall.detect(
            ws, incoming, _clause_outcome(ws.task_id, actionable_ids=current_ids)
        )
        assert stall is not None
        return stall

    def test_three_flapping_runs_mint_one_question_id(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        first = self._stall_for_pair(
            ws, 1, ("req-a", "req-b"), ("req-a", "req-b", "req-c"),
        )
        second = self._stall_for_pair(
            ws, 3, ("req-a", "req-b", "req-c"), ("req-a", "req-d", "req-b"),
        )

        assert _clause_stall_question_id(first.repeated_ids) == _clause_stall_question_id(
            second.repeated_ids
        )
        assert _clause_stall_question_id(first.clause_ids) != _clause_stall_question_id(
            second.clause_ids
        )

    def test_singleton_overlap_keeps_the_legacy_question_id(
        self, ws: Workspace, audit_root: Path
    ) -> None:
        stall = self._stall_for_pair(ws, 1, ("req-a", "req-b"), ("req-a", "req-c"))
        assert _clause_stall_question_id(stall.repeated_ids) == "clause-stall-req-a"


def test_emit_clause_stall_escalation_construction(ws: Workspace) -> None:
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a",),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a",
                text="the endpoint must be idempotent",
                evidence="POST /x is not idempotent",
            ),
        ),
        judge_rationale="the diff never guards against a repeat call",
        prior_handover_id="handover_stall-task_001",
    )
    events: list[object] = []
    handover = _emit_clause_stall_escalation(
        ws, None, events.append, "inv_z", "judge output", stall=stall,
    )
    assert handover.to_team_suggestions == []
    assert len(handover.open_questions) == 1
    oq = handover.open_questions[0]
    assert oq.blocking is True
    assert oq.resolver_role == "human"
    assert oq.question_class == "verification_escalation"
    assert oq.id == "clause-stall-req-a"
    assert "the endpoint must be idempotent" in oq.question
    assert "the diff never guards against a repeat call" in oq.question
    assert oq.proposed_answer_space == [
        OPTION_WITHDRAW_WORDING_CURRENT,
        OPTION_WAIVE_WORDING,
    ]
    assert oq.recommended == OPTION_WITHDRAW_WORDING_CURRENT
    assert oq.option_resolvers == {
        OPTION_WITHDRAW_WORDING_CURRENT: "verification",
        OPTION_WAIVE_WORDING: "pr_merge",
    }
    assert oq.option_effects == {
        OPTION_WITHDRAW_WORDING_CURRENT: "pending_external",
        OPTION_WAIVE_WORDING: "closes",
    }
    assert handover.clause_outcome is None


def test_emit_clause_stall_escalation_carries_clause_outcome_when_provided(
    ws: Workspace,
) -> None:
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a",),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="the endpoint must be idempotent",
                evidence="POST /x is not idempotent",
            ),
        ),
        judge_rationale="the diff never guards against a repeat call",
        prior_handover_id="handover_stall-task_001",
    )
    outcome = _clause_outcome(ws.task_id)
    handover = _emit_clause_stall_escalation(
        ws, None, lambda _e: None, "inv_z", "judge output", stall=stall,
        clause_outcome=outcome,
    )
    assert handover.clause_outcome == outcome


def test_emit_clause_stall_escalation_names_every_clause_in_group(ws: Workspace) -> None:
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a", "req-b"),
        repeated_ids=("req-a", "req-b"),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="clause a text", evidence="evidence a",
            ),
            _clause_stall.StalledClause(
                req_id="req-b", text="clause b text", evidence="evidence b",
            ),
        ),
        judge_rationale="neither can be satisfied by a fix worker",
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, None, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    oq = handover.open_questions[0]
    assert "req-a" in oq.question
    assert "req-b" in oq.question
    assert "clause a text" in oq.question
    assert "clause b text" in oq.question
    assert oq.id != "clause-stall-req-a"
    assert oq.id.startswith("clause-stall-")


def _answered_amend(ws: Workspace, *, question_id: str, answer: str) -> Handover:
    question = OpenQuestion(
        id=question_id,
        question="acceptance clause stalled",
        proposed_answer_space=[OPTION_WITHDRAW_WORDING_CURRENT, OPTION_WAIVE_WORDING],
        recommended=OPTION_WITHDRAW_WORDING_CURRENT,
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        answer=answer,
    )
    return Handover(
        handover_id=f"handover_{ws.task_id}_amend",
        handover_chain_id=_CHAIN,
        handover_type="revisit_amend",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_amend",
        iteration=5,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="recorded ruling",
        open_questions=[question],
    )


def test_emit_clause_stall_escalation_does_not_reuse_an_answered_overlap_id_for_a_wider_scope(
    ws: Workspace,
) -> None:
    save_handover(
        ws,
        _answered_amend(
            ws, question_id="clause-stall-req-a", answer=OPTION_WITHDRAW_WORDING_CURRENT
        ),
    )
    prior = _handover(ws, seq=6, from_team="implementation_fix")
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a", "req-b"),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="clause a text", evidence="evidence a",
            ),
            _clause_stall.StalledClause(
                req_id="req-b", text="clause b text", evidence="evidence b",
            ),
        ),
        judge_rationale="req-b newly surfaced after the earlier ruling",
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, prior, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    oq = handover.open_questions[0]
    assert oq.id != "clause-stall-req-a"
    assert oq.id == _clause_stall_question_id(("req-a", "req-b"))


def test_emit_clause_stall_escalation_reuses_the_overlap_id_while_it_is_unanswered(
    ws: Workspace,
) -> None:
    prior = _handover(ws, seq=6, from_team="implementation_fix")
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a", "req-b"),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="clause a text", evidence="evidence a",
            ),
            _clause_stall.StalledClause(
                req_id="req-b", text="clause b text", evidence="evidence b",
            ),
        ),
        judge_rationale="the auditor flagged a different second clause each run",
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, prior, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    assert handover.open_questions[0].id == "clause-stall-req-a"


def test_emit_clause_stall_escalation_keeps_an_answered_id_when_the_scope_is_unchanged(
    ws: Workspace,
) -> None:
    save_handover(
        ws,
        _answered_amend(
            ws, question_id="clause-stall-req-a", answer=OPTION_WITHDRAW_WORDING_CURRENT
        ),
    )
    prior = _handover(ws, seq=6, from_team="implementation_fix")
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a",),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="clause a text", evidence="evidence a",
            ),
        ),
        judge_rationale="the same single clause repeated",
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, prior, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    assert handover.open_questions[0].id == "clause-stall-req-a"


def test_emit_clause_stall_escalation_keys_id_on_the_overlap_but_names_every_clause(
    ws: Workspace,
) -> None:
    stall = _clause_stall.ClauseStall(
        clause_ids=("req-a", "req-b"),
        repeated_ids=("req-a",),
        clauses=(
            _clause_stall.StalledClause(
                req_id="req-a", text="clause a text", evidence="evidence a",
            ),
            _clause_stall.StalledClause(
                req_id="req-b", text="clause b text", evidence="evidence b",
            ),
        ),
        judge_rationale="the auditor flagged a different second clause each run",
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, None, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    oq = handover.open_questions[0]
    assert oq.id == "clause-stall-req-a"
    assert "clause a text" in oq.question
    assert "clause b text" in oq.question
    assert "req-b" in oq.question


_REPEATED_STALL_IDS = ("req-9fea4b808669", "req-ae1983946acb")
_ACTIONABLE_STALL_IDS = (*_REPEATED_STALL_IDS, "req-96a67ebd9852")
_STALL_CLAUSE_TEXT = "the report lists every measured lane"
_STALL_EVIDENCE = "the report omits the idle lane"
_STALL_RATIONALE = "Neutral rationale."


def _realistic_stall_question(ws: Workspace) -> str:
    stall = _clause_stall.ClauseStall(
        clause_ids=_ACTIONABLE_STALL_IDS,
        repeated_ids=_REPEATED_STALL_IDS,
        clauses=tuple(
            _clause_stall.StalledClause(
                req_id=req_id, text=_STALL_CLAUSE_TEXT, evidence=_STALL_EVIDENCE,
            )
            for req_id in _ACTIONABLE_STALL_IDS
        ),
        judge_rationale=_STALL_RATIONALE,
        prior_handover_id="handover_stall-task_001",
    )
    handover = _emit_clause_stall_escalation(
        ws, None, lambda _e: None, "inv_z", "judge output", stall=stall,
    )
    return handover.open_questions[0].question


def test_stall_template_carries_none_of_the_removed_vocabulary() -> None:
    for removed in (
        NO_FLEET_ROLE_PHRASE, "not a fixable defect", "scoping decision", "unresolvable",
    ):
        assert removed not in _CLAUSE_STALL_QUESTION_TEMPLATE
    field_names = {
        name
        for _, name, _, _ in string.Formatter().parse(_CLAUSE_STALL_QUESTION_TEMPLATE)
        if name
    }
    assert "phrase" not in field_names


def test_stall_question_omits_phrase_and_removed_sentences(ws: Workspace) -> None:
    question = _realistic_stall_question(ws)
    assert NO_FLEET_ROLE_PHRASE not in question
    assert "not a fixable defect" not in question
    assert "scoping decision" not in question


def test_stall_question_names_repeating_ids_and_two_run_statement_within_300_chars(
    ws: Workspace,
) -> None:
    lead = _realistic_stall_question(ws)[:300]
    for req_id in _REPEATED_STALL_IDS:
        assert len(req_id) == 16
        assert req_id in lead
    assert "two consecutive verification runs" in lead


def test_stall_question_names_actionable_set_within_500_chars(ws: Workspace) -> None:
    assert ", ".join(_ACTIONABLE_STALL_IDS) in _realistic_stall_question(ws)[:500]


def test_stall_question_still_carries_clause_text_evidence_and_judge_reasoning(
    ws: Workspace,
) -> None:
    question = _realistic_stall_question(ws)
    assert _STALL_CLAUSE_TEXT in question
    assert _STALL_EVIDENCE in question
    assert f"Judge's reasoning: {_STALL_RATIONALE}" in question
