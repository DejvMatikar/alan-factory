from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from alan import _dispatcher_resume_outcome as _resume_outcome
from alan import auto_continue as auto_continue_mod
from alan import engine_config as _engine_config_mod
from alan import investigator as investigator_mod
from alan import manager_summary as _manager_summary_mod
from alan import project_auto_continue as project_auto_continue_mod
from alan import worker_records
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.clock import Clock, Monotonic
from alan.events import PubSub
from alan.handover import Handover, list_handovers
from alan.incident import Disposition, IncidentClass, derive_incidents
from alan.queue import Job, Queue
from alan.roots import logs_root
from alan.slot_pool import SlotPool
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import Team, TeamEvent
from alan.workspace import EngineSnapshot, Workspace
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.dispatcher")

_JOB_FAILURE_AUDIT_FILE = "job_failure_audit.jsonl"


class DispatcherState:
    _clock: Clock
    _queue: Queue
    _teams: dict[str, Team]
    _pubsub: PubSub
    _workspaces_root: Path | None
    _poll_interval: float
    _concurrency: int
    _slot_pool: SlotPool | None
    _slot_pools_by_engine: dict[str, SlotPool]
    _pool_factory: Callable[[_engine_config_mod.EngineConfig], SlotPool] | None
    _slot_pools_lock: threading.Lock
    _workspace_index: WorkspaceIndex | None
    _stop: threading.Event
    _threads: list[threading.Thread]
    _proc_registry: "ProcRegistry"
    _user_paused: set[int]
    _user_paused_lock: threading.Lock
    _workspace_locks: dict[str, "_NonReentrantLock"]
    _workspace_locks_meta: threading.Lock
    _alive_lock: threading.Lock
    _alive_worker_count: int
    _deploy_gate_sweep_next_at: Monotonic
    _deploy_gate_sweep_lock: threading.Lock
    _worker_liveness_next_at: float = 0.0
    _unblock_sweep_next_at: float = 0.0

    def _fail_job(self, job: Job, reason: str) -> None:
        raise NotImplementedError

    def _get_workspace_lock(self, workspace_id: str) -> "_NonReentrantLock":
        raise NotImplementedError

    def _publish(self, job: Job, kind: str, extra: dict[str, str]) -> None:
        raise NotImplementedError

    def _run_team(
        self, job: Job, ws: Workspace, team: Team, prior: Handover | None
    ) -> None:
        raise NotImplementedError

    def _fail_genuine(
        self, job: Job, ws: Workspace, exc: Exception, inv_id: str, team: Team
    ) -> None:
        raise NotImplementedError

    def _fail_workspace(self, ws: Workspace, reason: str) -> None:
        raise NotImplementedError

    def _observe_task_failure(self, job: Job) -> None:
        raise NotImplementedError

    def _read_workspace_and_pool(
        self, job: Job
    ) -> tuple[Workspace, SlotPool | None] | None:
        raise NotImplementedError


class OutOfBandCompletionError(RuntimeError):
    """Raised when the out-of-band subtask completion cannot be applied safely."""


class _NonReentrantLock:
    """Per-workspace mutex that raises RuntimeError on same-thread re-entry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()

    def __enter__(self) -> "_NonReentrantLock":
        if getattr(self._local, "held", False):
            raise RuntimeError(
                "workspace lock re-entry from the same thread — potential deadlock"
            )
        self._lock.acquire()
        self._local.held = True
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        self._local.held = False
        self._lock.release()


class ProcRegistry:
    """Per-job Popen set (not one handle) — Stop SIGTERM/SIGKILLs every stage-group proc."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[int, set[subprocess.Popen[str]]] = {}

    def attach(self, job_id: int, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._procs.setdefault(job_id, set()).add(proc)

    def detach(self, job_id: int) -> None:
        with self._lock:
            self._procs.pop(job_id, None)

    def terminate(self, job_id: int) -> bool:
        with self._lock:
            procs = set(self._procs.get(job_id, ()))
        if not procs:
            return _terminate_via_records(job_id)
        for proc in procs:
            _terminate_proc(proc)
        return True


@dataclass(frozen=True)
class ProcStartedHook:
    registry: ProcRegistry
    job_id: int

    def __call__(self, proc: subprocess.Popen[str]) -> None:
        self.registry.attach(self.job_id, proc)


def _terminate_via_records(job_id: int) -> bool:
    this_service = worker_records.this_service_identity()
    terminated = False
    for _path, record in worker_records.load_all():
        if record is None or record.job_id != job_id:
            continue
        verdict = worker_records.probe(record, this_service=this_service)
        if not worker_records.is_terminable(verdict):
            continue
        # is_terminable() is LIVE-only, and probe() never returns LIVE
        # without a confirmed pid — see docs/architecture/worker_records.md.
        if not _terminate_record(record):
            continue
        worker_records.remove_record(record.session_id)
        terminated = True
    return terminated


def _terminate_record(record: worker_records.WorkerRecord) -> bool:
    pid = cast(int, record.pid)
    if record.pgid is None:
        worker_records.terminate_pid(pid)
        return True
    try:
        worker_records.guard_no_group_signal(pid)
    except worker_records.GroupSignalGuardError:
        logger.warning("refusing to signal own process group for job pid=%d", pid)
        return False
    worker_records.terminate_group(record.pgid)
    return True


def _terminate_proc(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=worker_records.SIGTERM_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()


def _governing_engine_config(engine_key: str | None) -> _engine_config_mod.EngineConfig:
    return _engine_config_mod._resolve_scope_config(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        engine_key
    )


def _stamp_snapshot(ws: Workspace) -> Workspace:
    cfg = _governing_engine_config(ws.meta.engine_key)
    snapshot = EngineSnapshot(
        posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
    )
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


def governing_engine_config(ws: Workspace) -> _engine_config_mod.EngineConfig:
    snapshot = ws.meta.engine_snapshot
    if snapshot is not None and snapshot.config is not None:
        return snapshot.config
    return _governing_engine_config(ws.meta.engine_key)


def forge_target_for_ws(ws: Workspace) -> _engine_config_mod.ForgeTarget:
    snapshot = ws.meta.engine_snapshot
    snapshot_config = snapshot.config if snapshot is not None else None
    return _engine_config_mod.forge_target_for_workspace(ws.meta.engine_key, snapshot_config)


def _record_job_failure_audit(job: Job, reason: str) -> None:
    append_jsonl_audit(
        logs_root() / _JOB_FAILURE_AUDIT_FILE,
        {
            "task_id": job.task_id,
            "workspace_id": job.workspace_id,
            "team": job.team,
            "inv_id": job.triggered_by_invocation_id or "",
            "error": reason,
            "server_error_retry_count": job.server_error_retry_count,
        },
        log_label="job_failure",
    )


_INVESTIGATOR_INCIDENT_CLASSES: frozenset[IncidentClass] = frozenset({
    IncidentClass.ESCALATION,
    IncidentClass.TERMINAL_FAILED,
    IncidentClass.TERMINAL_PARTIAL,
    IncidentClass.PARK_CRASH,
    IncidentClass.PARK_ROUTING_ERROR,
    IncidentClass.PARK_STALL,
})


def _maybe_trigger_investigator(ws: Workspace, queue: Queue) -> None:
    if ws.meta.team == "triage":
        return
    handovers = list_handovers(ws)
    incidents = derive_incidents(
        workspaces=[ws], handovers_by_workspace={ws.task_id: handovers}
    )
    if not incidents:
        return
    incident = incidents[0]
    if incident.incident_class not in _INVESTIGATOR_INCIDENT_CLASSES:
        return
    valid_dispositions = frozenset(d.value for d in Disposition)
    investigator_mod.trigger_investigator(
        ws.path, incident.fingerprint, incident.evidence, valid_dispositions, queue, ws.task_id
    )


def _schedule_manager_summary(  # pyright: ignore[reportUnusedFunction]
    ws: Workspace,
    synthesis: str | None,
    pubsub: PubSub,
    queue: Queue,
    *,
    stop_reason: str | None = None,
    workspace_index: WorkspaceIndex | None = None,
    workspaces_root: Path | None = None,
) -> None:
    if ws.meta.team == "triage":
        return
    ws_path = ws.path
    workspace_id = ws.workspace_id
    task_id = ws.task_id
    title = ws.meta.title
    status = str(ws.meta.status)

    def on_ready() -> None:
        pubsub.publish(
            workspace_id, TeamEvent("manager_summary_ready", {"task_id": task_id})
        )

    _manager_summary_mod.trigger_manager_summary(
        ws_path, workspace_id, title, status, synthesis, on_ready, stop_reason=stop_reason
    )
    # auto-continue and the investigator both derive an incident for the same
    # transition; a dispatch already resolves it, so the investigator would
    # otherwise redundantly diagnose a workspace that just moved on.
    if not auto_continue_mod.maybe_auto_continue(
        ws, queue, pubsub, enqueue_with_pending_workflow, workspace_index=workspace_index
    ):
        _maybe_trigger_investigator(ws, queue)
        # Cross-item roadmap advancement rides this same terminal-transition
        # seam, strictly after the within-workspace continuation above has
        # been resolved (dispatched or not) — at most one consideration per
        # delivered workspace, never a standalone loop. Best-effort: a bug
        # here must not crash the worker or block the summary/investigator
        # work that already ran above.
        try:
            project_auto_continue_mod.maybe_advance_project(
                ws, queue, pubsub, enqueue_with_pending_workflow,
                workspaces_root=workspaces_root, workspace_index=workspace_index,
            )
        except Exception:  # noqa: BLE001 - cross-item advance is best-effort, must not crash the worker
            logger.warning(
                "cross-item advance failed for workspace=%s project=%s",
                ws.workspace_id, ws.meta.project_id, exc_info=True,
            )


def enqueue_with_pending_workflow(
    *,
    queue: Queue,
    pubsub: PubSub,
    workspace: Workspace,
    team: Team,
    prior: Handover | None,
    parent_handover_id: str | None = None,
    instructions: str | None = None,
    instructions_origin: str | None = None,
    task_id: str | None = None,
    eligible_at: float | None = None,
) -> tuple[Job, str]:
    """Pre-allocate a pending workflow.yaml for the team, then enqueue.

    Used by both the HTTP dispatch endpoint (user-initiated) and the
    in-process router (team-completion-initiated). The pending workflow.yaml
    is what makes the InvocationCard visible in the Timeline before the
    dispatcher even claims the job. `instructions` is a free-form addendum
    the user (or chat agent) attached to a manual dispatch; the dispatcher
    forwards it to `team.run` so the team can fold it into its prompt.
    """
    if workspace.meta.engine_snapshot is None:
        workspace = _stamp_snapshot(workspace)
    _task_id = task_id if task_id is not None else workspace.task_id
    specs = team.plan_workflow(workspace, prior)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team=team.name, tasks=specs, source_task_id=_task_id
    )
    job = queue.enqueue(
        _task_id,
        team.name,
        workspace_id=workspace.workspace_id,
        parent_handover_id=parent_handover_id,
        triggered_by_invocation_id=inv_id,
        instructions=instructions,
        instructions_origin=instructions_origin,
        eligible_at=eligible_at,
    )
    pubsub.publish(
        workspace.workspace_id,
        TeamEvent(
            "workflow_created",
            {"task_id": _task_id, "inv_id": inv_id, "team": team.name, "job_id": str(job.id)},
        ),
    )
    return job, inv_id


class BaseMixin(DispatcherState):
    def _get_workspace_lock(self, workspace_id: str) -> _NonReentrantLock:
        with self._workspace_locks_meta:
            if workspace_id not in self._workspace_locks:
                self._workspace_locks[workspace_id] = _NonReentrantLock()
            return self._workspace_locks[workspace_id]

    def _publish(self, job: Job, kind: str, extra: dict[str, str]) -> None:
        payload = {"team": job.team, "job_id": str(job.id), "task_id": job.task_id, **extra}
        self._pubsub.publish(job.workspace_id or job.task_id, TeamEvent(kind, payload))

    def _emitter(self, workspace_id: str, task_id: str) -> Callable[[TeamEvent], None]:
        def emit(event: TeamEvent) -> None:
            augmented = TeamEvent(event.kind, {**event.payload, "task_id": task_id})
            self._pubsub.publish(workspace_id, augmented)

        return emit

    def _fail_job(self, job: Job, reason: str) -> None:
        self._queue.fail(job.id, reason)
        logger.warning(
            "job failed: task=%s team=%s inv=%s error=%s",
            job.task_id, job.team, job.triggered_by_invocation_id or "-", reason,
        )
        _record_job_failure_audit(job, reason)
        _resume_outcome.record_terminal_resume_outcome(job, succeeded=False)
        self._observe_task_failure(job)
        self._publish(job, "job_failed", {"error": reason})
