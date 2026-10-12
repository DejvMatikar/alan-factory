from __future__ import annotations

import dataclasses
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import alan._dispatcher_base as _base
import alan._dispatcher_brief_refine as _brief_refine
import alan._dispatcher_loop as _loop
import alan._dispatcher_reconcile as _reconcile
import alan._dispatcher_resume_outcome as _resume_outcome
import alan._dispatcher_scenario_rebind as _scenario_rebind
import alan._dispatcher_selfheal as _selfheal
import alan._dispatcher_usage_limit as _usage_limit
import alan.workspace_dag as _workspace_dag
from alan import (
    deploy_gate_route,
    deploy_wait_gate,
    merge_recovery,
    sticky_instructions,
    workflow_interrupt,
)
from alan import engine_config as _engine_config_mod
from alan import merge_gate as merge_gate_mod
from alan import park as park_mod
from alan import proactive_rebase as _proactive_rebase_mod
from alan import project as project_mod
from alan import project_doc as project_doc_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli import ServerErrorRetryable, UsageLimitError
from alan.clock import SYSTEM_CLOCK, Clock, Monotonic
from alan.dispatch import team_is_dispatchable
from alan.engine_config import Posture
from alan.engine_config import admitted_teams as _admitted_teams
from alan.events import PubSub as PubSub
from alan.handover import Handover, HandoverError, save_handover
from alan.loop_guard import (
    REVISIT_CAP,
    append_spiral_audit,
    check_loop_guard,
    detect_spiral,
    has_forward_progress,
)
from alan.overlap import find_sibling_overlap
from alan.queue import Job, JobStatus, Queue, QueueError
from alan.roots import logs_root
from alan.router import RouterDecision, select_next_team
from alan.slot_pool import SlotPool
from alan.teams import _pr_audit_log, _pr_branch, _pr_state_paths
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import Team, TeamEvent
from alan.teams._spiral_handover import spiral_escalation_handover
from alan.workspace import Status, TaskStatus, Workspace
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.dispatcher")

_PR_MERGE_REMEDIATION_RETRY_DELAY_ENV = "ALAN_PR_MERGE_REMEDIATION_RETRY_DELAY_S"
_DEFAULT_PR_MERGE_REMEDIATION_RETRY_DELAY_S = 90.0

_BOUNDED_RETRY_REMEDIATION_CLASSES: frozenset[merge_recovery.MergeBlockClass] = frozenset({
    merge_recovery.MergeBlockClass.STALE_REVIEW,
    merge_recovery.MergeBlockClass.PRE_EXISTING_RED,
    merge_recovery.MergeBlockClass.TRANSIENT_FLAKE,
    merge_recovery.MergeBlockClass.DIRTY,
    merge_recovery.MergeBlockClass.STALE_MEASURED_BASELINE,
    merge_recovery.MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT,
    merge_recovery.MergeBlockClass.NO_CHECK_RESULTS,
})

def _delay_for_ci_reset(
    decision: RouterDecision,
    block_class: merge_recovery.MergeBlockClass,
    *,
    clock: Clock = SYSTEM_CLOCK,
) -> RouterDecision:
    if block_class not in merge_recovery.CI_RESET_DELAY_CLASSES:
        return decision
    delay = _usage_limit._positive_float_env(  # pyright: ignore[reportPrivateUsage]
        _PR_MERGE_REMEDIATION_RETRY_DELAY_ENV,
        _DEFAULT_PR_MERGE_REMEDIATION_RETRY_DELAY_S,
    )
    return cast(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        RouterDecision, dataclasses.replace(decision, eligible_at=clock.now().value + delay)
    )


_USER_PAUSE_ERROR = "user paused"


_STATUS_BY_DECISION_KIND: dict[str, Status] = {
    "await_human": Status.AWAITING_INPUT,
    # ESCALATED (not AWAITING_INPUT) per docs/architecture/
    # pr_iteration_pipeline.md §A7 — distinguishes "I tried and failed" from
    # "I need a human decision to proceed". Worker-crash escalations still
    # land at AWAITING_INPUT via Dispatcher._fail_workspace.
    "escalate": Status.ESCALATED,
}


def pinned_engine_config(ws: Workspace) -> tuple[Posture, dict[str, list[str]]]:
    snapshot = ws.meta.engine_snapshot
    if snapshot is not None:
        posture = _engine_config_mod.live_floored_posture(
            snapshot.posture, ws.meta.engine_key
        )
        return posture, dict(snapshot.transition_graph)
    cfg = _base._governing_engine_config(ws.meta.engine_key)  # pyright: ignore[reportPrivateUsage]
    return cfg.posture, dict(cfg.transition_graph)


_PROJECT_FACT_BLOCK_RE = re.compile(
    r"##[ \t]+PROJECT_FACT[ \t]*\n+[ \t]*```[ \t]*[A-Za-z0-9_+-]*[ \t]*\n"
    r"(.*?)\n[ \t]*```",
    re.DOTALL,
)


def _extract_project_fact(text: str) -> str | None:
    normalized = text.replace("\r\n", "\n")
    match = _PROJECT_FACT_BLOCK_RE.search(normalized)
    if match is None:
        return None
    fact = " ".join(match.group(1).split())
    return fact or None


def _format_project_fact_bullet(team: str, fact: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"- **{stamp}** ({team}): {fact}"


def _maybe_harvest_project_fact(ws: Workspace, handover: Handover) -> None:
    if ws.meta.project_id is None:
        return
    fact = _extract_project_fact(handover.full_synthesis)
    if fact is None:
        return
    try:
        project = project_mod.read(ws.meta.project_id)
        project_doc_mod.append_discovered_fact(
            project, _format_project_fact_bullet(handover.from_team, fact)
        )
    except Exception:  # noqa: BLE001 - fact harvest is best-effort, must not crash the worker
        logger.warning(
            "project fact harvest failed for task=%s project=%s",
            ws.task_id, ws.meta.project_id, exc_info=True,
        )


def _is_recoverable_failure(ws: Workspace, team: Team) -> bool:
    return team.supports_resume and not ws.meta.tasks


def _decision_outcome(result: RouterDecision) -> str:
    return result.team or result.kind or ""


def _brief_attached(result: RouterDecision) -> bool:
    return result.kind == "enqueue" and result.team == "implementation_fix"


class DispatcherError(RuntimeError):
    pass


class NoPoolForEngineError(DispatcherError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"no slot pool registered for engine {slug!r}")
        self.slug = slug


_TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


class Dispatcher(
    _loop.LoopMixin,
    _reconcile.ReconcileMixin,
    _selfheal.SelfHealMixin,
    _usage_limit.UsageLimitMixin,
    _base.BaseMixin,
):
    def __init__(
        self,
        queue: Queue,
        teams: dict[str, Team],
        *,
        pubsub: PubSub | None = None,
        workspaces_root: Path | None = None,
        poll_interval: float = _loop._DEFAULT_POLL_INTERVAL,  # pyright: ignore[reportPrivateUsage]
        concurrency: int = 1,
        slot_pool: SlotPool | None = None,
        slot_pools_by_engine: dict[str, SlotPool] | None = None,
        workspace_index: WorkspaceIndex | None = None,
        pool_factory: Callable[[_engine_config_mod.EngineConfig], SlotPool] | None = None,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        if concurrency < 1:
            raise DispatcherError(f"concurrency must be >= 1, got {concurrency}")
        self._clock = clock
        self._queue = queue
        self._teams = dict(teams)
        self._pubsub = pubsub if pubsub is not None else PubSub()
        self._workspaces_root = workspaces_root
        self._poll_interval = poll_interval
        self._concurrency = concurrency
        self._slot_pool = slot_pool
        self._slot_pools_by_engine = (
            dict(slot_pools_by_engine) if slot_pools_by_engine else {}
        )
        self._pool_factory = pool_factory
        # Lock order when both are needed: a pool's own `_cond` is always
        # acquired (and released) independently of `_slot_pools_lock` — the
        # two are never held simultaneously, so no ordering hazard exists.
        # Dict-removal in dispose_pool sits between the pool's begin_dispose
        # (busy-check under _cond) and finish_dispose (rmtree); any new
        # timeout added here must stay in the Monotonic domain (alan.clock),
        # matching SlotPool's own lease timestamps.
        self._slot_pools_lock = threading.Lock()
        self._workspace_index = workspace_index
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._proc_registry = _base.ProcRegistry()
        self._user_paused: set[int] = set()
        self._user_paused_lock = threading.Lock()
        self._workspace_locks: dict[str, _base._NonReentrantLock] = {}  # pyright: ignore[reportPrivateUsage]
        self._workspace_locks_meta = threading.Lock()
        self._alive_lock = threading.Lock()
        self._alive_worker_count = 0
        self._deploy_gate_sweep_next_at = Monotonic(
            self._clock.monotonic().value
            + _loop._DEPLOY_GATE_SWEEP_INTERVAL_S  # pyright: ignore[reportPrivateUsage]
        )
        self._deploy_gate_sweep_lock = threading.Lock()

    def start(self) -> None:
        if self._threads:
            raise DispatcherError("dispatcher already started")
        self._stop.clear()
        for i in range(self._concurrency):
            with self._alive_lock:
                self._alive_worker_count += 1
            thread = threading.Thread(
                target=self._loop, daemon=True, name=f"alan-dispatcher-{i}"
            )
            self._threads.append(thread)
            try:
                thread.start()
            except Exception:
                self._threads.pop()
                with self._alive_lock:
                    self._alive_worker_count -= 1
                raise
        self._publish_worker_state()

    def stop(self, *, timeout: float | None = 5.0) -> None:
        self._stop.set()
        if not self._threads:
            return
        self._join_workers(timeout)
        if any(t.is_alive() for t in self._threads):
            raise DispatcherError(_loop._stop_failure_message(timeout))  # pyright: ignore[reportPrivateUsage]
        self._threads = []

    def _join_workers(self, timeout: float | None) -> None:
        if timeout is None:
            for thread in self._threads:
                thread.join()
            return
        deadline = Monotonic(self._clock.monotonic().value + timeout)
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - self._clock.monotonic()))

    def _read_workspace_and_pool(
        self, job: Job
    ) -> tuple[Workspace, SlotPool | None] | None:
        try:
            workspace = workspace_mod.read(
                job.workspace_id or job.task_id, root=self._workspaces_root
            )
        except workspace_mod.WorkspaceError as exc:
            self._fail_job(job, f"workspace error: {exc}")
            return None
        try:
            pool = self._pool_for_workspace(workspace)
        except NoPoolForEngineError as exc:
            self._fail_job(job, str(exc))
            return None
        return workspace, pool

    def _pool_for_workspace(self, ws: Workspace) -> SlotPool | None:
        # Asymmetric guard: a non-default engine with no keyed pool is a
        # refusal (NoPoolForEngineError), never a silent fallback to the
        # default engine's pool or the unpooled worktree substrate — that
        # fallback would aim work at the wrong repository. The default
        # engine alone may still resolve to `self._slot_pool is None`
        # (unpooled worktree substrate), matching pre-existing behavior.
        cfg = _base.governing_engine_config(ws)
        with self._slot_pools_lock:
            keyed = self._slot_pools_by_engine.get(cfg.repo.slug)
        if keyed is not None:
            return keyed
        default_slug = _base._governing_engine_config(None).repo.slug  # pyright: ignore[reportPrivateUsage]
        if cfg.repo.slug == default_slug:
            return self._slot_pool
        raise NoPoolForEngineError(cfg.repo.slug)

    def interrupt_task(self, task_id: str) -> bool:
        running = self._queue.list_jobs_for_workspace(task_id, status=JobStatus.RUNNING)
        if not running:
            return False
        job = running[0]
        with self._user_paused_lock:
            self._user_paused.add(job.id)
        self._queue.pause_task(task_id)
        if self._proc_registry.terminate(job.id):
            return True
        with self._user_paused_lock:
            self._user_paused.discard(job.id)
        self._queue.unpause_task(task_id)
        return False

    def resume_task(self, task_id: str) -> None:
        paused_ids = self._queue.list_paused_task_ids_for_workspace(task_id)
        for subtask_id in paused_ids:
            self._queue.unpause_task(subtask_id)
        if not paused_ids:
            self._queue.unpause_task(task_id)
        failed = self._queue.list_resumable_failed_by_workspace(task_id)
        if not failed:
            return
        job = failed[0]
        if not job.resume_inv_id:
            logger.warning(
                "resume_task: job %d has empty resume_inv_id, skipping requeue", job.id
            )
            return
        try:
            requeued = self._queue.requeue_failed_for_resume(job.id)
        except QueueError:
            logger.warning(
                "resume_task: requeue_failed_for_resume failed for job %d", job.id
            )
            return
        self._publish(
            requeued, "job_requeued_for_resume", {"inv_id": requeued.resume_inv_id or ""}
        )

    def replay_handover_routing(self, job: Job, handover: Handover) -> None:
        ws_id = job.workspace_id or job.task_id
        try:
            ws = workspace_mod.read(ws_id, root=self._workspaces_root)
        except workspace_mod.WorkspaceError as exc:
            logger.warning(
                "replay_handover_routing: workspace not found for job %d (%s): %s",
                job.id, ws_id, exc,
            )
            return
        self._route_after_handover(job, ws, handover)

    def _fail_genuine(
        self, job: Job, ws: Workspace, exc: Exception, inv_id: str, team: Team
    ) -> None:
        blocker = _selfheal.merged_stamp_blocker(ws, job)
        blockers = [blocker] if blocker is not None else None
        if _is_recoverable_failure(ws, team):
            workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)
            self._fail_workspace(ws, str(exc), blockers)
            _resume_outcome.record_terminal_resume_outcome(job, succeeded=False)
            try:
                self._queue.mark_interrupted_and_fail(
                    job.id, str(exc), resume_inv_id=inv_id
                )
            except QueueError:
                logger.warning("mark_interrupted_and_fail failed for job %d", job.id)
                self._publish(job, "job_failed", {"error": str(exc)})
                return
            self._publish(job, "job_failed", {"error": str(exc)})
        elif ws.meta.tasks and team.supports_resume:
            self._fail_recoverable_subtask(job, ws, exc, inv_id)
        else:
            self._fail_workspace(ws, str(exc), blockers)
            self._fail_job(job, str(exc))

    def _fail_recoverable_subtask(
        self, job: Job, ws: Workspace, exc: Exception, inv_id: str
    ) -> None:
        lock = self._get_workspace_lock(job.workspace_id)
        oserr = False
        with lock:
            ws = workspace_mod.read(ws.task_id, root=self._workspaces_root)
            workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)
            try:
                ws = workspace_mod.transition(
                    ws,
                    Status.AWAITING_INPUT,
                    park_reason=park_mod.ParkReason.CRASH,
                    blockers=park_mod.synthetic_blocker(str(exc)),
                )
            except OSError:
                logger.exception(
                    "failed to flip workspace %s to AWAITING_INPUT", ws.task_id
                )
                oserr = True
            else:
                _base._schedule_manager_summary(  # pyright: ignore[reportPrivateUsage]
                    ws, None, self._pubsub, self._queue,
                    workspace_index=self._workspace_index,
                    workspaces_root=self._workspaces_root,
                )
        if oserr:
            self._fail_job(job, str(exc))
            return
        _resume_outcome.record_terminal_resume_outcome(job, succeeded=False)
        try:
            self._queue.mark_interrupted_and_fail(job.id, str(exc), resume_inv_id=inv_id)
        except QueueError:
            logger.warning("mark_interrupted_and_fail failed for job %d", job.id)
            self._publish(job, "job_failed", {"error": str(exc)})
            return
        self._publish(job, "job_failed", {"error": str(exc)})

    def _handle_team_exception(
        self, job: Job, ws: Workspace, exc: Exception, inv_id: str, team: Team
    ) -> None:
        if self._claim_user_paused(job.id):
            self._pause_job(job, ws, inv_id, team)
            return
        self._fail_genuine(job, ws, exc, inv_id, team)

    def _claim_user_paused(self, job_id: int) -> bool:
        with self._user_paused_lock:
            if job_id in self._user_paused:
                self._user_paused.discard(job_id)
                return True
        return False

    def _pause_job(self, job: Job, ws: Workspace, inv_id: str, team: Team) -> None:
        try:
            workflow_interrupt.interrupt_workflow_on_disk(
                ws,
                inv_id,
                error=_USER_PAUSE_ERROR,
                resumable=team.supports_resume,
                recover_finalized=team.supports_resume,
            )
        except OSError:
            logger.exception("interrupt_workflow_on_disk failed for job %d", job.id)
        if not self._requeue_for_pause(job, ws, team, inv_id, label="user-pause"):
            return
        mode = "resume" if team.supports_resume else "pending"
        self._publish(job, "job_paused", {"inv_id": inv_id, "mode": mode})

    def _call_team(
        self,
        job: Job,
        ws: Workspace,
        team: Team,
        prior: Handover | None,
        inv_id: str,
    ) -> Handover | None:
        try:
            return team.run(
                ws,
                prior,
                self._emitter(ws.workspace_id, job.task_id),
                inv_id=inv_id,
                resume_inv_id=job.resume_inv_id,
                instructions=job.instructions,
                on_proc_started=_base.ProcStartedHook(self._proc_registry, job.id),
                task_id=job.task_id,
            )
        except UsageLimitError as exc:
            self._handle_usage_limit(job, ws, team, exc, inv_id)
        except ServerErrorRetryable as exc:
            self._handle_server_error(job, ws, exc, inv_id, team)
        except Exception as exc:  # noqa: BLE001 - team failures must not crash the worker
            self._handle_team_exception(job, ws, exc, inv_id, team)
        finally:
            self._proc_registry.detach(job.id)
        return None

    def _surface_routing_error(
        self, ws: Workspace, job: Job, error: str
    ) -> None:
        updated_ws: Workspace | None = None
        try:
            updated_ws = workspace_mod.transition(
                ws,
                Status.AWAITING_INPUT,
                park_reason=park_mod.ParkReason.ROUTING_ERROR,
                blockers=park_mod.synthetic_blocker(error),
            )
        except Exception:  # noqa: BLE001 - silent stall path: must not suppress any update failure
            logger.exception(
                "_surface_routing_error: status update failed for %s", ws.workspace_id
            )
        if updated_ws is not None:
            _base._schedule_manager_summary(  # pyright: ignore[reportPrivateUsage]
                updated_ws, None, self._pubsub, self._queue,
                stop_reason=f"routing error: {error}",
                workspace_index=self._workspace_index,
                workspaces_root=self._workspaces_root,
            )
        self._publish(job, "job_failed", {"error": f"routing error: {error}"})

    def _effective_teams(self, ws: Workspace) -> dict[str, Team]:
        posture, _ = pinned_engine_config(ws)
        admitted = _admitted_teams(set(self._teams), posture)
        return {k: v for k, v in self._teams.items() if k in admitted}

    def _pinned_policy(self, ws: Workspace) -> dict[str, frozenset[str]]:
        _, graph = pinned_engine_config(ws)
        return {team: frozenset(targets) for team, targets in graph.items()}

    def _route_after_handover(
        self, job: Job, ws: Workspace, handover: Handover
    ) -> None:
        effective = self._effective_teams(ws)
        policy = self._pinned_policy(ws)
        ws_after = ws
        lock = self._get_workspace_lock(ws.workspace_id)
        try:
            # select_next_team is pure; _enforce_pr_merge_gate may call gh pr view
            # (slow network) and _maybe_self_heal may run DoD scenarios (pytest
            # subprocesses) — both run outside the lock. apply_rebinds does no
            # subprocess work, so it runs inside the lock ahead of the
            # revisit-count block, so an accepted rebind decays, not increments.
            decision = select_next_team(handover, effective, policy)
            decision = self._enforce_pr_merge_gate(ws, handover, decision, effective)
            decision = self._enforce_deploy_gate(handover, decision, effective)
            decision, handover = self._maybe_self_heal(ws, handover, decision, job.task_id)
            self._audit_sibling_overlap(ws, handover, job.task_id)
            with lock:
                ws_after = workspace_mod.read(ws.task_id, root=self._workspaces_root)
                ws_after, rebind_outcome = _scenario_rebind.apply_rebinds(
                    ws_after, handover, job.task_id
                )
                decision = _scenario_rebind.route_after_rebind(
                    decision, handover, rebind_outcome, effective
                )
                ws_after = self._apply_revisit_progress(
                    ws_after, handover, decision, job.task_id
                )
                ws_after = self._maybe_populate_keep_manifest(
                    ws_after, handover, decision, job.task_id
                )
                self._apply_router_decision(ws_after, handover, job.task_id, decision)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "post-handover routing failed job=%d task=%s team=%s",
                job.id, job.task_id, job.team,
            )
            self._surface_routing_error(ws_after, job, str(exc))
        finally:
            completed, current = self._queue.try_complete_or_current(job.id)
            if completed is None:
                self._log_uncompletable_routing_job(job, current)
            else:
                _resume_outcome.record_terminal_resume_outcome(job, succeeded=True)

    def _apply_revisit_progress(
        self, ws_after: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> Workspace:
        if decision.team is not None and has_forward_progress(
            ws_after, handover, decision.team
        ):
            ws_after = workspace_mod.decay_revisit_count(
                ws_after, handover.from_team, handover.handover_chain_id, task_id=task_id,
            )
            append_spiral_audit({
                "kind": "chain_decay", "task_id": ws_after.task_id,
                "from_team": handover.from_team, "next_team": decision.team,
                "handover_chain_id": handover.handover_chain_id,
            })
            return ws_after
        return workspace_mod.increment_revisit_count(
            ws_after, handover.from_team, handover.handover_chain_id, task_id=task_id,
        )

    def _log_uncompletable_routing_job(self, job: Job, current: Job | None) -> None:
        if current is not None and current.status in _TERMINAL_JOB_STATUSES:
            logger.debug(
                "_route_after_handover: job %d already finalized (status=%s) by "
                "another concurrent path; skipping redundant completion",
                job.id, current.status,
            )
            return
        logger.warning(
            "_route_after_handover: job %d unexpectedly not RUNNING at routing "
            "completion (status=%s)",
            job.id, current.status if current is not None else "missing",
        )

    def _run_team(
        self,
        job: Job,
        ws: Workspace,
        team: Team,
        prior: Handover | None,
    ) -> None:
        inv_id = self._resolve_inv_id(job, ws, team, prior)
        job.triggered_by_invocation_id = inv_id
        logger.info(
            "team start: task=%s team=%s inv=%s%s",
            job.task_id, team.name, inv_id,
            " (resume)" if job.resume_inv_id else "",
        )
        self._publish(
            job,
            "job_started",
            {"inv_id": inv_id, "resume_inv_id": job.resume_inv_id or ""},
        )
        handover = self._call_team(job, ws, team, prior, inv_id)
        if handover is None:
            return
        try:
            path = save_handover(ws, handover)
        except HandoverError as exc:
            self._fail_workspace(ws, f"handover save: {exc}")
            self._fail_job(job, f"handover save: {exc}")
            return
        logger.info(
            "team done: task=%s team=%s inv=%s next=%s",
            job.task_id, team.name, inv_id, ",".join(handover.to_team_suggestions) or "-",
        )
        self._publish(job, "job_completed", {"handover_path": str(path)})
        _maybe_harvest_project_fact(ws, handover)
        self._route_after_handover(job, ws, handover)
        self._maybe_trigger_proactive_rebase(job, ws, team, handover)

    def _maybe_trigger_proactive_rebase(
        self, job: Job, ws: Workspace, team: Team, handover: Handover
    ) -> None:
        if team.name != "pr_merge":
            return
        if handover.handover_type != "terminal":
            return
        try:
            _proactive_rebase_mod.maybe_trigger_proactive_rebase(
                skip_workspace_id=ws.workspace_id,
                workspaces_root=self._workspaces_root,
                queue=self._queue,
                audit_dir=logs_root(),
                triggered_by_pr=handover.pr_number,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "proactive rebase spawn failed: task=%s", job.task_id, exc_info=True
            )

    def _resolve_inv_id(
        self, job: Job, ws: Workspace, team: Team, prior: Handover | None
    ) -> str:
        if job.triggered_by_invocation_id is not None:
            return job.triggered_by_invocation_id
        # Defensive fallback: a job row predating Phase 3 (or enqueued by an
        # endpoint that forgot to seed) has no pre-allocated invocation. Seed
        # one on the fly so the team still has a valid workflow to load.
        specs = team.plan_workflow(ws, prior)
        return workflow_mod.seed_pending_workflow(
            ws, team=team.name, tasks=specs, source_task_id=job.task_id
        )

    def _fail_workspace(
        self, ws: Workspace, reason: str, blockers: list[park_mod.Blocker] | None = None,
    ) -> None:
        lock = self._get_workspace_lock(ws.workspace_id)
        try:
            with lock:
                ws = workspace_mod.read(ws.task_id, root=self._workspaces_root)
                if ws.meta.tasks:
                    return
                resolved_blockers = (
                    blockers if blockers is not None else park_mod.synthetic_blocker(reason)
                )
                ws = workspace_mod.transition(
                    ws,
                    Status.AWAITING_INPUT,
                    park_reason=park_mod.ParkReason.CRASH,
                    blockers=resolved_blockers,
                )
        except (OSError, workspace_mod.WorkspaceError):
            logger.exception(
                "failed to flip workspace %s to AWAITING_INPUT", ws.task_id
            )
            return
        _base._schedule_manager_summary(  # pyright: ignore[reportPrivateUsage]
            ws, None, self._pubsub, self._queue,
            workspace_index=self._workspace_index,
            workspaces_root=self._workspaces_root,
        )

    def _apply_router_decision(
        self,
        ws: Workspace,
        handover: Handover,
        task_id: str | None = None,
        decision: RouterDecision | None = None,
    ) -> None:
        _tid = task_id if task_id is not None else handover.task_id
        if decision is None:
            effective = self._effective_teams(ws)
            policy = self._pinned_policy(ws)
            decision = select_next_team(handover, effective, policy)
            decision = self._enforce_pr_merge_gate(ws, handover, decision, effective)
            decision = self._enforce_deploy_gate(handover, decision, effective)
        decision, handover = self._enforce_fix_ceiling(ws, handover, decision, task_id=_tid)
        decision = self._enforce_loop_guard(ws, handover, decision, task_id=_tid)
        decision = deploy_wait_gate.apply(handover, decision)
        decision = _brief_refine.apply(ws, handover, decision)
        self._handle_decision(ws, handover, decision, _tid)
        self._publish_router_event(ws, handover, decision, _tid)

    def _maybe_populate_keep_manifest(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> Workspace:
        return _scenario_rebind.maybe_populate_keep_manifest(ws, handover, decision, task_id)

    def _audit_sibling_overlap(
        self, ws: Workspace, handover: Handover, task_id: str
    ) -> None:
        if (
            handover.handover_type != "terminal"
            or handover.from_team != "pr_merge"
            or handover.pr_number is None
        ):
            return
        siblings = _reconcile._sibling_manifests(ws, task_id)  # pyright: ignore[reportPrivateUsage]
        if not siblings:
            return
        forge_target = _base.forge_target_for_ws(ws)
        diff_files = _pr_branch.fetch_diff_files(handover.pr_number, forge_target=forge_target)
        if diff_files is None:
            return
        overlaps = find_sibling_overlap(sorted(diff_files), siblings)
        if not overlaps:
            return
        self._pubsub.publish(
            ws.workspace_id,
            TeamEvent(
                "sibling_overlap_detected",
                {
                    "task_id": task_id,
                    "pr_number": str(handover.pr_number),
                    "overlaps": json.dumps(overlaps, sort_keys=True),
                },
            ),
        )
        append_jsonl_audit(
            logs_root() / _pr_audit_log.AUDIT_FILE,
            {"task_id": task_id, "pr_number": handover.pr_number, "overlaps": overlaps},
            log_label="sibling_overlap",
        )

    def _enforce_loop_guard(
        self, ws: Workspace, handover: Handover, decision: RouterDecision,
        *, task_id: str | None = None,
    ) -> RouterDecision:
        if decision.kind != "enqueue" or decision.team is None:
            return decision
        spiral = detect_spiral(ws, handover, decision.team, task_id=task_id)
        if spiral is not None:
            spiral_reason = f"spiral:{spiral.kind}: {spiral.evidence}"
            save_handover(ws, spiral_escalation_handover(ws, handover, spiral_reason))
            return RouterDecision(kind="escalate", reason=spiral_reason)
        override = check_loop_guard(ws, handover, decision.team, task_id=task_id)
        return override if override is not None else decision

    def _enforce_deploy_gate(
        self,
        handover: Handover,
        decision: RouterDecision,
        effective_teams: dict[str, Team] | None = None,
    ) -> RouterDecision:
        teams = effective_teams if effective_teams is not None else self._teams
        if (
            decision.kind != "done"
            or handover.from_team != "pr_merge"
            or handover.pr_number is None
        ):
            return decision
        if "deploy_verify" not in teams:
            return decision
        return deploy_gate_route.deploy_gate_decision(handover.pr_number)

    def _enforce_pr_merge_gate(
        self,
        ws: Workspace,
        handover: Handover,
        decision: RouterDecision,
        effective_teams: dict[str, Team] | None = None,
    ) -> RouterDecision:
        if (
            decision.kind != "enqueue"
            or decision.team != "pr_merge"
            or handover.pr_number is None
        ):
            return decision
        pr_number = handover.pr_number
        attempt = merge_gate_mod.attempt_recovery(ws, pr_number, recovered_outcome="pr_merge")
        return self._decision_for_gate_attempt(
            ws, handover, pr_number, decision, attempt, effective_teams,
        )

    def _decision_for_gate_attempt(
        self, ws: Workspace, handover: Handover, pr_number: int, decision: RouterDecision,
        attempt: merge_gate_mod.MergeGateAttempt, effective_teams: dict[str, Team] | None,
    ) -> RouterDecision:
        match attempt:
            case merge_gate_mod.MergeGateNotAttempted() | merge_gate_mod.MergeGateAttemptFailed():
                return decision
            case merge_gate_mod.MergeGateRecovered():
                return _delay_for_ci_reset(decision, attempt.block_class, clock=self._clock)
            case merge_gate_mod.MergeGateBlocked():  # pragma: no branch
                result = self._pr_merge_remediation(
                    ws, handover, decision, attempt, effective_teams,
                )
                merge_gate_mod.record_merge_gate_audit(
                    ws.task_id, pr_number, attempt.block_class, False, _decision_outcome(result),
                    merge_gate_mod.reconcile_outcome_str(attempt.reconcile),
                    brief_attached=_brief_attached(result),
                )
                return result

    def _pr_merge_remediation(
        self, ws: Workspace, handover: Handover, decision: RouterDecision,
        attempt: merge_gate_mod.MergeGateBlocked, effective_teams: dict[str, Team] | None,
    ) -> RouterDecision:
        teams = effective_teams if effective_teams is not None else self._teams
        block_class = attempt.block_class
        if merge_gate_mod.routes_to_merge_lane(block_class):
            return decision
        reason, bc, blockers = merge_gate_mod.pr_merge_remediation_context(
            handover, attempt.readiness, block_class, attempt.forge_target
        )
        if block_class in _BOUNDED_RETRY_REMEDIATION_CLASSES:
            retry = self._pr_merge_conflict_or_retry(
                ws, handover.pr_number, reason, bc, blockers, attempt, teams,
            )
            if retry is not None:
                return retry
            return RouterDecision(
                kind="await_human", reason=reason, block_class=bc, blockers=tuple(blockers)
            )
        if block_class is merge_recovery.MergeBlockClass.SONAR_HOTSPOT:
            return RouterDecision(
                kind="await_human", reason=reason, block_class=bc, blockers=tuple(blockers)
            )
        return self._fix_or_await(ws, reason, bc, blockers, attempt, teams)

    def _fix_or_await(
        self, ws: Workspace, reason: str, bc: str, blockers: list[park_mod.Blocker],
        attempt: merge_gate_mod.MergeGateBlocked, teams: dict[str, Team],
    ) -> RouterDecision:
        if "implementation_fix" in teams and team_is_dispatchable(ws, "implementation_fix"):
            return RouterDecision(
                kind="enqueue",
                team="implementation_fix",
                reason=reason,
                block_class=bc,
                blockers=tuple(blockers),
                instructions=merge_gate_mod.render_remediation_instructions(
                    attempt.block_class, "; ".join(attempt.readiness.failed_reasons),
                    tuple(blockers),
                ),
            )
        return RouterDecision(
            kind="await_human", reason=reason, block_class=bc, blockers=tuple(blockers)
        )

    def _pr_merge_conflict_or_retry(
        self, ws: Workspace, pr_number: int | None, reason: str, bc: str,
        blockers: list[park_mod.Blocker], attempt: merge_gate_mod.MergeGateBlocked,
        teams: dict[str, Team],
    ) -> RouterDecision | None:
        readiness = attempt.readiness
        if merge_gate_mod.conflict_fix_decision(
            blockers, attempt.reconcile, attempt.block_class,
            readiness.head_sha, readiness.head_ref_name,
        ):
            return self._fix_or_await(ws, reason, bc, blockers, attempt, teams)
        return self._maybe_retry_pr_merge(pr_number, reason, bc, attempt.forge_target)

    def _maybe_retry_pr_merge(
        self, pr_number: int | None, reason: str, block_class: str,
        forge_target: _engine_config_mod.ForgeTarget,
    ) -> RouterDecision | None:
        if pr_number is None:
            return None
        retry_count = _pr_state_paths.load_remediation_retry_count(pr_number, forge_target)
        if retry_count >= REVISIT_CAP:
            return None
        _pr_state_paths.bump_remediation_retry_count(pr_number, forge_target)
        delay = _usage_limit._positive_float_env(  # pyright: ignore[reportPrivateUsage]
            _PR_MERGE_REMEDIATION_RETRY_DELAY_ENV,
            _DEFAULT_PR_MERGE_REMEDIATION_RETRY_DELAY_S,
        )
        return RouterDecision(
            kind="enqueue",
            team="pr_merge",
            reason=f"{reason} — auto-retrying pr_merge (attempt {retry_count + 1})",
            block_class=block_class,
            eligible_at=self._clock.now().value + delay,
        )

    def _handle_decision(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> None:
        if decision.kind not in ("enqueue", "done"):
            self._finalize_blocked_decision(ws, handover, decision, task_id)
            return
        ws, rollback = _workspace_dag.stamp_for_advance(ws, decision.block_class)
        try:
            self._advance_or_enqueue(ws, handover, decision, task_id)
        except Exception:
            if rollback is not None:
                _workspace_dag.restore_escalated(ws, rollback)
            raise

    def _finalize_blocked_decision(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> None:
        park_reason = park_mod.park_reason_for_decision(decision.kind, decision.block_class)
        blockers = (
            list(decision.blockers)
            if decision.blockers
            else park_mod.synthetic_blocker(decision.reason or "")
        )
        ws = workspace_mod.transition(
            ws,
            _STATUS_BY_DECISION_KIND[decision.kind],
            merge_block_class=decision.block_class,
            clear_merge_block_class=True,
            park_reason=park_reason,
            blockers=blockers,
        )
        if ws.meta.tasks:
            try:
                ws = workspace_mod.update_task_status(
                    ws, task_id, TaskStatus.AWAITING_INPUT,
                    blocked_handover_id=handover.handover_id,
                )
            except workspace_mod.WorkspaceError:
                logger.warning(
                    "_finalize_blocked_decision: task %r not found in ws %s",
                    task_id, ws.workspace_id,
                )
        _base._schedule_manager_summary(  # pyright: ignore[reportPrivateUsage]
            ws, handover.full_synthesis, self._pubsub, self._queue,
            stop_reason=decision.reason,
            workspace_index=self._workspace_index,
            workspaces_root=self._workspaces_root,
        )

    def _advance_or_enqueue(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> None:
        if decision.kind == "enqueue" and decision.team is not None:
            composed, origin = sticky_instructions.for_enqueue(
                ws, task_id, decision.team, decision.instructions
            )
            _base.enqueue_with_pending_workflow(
                queue=self._queue,
                pubsub=self._pubsub,
                workspace=ws,
                team=self._teams[decision.team],
                prior=handover,
                parent_handover_id=handover.handover_id,
                task_id=task_id,
                eligible_at=decision.eligible_at,
                instructions=composed,
                instructions_origin=origin,
            )
        else:
            self._advance_workspace_dag(ws, task_id, handover=handover)

    def _publish_router_event(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> None:
        payload: dict[str, str] = {
            "task_id": task_id,
            "from_team": handover.from_team,
            "kind": decision.kind,
            "reason": decision.reason or "",
        }
        if decision.team is not None:
            payload["next_team"] = decision.team
        if decision.block_class is not None:
            payload["block_class"] = decision.block_class
        self._pubsub.publish(ws.workspace_id, TeamEvent(f"router_{decision.kind}", payload))

    def _observe_task_failure(self, job: Job) -> None:
        workspace_id = job.workspace_id or job.task_id
        lock = self._get_workspace_lock(workspace_id)
        try:
            with lock:
                ws = workspace_mod.read(workspace_id, root=self._workspaces_root)
                if not ws.meta.tasks:
                    return
                task_ids = {t.id for t in ws.meta.tasks}
                if job.task_id not in task_ids:
                    logger.warning(
                        "_observe_task_failure: task_id %r not in workspace %s meta",
                        job.task_id,
                        ws.workspace_id,
                    )
                    return
                ws, terminal_ws = _reconcile._propagate_and_cancel(  # pyright: ignore[reportPrivateUsage]
                    self._queue, ws, job.task_id
                )
                if terminal_ws is not None:
                    _base._schedule_manager_summary(  # pyright: ignore[reportPrivateUsage]
                        terminal_ws, None, self._pubsub, self._queue,
                        workspace_index=self._workspace_index,
                        workspaces_root=self._workspaces_root,
                    )
                else:
                    self._maybe_recover_frozen_chain(ws)
        except Exception:  # noqa: BLE001
            logger.exception(
                "_observe_task_failure: failed for job %d", job.id
            )
