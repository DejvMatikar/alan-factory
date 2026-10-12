from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from alan.handover import ClauseOutcome, Handover, is_answer_recording, list_handovers
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Workspace

_AUDIT_FILE = "verification_audit.jsonl"
_TEXT_MAX_LENGTH = 2000


class StalledClause(NamedTuple):
    req_id: str
    text: str
    evidence: str


@dataclass(frozen=True)
class ClauseStall:
    clause_ids: tuple[str, ...]
    repeated_ids: tuple[str, ...]
    clauses: tuple[StalledClause, ...]
    judge_rationale: str
    prior_handover_id: str


def _append_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(logs_root() / _AUDIT_FILE, entry, log_label="verification")


def _is_transparent_amend(handover: Handover) -> bool:
    return handover.handover_type == "revisit_amend" and is_answer_recording(handover)


def _latest_verification_since_intervention(
    ws: Workspace, chain_id: str, task_id: str,
) -> Handover | None:
    del task_id
    for handover in reversed(list_handovers(ws)):
        if handover.handover_chain_id != chain_id or _is_transparent_amend(handover):
            continue
        if handover.handover_type == "revisit_amend":
            return None
        if handover.from_team == "verification":
            return handover
    return None


def _build_stall(
    ws: Workspace,
    prior: Handover,
    prior_outcome: ClauseOutcome,
    current: ClauseOutcome,
    repeated: tuple[str, ...],
) -> ClauseStall:
    ids = tuple(sorted(current.actionable_ids))
    clauses = tuple(
        StalledClause(
            req_id=req_id,
            text=current.clause_texts.get(req_id, "")[:_TEXT_MAX_LENGTH],
            evidence=current.evidence.get(req_id, "")[:_TEXT_MAX_LENGTH],
        )
        for req_id in ids
    )
    stall = ClauseStall(
        clause_ids=ids,
        repeated_ids=repeated,
        clauses=clauses,
        judge_rationale=current.judge_rationale[:_TEXT_MAX_LENGTH],
        prior_handover_id=prior.handover_id,
    )
    _append_audit({
        "task_id": ws.task_id,
        "action": "clause_stall_detected",
        "audited_task_id": current.task_id,
        "req_id": ids[0],
        "req_ids": list(ids),
        "repeated_req_ids": list(repeated),
        "prior_req_ids": sorted(set(prior_outcome.actionable_ids)),
        "prior_handover_id": prior.handover_id,
    })
    return stall


def _repeated_actionable_ids(
    prior_outcome: ClauseOutcome, current: ClauseOutcome,
) -> tuple[str, ...]:
    if prior_outcome.task_id != current.task_id:
        return ()
    return tuple(sorted(set(prior_outcome.actionable_ids) & set(current.actionable_ids)))


def detect(
    ws: Workspace, incoming: Handover | None, current: ClauseOutcome | None,
) -> ClauseStall | None:
    if current is None or incoming is None or not current.actionable_ids:
        return None
    prior = _latest_verification_since_intervention(
        ws, incoming.handover_chain_id, current.task_id,
    )
    if prior is None or prior.clause_outcome is None:
        return None
    repeated = _repeated_actionable_ids(prior.clause_outcome, current)
    if repeated:
        return _build_stall(ws, prior, prior.clause_outcome, current, repeated)
    return None


__all__ = ["ClauseStall", "StalledClause", "detect"]
