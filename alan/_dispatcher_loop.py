from __future__ import annotations

import logging
import threading

import alan._dispatcher_base as _base
import alan._dispatcher_selfheal as _selfheal
from alan import claude_cli as claude_cli_mod
from alan import deploy_gate_sweep as deploy_gate_sweep_mod
from alan import engine_config as _engine_config_mod
from alan import project as project_mod
from alan import project_doc as project_doc_mod
from alan import unblock_sweep as unblock_sweep_mod
from alan import worker_liveness
from alan.clock import Epoch, Monotonic
from alan.dispatch import recover_prior_pr
from alan.engine_preflight import blocking_issues
from alan.events import PubSub
from alan.handover import Handover, find_handover_by_id
from alan.project_doc_context import set_active_project_doc_path
from alan.queue import WORKER_STATE_META_KEY, Job, QueueError
from alan.slot_pool import PoolState, SlotPool, set_active_pool
from alan.teams._base import TeamEvent
from alan.workspace import Workspace

logger = logging.getLogger("alan.dispatcher")

_DEFAULT_POLL_INTERVAL = 0.05
_DEPLOY_GATE_SWEEP_INTERVAL_S = 60.0
_WORKER_LIVENESS_REPUBLISH_INTERVAL_S = 60.0
_UNBLOCK_SWEEP_INTERVAL_S = unblock_sweep_mod.SWEEP_INTERVAL_S


def _stop_failure_message(timeout: float | None) -> str:  # pyright: ignore[reportUnusedFunction]
    suffix = "" if timeout is None else f" within {timeout}s"
    return f"dispatcher thread(s) did not stop{suffix}"


def _resolve_project_doc_path(ws: Workspace) -> str | None:
    if ws.meta.project_id is None:
        return None
    try:
        project = project_mod.read(ws.meta.project_id)
        return str(project_doc_mod.doc_path(project))
    except (project_mod.ProjectError, project_doc_mod.ProjectDocError):
        return None


class LoopMixin(_base.DispatcherState):
    @property
    def pubsub(self) -> PubSub:
        return self._pubsub

    @property
    def live_worker_count(self) -> int:
        with self._alive_lock:
            return self._alive_worker_count

    @property
    def configured_worker_count(self) -> int:
        return self._concurrency

    def run_once(self) -> bool:
        job = self._queue.claim()
        if job is None:
            return False
        self._execute(job)
        return True

    def _publish_worker_state(self) -> None:
        try:
            with self._alive_lock:
                if self._stop.is_set():
                    self._queue.delete_meta(WORKER_STATE_META_KEY)
                    return
                reading = worker_liveness.DispatchLoopReading(
                    live_loops=self._alive_worker_count,
                    configured_loops=self._concurrency,
                    observed_at=self._clock.now().value,
                )
                self._queue.set_meta(WORKER_STATE_META_KEY, worker_liveness.encode(reading))
        except Exception:  # noqa: BLE001
            logger.exception("_publish_worker_state: failed to write queue_meta")

    def _on_worker_exit(self) -> None:
        with self._alive_lock:
            self._alive_worker_count -= 1
        self._publish_worker_state()

    def _maybe_republish_worker_liveness(self) -> None:
        now = self._clock.monotonic()
        with self._alive_lock:
            if now.value < self._worker_liveness_next_at:
                return
            self._worker_liveness_next_at = now.value + _WORKER_LIVENESS_REPUBLISH_INTERVAL_S
        self._publish_worker_state()

    def _maybe_sweep_deploy_gates(self) -> None:
        now = self._clock.monotonic()
        with self._deploy_gate_sweep_lock:
            if now < self._deploy_gate_sweep_next_at:
                return
            self._deploy_gate_sweep_next_at = Monotonic(
                now.value + _DEPLOY_GATE_SWEEP_INTERVAL_S
            )
        deploy_gate_sweep_mod.promote_covered_jobs(self._queue)

    def _maybe_sweep_unblocks(self) -> None:
        now = self._clock.monotonic()
        with self._deploy_gate_sweep_lock:
            if now.value < self._unblock_sweep_next_at:
                return
            self._unblock_sweep_next_at = now.value + _UNBLOCK_SWEEP_INTERVAL_S
        if self._workspace_index is None or self._workspaces_root is None:
            return
        try:
            unblock_sweep_mod.sweep(
                queue=self._queue,
                pubsub=self._pubsub,
                workspace_index=self._workspace_index,
                workspaces_root=self._workspaces_root,
                now=self._clock.now().value,
                lock_for=self._get_workspace_lock,
                enqueue_workflow=_base.enqueue_with_pending_workflow,
            )
        except Exception:  # noqa: BLE001
            logger.exception("_maybe_sweep_unblocks: sweep failed")

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._maybe_sweep_deploy_gates()
                    self._maybe_sweep_unblocks()
                    self._maybe_republish_worker_liveness()
                    if not self.run_once():
                        wait = self._poll_interval
                        paused = self._queue.paused_until()
                        if paused is not None:
                            wait = min(
                                Epoch(paused) - self._clock.now(), self._poll_interval
                            )
                        self._stop.wait(wait)
                except Exception:  # noqa: BLE001
                    logger.exception("_loop: unexpected error in iteration; continuing")
                    self._stop.wait(self._poll_interval)
        finally:
            self._on_worker_exit()

    def _safe_fail_job(self, job: Job, reason: str) -> None:
        try:
            self._fail_job(job, reason)
        except QueueError:
            logger.warning(
                "_safe_fail_job: job %d no longer RUNNING (QueueError); skipping", job.id
            )
        except Exception:  # noqa: BLE001
            logger.exception("_safe_fail_job: unexpected error for job %d", job.id)

    def _hold_until_ready(self, job: Job, workspace: Workspace) -> bool:
        if not blocking_issues(_base.governing_engine_config(workspace)):
            return False
        try:
            self._queue.hold_until_ready(job.id)
        except QueueError:
            logger.warning(
                "_hold_until_ready: job %d no longer RUNNING (QueueError); skipping", job.id
            )
        return True

    def _execute(self, job: Job) -> None:
        try:
            team = self._teams.get(job.team)
            if team is None:
                self._fail_job(job, f"unknown team: {job.team}")
                return
            resolved = self._read_workspace_and_pool(job)
            if resolved is None:
                return
            workspace, pool = resolved
            if self._hold_until_ready(job, workspace):
                return
            set_active_pool(pool)
            claude_cli_mod.set_active_cache_env(
                _base.governing_engine_config(workspace).cache_env
            )
            set_active_project_doc_path(_resolve_project_doc_path(workspace))
            try:
                try:
                    prior = self._load_prior(workspace, job)
                except _selfheal._MissingParentHandoverError as exc:  # pyright: ignore[reportPrivateUsage]
                    self._fail_job(job, f"parent handover missing: {exc}")
                    return
                self._run_team(job, workspace, team, prior)
            finally:
                set_active_project_doc_path(None)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "_execute: unexpected error for job %d task=%s team=%s",
                job.id, job.task_id, job.team,
            )
            self._safe_fail_job(job, f"internal error: {exc}")

    def register_pool(self, cfg: _engine_config_mod.EngineConfig) -> None:
        if self._pool_factory is None:
            return
        pool = self._pool_factory(cfg)
        with self._slot_pools_lock:
            self._slot_pools_by_engine[cfg.repo.slug] = pool
        self._pubsub.publish_global(
            TeamEvent("pool_lifecycle_changed", {"slug": cfg.repo.slug})
        )
        threading.Thread(
            target=self._warm_pool, args=(pool, cfg.repo.slug), daemon=True
        ).start()

    def _warm_pool(self, pool: SlotPool, slug: str) -> None:
        # publish_global runs on this daemon thread: it copies listeners under
        # PubSub._lock, then calls the module-level _fanout (alan/events.py)
        # synchronously after releasing the lock — the asyncio-loop hop back to
        # SSE subscribers is events.py's call_soon_threadsafe, not ours.
        try:
            pool.reset_all()
            pool.mark_warm()
        except Exception:  # noqa: BLE001 — warmup is best-effort; see slot_pool.md
            logger.exception("_warm_pool: warmup failed")
        finally:
            self._pubsub.publish_global(TeamEvent("pool_lifecycle_changed", {"slug": slug}))

    def dispose_pool(self, slug: str) -> None:
        with self._slot_pools_lock:
            pool = self._slot_pools_by_engine.get(slug)
        if pool is None:
            return
        pool.begin_dispose()
        with self._slot_pools_lock:
            # Only remove the entry we validated/quiesced above — if
            # register_pool raced in and replaced it with a fresh pool while
            # we held no lock (between the two acquisitions here), popping
            # unconditionally would evict the *new* pool from the dict.
            if self._slot_pools_by_engine.get(slug) is pool:
                self._slot_pools_by_engine.pop(slug, None)
        pool.finish_dispose()
        self._pubsub.publish_global(TeamEvent("pool_lifecycle_changed", {"slug": slug}))

    def pool_states(self) -> dict[str, PoolState]:
        with self._slot_pools_lock:
            keyed = dict(self._slot_pools_by_engine)
        states = {slug: pool.snapshot(slug) for slug, pool in keyed.items()}
        if self._slot_pool is not None:
            default_slug = _base._governing_engine_config(None).repo.slug  # pyright: ignore[reportPrivateUsage]
            states[default_slug] = self._slot_pool.snapshot(default_slug)
        return states

    def _load_prior(self, workspace: Workspace, job: Job) -> Handover | None:
        if job.parent_handover_id is None:
            return None
        found = find_handover_by_id(workspace, job.parent_handover_id)
        if found is None:
            raise _selfheal._MissingParentHandoverError(job.parent_handover_id)  # pyright: ignore[reportPrivateUsage]
        return recover_prior_pr(workspace, job.task_id, found)
