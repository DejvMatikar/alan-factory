from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import alan.engine_config as engine_config_mod
import alan.health_repo_metrics as health_repo_metrics_mod
import alan.health_windows as health_windows_mod
import alan.unblock_activity as unblock_activity_mod
import alan.unblock_ledger as unblock_ledger_mod
import alan.workspace as workspace_mod
from alan.digest import DigestDoc, TrendDirection, recovery_totals
from alan.digest_sweep import DIGEST_LAST_DATE_META_KEY, load_latest_digest
from alan.finisher_rate import FunnelCounts
from alan.handover import list_handovers
from alan.health import (
    HealthFinding,
    add_completed_finding,
    load_health_state,
    resolve_digest_deadline,
)
from alan.incident import Disposition, digest_overdue
from alan.incident_store import set_disposition
from alan.queue import Queue
from alan.roots import logs_root
from alan.routes._deps import QueueDep, WorkspacesRootDep
from alan.teams.triage_pregate import (
    _build_pr_number_index,  # pyright: ignore[reportPrivateUsage]
    _collect_linked_pr_numbers,  # pyright: ignore[reportPrivateUsage]
    _fetch_raw_prs,  # pyright: ignore[reportPrivateUsage]
    is_merged_ghost,
)

health_router = APIRouter(prefix="/api/health")

_SEVERITY_WEIGHT: dict[str, float] = {"critical": 3.0, "high": 2.0, "medium": 1.0}

_REPO_METRICS_TTL_S = 30.0
_REPO_METRICS_LOCK = threading.Lock()
_REPO_METRICS_CACHE: dict[Any, tuple[float, _RepoMetricsBundle]] = {}


class CompleteRequest(BaseModel):
    finding_id: str = Field(min_length=1, max_length=256)


class CompleteResponse(BaseModel):
    task_id: str


def _stamp_task_evidence(ws: workspace_mod.Workspace, evidence: str) -> None:
    for task in ws.meta.tasks:
        if task.dod_clauses:
            ws = workspace_mod.record_task_evidence(ws, task.id, evidence)


def _complete_merged_ghost(
    finding_id: str,
    queue: Any,
    workspaces_root: Any,
) -> str:
    try:
        ws = workspace_mod.read(finding_id, root=workspaces_root)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="workspace_not_found") from exc
    raw_prs = _fetch_raw_prs()
    pr_number_index = _build_pr_number_index(raw_prs)
    if not is_merged_ghost(ws, queue, pr_number_index):
        raise HTTPException(status_code=409, detail="no_longer_finished")
    try:
        handovers = list_handovers(ws)
    except Exception:  # noqa: BLE001
        handovers = []
    linked = _collect_linked_pr_numbers(ws, handovers)
    evidence = ", ".join(f"PR #{n}" for n in sorted(linked)) if linked else "merged"
    workspace_mod.transition(ws, workspace_mod.Status.COMPLETED)
    ws = workspace_mod.read(finding_id, root=workspaces_root)
    _stamp_task_evidence(ws, evidence)
    workspace_mod.record_intervention(ws, workspace_mod.InterventionAction.HEALTH_COMPLETE)
    add_completed_finding(queue, finding_id)
    return finding_id


@health_router.post(
    "/complete",
    status_code=200,
    responses={
        404: {"description": "workspace not found"},
        409: {"description": "no longer a merged ghost (TOCTOU)"},
    },
)
async def complete(
    body: CompleteRequest,
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
) -> CompleteResponse:
    task_id = await run_in_threadpool(
        _complete_merged_ghost, body.finding_id, queue, workspaces_root
    )
    return CompleteResponse(task_id=task_id)


def _weighted_findings_by_repo(
    findings: list[HealthFinding],
) -> dict[str, tuple[float, int]]:
    result: dict[str, tuple[float, int]] = {}
    for f in findings:
        slug = engine_config_mod.canonical_scope_key(f.engine_key).slug
        weight, count = result.get(slug, (0.0, 0))
        result[slug] = (weight + _SEVERITY_WEIGHT.get(f.severity, 1.0), count + 1)
    return result


def _visible_repo_slugs() -> list[str]:
    return sorted(
        cfg.repo.slug for cfg in engine_config_mod.registered_engines() if not cfg.hidden
    )


class HealthWindow(BaseModel):
    value: float | None
    sample: int
    covered_days: int


class HealthMetricWindows(BaseModel):
    d7: HealthWindow
    d30: HealthWindow
    verdict: health_windows_mod.Verdict
    higher_is_better: bool


class HealthFunnelWindows(BaseModel):
    d7: FunnelCounts
    d30: FunnelCounts


class HealthScopeWindows(BaseModel):
    outcome: HealthMetricWindows
    autonomous: HealthMetricWindows
    build_success: HealthMetricWindows
    first_pass_approval: HealthMetricWindows
    unproven_clauses: HealthMetricWindows
    format_error: HealthMetricWindows
    correction_recovery: HealthMetricWindows
    rescue_success: HealthMetricWindows
    rescue_load: HealthMetricWindows
    funnel: HealthFunnelWindows


class RepoRateValue(BaseModel):
    value: int | float | None
    sample: int
    low_data: bool


class RepoHealthRow(BaseModel):
    repo: str
    outstanding_findings: RepoRateValue
    in_flight: RepoRateValue
    windows: HealthScopeWindows


class RepoHealthResponse(BaseModel):
    computed_at: str | None
    metrics_computed_at: str
    unattributed: int
    fleet: HealthScopeWindows
    rows: list[RepoHealthRow]


_METRIC_NAMES = (
    "outcome",
    "autonomous",
    "build_success",
    "first_pass_approval",
    "unproven_clauses",
    "format_error",
    "correction_recovery",
    "rescue_success",
    "rescue_load",
)


@dataclass(frozen=True)
class _RepoMetricsBundle:
    windows: health_windows_mod.HealthWindows
    vacant: health_windows_mod.ScopeWindows
    in_flight: Mapping[str, int]


def _window_model(window: health_windows_mod.Window) -> HealthWindow:
    return HealthWindow(
        value=window.value, sample=window.sample, covered_days=window.covered_days
    )


def _metric_model(metric: health_windows_mod.MetricWindows) -> HealthMetricWindows:
    return HealthMetricWindows(
        d7=_window_model(metric.d7),
        d30=_window_model(metric.d30),
        verdict=metric.verdict.verdict,
        higher_is_better=metric.verdict.higher_is_better,
    )


def _scope_model(scope: health_windows_mod.ScopeWindows) -> HealthScopeWindows:
    return HealthScopeWindows.model_validate({
        **{name: _metric_model(getattr(scope, name)) for name in _METRIC_NAMES},
        "funnel": HealthFunnelWindows(d7=scope.funnel.d7, d30=scope.funnel.d30),
    })


def _window_sources(
    queue: Queue, all_ws: list[workspace_mod.Workspace]
) -> health_windows_mod.WindowSources:
    audit_dir = logs_root()
    corrections = health_repo_metrics_mod.read_block_correction_rows(audit_dir)
    return health_windows_mod.read_window_sources(queue, audit_dir, all_ws, corrections)


def _vacant_scope(
    sources: health_windows_mod.WindowSources, now: datetime
) -> health_windows_mod.ScopeWindows:
    empty = health_windows_mod.WindowSources(
        queue=sources.queue,
        workspaces=[],
        job_failure_text="",
        merge_audit_text="",
        spiral_audit_text="",
        trajectories=[],
        corrections=[],
        reach=sources.reach,
    )
    return health_windows_mod.compute_health_windows(empty, now=now).fleet


def _list_workspaces(workspaces_root: Any) -> list[workspace_mod.Workspace]:
    try:
        return workspace_mod.list_workspaces(root=workspaces_root)
    except Exception:  # noqa: BLE001
        return []


def _compute_repo_metrics(queue: Queue, workspaces_root: Any) -> _RepoMetricsBundle:
    all_ws = _list_workspaces(workspaces_root)
    now = datetime.now(UTC)
    sources = _window_sources(queue, all_ws)
    return _RepoMetricsBundle(
        windows=health_windows_mod.compute_health_windows(sources, now=now),
        vacant=_vacant_scope(sources, now),
        in_flight=health_repo_metrics_mod.in_flight_by_repo(all_ws),
    )


def _cached_repo_metrics(queue: Queue, workspaces_root: Any) -> _RepoMetricsBundle:
    with _REPO_METRICS_LOCK:
        cached = _REPO_METRICS_CACHE.get(workspaces_root)
        if cached is not None and time.monotonic() - cached[0] < _REPO_METRICS_TTL_S:
            return cached[1]
        bundle = _compute_repo_metrics(queue, workspaces_root)
        _REPO_METRICS_CACHE[workspaces_root] = (time.monotonic(), bundle)
        return bundle


def _rate_field(value: float | int | None, sample: int, low_data: bool) -> RepoRateValue:
    return RepoRateValue(value=value, sample=sample, low_data=low_data)


def _findings_field(
    findings_by_repo: dict[str, tuple[float, int]] | None, slug: str
) -> RepoRateValue:
    if findings_by_repo is None:
        return _rate_field(None, 0, False)
    weighted, count = findings_by_repo.get(slug, (0.0, 0))
    return _rate_field(weighted, count, False)


def _repo_row(
    slug: str,
    findings_by_repo: dict[str, tuple[float, int]] | None,
    bundle: _RepoMetricsBundle,
) -> RepoHealthRow:
    in_flight = bundle.in_flight.get(slug, 0)
    scope = bundle.windows.repos.get(slug, bundle.vacant)
    return RepoHealthRow(
        repo=slug,
        outstanding_findings=_findings_field(findings_by_repo, slug),
        in_flight=_rate_field(in_flight, in_flight, False),
        windows=_scope_model(scope),
    )


@health_router.get("/repos")
async def health_repos(
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
) -> RepoHealthResponse:
    state = await run_in_threadpool(load_health_state, queue)
    bundle = await run_in_threadpool(_cached_repo_metrics, queue, workspaces_root)
    findings_by_repo = None if state is None else _weighted_findings_by_repo(state.findings)
    return RepoHealthResponse(
        computed_at=None if state is None else state.computed_at,
        metrics_computed_at=bundle.windows.computed_at.isoformat(),
        unattributed=bundle.windows.unattributed,
        fleet=_scope_model(bundle.windows.fleet),
        rows=[_repo_row(slug, findings_by_repo, bundle) for slug in _visible_repo_slugs()],
    )


class ResidueItem(BaseModel):
    fingerprint: str
    title: str


class RecoverySnapshot(BaseModel):
    current_count: int
    previous_count: int
    direction: TrendDirection


class DigestSnapshotResponse(BaseModel):
    recovery: RecoverySnapshot | None
    residue: list[ResidueItem] | None
    digest_overdue: bool


def _digest_snapshot(queue: Queue) -> tuple[DigestDoc | None, bool]:
    doc = load_latest_digest(queue)
    now = datetime.now(UTC)
    digest_last_date = queue.get_meta(DIGEST_LAST_DATE_META_KEY)
    overdue = digest_overdue(digest_last_date, now, resolve_digest_deadline(now))
    return doc, overdue


@health_router.get("/digest")
async def health_digest(queue: QueueDep) -> DigestSnapshotResponse:
    doc, overdue = await run_in_threadpool(_digest_snapshot, queue)
    if doc is None:
        return DigestSnapshotResponse(recovery=None, residue=None, digest_overdue=overdue)
    totals = recovery_totals(doc.recovery_trend)
    recovery = RecoverySnapshot(
        current_count=totals.current_count,
        previous_count=totals.previous_count,
        direction=totals.direction,
    )
    residue = [ResidueItem(fingerprint=e.fingerprint, title=e.title) for e in doc.residue]
    return DigestSnapshotResponse(recovery=recovery, residue=residue, digest_overdue=overdue)


class DispositionRequest(BaseModel):
    disposition: Disposition


class DispositionResponse(BaseModel):
    fingerprint: str
    disposition: Disposition
    closed_at: str


def _set_incident_disposition(
    queue: Queue, fingerprint: str, disposition: Disposition
) -> DispositionResponse:
    record = set_disposition(queue, fingerprint, disposition, now=datetime.now(UTC))
    return DispositionResponse(
        fingerprint=fingerprint, disposition=disposition, closed_at=record.closed_at or ""
    )


@health_router.post("/incidents/{fingerprint}/disposition")
async def post_incident_disposition(
    fingerprint: str, body: DispositionRequest, queue: QueueDep
) -> DispositionResponse:
    return await run_in_threadpool(
        _set_incident_disposition, queue, fingerprint, body.disposition
    )


class UnblockOutcomeCount(BaseModel):
    outcome: str
    count: int


class UnblockShapeAggregate(BaseModel):
    shape: str
    total: int
    workspaces: int
    cleared_workspaces: int
    still_stuck_workspaces: int
    gone_workspaces: int
    retried_workspaces: int
    attempt_outcomes: list[UnblockOutcomeCount]
    report_outcomes: list[UnblockOutcomeCount]


class UnblockDiagnosis(BaseModel):
    signature: str
    proposed_disposition: str | None
    verified: bool


class UnblockAttemptIndex(BaseModel):
    fingerprint_index: int
    fingerprint_ceiling: int
    workspace_index: int
    workspace_ceiling: int


class UnblockActivityEvent(BaseModel):
    id: int
    at: str
    workspace_key: str
    shape: str
    kind: Literal["attempt", "report"]
    action: str | None
    outcome: str
    reason: str | None
    authority_holder: str | None
    clearance: Literal["cleared", "still_stuck", "unknown"]
    attempt: UnblockAttemptIndex | None
    attempts_total: int | None
    issue_url: str | None
    diagnosis: UnblockDiagnosis | None


class UnblockWorkspaceActivity(BaseModel):
    workspace_key: str
    last_at: str
    shape: str
    events_total: int
    events_listed: int
    attempts_total: int
    attempts_ceiling: int
    clearance: Literal["cleared", "still_stuck", "unknown"]
    gave_up: bool
    issue_url: str | None
    diagnosis: UnblockDiagnosis | None


class UnblockActivitySummary(BaseModel):
    window_days: int
    events_total: int
    workspaces_touched: int
    waited_workspaces: int
    diagnosed_workspaces: int
    diagnosable_workspaces: int
    still_stuck_workspaces: int
    gave_up_workspaces: int
    aggregates: list[UnblockShapeAggregate]


class UnblockFleetActivity(UnblockActivitySummary):
    scope: Literal["fleet"] = "fleet"
    ever_recorded: bool
    unattributed_events: int


class UnblockScopedActivity(UnblockActivitySummary):
    scope: Literal["repo"] = "repo"
    repo: str
    fleet_ever_recorded: bool
    events: list[UnblockActivityEvent]
    workspaces: list[UnblockWorkspaceActivity]


_ACTIVITY_TTL_S = 30.0
_ACTIVITY_CACHE_MAX = 32
_ACTIVITY_LOCK = threading.Lock()
_ACTIVITY_CACHE: dict[Any, tuple[float, UnblockFleetActivity | UnblockScopedActivity]] = {}


def _summary_fields(summary: unblock_activity_mod.ActivitySummary) -> dict[str, Any]:
    return {
        "window_days": unblock_ledger_mod.RESCUE_WINDOW_DAYS,
        "events_total": summary.events_total,
        "workspaces_touched": summary.workspaces_touched,
        "waited_workspaces": summary.waited_workspaces,
        "diagnosed_workspaces": summary.diagnosed_workspaces,
        "diagnosable_workspaces": summary.diagnosable_workspaces,
        "still_stuck_workspaces": summary.still_stuck_workspaces,
        "gave_up_workspaces": summary.gave_up_workspaces,
        "aggregates": [
            UnblockShapeAggregate(
                shape=agg.shape,
                total=agg.total,
                workspaces=agg.workspaces,
                cleared_workspaces=agg.cleared_workspaces,
                still_stuck_workspaces=agg.still_stuck_workspaces,
                gone_workspaces=agg.gone_workspaces,
                retried_workspaces=agg.retried_workspaces,
                attempt_outcomes=[
                    UnblockOutcomeCount(outcome=o.outcome, count=o.count)
                    for o in agg.attempt_outcomes
                ],
                report_outcomes=[
                    UnblockOutcomeCount(outcome=o.outcome, count=o.count)
                    for o in agg.report_outcomes
                ],
            )
            for agg in summary.aggregates
        ],
    }


def _diagnosis_model(
    diagnosis: unblock_activity_mod.DiagnosisSummary | None,
) -> UnblockDiagnosis | None:
    if diagnosis is None:
        return None
    return UnblockDiagnosis(
        signature=diagnosis.signature,
        proposed_disposition=diagnosis.proposed_disposition,
        verified=diagnosis.verified,
    )


def _attempt_model(
    attempt: unblock_activity_mod.AttemptIndex | None,
) -> UnblockAttemptIndex | None:
    if attempt is None:
        return None
    return UnblockAttemptIndex(
        fingerprint_index=attempt.fingerprint_index,
        fingerprint_ceiling=attempt.fingerprint_ceiling,
        workspace_index=attempt.workspace_index,
        workspace_ceiling=attempt.workspace_ceiling,
    )


def _event_model(event: unblock_activity_mod.ActivityEvent) -> UnblockActivityEvent:
    return UnblockActivityEvent.model_validate({
        "id": event.id,
        "at": unblock_ledger_mod.stamp(event.at),
        "workspace_key": event.workspace_key,
        "shape": event.shape,
        "kind": event.kind,
        "action": event.action,
        "outcome": event.outcome,
        "reason": event.reason,
        "authority_holder": event.authority_holder,
        "clearance": event.clearance.value,
        "attempt": _attempt_model(event.attempt),
        "attempts_total": event.attempts_total,
        "issue_url": event.issue_url,
        "diagnosis": _diagnosis_model(event.diagnosis),
    })


def _workspace_model(
    workspace: unblock_activity_mod.WorkspaceActivity,
) -> UnblockWorkspaceActivity:
    return UnblockWorkspaceActivity.model_validate({
        "workspace_key": workspace.workspace_key,
        "last_at": unblock_ledger_mod.stamp(workspace.last_at),
        "shape": workspace.shape,
        "events_total": workspace.events_total,
        "events_listed": workspace.events_listed,
        "attempts_total": workspace.attempts_total,
        "attempts_ceiling": workspace.attempts_ceiling,
        "clearance": workspace.clearance.value,
        "gave_up": workspace.gave_up,
        "issue_url": workspace.issue_url,
        "diagnosis": _diagnosis_model(workspace.diagnosis),
    })


def _compute_activity(
    queue: Queue, workspaces_root: Any, slug: str | None
) -> UnblockFleetActivity | UnblockScopedActivity:
    all_ws = workspace_mod.list_workspaces(root=workspaces_root)
    now = time.time()
    if slug is None:
        fleet = unblock_activity_mod.collect_fleet(
            queue, all_ws, now=now, visible_slugs=_visible_repo_slugs()
        )
        return UnblockFleetActivity(
            ever_recorded=fleet.ever_recorded,
            unattributed_events=fleet.unattributed_events,
            **_summary_fields(fleet.summary),
        )
    scoped = unblock_activity_mod.collect_repo(queue, all_ws, now=now, slug=slug)
    return UnblockScopedActivity(
        repo=scoped.slug,
        fleet_ever_recorded=scoped.fleet_ever_recorded,
        events=[_event_model(e) for e in scoped.events],
        workspaces=[_workspace_model(w) for w in scoped.workspaces],
        **_summary_fields(scoped.summary),
    )


def _evict_activity_cache(now: float) -> None:
    expired = [
        key
        for key, (stamped, _payload) in _ACTIVITY_CACHE.items()
        if now - stamped >= _ACTIVITY_TTL_S
    ]
    for key in expired:
        del _ACTIVITY_CACHE[key]
    while len(_ACTIVITY_CACHE) >= _ACTIVITY_CACHE_MAX:
        del _ACTIVITY_CACHE[next(iter(_ACTIVITY_CACHE))]


def _cached_activity(
    queue: Queue, workspaces_root: Any, slug: str | None
) -> UnblockFleetActivity | UnblockScopedActivity:
    key = (id(queue), workspaces_root, slug)
    # The compute runs outside the lock so a cold ledger scan never blocks cache hits;
    # concurrent misses on one key may each compute, and the last store wins.
    with _ACTIVITY_LOCK:
        cached = _ACTIVITY_CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] < _ACTIVITY_TTL_S:
        return cached[1]
    payload = _compute_activity(queue, workspaces_root, slug)
    with _ACTIVITY_LOCK:
        _evict_activity_cache(time.monotonic())
        _ACTIVITY_CACHE[key] = (time.monotonic(), payload)
    return payload


@health_router.get("/unblocker-activity")
async def unblocker_activity(
    queue: QueueDep,
    workspaces_root: WorkspacesRootDep,
    engine: Annotated[str | None, Query()] = None,
) -> UnblockFleetActivity | UnblockScopedActivity:
    return await run_in_threadpool(_cached_activity, queue, workspaces_root, engine)
