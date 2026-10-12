from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

from alan import default_profile, live_staleness, secrets_store, store
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import live_hidden
from alan.finisher_rate import (
    FINISHER_EPOCH,
    FINISHER_FLOOR,
    FinisherRate,
    compute_finisher_rate,
)
from alan.handover import Handover, prune_handover_files
from alan.health import SWEEP_CADENCE_SECONDS as _CADENCE_SECONDS
from alan.health import (
    HealthState,
    compute_health_state,
    save_finisher_rate,
    save_health_state,
    save_learning_metrics,
)
from alan.health_repo_metrics import AUDIT_TAIL_BYTES as _FR_AUDIT_TAIL_BYTES
from alan.health_repo_metrics import read_audit_tail as _hrm_read_audit_tail
from alan.learning_metrics import (
    MERGE_GATE_WINDOW_HOURS,
    LearningMetrics,
    first_pass_approval_rate,
    mean_fix_cycles_per_merged_pr,
    mean_fix_cycles_when_fixed,
    merge_gate_recovery_rates,
    pct_merged_prs_needing_fix,
    restart_recovery_counts,
)
from alan.learning_metrics import (
    sample_size as _lm_sample_size,
)
from alan.queue import JobStatus, Queue
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import (
    EmitFn,
    TeamEvent,
    chain_id_for,
    emit_node,
    make_handover_id,
    reject_resume_inv_id,
    workflow_lifecycle,
)
from alan.teams._triage_auto_update import auto_update_surfaces
from alan.teams._triage_config_quarantine import config_quarantine_surfaces
from alan.teams._triage_driver_loops import (
    driver_loop_config_quarantine_surfaces,
    driver_loop_surfaces,
)
from alan.teams._triage_sweep_status import (
    TriageSweepStatus,
    load_triage_sweep_status,
)
from alan.teams._triage_sweep_status import (
    _write_triage_sweep_status as _write_triage_sweep_status,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams.triage_pregate import (
    ClassifiedSurface,
    SurfaceRecord,
    decide,
    gather_all,
    load_surface_state,
    save_surface_state,
    surface_fingerprint,
)
from alan.trajectory import derive_trajectories
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_TEAM_NAME = "triage"
_NODE_ROLE = "sensor_sweep"
_MODEL = "none"
_AUDIT_FILE = "triage_audit.jsonl"
_CADENCE_META_KEY = "triage_last_scheduled_at"
_DEFAULT_MAIN_CI_SURFACE_ID = f"main_ci:{_ENGINE_DEFAULT.repo.slug}"
_DEFAULT_DEPLOY_SURFACE_ID = f"deploy:{_ENGINE_DEFAULT.repo.slug}"
_SLOTS_PRUNED_ACTION = "invocation_slots_pruned"
_HANDOVERS_PRUNED_ACTION = "handover_slots_pruned"
_REVISIT_KEYS_PRUNED_ACTION = "revisit_keys_pruned"
_SEED_FAILURE_ACTION = "seed_triage_failed"
_SWEEP_COMPLETE_ACTION = "sweep_complete"
_ERROR_LOOKBACK_HOURS = 2
_MERGE_AUDIT_FILE = "pr_merge_audit.jsonl"
_MERGE_GATE_AUDIT_FILE = "merge_gate_audit.jsonl"
_MERGE_GATE_RECOVERY_META_KEY = "merge_gate_recovery_v1"
_RESUME_AUDIT_FILE = "resume_audit.jsonl"
_RESTART_RECOVERY_META_KEY = "restart_recovery_v1"
_RESTART_RECOVERY_WINDOW_HOURS = 168.0
_OUTCOME_SUCCESS = "success"
_OUTCOME_DEGRADED = "degraded"
_OUTCOME_FAILURE = "failure"

ROLE_SPECS: tuple[PendingTaskSpec, ...] = (
    PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=_MODEL),
)


def _classify_sweep_outcome(*, sweep_failed: bool, degraded: bool) -> str:
    if sweep_failed:
        return _OUTCOME_FAILURE
    if degraded:
        return _OUTCOME_DEGRADED
    return _OUTCOME_SUCCESS

ReconcileIncidentsFn = Callable[[Queue, Path | None, HealthState, datetime], None]
RunRetentionFn = Callable[[], object]
RetentionCapacityFn = Callable[[Path | None], list[ClassifiedSurface]]
CheckCredentialExpiryFn = Callable[[datetime], None]


class RedMainRun(NamedTuple):
    slug: str
    run_id: str
    sha: str | None


OnMainCiRedFn = Callable[[Sequence[RedMainRun]], None]

_T = TypeVar("_T")


class _GuardedStep(NamedTuple):
    degraded: str | None
    log_msg: str


class _GuardedSteps(NamedTuple):
    credential_expiry: _GuardedStep
    finisher_rate: _GuardedStep
    retention_capacity: _GuardedStep
    stale_live_process: _GuardedStep
    driver_loops: _GuardedStep
    driver_loop_quarantine: _GuardedStep
    credential_expiry_surfaces: _GuardedStep
    main_ci_red: _GuardedStep
    health_state: _GuardedStep
    reconcile_incidents: _GuardedStep
    auto_update: _GuardedStep


_GUARDED_STEPS = _GuardedSteps(
    credential_expiry=_GuardedStep("credential_expiry", "triage: credential expiry check failed"),
    finisher_rate=_GuardedStep("finisher_rate", "triage: failed to compute finisher rate"),
    retention_capacity=_GuardedStep(
        "retention_capacity", "triage: retention capacity check failed"
    ),
    stale_live_process=_GuardedStep(None, "triage: stale live process check failed"),
    driver_loops=_GuardedStep("driver_loops", "triage: driver loop status check failed"),
    driver_loop_quarantine=_GuardedStep(
        "driver_loops", "triage: driver loop quarantine check failed"
    ),
    credential_expiry_surfaces=_GuardedStep(
        "credential_expiry_surfaces", "triage: credential expiry surface check failed"
    ),
    main_ci_red=_GuardedStep("main_ci_red", "triage: main CI red hook failed"),
    health_state=_GuardedStep("health_state", "triage: failed to save health state"),
    reconcile_incidents=_GuardedStep(
        "reconcile_incidents", "triage: failed to reconcile incidents"
    ),
    auto_update=_GuardedStep("auto_update", "triage: auto-update surface check failed"),
)


def _guarded(
    step: _GuardedStep, degraded: list[str], call: Callable[[], _T], fallback: _T
) -> _T:
    try:
        return call()
    except Exception:  # noqa: BLE001
        logger.exception(step.log_msg)
        if step.degraded is not None:
            degraded.append(step.degraded)
        return fallback


class TriageTeam:
    name = _TEAM_NAME
    supports_resume = False

    def __init__(
        self,
        *,
        queue: Queue,
        workspaces_root: Path | None,
        reconcile_incidents: ReconcileIncidentsFn | None = None,
        run_retention: RunRetentionFn | None = None,
        retention_capacity_findings: RetentionCapacityFn | None = None,
        check_credential_expiry: CheckCredentialExpiryFn | None = None,
        on_main_ci_red: OnMainCiRedFn | None = None,
    ) -> None:
        self.queue = queue
        self.workspaces_root = workspaces_root
        self.reconcile_incidents = reconcile_incidents
        self.run_retention = run_retention
        self.retention_capacity_findings = retention_capacity_findings
        self.check_credential_expiry = check_credential_expiry
        self.on_main_ci_red = on_main_ci_red

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return list(ROLE_SPECS)

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del instructions, task_id, on_proc_started
        reject_resume_inv_id(self.name, resume_inv_id)
        surfaces: list[ClassifiedSurface] = []
        new_state: dict[str, SurfaceRecord] | None = None
        started = time.monotonic()
        degraded: list[str] = []
        sweep_failed = True
        try:
            with workflow_lifecycle(
                workspace, team_name=self.name, inv_id=inv_id, emit=emit
            ) as graph:
                node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
                workflow_mod.save_workflow(workspace, graph.doc)
                emit_node(emit, "node_started", inv_id, node)
                surfaces, new_state, degraded = self._run_sensors()
                emit(TeamEvent("health_state_saved", {}))
                self._maybe_run_retention(degraded)
                done = graph.complete_node(_NODE_ROLE)
                workflow_mod.save_workflow(workspace, graph.doc)
                emit_node(emit, "node_completed", inv_id, done)
            sweep_failed = False
        finally:
            if new_state is not None:
                _save_surface_state_safe(self.queue, new_state, degraded)
            outcome = _classify_sweep_outcome(sweep_failed=sweep_failed, degraded=bool(degraded))
            duration_seconds = time.monotonic() - started
            _write_audit(
                workspace, inv_id, surfaces, outcome=outcome, duration_seconds=duration_seconds
            )
            self._schedule_next_sweep(workspace)
            _write_triage_sweep_status(outcome, duration_seconds)
        return _build_handover(workspace, prior, inv_id, surfaces)

    def _maybe_run_retention(self, degraded: list[str]) -> None:
        if self.run_retention is None:
            return
        try:
            self.run_retention()
        except Exception:  # noqa: BLE001 - retention must never break the sweep
            logger.exception("triage: retention hook failed")
            degraded.append("retention")

    def _augmented_surfaces(
        self,
        surfaces: list[ClassifiedSurface],
        audit_dir: Path,
        now: datetime,
        degraded: list[str],
    ) -> list[ClassifiedSurface]:
        steps = _GUARDED_STEPS
        fr: FinisherRate | None = _guarded(
            steps.finisher_rate,
            degraded,
            lambda: _compute_finisher_rate(self.queue, self.workspaces_root, audit_dir),
            None,
        )
        if fr is not None and fr.floor_alert:
            surfaces = [*surfaces, _finisher_regression_surface(fr)]
        retention = self.retention_capacity_findings
        if retention is not None:
            found: list[ClassifiedSurface] = _guarded(
                steps.retention_capacity, degraded, lambda: retention(self.workspaces_root), []
            )
            surfaces = [*surfaces, *found]
        stale = _guarded(
            steps.stale_live_process,
            degraded,
            lambda: _stale_live_process_surface(surfaces, now),
            None,
        )
        if stale is not None:
            surfaces = [*surfaces, stale]
        loops: list[ClassifiedSurface] = _guarded(
            steps.driver_loops, degraded, lambda: driver_loop_surfaces(queue=self.queue), []
        )
        quarantined: list[ClassifiedSurface] = _guarded(
            steps.driver_loop_quarantine, degraded, driver_loop_config_quarantine_surfaces, []
        )
        metas: list[secrets_store.SecretRecordMeta] = _guarded(
            steps.credential_expiry_surfaces,
            degraded,
            lambda: secrets_store.expiring_secrets(now),
            [],
        )
        auto_update: list[ClassifiedSurface] = _guarded(
            steps.auto_update, degraded, lambda: auto_update_surfaces(now=now), []
        )
        return [
            *surfaces,
            *loops,
            *quarantined,
            *(_credential_expiry_surface(meta) for meta in metas),
            *config_quarantine_surfaces(),
            *auto_update,
        ]

    def _run_sensors(
        self,
    ) -> tuple[list[ClassifiedSurface], dict[str, SurfaceRecord], list[str]]:
        now = datetime.now(UTC)
        degraded: list[str] = []
        steps = _GUARDED_STEPS
        check_credential_expiry = self.check_credential_expiry
        if check_credential_expiry is not None:
            _guarded(
                steps.credential_expiry, degraded, lambda: check_credential_expiry(now), None
            )
        stored = load_surface_state(self.queue)
        audit_dir = logs_root()
        surfaces = gather_all(self.workspaces_root, self.queue, audit_dir, _ERROR_LOOKBACK_HOURS)
        surfaces = self._augmented_surfaces(surfaces, audit_dir, now, degraded)
        self._notify_main_ci_red(surfaces, degraded)
        health_state = compute_health_state(surfaces, now, workspaces_root=self.workspaces_root)
        _guarded(
            steps.health_state, degraded, lambda: save_health_state(self.queue, health_state), None
        )
        reconcile = self.reconcile_incidents
        if reconcile is not None:
            _guarded(
                steps.reconcile_incidents,
                degraded,
                lambda: reconcile(self.queue, self.workspaces_root, health_state, now),
                None,
            )
        _compute_learning_metrics_safe(self.queue, audit_dir, now, degraded=degraded)
        _compute_merge_gate_recovery_safe(self.queue, audit_dir, now, degraded=degraded)
        _compute_restart_recovery_safe(self.queue, audit_dir, now, degraded=degraded)
        eligible, new_state = decide(surfaces, stored, now)
        if eligible:
            eligible_fps = {
                s.surface_id: surface_fingerprint(s.fp_obs if s.fp_obs is not None else s.obs)
                for s in eligible
            }
            _mark_diagnosed(new_state, eligible_fps)
        return surfaces, new_state, degraded

    def _notify_main_ci_red(self, surfaces: list[ClassifiedSurface], degraded: list[str]) -> None:
        hook = self.on_main_ci_red
        if hook is None:
            return
        _guarded(
            _GUARDED_STEPS.main_ci_red,
            degraded,
            lambda: hook(_red_main_ci_runs(surfaces)),
            None,
        )

    def _schedule_next_sweep(self, workspace: Workspace) -> None:
        try:
            self._do_schedule_next_sweep(workspace)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "triage: failed to schedule next sweep for %s", workspace.task_id
            )
            write_seed_failure_audit("reschedule", str(exc))

    def _do_schedule_next_sweep(self, workspace: Workspace) -> None:
        try:
            workspace = workspace_mod.read(workspace.task_id, root=self.workspaces_root)
        except workspace_mod.WorkspaceError:
            logger.warning(
                "triage: workspace %s gone; skipping reschedule", workspace.task_id
            )
            return
        if live_hidden(workspace.meta.engine_key) or not default_profile.triage_sweep_enabled():
            return
        pending = self.queue.list_jobs(task_id=workspace.task_id, status=JobStatus.PENDING)
        if pending:
            return
        prune_triage_invocations(workspace)
        inv_id = workflow_mod.seed_pending_workflow(
            workspace,
            team=_TEAM_NAME,
            tasks=list(ROLE_SPECS),
            source_task_id=workspace.task_id,
        )
        self.queue.enqueue(
            workspace.task_id,
            _TEAM_NAME,
            triggered_by_invocation_id=inv_id,
            eligible_at=time.time() + _CADENCE_SECONDS,
        )
        self.queue.set_meta(_CADENCE_META_KEY, str(time.time()))

def _red_main_run(surface: ClassifiedSurface) -> RedMainRun | None:
    if surface.kind != "main_ci" or not surface.is_blocked:
        return None
    slug, run_id, sha = (surface.obs.get(key) for key in ("repo", "run_id", "sha"))
    if not isinstance(slug, str) or not isinstance(run_id, (int, str)):
        return None
    return RedMainRun(slug, str(run_id), sha if isinstance(sha, str) else None)


def _red_main_ci_runs(surfaces: list[ClassifiedSurface]) -> list[RedMainRun]:
    found = (_red_main_run(surface) for surface in surfaces)
    return [run for run in found if run is not None]


def _credential_expiry_surface(meta: secrets_store.SecretRecordMeta) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"credential_expiry:{meta.name}",
        kind="credential_expiry",
        is_blocked=True,
        blocker_reason="credential_expiry",
        obs={
            "name": meta.name,
            "expires_at": meta.expires_at,
            "engine_key": meta.name.split(":", 1)[0],
        },
    )


def _find_surface(
    surfaces: list[ClassifiedSurface], surface_id: str
) -> ClassifiedSurface | None:
    for surface in surfaces:
        if surface.surface_id == surface_id:
            return surface
    return None


def _stale_live_process_surface(
    surfaces: list[ClassifiedSurface], now: datetime
) -> ClassifiedSurface | None:
    main_ci = _find_surface(surfaces, _DEFAULT_MAIN_CI_SURFACE_ID)
    deploy = _find_surface(surfaces, _DEFAULT_DEPLOY_SURFACE_ID)
    if main_ci is None or deploy is None:
        return None
    target_sha = main_ci.obs.get("sha")
    finding, reason = live_staleness.detect(
        target_sha=target_sha,
        main_ci_status=main_ci.obs.get("status"),
        deploy_status=deploy.obs.get("status"),
        deploy_obs_empty=not deploy.obs,
        now=now,
    )
    if finding is None:
        if reason and reason != "no_divergence":
            logger.info("triage: stale live process check silent: %s", reason)
        return None
    return ClassifiedSurface(
        surface_id=f"stale_live_process:{finding.target_sha[:12]}",
        kind="stale_live_process",
        is_blocked=True,
        blocker_reason="hot_paths",
        obs={
            "served_sha": finding.served_sha,
            "target_sha": finding.target_sha,
            "hot_paths": finding.hot_paths,
        },
        fp_obs={"served_sha": finding.served_sha, "target_sha": finding.target_sha},
    )


def prune_triage_invocations(ws: Workspace) -> None:
    _audit_pruned(ws, _SLOTS_PRUNED_ACTION, workflow_mod.prune_invocation_dirs(ws))
    _audit_pruned(ws, _HANDOVERS_PRUNED_ACTION, prune_handover_files(ws))
    _audit_pruned(
        ws,
        _REVISIT_KEYS_PRUNED_ACTION,
        workspace_mod.prune_revisit_counts_for_team(ws, _TEAM_NAME),
    )


def _audit_pruned(ws: Workspace, action: str, removed: int) -> None:
    if not removed:
        return
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE,
        {
            "action": action,
            "task_id": ws.task_id,
            "removed": removed,
        },
        log_label="triage",
    )


def write_seed_failure_audit(source: str, error: str) -> None:
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE,
        {
            "action": _SEED_FAILURE_ACTION,
            "source": source,
            "error": error[:500],
        },
        log_label="triage",
    )


_NODE_TURNS_FILE = "node_turns_audit.jsonl"
_VERIFICATION_FILE = "verification_audit.jsonl"
_GIT_LOG_CMD: list[str] = [
    "git", "log", "--format=%x1e%aI%x00%s%x00%b", "--all", "--max-count=2000",
]


def _read_audit_text(path: Path) -> str:
    return _hrm_read_audit_tail(path, _FR_AUDIT_TAIL_BYTES)


def _workspace_meta_dicts(workspaces_root: Path | None) -> list[dict[str, Any]]:
    try:
        all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    except Exception:  # noqa: BLE001
        return []
    return [ws.meta.model_dump(mode="json") for ws in all_ws]


def _read_git_log_text() -> str:
    try:
        result = subprocess.run(  # noqa: S603
            _GIT_LOG_CMD,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _derive_learning_metrics(audit_dir: Path) -> LearningMetrics:
    node_turns_text = _read_audit_text(audit_dir / _NODE_TURNS_FILE)
    verification_text = _read_audit_text(audit_dir / _VERIFICATION_FILE)
    merge_audit_text = _read_audit_text(audit_dir / _MERGE_AUDIT_FILE)
    git_log_text = _read_git_log_text()
    records = derive_trajectories(
        node_turns_text, verification_text, merge_audit_text, git_log_text
    )
    per_pr = mean_fix_cycles_per_merged_pr(records)
    return LearningMetrics(
        first_pass_approval_rate=first_pass_approval_rate(records),
        mean_fix_cycles_to_merge=per_pr,
        sample_size=_lm_sample_size(records),
        mean_fix_cycles_per_merged_pr=per_pr,
        pct_merged_prs_needing_fix=pct_merged_prs_needing_fix(records),
        mean_fix_cycles_when_fixed=mean_fix_cycles_when_fixed(records),
    )


def _compute_learning_metrics_safe(
    queue: Queue, audit_dir: Path, now: datetime, *, degraded: list[str] | None = None
) -> None:
    try:
        metrics = _derive_learning_metrics(audit_dir)
        save_learning_metrics(queue, metrics, now)
    except Exception:  # noqa: BLE001
        logger.exception("triage: failed to compute learning metrics")
        if degraded is not None:
            degraded.append("learning_metrics")


def _save_meta_safe(
    queue: Queue,
    key: str,
    build: Callable[[], dict[str, object]],
    *,
    log_msg: str,
    degraded: list[str] | None = None,
) -> None:
    try:
        queue.set_meta(key, json.dumps(build()))
    except Exception:  # noqa: BLE001
        logger.exception(log_msg)
        if degraded is not None:
            degraded.append(key)


def _compute_merge_gate_recovery_safe(
    queue: Queue, audit_dir: Path, now: datetime, *, degraded: list[str] | None = None
) -> None:
    def _build() -> dict[str, object]:
        text = _read_audit_text(audit_dir / _MERGE_GATE_AUDIT_FILE)
        return {
            "rates": merge_gate_recovery_rates(text, now=now),
            "computed_at": now.isoformat(),
            "window_hours": MERGE_GATE_WINDOW_HOURS,
        }

    _save_meta_safe(
        queue, _MERGE_GATE_RECOVERY_META_KEY, _build,
        log_msg="triage: failed to compute merge gate recovery rates",
        degraded=degraded,
    )


def _compute_restart_recovery_safe(
    queue: Queue, audit_dir: Path, now: datetime, *, degraded: list[str] | None = None
) -> None:
    def _build() -> dict[str, object]:
        text = _read_audit_text(audit_dir / _RESUME_AUDIT_FILE)
        counts = restart_recovery_counts(
            text, now=now, window_hours=_RESTART_RECOVERY_WINDOW_HOURS
        )
        return {
            "counts": counts,
            "computed_at": now.isoformat(),
            "window_hours": _RESTART_RECOVERY_WINDOW_HOURS,
        }

    _save_meta_safe(
        queue, _RESTART_RECOVERY_META_KEY, _build,
        log_msg="triage: failed to compute restart recovery counts",
        degraded=degraded,
    )


def _compute_finisher_rate(
    queue: Queue, workspaces_root: Path | None, audit_dir: Path
) -> FinisherRate:
    job_text = _read_audit_text(audit_dir / "job_failure_audit.jsonl")
    merge_text = _read_audit_text(audit_dir / _MERGE_AUDIT_FILE)
    spiral_text = _read_audit_text(audit_dir / "spiral_audit.jsonl")
    metas = _workspace_meta_dicts(workspaces_root)
    fr = compute_finisher_rate(job_text, merge_text, spiral_text, metas, epoch=FINISHER_EPOCH)
    save_finisher_rate(queue, fr)
    return fr


def _finisher_regression_surface(fr: FinisherRate) -> ClassifiedSurface:
    rounded_rate = round(fr.rate, 2) if fr.rate is not None else None
    return ClassifiedSurface(
        surface_id="finisher_regression",
        kind="finisher_regression",
        is_blocked=True,
        blocker_reason="finisher_rate_below_floor",
        obs={
            "rate": fr.rate,
            "floor": FINISHER_FLOOR,
            "sample_size": fr.sample_size,
            "window_days": fr.window_days,
        },
        fp_obs={"rate": rounded_rate, "floor": FINISHER_FLOOR},
    )


def _save_surface_state_safe(
    queue: Queue, state: dict[str, SurfaceRecord], degraded: list[str] | None = None
) -> None:
    try:
        save_surface_state(queue, state)
    except Exception:  # noqa: BLE001
        logger.exception("triage: failed to save surface state")
        if degraded is not None:
            degraded.append("surface_state")


def _mark_diagnosed(
    new_state: dict[str, SurfaceRecord], eligible_fps: dict[str, str]
) -> None:
    for sid, fp in eligible_fps.items():
        if sid in new_state:
            rec = new_state[sid]
            new_state[sid] = SurfaceRecord(
                fingerprint=rec.fingerprint,
                first_seen=rec.first_seen,
                last_seen=rec.last_seen,
                last_diagnosed_fp=fp,
            )


def _write_audit(
    workspace: Workspace,
    inv_id: str,
    surfaces: list[ClassifiedSurface],
    *,
    outcome: str,
    duration_seconds: float,
) -> None:
    blocked = sum(1 for s in surfaces if s.is_blocked)
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE,
        {
            "action": _SWEEP_COMPLETE_ACTION,
            "task_id": workspace.task_id,
            "inv_id": inv_id,
            "surface_count": len(surfaces),
            "blocked_count": blocked,
            "outcome": outcome,
            "duration_seconds": duration_seconds,
        },
        log_label="triage",
    )


_SWEEPER_TAIL_ROWS = 10
_SWEEPER_TAIL_CHUNK_BYTES = 4096
_SWEEPER_STATUS_EMPTY: dict[str, Any] = {
    "timestamp_utc": None,
    "surface_count": None,
    "blocked_count": None,
}


def _tail_jsonl_lines(path: Path, max_lines: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= max_lines:
                step = min(_SWEEPER_TAIL_CHUNK_BYTES, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]


def _sweep_complete_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp_utc": row.get("timestamp_utc"),
        "surface_count": row.get("surface_count"),
        "blocked_count": row.get("blocked_count"),
    }


def _recent_action_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    action = row.get("action")
    timestamp_utc = row.get("timestamp_utc")
    if not isinstance(action, str) or not isinstance(timestamp_utc, str):
        return None
    return {"action": action, "timestamp_utc": timestamp_utc}


def _recent_audit_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = (_recent_action_entry(row) for row in reversed(rows))
    return [entry for entry in entries if entry is not None]


def _parse_audit_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows = (store.parse_jsonl_row(line) for line in lines)
    return [row for row in rows if row is not None]


def _latest_sweep_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(rows):
        if row.get("action") == _SWEEP_COMPLETE_ACTION:
            return _sweep_complete_fields(row)
    return dict(_SWEEPER_STATUS_EMPTY)


def read_sweeper_status(audit_dir: Path | None = None) -> dict[str, Any]:
    base = audit_dir if audit_dir is not None else logs_root()
    lines = _tail_jsonl_lines(base / _AUDIT_FILE, _SWEEPER_TAIL_ROWS)
    rows = _parse_audit_rows(lines)
    return {**_latest_sweep_fields(rows), "recent_actions": _recent_audit_actions(rows)}


def next_eligible_sweep_at(queue: Queue) -> str | None:
    raw = queue.get_meta(_CADENCE_META_KEY)
    if raw is None:
        return None
    try:
        last_scheduled = float(raw)
    except ValueError:
        return None
    return datetime.fromtimestamp(last_scheduled + _CADENCE_SECONDS, tz=UTC).isoformat()


def _build_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    surfaces: list[ClassifiedSurface],
) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    blocked = sum(1 for s in surfaces if s.is_blocked)
    synthesis = f"[SENSOR_SWEEP] surfaces={len(surfaces)} blocked={blocked}"
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=[],
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
    )


__all__ = [
    "OnMainCiRedFn",
    "RedMainRun",
    "TriageSweepStatus",
    "TriageTeam",
    "load_triage_sweep_status",
]
