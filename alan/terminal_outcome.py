from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

import alan.workflow as workflow_mod
from alan.handover import Handover
from alan.queue import Job
from alan.workflow import WorkflowDoc, WorkflowError
from alan.workspace import Status, TaskStatus, Workspace

_REASON_MAX = 200
_FALLBACK_FAILED = "The run failed. Check the invocation log for details."
_FALLBACK_ESCALATED = "The run was escalated. Check the handover for details."
_MERGED_EVIDENCE_PREFIX = "PR #"


class TerminalOutcome(BaseModel):
    outcome: Literal["escalated", "failed", "partial"]
    reason: str
    block_class: str | None = None


def _first_line(text: str, max_chars: int = _REASON_MAX) -> str:
    line = text.strip().split("\n", 1)[0].strip()
    return line[:max_chars]


def _escalated_reason(handovers: list[Handover]) -> str:
    for h in reversed(handovers):
        if h.handover_type == "escalation" and h.full_synthesis:
            return _first_line(h.full_synthesis)
    return _FALLBACK_ESCALATED


def _failed_reason_from_doc(doc: WorkflowDoc) -> str | None:
    for task in doc.tasks:
        if task.status in ("failed", "interrupted") and task.error:
            return _first_line(task.error)
    return None


def _failed_reason_from_workflow(ws: Workspace) -> str | None:
    for inv_id in reversed(workflow_mod.list_invocations(ws)):
        path = workflow_mod.workflow_path(ws, inv_id)
        try:
            doc = workflow_mod.load_workflow_cached(path)
        except (WorkflowError, OSError):
            continue
        reason = _failed_reason_from_doc(doc)
        if reason:
            return reason
    return None


def _failed_reason_from_jobs(failed_jobs: list[Job]) -> str | None:
    for j in reversed(failed_jobs):
        if j.error:
            return _first_line(j.error)
    return None


def _merged_sibling_prs(ws: Workspace) -> list[str]:
    evidence = (
        (clause.evidence or "").strip()
        for task in ws.meta.tasks
        if task.status == TaskStatus.COMPLETED
        for clause in task.dod_clauses
    )
    merged = (e for e in evidence if e.startswith(_MERGED_EVIDENCE_PREFIX))
    return list(dict.fromkeys(merged))


def _with_merged_credit(base: str, merged: list[str]) -> str:
    if not merged:
        return base
    credit = f"Already merged: {', '.join(merged)}."
    head = base[: max(0, _REASON_MAX - len(credit) - 1)].rstrip()
    joined = f"{head} {credit}" if head else credit
    return joined[:_REASON_MAX]


def has_merged_sibling(ws: Workspace) -> bool:
    return bool(_merged_sibling_prs(ws))


def merged_sibling_pr_number(ws: Workspace) -> int | None:
    numbers = [
        int(digits)
        for evidence in _merged_sibling_prs(ws)
        if (digits := evidence.removeprefix(_MERGED_EVIDENCE_PREFIX).strip()).isdigit()
    ]
    return max(numbers) if numbers else None


def resolve_terminal_outcome(
    ws: Workspace,
    handovers: list[Handover],
    failed_jobs: list[Job],
) -> TerminalOutcome | None:
    if ws.meta.status == Status.ESCALATED:
        return TerminalOutcome(
            outcome="escalated",
            reason=_escalated_reason(handovers),
            block_class=ws.meta.merge_block_class,
        )
    if ws.meta.status in (Status.FAILED, Status.PARTIAL):
        base: str = (
            _failed_reason_from_workflow(ws)
            or _failed_reason_from_jobs(failed_jobs)
            or _FALLBACK_FAILED
        )
        reason = _with_merged_credit(base, _merged_sibling_prs(ws))
        if ws.meta.status == Status.PARTIAL:
            return TerminalOutcome(
                outcome="partial", reason=reason, block_class=ws.meta.merge_block_class
            )
        return TerminalOutcome(
            outcome="failed", reason=reason, block_class=ws.meta.merge_block_class
        )
    return None
