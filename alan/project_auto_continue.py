from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from alan import dispatch_provenance as provenance_mod
from alan import merge_gate as merge_gate_mod
from alan import project as project_mod
from alan import project_roadmap as project_roadmap_mod
from alan import workspace as workspace_mod
from alan.auto_continue import (
    _project_autonomy_is_auto_continue,  # pyright: ignore[reportPrivateUsage]
    auto_continue_disabled,
)
from alan.dispatch import DispatchOutcome, EnqueueWorkflowFn, dispatch_team_core
from alan.events import PubSub
from alan.handover import list_handovers
from alan.project import Project
from alan.project_budget import budget_exhausted, budget_state, resolve_burn_for_project
from alan.project_roadmap import RoadmapItem
from alan.proposal_items import ProposalItem, next_item_proposal_item
from alan.queue import Queue
from alan.roots import logs_root
from alan.teams import TEAMS
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.project_auto_continue")

PROJECT_ADVANCE_AUDIT_FILE = "project_advance_audit.jsonl"
_ENTRY_TEAM = "implementation"
_BUDGET_SKIP_REASON = (
    "Project advance skipped: project budget is exhausted (remaining_usd <= 0)."
)
_MERGE_GATE_SKIP_REASON = (
    "advance_project both creates the workspace and dispatches it in the same "
    "step, so there is no prior handover and no pull-request number to gate "
    "against yet."
)
_DELIVERED_STATUSES: frozenset[Status] = frozenset({Status.COMPLETED, Status.ARCHIVED})

# Serializes "is this item already attempted?" against "seed + dispatch it" as
# one indivisible step (module-level, not per-project — alan is single-process
# and advancement volume is low). Both the advance endpoint and the
# dispatcher's terminal-transition seam funnel through advance_project(), so
# two near-simultaneous callers can never start the same next item twice.
_LOCK = threading.Lock()


def _workspace_delivered(ws: Workspace) -> bool:
    """Terminal status AND a merged PR — via any handover's `pr_number`.

    Deliberately handover-based rather than the DoD-clause evidence
    `alan.routes.projects._roadmap_is_delivered` scans: a workspace created
    by `advance_project` (team-only dispatch, no task DAG) never gets
    DoD-clause evidence stamped, but `pr_merge`'s terminal handover always
    carries `pr_number` regardless of DAG shape, so this is the general
    signal that also covers the DAG case (any DoD-clause evidence traces back
    to a handover that set the same `pr_number`).
    """
    if ws.meta.status not in _DELIVERED_STATUSES:
        return False
    return any(h.pr_number is not None for h in list_handovers(ws))


def _project_workspaces(project_id: str, workspaces_root: Path | None) -> list[Workspace]:
    return [
        ws
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.project_id == project_id
    ]


def _stamped_by_item(
    project_id: str, workspaces_root: Path | None
) -> dict[str, list[Workspace]]:
    by_item: dict[str, list[Workspace]] = {}
    for ws in _project_workspaces(project_id, workspaces_root):
        if ws.meta.roadmap_item_id is not None:
            by_item.setdefault(ws.meta.roadmap_item_id, []).append(ws)
    return by_item


def next_unattempted_item(
    project_id: str, *, workspaces_root: Path | None = None
) -> RoadmapItem | None:
    """First roadmap item, in stored order, with zero workspaces ever stamped for it.

    Gated on cold-start: a project with zero stamped workspaces anywhere
    returns `None` here, never `items[0]` — the very first roadmap item is
    never seeded through this deterministic path; starting it stays a
    human-initiated action outside this module. Attempted-but-undelivered
    (failed, awaiting human, still in flight) still counts as attempted and is
    never re-selected here. Always reads fresh on-disk workspace state
    (`workspace_mod.list_workspaces`) — never a cached `WorkspaceIndex` — so a
    caller holding `_LOCK` sees any just-seeded sibling.
    """
    items = project_roadmap_mod.read_items(project_id)
    if not items:
        return None
    by_item = _stamped_by_item(project_id, workspaces_root)
    if not by_item:
        return None
    for item in items:
        if not by_item.get(item.id):
            return item
    return None


def next_proposable_item(
    project_id: str, *, workspaces_root: Path | None = None
) -> RoadmapItem | None:
    """`next_unattempted_item`, gated on every prior stamped item having delivered.

    Inherits the same cold-start gate as `next_unattempted_item`: a project
    with no stamped workspace at all returns `None`, never `items[0]`. Returns
    `None` the moment any stamped item hasn't delivered yet (still in flight,
    failed, or awaiting a human) — that item stands as a human-attention
    signal instead of being silently skipped past.
    """
    items = project_roadmap_mod.read_items(project_id)
    if not items:
        return None
    by_item = _stamped_by_item(project_id, workspaces_root)
    if not by_item:
        return None
    for item in items:
        stamped = by_item.get(item.id)
        if not stamped:
            return item
        if not any(_workspace_delivered(ws) for ws in stamped):
            return None
    return None


def propose_next_item_proposal_item(
    project: Project,
    *,
    workspaces_root: Path | None = None,
    workspace_index: WorkspaceIndex | None = None,
) -> ProposalItem | None:
    """Pull-computed propose-mode card — no persisted pending record.

    Idempotent by construction: once the proposed item is actually started
    (stamped with a workspace), `next_proposable_item` naturally stops
    returning it, so there is no separate de-dup bookkeeping to keep in sync.
    """
    if project.autonomy != "propose":
        return None
    item = next_proposable_item(project.id, workspaces_root=workspaces_root)
    if item is None:
        return None
    burn = resolve_burn_for_project(project, workspace_index)
    if burn is not None and budget_state(burn) == "paused":
        return None
    return next_item_proposal_item(project, item)


@dataclass(frozen=True)
class AdvanceOutcome:
    workspace: Workspace
    item: RoadmapItem
    dispatch: DispatchOutcome


def advance_project(
    project_id: str,
    *,
    workspaces_root: Path | None,
    queue: Queue,
    pubsub: PubSub,
    enqueue_workflow: EnqueueWorkflowFn,
) -> AdvanceOutcome | None:
    """Atomically pick the next un-attempted item and start it, unattended.

    Uses `_ENTRY_TEAM` ("implementation") directly with the item's stored
    text as the complete brief — no AI planning step. The seed
    (`workspace_mod.create`, which durably stamps `roadmap_item_id`) and the
    dispatch both run inside `_LOCK`, so a concurrent caller's
    `next_unattempted_item` call always observes this item as attempted. If
    dispatch throws, the seeded workspace is rolled back (deleted) so the
    item's "unattempted" slot is freed rather than left permanently consumed
    by a dispatch-time bug (e.g. a transient enqueue error) — the caller sees
    the raised exception and can retry the whole call.
    """
    project = project_mod.cached_read(project_id, {})
    if project is None:
        return None
    with _LOCK:
        item = next_unattempted_item(project_id, workspaces_root=workspaces_root)
        if item is None:
            return None
        ws = workspace_mod.create(
            item.title,
            item.text,
            root=workspaces_root,
            project_id=project_id,
            roadmap_item_id=item.id,
            engine_key=project.engine_key,
            provenance=provenance_mod.DispatchProvenance(
                surface=provenance_mod.DispatchSurface.PROJECT_AUTO_CONTINUE
            ),
        )
        try:
            dispatch = dispatch_team_core(
                workspace=ws,
                task_id=ws.task_id,
                team=TEAMS[_ENTRY_TEAM],
                queue=queue,
                pubsub=pubsub,
                enqueue_workflow=enqueue_workflow,
                merge_gate=merge_gate_mod.MergeGateDecision.skipped(_MERGE_GATE_SKIP_REASON),
            )
        except Exception:  # noqa: BLE001 - any failure here must roll back the seeded workspace
            try:
                workspace_mod.delete(ws.task_id, root=workspaces_root)
            except Exception:  # noqa: BLE001 - rollback is best-effort; never mask the original error
                logger.warning(
                    "advance_project: rollback delete failed for %s", ws.task_id, exc_info=True
                )
            raise
    return AdvanceOutcome(workspace=ws, item=item, dispatch=dispatch)


def _record_advance_audit(ws: Workspace, outcome: AdvanceOutcome) -> None:
    append_jsonl_audit(
        logs_root() / PROJECT_ADVANCE_AUDIT_FILE,
        {
            "project_id": ws.meta.project_id,
            "from_task_id": ws.task_id,
            "roadmap_item_id": outcome.item.id,
            "new_task_id": outcome.workspace.task_id,
            "team": _ENTRY_TEAM,
            "inv_id": outcome.dispatch.inv_id,
        },
        log_label="project_advance",
    )


def _record_budget_skip_audit(ws: Workspace) -> None:
    append_jsonl_audit(
        logs_root() / PROJECT_ADVANCE_AUDIT_FILE,
        {
            "project_id": ws.meta.project_id,
            "from_task_id": ws.task_id,
            "skipped": True,
            "reason": _BUDGET_SKIP_REASON,
        },
        log_label="project_advance",
    )


def _project_active(project_id: str) -> bool:
    project = project_mod.cached_read(project_id, {})
    return project is not None and project.status != project_mod.ProjectStatus.ARCHIVED


def _unattended_project_id(ws: Workspace) -> str | None:
    """`ws.meta.project_id` iff every gate but the budget one passes, else `None`.

    Returning the id (not just a bool) is what lets the caller pass a
    type-narrowed `str` to `advance_project` without a redundant re-check. A
    workspace that isn't tied to a roadmap item, or that didn't actually
    deliver (terminal without a merged PR — a failure or an awaiting-human
    outcome), is left standing as a human-attention signal rather than
    advanced past; `_has_blocking_question` needs no separate check here
    because a delivered (terminal + merged PR) workspace can never also be an
    unresolved escalation. A project archived after the workspace was
    dispatched (autonomy dial still `auto_continue`) must not have new
    roadmap work seeded for it unattended, matching the `GET`/`POST` roadmap
    endpoints' own archived-project rejection.
    """
    if ws.meta.project_id is None or ws.meta.roadmap_item_id is None:
        return None
    if not _workspace_delivered(ws):
        return None
    if auto_continue_disabled() or not _project_autonomy_is_auto_continue(ws.meta.project_id):
        return None
    if not _project_active(ws.meta.project_id):
        return None
    return ws.meta.project_id


def maybe_advance_project(
    ws: Workspace,
    queue: Queue,
    pubsub: PubSub,
    enqueue_workflow: EnqueueWorkflowFn,
    *,
    workspaces_root: Path | None = None,
    workspace_index: WorkspaceIndex | None = None,
) -> bool:
    """Consider advancing `ws`'s project to its next roadmap item, unattended.

    Called once per delivered workspace from the dispatcher's terminal-summary
    seam, strictly AFTER any within-workspace `auto_continue.maybe_auto_continue`
    has already been resolved for the same transition — this is the only call
    site for the unattended path, so at most one advancement is ever
    considered per delivery, with no standalone loop.
    """
    project_id = _unattended_project_id(ws)
    if project_id is None:
        return False
    if budget_exhausted(project_id, workspace_index):
        _record_budget_skip_audit(ws)
        return False
    outcome = advance_project(
        project_id,
        workspaces_root=workspaces_root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=enqueue_workflow,
    )
    if outcome is None:
        return False
    _record_advance_audit(ws, outcome)
    return True


__all__ = [
    "PROJECT_ADVANCE_AUDIT_FILE",
    "AdvanceOutcome",
    "advance_project",
    "budget_exhausted",
    "maybe_advance_project",
    "next_proposable_item",
    "next_unattempted_item",
    "propose_next_item_proposal_item",
]
