from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from alan.workflow import (
    INTERRUPTED_BY_RESTART_ERROR,
    WorkflowDoc,
    WorkflowError,
    WorkflowTask,
    load_workflow,
    save_workflow,
    workflow_path,
)
from alan.workspace import Workspace


def interrupt_workflow_on_disk(
    workspace: Workspace,
    inv_id: str,
    *,
    error: str = INTERRUPTED_BY_RESTART_ERROR,
    resumable: bool = False,
    recover_finalized: bool = False,
) -> WorkflowDoc | None:
    try:
        doc = load_workflow(workflow_path(workspace, inv_id))
    except WorkflowError:
        return None
    now = datetime.now(UTC)
    mutated, has_interrupted = _apply_running_to_terminal(
        doc, now=now, error=error, resumable=resumable, recover_finalized=recover_finalized
    )
    if _apply_doc_status(
        doc,
        now=now,
        resumable=resumable,
        has_interrupted=has_interrupted,
        recover_finalized=recover_finalized,
    ):
        mutated = True
    if not mutated:
        return doc
    save_workflow(workspace, doc)
    return doc


def _apply_doc_status(
    doc: WorkflowDoc,
    *,
    now: datetime,
    resumable: bool,
    has_interrupted: bool,
    recover_finalized: bool,
) -> bool:
    if doc.status in ("running", "pending"):
        if resumable and has_interrupted:
            if recover_finalized:
                return _ensure_doc_interrupted(doc)
            return _ensure_doc_running(doc)
        doc.status = "failed"
        doc.completed_at = now
        return True
    return _maybe_recover_finalized_doc(
        doc,
        has_interrupted=has_interrupted,
        resumable=resumable,
        recover_finalized=recover_finalized,
    )


def _ensure_doc_running(doc: WorkflowDoc) -> bool:
    if doc.status == "running":
        return False
    doc.status = "running"
    return True


def _ensure_doc_interrupted(doc: WorkflowDoc) -> bool:
    doc.status = "interrupted"
    return True


def _maybe_recover_finalized_doc(
    doc: WorkflowDoc, *, has_interrupted: bool, resumable: bool, recover_finalized: bool
) -> bool:
    if not (resumable and recover_finalized and has_interrupted and doc.status == "failed"):
        return False
    doc.status = "interrupted"
    doc.completed_at = None
    return True


def _apply_running_to_terminal(
    doc: WorkflowDoc,
    *,
    now: datetime,
    error: str,
    resumable: bool,
    recover_finalized: bool,
) -> tuple[bool, bool]:
    mutated = False
    has_interrupted = False
    deferred: list[WorkflowTask] = []
    for task in doc.tasks:
        outcome = _transition_task(
            task, now=now, error=error, resumable=resumable, recover_finalized=recover_finalized
        )
        mutated = mutated or outcome.mutated
        has_interrupted = has_interrupted or outcome.interrupted
        if outcome.deferred:
            deferred.append(task)
    if _settle_deferred(deferred, has_interrupted=has_interrupted, now=now, error=error):
        mutated = True
    return mutated, has_interrupted


def _settle_deferred(
    deferred: list[WorkflowTask], *, has_interrupted: bool, now: datetime, error: str
) -> bool:
    if not deferred:
        return False
    if has_interrupted:
        for task in deferred:
            task.status = "pending"
            task.error = error
            task.started_at = None
            task.completed_at = None
            task.claude_session_id = None
        return True
    for task in deferred:
        _fail_task(task, now=now, error=error)
    return True


@dataclass(frozen=True)
class _TransitionOutcome:
    mutated: bool
    interrupted: bool
    deferred: bool = False


def _transition_task(
    task: WorkflowTask,
    *,
    now: datetime,
    error: str,
    resumable: bool,
    recover_finalized: bool,
) -> _TransitionOutcome:
    if task.status == "running":
        return _transition_running(task, now=now, error=error, resumable=resumable)
    if task.status == "interrupted":
        return _transition_existing_interrupted(
            task, now=now, error=error, resumable=resumable
        )
    if task.status == "failed":
        return _transition_existing_failed(
            task, error=error, resumable=resumable, recover_finalized=recover_finalized
        )
    return _TransitionOutcome(mutated=False, interrupted=False)


def _transition_existing_failed(
    task: WorkflowTask, *, error: str, resumable: bool, recover_finalized: bool
) -> _TransitionOutcome:
    if not (resumable and recover_finalized and task.claude_session_id):
        return _TransitionOutcome(mutated=False, interrupted=False)
    return _rescue_failed(task, error=error)


def _transition_existing_interrupted(
    task: WorkflowTask, *, now: datetime, error: str, resumable: bool
) -> _TransitionOutcome:
    if resumable:
        return _TransitionOutcome(mutated=False, interrupted=True)
    _fail_task(task, now=now, error=error)
    return _TransitionOutcome(mutated=True, interrupted=False)


def _transition_running(
    task: WorkflowTask, *, now: datetime, error: str, resumable: bool
) -> _TransitionOutcome:
    if resumable and task.claude_session_id:
        task.status = "interrupted"
        task.error = error
        return _TransitionOutcome(mutated=True, interrupted=True)
    if resumable:
        return _TransitionOutcome(mutated=False, interrupted=False, deferred=True)
    _fail_task(task, now=now, error=error)
    return _TransitionOutcome(mutated=True, interrupted=False)


def _rescue_failed(task: WorkflowTask, *, error: str) -> _TransitionOutcome:
    task.status = "interrupted"
    task.completed_at = None
    task.error = error
    return _TransitionOutcome(mutated=True, interrupted=True)


def _fail_task(task: WorkflowTask, *, now: datetime, error: str) -> None:
    task.status = "failed"
    task.completed_at = now
    task.error = error
