from __future__ import annotations

import os

from alan import merge_gate as merge_gate_mod
from alan import project as project_mod
from alan.dispatch import (
    EnqueueWorkflowFn,
    _pinned_transition_graph,  # pyright: ignore[reportPrivateUsage]
    _transition_graph_nodes,  # pyright: ignore[reportPrivateUsage]
    dispatch_team_core,
)
from alan.events import PubSub
from alan.handover import Handover, list_handovers
from alan.incident import Incident, derive_incidents
from alan.project_budget import budget_exhausted as project_budget_exhausted
from alan.proposal_items import propose_team
from alan.queue import Queue
from alan.roots import logs_root
from alan.teams import AUTO_CONTINUE_TEAMS, TEAMS
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex

AUTO_CONTINUE_AUDIT_FILE = "auto_continue_audit.jsonl"
_DISABLE_ENV = "ALAN_AUTO_CONTINUE_DISABLE"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

_AUTO_CONTINUE_INSTRUCTIONS_TEMPLATE = (
    "Auto-continued: dispatch to {team}, per the prior handover's own "
    "to_team_suggestions/next_hop routing — no model call was used to pick the team. "
    "This project's autonomy is set to auto_continue, so no human approval was required."
)
_BUDGET_SKIP_REASON = "Auto-continue skipped: project budget is exhausted (remaining_usd <= 0)."
_MERGE_GATE_SKIP_REASON = (
    "auto_continue fires on the terminal-transition seam the dispatcher's own "
    "pr_merge gate has just evaluated for this same handover, inside the "
    "dispatcher's workspace lock — a second attempt against unchanged state "
    "buys no new information and must not run a forge read under this lock."
)


def auto_continue_disabled() -> bool:
    return os.environ.get(_DISABLE_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


def _project_autonomy_is_auto_continue(project_id: str | None) -> bool:
    if project_id is None:
        return False
    try:
        project = project_mod.read(project_id)
    except project_mod.ProjectError:
        return False
    return project.autonomy == "auto_continue"


def _has_blocking_question(latest: Handover | None) -> bool:
    if latest is None:
        return False
    return any(q.blocking and q.answer is None for q in latest.open_questions)


def _dispatchable_task_id(ws: Workspace) -> str:
    root_ids = [t.id for t in ws.meta.tasks if not t.depends_on]
    return root_ids[0] if root_ids else ws.task_id


def _record_audit(ws: Workspace, incident: Incident, team: str, inv_id: str) -> None:
    append_jsonl_audit(
        logs_root() / AUTO_CONTINUE_AUDIT_FILE,
        {
            "task_id": ws.task_id,
            "workspace_id": ws.task_id,
            "project_id": ws.meta.project_id,
            "team": team,
            "incident_class": incident.incident_class.value,
            "reason": incident.title,
            "inv_id": inv_id,
        },
        log_label="auto_continue",
    )


def _record_budget_skip_audit(ws: Workspace) -> None:
    append_jsonl_audit(
        logs_root() / AUTO_CONTINUE_AUDIT_FILE,
        {
            "task_id": ws.task_id,
            "workspace_id": ws.task_id,
            "project_id": ws.meta.project_id,
            "skipped": True,
            "reason": _BUDGET_SKIP_REASON,
        },
        log_label="auto_continue",
    )


def _budget_exhausted(project_id: str | None, workspace_index: WorkspaceIndex | None) -> bool:
    return project_budget_exhausted(project_id, workspace_index)


def _off_roster(ws: Workspace, team_name: str) -> bool:
    roster = _transition_graph_nodes(_pinned_transition_graph(ws))
    return bool(roster) and team_name not in roster


def _resolvable_auto_continue_team(latest: Handover | None, ws: Workspace) -> str | None:
    team_name = propose_team(latest)
    if team_name is None or team_name not in AUTO_CONTINUE_TEAMS:
        return None
    if _off_roster(ws, team_name):
        return None
    return team_name


def _resolve_auto_continue_target(
    ws: Workspace, handovers: list[Handover]
) -> tuple[Incident, str] | None:
    """The escalation/incident/team gating auto-continue shares with propose-mode.

    Escalations carrying a blocking question are a human *decision*, never an
    auto-continue target, regardless of what a resolvable team might suggest.
    """
    latest = handovers[-1] if handovers else None
    if ws.meta.status == Status.ESCALATED and _has_blocking_question(latest):
        return None
    incidents = derive_incidents(workspaces=[ws], handovers_by_workspace={ws.task_id: handovers})
    if not incidents:
        return None
    team_name = _resolvable_auto_continue_team(latest, ws)
    if team_name is None:
        return None
    return incidents[0], team_name


def maybe_auto_continue(
    ws: Workspace,
    queue: Queue,
    pubsub: PubSub,
    enqueue_workflow: EnqueueWorkflowFn,
    *,
    workspace_index: WorkspaceIndex | None = None,
) -> bool:
    """Dispatch `ws` unattended when its project opted into auto_continue.

    Mirrors `alan._dispatcher_base._maybe_trigger_investigator`: fired at the same
    transition hook, deriving its own single-workspace incident inline. Reuses
    propose-mode's deterministic `propose_team` resolution — no model call —
    so this is a policy gate over the existing dispatch path, not a new one.
    Returns True iff a dispatch was made, so the caller can skip triggering
    the investigator for the same transition.

    When `workspace_index` is supplied, a project that has burned through its
    P4.4 budget dial (remaining_usd <= 0) is skipped — a budget fyi audit row
    is recorded and the caller falls through to the investigator/propose-mode
    path instead, same as any other declined target.
    """
    if auto_continue_disabled() or not _project_autonomy_is_auto_continue(ws.meta.project_id):
        return False
    if _budget_exhausted(ws.meta.project_id, workspace_index):
        _record_budget_skip_audit(ws)
        return False
    target = _resolve_auto_continue_target(ws, list_handovers(ws))
    if target is None:
        return False
    incident, team_name = target
    outcome = dispatch_team_core(
        workspace=ws,
        task_id=_dispatchable_task_id(ws),
        team=TEAMS[team_name],
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=enqueue_workflow,
        merge_gate=merge_gate_mod.MergeGateDecision.skipped(_MERGE_GATE_SKIP_REASON),
        instructions=_AUTO_CONTINUE_INSTRUCTIONS_TEMPLATE.format(team=team_name),
        instructions_origin="generated",
    )
    _record_audit(ws, incident, team_name, outcome.inv_id)
    return True


__all__ = [
    "AUTO_CONTINUE_AUDIT_FILE",
    "auto_continue_disabled",
    "maybe_auto_continue",
]
