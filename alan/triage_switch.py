from __future__ import annotations

import logging
from pathlib import Path

from alan import default_profile, reconciler
from alan import workspace as workspace_mod
from alan.queue import Queue

logger = logging.getLogger(__name__)

_TRIAGE_TEAM = "triage"


def start_triage_sweep(*, queue: Queue, workspaces_root: Path | None) -> None:
    if default_profile.triage_sweep_enabled():
        reconciler.seed_triage_sweep(queue=queue, workspaces_root=workspaces_root)
        return
    _disable_triage_sweep(queue, workspaces_root)


def _disable_triage_sweep(queue: Queue, workspaces_root: Path | None) -> None:
    try:
        removed = _delete_triage_jobs(queue, workspaces_root)
    except Exception:  # noqa: BLE001
        logger.exception("triage_switch: failed to clear triage jobs while switch is off")
        return
    logger.info("triage_switch: triage switch off; removed %d job(s)", removed)


def _delete_triage_jobs(queue: Queue, workspaces_root: Path | None) -> int:
    triage_workspaces = [
        ws
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.team == _TRIAGE_TEAM
    ]
    return sum(queue.delete_jobs(ws.task_id) for ws in triage_workspaces)
