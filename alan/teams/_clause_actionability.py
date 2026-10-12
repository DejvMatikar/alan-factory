from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from alan.teams._pr_ready import (
    CheckOutcome,
    PrReadiness,
    ReadinessResult,
    resolve_named_check_outcome,
)

if TYPE_CHECKING:
    from alan.teams._clause_line_parse import ClauseAuditRow

_PR_STATE_EVIDENCE_PREFIX = "PR_STATE:"
_ENV_EVIDENCE_PREFIX = "ENV:"
_OPERATOR_ONLY_EVIDENCE_PREFIX = "OPERATOR_ONLY:"
_CHECK_CITATION_RE = re.compile(r'check="([^"]+)"')


class ClauseActionability(str, Enum):
    ACTIONABLE = "actionable"
    PR_STATE = "pr_state"
    HOST_ENV = "host_env"
    CHECK_CONTRADICTED = "check_contradicted"
    OPERATOR_ONLY = "operator_only"


class DemotedClause(NamedTuple):
    row: ClauseAuditRow
    classification: ClauseActionability
    check_name: str | None
    check_outcome: CheckOutcome | None


class ClausePartition(NamedTuple):
    actionable: tuple[ClauseAuditRow, ...]
    unactionable: tuple[DemotedClause, ...]


def _cited_check_name(evidence: str) -> str | None:
    match = _CHECK_CITATION_RE.search(evidence)
    return match.group(1) if match else None


def _uncorroborated_class(evidence: str) -> ClauseActionability | None:
    if evidence.startswith(_PR_STATE_EVIDENCE_PREFIX):
        return ClauseActionability.PR_STATE
    if evidence.startswith(_OPERATOR_ONLY_EVIDENCE_PREFIX):
        return ClauseActionability.OPERATOR_ONLY
    return None


def _classify_and_corroborate(
    evidence: str, readiness: ReadinessResult | None,
) -> tuple[ClauseActionability, str | None, CheckOutcome | None]:
    uncorroborated = _uncorroborated_class(evidence)
    if uncorroborated is not None:
        return uncorroborated, None, None
    if not isinstance(readiness, PrReadiness):
        return ClauseActionability.ACTIONABLE, None, None
    check_name = _cited_check_name(evidence)
    if check_name is None:
        return ClauseActionability.ACTIONABLE, None, None
    outcome = resolve_named_check_outcome(check_name, readiness)
    if outcome is not CheckOutcome.PASS:
        return ClauseActionability.ACTIONABLE, check_name, outcome
    if evidence.startswith(_ENV_EVIDENCE_PREFIX):
        return ClauseActionability.HOST_ENV, check_name, outcome
    return ClauseActionability.CHECK_CONTRADICTED, check_name, outcome


def cites_check(rows: tuple[ClauseAuditRow, ...]) -> bool:
    return any(
        _uncorroborated_class(row.evidence) is None
        and _cited_check_name(row.evidence) is not None
        for row in rows
    )


def same_head(anchor: PrReadiness | None, decision: ReadinessResult) -> PrReadiness | None:
    if anchor is None or not anchor.head_sha:
        return None
    if not isinstance(decision, PrReadiness) or decision.head_sha != anchor.head_sha:
        return None
    return decision


def check_name_lines(readiness: ReadinessResult | None) -> list[str]:
    if not isinstance(readiness, PrReadiness):
        return []
    names = sorted(
        {*readiness.passing_checks, *readiness.pending_checks, *readiness.failing_checks}
    )
    if not names:
        return []
    return [
        "",
        "Required-check names on this pull request (host-fetched, exact "
        "spelling) — for `check=\"<name>\"` citations only; a name's "
        "presence here says nothing about its conclusion and never grounds "
        "a PASS:",
        *[f"- {name}" for name in names],
    ]


def classify_failure(evidence: str, readiness: ReadinessResult | None) -> ClauseActionability:
    return _classify_and_corroborate(evidence, readiness)[0]


def partition(
    rows: tuple[ClauseAuditRow, ...], readiness: ReadinessResult | None,
) -> ClausePartition:
    actionable: list[ClauseAuditRow] = []
    unactionable: list[DemotedClause] = []
    for row in rows:
        classification, check_name, outcome = _classify_and_corroborate(row.evidence, readiness)
        if classification is ClauseActionability.ACTIONABLE:
            actionable.append(row)
        else:
            unactionable.append(DemotedClause(row, classification, check_name, outcome))
    return ClausePartition(tuple(actionable), tuple(unactionable))


_CLASS_CAUSE_LABELS: dict[ClauseActionability, str] = {
    ClauseActionability.PR_STATE: (
        "evidence resting solely on pull-request state (draft/head/base), "
        "which a fix worker cannot mutate"
    ),
    ClauseActionability.HOST_ENV: (
        "evidence the auditor itself characterised as host-environment "
        "pollution in its own checkout"
    ),
    ClauseActionability.CHECK_CONTRADICTED: (
        "a local command result contradicted by a green required check on "
        "the same commit"
    ),
    ClauseActionability.OPERATOR_ONLY: (
        "a measurement only an operator can take after merge, with live "
        "access the fleet does not have"
    ),
}


def cause_text(rows: tuple[DemotedClause, ...]) -> str:
    classes = sorted({d.classification for d in rows}, key=lambda c: c.value)
    labels = "; ".join(_CLASS_CAUSE_LABELS[c] for c in classes)
    ids = ", ".join(d.row.req_id for d in rows)
    return f"{labels} (clauses: {ids})"


_STAND_DOWN_ELIGIBLE: frozenset[ClauseActionability] = frozenset({
    ClauseActionability.HOST_ENV, ClauseActionability.CHECK_CONTRADICTED,
})


def stands_down(parts: ClausePartition) -> bool:
    if parts.actionable or not parts.unactionable:
        return False
    return all(
        d.classification in _STAND_DOWN_ELIGIBLE and d.check_outcome is CheckOutcome.PASS
        for d in parts.unactionable
    )
