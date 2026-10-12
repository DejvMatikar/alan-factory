from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from alan import store, worker_liveness
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeTarget,
    RepoIdentity,
    registered_engines,
)
from alan.forge import active_forge
from alan.handover import Handover, list_handovers
from alan.queue import (
    FORGE_DEGRADED_META_KEY,
    WORKER_STATE_META_KEY,
    Job,
    JobStatus,
    Queue,
    parse_forge_degraded,
)
from alan.teams import _forge_quota, _pr_forge, _pr_state_paths
from alan.workspace import Status, Task, Workspace, list_workspaces, pr_numbers_from_tasks

logger = logging.getLogger(__name__)

_BUILD_TEAMS: frozenset[str] = frozenset({"implementation", "implementation_fix"})
_STUCK_WS_STATUSES: frozenset[Status] = frozenset({
    Status.AWAITING_INPUT, Status.ESCALATED, Status.FAILED, Status.PARTIAL
})
_DONE_WS_STATUSES: frozenset[Status] = frozenset({
    Status.ARCHIVED, Status.COMPLETED
})
_SURFACE_STATE_META_KEY = "triage_surface_state"
_STALE_HOURS = 1
_GH_TIMEOUT = 30
_FAILING_CHECK_CONCLUSIONS: frozenset[str] = frozenset({
    "FAILURE", "TIMED_OUT", "ACTION_REQUIRED"
})
_MAIN_CI_FAILING_CONCLUSIONS: frozenset[str] = frozenset(
    c.lower() for c in _FAILING_CHECK_CONCLUSIONS
)
_AUDIT_FILENAMES: tuple[str, ...] = (
    "verification_audit.jsonl",
    "impl_audit.jsonl",
    "spiral_audit.jsonl",
    "copilot_review_audit.jsonl",
    "pr_merge_audit.jsonl",
)
_AUDIT_TAIL_BYTES = 256 * 1024
_FAILURE_KEYWORDS: frozenset[str] = frozenset({
    "fail", "error", "timeout", "exception", "401", "403", "5xx",
})
_NON_FAILURE_ACTIONS: frozenset[str] = frozenset({"verifier_settle_fail"})
_RECURRING_MIN_COUNT = 2
_STALE_JOB_THRESHOLD_S = 7200.0
_PR_REF_FIELDS = "number,state,headRefName"
_PR_ROLLUP_FIELDS = "number,reviewDecision,mergeStateStatus,statusCheckRollup"
_OPEN_ROLLUP_LIMIT = 30


@dataclass
class SurfaceRecord:
    fingerprint: str
    first_seen: str
    last_seen: str
    last_diagnosed_fp: str | None


_GHOST_WS_STATUSES: frozenset[Status] = frozenset({Status.ESCALATED, Status.FAILED})


@dataclass
class ClassifiedSurface:
    surface_id: str
    kind: str
    is_blocked: bool
    blocker_reason: str
    obs: dict[str, Any]
    resolvable: bool = field(default=False)
    fp_obs: dict[str, Any] | None = None


def surface_fingerprint(obs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(obs, sort_keys=True).encode()).hexdigest()[:16]


def load_surface_state(queue: Queue) -> dict[str, SurfaceRecord]:
    raw = queue.get_meta(_SURFACE_STATE_META_KEY)
    if raw is None:
        return {}
    try:
        data: dict[str, Any] = json.loads(raw)
        return {k: SurfaceRecord(**v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        return {}


def save_surface_state(queue: Queue, state: dict[str, SurfaceRecord]) -> None:
    blob = {
        k: {
            "fingerprint": v.fingerprint,
            "first_seen": v.first_seen,
            "last_seen": v.last_seen,
            "last_diagnosed_fp": v.last_diagnosed_fp,
        }
        for k, v in state.items()
    }
    queue.set_meta(_SURFACE_STATE_META_KEY, json.dumps(blob))


def _carry_healthy(prior: SurfaceRecord, now_str: str) -> SurfaceRecord:
    return SurfaceRecord(
        fingerprint=prior.fingerprint,
        first_seen=prior.first_seen,
        last_seen=now_str,
        last_diagnosed_fp=prior.last_diagnosed_fp,
    )


def _parse_aware(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo is not None else None


def _normalize_first_seen(
    record: SurfaceRecord, now: datetime, now_str: str
) -> tuple[SurfaceRecord, datetime]:
    first_seen = _parse_aware(record.first_seen)
    if first_seen is None:
        return replace(record, first_seen=now_str), now
    return record, first_seen


def _compute_blocked_record(
    fp: str, prior: SurfaceRecord | None, now_str: str
) -> SurfaceRecord:
    if prior is None or prior.fingerprint != fp:
        return SurfaceRecord(
            fingerprint=fp,
            first_seen=now_str,
            last_seen=now_str,
            last_diagnosed_fp=prior.last_diagnosed_fp if prior else None,
        )
    return SurfaceRecord(
        fingerprint=fp,
        first_seen=prior.first_seen,
        last_seen=now_str,
        last_diagnosed_fp=prior.last_diagnosed_fp,
    )


def _process_surface(
    surface: ClassifiedSurface,
    prior: SurfaceRecord | None,
    now: datetime,
    now_str: str,
    stale_delta: timedelta,
) -> tuple[SurfaceRecord | None, bool]:
    fp = surface_fingerprint(surface.fp_obs if surface.fp_obs is not None else surface.obs)
    if not surface.is_blocked:
        carried = _carry_healthy(prior, now_str) if prior is not None else None
        return carried, False
    record, first_seen = _normalize_first_seen(
        _compute_blocked_record(fp, prior, now_str), now, now_str
    )
    if now - first_seen > stale_delta:
        return record, False
    if record.last_diagnosed_fp == fp:
        return record, False
    return record, True


def decide(
    surfaces: list[ClassifiedSurface],
    stored: dict[str, SurfaceRecord],
    now: datetime,
) -> tuple[list[ClassifiedSurface], dict[str, SurfaceRecord]]:
    stale_delta = timedelta(hours=_STALE_HOURS)
    now_str = now.isoformat()
    eligible: list[ClassifiedSurface] = []
    new_state: dict[str, SurfaceRecord] = {}

    for surface in surfaces:
        record, is_eligible = _process_surface(
            surface, stored.get(surface.surface_id), now, now_str, stale_delta
        )
        if record is not None:
            new_state[surface.surface_id] = record
        if is_eligible:
            eligible.append(surface)

    return eligible, new_state


def _is_build_task(ws: Workspace) -> bool:
    if ws.meta.team == "triage":
        return False
    if ws.meta.team in _BUILD_TEAMS:
        return True
    try:
        for ho in list_handovers(ws):
            if ho.from_team in _BUILD_TEAMS or ho.pr_number is not None:
                return True
    except Exception:  # noqa: BLE001, S110  # nosec B110
        pass
    return False


def _is_live_build_task(ws: Workspace) -> bool:
    return ws.meta.status not in _DONE_WS_STATUSES and _is_build_task(ws)


def _pr_matches_task(ref: str, tid: str) -> bool:
    return ref == f"teams/{tid}" or ref.startswith(f"teams/{tid}-")


def _match_prs_to_tasks_full(
    prs: list[dict[str, Any]], task_ids: list[str]
) -> dict[str, dict[str, Any]]:
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    for pr in prs:
        ref = str(pr.get("headRefName", ""))
        number = int(pr.get("number", 0))
        for tid in task_ids:
            if _pr_matches_task(ref, tid) and number > best.get(tid, (0, {}))[0]:
                best[tid] = (number, pr)
    return {tid: info for tid, (_, info) in best.items()}


def _target_for(repo: RepoIdentity) -> ForgeTarget:
    return ForgeTarget(
        repo=repo, credential_env_name=DEFAULT_FORGE_TARGET.credential_env_name
    )


def _pr_list(state: Literal["open", "all"], fields: str, limit: int) -> list[dict[str, Any]]:
    try:
        read = active_forge().prs_by_head_prefix(
            "teams/", tuple(fields.split(",")),
            forge_target=_target_for(_ENGINE_DEFAULT.repo), state=state, limit=limit,
            timeout=_GH_TIMEOUT,
        )
    except Exception:  # noqa: BLE001
        return []
    return [dict(row) for row in read.rows or ()]


def _fetch_raw_prs() -> list[dict[str, Any]]:
    return _pr_list("all", _PR_REF_FIELDS, 100)


def _fetch_open_pr_rollups() -> list[dict[str, Any]]:
    return _pr_list("open", _PR_ROLLUP_FIELDS, _OPEN_ROLLUP_LIMIT)


def _build_pr_number_index(prs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(pr["number"]): pr for pr in prs if "number" in pr}


def _merge_open_rollups(pr_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not any(pr.get("state") == "OPEN" for pr in pr_map.values()):
        return pr_map
    rollups = _build_pr_number_index(_fetch_open_pr_rollups())
    return {
        tid: {**pr, **rollups.get(int(pr.get("number", 0)), {})}
        if pr.get("state") == "OPEN"
        else pr
        for tid, pr in pr_map.items()
    }


def _fetch_pr_by_number(n: int) -> dict[str, Any] | None:
    try:
        read = active_forge().read_pr(
            n, ("number", "state"),
            forge_target=_target_for(_ENGINE_DEFAULT.repo), timeout=_GH_TIMEOUT,
        )
    except Exception:  # noqa: BLE001
        return None
    return dict(read.fields) if read.ok else None


def verify_pr_merged(n: int) -> bool:
    pr = _fetch_pr_by_number(n)
    return pr is not None and pr.get("state") == "MERGED"


def _evidence_prs_from_tasks(tasks: list[Task]) -> set[int]:
    return set(pr_numbers_from_tasks(tasks))


def _collect_linked_pr_numbers(ws: Workspace, handovers: list[Handover]) -> set[int]:
    linked: set[int] = set()
    for ho in handovers:
        if ho.pr_number is not None:
            linked.add(ho.pr_number)
    return linked | _evidence_prs_from_tasks(ws.meta.tasks)


def _pr_is_merged(n: int, pr_number_index: dict[int, dict[str, Any]]) -> bool:
    if n in pr_number_index:
        return pr_number_index[n].get("state") == "MERGED"
    pr = _fetch_pr_by_number(n)
    return pr is not None and pr.get("state") == "MERGED"


def _all_linked_prs_merged(
    linked: set[int], pr_number_index: dict[int, dict[str, Any]]
) -> bool:
    return all(_pr_is_merged(n, pr_number_index) for n in linked)


def is_merged_ghost(
    ws: Workspace, queue: Queue, pr_number_index: dict[int, dict[str, Any]]
) -> bool:
    if ws.meta.status not in _GHOST_WS_STATUSES:
        return False
    _, job_running = _job_flags(queue, ws.task_id)
    if job_running:
        return False
    try:
        handovers = list_handovers(ws)
    except Exception:  # noqa: BLE001
        return False
    linked = _collect_linked_pr_numbers(ws, handovers)
    if not linked:
        return False
    return _all_linked_prs_merged(linked, pr_number_index)


def _has_multiple_subtasks(ws: Workspace) -> bool:
    return len(ws.meta.tasks) > 1


def _suppress_ghost_for_subtasks(surface: ClassifiedSurface) -> ClassifiedSurface:
    fp_obs = surface.fp_obs if surface.fp_obs is not None else surface.obs
    return ClassifiedSurface(
        surface.surface_id, surface.kind,
        is_blocked=surface.is_blocked, blocker_reason=surface.blocker_reason,
        obs={**surface.obs, "has_subtasks": True}, resolvable=False, fp_obs=fp_obs,
    )


def _maybe_mark_resolvable(
    surface: ClassifiedSurface,
    ws: Workspace,
    queue: Queue,
    pr_number_index: dict[int, dict[str, Any]],
) -> ClassifiedSurface:
    if not is_merged_ghost(ws, queue, pr_number_index):
        return surface
    if _has_multiple_subtasks(ws):
        return _suppress_ghost_for_subtasks(surface)
    return ClassifiedSurface(
        surface.surface_id, surface.kind,
        is_blocked=False, blocker_reason=surface.blocker_reason,
        obs=surface.obs, resolvable=True,
    )


def _has_failing_checks(pr_info: dict[str, Any] | None) -> bool:
    if pr_info is None:
        return False
    rollup: list[dict[str, Any]] = pr_info.get("statusCheckRollup") or []
    return any(c.get("conclusion") in _FAILING_CHECK_CONCLUSIONS for c in rollup)


def _pr_obs_dict(pr_info: dict[str, Any] | None) -> dict[str, Any]:
    if pr_info is None:
        return {
            "number": None,
            "state": None,
            "review": None,
            "merge_state": None,
            "failing_checks": False,
        }
    return {
        "number": pr_info.get("number"),
        "state": pr_info.get("state"),
        "review": pr_info.get("reviewDecision"),
        "merge_state": pr_info.get("mergeStateStatus"),
        "failing_checks": _has_failing_checks(pr_info),
    }


def _is_pr_approved_unmerged(pr: dict[str, Any], job_running: bool) -> bool:
    return (
        pr["state"] == "OPEN"
        and pr["review"] == "APPROVED"
        and pr["merge_state"] == "CLEAN"
        and not job_running
    )


def _job_flags(queue: Queue, task_id: str) -> tuple[bool, bool]:
    failed = queue.list_jobs(task_id=task_id, status=JobStatus.FAILED)
    running = queue.list_jobs(task_id=task_id, status=JobStatus.RUNNING)
    job_running = bool(running)
    job_failed = bool(failed) and not job_running
    return job_failed, job_running


def _build_blocker_reason(
    ws_blocked: bool,
    job_failed: bool,
    pr_checks_failing: bool,
    pr_approved_unmerged: bool,
) -> str:
    reasons: list[str] = []
    if ws_blocked:
        reasons.append("ws_stuck")
    if job_failed:
        reasons.append("job_failed")
    if pr_checks_failing:
        reasons.append("pr_checks_failing")
    if pr_approved_unmerged:
        reasons.append("pr_approved_unmerged")
    return ",".join(reasons)


def _effective_job_running(ws: Workspace, queue: Queue, task_running: bool) -> bool:
    # PARTIAL workspaces have running subtask jobs whose task_id is the subtask id
    # (not the parent ws.task_id), so _job_flags misses them; check at workspace level.
    if ws.meta.status == Status.PARTIAL and not task_running:
        return bool(queue.list_jobs_for_workspace(ws.task_id, status=JobStatus.RUNNING))
    return task_running


def _fix_cycle_obs(pr_number: object, ws: Workspace, job_running: bool) -> tuple[int, bool]:
    if not isinstance(pr_number, int) or isinstance(pr_number, bool):
        return 0, False
    forge_target = _pr_forge.forge_target_for_ws(ws)
    count = _pr_state_paths.load_fix_cycle_state(pr_number, forge_target).count
    return count, _pr_state_paths.should_escalate_fix_cycle_severity(count, job_running)


def _classify_build_task(
    ws: Workspace,
    queue: Queue,
    pr_info: dict[str, Any] | None,
) -> ClassifiedSurface:
    job_failed, task_running = _job_flags(queue, ws.task_id)
    job_running = _effective_job_running(ws, queue, task_running)
    pr = _pr_obs_dict(pr_info)
    fix_cycle_count, fix_cycle_escalate = _fix_cycle_obs(pr["number"], ws, job_running)
    obs: dict[str, Any] = {
        "ws_status": ws.meta.status.value,
        "job_failed": job_failed,
        "job_running": job_running,
        "pr_number": pr["number"],
        "pr_state": pr["state"],
        "pr_review": pr["review"],
        "pr_merge_state": pr["merge_state"],
        "pr_failing_checks": pr["failing_checks"],
        "fix_cycle_count": fix_cycle_count,
        "fix_cycle_escalate": fix_cycle_escalate,
    }
    ws_blocked = ws.meta.status in _STUCK_WS_STATUSES
    pr_approved_unmerged = _is_pr_approved_unmerged(pr, job_running)
    # A PARTIAL workspace with active subtask work is not yet stuck — a sibling is being retried.
    active_partial_work = ws.meta.status == Status.PARTIAL and job_running
    blocked = (not active_partial_work) and (
        ws_blocked or job_failed or pr["failing_checks"] or pr_approved_unmerged
    )
    reason = _build_blocker_reason(
        ws_blocked, job_failed, pr["failing_checks"], pr_approved_unmerged
    )
    return ClassifiedSurface(ws.task_id, "build_task", blocked, reason, obs)


def _classify_main_ci_run(run: dict[str, Any], surface_id: str, repo: str) -> ClassifiedSurface:
    obs: dict[str, Any] = {
        "run_id": run.get("databaseId"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "sha": run.get("headSha"),
        "repo": repo,
    }
    blocked = (run.get("conclusion") or "").lower() in _MAIN_CI_FAILING_CONCLUSIONS
    reason = "ci_failure" if blocked else ""
    return ClassifiedSurface(surface_id, "main_ci", blocked, reason, obs)


def _unknown_main_ci(surface_id: str) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id, "main_ci", is_blocked=False, blocker_reason="", obs={}
    )


def _classify_deploy_run(run: dict[str, Any], surface_id: str, repo: str) -> ClassifiedSurface:
    obs: dict[str, Any] = {
        "run_id": run.get("databaseId"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "sha": run.get("headSha"),
        "repo": repo,
    }
    blocked = (run.get("conclusion") or "").lower() in _MAIN_CI_FAILING_CONCLUSIONS
    reason = "deploy_failure" if blocked else ""
    return ClassifiedSurface(surface_id, "deploy", blocked, reason, obs)


def _unknown_deploy(surface_id: str) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id, "deploy", is_blocked=False, blocker_reason="", obs={}
    )


def _is_failure_line(action: str) -> bool:
    if action in _NON_FAILURE_ACTIONS:
        return False
    al = action.lower()
    return any(kw in al for kw in _FAILURE_KEYWORDS)


def _parse_tally_row(line: str) -> tuple[str, datetime] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        raw: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    row: dict[str, Any] = cast(dict[str, Any], raw)
    ts_str = row.get("timestamp_utc")
    if not isinstance(ts_str, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return None
    return str(row.get("action", "")), ts


def _tally_error_line(line: str, cutoff: datetime, sigs: dict[str, int]) -> None:
    parsed = _parse_tally_row(line)
    if parsed is None:
        return
    action, ts = parsed
    if ts < cutoff:
        return
    if _is_failure_line(action):
        sigs[action] = sigs.get(action, 0) + 1


def _error_action_signatures(audit_dir: Path, hours: int) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    sigs: dict[str, int] = {}
    for filename in _AUDIT_FILENAMES:
        path = audit_dir / filename
        if not path.exists():
            continue
        try:
            for line in store.read_tail_text(path, _AUDIT_TAIL_BYTES).splitlines():
                _tally_error_line(line, cutoff, sigs)
        except Exception:  # noqa: BLE001, S112  # nosec B112
            continue
    return sigs


def _low_headroom_surface() -> ClassifiedSurface | None:
    sample = _forge_quota.last_sample(_forge_quota.PR_FEEDBACK_CALL_SITE)
    if sample is None or not _forge_quota.low_headroom(sample.remaining, sample.limit):
        return None
    return ClassifiedSurface(
        "forge_degraded", "forge_degraded", False, "forge_low_headroom",
        {"remaining": sample.remaining, "limit": sample.limit}, resolvable=True,
    )


def gather_forge_degraded(
    queue: Queue, *, _now: datetime | None = None
) -> ClassifiedSurface:
    empty = ClassifiedSurface("forge_degraded", "forge_degraded", False, "", {})
    marker = parse_forge_degraded(queue.get_meta(FORGE_DEGRADED_META_KEY))
    if marker is None:
        return _low_headroom_surface() or empty
    now = _now if _now is not None else datetime.now(UTC)
    if marker.reset_at <= now.timestamp():
        return _low_headroom_surface() or empty
    return ClassifiedSurface(
        "forge_degraded", "forge_degraded", True, "forge_rate_limited",
        {"reset_at": marker.reset_at, "detail": marker.detail},
    )


def gather_worker_pool(queue: Queue) -> ClassifiedSurface:
    reading = worker_liveness.parse(queue.get_meta(WORKER_STATE_META_KEY))
    blocked, reason = worker_liveness.classify(
        reading,
        now=datetime.now(UTC).timestamp(),
        running_jobs=len(queue.list_running()),
        probe_active_workers=worker_liveness.probe_active_workers,
    )
    obs: dict[str, Any] = (
        {}
        if reading is None
        else {
            "alive": reading.live_loops,
            "configured": reading.configured_loops,
            "observed_at": datetime.fromtimestamp(reading.observed_at, tz=UTC).isoformat(),
        }
    )
    return ClassifiedSurface("worker_pool", "worker_pool", blocked, reason, obs)


def gather_host_toolchain(
    probe: Callable[[], list[worker_liveness.ToolProbe]] = worker_liveness.probe_host_toolchain,
) -> ClassifiedSurface:
    blocked, reason, obs, fp_obs = worker_liveness.classify_host_toolchain(probe())
    return ClassifiedSurface(
        "host_toolchain", "host_toolchain", blocked, reason, obs, fp_obs=fp_obs
    )


def _stale_job_surface(
    job: Job, now: datetime, threshold_s: float
) -> ClassifiedSurface | None:
    if job.started_at is None:
        return None
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = (now - started).total_seconds()
    if elapsed < threshold_s:
        return None
    workspace_id = job.workspace_id or job.task_id
    return ClassifiedSurface(
        f"stale_job_{job.id}", "stale_job", True, "stale_running_job",
        {
            "workspace_id": workspace_id,
            "job_id": job.id,
            "team": job.team,
            "started_at": started.isoformat(),
            "elapsed_seconds": int(elapsed),
        },
    )


def gather_stale_jobs(
    queue: Queue,
    threshold_s: float = _STALE_JOB_THRESHOLD_S,
    *,
    _now: datetime | None = None,
) -> list[ClassifiedSurface]:
    now = _now if _now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    candidates = [_stale_job_surface(j, now, threshold_s) for j in queue.list_running()]
    return [s for s in candidates if s is not None]


def gather_build_tasks(
    workspaces_root: Path | None, queue: Queue
) -> list[ClassifiedSurface]:
    try:
        all_ws = list_workspaces(root=workspaces_root)
    except Exception:  # noqa: BLE001
        return []
    build_ws = [ws for ws in all_ws if _is_live_build_task(ws)]
    if not build_ws:
        return []
    raw_prs = _fetch_raw_prs()
    pr_map = _merge_open_rollups(
        _match_prs_to_tasks_full(raw_prs, [ws.task_id for ws in build_ws])
    )
    pr_number_index = _build_pr_number_index(raw_prs)
    result: list[ClassifiedSurface] = []
    for ws in build_ws:
        surface = _classify_build_task(ws, queue, pr_map.get(ws.task_id))
        result.append(_maybe_mark_resolvable(surface, ws, queue, pr_number_index))
    return result


def _latest_main_run(repo: RepoIdentity, workflow: str) -> dict[str, Any] | None:
    try:
        read = active_forge().latest_branch_run(
            workflow, "main", forge_target=_target_for(repo), timeout=_GH_TIMEOUT,
        )
    except Exception:  # noqa: BLE001
        return None
    return dict(read.rows[0]) if read.rows else None


def gather_ci_surfaces_for(config: EngineConfig) -> list[ClassifiedSurface]:
    if config.ci is None:
        return []
    repo = config.repo.slug
    surfaces: list[ClassifiedSurface] = []
    main_ci_id = f"main_ci:{repo}"
    main_run = _latest_main_run(config.repo, config.ci.main_ci_workflow)
    surfaces.append(
        _unknown_main_ci(main_ci_id) if main_run is None
        else _classify_main_ci_run(main_run, main_ci_id, repo)
    )
    if config.ci.deploy_workflow is not None:
        deploy_id = f"deploy:{repo}"
        deploy_run = _latest_main_run(config.repo, config.ci.deploy_workflow)
        surfaces.append(
            _unknown_deploy(deploy_id) if deploy_run is None
            else _classify_deploy_run(deploy_run, deploy_id, repo)
        )
    return surfaces


def gather_recurring_errors(audit_dir: Path, hours: int) -> ClassifiedSurface:
    sigs = _error_action_signatures(audit_dir, hours)
    recurring = {sig for sig, count in sigs.items() if count >= _RECURRING_MIN_COUNT}
    obs: dict[str, Any] = {"recurring_signatures": sorted(recurring)}
    blocked = bool(recurring)
    reason = f"recurring_errors:{len(recurring)}" if blocked else ""
    return ClassifiedSurface("recurring_errors", "recurring_errors", blocked, reason, obs)


def gather_all(
    workspaces_root: Path | None,
    queue: Queue,
    audit_dir: Path,
    hours: int,
) -> list[ClassifiedSurface]:
    surfaces: list[ClassifiedSurface] = []
    surfaces.extend(gather_build_tasks(workspaces_root, queue))
    for cfg in registered_engines():
        surfaces.extend(gather_ci_surfaces_for(cfg))
    surfaces.append(gather_recurring_errors(audit_dir, hours))
    surfaces.append(gather_worker_pool(queue))
    surfaces.append(gather_forge_degraded(queue))
    surfaces.extend(gather_stale_jobs(queue))
    surfaces.append(gather_host_toolchain())
    return surfaces


__all__ = [
    "ClassifiedSurface",
    "SurfaceRecord",
    "decide",
    "gather_all",
    "gather_build_tasks",
    "gather_ci_surfaces_for",
    "gather_forge_degraded",
    "gather_host_toolchain",
    "gather_recurring_errors",
    "gather_stale_jobs",
    "gather_worker_pool",
    "is_merged_ghost",
    "load_surface_state",
    "save_surface_state",
    "surface_fingerprint",
    "verify_pr_merged",
]
