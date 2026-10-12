from __future__ import annotations

from pathlib import Path

import pytest

from alan.handover import ClauseOutcome
from alan.teams import _clause_actionability, _fix_escalation
from alan.teams import _verification_review_post as review_post_mod
from alan.teams._base import TeamEvent
from alan.teams._clause_line_parse import ClauseAuditRow
from alan.workspace import Workspace
from tests import _teams_verification_support as support


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def test_capability_boundary_option_effects_map_matches_the_two_options(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output",
        reason="rotating the prod signing key needs a human with vault access.",
    )
    oq = handover.open_questions[0]
    assert oq.option_effects == {
        review_post_mod._CAPABILITY_BOUNDARY_OPTION_VERIFICATION: "pending_external",  # noqa: SLF001
        review_post_mod._CAPABILITY_BOUNDARY_OPTION_WAIVE: "closes",  # noqa: SLF001
    }


def test_judge_parse_failed_emits_empty_option_effects(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = review_post_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output",
    )
    assert handover.open_questions[0].option_effects == {}


def test_clause_pr_state_escalation_emits_empty_option_effects(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output",
    )
    assert handover.open_questions[0].option_effects == {}


def test_resolve_by_hand_is_unclassified_despite_routing_to_verification() -> None:
    question = _fix_escalation.non_actionable_question(None, "inv_00000001", "feedback")
    assert question.option_resolvers[_fix_escalation.RESOLVE_BY_HAND] == "verification"
    assert question.option_effects == {}


def _operator_only_row(req_id: str = "req-op") -> _clause_actionability.DemotedClause:
    return _clause_actionability.DemotedClause(
        row=ClauseAuditRow(req_id=req_id, verdict="FAIL", evidence="OPERATOR_ONLY: needs a probe"),
        classification=_clause_actionability.ClauseActionability.OPERATOR_ONLY,
        check_name=None,
        check_outcome=None,
    )


def _pr_state_row(req_id: str = "req-state") -> _clause_actionability.DemotedClause:
    return _clause_actionability.DemotedClause(
        row=ClauseAuditRow(req_id=req_id, verdict="FAIL", evidence="PR_STATE: isDraft is false"),
        classification=_clause_actionability.ClauseActionability.PR_STATE,
        check_name=None,
        check_outcome=None,
    )


def test_operator_only_escalation_maps_both_options_to_pr_merge_closes(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(_operator_only_row(),),
    )
    oq = handover.open_questions[0]
    assert oq.id == "clause-audit-operator-only"
    assert set(oq.option_resolvers.values()) == {"pr_merge"}
    assert set(oq.option_effects.values()) == {"closes"}


def test_operator_only_escalation_attaches_clause_outcome(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    outcome = ClauseOutcome(
        task_id=workspace.task_id, actionable_ids=(), clause_texts={}, evidence={},
        operator_only_ids=("req-op",),
    )
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(_operator_only_row(),), clause_outcome=outcome,
    )
    assert handover.clause_outcome == outcome


def test_pr_state_only_escalation_keeps_empty_option_maps(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(_pr_state_row(),),
    )
    oq = handover.open_questions[0]
    assert oq.id == "clause-audit-pr-state"
    assert oq.option_resolvers == {}
    assert oq.option_effects == {}


def test_no_unactionable_rows_escalation_carries_no_clause_outcome(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
    )
    assert handover.clause_outcome is None
