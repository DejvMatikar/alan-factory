from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

import alan.build_chat as build_chat_mod
import alan.config_files as config_files_mod
import alan.engine_config as engine_config_mod
import alan.engine_registry_store as engine_registry_store_mod
import alan.explore_chat as explore_chat_mod
import alan.reconciler as reconciler
import alan.teams
import alan.triage_switch as triage_switch
import alan.unblock_ledger as unblock_ledger_mod
import alan.workspace as workspace_mod
from alan import (
    default_engine_boot,
    deploy_gate_sweep,
    detached_mode,
    driver_loop_runner,
    fleet_load,
    secrets_store,
    worker_reaper,
)
from alan.dep_advisory_seed import seed_advisory_checks
from alan.digest_sweep import (
    DIGEST_LAST_DATE_META_KEY,
    reconcile_incidents_sweep,
)
from alan.dispatcher import Dispatcher, DispatcherError
from alan.engine_preflight import ReadinessIssue, readiness_report
from alan.events import PubSub
from alan.finisher_rate import FinisherRate
from alan.handover import Handover
from alan.health import (
    HealthFinding,
    HealthState,
    HealthVerdict,
    SpendCapStop,
    is_stale,
    load_finisher_rate,
    load_health_state,
    load_learning_metrics,
    load_spend_cap_stop,
    resolve_digest_deadline,
)
from alan.health_repo_metrics import (
    classify_build_workspaces as _classify_build_workspaces,
)
from alan.health_repo_metrics import (
    format_correction_rates_from_audit_dir as _format_correction_rates_from_audit_dir,
)
from alan.health_repo_metrics import (
    is_build_success as _is_build_success,
)
from alan.health_repo_metrics import (
    unproven_clause_count as _unproven_clause_count,
)
from alan.incident import digest_overdue, stuck_workspace_keys
from alan.queue import INTRA_WORKSPACE_CAP, INTRA_WORKSPACE_CAP_POOLED, Job, Queue
from alan.retention import archive_stale_completed
from alan.roots import logs_root, slot_pool_root
from alan.routes._deps import (
    GUARDED,
    OptionalDispatcherDep,
    PubSubDep,
    QueueDep,
    WorkspaceIndexDep,
    WorkspacesRootDep,
    dispatcher_disabled,
    require_control_token,
    resolve_build_chat_sessions_root,
    resolve_dispatcher_concurrency,
    resolve_explore_chat_sessions_root,
    resolve_queue_db,
    resolve_workspaces_root,
)
from alan.served_sha import served_sha
from alan.slot_pool import (
    Reservation,
    SlotPool,
    is_git_clone,
    is_remote_url,
    resolve_repo_root,
)
from alan.teams import (
    INITIAL_DISPATCHABLE_TEAMS,
    MANUALLY_DISPATCHABLE_TEAMS,
    TEAM_ROLE_SUMMARIES,
    Team,
)
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import TeamEvent
from alan.teams.dependency_sweep import DependencySweepTeam
from alan.teams.triage import TriageTeam, next_eligible_sweep_at, read_sweeper_status
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.server")


def _check_credential_expiry(now: datetime) -> None:
    for meta in secrets_store.due_expiry_warnings(now):
        append_jsonl_audit(
            logs_root() / secrets_store.CREDENTIAL_EXPIRY_AUDIT_FILE,
            {"name": meta.name, "expires_at": meta.expires_at},
            log_label="credential_expiry",
        )


class TeamInfo(BaseModel):
    name: str
    manually_dispatchable: bool
    initial_dispatchable: bool
    role_summary: str


class TeamsListResponse(BaseModel):
    teams: list[TeamInfo]


_QUEUE_RESUME_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED,
}


class QueueResumeAck(BaseModel):
    ok: bool
    queue_paused: bool
    paused_until: float | None


QUEUE_PAUSE_AUDIT_FILE = "queue_pause_audit.jsonl"


def _log_job_reconcile_report(report: reconciler.ReconciliationReport) -> None:
    if report.interrupted:
        logger.info("reconciler interrupted %d orphan job(s)", len(report.interrupted))
    if report.replayed:
        logger.info("reconciler replayed %d handover routing(s)", len(report.replayed))
    if report.rerun:
        logger.info("reconciler re-ran %d never-started job(s)", len(report.rerun))
    if report.errors:
        logger.warning("reconciler errors: %s", report.errors)


def _create_slot_pool(size: int) -> SlotPool | None:
    pool_root = slot_pool_root()
    if pool_root is None:
        return None
    source = resolve_repo_root()
    if source is None or not (is_remote_url(source) or is_git_clone(Path(source))):
        logger.warning(
            "ALAN_SLOT_POOL_ROOT set but clone source %r is unusable "
            "(not a URL or a local git checkout); slot pool disabled",
            source,
        )
        return None
    return SlotPool(
        root=pool_root,
        size=size,
        repo_root=source,
        forge_target=engine_config_mod.DEFAULT_FORGE_TARGET,
        load_sampler=fleet_load.read_load1,
    )


_ENGINE_POOL_DIRNAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _engine_pool_dirname(slug: str) -> str:
    sanitized = _ENGINE_POOL_DIRNAME_RE.sub("_", slug)
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


def _clone_source_for_engine(config: engine_config_mod.EngineConfig) -> str:
    return f"https://github.com/{config.repo.owner}/{config.repo.repo}.git"


def _record_warmup_seconds(slug: str, seconds: float) -> None:
    engine_registry_store_mod.write_warmup_seconds(slug, seconds)


def _create_slot_pool_for_engine(
    pool_root: Path,
    size: int,
    config: engine_config_mod.EngineConfig,
    *,
    reserved: Mapping[Path, Reservation] | None = None,
) -> SlotPool:
    return SlotPool(
        root=pool_root / _engine_pool_dirname(config.repo.slug),
        size=size,
        repo_root=_clone_source_for_engine(config),
        warmup_commands=config.warmup_commands,
        cache_env=config.cache_env,
        forge_target=(
            engine_config_mod.ForgeTarget.from_forge_config(config.forge)
            if config.forge is not None
            else None
        ),
        on_warmup_measured=partial(_record_warmup_seconds, config.repo.slug),
        reserved=reserved,
        load_sampler=fleet_load.read_load1,
    )


_DEFAULT_SECONDARY_POOL_SIZE = 2


def _secondary_pool_size(config: engine_config_mod.EngineConfig) -> int:
    return config.pool_size or _DEFAULT_SECONDARY_POOL_SIZE


def _create_keyed_slot_pools() -> dict[str, SlotPool]:
    pool_root = slot_pool_root()
    if pool_root is None:
        return {}
    default_config = engine_config_mod.get_registered() or engine_config_mod.default_engine_config()
    return {
        config.repo.slug: _create_slot_pool_for_engine(
            pool_root, _secondary_pool_size(config), config
        )
        for config in engine_config_mod.registered_engines()
        if config.repo.slug != default_config.repo.slug
    }


def _reset_and_warm_pools(
    slot_pool: SlotPool | None,
    slot_pools_by_engine: dict[str, SlotPool],
    reserved: Mapping[Path, Reservation],
    *,
    clean: bool = True,
) -> None:
    if slot_pool is not None:
        slot_pool.reset_all(reserved, clean=clean)
    for pool in slot_pools_by_engine.values():
        pool.reset_all(reserved, clean=clean)
        pool.mark_warm()


def _build_pool_factory(
    pool_root: Path | None,
    reserved: Mapping[Path, Reservation] | None = None,
) -> Callable[[engine_config_mod.EngineConfig], SlotPool] | None:
    if pool_root is None:
        return None

    def factory(config: engine_config_mod.EngineConfig) -> SlotPool:
        return _create_slot_pool_for_engine(
            pool_root, _secondary_pool_size(config), config, reserved=reserved
        )

    return factory


def _intra_workspace_cap(slot_pool: SlotPool | None) -> int:
    return INTRA_WORKSPACE_CAP_POOLED if slot_pool is not None else INTRA_WORKSPACE_CAP


_PoolPair = tuple[SlotPool | None, dict[str, SlotPool]]


def _create_pools(concurrency: int) -> _PoolPair:
    if dispatcher_disabled():
        return None, {}
    return _create_slot_pool(concurrency), _create_keyed_slot_pools()


def _run_job_reconciler(
    queue: Queue,
    workspaces_root: Path | None,
    pubsub: PubSub,
    replay_routing: Callable[[Job, Handover], None] | None = None,
) -> None:
    _log_job_reconcile_report(
        reconciler.reconcile_on_startup(
            queue=queue,
            workspaces_root=workspaces_root,
            pubsub=pubsub,
            replay_routing=replay_routing,
        )
    )
    reconciler.reconcile_stalled_dags(queue=queue, workspaces_root=workspaces_root)


def _run_build_chat_reconciler(build_chat_root: Path) -> None:
    bridged = build_chat_mod.sweep_legacy_engine_keys(root=build_chat_root)
    if bridged:
        logger.info("build-chat reconciler backfilled engine_key for %d session(s)", len(bridged))
    cleared = build_chat_mod.reconcile_in_flight_turns(root=build_chat_root)
    if cleared:
        logger.info("build-chat reconciler cleared %d session(s)", len(cleared))
    reaped = build_chat_mod.reap_stale_repo_checkouts(root=build_chat_root)
    if reaped:
        logger.info("build-chat reconciler reaped %d stale repo checkout(s)", len(reaped))


def _run_explore_chat_reconciler(explore_chat_root: Path) -> None:
    bridged = explore_chat_mod.sweep_legacy_engine_keys(root=explore_chat_root)
    if bridged:
        logger.info(
            "explore-chat reconciler backfilled engine_key for %d session(s)", len(bridged)
        )
    cleared = explore_chat_mod.reconcile_in_flight_turns(root=explore_chat_root)
    if cleared:
        logger.info("explore-chat reconciler cleared %d session(s)", len(cleared))


_LEGACY_ENGINES_DIR_ENV = "ALAN_ENGINES_DIR"


def _import_legacy_engines_once() -> None:
    raw = os.environ.get(_LEGACY_ENGINES_DIR_ENV, "").strip()
    if not raw:
        return
    try:
        loaded = engine_config_mod.import_legacy_directory_once(Path(raw))
    except engine_config_mod.DuplicateEngineError:
        logger.warning(
            "one-time legacy engine import from %s found a duplicate repo identity; skipped",
            raw,
        )
        return
    if loaded:
        logger.info(
            "one-time import of %d legacy engine config(s) from %s: %s",
            len(loaded),
            raw,
            ", ".join(cfg.repo.slug for cfg in loaded),
        )


def _register_file_backed_engine(
    repo: engine_config_mod.RepoIdentity,
    file_cfg: engine_config_mod.EngineConfig,
    *,
    default: bool,
) -> None:
    stored = engine_config_mod.lookup_by_repo(repo)
    effective = engine_config_mod.apply_stored_projection(file_cfg, stored)
    engine_config_mod.register_replacing(effective, default=default)


def _quarantined_config_path(repo: engine_config_mod.RepoIdentity) -> Path:
    return config_files_mod.engines_config_dir() / f"{repo.repo}.yaml"


def _register_file_configs(
    file_configs: dict[engine_config_mod.RepoIdentity, engine_config_mod.EngineConfig],
    default_repo: engine_config_mod.RepoIdentity,
) -> tuple[
    dict[engine_config_mod.RepoIdentity, engine_config_mod.Posture],
    list[engine_config_mod.QuarantinedConfig],
]:
    ceilings: dict[engine_config_mod.RepoIdentity, engine_config_mod.Posture] = {}
    failures: list[engine_config_mod.QuarantinedConfig] = []
    for repo, file_cfg in file_configs.items():
        try:
            _register_file_backed_engine(repo, file_cfg, default=(repo == default_repo))
        except Exception as exc:  # noqa: BLE001 - fail-soft per file; service must still start
            logger.warning(
                "quarantined engine config for %s: registration failed", repo.slug, exc_info=True
            )
            ceilings[repo] = engine_config_mod.Posture.observe
            failures.append(
                engine_config_mod.QuarantinedConfig(
                    path=_quarantined_config_path(repo), error=str(exc), repo=repo
                )
            )
            continue
        ceilings[repo] = file_cfg.posture
    return ceilings, failures


def _resolve_parse_failures(
    quarantined: list[config_files_mod.QuarantinedEngineFile],
    ceilings: dict[engine_config_mod.RepoIdentity, engine_config_mod.Posture],
) -> tuple[list[engine_config_mod.QuarantinedConfig], bool]:
    candidates = [cfg.repo for cfg in engine_config_mod.registered_engines()]
    records: list[engine_config_mod.QuarantinedConfig] = []
    unresolved = False
    for record in quarantined:
        logger.warning("quarantined engine config %s: %s", record.path, record.error)
        resolved = config_files_mod.resolve_quarantined_identity(record.path, candidates)
        if resolved is None:
            unresolved = True
        else:
            ceilings.setdefault(resolved, engine_config_mod.Posture.observe)
        records.append(
            engine_config_mod.QuarantinedConfig(path=record.path, error=record.error, repo=resolved)
        )
    return records, unresolved


def _load_file_backed_engines() -> None:
    file_configs, quarantined = config_files_mod.load_tracked_configs()
    default_repo = engine_config_mod.default_engine_config().repo
    ceilings, registration_failures = _register_file_configs(file_configs, default_repo)
    parse_failures, unresolved = _resolve_parse_failures(quarantined, ceilings)
    fallback = (
        engine_config_mod.Posture.observe if unresolved else engine_config_mod.Posture.autonomous
    )
    engine_config_mod.set_file_ceilings(ceilings, fallback=fallback)
    engine_config_mod.set_quarantined_configs(registration_failures + parse_failures)


def _load_engines() -> None:
    _import_legacy_engines_once()
    default_engine_boot.load_engines(_load_file_backed_engines)


def _build_triage_team(queue: Queue, workspaces_root: Path | None) -> TriageTeam:
    return TriageTeam(
        queue=queue,
        workspaces_root=workspaces_root,
        reconcile_incidents=reconcile_incidents_sweep,
        run_retention=partial(
            archive_stale_completed, queue=queue, workspaces_root=workspaces_root
        ),
        check_credential_expiry=_check_credential_expiry,
        on_main_ci_red=partial(seed_advisory_checks, queue, workspaces_root),
    )


def _start_workspace_index(app: FastAPI, workspaces_root: Path, queue: Queue) -> WorkspaceIndex:
    workspace_index = WorkspaceIndex(root=workspaces_root, queue=queue)
    workspace_index.rebuild()
    threading.Thread(target=workspace_index.backfill_archived, daemon=True).start()
    app.state.workspace_indexes = {workspaces_root: workspace_index}
    return workspace_index


def _run_chat_reconcilers(build_chat_root: Path, explore_chat_root: Path) -> None:
    _run_build_chat_reconciler(build_chat_root)
    _run_explore_chat_reconciler(explore_chat_root)


def _publish_driver_loop_changed(pubsub: PubSub) -> None:
    pubsub.publish_global(TeamEvent("driver_loop_changed", {}))


def _start_driver_loop_runner(queue: Queue, pubsub: PubSub) -> None:
    driver_loop_runner.start(queue=queue, on_change=partial(_publish_driver_loop_changed, pubsub))


def _stash_app_state(
    app: FastAPI,
    queue: Queue,
    pubsub: PubSub,
    workspaces_root: Path,
    build_chat_sessions_root: Path,
    explore_chat_sessions_root: Path,
) -> None:
    app.state.queue = queue
    app.state.pubsub = pubsub
    app.state.workspaces_root = workspaces_root
    app.state.build_chat_sessions_root = build_chat_sessions_root
    app.state.explore_chat_sessions_root = explore_chat_sessions_root
    app.state.build_chat_locks = build_chat_mod.BuildChatSessionLocks()
    app.state.explore_chat_locks = explore_chat_mod.ExploreChatSessionLocks()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    served_sha()  # warm the cache once at process start
    _load_engines()
    workspaces_root = resolve_workspaces_root()
    concurrency = resolve_dispatcher_concurrency()
    slot_pool, slot_pools_by_engine = _create_pools(concurrency)
    queue = Queue(db_path=resolve_queue_db(), intra_workspace_cap=_intra_workspace_cap(slot_pool))
    if not detached_mode.log_resolved_mode():
        worker_reaper.terminate_all_detached()
    reserved = worker_reaper.reap_and_reserve(queue)
    clean_allowed = worker_reaper.boot_clean_allowed(queue)
    deploy_gate_sweep.promote_covered_jobs(queue)
    _reset_and_warm_pools(slot_pool, slot_pools_by_engine, reserved, clean=clean_allowed)
    pool_factory = (
        _build_pool_factory(slot_pool_root(), reserved) if not dispatcher_disabled() else None
    )
    pubsub = PubSub()
    _start_driver_loop_runner(queue, pubsub)
    build_chat_sessions_root = resolve_build_chat_sessions_root()
    explore_chat_sessions_root = resolve_explore_chat_sessions_root()
    workspace_index = _start_workspace_index(app, workspaces_root, queue)
    _stash_app_state(
        app, queue, pubsub, workspaces_root, build_chat_sessions_root, explore_chat_sessions_root
    )
    _run_chat_reconcilers(build_chat_sessions_root, explore_chat_sessions_root)
    dispatcher: Dispatcher | None = None
    if not dispatcher_disabled():
        teams: dict[str, Team] = dict(alan.teams.TEAMS)
        triage_team = _build_triage_team(queue, workspaces_root)
        teams[triage_team.name] = triage_team
        dep_sweep_team = DependencySweepTeam(queue=queue, workspaces_root=workspaces_root)
        teams[dep_sweep_team.name] = dep_sweep_team
        dispatcher = Dispatcher(
            queue,
            teams,
            pubsub=pubsub,
            workspaces_root=workspaces_root,
            concurrency=concurrency,
            slot_pool=slot_pool,
            slot_pools_by_engine=slot_pools_by_engine,
            workspace_index=workspace_index,
            pool_factory=pool_factory,
        )
        _run_job_reconciler(queue, workspaces_root, pubsub, dispatcher.replay_handover_routing)
        triage_switch.start_triage_sweep(queue=queue, workspaces_root=workspaces_root)
        reconciler.seed_dep_sweep(queue=queue, workspaces_root=workspaces_root)
        dispatcher.start()
    app.state.dispatcher = dispatcher
    try:
        yield
    finally:
        driver_loop_runner.stop()
        if dispatcher is not None:
            try:
                dispatcher.stop()
            except DispatcherError:
                logger.exception("dispatcher.stop() raised; closing queue anyway")
        queue.close()


def looks_like_spa_route(path: str) -> bool:
    if path == "api" or path.startswith("api/"):
        return False
    return "." not in path.rsplit("/", 1)[-1]


class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not looks_like_spa_route(path):
                raise
            index = Path(str(self.directory)) / "index.html"
            if not index.is_file():
                raise
            return FileResponse(index)


def mount_ui(target: FastAPI, dist_dir: Path) -> bool:
    if not dist_dir.is_dir():
        return False
    if not (dist_dir / "index.html").is_file():
        return False
    target.mount("/", _SPAStaticFiles(directory=str(dist_dir), html=True), name="ui")
    return True


class RecentSweeperAction(BaseModel):
    action: str
    timestamp_utc: str


class SweeperStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_utc: str | None
    surface_count: int | None
    blocked_count: int | None
    recent_actions: list[RecentSweeperAction]
    next_eligible_utc: str | None


class QualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_success_rate: float | None
    in_flight_build_count: int
    unproven_clause_count: int
    first_pass_approval_rate: float | None
    mean_fix_cycles_to_merge: float | None
    sample_size: int | None
    computed_at: str | None
    format_error_rate: float | None
    format_error_sample: int
    format_error_low_data: bool
    recovery_rate: float | None
    recovery_sample: int
    recovery_low_data: bool
    rescue_success_rate: float | None
    rescue_success_rate_sample: int
    rescue_success_rate_low_data: bool
    rescue_load_rate: float | None
    rescue_load_rate_sample: int
    rescue_load_rate_low_data: bool


class HealthStateResponse(BaseModel):
    verdict: HealthVerdict
    blocked_count: int
    in_flight_count: int
    computed_at: str
    findings: list[HealthFinding]


class HealthResponse(BaseModel):
    ok: bool
    queue_paused: bool
    paused_until: float | None
    health_state: HealthStateResponse | None
    last_computed_at: str | None
    dispatch_loops_alive: int | None
    dispatch_loops_configured: int | None
    finisher_rate: FinisherRate | None
    quality_metrics: QualityMetrics
    digest_overdue: bool
    sweeper: SweeperStatus
    served_sha: str | None
    spend_cap_stop: SpendCapStop | None = None
    dispatch_readiness: list[ReadinessIssue] = Field(default_factory=list[ReadinessIssue])


infra_router = APIRouter(prefix="/api")


def _dispatch_loop_counts(
    dispatcher: Dispatcher | None,
) -> tuple[int | None, int | None]:
    if dispatcher is None:
        return None, None
    return dispatcher.live_worker_count, dispatcher.configured_worker_count


def _suppress_worker_pool(
    findings: list[HealthFinding], *, dispatcher: Dispatcher | None
) -> list[HealthFinding]:
    if dispatcher is None or dispatcher.live_worker_count < dispatcher.configured_worker_count:
        return findings
    return [f for f in findings if f.kind != "worker_pool"]


def _prepare_health_state(
    state: HealthState | None, findings: list[HealthFinding]
) -> HealthStateResponse | None:
    if state is None or is_stale(state):
        return None
    verdict: HealthVerdict = "degraded" if findings else "healthy"
    return HealthStateResponse(
        verdict=verdict,
        blocked_count=len(findings),
        in_flight_count=sum(1 for f in findings if f.in_flight),
        computed_at=state.computed_at,
        findings=findings,
    )


def _finding_project_id(
    f: HealthFinding, workspaces_root: Path | None, cache: dict[str, str | None]
) -> str | None:
    if f.workspace_id is None:
        return None
    if f.workspace_id not in cache:
        try:
            ws = workspace_mod.read(f.workspace_id, root=workspaces_root)
            cache[f.workspace_id] = ws.meta.project_id
        except workspace_mod.WorkspaceError:
            cache[f.workspace_id] = None
    return cache[f.workspace_id]


def _filter_findings(
    findings: list[HealthFinding],
    *,
    engine: str | None,
    project_id: str | None,
    workspaces_root: Path | None,
) -> list[HealthFinding]:
    out = list(findings)
    if engine is not None:
        target = engine_config_mod.canonical_scope_key(engine)
        out = [f for f in out if engine_config_mod.canonical_scope_key(f.engine_key) == target]
    if project_id is not None:
        cache: dict[str, str | None] = {}
        out = [f for f in out if _finding_project_id(f, workspaces_root, cache) == project_id]
    return out


def _lm_float_or_none(val: object) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _compute_build_success_rate(
    all_ws: list[workspace_mod.Workspace],
) -> tuple[float | None, int]:
    terminal_metas, in_flight = _classify_build_workspaces(all_ws)
    if not terminal_metas:
        return None, in_flight
    successes = sum(1 for meta in terminal_metas if _is_build_success(meta))
    return successes / len(terminal_metas), in_flight


_QUALITY_METRICS_TTL_S = 30.0
_QUALITY_METRICS_LOCK = threading.Lock()
_QUALITY_METRICS_CACHE: dict[Any, tuple[float, QualityMetrics]] = {}


def _cached_quality_metrics(queue: Any, workspaces_root: Any) -> QualityMetrics:
    with _QUALITY_METRICS_LOCK:
        cached = _QUALITY_METRICS_CACHE.get(workspaces_root)
        if cached is not None and time.monotonic() - cached[0] < _QUALITY_METRICS_TTL_S:
            return cached[1]
        metrics = _build_quality_metrics(queue, workspaces_root)
        _QUALITY_METRICS_CACHE[workspaces_root] = (time.monotonic(), metrics)
        return metrics


def _format_correction_metrics() -> dict[str, Any]:
    rates = _format_correction_rates_from_audit_dir(logs_root())
    return {
        "format_error_rate": rates.format_error_rate,
        "format_error_sample": rates.format_error_sample,
        "format_error_low_data": rates.format_error_low_data,
        "recovery_rate": rates.recovery_rate,
        "recovery_sample": rates.recovery_sample,
        "recovery_low_data": rates.recovery_low_data,
    }


def _rescue_metrics(queue: Any, all_ws: list[workspace_mod.Workspace]) -> dict[str, Any]:
    workspace_keys = [ws.task_id for ws in all_ws]
    stuck_keys = stuck_workspace_keys((ws.task_id, ws.meta.status) for ws in all_ws)
    stats = unblock_ledger_mod.rescue_stats(queue, workspace_keys, stuck_keys, time.time())
    return {
        "rescue_success_rate": stats.success_rate,
        "rescue_success_rate_sample": stats.success_sample,
        "rescue_success_rate_low_data": stats.success_low_data,
        "rescue_load_rate": stats.load_rate,
        "rescue_load_rate_sample": stats.load_sample,
        "rescue_load_rate_low_data": stats.load_low_data,
    }


def _build_quality_metrics(queue: Any, workspaces_root: Any) -> QualityMetrics:
    lm_blob = load_learning_metrics(queue)
    try:
        all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    except Exception:  # noqa: BLE001
        all_ws = []
    build_success_rate, in_flight_build_count = _compute_build_success_rate(all_ws)
    unproven_clause_total = _unproven_clause_count(all_ws)
    format_correction = _format_correction_metrics()
    rescue = _rescue_metrics(queue, all_ws)
    if lm_blob is None:
        first_pass_approval_rate: float | None = None  # nosec B105
        sample: int | None = None
        computed_at: str | None = None
    else:
        sample_raw: Any = lm_blob.get("sample_size")
        sample = (
            int(sample_raw)
            if isinstance(sample_raw, int) and not isinstance(sample_raw, bool)
            else None
        )
        computed_at_raw: Any = lm_blob.get("computed_at")
        computed_at = computed_at_raw if isinstance(computed_at_raw, str) else None
        first_pass_approval_rate = _lm_float_or_none(lm_blob.get("first_pass_approval_rate"))
    return QualityMetrics.model_validate({
        "build_success_rate": build_success_rate,
        "in_flight_build_count": in_flight_build_count,
        "unproven_clause_count": unproven_clause_total,
        "first_pass_approval_rate": first_pass_approval_rate,
        "mean_fix_cycles_to_merge": None,
        "sample_size": sample,
        "computed_at": computed_at,
        **format_correction,
        **rescue,
    })


def _digest_overdue_flag(queue: Queue, now: datetime) -> bool:
    digest_last_date = queue.get_meta(DIGEST_LAST_DATE_META_KEY)
    return digest_overdue(digest_last_date, now, resolve_digest_deadline(now))


@infra_router.get("/health")
async def health(
    queue: QueueDep,
    dispatcher: OptionalDispatcherDep,
    workspaces_root: WorkspacesRootDep,
    engine: Annotated[str | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
) -> HealthResponse:
    paused = queue.paused_until()
    state = load_health_state(queue)
    filtered_findings = await run_in_threadpool(
        _filter_findings,
        state.findings if state is not None else [],
        engine=engine,
        project_id=project_id,
        workspaces_root=workspaces_root,
    )
    filtered_findings = _suppress_worker_pool(filtered_findings, dispatcher=dispatcher)
    health_state_out = _prepare_health_state(state, filtered_findings)
    last_computed_at = state.computed_at if state is not None else None
    dispatch_loops_alive, dispatch_loops_configured = _dispatch_loop_counts(dispatcher)
    fr = load_finisher_rate(queue)
    quality_metrics = await run_in_threadpool(_cached_quality_metrics, queue, workspaces_root)
    sweeper = await run_in_threadpool(read_sweeper_status)
    dispatch_readiness = await run_in_threadpool(readiness_report)
    sweeper_out = SweeperStatus.model_validate(
        {**sweeper, "next_eligible_utc": next_eligible_sweep_at(queue)}
    )
    return HealthResponse(
        ok=True,
        queue_paused=paused is not None,
        paused_until=paused,
        health_state=health_state_out,
        last_computed_at=last_computed_at,
        dispatch_loops_alive=dispatch_loops_alive,
        dispatch_loops_configured=dispatch_loops_configured,
        finisher_rate=fr,
        quality_metrics=quality_metrics,
        digest_overdue=_digest_overdue_flag(queue, datetime.now(UTC)),
        sweeper=sweeper_out,
        served_sha=served_sha(),
        spend_cap_stop=load_spend_cap_stop(queue),
        dispatch_readiness=dispatch_readiness,
    )


class ReindexResponse(BaseModel):
    rows: int
    archived_rows: int


@infra_router.post("/admin/reindex")
async def reindex_workspaces(index: WorkspaceIndexDep) -> ReindexResponse:
    await run_in_threadpool(index.reindex)
    rows = await run_in_threadpool(index.rows)
    archived = await run_in_threadpool(index.archived_rows)
    return ReindexResponse(rows=len(rows), archived_rows=len(archived))


@infra_router.get("/teams")
async def list_teams() -> TeamsListResponse:
    items = sorted(alan.teams.TEAMS)
    return TeamsListResponse(
        teams=[
            TeamInfo(
                name=name,
                manually_dispatchable=name in MANUALLY_DISPATCHABLE_TEAMS,
                initial_dispatchable=name in INITIAL_DISPATCHABLE_TEAMS,
                role_summary=TEAM_ROLE_SUMMARIES.get(name, ""),
            )
            for name in items
        ]
    )


def _clear_pause_and_audit(queue: Queue) -> float | None:
    prior = queue.clear_pause()
    append_jsonl_audit(
        logs_root() / QUEUE_PAUSE_AUDIT_FILE,
        {"cleared_at": datetime.now(UTC).isoformat(), "prior_paused_until": prior},
        log_label="queue_pause",
    )
    return queue.paused_until()


@infra_router.post(
    "/queue/resume",
    dependencies=[Depends(require_control_token)],
    responses=_QUEUE_RESUME_RESPONSES,
)
async def resume_queue(queue: QueueDep, pubsub: PubSubDep) -> QueueResumeAck:
    paused = await run_in_threadpool(_clear_pause_and_audit, queue)
    pubsub.publish_global(TeamEvent("queue_resumed", {}))
    return QueueResumeAck(ok=True, queue_paused=paused is not None, paused_until=paused)
