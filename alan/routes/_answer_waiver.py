from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from alan import clause_withdrawal
from alan import park as park_mod
from alan import workspace as workspace_mod
from alan.handover import Handover, OpenQuestion
from alan.roots import logs_root
from alan.routes import _deps
from alan.scenario_binding import AUDIT_FILE as _SCENARIO_OVERRIDE_AUDIT_FILE
from alan.teams import _pr_forge, _verdict
from alan.teams._audit import append_jsonl_audit
from alan.teams._pr_waiver_dismissal import (
    WaiverDismissal,
    dismiss_for_waiver,
    record_waiver_dismissal,
)
from alan.teams._verification_artifacts import read_count_record, read_review_artifact
from alan.workspace import Requirement, Status, Workspace

logger = logging.getLogger("alan.server")

CLAUSE_REINSTATEMENT_RESPONSES: dict[int | str, dict[str, str]] = {
    **_deps.GUARDED,
    404: {"description": "workspace not found, or clause_id does not resolve"},
    409: {
        "description": "workspace is archived, clause is not withdrawn, "
        "or the withdrawal came from a human answering a question"
    },
}


@dataclass(frozen=True)
class WaiverApplication:
    cleared: bool = False
    blocker: park_mod.Blocker | None = None


def is_waiver(question: OpenQuestion, answer: str) -> bool:
    ruling = clause_withdrawal.resolve_human_ruling(question, answer)
    return ruling is not None and ruling.resolver == "pr_merge"


def waiver_floor_inputs(ws: Workspace, blocking: Handover) -> tuple[int | None, str | None]:
    inv_id = blocking.from_invocation_id
    artifact = read_review_artifact(ws, inv_id)
    if artifact is None:
        return None, "count_not_reported"
    record = read_count_record(ws, inv_id)
    if record is None:
        return None, "count_not_reported"
    critical = _verdict.parse_critical_count_or_none(artifact)
    floor_reason = _verdict.count_floor_reason(critical, record)
    return record.reviewer, floor_reason


def _withheld_blocker(
    pr_number: int, outcome: str, floor_reason: str | None,
) -> park_mod.Blocker:
    reason = floor_reason or "dismissal call failed"
    return park_mod.Blocker(
        kind="reason",
        name=park_mod.WAIVER_WITHHELD_BLOCKER,
        detail=(
            f"Waiver on PR #{pr_number} did not clear the verifier review: "
            f"{outcome} ({reason})"
        ),
        instruction=(
            f"Dismiss the verifier review on PR #{pr_number} by hand, "
            "then dispatch the pr_merge team."
        ),
    )


def apply(
    *, ws: Workspace, question: OpenQuestion, answer: str, blocking: Handover,
) -> WaiverApplication:
    if blocking.pr_number is None or not is_waiver(question, answer):
        return WaiverApplication()
    reviewer_count, floor_reason = waiver_floor_inputs(ws, blocking)
    try:
        result = dismiss_for_waiver(
            blocking.pr_number,
            forge_target=_pr_forge.forge_target_for_ws(ws),
            question_id=question.id,
            floor_reason=floor_reason,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "waiver dismissal failed for pr=%s question=%s",
            blocking.pr_number, question.id,
        )
        result = WaiverDismissal("failed", (), False)
    record_waiver_dismissal(
        task_id=ws.task_id,
        pr_number=blocking.pr_number,
        question_id=question.id,
        invocation_id=blocking.from_invocation_id,
        reviewer_count=reviewer_count,
        floor_reason=floor_reason,
        result=result,
    )
    blocker = None
    if result.outcome in ("withheld_floor", "failed"):
        blocker = _withheld_blocker(blocking.pr_number, result.outcome, floor_reason)
    return WaiverApplication(
        cleared=result.outcome == "dismissed" and not result.other_author_blocking,
        blocker=blocker,
    )


def find_live_requirement(
    requirements: list[Requirement], clause_id: str
) -> Requirement | None:
    for req in requirements:
        if req.id == clause_id:
            return req
    return None


def append_operator_clause_audit(
    workspace_id: str, clause_id: str, reason: str, *, kind: str, log_label: str
) -> None:
    append_jsonl_audit(
        logs_root() / _SCENARIO_OVERRIDE_AUDIT_FILE,
        {
            "kind": kind,
            "workspace_id": workspace_id,
            "clause_id": clause_id,
            "reason": reason,
        },
        log_label=log_label,
    )


def require_reinstatable_clause(ws: Workspace, clause_id: str) -> None:
    target = find_live_requirement(ws.meta.requirements or [], clause_id)
    if target is None:
        raise HTTPException(status_code=404, detail="clause_id does not resolve")
    if target.withdrawn_by_question_id is None:
        raise HTTPException(status_code=409, detail="clause is not withdrawn")
    if not clause_withdrawal.is_operator_withdrawal(target):
        raise HTTPException(
            status_code=409,
            detail="clause was withdrawn by a human answering a question",
        )


def reinstate_clause_locked(
    lock: threading.Lock, ws: Workspace, clause_id: str, reason: str, workspaces_root: Path,
) -> Workspace:
    with lock:
        fresh = workspace_mod.read(ws.task_id, root=workspaces_root)
        if fresh.meta.status == Status.ARCHIVED:
            raise HTTPException(status_code=409, detail="workspace is archived")
        require_reinstatable_clause(fresh, clause_id)
        fresh = clause_withdrawal.reinstate(fresh, [clause_id])
        append_operator_clause_audit(
            ws.task_id, clause_id, reason,
            kind="clause_reinstatement", log_label="clause_reinstatement_override",
        )
        return fresh
