from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

import alan.workspace as workspace_mod
from alan.engine_config import registered_engines
from alan.finisher_rate import FINISHER_FLOOR, FinisherRate, fr_from_dict, fr_to_dict
from alan.learning_metrics import LearningMetrics, learning_metrics_to_dict
from alan.queue import SPEND_CAP_STOP_META_KEY, Queue
from alan.teams.triage_pregate import ClassifiedSurface

_ListObject: TypeAlias = list[object]
_DictStrAny: TypeAlias = dict[str, Any]

SWEEP_CADENCE_SECONDS = 1800.0
_STALE_SECONDS = SWEEP_CADENCE_SECONDS * 2
HEALTH_META_KEY = "health_state_v1"
_FINISHER_RATE_META_KEY = "finisher_rate_v1"
_LEARNING_METRICS_META_KEY = "learning_metrics_v1"
_MERGE_GATE_RECOVERY_META_KEY = "merge_gate_recovery_v1"
_RESTART_RECOVERY_META_KEY = "restart_recovery_v1"

SPEND_CAP_CAUSE = (
    "Anthropic refused work because the spend limit for this organisation "
    "or workspace was reached."
)
SPEND_CAP_FIX = (
    "Raise the spend limit in the Anthropic Console, or wait until access "
    "returns, then press Resume."
)

_DIGEST_TZ_ENV = "ALAN_DIGEST_TZ"
_DIGEST_HOUR_ENV = "ALAN_DIGEST_HOUR"
_DEFAULT_DIGEST_HOUR = 9

_ACTION_MERGE_PR: Literal["merge_pr"] = "merge_pr"
_ACTION_DISPATCH: Literal["dispatch_remediation"] = "dispatch_remediation"
_ACTION_DEEP_LINK: Literal["deep_link"] = "deep_link"
_ACTION_COMPLETE: Literal["complete_workspace"] = "complete_workspace"
_COMPLETED_FINDING_IDS_KEY = "completed_finding_ids"

HealthAction = Literal["merge_pr", "dispatch_remediation", "deep_link", "complete_workspace"]
HealthSeverity = Literal["critical", "high", "medium"]
HealthVerdict = Literal["healthy", "degraded"]


@dataclass
class HealthFinding:
    finding_id: str
    kind: str
    severity: HealthSeverity
    description: str
    workspace_id: str | None
    pr_number: int | None
    blocker_flags: list[str]
    action: HealthAction
    in_flight: bool = False
    repo: str | None = None
    engine_key: str | None = None
    observed_at: str | None = None


@dataclass
class HealthState:
    verdict: HealthVerdict
    blocked_count: int
    in_flight_count: int
    findings: list[HealthFinding]
    computed_at: str
    resolvable_findings: list[HealthFinding] = field(
        default_factory=list[HealthFinding]
    )


class SpendCapStop(BaseModel):
    cause: str
    fix: str
    regain_at: str | None = None


def _parse_blocker_flags(reason: str) -> list[str]:
    if not reason:
        return []
    return [f.strip() for f in reason.split(",") if f.strip()]


def _build_task_severity_action(
    flags: set[str], in_flight: bool, fix_cycle_escalate: bool = False
) -> tuple[HealthSeverity, HealthAction]:
    if "pr_checks_failing" in flags:
        escalated = in_flight and fix_cycle_escalate
        severity: HealthSeverity = "critical" if (escalated or not in_flight) else "medium"
        return severity, _ACTION_DISPATCH
    if "job_failed" in flags:
        return "high", _ACTION_DISPATCH
    if "pr_approved_unmerged" in flags:
        return "high", _ACTION_MERGE_PR
    return "medium", _ACTION_DEEP_LINK


_SIMPLE_SEVERITY_ACTION: dict[str, tuple[HealthSeverity, HealthAction]] = {
    "main_ci": ("critical", _ACTION_DISPATCH),
    "deploy": ("critical", _ACTION_DISPATCH),
    "worker_pool": ("critical", _ACTION_DEEP_LINK),
    "forge_degraded": ("high", _ACTION_DEEP_LINK),
    "stale_job": ("high", _ACTION_DEEP_LINK),
    "finisher_regression": ("medium", _ACTION_DEEP_LINK),
    "planner_behavioral_regression": ("medium", _ACTION_DEEP_LINK),
    "retention_near_cap": ("high", _ACTION_DEEP_LINK),
    "stale_live_process": ("critical", _ACTION_DEEP_LINK),
    "config_quarantine": ("high", _ACTION_DEEP_LINK),
    "driver_loop_unhealthy": ("high", _ACTION_DEEP_LINK),
    "driver_loop_attention": ("medium", _ACTION_DEEP_LINK),
    "credential_expiry": ("high", _ACTION_DEEP_LINK),
    "host_toolchain": ("critical", _ACTION_DEEP_LINK),
    "auto_update": ("high", _ACTION_DEEP_LINK),
}

_CRITICAL_AUTO_UPDATE_FLAG = "rollback_failed"


def _severity_action(kind: str, flags: set[str]) -> tuple[HealthSeverity, HealthAction]:
    if "forge_low_headroom" in flags:
        return "medium", _ACTION_DEEP_LINK
    if _CRITICAL_AUTO_UPDATE_FLAG in flags:
        return "critical", _ACTION_DEEP_LINK
    return _SIMPLE_SEVERITY_ACTION.get(kind, ("high", _ACTION_DISPATCH))


def _fix_cycle_running_tag(obs: dict[str, Any]) -> str:
    if not obs.get("job_running", False):
        return ""
    raw_n = obs.get("fix_cycle_count", 0)
    n = raw_n if isinstance(raw_n, int) else 0
    return f" — work is running ({n} fix cycles)" if n > 0 else " — work is running"


def _describe_build(surface_id: str, flags: set[str], obs: dict[str, Any]) -> str:
    pr = obs.get("pr_number")
    pr_tag = f" (PR #{pr})" if pr else ""
    if "pr_checks_failing" in flags:
        return (
            f"Build task {surface_id} has failing CI checks{pr_tag}"
            f"{_fix_cycle_running_tag(obs)}"
        )
    if "job_failed" in flags:
        return f"Build task {surface_id} has a failed job"
    if "pr_approved_unmerged" in flags:
        return f"Build task {surface_id} has an approved, unmerged PR{pr_tag}"
    if obs.get("has_subtasks"):
        return (
            f"Build task {surface_id} looks finished — its linked PRs are "
            "all merged, but the workspace has multiple subtasks and "
            "completing it here would abandon any sibling that never "
            "produced a PR. Recovery: open the workspace, check each "
            "subtask's status, and dispatch or resolve the pending sibling "
            "before completing the workspace."
        )
    return f"Build task {surface_id} is awaiting human input"


def _describe_stale_job(surface_id: str, obs: dict[str, Any]) -> str:
    ws = obs.get("workspace_id") or surface_id
    elapsed = obs.get("elapsed_seconds", "?")
    return f"Stale running job in workspace {ws} ({elapsed}s elapsed)"


def _describe_main_ci(obs: dict[str, Any]) -> str:
    conclusion = obs.get("conclusion") or "unknown"
    return f"Main branch CI is failing ({conclusion})"


def _describe_deploy(obs: dict[str, Any]) -> str:
    conclusion = obs.get("conclusion") or "unknown"
    return f"Main branch deploy is failing ({conclusion}) — live build is stale"


def _describe_finisher_regression(obs: dict[str, Any]) -> str:
    rate = obs.get("rate")
    floor = obs.get("floor", FINISHER_FLOOR)
    sample = obs.get("sample_size", 0)
    rate_str = f"{rate:.1%}" if isinstance(rate, (int, float)) else "unknown"
    floor_str = f"{floor:.1%}" if isinstance(floor, (int, float)) else f"{FINISHER_FLOOR:.1%}"
    return f"Finisher rate {rate_str} is below floor {floor_str} (sample={sample})"


def _describe_behavioral_regression(obs: dict[str, Any]) -> str:
    score = obs.get("score")
    baseline = obs.get("baseline")
    score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "unknown"
    baseline_str = f"{baseline:.3f}" if isinstance(baseline, (int, float)) else "unknown"
    return f"Planner behavioral eval score {score_str} regressed below baseline {baseline_str}"


def _describe_worker_pool(obs: dict[str, Any]) -> str:
    alive = obs.get("alive", 0)
    configured = obs.get("configured", 0)
    return (
        f"Dispatch loop threads: {alive}/{configured} live; "
        "no running jobs or live worker processes found"
    )


def _describe_forge_degraded(flags: set[str], obs: dict[str, Any]) -> str:
    if "forge_low_headroom" in flags:
        remaining = obs.get("remaining")
        limit = obs.get("limit")
        if isinstance(remaining, int) and isinstance(limit, int) and limit > 0:
            return f"GitHub API quota headroom low: {remaining}/{limit} remaining"
        return "GitHub API quota headroom is low"
    base = "GitHub API degraded (rate limited) — dispatch paused"
    reset_at = obs.get("reset_at")
    if not isinstance(reset_at, (int, float)):
        return base
    try:
        when = datetime.fromtimestamp(reset_at, tz=UTC).strftime("%H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return base
    return f"{base}, auto-resumes at {when}"


_STALE_LIVE_PROCESS_SAMPLE_CAP = 8


def _hot_path_sample_suffix(paths: list[str]) -> str:
    if not paths:
        return ""
    sample = paths[:_STALE_LIVE_PROCESS_SAMPLE_CAP]
    omitted = len(paths) - len(sample)
    more = f" (+{omitted} more)" if omitted > 0 else ""
    return f": {', '.join(sample)}{more}"


def _describe_stale_live_process(obs: dict[str, Any]) -> str:
    served = str(obs.get("served_sha") or "")[:12]
    target = str(obs.get("target_sha") or "")[:12]
    raw: Any = obs.get("hot_paths")
    items = cast(_ListObject, raw) if isinstance(raw, list) else []
    paths = [p for p in items if isinstance(p, str)]
    count = len(paths)
    return (
        f"Live process running {served} but main is at {target} "
        f"({count} hot-code path{'s' if count != 1 else ''} not yet loaded)"
        f"{_hot_path_sample_suffix(paths)}"
    )


def _describe_retention_near_cap(obs: dict[str, Any]) -> str:
    title = obs.get("title") or obs.get("task_id") or "workspace"
    used = obs.get("used", "?")
    cap = obs.get("cap", "?")
    return f"Recurring workspace '{title}' is near its invocation-slot cap ({used}/{cap})"


def _describe_recurring(obs: dict[str, Any]) -> str:
    raw: Any = obs.get("recurring_signatures")
    items = cast(_ListObject, raw) if isinstance(raw, list) else []
    sigs = [s for s in items if isinstance(s, str)]
    count = len(sigs)
    base = f"Recurring errors detected ({count} pattern{'s' if count != 1 else ''})"
    return f"{base}: {', '.join(sigs)}" if sigs else base


def _describe_config_quarantine(obs: dict[str, Any]) -> str:
    path = obs.get("path") or "unknown path"
    error = obs.get("error") or "unknown error"
    return f"Config file {path} failed to load: {error}"


def _describe_driver_loop_unhealthy(obs: dict[str, Any]) -> str:
    name = obs.get("name") or "unknown loop"
    reason = obs.get("reason") or "unhealthy"
    return f"Driver loop '{name}' is unhealthy ({reason})"


def _describe_driver_loop_attention(obs: dict[str, Any]) -> str:
    name = obs.get("name") or "unknown loop"
    reason = obs.get("reason") or "needs attention"
    return f"Driver loop '{name}' needs attention ({reason})"


def _describe_credential_expiry(obs: dict[str, Any]) -> str:
    name = obs.get("name") or "unknown credential"
    expires_at = obs.get("expires_at") or "an unrecorded date"
    return f"Credential {name!r} expires {expires_at}"


def _describe_host_toolchain(obs: dict[str, Any]) -> str:
    raw_paths: Any = obs.get("resolved_paths")
    paths = cast(_DictStrAny, raw_paths) if isinstance(raw_paths, dict) else {}
    git_path = paths.get("git") or "unresolved"
    python_path = paths.get("python3") or "unresolved"
    failing_tool = obs.get("failing_tool") or "a probed tool"
    message = obs.get("message") or "no output captured"
    recovery = obs.get("recovery_command") or "no recovery command captured"
    return (
        f"Host toolchain gated — git resolves to {git_path}, python3 resolves "
        f"to {python_path}. {failing_tool} failed: {message!r}. "
        f"Recovery: {recovery}. This finding clears on the first health sweep "
        "after the host is repaired."
    )


def _describe_auto_update(obs: dict[str, Any]) -> str:
    reason = obs.get("reason") or "unknown"
    checked_at = obs.get("checked_at")
    target_sha = obs.get("target_sha")
    detail = f" (checked {checked_at})" if isinstance(checked_at, str) else ""
    if not detail and isinstance(target_sha, str):
        detail = f" (target {target_sha[:12]})"
    return f"Automatic update needs attention: {reason}{detail}"


_OBS_DESCRIBERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "main_ci": _describe_main_ci,
    "deploy": _describe_deploy,
    "worker_pool": _describe_worker_pool,
    "finisher_regression": _describe_finisher_regression,
    "planner_behavioral_regression": _describe_behavioral_regression,
    "recurring_errors": _describe_recurring,
    "retention_near_cap": _describe_retention_near_cap,
    "stale_live_process": _describe_stale_live_process,
    "config_quarantine": _describe_config_quarantine,
    "driver_loop_unhealthy": _describe_driver_loop_unhealthy,
    "driver_loop_attention": _describe_driver_loop_attention,
    "credential_expiry": _describe_credential_expiry,
    "host_toolchain": _describe_host_toolchain,
    "auto_update": _describe_auto_update,
}


def _describe(kind: str, surface_id: str, flags: set[str], obs: dict[str, Any]) -> str:
    if kind == "build_task":
        return _describe_build(surface_id, flags, obs)
    if kind == "stale_job":
        return _describe_stale_job(surface_id, obs)
    if kind == "forge_degraded":
        return _describe_forge_degraded(flags, obs)
    describer = _OBS_DESCRIBERS.get(kind)
    return describer(obs) if describer is not None else _describe_recurring(obs)


def _is_in_flight(s: ClassifiedSurface) -> bool:
    return s.kind == "build_task" and bool(s.obs.get("job_running", False))


def _opt_str_obs(obs: dict[str, Any], key: str) -> str | None:
    raw: Any = obs.get(key)
    return str(raw) if raw is not None else None


_WORKSPACE_ID_OBS_KEY: dict[str, str] = {
    "stale_job": "workspace_id",
    "retention_near_cap": "task_id",
}


def _finding_workspace_and_pr(surface: ClassifiedSurface) -> tuple[str | None, int | None]:
    if surface.kind == "build_task":
        raw_pr = surface.obs.get("pr_number")
        pr_number = int(raw_pr) if isinstance(raw_pr, int) else None
        return surface.surface_id, pr_number
    obs_key = _WORKSPACE_ID_OBS_KEY.get(surface.kind)
    if obs_key is None:
        return None, None
    return _opt_str_obs(surface.obs, obs_key), None


def _engine_key_for_workspace(
    workspace_id: str | None, workspaces_root: Path | None
) -> str | None:
    if workspace_id is None:
        return None
    try:
        ws = workspace_mod.read(workspace_id, root=workspaces_root)
    except workspace_mod.WorkspaceError:
        return None
    return ws.meta.engine_key


def _surface_to_finding(
    surface: ClassifiedSurface, workspaces_root: Path | None = None
) -> HealthFinding:
    flags = set(_parse_blocker_flags(surface.blocker_reason))
    in_flight = _is_in_flight(surface)
    severity: HealthSeverity
    action: HealthAction
    if surface.resolvable and surface.kind == "build_task":
        severity = "medium"
        action = _ACTION_COMPLETE
        description = (
            f"Build task {surface.surface_id} looks finished — linked PRs all merged"
        )
    elif surface.kind == "build_task":
        fix_cycle_escalate = bool(surface.obs.get("fix_cycle_escalate", False))
        severity, action = _build_task_severity_action(flags, in_flight, fix_cycle_escalate)
        description = _describe(surface.kind, surface.surface_id, flags, surface.obs)
    else:
        severity, action = _severity_action(surface.kind, flags)
        description = _describe(surface.kind, surface.surface_id, flags, surface.obs)
    workspace_id, pr_number = _finding_workspace_and_pr(surface)
    return HealthFinding(
        finding_id=surface.surface_id,
        kind=surface.kind,
        severity=severity,
        description=description,
        workspace_id=workspace_id,
        pr_number=pr_number,
        blocker_flags=list(flags),
        action=action,
        in_flight=in_flight,
        repo=_opt_str_obs(surface.obs, "repo"),
        engine_key=(
            _opt_str_obs(surface.obs, "engine_key")
            or _engine_key_for_workspace(workspace_id, workspaces_root)
        ),
        observed_at=_opt_str_obs(surface.obs, "observed_at"),
    )


_BEHAVIORAL_REGRESSION_KIND = "planner_behavioral_regression"


def behavioral_regression_surface(metrics: LearningMetrics) -> ClassifiedSurface | None:
    if not metrics.planner_behavioral_regression:
        return None
    return ClassifiedSurface(
        surface_id=_BEHAVIORAL_REGRESSION_KIND,
        kind=_BEHAVIORAL_REGRESSION_KIND,
        is_blocked=True,
        blocker_reason="planner_behavioral_score_regressed",
        obs={
            "score": metrics.planner_behavioral_score,
            "baseline": metrics.planner_behavioral_baseline,
        },
    )


def _split_surfaces(
    surfaces: list[ClassifiedSurface],
) -> tuple[list[ClassifiedSurface], list[ClassifiedSurface]]:
    blocked = [s for s in surfaces if s.is_blocked]
    resolvable = [s for s in surfaces if s.resolvable and not s.is_blocked]
    return blocked, resolvable


def hidden_repo_slugs() -> frozenset[str]:
    return frozenset(cfg.repo.slug for cfg in registered_engines() if cfg.hidden)


def _is_hidden_finding(finding: HealthFinding, hidden_slugs: frozenset[str]) -> bool:
    return finding.repo in hidden_slugs or finding.engine_key in hidden_slugs


def _visible_findings(
    surfaces: list[ClassifiedSurface],
    workspaces_root: Path | None,
    hidden_slugs: frozenset[str],
) -> list[HealthFinding]:
    findings = [_surface_to_finding(s, workspaces_root) for s in surfaces]
    if not hidden_slugs:
        return findings
    return [f for f in findings if not _is_hidden_finding(f, hidden_slugs)]


def compute_health_state(
    surfaces: list[ClassifiedSurface],
    computed_at: datetime,
    *,
    workspaces_root: Path | None = None,
) -> HealthState:
    blocked, resolvable = _split_surfaces(surfaces)
    hidden_slugs = hidden_repo_slugs()
    findings = _visible_findings(blocked, workspaces_root, hidden_slugs)
    resolvable_findings = _visible_findings(resolvable, workspaces_root, hidden_slugs)
    verdict: HealthVerdict = "degraded" if findings else "healthy"
    return HealthState(
        verdict=verdict,
        blocked_count=len(findings),
        in_flight_count=sum(1 for f in findings if f.in_flight),
        findings=findings,
        computed_at=computed_at.isoformat(),
        resolvable_findings=resolvable_findings,
    )


def _finding_to_dict(f: HealthFinding) -> dict[str, Any]:
    return {
        "finding_id": f.finding_id,
        "kind": f.kind,
        "severity": f.severity,
        "description": f.description,
        "workspace_id": f.workspace_id,
        "pr_number": f.pr_number,
        "blocker_flags": f.blocker_flags,
        "action": f.action,
        "in_flight": f.in_flight,
        "repo": f.repo,
        "engine_key": f.engine_key,
        "observed_at": f.observed_at,
    }


def _state_to_dict(state: HealthState) -> dict[str, Any]:
    return {
        "verdict": state.verdict,
        "blocked_count": state.blocked_count,
        "in_flight_count": state.in_flight_count,
        "findings": [_finding_to_dict(f) for f in state.findings],
        "computed_at": state.computed_at,
        "resolvable_findings": [_finding_to_dict(f) for f in state.resolvable_findings],
    }


def _prune_stale_completed(queue: Queue, active_resolvable_ids: set[str]) -> None:
    raw = queue.get_meta(_COMPLETED_FINDING_IDS_KEY)
    if raw is None:
        return
    try:
        loaded: Any = json.loads(raw)
        if not isinstance(loaded, list):
            return
        pruned = sorted(
            x for x in cast(_ListObject, loaded)
            if isinstance(x, str) and x in active_resolvable_ids
        )
        queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps(pruned))
    except Exception:  # noqa: BLE001, S110  # nosec B110
        pass


def save_health_state(queue: Queue, state: HealthState) -> None:
    queue.set_meta(HEALTH_META_KEY, json.dumps(_state_to_dict(state)))
    resolvable_ids = {f.finding_id for f in state.resolvable_findings}
    _prune_stale_completed(queue, resolvable_ids)


def _opt_str(d: dict[str, Any], key: str) -> str | None:
    val: Any = d.get(key)
    return str(val) if val is not None else None


def _opt_int(d: dict[str, Any], key: str) -> int | None:
    val: Any = d.get(key)
    return int(val) if val is not None else None


def _str_default(d: dict[str, Any], key: str, default: str) -> str:
    return str(d.get(key) or default)


def _build_finding(d: dict[str, Any]) -> HealthFinding:
    flags_raw: Any = d.get("blocker_flags")
    flags: list[str] = cast("list[str]", flags_raw) if isinstance(flags_raw, list) else []
    in_flight_raw: Any = d.get("in_flight", False)
    return HealthFinding(
        finding_id=_str_default(d, "finding_id", ""),
        kind=_str_default(d, "kind", ""),
        severity=cast("HealthSeverity", _str_default(d, "severity", "medium")),
        description=_str_default(d, "description", ""),
        workspace_id=_opt_str(d, "workspace_id"),
        pr_number=_opt_int(d, "pr_number"),
        blocker_flags=flags,
        action=cast("HealthAction", _str_default(d, "action", "deep_link")),
        in_flight=bool(in_flight_raw),
        repo=_opt_str(d, "repo"),
        engine_key=_opt_str(d, "engine_key"),
        observed_at=_opt_str(d, "observed_at"),
    )


def _load_finding(raw: Any) -> HealthFinding | None:
    if not isinstance(raw, dict):
        return None
    d: dict[str, Any] = cast(_DictStrAny, raw)
    try:
        return _build_finding(d)
    except Exception:  # noqa: BLE001
        return None


def _load_findings_list(raw: dict[str, Any]) -> list[HealthFinding]:
    items: object = raw.get("findings")
    if not isinstance(items, list):
        return []
    typed = cast(_ListObject, items)
    return [f for f in (_load_finding(r) for r in typed) if f is not None]


def _load_resolvable_findings(d: dict[str, Any]) -> list[HealthFinding]:
    resolvable_raw: object = d.get("resolvable_findings", [])
    if not isinstance(resolvable_raw, list):
        return []
    typed = cast(_ListObject, resolvable_raw)
    return [f for f in (_load_finding(r) for r in typed) if f is not None]


def load_health_state(queue: Queue) -> HealthState | None:
    raw_str = queue.get_meta(HEALTH_META_KEY)
    if raw_str is None:
        return None
    try:
        loaded: Any = json.loads(raw_str)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    d: dict[str, Any] = cast(_DictStrAny, loaded)
    try:
        return HealthState(
            verdict=cast("HealthVerdict", _str_default(d, "verdict", "healthy")),
            blocked_count=int(d.get("blocked_count") or 0),
            in_flight_count=int(d.get("in_flight_count") or 0),
            findings=_load_findings_list(d),
            computed_at=_str_default(d, "computed_at", ""),
            resolvable_findings=_load_resolvable_findings(d),
        )
    except Exception:  # noqa: BLE001
        return None


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo is not None else None


def is_stale(state: HealthState) -> bool:
    computed = _parse_iso(state.computed_at)
    if computed is None:
        return True
    return (datetime.now(UTC) - computed).total_seconds() > _STALE_SECONDS


def _resolve_digest_tz() -> ZoneInfo:
    name = os.environ.get(_DIGEST_TZ_ENV, "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _resolve_digest_hour() -> int:
    raw = os.environ.get(_DIGEST_HOUR_ENV, "").strip()
    try:
        hour = int(raw) if raw else _DEFAULT_DIGEST_HOUR
    except ValueError:
        return _DEFAULT_DIGEST_HOUR
    return hour if 0 <= hour <= 23 else _DEFAULT_DIGEST_HOUR


def resolve_digest_deadline(now: datetime) -> datetime:
    local_now = now.astimezone(_resolve_digest_tz())
    return local_now.replace(hour=_resolve_digest_hour(), minute=0, second=0, microsecond=0)


def add_completed_finding(queue: Queue, finding_id: str) -> None:
    raw = queue.get_meta(_COMPLETED_FINDING_IDS_KEY)
    ids: list[str] = []
    if raw is not None:
        try:
            loaded: Any = json.loads(raw)
            if isinstance(loaded, list):
                ids = [x for x in cast(_ListObject, loaded) if isinstance(x, str)]
        except Exception:  # noqa: BLE001, S110  # nosec B110
            pass
    if finding_id not in ids:
        ids.append(finding_id)
    queue.set_meta(_COMPLETED_FINDING_IDS_KEY, json.dumps(sorted(ids)))


def load_completed_finding_ids(queue: Queue) -> set[str]:
    raw = queue.get_meta(_COMPLETED_FINDING_IDS_KEY)
    if raw is None:
        return set()
    try:
        loaded: Any = json.loads(raw)
        if not isinstance(loaded, list):
            return set()
        return {x for x in cast(_ListObject, loaded) if isinstance(x, str)}
    except Exception:  # noqa: BLE001, S110  # nosec B110
        return set()


def save_finisher_rate(queue: Queue, fr: FinisherRate) -> None:
    queue.set_meta(_FINISHER_RATE_META_KEY, json.dumps(fr_to_dict(fr)))


def save_learning_metrics(
    queue: Queue, metrics: LearningMetrics, computed_at: datetime
) -> None:
    data: dict[str, object] = {
        **learning_metrics_to_dict(metrics),
        "computed_at": computed_at.isoformat(),
    }
    queue.set_meta(_LEARNING_METRICS_META_KEY, json.dumps(data))


def load_learning_metrics(queue: Queue) -> dict[str, Any] | None:
    raw = queue.get_meta(_LEARNING_METRICS_META_KEY)
    if raw is None:
        return None
    try:
        loaded: Any = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    return cast(_DictStrAny, loaded)


def load_merge_gate_recovery(queue: Queue) -> dict[str, Any] | None:
    raw = queue.get_meta(_MERGE_GATE_RECOVERY_META_KEY)
    if raw is None:
        return None
    try:
        loaded: Any = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    return cast(_DictStrAny, loaded)


def load_restart_recovery(queue: Queue) -> dict[str, Any] | None:
    raw = queue.get_meta(_RESTART_RECOVERY_META_KEY)
    if raw is None:
        return None
    try:
        loaded: Any = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    return cast(_DictStrAny, loaded)


def _is_valid_regain_at(value: object) -> bool:
    return value is None or (isinstance(value, str) and _parse_iso(value) is not None)


def _is_valid_stopped_at(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_valid_spend_cap_marker(marker: _DictStrAny) -> bool:
    return (
        "regain_at" in marker
        and _is_valid_regain_at(marker["regain_at"])
        and _is_valid_stopped_at(marker.get("stopped_at"))
    )


def load_spend_cap_stop(queue: Queue) -> SpendCapStop | None:
    raw = queue.get_meta(SPEND_CAP_STOP_META_KEY)
    if raw is None:
        return None
    try:
        loaded: Any = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    marker = cast(_DictStrAny, loaded)
    if not _is_valid_spend_cap_marker(marker):
        return None
    return SpendCapStop(
        cause=SPEND_CAP_CAUSE, fix=SPEND_CAP_FIX, regain_at=marker["regain_at"]
    )


def load_finisher_rate(queue: Queue) -> FinisherRate | None:
    raw = queue.get_meta(_FINISHER_RATE_META_KEY)
    if raw is None:
        return None
    try:
        loaded: Any = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    try:
        return fr_from_dict(cast(_DictStrAny, loaded))
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "HEALTH_META_KEY",
    "SWEEP_CADENCE_SECONDS",
    "HealthFinding",
    "HealthState",
    "SpendCapStop",
    "add_completed_finding",
    "behavioral_regression_surface",
    "compute_health_state",
    "hidden_repo_slugs",
    "is_stale",
    "load_completed_finding_ids",
    "load_finisher_rate",
    "load_health_state",
    "load_learning_metrics",
    "load_merge_gate_recovery",
    "load_restart_recovery",
    "load_spend_cap_stop",
    "resolve_digest_deadline",
    "save_finisher_rate",
    "save_health_state",
    "save_learning_metrics",
]
