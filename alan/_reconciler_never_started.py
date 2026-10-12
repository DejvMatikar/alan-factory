from __future__ import annotations

from typing import Literal

import alan.teams as teams_mod
from alan import workflow as workflow_mod
from alan.queue import Job, Queue, QueueError
from alan.workspace import Status, Workspace

RerunOutcome = Literal["rerun", "exhausted", "not_applicable"]

RERUN_CAP = 3

RERUN_CAP_MESSAGE = (
    "A service restart interrupted this run before any worker session was "
    "captured, and automatic re-run exhausted its 3 attempts. Re-dispatch "
    "this workspace from its own page."
)


def team_supports_resume(team_name: str) -> bool:
    team = teams_mod.TEAMS.get(team_name)
    if team is None:
        return False
    return bool(getattr(team, "supports_resume", False))


def team_supports_rederive(team_name: str) -> bool:
    team = teams_mod.TEAMS.get(team_name)
    if team is None:
        return False
    return bool(getattr(team, "restart_rederivable", False))


def has_captured_session(workspace: Workspace, inv_id: str) -> bool:
    try:
        doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    except (workflow_mod.WorkflowError, OSError):
        return True
    return any(task.claude_session_id is not None for task in doc.tasks)


def has_completed_node(workspace: Workspace, inv_id: str) -> bool:
    try:
        doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    except (workflow_mod.WorkflowError, OSError):
        return True
    return any(task.status == "success" for task in doc.tasks)


def resume_would_lose_captured_session(
    job: Job, workspace: Workspace, inv_id: str, *, resume_was_attempted: bool
) -> bool:
    if resume_was_attempted:
        return False
    return team_supports_resume(job.team) and has_captured_session(workspace, inv_id)


def _eligible_team(team_name: str) -> bool:
    return team_supports_resume(team_name) and not team_supports_rederive(team_name)


def _rerun_target(job: Job, ws: Workspace | None) -> tuple[Workspace, str] | None:
    if ws is None or ws.meta.status == Status.ARCHIVED:
        return None
    inv_id = job.triggered_by_invocation_id
    if inv_id is None or not _eligible_team(job.team):
        return None
    if has_captured_session(ws, inv_id):
        return None
    if has_completed_node(ws, inv_id):
        return None
    return ws, inv_id


def _prepare_rerun(ws: Workspace, inv_id: str) -> bool:
    workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)
    try:
        doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    except (workflow_mod.WorkflowError, OSError):
        return False
    return all(task.status == "pending" for task in doc.tasks)


def try_rerun(job: Job, queue: Queue, ws: Workspace | None) -> RerunOutcome:
    target = _rerun_target(job, ws)
    if target is None:
        return "not_applicable"
    target_ws, inv_id = target
    if job.interruption_count >= RERUN_CAP:
        return "exhausted"
    if not _prepare_rerun(target_ws, inv_id):
        return "not_applicable"
    try:
        queue.requeue_pending(job.id)
    except QueueError:
        return "not_applicable"
    return "rerun"


__all__ = [
    "RERUN_CAP",
    "RERUN_CAP_MESSAGE",
    "RerunOutcome",
    "has_captured_session",
    "has_completed_node",
    "resume_would_lose_captured_session",
    "team_supports_rederive",
    "team_supports_resume",
    "try_rerun",
]
