from __future__ import annotations

import logging

from alan._reconciler_never_started import team_supports_resume
from alan.queue import Job, Queue, QueueError
from alan.workflow import reset_failed_nodes_for_rerun
from alan.workflow_interrupt import interrupt_workflow_on_disk
from alan.workspace import Workspace

logger = logging.getLogger("alan.reconciler")

__all__ = ["requeue_paused_job"]

_PAUSED_ERROR = "task paused"


def requeue_paused_job(job: Job, queue: Queue, workspace: Workspace | None) -> None:
    inv_id = job.triggered_by_invocation_id
    if workspace is None or inv_id is None:
        _requeue_pending(job, queue)
        return
    if team_supports_resume(job.team) and _requeue_for_resume(job, queue, workspace, inv_id):
        return
    reset_failed_nodes_for_rerun(workspace, inv_id)
    _requeue_pending(job, queue)


def _requeue_for_resume(job: Job, queue: Queue, workspace: Workspace, inv_id: str) -> bool:
    try:
        doc = interrupt_workflow_on_disk(
            workspace, inv_id, error=_PAUSED_ERROR, resumable=True, recover_finalized=True
        )
    except Exception:  # noqa: BLE001 - IO trouble falls back to the fresh-rerun path
        logger.exception("reconciler: resumable interrupt failed for paused job %d", job.id)
        return False
    if doc is None or not any(task.status == "interrupted" for task in doc.tasks):
        return False
    try:
        queue.requeue_for_resume(job.id, inv_id)
    except QueueError:
        logger.warning("reconciler: failed to park paused job %d for resume", job.id)
    return True


def _requeue_pending(job: Job, queue: Queue) -> None:
    try:
        queue.requeue_pending(job.id)
    except QueueError:
        logger.warning("reconciler: failed to park paused job %d to pending", job.id)
