from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from alan import acceptance, lifecycle
from alan import park as park_mod
from alan import workspace as workspace_mod
from alan.events import PubSub
from alan.handover import Handover
from alan.loop_guard import REVISIT_CAP
from alan.queue import Job, JobStatus, Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import Team, TeamEvent
from alan.terminal_outcome import has_merged_sibling
from alan.workspace import Status, Task, TaskStatus, Workspace
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.workspace_dag")

_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED}
)

ApplyInjection = Callable[[Workspace, str, Handover], Workspace]
EvaluateRequirements = Callable[..., Workspace]
ScheduleManagerSummary = Callable[..., None]
EnqueueWorkflow = Callable[..., tuple[Job, str]]


def all_leaves_done(ws: Workspace) -> bool:
    return all(t.status in _TERMINAL_TASK_STATUSES for t in ws.meta.tasks)


def incomplete_subtask_ids(ws: Workspace) -> tuple[str, ...]:
    return tuple(t.id for t in ws.meta.tasks if t.status not in _TERMINAL_TASK_STATUSES)


def all_tasks_completed(ws: Workspace) -> bool:
    return bool(ws.meta.tasks) and all(
        t.status == TaskStatus.COMPLETED for t in ws.meta.tasks
    )


def has_awaiting_subtask(ws: Workspace) -> bool:
    return any(t.status == TaskStatus.AWAITING_INPUT for t in ws.meta.tasks)


@dataclass(frozen=True)
class ParkRollback:
    park_reason: park_mod.ParkReason | None
    blockers: list[park_mod.Blocker]


def stamp_for_advance(
    ws: Workspace, block_class: str | None
) -> tuple[Workspace, ParkRollback | None]:
    was_escalated = ws.meta.status == Status.ESCALATED
    rollback = (
        ParkRollback(park_reason=ws.meta.park_reason, blockers=list(ws.meta.blockers))
        if was_escalated
        else None
    )
    target = lifecycle.advance_target(
        ws.meta.status,
        has_block_class=block_class is not None,
        has_awaiting_subtask=was_escalated and has_awaiting_subtask(ws),
    )
    if target is None:
        return ws, rollback
    return workspace_mod.transition(ws, target, merge_block_class=block_class), rollback


def restore_escalated(ws: Workspace, rollback: ParkRollback) -> None:
    workspace_mod.transition(
        ws,
        Status.ESCALATED,
        park_reason=rollback.park_reason,
        blockers=rollback.blockers,
    )


_ACCEPTANCE_VACUITY_AUDIT_FILE = "acceptance_vacuity_audit.jsonl"


def _maybe_audit_vacuity(ws: Workspace) -> None:
    if not acceptance.requirements_unbound(ws.meta):
        return
    append_jsonl_audit(
        logs_root() / _ACCEPTANCE_VACUITY_AUDIT_FILE,
        {"task_id": ws.task_id, "workspace_id": ws.workspace_id},
        log_label="acceptance_vacuity",
    )


def workspace_terminal_status(ws: Workspace) -> Status | None:
    if not all_leaves_done(ws):
        return None
    if not ws.meta.tasks:
        if acceptance.all_clauses_satisfied(ws.meta):
            _maybe_audit_vacuity(ws)
            return Status.COMPLETED
        return Status.FAILED
    if all_tasks_completed(ws):
        if acceptance.all_clauses_satisfied(ws.meta):
            _maybe_audit_vacuity(ws)
            return Status.COMPLETED
        return Status.FAILED
    if has_merged_sibling(ws):
        return Status.PARTIAL
    return Status.FAILED


def _resolve_evidence(handover: Handover | None, evidence: str | None) -> str | None:
    if evidence is not None:
        return evidence
    if handover is None or handover.pr_number is None:
        return None
    return f"PR #{handover.pr_number}"


def stamp_evidence(
    ws: Workspace,
    task_id: str,
    handover: Handover | None,
    *,
    evidence: str | None = None,
) -> Workspace:
    resolved = _resolve_evidence(handover, evidence)
    if resolved is None:
        return ws
    task = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if task is None or not task.dod_clauses:
        return ws
    return workspace_mod.record_task_evidence(ws, task_id, resolved)


def _partition_terminal_ids(ws: Workspace) -> tuple[frozenset[str], frozenset[str]]:
    completed: set[str] = set()
    failed: set[str] = set()
    for t in ws.meta.tasks:
        if t.status == TaskStatus.COMPLETED:
            completed.add(t.id)
        elif t.status == TaskStatus.FAILED:
            failed.add(t.id)
    return frozenset(completed), frozenset(failed)


def _can_propagate_failure(task: Task, failed_ids: set[str]) -> bool:
    return (
        task.id not in failed_ids
        and task.status not in _TERMINAL_TASK_STATUSES
        and any(dep in failed_ids for dep in task.depends_on)
    )


def propagate_task_failure(ws: Workspace, failed_task_id: str) -> Workspace:
    failed_ids: set[str] = {failed_task_id}
    changed = True
    while changed:
        changed = False
        for t in ws.meta.tasks:
            if _can_propagate_failure(t, failed_ids):
                failed_ids.add(t.id)
                changed = True
    for tid in failed_ids:
        ws = workspace_mod.update_task_status(ws, tid, TaskStatus.FAILED)
    return ws


def task_blocked(
    task: Task,
    completed_ids: frozenset[str],
    failed_ids: frozenset[str],
    workspace_id: str,
    *,
    queue: Queue,
) -> bool:
    if any(dep not in completed_ids or dep in failed_ids for dep in task.depends_on):
        return True
    return bool(
        queue.list_jobs(task_id=task.id, workspace_id=workspace_id, status=JobStatus.PENDING)
        or queue.list_jobs(task_id=task.id, workspace_id=workspace_id, status=JobStatus.RUNNING)
    )


def ready_tasks(ws: Workspace, *, queue: Queue) -> list[Task]:
    completed, failed = _partition_terminal_ids(ws)
    return [
        t for t in ws.meta.tasks
        if t.status in (TaskStatus.PENDING, TaskStatus.READY)
        and not task_blocked(t, completed, failed, ws.workspace_id, queue=queue)
    ]


def enqueue_ready(
    ws: Workspace,
    *,
    queue: Queue,
    pubsub: PubSub,
    teams: dict[str, Team],
    enqueue_workflow: EnqueueWorkflow,
) -> None:
    for task in ready_tasks(ws, queue=queue):
        team_obj = teams.get(task.team or "")
        if team_obj is None:
            logger.warning(
                "enqueue_ready: no team for task %s in workspace %s",
                task.id,
                ws.workspace_id,
            )
            continue
        enqueue_workflow(
            queue=queue,
            pubsub=pubsub,
            workspace=ws,
            team=team_obj,
            prior=None,
            task_id=task.id,
            instructions=task.instructions,
        )


def dag_has_active_work(ws: Workspace, *, queue: Queue) -> bool:
    active_ids = {
        t.id for t in ws.meta.tasks if t.status not in _TERMINAL_TASK_STATUSES
    }
    if not active_ids:
        return False
    pending = {
        j.task_id
        for j in queue.list_jobs_for_workspace(ws.workspace_id, status=JobStatus.PENDING)
    }
    running = {
        j.task_id
        for j in queue.list_jobs_for_workspace(ws.workspace_id, status=JobStatus.RUNNING)
    }
    return bool(active_ids & (pending | running))


def maybe_surface_stall(
    ws: Workspace,
    completed_task_id: str,
    *,
    queue: Queue,
    pubsub: PubSub,
    schedule_manager_summary: ScheduleManagerSummary,
    workspace_index: WorkspaceIndex | None = None,
) -> None:
    non_terminal = [t for t in ws.meta.tasks if t.status not in _TERMINAL_TASK_STATUSES]
    if not non_terminal:
        return
    if has_awaiting_subtask(ws) or dag_has_active_work(ws, queue=queue):
        return
    reason = (
        f"DAG stalled after completing {completed_task_id!r}: "
        f"{len(non_terminal)} subtask(s) remain but none have active work"
    )
    logger.warning("dag_stall: workspace=%s %s", ws.workspace_id, reason)
    try:
        ws = workspace_mod.transition(
            ws,
            Status.AWAITING_INPUT,
            park_reason=park_mod.ParkReason.STALL,
            blockers=park_mod.synthetic_blocker(reason),
        )
    except OSError:
        logger.exception(
            "maybe_surface_stall: transition failed for %s", ws.workspace_id
        )
        return
    schedule_manager_summary(
        ws, None, pubsub, queue, stop_reason=reason, workspace_index=workspace_index
    )
    pubsub.publish(
        ws.workspace_id,
        TeamEvent("dag_stall", {"reason": reason, "workspace_id": ws.workspace_id}),
    )


# ---------------------------------------------------------------------------
# Frozen-chain detect, recover, park
#
# A subtask that escalates or awaits human input with pending descendants
# freezes the DAG forever: those descendants can never become ready, so the
# workspace never reaches a terminal status. frozen_chain() detects that one
# reachable shape; maybe_recover_frozen_chain() bounds recovery with a revisit
# cap independent of the escalation's own handover_chain_id, so a task that
# already exhausted the per-chain loop-guard cap before escalating still gets
# bounded retries here.
# ---------------------------------------------------------------------------

_FROZEN_CHAIN_STATUSES: frozenset[Status] = frozenset({Status.ESCALATED, Status.AWAITING_INPUT})
_FROZEN_CHAIN_NAMESPACE = "frozen-chain"


@dataclass(frozen=True)
class FrozenChainFinding:
    ancestor_id: str
    blocked_descendant_ids: tuple[str, ...]


def _stuck_ancestor_candidates(ws: Workspace) -> list[Task]:
    return [
        t
        for t in ws.meta.tasks
        if t.status == TaskStatus.AWAITING_INPUT and t.blocked_handover_id is not None
    ]


def _ancestor_has_in_flight_job(queue: Queue, workspace_id: str, ancestor_id: str) -> bool:
    return bool(
        queue.list_jobs(task_id=ancestor_id, workspace_id=workspace_id, status=JobStatus.PENDING)
        or queue.list_jobs(task_id=ancestor_id, workspace_id=workspace_id, status=JobStatus.RUNNING)
    )


def _depends_only_on_ancestor_or_completed(
    task: Task, ancestor_id: str, completed_ids: set[str]
) -> bool:
    return all(dep == ancestor_id or dep in completed_ids for dep in task.depends_on)


def _blocked_descendants(ws: Workspace, ancestor_id: str) -> tuple[str, ...]:
    completed = {t.id for t in ws.meta.tasks if t.status == TaskStatus.COMPLETED}
    return tuple(
        t.id
        for t in ws.meta.tasks
        if t.status == TaskStatus.PENDING
        and ancestor_id in t.depends_on
        and _depends_only_on_ancestor_or_completed(t, ancestor_id, completed)
    )


def _find_frozen_ancestor(ws: Workspace, *, queue: Queue) -> FrozenChainFinding | None:
    for ancestor in _stuck_ancestor_candidates(ws):
        if _ancestor_has_in_flight_job(queue, ws.workspace_id, ancestor.id):
            continue
        descendants = _blocked_descendants(ws, ancestor.id)
        if descendants:
            return FrozenChainFinding(ancestor_id=ancestor.id, blocked_descendant_ids=descendants)
    return None


def frozen_chain(ws: Workspace, *, queue: Queue) -> FrozenChainFinding | None:
    if ws.meta.status not in _FROZEN_CHAIN_STATUSES:
        return None
    if has_merged_sibling(ws):
        return None
    try:
        return _find_frozen_ancestor(ws, queue=queue)
    except Exception:  # noqa: BLE001 — detection must never break DAG advance
        logger.exception("frozen_chain: detection failed for workspace=%s", ws.workspace_id)
        return None


def _frozen_chain_revisit_count(ws: Workspace, ancestor_id: str) -> int:
    key = workspace_mod.revisit_key(
        ws.workspace_id, ancestor_id, _FROZEN_CHAIN_NAMESPACE, _FROZEN_CHAIN_NAMESPACE
    )
    return ws.meta.revisit_counts.get(key, 0)


def _resume_workspace_if_unblocked(ws: Workspace) -> Workspace:
    if has_awaiting_subtask(ws):
        return ws
    return workspace_mod.transition(ws, Status.ACTIVE)


def _redispatch_frozen_ancestor(
    ws: Workspace,
    finding: FrozenChainFinding,
    *,
    queue: Queue,
    pubsub: PubSub,
    teams: dict[str, Team],
    enqueue_workflow: EnqueueWorkflow,
) -> Workspace:
    ws = workspace_mod.increment_revisit_count(
        ws, _FROZEN_CHAIN_NAMESPACE, _FROZEN_CHAIN_NAMESPACE, task_id=finding.ancestor_id
    )
    ws = workspace_mod.update_task_status(
        ws, finding.ancestor_id, TaskStatus.PENDING, clear_blocked_handover_id=True
    )
    ws = _resume_workspace_if_unblocked(ws)
    enqueue_ready(ws, queue=queue, pubsub=pubsub, teams=teams, enqueue_workflow=enqueue_workflow)
    logger.warning(
        "frozen_chain: re-dispatched stuck ancestor=%s workspace=%s",
        finding.ancestor_id, ws.workspace_id,
    )
    return ws


def _frozen_chain_blockers(finding: FrozenChainFinding) -> list[park_mod.Blocker]:
    descendants = ", ".join(finding.blocked_descendant_ids)
    return [
        park_mod.Blocker(
            kind="reason",
            name=finding.ancestor_id,
            detail=(
                f"frozen chain: ancestor task {finding.ancestor_id!r} blocks "
                f"{len(finding.blocked_descendant_ids)} descendant(s): {descendants}"
            ),
            instruction=(
                f"Re-dispatch or manually resolve task {finding.ancestor_id!r} — it is "
                f"blocking {descendants} from ever becoming ready."
            ),
        )
    ]


def _park_frozen_chain(
    ws: Workspace,
    finding: FrozenChainFinding,
    *,
    queue: Queue,
    pubsub: PubSub,
    schedule_manager_summary: ScheduleManagerSummary,
    workspace_index: WorkspaceIndex | None = None,
) -> None:
    reason = (
        f"frozen chain: ancestor task {finding.ancestor_id!r} exhausted the revisit cap "
        f"with {len(finding.blocked_descendant_ids)} descendant(s) permanently blocked"
    )
    ws = workspace_mod.transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=park_mod.ParkReason.FROZEN_CHAIN,
        blockers=_frozen_chain_blockers(finding),
    )
    logger.warning("frozen_chain: parked workspace=%s %s", ws.workspace_id, reason)
    schedule_manager_summary(
        ws, None, pubsub, queue, stop_reason=reason, workspace_index=workspace_index
    )
    pubsub.publish(
        ws.workspace_id,
        TeamEvent(
            "frozen_chain_park",
            {
                "reason": reason,
                "workspace_id": ws.workspace_id,
                "ancestor_id": finding.ancestor_id,
            },
        ),
    )


def maybe_recover_frozen_chain(
    ws: Workspace,
    *,
    queue: Queue,
    pubsub: PubSub,
    teams: dict[str, Team],
    enqueue_workflow: EnqueueWorkflow,
    schedule_manager_summary: ScheduleManagerSummary,
    workspace_index: WorkspaceIndex | None = None,
) -> bool:
    finding = frozen_chain(ws, queue=queue)
    if finding is None:
        return False
    try:
        if _frozen_chain_revisit_count(ws, finding.ancestor_id) < REVISIT_CAP:
            _redispatch_frozen_ancestor(
                ws, finding,
                queue=queue, pubsub=pubsub, teams=teams, enqueue_workflow=enqueue_workflow,
            )
        else:
            _park_frozen_chain(
                ws, finding,
                queue=queue, pubsub=pubsub, schedule_manager_summary=schedule_manager_summary,
                workspace_index=workspace_index,
            )
    except OSError:
        logger.exception(
            "maybe_recover_frozen_chain: recovery failed workspace=%s ancestor=%s",
            ws.workspace_id, finding.ancestor_id,
        )
        return False
    return True


def advance_workspace_dag(
    ws: Workspace,
    task_id: str,
    *,
    queue: Queue,
    pubsub: PubSub,
    teams: dict[str, Team],
    handover: Handover | None = None,
    evidence: str | None = None,
    precomputed_scenario_results: list[tuple[str, str]] | None = None,
    apply_injection: ApplyInjection,
    evaluate_requirements: EvaluateRequirements,
    schedule_manager_summary: ScheduleManagerSummary,
    enqueue_workflow: EnqueueWorkflow,
    workspace_index: WorkspaceIndex | None = None,
) -> None:
    if ws.meta.tasks:
        ws = workspace_mod.update_task_status(ws, task_id, TaskStatus.COMPLETED)
        ws = stamp_evidence(ws, task_id, handover, evidence=evidence)
    if handover and handover.injected_tasks:
        ws = apply_injection(ws, task_id, handover)
    enqueue_ready(ws, queue=queue, pubsub=pubsub, teams=teams, enqueue_workflow=enqueue_workflow)
    ws = evaluate_requirements(
        ws, handover, precomputed_scenario_results=precomputed_scenario_results
    )
    terminal_status = workspace_terminal_status(ws)
    if terminal_status is not None:
        ws = workspace_mod.transition(
            ws, terminal_status, clear_merge_block_class=True
        )
        synthesis = handover.full_synthesis if handover is not None else None
        schedule_manager_summary(ws, synthesis, pubsub, queue, workspace_index=workspace_index)
    elif not maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams=teams,
        enqueue_workflow=enqueue_workflow, schedule_manager_summary=schedule_manager_summary,
        workspace_index=workspace_index,
    ):
        maybe_surface_stall(
            ws, task_id,
            queue=queue, pubsub=pubsub,
            schedule_manager_summary=schedule_manager_summary,
            workspace_index=workspace_index,
        )
