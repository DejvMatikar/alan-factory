from __future__ import annotations

import re
from dataclasses import dataclass

from alan.handover import Handover, OpenQuestion, QuestionClass
from alan.teams import _fix_forward
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_YES,
    GATE_FAILING_TEST_PREFIX,
    gate_block_section,
)

QUESTION_CLASS: QuestionClass = "fix_escalation"
QUESTION_ID_PREFIX = "non_actionable_feedback_"
GENERIC_OPTIONS: tuple[str, str] = (
    "Re-dispatch implementation_fix once the flaky/environmental cause has "
    "cleared, so the same review can be attempted again.",
    "Resolve the ambiguity by hand (clarify scope, fix the environment "
    "issue), then re-dispatch verification directly.",
)
RESOLVE_BY_HAND = GENERIC_OPTIONS[1]
_NAMED_CHECK_CAP = 5
_RATCHET_TITLE_MARKER = "Flake ratchet"
_FAILING_CHECKS_RE = re.compile(r"^- failing checks: (.+)$", re.MULTILINE)
_STATUS_RE = re.compile(
    r"^\(mergeStateStatus=(?P<merge>[^,]*), reviewDecision=(?P<review>[^)]*)\)$",
    re.MULTILINE,
)
_FAILING_TEST_RE = re.compile(
    rf"^{re.escape(GATE_FAILING_TEST_PREFIX)} (.+)$", re.MULTILINE
)
_TOP_LEVEL_COMMA_RE = re.compile(r",(?![^()]*\))")


@dataclass(frozen=True)
class GateCause:
    failing_checks: tuple[str, ...]
    failing_tests: tuple[str, ...]
    ratchet_verdicts: tuple[str, ...]
    merge_state_status: str
    review_decision: str


def _split_check_names(raw: str) -> tuple[str, ...]:
    return tuple(name.strip() for name in _TOP_LEVEL_COMMA_RE.split(raw) if name.strip())


def _failing_checks(section: str) -> tuple[str, ...]:
    match = _FAILING_CHECKS_RE.search(section)
    return _split_check_names(match.group(1)) if match else ()


def _status_fields(section: str) -> tuple[str, str]:
    match = _STATUS_RE.search(section)
    if match is None:
        return "", ""
    return match.group("merge"), match.group("review")


def _partition_tests(section: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tests = tuple(_FAILING_TEST_RE.findall(section))
    ratchet = tuple(t for t in tests if _RATCHET_TITLE_MARKER in t)
    return tuple(t for t in tests if t not in ratchet), ratchet


def parse_gate_cause(synthesis: str) -> GateCause | None:
    section = gate_block_section(synthesis)
    if not section or GATE_ACTIONABLE_YES in section:
        return None
    failing_tests, ratchet = _partition_tests(section)
    merge_state, review = _status_fields(section)
    return GateCause(
        failing_checks=_failing_checks(section),
        failing_tests=failing_tests,
        ratchet_verdicts=ratchet,
        merge_state_status=merge_state,
        review_decision=review,
    )


def _cause_summary(cause: GateCause) -> str:
    checks = ", ".join(cause.failing_checks) or "none"
    tests = "; ".join(cause.failing_tests) or "none named"
    ratchet = "; ".join(cause.ratchet_verdicts) or "not triggered"
    return (
        f"failing checks: {checks}; failing tests: {tests}; flake ratchet: {ratchet}; "
        f"mergeStateStatus={cause.merge_state_status or '?'}, "
        f"reviewDecision={cause.review_decision or '?'}"
    )


def _generic_question(inv_id: str, reason: str) -> OpenQuestion:
    return OpenQuestion(
        id=f"{QUESTION_ID_PREFIX}{inv_id[:8]}",
        question=reason,
        proposed_answer_space=list(GENERIC_OPTIONS),
        recommended=RESOLVE_BY_HAND,
        question_class=QUESTION_CLASS,
        resolver_role="human",
        blocking=True,
        option_resolvers={RESOLVE_BY_HAND: "verification"},
    )


def _named_checks(names: tuple[str, ...]) -> str:
    listed = ", ".join(names[:_NAMED_CHECK_CAP])
    hidden = len(names) - _NAMED_CHECK_CAP
    return f"{listed} and {hidden} more" if hidden > 0 else listed


def _contradiction_question(
    inv_id: str, reason: str, contradicted: tuple[str, ...], ceiling_reached: bool, head_sha: str,
) -> OpenQuestion:
    named = _named_checks(contradicted)
    state = (
        f"required check(s) {named} are red at head {head_sha[:12] or 'unknown'} "
        "and no flake evidence excuses them"
    )
    if ceiling_reached:
        fix_option = (
            "Raise this pull request's fix-cycle allowance, then re-dispatch "
            f"implementation_fix scoped to {named} — the fix-cycle ceiling is "
            f"reached, so it cannot be dispatched as things stand ({state})."
        )
        recommended, resolvers = RESOLVE_BY_HAND, {RESOLVE_BY_HAND: "verification"}
    else:
        fix_option = (
            f"Re-dispatch implementation_fix scoped to {named} — {state}, "
            "which contradicts the claim that nothing here is actionable; "
            "investigate the red check(s) at that head instead of giving up."
        )
        recommended = fix_option
        resolvers = {fix_option: "implementation_fix", RESOLVE_BY_HAND: "verification"}
    return OpenQuestion(
        id=f"{QUESTION_ID_PREFIX}{inv_id[:8]}",
        question=f"{reason} Contradicted by the pull request: {state}.",
        proposed_answer_space=[fix_option, RESOLVE_BY_HAND],
        recommended=recommended,
        question_class=QUESTION_CLASS,
        resolver_role="human",
        blocking=True,
        option_resolvers=resolvers,
    )


def non_actionable_question(
    prior: Handover | None,
    inv_id: str,
    reason: str,
    *,
    contradicted: tuple[str, ...] = (),
    ceiling_reached: bool = False,
    head_sha: str = "",
) -> OpenQuestion:
    if contradicted:
        return _contradiction_question(inv_id, reason, contradicted, ceiling_reached, head_sha)
    cause = (
        parse_gate_cause(_fix_forward.resolve_review_text(prior))
        if prior is not None
        else None
    )
    if cause is None:
        return _generic_question(inv_id, reason)
    summary = _cause_summary(cause)
    pr_merge_option = (
        "Re-dispatch pr_merge — the block is external gate state, not code "
        f"({summary}); pr_merge owns the check rerun ladder and the bounded park."
    )
    return OpenQuestion(
        id=f"{QUESTION_ID_PREFIX}{inv_id[:8]}",
        question=f"{reason} External gate cause: {summary}.",
        proposed_answer_space=[pr_merge_option, RESOLVE_BY_HAND],
        recommended=pr_merge_option,
        question_class=QUESTION_CLASS,
        resolver_role="human",
        blocking=True,
        option_resolvers={pr_merge_option: "pr_merge", RESOLVE_BY_HAND: "verification"},
    )


__all__ = [
    "GENERIC_OPTIONS",
    "QUESTION_CLASS",
    "QUESTION_ID_PREFIX",
    "RESOLVE_BY_HAND",
    "GateCause",
    "non_actionable_question",
    "parse_gate_cause",
]
