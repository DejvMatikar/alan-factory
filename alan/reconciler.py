from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import alan.teams as teams_mod
from alan import _reconciler_never_started as never_started
from alan import dispatch_provenance as provenance_mod
from alan import park as park_mod
from alan import retention_registry, workflow_interrupt
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan._reconciler_never_started import team_supports_rederive as _team_supports_rederive
from alan._reconciler_never_started import team_supports_resume as _team_supports_resume
from alan._reconciler_orphans import RESTART_PARK_MESSAGE, reconcile_orphaned_workflows
from alan._reconciler_paused import requeue_paused_job
from alan.e2e_health_fixture import seed_e2e_health_fixtures
from alan.engine_config import (
    EngineConfig,
    canonical_scope_key,
    default_engine_config,
    registered_engines,
)
from alan.events import PubSub
from alan.handover import Handover, HandoverError, handover_paths, load_handover
from alan.queue import Job, JobStatus, Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import TeamEvent
from alan.teams._resume_audit import ResumeOutcome, record_resume_outcome
from alan.teams.dependency_sweep import ROLE_SPEC as _DEP_SWEEP_ROLE_SPEC
from alan.teams.triage import ROLE_SPECS as _TRIAGE_ROLE_SPECS
from alan.teams.triage import write_seed_failure_audit
from alan.workspace import Status, TaskStatus, Workspace

_TERMINAL_WORKSPACE_STATUSES: frozenset[Status] = frozenset({Status.COMPLETED, Status.FAILED})

logger = logging.getLogger("alan.reconciler")

_INTERRUPTED_ERROR = workflow_mod.INTERRUPTED_BY_RESTART_ERROR
_RESUME_AUDIT_FILE = "resume_audit.jsonl"
_REDERIVE_AUDIT_FILE = "rederive_audit.jsonl"
_REDERIVE_CAP = 3
_HANDOVER_REPLAY_AUDIT_FILE = "handover_replay_audit.jsonl"


def _empty_int_list() -> list[int]:
    return []


def _empty_error_list() -> list[tuple[int, str]]:
    return []


@dataclass
class ReconciliationReport:
    interrupted: list[int] = field(default_factory=_empty_int_list)
    resumed: list[int] = field(default_factory=_empty_int_list)
    replayed: list[int] = field(default_factory=_empty_int_list)
    rerun: list[int] = field(default_factory=_empty_int_list)
    errors: list[tuple[int, str]] = field(default_factory=_empty_error_list)


def _handover_to_replay(workspace: Workspace, inv_id: str) -> Handover | None:
    path = workflow_mod.workflow_path(workspace, inv_id)
    if not path.exists():
        return None
    try:
        doc = workflow_mod.load_workflow(path)
    except Exception:  # noqa: BLE001
        return None
    if doc.status != "success":
        return None
    return _latest_handover_for_inv(workspace, inv_id)


def _latest_handover_for_inv(workspace: Workspace, inv_id: str) -> Handover | None:
    for path in reversed(handover_paths(workspace)):
        try:
            h = load_handover(path)
        except (HandoverError, OSError):
            continue
        if h.from_invocation_id == inv_id:
            return h
    return None


def _has_successor_job(queue: Queue, handover_id: str, workspace_id: str) -> bool:
    return any(
        j.parent_handover_id == handover_id for j in queue.list_jobs_for_workspace(workspace_id)
    )


def _complete_zombie_row(queue: Queue, job_id: int) -> None:
    try:
        queue.complete(job_id)
    except Exception:  # noqa: BLE001
        logger.warning("reconciler: failed to complete zombie row %d", job_id, exc_info=True)


def _record_replay_audit(job: Job, inv_id: str, handover: Handover, *, outcome: str) -> None:
    append_jsonl_audit(
        logs_root() / _HANDOVER_REPLAY_AUDIT_FILE,
        {
            "task_id": job.task_id,
            "team": job.team,
            "job_id": job.id,
            "inv_id": inv_id,
            "handover_id": handover.handover_id,
            "outcome": outcome,
        },
        log_label="handover_replay",
    )


def _call_replay_routing(
    replay_routing: Callable[[Job, Handover], None], job: Job, handover: Handover
) -> bool:
    try:
        replay_routing(job, handover)
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "reconciler: replay_routing raised for job %d; falling through to fail", job.id
        )
        return False


def _replay_unrouted_handover(
    job: Job,
    queue: Queue,
    workspace: Workspace | None,
    replay_routing: Callable[[Job, Handover], None] | None,
) -> bool:
    if replay_routing is None:
        return False
    if workspace is None:
        return False
    inv_id = job.triggered_by_invocation_id
    if inv_id is None:
        return False
    handover = _handover_to_replay(workspace, inv_id)
    if handover is None:
        return False
    if workspace.meta.status in _TERMINAL_WORKSPACE_STATUSES:
        _complete_zombie_row(queue, job.id)
        _record_replay_audit(job, inv_id, handover, outcome="already_terminal")
        return True
    if _has_successor_job(queue, handover.handover_id, workspace.workspace_id):
        _complete_zombie_row(queue, job.id)
        _record_replay_audit(job, inv_id, handover, outcome="successor_exists")
        return True
    _record_replay_audit(job, inv_id, handover, outcome="handover_replay_invoked")
    return _call_replay_routing(replay_routing, job, handover)


def reconcile_on_startup(
    *,
    queue: Queue,
    workspaces_root: Path | None,
    pubsub: PubSub,
    replay_routing: Callable[[Job, Handover], None] | None = None,
) -> ReconciliationReport:
    # Ordering invariant: must precede the per-row sweep or it races its freshly-resumed rows.
    reconcile_orphaned_workflows(queue=queue, workspaces_root=workspaces_root, pubsub=pubsub)
    report = ReconciliationReport()
    for job in queue.list_running():
        _reconcile_one_job(job, queue, workspaces_root, pubsub, report, replay_routing)
    seed_e2e_health_fixtures(queue)
    return report


def _reconcile_one_job(
    job: Job,
    queue: Queue,
    workspaces_root: Path | None,
    pubsub: PubSub,
    report: ReconciliationReport,
    replay_routing: Callable[[Job, Handover], None] | None = None,
) -> None:
    workspace = _load_workspace(job, workspaces_root)
    if queue.is_task_paused(job.workspace_id):
        requeue_paused_job(job, queue, workspace)
        return
    if _replay_unrouted_handover(job, queue, workspace, replay_routing):
        report.replayed.append(job.id)
        return
    rerun_outcome = never_started.try_rerun(job, queue, workspace)
    if rerun_outcome == "rerun":
        _record_resume_audit(job, "rerun_never_started", job.triggered_by_invocation_id)
        report.rerun.append(job.id)
        return
    resume_attempt = _try_resume(job, queue, workspace, pubsub, report)
    if resume_attempt.succeeded:
        return
    if _try_rederive(
        job, queue, workspace, pubsub, resume_was_attempted=resume_attempt.attempted
    ):
        return
    _finalize_interrupted(
        job, queue, workspace, pubsub, report, cap_exhausted=rerun_outcome == "exhausted"
    )


def _resumable_marker(job: Job, workspace: Workspace | None) -> str | None:
    inv_id = job.triggered_by_invocation_id
    if workspace is None or inv_id is None or not _team_supports_resume(job.team):
        return None
    try:
        doc = workflow_mod.load_workflow(workflow_mod.workflow_path(workspace, inv_id))
    except (workflow_mod.WorkflowError, OSError):
        return None
    return inv_id if _has_interrupted_node(doc) else None


def _finalize_interrupted(
    job: Job,
    queue: Queue,
    workspace: Workspace | None,
    pubsub: PubSub,
    report: ReconciliationReport,
    *,
    cap_exhausted: bool,
) -> None:
    marker = _resumable_marker(job, workspace)
    try:
        queue.mark_interrupted_and_fail(
            job.id, _INTERRUPTED_ERROR, resume_inv_id=marker, clear_resume=marker is None
        )
    except Exception as exc:  # noqa: BLE001 - one bad row must not abort the sweep
        logger.exception("reconciler failed to flip job %d", job.id)
        report.errors.append((job.id, str(exc)))
        return
    outcome: ResumeOutcome = "rerun_cap_exhausted" if cap_exhausted else "interrupted_failed"
    _record_resume_audit(job, outcome, job.triggered_by_invocation_id)
    park_message = never_started.RERUN_CAP_MESSAGE if cap_exhausted else RESTART_PARK_MESSAGE
    _best_effort_followup(job, workspace=workspace, pubsub=pubsub, park_message=park_message)
    report.interrupted.append(job.id)


class _ResumeAttempt(NamedTuple):
    succeeded: bool
    attempted: bool


def _try_resume(
    job: Job,
    queue: Queue,
    workspace: Workspace | None,
    pubsub: PubSub,
    report: ReconciliationReport,
) -> _ResumeAttempt:
    eligible = _resume_target_if_eligible(job, workspace)
    if eligible is None:
        return _ResumeAttempt(succeeded=False, attempted=False)
    target_ws, inv_id = eligible
    if not _interrupt_for_resume(target_ws, inv_id):
        return _ResumeAttempt(succeeded=False, attempted=True)
    if not _requeue_for_resume(queue, job.id, inv_id):
        return _ResumeAttempt(succeeded=False, attempted=True)
    _record_resume_audit(job, "requeued_for_resume", inv_id)
    _publish_resuming(job, pubsub, inv_id)
    report.resumed.append(job.id)
    return _ResumeAttempt(succeeded=True, attempted=True)


def _resume_target_if_eligible(
    job: Job, workspace: Workspace | None
) -> tuple[Workspace, str] | None:
    if workspace is None:
        return None
    if workspace.meta.status == Status.ARCHIVED:
        return None
    inv_id = job.triggered_by_invocation_id
    if inv_id is None:
        return None
    if not _team_supports_resume(job.team):
        return None
    return workspace, inv_id


def _interrupt_for_resume(workspace: Workspace, inv_id: str) -> bool:
    try:
        doc = workflow_interrupt.interrupt_workflow_on_disk(
            workspace, inv_id, error=_INTERRUPTED_ERROR, resumable=True
        )
    except Exception:  # noqa: BLE001 - any IO trouble falls back to PR1 path
        logger.exception(
            "reconciler: resumable interrupt failed for %s/%s; falling back to mark-failed",
            workspace.task_id,
            inv_id,
        )
        return False
    return doc is not None and _has_interrupted_node(doc)


def _requeue_for_resume(queue: Queue, job_id: int, inv_id: str) -> bool:
    try:
        queue.requeue_for_resume(job_id, inv_id)
    except Exception as exc:  # noqa: BLE001 - queue write failure falls back to mark-failed
        logger.exception("reconciler: requeue_for_resume failed for job %d: %s", job_id, exc)
        return False
    return True


def _failed_rederive_count(queue: Queue, task_id: str, team: str) -> int:
    return sum(
        1 for j in queue.list_jobs(task_id=task_id, status=JobStatus.FAILED) if j.team == team
    )


def _publish_rederived(job: Job, pubsub: PubSub, new_inv_id: str) -> None:
    event_name = f"{job.team}_rederived"
    try:
        pubsub.publish(
            job.workspace_id or job.task_id,
            TeamEvent(
                event_name,
                {
                    "job_id": str(job.id),
                    "task_id": job.task_id,
                    "inv_id": new_inv_id,
                    "team": job.team,
                },
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: publish %s failed for job %d", event_name, job.id)


def _record_rederive_audit(job: Job, new_inv_id: str) -> None:
    append_jsonl_audit(
        logs_root() / _REDERIVE_AUDIT_FILE,
        {
            "task_id": job.task_id,
            "team": job.team,
            "job_id": job.id,
            "old_inv_id": job.triggered_by_invocation_id or "",
            "new_inv_id": new_inv_id,
            "outcome": "rederived",
        },
        log_label="rederive",
    )


def _resume_would_lose_captured_session(
    job: Job, workspace: Workspace, inv_id: str, *, resume_was_attempted: bool
) -> bool:
    return never_started.resume_would_lose_captured_session(
        job, workspace, inv_id, resume_was_attempted=resume_was_attempted
    )


def _rederive_eligible(
    job: Job, queue: Queue, workspace: Workspace, *, resume_was_attempted: bool
) -> str | None:
    inv_id = job.triggered_by_invocation_id
    if inv_id is None or not _team_supports_rederive(job.team):
        return None
    if _resume_would_lose_captured_session(
        job, workspace, inv_id, resume_was_attempted=resume_was_attempted
    ):
        return None
    if any(h == inv_id for h, _ in _terminal_handovers_for_team(workspace, job.team)):
        return None
    if _failed_rederive_count(queue, job.task_id, job.team) >= _REDERIVE_CAP:
        return None
    return inv_id


def _try_rederive(
    job: Job,
    queue: Queue,
    workspace: Workspace | None,
    pubsub: PubSub,
    *,
    resume_was_attempted: bool,
) -> bool:
    if workspace is None or workspace.meta.status == Status.ARCHIVED:
        return False
    inv_id = _rederive_eligible(job, queue, workspace, resume_was_attempted=resume_was_attempted)
    if inv_id is None:
        return False
    try:
        queue.mark_interrupted_and_fail(job.id, _INTERRUPTED_ERROR)
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: rederive failed to flip job %d", job.id)
        return False
    _interrupt_workflow(workspace, inv_id)
    try:
        team = teams_mod.TEAMS[job.team]
        tasks = team.plan_workflow(workspace, None)
        new_inv_id = workflow_mod.seed_pending_workflow(
            workspace,
            team=job.team,
            tasks=tasks,
            source_task_id=job.task_id,
        )
        queue.enqueue(
            job.task_id,
            job.team,
            workspace_id=job.workspace_id,
            parent_handover_id=job.parent_handover_id,
            triggered_by_invocation_id=new_inv_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: rederive enqueue failed for job %d", job.id)
        _fail_workspace(workspace)
        _publish_interrupted(job, pubsub)
        return True
    _record_rederive_audit(job, new_inv_id)
    _publish_rederived(job, pubsub, new_inv_id)
    return True


def _has_interrupted_node(doc: workflow_mod.WorkflowDoc) -> bool:
    return any(task.status == "interrupted" for task in doc.tasks)


def _best_effort_followup(
    job: Job,
    *,
    workspace: Workspace | None,
    pubsub: PubSub,
    park_message: str = RESTART_PARK_MESSAGE,
) -> None:
    """Run after the queue row is already FAILED. Any disk/IO error here is
    logged but never re-raised — the load-bearing zombie-prevention step
    (the queue flip) has already succeeded."""
    inv_id = job.triggered_by_invocation_id
    if workspace is not None and inv_id is not None:
        _interrupt_workflow(workspace, inv_id)
    if workspace is not None:
        _fail_workspace(workspace, park_message)
    _publish_interrupted(job, pubsub)


def _load_workspace(job: Job, workspaces_root: Path | None) -> Workspace | None:
    ws_id = job.workspace_id if job.workspace_id else job.task_id
    try:
        return workspace_mod.read(ws_id, root=workspaces_root)
    except workspace_mod.WorkspaceError:
        logger.warning("reconciler: workspace missing for job %d (%s)", job.id, ws_id)
        return None
    except Exception:  # noqa: BLE001 - best-effort follow-up; queue row already failed
        logger.exception("reconciler: workspace read failed for job %d (%s)", job.id, ws_id)
        return None


def _interrupt_workflow(workspace: Workspace, inv_id: str) -> None:
    try:
        workflow_interrupt.interrupt_workflow_on_disk(workspace, inv_id, error=_INTERRUPTED_ERROR)
    except Exception:  # noqa: BLE001 - best-effort follow-up; queue row already failed
        logger.exception(
            "reconciler: workflow interrupt failed for %s/%s", workspace.task_id, inv_id
        )


def _fail_workspace(workspace: Workspace, park_message: str = RESTART_PARK_MESSAGE) -> None:
    try:
        workspace_mod.transition(
            workspace,
            Status.AWAITING_INPUT,
            park_reason=park_mod.ParkReason.CRASH,
            blockers=park_mod.synthetic_blocker(park_message),
        )
    except Exception:  # noqa: BLE001 - best-effort follow-up; queue row already failed
        logger.exception(
            "reconciler: failed to flip workspace %s to AWAITING_INPUT", workspace.task_id
        )


def _publish_interrupted(job: Job, pubsub: PubSub) -> None:
    try:
        pubsub.publish(
            job.workspace_id or job.task_id,
            TeamEvent(
                "workspace_interrupted",
                {
                    "job_id": str(job.id),
                    "task_id": job.task_id,
                    "inv_id": job.triggered_by_invocation_id or "",
                    "team": job.team,
                },
            ),
        )
    except Exception:  # noqa: BLE001 - subscriber faults must not stall the sweep
        logger.exception("reconciler: publish workspace_interrupted failed for job %d", job.id)


def _publish_resuming(job: Job, pubsub: PubSub, inv_id: str) -> None:
    try:
        pubsub.publish(
            job.task_id,
            TeamEvent(
                "workspace_resuming",
                {"job_id": str(job.id), "inv_id": inv_id, "team": job.team},
            ),
        )
    except Exception:  # noqa: BLE001 - subscriber faults must not stall the sweep
        logger.exception("reconciler: publish workspace_resuming failed for job %d", job.id)


def _record_resume_audit(
    job: Job, outcome: ResumeOutcome, inv_id: str | None = None
) -> None:
    record_resume_outcome(
        logs_root() / _RESUME_AUDIT_FILE,
        task_id=job.task_id, team=job.team, inv_id=inv_id or "",
        outcome=outcome,
        extra={"job_id": job.id, "interruption_count": job.interruption_count + 1},
    )


_TRIAGE_TEAM = "triage"
_DEP_SWEEP_TEAM = "dependency_sweep"
DEP_SWEEP_TITLE = "Dependency Sweep"
_DEP_SWEEP_NEXT_RUN_META_KEY = "dep_sweep_next_run"
_TUESDAY = 1
_DEP_SWEEP_HOUR_UTC = 9


def reconcile_stalled_dags(queue: Queue, workspaces_root: Path | None) -> int:
    repaired = 0
    for ws in workspace_mod.list_workspaces(root=workspaces_root):
        if ws.meta.status != Status.ACTIVE:
            continue
        try:
            if _repair_stalled_dag(ws, queue):
                repaired += 1
        except Exception:  # noqa: BLE001
            logger.exception("reconciler: stalled-DAG repair failed for %s", ws.workspace_id)
    if repaired:
        logger.info("reconciler: repaired %d stalled multi-subtask workspace(s)", repaired)
    return repaired


def _build_in_degree(
    tasks: list[workspace_mod.Task],
) -> dict[str, int]:
    in_deg: dict[str, int] = {t.id: 0 for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep in in_deg:
                in_deg[t.id] += 1
    return in_deg


def _kahn_advance(
    tasks: list[workspace_mod.Task],
    node: workspace_mod.Task,
    in_deg: dict[str, int],
    ready: list[workspace_mod.Task],
) -> None:
    for t in tasks:
        if node.id in t.depends_on:
            in_deg[t.id] -= 1
            if in_deg[t.id] == 0:
                ready.append(t)


def _topo_remaining(
    tasks: list[workspace_mod.Task], result: list[workspace_mod.Task]
) -> list[workspace_mod.Task]:
    result_ids = {t.id for t in result}
    return [t for t in tasks if t.id not in result_ids]


def _injected_subtasks_topo(ws: Workspace) -> list[workspace_mod.Task]:
    tasks = [t for t in ws.meta.tasks if t.injection_depth > 0]
    in_deg = _build_in_degree(tasks)
    ready: list[workspace_mod.Task] = [t for t in tasks if in_deg[t.id] == 0]
    result: list[workspace_mod.Task] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        _kahn_advance(tasks, node, in_deg, ready)
    return result + _topo_remaining(tasks, result)


def _terminal_handovers_for_team(ws: Workspace, team_name: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for path in handover_paths(ws):
        try:
            h = load_handover(path)
        except (HandoverError, OSError):
            continue
        if h.from_team != team_name or h.handover_type != "terminal":
            continue
        if h.pr_number is not None:
            result.append((h.from_invocation_id, h.pr_number))
    return result


def _build_inv_to_task(ws: Workspace, queue: Queue) -> dict[str, str]:
    inv_to_task: dict[str, str] = {}
    for job in queue.list_jobs_for_workspace(ws.workspace_id):
        if job.team == "pr_merge" and job.triggered_by_invocation_id:
            inv_to_task[job.triggered_by_invocation_id] = job.task_id
    return inv_to_task


def _accum_delivery(
    deliveries: dict[str, int],
    subtask_id: str,
    pr_number: int,
    conflicted: set[str],
) -> None:
    if subtask_id in conflicted:
        return
    if subtask_id in deliveries:
        del deliveries[subtask_id]
        conflicted.add(subtask_id)
    else:
        deliveries[subtask_id] = pr_number


def _drop_cross_pr_conflicts(
    deliveries: dict[str, int],
    pr_to_subtasks: dict[int, list[str]],
) -> None:
    for _, subs in pr_to_subtasks.items():
        if len(set(subs)) > 1:
            for sid in subs:
                deliveries.pop(sid, None)


def _build_lineage_deliveries(ws: Workspace, queue: Queue) -> dict[str, int]:
    handovers = _terminal_handovers_for_team(ws, "pr_merge")
    if not handovers:
        return {}
    inv_to_task = _build_inv_to_task(ws, queue)
    deliveries: dict[str, int] = {}
    conflicted: set[str] = set()
    pr_to_subtasks: dict[int, list[str]] = {}
    seen_pairs: set[tuple[str, int]] = set()
    for inv_id, pr_number in handovers:
        subtask_id = inv_to_task.get(inv_id)
        if subtask_id is None:
            continue
        if (subtask_id, pr_number) in seen_pairs:
            continue
        seen_pairs.add((subtask_id, pr_number))
        pr_to_subtasks.setdefault(pr_number, []).append(subtask_id)
        _accum_delivery(deliveries, subtask_id, pr_number, conflicted)
    _drop_cross_pr_conflicts(deliveries, pr_to_subtasks)
    return deliveries


def _subtask_can_run(subtask: workspace_mod.Task, completed_ids: set[str]) -> bool:
    if subtask.id in completed_ids:
        return False
    if subtask.status not in (TaskStatus.PENDING, TaskStatus.READY):
        return False
    if subtask.team is None:
        return False
    return all(dep in completed_ids for dep in subtask.depends_on)


def _enqueue_ready_next(
    subtasks: list[workspace_mod.Task],
    completed_ids: set[str],
    ws: Workspace,
    queue: Queue,
) -> None:
    for subtask in subtasks:
        if not _subtask_can_run(subtask, completed_ids):
            continue
        pending = queue.list_jobs(
            task_id=subtask.id, workspace_id=ws.workspace_id, status=JobStatus.PENDING
        )
        running = queue.list_jobs(
            task_id=subtask.id, workspace_id=ws.workspace_id, status=JobStatus.RUNNING
        )
        if not pending and not running:
            queue.enqueue(
                subtask.id,
                subtask.team,  # type: ignore[arg-type]  # team is not None: _subtask_can_run checks
                workspace_id=ws.workspace_id,
                instructions=subtask.instructions,
            )


def _count_and_collect_completed(
    subtasks: list[workspace_mod.Task],
) -> tuple[int, set[str]]:
    count = 0
    ids: set[str] = set()
    for t in subtasks:
        if t.status == TaskStatus.COMPLETED:
            count += 1
            ids.add(t.id)
    return count, ids


def _is_subtask_live(subtask_id: str, workspace_id: str, queue: Queue) -> bool:
    pending = queue.list_jobs(
        task_id=subtask_id, workspace_id=workspace_id, status=JobStatus.PENDING
    )
    if pending:
        return True
    running = queue.list_jobs(
        task_id=subtask_id, workspace_id=workspace_id, status=JobStatus.RUNNING
    )
    return bool(running)


def _try_complete_subtask(
    ws: Workspace,
    subtask: workspace_mod.Task,
    pr_number: int,
    completed_ids: set[str],
    queue: Queue,
) -> tuple[Workspace, bool]:
    if subtask.status not in (TaskStatus.PENDING, TaskStatus.READY):
        return ws, False
    if not all(dep in completed_ids for dep in subtask.depends_on):
        return ws, False
    if _is_subtask_live(subtask.id, ws.workspace_id, queue):
        return ws, False
    ws = workspace_mod.update_task_status(ws, subtask.id, TaskStatus.COMPLETED)
    ws = workspace_mod.record_task_evidence(ws, subtask.id, f"PR #{pr_number}")
    completed_ids.add(subtask.id)
    return ws, True


def _apply_deliveries(
    ws: Workspace,
    deliveries: dict[str, int],
    subtasks: list[workspace_mod.Task],
    completed_ids: set[str],
    queue: Queue,
) -> tuple[Workspace, bool]:
    repaired_any = False
    for subtask in subtasks:
        pr_number = deliveries.get(subtask.id)
        if pr_number is None:
            continue
        ws, did = _try_complete_subtask(ws, subtask, pr_number, completed_ids, queue)
        if did:
            repaired_any = True
    return ws, repaired_any


def _repair_stalled_dag(ws: Workspace, queue: Queue) -> bool:
    subtasks = _injected_subtasks_topo(ws)
    if not subtasks:
        return False
    _, completed_ids = _count_and_collect_completed(subtasks)
    deliveries = _build_lineage_deliveries(ws, queue)
    if not deliveries:
        return False
    ws, repaired_any = _apply_deliveries(ws, deliveries, subtasks, completed_ids, queue)
    if not repaired_any:
        return False
    ws = workspace_mod.read(ws.task_id, root=ws.path.parent.parent.parent)
    _enqueue_ready_next(subtasks, completed_ids, ws, queue)
    logger.info(
        "reconciler: stalled DAG %s repaired; re-enqueued ready subtask(s)",
        ws.workspace_id,
    )
    return True


def seed_triage_sweep(queue: Queue, workspaces_root: Path | None) -> None:
    try:
        _seed_triage_sweep_impl(queue, workspaces_root)
    except Exception as exc:  # noqa: BLE001
        logger.exception("reconciler: seed_triage_sweep failed; triage will not start")
        write_seed_failure_audit("startup", str(exc))


def _seed_triage_sweep_impl(queue: Queue, workspaces_root: Path | None) -> None:
    all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    triage_workspaces = [ws for ws in all_ws if ws.meta.team == _TRIAGE_TEAM]
    triage_ws = _select_canonical_triage_workspace(triage_workspaces)
    if triage_ws is not None:
        _archive_duplicate_triage_workspaces(queue, triage_workspaces, triage_ws.task_id)
        pending = queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING)
        running = queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.RUNNING)
        if pending or running:
            return
    else:
        triage_ws = workspace_mod.create(
            "Failure Triage Sweep",
            "Recurring read-only sweep that surfaces failure signals.",
            team=_TRIAGE_TEAM,
            root=workspaces_root,
            provenance=provenance_mod.DispatchProvenance(
                surface=provenance_mod.DispatchSurface.TRIAGE_SWEEP
            ),
        )
    retention_registry.seed_recurring_workspace(triage_ws)
    inv_id = workflow_mod.seed_pending_workflow(
        triage_ws,
        team=_TRIAGE_TEAM,
        tasks=list(_TRIAGE_ROLE_SPECS),
        source_task_id=triage_ws.task_id,
    )
    queue.enqueue(triage_ws.task_id, _TRIAGE_TEAM, triggered_by_invocation_id=inv_id)


def _select_canonical_triage_workspace(
    workspaces: list[Workspace],
) -> Workspace | None:
    if not workspaces:
        return None
    return min(workspaces, key=lambda ws: (ws.meta.created_at, ws.task_id))


_ARCHIVED_TRIAGE_DIRNAME = "_archived_triage"
_TRIAGE_ARCHIVED_ACTION = "triage_workspace_archived"


def _archive_duplicate_triage_workspaces(
    queue: Queue, workspaces: list[Workspace], canonical_task_id: str
) -> None:
    for ws in workspaces:
        if ws.task_id != canonical_task_id:
            _archive_triage_workspace(queue, ws)


def _archive_triage_workspace(queue: Queue, ws: Workspace) -> None:
    try:
        _do_archive_triage_workspace(queue, ws)
    except Exception:  # noqa: BLE001
        logger.exception("reconciler: failed to archive duplicate triage workspace %s", ws.task_id)


def _do_archive_triage_workspace(queue: Queue, ws: Workspace) -> None:
    queue.delete_jobs(ws.task_id)
    outer_dir = ws.path.parent.parent
    archive_root = outer_dir.parent / _ARCHIVED_TRIAGE_DIRNAME
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    dest = archive_root / f"{ws.task_id}-{timestamp}"
    shutil.move(str(outer_dir), str(dest))
    append_jsonl_audit(
        logs_root() / "triage_audit.jsonl",
        {
            "action": _TRIAGE_ARCHIVED_ACTION,
            "task_id": ws.task_id,
            "archived_to": str(dest),
        },
        log_label="triage",
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_tuesday_09_utc(now: datetime) -> datetime:
    days_to_tuesday = (_TUESDAY - now.weekday()) % 7
    this_tuesday_09 = (now + timedelta(days=days_to_tuesday)).replace(
        hour=_DEP_SWEEP_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    if now >= this_tuesday_09:
        return this_tuesday_09 + timedelta(weeks=1)
    return this_tuesday_09


def _is_dep_sweep_parent_for(ws: Workspace, cfg: EngineConfig) -> bool:
    return (
        ws.meta.team == _DEP_SWEEP_TEAM
        and ws.meta.title == DEP_SWEEP_TITLE
        and canonical_scope_key(ws.meta.engine_key) == cfg.repo
    )


def _dep_sweep_workspace_if_idle(
    queue: Queue, workspaces_root: Path | None, cfg: EngineConfig
) -> Workspace | None:
    all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    dep_ws = next((ws for ws in all_ws if _is_dep_sweep_parent_for(ws, cfg)), None)
    if dep_ws is None:
        return workspace_mod.create(
            DEP_SWEEP_TITLE,
            "Autonomous recurring sweep that drives Dependabot PRs and audits vulnerabilities.",
            team=_DEP_SWEEP_TEAM,
            root=workspaces_root,
            engine_key=cfg.repo.slug,
            provenance=provenance_mod.DispatchProvenance(
                surface=provenance_mod.DispatchSurface.DEP_SWEEP_PARENT
            ),
        )
    pending = queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)
    running = queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.RUNNING)
    if pending or running:
        return None
    return dep_ws


def _dep_sweep_next_run_key(slug: str) -> str:
    return f"{_DEP_SWEEP_NEXT_RUN_META_KEY}:{slug}"


def _read_dep_sweep_next_run(queue: Queue, slug: str) -> str | None:
    raw = queue.get_meta(_dep_sweep_next_run_key(slug))
    if raw is not None:
        return raw
    if slug == default_engine_config().repo.slug:
        return queue.get_meta(_DEP_SWEEP_NEXT_RUN_META_KEY)
    return None


def seed_dep_sweep(queue: Queue, workspaces_root: Path | None) -> None:
    for cfg in registered_engines():
        if cfg.hidden or not cfg.dependency_sweep.enabled:
            continue
        try:
            _seed_dep_sweep_impl(queue, workspaces_root, cfg)
        except Exception:  # noqa: BLE001
            logger.exception(
                "reconciler: seed_dep_sweep failed for %s; dependency sweep will not start",
                cfg.repo.slug,
            )


def _seed_dep_sweep_impl(queue: Queue, workspaces_root: Path | None, cfg: EngineConfig) -> None:
    now = _utc_now()
    raw = _read_dep_sweep_next_run(queue, cfg.repo.slug)
    if raw is not None:
        try:
            if now.timestamp() < float(raw):
                return
        except ValueError:
            pass
    dep_ws = _dep_sweep_workspace_if_idle(queue, workspaces_root, cfg)
    if dep_ws is None:
        return
    retention_registry.seed_recurring_workspace(dep_ws)
    inv_id = workflow_mod.seed_pending_workflow(
        dep_ws,
        team=_DEP_SWEEP_TEAM,
        tasks=[_DEP_SWEEP_ROLE_SPEC],
        source_task_id=dep_ws.task_id,
    )
    queue.enqueue(dep_ws.task_id, _DEP_SWEEP_TEAM, triggered_by_invocation_id=inv_id)
    queue.set_meta(
        _dep_sweep_next_run_key(cfg.repo.slug), str(_next_tuesday_09_utc(now).timestamp())
    )
