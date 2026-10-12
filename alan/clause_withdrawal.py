from __future__ import annotations

import logging
from typing import Literal, NamedTuple

from alan.handover import ClauseOutcome, Handover, OpenQuestion, OptionEffect, list_handovers
from alan.workspace import Requirement, Workspace, update_requirements

logger = logging.getLogger(__name__)

CLAUSE_STALL_QUESTION_PREFIX = "clause-stall-"
CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID = "clause-audit-operator-only"
CLAUSE_AUDIT_PR_STATE_QUESTION_ID = "clause-audit-pr-state"
OPERATOR_WITHDRAWAL_SENTINEL = "operator:withdrawal"

OPTION_WITHDRAW_WORDING_LEGACY = "Rewrite or drop this clause, then re-dispatch verification."
OPTION_WITHDRAW_WORDING_CURRENT = (
    "Withdraw this clause and re-dispatch verification against what remains."
)
OPTION_WAIVE_WORDING = "Waive this clause and proceed to merge."

OPTION_OPERATOR_ONLY_WORDING_LEGACY = (
    "File or reference a tracking issue for these operator-only "
    "post-merge measurements, then re-dispatch verification."
)
OPTION_OPERATOR_ONLY_WORDING_CURRENT = (
    "File or reference a tracking issue for these operator-only "
    "post-merge measurements, then proceed to merge."
)
OPTION_CLAUSE_OVERRIDE_WORDING = "Override the clause manually and proceed."

_WITHDRAW_WORDINGS: frozenset[str] = frozenset(
    {OPTION_WITHDRAW_WORDING_LEGACY, OPTION_WITHDRAW_WORDING_CURRENT}
)
_OPERATOR_ONLY_WORDINGS: frozenset[str] = frozenset({
    OPTION_OPERATOR_ONLY_WORDING_LEGACY,
    OPTION_OPERATOR_ONLY_WORDING_CURRENT,
    OPTION_CLAUSE_OVERRIDE_WORDING,
})


def is_clause_stall_question(question: OpenQuestion) -> bool:
    return question.id.startswith(CLAUSE_STALL_QUESTION_PREFIX)


def is_operator_only_escalation(question: OpenQuestion) -> bool:
    return question.id == CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID


def is_pr_state_escalation(question: OpenQuestion) -> bool:
    return question.id == CLAUSE_AUDIT_PR_STATE_QUESTION_ID


class Ruling(NamedTuple):
    resolver: str
    effect: OptionEffect
    scope: Literal["clause", "pr"]


def _resolve_operator_only_ruling(question: OpenQuestion, answer: str) -> Ruling | None:
    if not is_operator_only_escalation(question):
        return None
    if answer in _OPERATOR_ONLY_WORDINGS:
        return Ruling("pr_merge", "closes", "clause")
    return None


def _resolve_pr_state_override_ruling(question: OpenQuestion, answer: str) -> Ruling | None:
    if is_pr_state_escalation(question) and answer == OPTION_CLAUSE_OVERRIDE_WORDING:
        return Ruling("pr_merge", "closes", "clause")
    return None


def _resolve_clause_stall_ruling(question: OpenQuestion, answer: str) -> Ruling | None:
    if not is_clause_stall_question(question):
        return None
    if answer in _WITHDRAW_WORDINGS:
        return Ruling("verification", "closes", "clause")
    if answer == OPTION_WAIVE_WORDING:
        return Ruling("pr_merge", "closes", "clause")
    return None


def _resolve_generic_waiver_ruling(question: OpenQuestion, answer: str) -> Ruling | None:
    if question.question_class != "verification_escalation":
        return None
    if question.option_resolvers.get(answer) != "pr_merge":
        return None
    return Ruling("pr_merge", question.option_effects.get(answer, "closes"), "pr")


def resolve_human_ruling(question: OpenQuestion, answer: str) -> Ruling | None:
    for resolver in (
        _resolve_operator_only_ruling,
        _resolve_pr_state_override_ruling,
        _resolve_clause_stall_ruling,
        _resolve_generic_waiver_ruling,
    ):
        ruling = resolver(question, answer)
        if ruling is not None:
            return ruling
    return None


def latest_verification_with_clause_outcome(ws: Workspace, chain_id: str) -> Handover | None:
    for handover in reversed(list_handovers(ws)):
        if handover.handover_chain_id != chain_id:
            continue
        if handover.handover_type == "revisit_amend":
            return None
        if handover.from_team == "verification" and handover.clause_outcome is not None:
            return handover
    return None


def _bucket_attr_for_question(question: OpenQuestion | None) -> str:
    if question is not None and is_operator_only_escalation(question):
        return "operator_only_ids"
    if question is not None and is_pr_state_escalation(question):
        return "unactionable_ids"
    return "actionable_ids"


def _outcome_ids(outcome: ClauseOutcome, *, bucket_attr: str) -> list[str]:
    return list(getattr(outcome, bucket_attr))


def clause_ids_for_withdrawal(
    ws: Workspace, escalation: Handover, *, question: OpenQuestion | None = None,
) -> list[str] | None:
    bucket_attr = _bucket_attr_for_question(question)
    if escalation.clause_outcome is not None:
        return _outcome_ids(escalation.clause_outcome, bucket_attr=bucket_attr)
    recovered = latest_verification_with_clause_outcome(ws, escalation.handover_chain_id)
    if recovered is None or recovered.clause_outcome is None:
        return None
    return _outcome_ids(recovered.clause_outcome, bucket_attr=bucket_attr)


def _withdraw_one(req: Requirement, wanted: set[str], question_id: str) -> Requirement:
    if req.id in wanted and req.withdrawn_by_question_id is None:
        return req.model_copy(update={"withdrawn_by_question_id": question_id})
    return req


def _warn_on_unresolvable_ids(ws: Workspace, wanted: set[str], reqs: list[Requirement]) -> None:
    missing = wanted - {r.id for r in reqs}
    if not missing:
        return
    logger.warning(
        "clause_withdrawal.withdraw: %d clause id(s) no longer resolve for workspace %s: %s",
        len(missing), ws.task_id, sorted(missing),
    )


def withdraw(ws: Workspace, clause_ids: list[str], question_id: str) -> Workspace:
    reqs = ws.meta.requirements or []
    wanted = set(clause_ids)
    _warn_on_unresolvable_ids(ws, wanted, reqs)
    new_reqs = [_withdraw_one(r, wanted, question_id) for r in reqs]
    if new_reqs == reqs:
        return ws
    return update_requirements(ws, new_reqs)


def is_operator_withdrawal(req: Requirement) -> bool:
    return req.withdrawn_by_question_id == OPERATOR_WITHDRAWAL_SENTINEL


def _reinstate_one(req: Requirement, wanted: set[str]) -> Requirement:
    if req.id in wanted and is_operator_withdrawal(req):
        return req.model_copy(update={"withdrawn_by_question_id": None})
    return req


def reinstate(ws: Workspace, clause_ids: list[str]) -> Workspace:
    reqs = ws.meta.requirements or []
    wanted = set(clause_ids)
    _warn_on_unresolvable_ids(ws, wanted, reqs)
    new_reqs = [_reinstate_one(r, wanted) for r in reqs]
    if new_reqs == reqs:
        return ws
    return update_requirements(
        ws, new_reqs, clear_withdrawn=(frozenset(wanted), OPERATOR_WITHDRAWAL_SENTINEL)
    )


__all__ = [
    "CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID",
    "CLAUSE_AUDIT_PR_STATE_QUESTION_ID",
    "CLAUSE_STALL_QUESTION_PREFIX",
    "OPERATOR_WITHDRAWAL_SENTINEL",
    "OPTION_CLAUSE_OVERRIDE_WORDING",
    "OPTION_OPERATOR_ONLY_WORDING_CURRENT",
    "OPTION_OPERATOR_ONLY_WORDING_LEGACY",
    "OPTION_WAIVE_WORDING",
    "OPTION_WITHDRAW_WORDING_CURRENT",
    "OPTION_WITHDRAW_WORDING_LEGACY",
    "Ruling",
    "clause_ids_for_withdrawal",
    "is_clause_stall_question",
    "is_operator_only_escalation",
    "is_operator_withdrawal",
    "is_pr_state_escalation",
    "latest_verification_with_clause_outcome",
    "reinstate",
    "resolve_human_ruling",
    "withdraw",
]
