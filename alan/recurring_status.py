from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from alan import delivery_stats as delivery_stats_mod
from alan import engine_config, recurring_forge
from alan.digest_sweep import DIGEST_LAST_DATE_META_KEY, load_latest_digest
from alan.engine_config import EngineConfig, ForgeTarget, RepoIdentity, forge_target_for_workspace
from alan.events import PubSub
from alan.health import SWEEP_CADENCE_SECONDS, resolve_digest_deadline
from alan.incident import digest_overdue
from alan.queue import Queue
from alan.repo_status import Provenance, unknown_provenance
from alan.repo_status import (
    _resolve_engine as _resolve_engine,  # pyright: ignore[reportPrivateUsage]
)
from alan.repo_status import _scoped_rows as _scoped_rows  # pyright: ignore[reportPrivateUsage]
from alan.roots import state_root
from alan.teams._triage_sweep_status import load_triage_sweep_status
from alan.teams.dependency_sweep import load_dependency_sweep_status
from alan.workspace_index import WorkspaceIndex

ProcessFamily = Literal["scheduled_ci", "dependency_update", "system_sweep", "backup"]
DurationState = Literal["measured", "not_applicable", "unknown"]
OverdueState = Literal["on_time", "overdue", "unknown"]
CadenceKind = Literal["interval_seconds", "cron", "manual", "rides_on"]
RunState = Literal[
    "succeeded", "failed", "attention", "cancelled", "in_progress", "queued", "never_ran", "unknown"
]

_OVERDUE_GRACE_MULTIPLIER = 1.5
_BACKUP_JOB_CADENCE_SECONDS = 6 * 3600.0
_BACKUP_RECEIPT_FILE = "backup_receipt.json"
_CONCLUSION_STATE: dict[str, RunState] = {
    "success": "succeeded",
    "failure": "failed",
    "timed_out": "failed",
    "startup_failure": "failed",
    "action_required": "attention",
    "neutral": "cancelled",
    "cancelled": "cancelled",
    "skipped": "cancelled",
    "stale": "cancelled",
}
_SWEEP_STATE: dict[str, RunState] = {
    "success": "succeeded",
    "degraded": "attention",
    "failure": "failed",
}
_QUEUED_STATUSES: frozenset[str] = frozenset({"queued", "waiting", "requested", "pending"})
_BACKUP_EXCLUSION_CAVEAT = (
    "per-repository engine configuration (config/engines.d/, secrets, credentials) is "
    "structurally excluded from every backup snapshot"
)
_SERVICE_SCOPE_NOTE = (
    "the failure-triage sweep, daily digest, and state backup are system-wide processes; "
    "they are reported only on alan's own repository section"
)
_DEPENDENCY_SWEEP_LABEL = "Alan's dependency sweep"

_CRON_FIELD_COUNT = 5
_SECONDS_PER_MINUTE = 60.0
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_DAYS_PER_WEEK = 7


class Cadence(BaseModel, frozen=True):
    kind: CadenceKind
    interval_seconds: float | None = None
    cron_expression: str | None = None
    rides_on: str | None = None
    detail: str


class Overdue(BaseModel, frozen=True):
    state: OverdueState
    detail: str


class LastRun(BaseModel, frozen=True):
    provenance: Provenance
    at: str | None = None
    state: RunState = "unknown"
    outcome: str | None = None
    url: str | None = None


class RecurringProcess(BaseModel, frozen=True):
    process_id: str
    family: ProcessFamily
    label: str
    cadence: Cadence
    last_run: LastRun
    duration_state: DurationState
    duration_seconds: float | None = None
    overdue: Overdue
    sub_steps: tuple[RecurringProcess, ...] = ()


class BackupCoverage(BaseModel, frozen=True):
    provenance: Provenance
    included_in_last_snapshot: bool | None = None
    changed_since_count: int | None = None
    excluded_caveat: str


class RecurringStatus(BaseModel, frozen=True):
    slug: str
    is_default: bool
    processes: list[RecurringProcess]
    service_scope_note: str | None = None
    backup_coverage: BackupCoverage


class _BackupReceipt(BaseModel, frozen=True):
    outcome: str
    started_at: str
    finished_at: str
    duration_seconds: float


# ── pure cadence logic: in-house cron parsing, no croniter ──────────────


def _step_value(field: str) -> int | None:
    if not field.startswith("*/"):
        return None
    remainder = field[2:]
    if not remainder.isdigit():
        return None
    value = int(remainder)
    return value if value > 0 else None


def _is_fixed(field: str) -> bool:
    return field.isdigit()


def _minute_step_interval(minute: str, hour: str, dow: str) -> float | None:
    step = _step_value(minute)
    if step is None or hour != "*" or dow != "*":
        return None
    return step * _SECONDS_PER_MINUTE


def _hour_step_interval(minute: str, hour: str, dow: str) -> float | None:
    step = _step_value(hour)
    if step is None or not _is_fixed(minute) or dow != "*":
        return None
    return step * _MINUTES_PER_HOUR * _SECONDS_PER_MINUTE


def _fixed_time_interval(minute: str, hour: str, dow: str) -> float | None:
    if not _is_fixed(minute) or not _is_fixed(hour):
        return None
    if dow == "*":
        return _HOURS_PER_DAY * _MINUTES_PER_HOUR * _SECONDS_PER_MINUTE
    if _is_fixed(dow):
        return _DAYS_PER_WEEK * _HOURS_PER_DAY * _MINUTES_PER_HOUR * _SECONDS_PER_MINUTE
    return None


def cron_interval_seconds(expression: str) -> float | None:
    fields = expression.split()
    if len(fields) != _CRON_FIELD_COUNT:
        return None
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*":
        return None
    return (
        _minute_step_interval(minute, hour, dow)
        or _hour_step_interval(minute, hour, dow)
        or _fixed_time_interval(minute, hour, dow)
    )


# ── pure overdue logic ───────────────────────────────────────────────────


def _parse_aware(value: str) -> datetime | None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def compute_overdue(cadence: Cadence, last_run_at: str | None, *, now: datetime) -> Overdue:
    if cadence.kind in ("manual", "rides_on"):
        return Overdue(
            state="unknown",
            detail=f"cadence kind {cadence.kind!r} has no fixed interval to compare against",
        )
    if cadence.interval_seconds is None:
        return Overdue(state="unknown", detail="cadence could not be parsed into an interval")
    if last_run_at is None:
        return Overdue(state="unknown", detail="no run has ever been observed")
    last_dt = _parse_aware(last_run_at)
    if last_dt is None:
        return Overdue(state="unknown", detail="last-run timestamp could not be parsed")
    age_seconds = (now - last_dt).total_seconds()
    threshold = cadence.interval_seconds * _OVERDUE_GRACE_MULTIPLIER
    state: OverdueState = "overdue" if age_seconds > threshold else "on_time"
    return Overdue(
        state=state,
        detail=f"last ran {age_seconds:.0f}s ago; expected every {cadence.interval_seconds:.0f}s",
    )


def _finalize_overdue(process: RecurringProcess, *, now: datetime) -> RecurringProcess:
    sub_steps = tuple(_finalize_overdue(s, now=now) for s in process.sub_steps)
    if process.cadence.kind == "rides_on":
        return process.model_copy(update={"sub_steps": sub_steps})
    overdue = compute_overdue(process.cadence, process.last_run.at, now=now)
    return process.model_copy(update={"overdue": overdue, "sub_steps": sub_steps})


def _forge_run_state(
    status: str | None, conclusion: str | None, *, confirmed_absent: bool
) -> RunState:
    if status is None:
        return "never_ran" if confirmed_absent else "unknown"
    if status == "in_progress":
        return "in_progress"
    if status in _QUEUED_STATUSES:
        return "queued"
    if status == "completed":
        return _CONCLUSION_STATE.get(conclusion or "", "unknown")
    return "unknown"


def _sweep_run_state(outcome: str | None) -> RunState:
    return _SWEEP_STATE.get(outcome or "", "unknown")


# ── transform: recurring_forge's raw fetch results -> RecurringProcess ──


_DEPENDABOT_INTERVAL_SECONDS: dict[str, float] = {
    "daily": 86400.0, "weekly": 604800.0, "monthly": 2629800.0,
}


def _placeholder_overdue() -> Overdue:
    return Overdue(state="unknown", detail="not yet computed")


_PLACEHOLDER_CADENCE_KIND: dict[ProcessFamily, CadenceKind] = {
    "scheduled_ci": "cron",
    "dependency_update": "interval_seconds",
}


def _placeholder_process(
    process_id: str, family: ProcessFamily, label: str, provenance: Provenance
) -> RecurringProcess:
    return RecurringProcess(
        process_id=process_id,
        family=family,
        label=label,
        cadence=Cadence(kind=_PLACEHOLDER_CADENCE_KIND[family], detail=provenance.detail),
        last_run=LastRun(provenance=provenance),
        duration_state="unknown",
        overdue=_placeholder_overdue(),
    )


def _tightest_cron(entries: tuple[str, ...]) -> tuple[float | None, str | None]:
    best: tuple[float, str] | None = None
    for entry in entries:
        seconds = cron_interval_seconds(entry)
        if seconds is not None and (best is None or seconds < best[0]):
            best = (seconds, entry)
    if best is not None:
        return best
    return None, (entries[0] if entries else None)


def _process_from_workflow(wf: recurring_forge.WorkflowSchedule) -> RecurringProcess:
    if wf.manual:
        cadence = Cadence(
            kind="manual", detail="triggered by hand only (workflow_dispatch, no schedule)"
        )
    else:
        interval, chosen = _tightest_cron(wf.cron_entries)
        cadence = Cadence(
            kind="cron",
            interval_seconds=interval,
            cron_expression=chosen,
            detail=(
                "parsed from this repo's own workflow configuration"
                if interval is not None
                else "cron expression could not be parsed"
            ),
        )
    return RecurringProcess(
        process_id=f"scheduled_ci:{wf.workflow_id}",
        family="scheduled_ci",
        label=wf.name,
        cadence=cadence,
        last_run=LastRun(
            provenance=wf.last_run_provenance,
            at=wf.last_run_at,
            state=_forge_run_state(
                wf.last_run_status,
                wf.last_run_conclusion,
                confirmed_absent=wf.last_run_confirmed_absent,
            ),
            outcome=wf.last_run_outcome,
            url=wf.last_run_url,
        ),
        duration_state="measured" if wf.last_run_duration_seconds is not None else "unknown",
        duration_seconds=wf.last_run_duration_seconds,
        overdue=_placeholder_overdue(),
    )


def _process_from_dependency_update(
    du: recurring_forge.DependencyEcosystemUpdate,
) -> RecurringProcess:
    interval = _DEPENDABOT_INTERVAL_SECONDS.get(du.schedule_interval or "")
    detail = (
        f"dependabot.yml schedule.interval={du.schedule_interval!r}"
        if du.schedule_interval
        else "dependabot.yml did not declare a schedule.interval for this ecosystem"
    )
    return RecurringProcess(
        process_id=f"dependency_update:{du.ecosystem}",
        family="dependency_update",
        label=f"Dependabot updates — {du.ecosystem}",
        cadence=Cadence(kind="interval_seconds", interval_seconds=interval, detail=detail),
        last_run=LastRun(
            provenance=du.last_pr_provenance, at=du.last_pr_at, state="unknown", url=du.last_pr_url
        ),
        duration_state="not_applicable",
        overdue=_placeholder_overdue(),
    )


def _processes_from_forge(status: recurring_forge.RecurringForgeStatus) -> list[RecurringProcess]:
    processes: list[RecurringProcess] = []
    if status.workflows:
        processes.extend(_process_from_workflow(wf) for wf in status.workflows)
    elif status.workflows_provenance.status == "unknown":
        processes.append(
            _placeholder_process(
                "scheduled_ci",
                "scheduled_ci",
                "Scheduled CI workflows",
                status.workflows_provenance,
            )
        )
    if status.dependency_updates:
        processes.extend(_process_from_dependency_update(du) for du in status.dependency_updates)
    elif status.dependency_updates_provenance.status == "unknown":
        processes.append(
            _placeholder_process(
                "dependency_update",
                "dependency_update",
                "Automated dependency updates",
                status.dependency_updates_provenance,
            )
        )
    return processes


# ── alan's own recurring sweeps ──────────────────────────────────────────


def _digest_process(*, queue: Queue, now: datetime) -> RecurringProcess:
    digest_last_date = queue.get_meta(DIGEST_LAST_DATE_META_KEY)
    deadline = resolve_digest_deadline(now)
    overdue = Overdue(
        state="overdue" if digest_overdue(digest_last_date, now, deadline) else "on_time",
        detail=f"daily deadline is {deadline.isoformat()}",
    )
    latest = load_latest_digest(queue)
    if latest is None:
        last_run = LastRun(provenance=unknown_provenance("no digest has been generated yet"))
    else:
        last_run = LastRun(
            provenance=Provenance(status="fresh", detail="most recently generated digest"),
            at=latest.generated_at,
        )
    return RecurringProcess(
        process_id="daily_digest",
        family="system_sweep",
        label="Daily digest",
        cadence=Cadence(
            kind="rides_on",
            rides_on="triage_sweep",
            detail="generated once a day by the triage sweep that runs past the daily deadline",
        ),
        last_run=last_run,
        duration_state="not_applicable",
        overdue=overdue,
    )


def _triage_process(*, queue: Queue, now: datetime) -> RecurringProcess:
    status = load_triage_sweep_status()
    cadence = Cadence(
        kind="interval_seconds",
        interval_seconds=SWEEP_CADENCE_SECONDS,
        detail="fixed 30-minute sweep cadence",
    )
    if status is None:
        last_run = LastRun(provenance=unknown_provenance("no triage sweep has completed yet"))
        duration_state: DurationState = "unknown"
        duration_seconds: float | None = None
    else:
        last_run = LastRun(
            provenance=Provenance(status="fresh", detail="tailed from triage_sweep_status.json"),
            at=status.computed_at,
            state=_sweep_run_state(status.outcome),
            outcome=status.outcome,
        )
        duration_state = "measured"
        duration_seconds = status.duration_seconds
    return RecurringProcess(
        process_id="triage_sweep",
        family="system_sweep",
        label="Failure triage sweep",
        cadence=cadence,
        last_run=last_run,
        duration_state=duration_state,
        duration_seconds=duration_seconds,
        overdue=_placeholder_overdue(),
        sub_steps=(_digest_process(queue=queue, now=now),),
    )


def _dependency_sweep_process(cfg: EngineConfig, forge: ForgeTarget) -> RecurringProcess:
    cadence = Cadence(
        kind="interval_seconds",
        interval_seconds=float(cfg.dependency_sweep.cadence_seconds),
        detail="this engine's configured dependency-sweep cadence",
    )
    if not cfg.dependency_sweep.enabled:
        return RecurringProcess(
            process_id="dependency_sweep",
            family="system_sweep",
            label=_DEPENDENCY_SWEEP_LABEL,
            cadence=cadence,
            last_run=LastRun(
                provenance=unknown_provenance("dependency sweep is disabled for this engine")
            ),
            duration_state="unknown",
            overdue=_placeholder_overdue(),
        )
    status = load_dependency_sweep_status(forge)
    if status is None or status.last_run_at is None:
        return RecurringProcess(
            process_id="dependency_sweep",
            family="system_sweep",
            label=_DEPENDENCY_SWEEP_LABEL,
            cadence=cadence,
            last_run=LastRun(
                provenance=unknown_provenance("no dependency sweep has completed yet")
            ),
            duration_state="unknown",
            overdue=_placeholder_overdue(),
        )
    outcome = status.last_outcome
    last_run = LastRun(
        provenance=Provenance(status="fresh", detail="tailed from dep_sweep_status.json"),
        at=status.last_run_at,
        state=_sweep_run_state(outcome),
        outcome=outcome,
    )
    duration_state: DurationState = (
        "measured" if status.last_duration_seconds is not None else "unknown"
    )
    return RecurringProcess(
        process_id="dependency_sweep",
        family="system_sweep",
        label=_DEPENDENCY_SWEEP_LABEL,
        cadence=cadence,
        last_run=last_run,
        duration_state=duration_state,
        duration_seconds=status.last_duration_seconds,
        overdue=_placeholder_overdue(),
    )


# ── backup: job status + per-repository coverage statement ──────────────


def _load_backup_receipt() -> _BackupReceipt | None:
    try:
        raw = (state_root() / _BACKUP_RECEIPT_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return _BackupReceipt.model_validate_json(raw)
    except ValidationError:
        return None


def _backup_job_process() -> RecurringProcess:
    cadence = Cadence(
        kind="interval_seconds",
        interval_seconds=_BACKUP_JOB_CADENCE_SECONDS,
        detail="four times a day",
    )
    receipt = _load_backup_receipt()
    if receipt is None:
        return RecurringProcess(
            process_id="backup_job",
            family="backup",
            label="State backup",
            cadence=cadence,
            last_run=LastRun(provenance=unknown_provenance("no backup receipt found yet")),
            duration_state="unknown",
            overdue=_placeholder_overdue(),
        )
    last_run = LastRun(
        provenance=Provenance(status="fresh", detail="tailed from backup_receipt.json"),
        at=receipt.finished_at,
        state=_sweep_run_state(receipt.outcome),
        outcome=receipt.outcome,
    )
    return RecurringProcess(
        process_id="backup_job",
        family="backup",
        label="State backup",
        cadence=cadence,
        last_run=last_run,
        duration_state="measured",
        duration_seconds=receipt.duration_seconds,
        overdue=_placeholder_overdue(),
    )


def _backup_coverage(identity: RepoIdentity, *, workspace_index: WorkspaceIndex) -> BackupCoverage:
    receipt = _load_backup_receipt()
    if receipt is None:
        return BackupCoverage(
            provenance=unknown_provenance("no backup receipt found yet"),
            excluded_caveat=_BACKUP_EXCLUSION_CAVEAT,
        )
    if receipt.outcome != "success":
        return BackupCoverage(
            provenance=unknown_provenance(
                f"most recent backup run did not succeed (outcome={receipt.outcome!r})"
            ),
            included_in_last_snapshot=False,
            excluded_caveat=_BACKUP_EXCLUSION_CAVEAT,
        )
    finished_at = _parse_aware(receipt.finished_at)
    changed_since_count: int | None = None
    if finished_at is not None:
        rows, archived, project_ids = _scoped_rows(identity, workspace_index)
        target = engine_config.canonical_scope_key(identity.slug)
        changed_since_count = sum(
            1
            for row in (*rows, *archived)
            if delivery_stats_mod.in_repo_scope(row, identity.slug, project_ids, target=target)
            and row.updated_at > finished_at
        )
    return BackupCoverage(
        provenance=Provenance(status="fresh", detail="most recent backup_receipt.json"),
        included_in_last_snapshot=True,
        changed_since_count=changed_since_count,
        excluded_caveat=_BACKUP_EXCLUSION_CAVEAT,
    )


# ── assembly ──────────────────────────────────────────────────────────────


def get_recurring_status(
    identity: RepoIdentity, *, queue: Queue, workspace_index: WorkspaceIndex, pubsub: PubSub
) -> RecurringStatus | None:
    resolved = _resolve_engine(identity)
    if resolved is None:
        return None
    cfg, is_default = resolved
    now = datetime.now(UTC)
    processes: list[RecurringProcess] = []
    if cfg.forge is not None:
        forge = forge_target_for_workspace(identity.slug, cfg)
        forge_data = recurring_forge.forge_status(forge, pubsub=pubsub)
        if forge_data is not None:
            processes.extend(_processes_from_forge(forge_data))
        else:
            refreshing = Provenance(
                status="refreshing", detail="no cached data yet — refresh in progress"
            )
            processes.append(
                _placeholder_process(
                    "scheduled_ci", "scheduled_ci", "Scheduled CI workflows", refreshing
                )
            )
            processes.append(
                _placeholder_process(
                    "dependency_update",
                    "dependency_update",
                    "Automated dependency updates",
                    refreshing,
                )
            )
        processes.append(_dependency_sweep_process(cfg, forge))
    service_scope_note: str | None = None
    if is_default:
        processes.append(_triage_process(queue=queue, now=now))
        processes.append(_backup_job_process())
    else:
        service_scope_note = _SERVICE_SCOPE_NOTE
    finalized = [_finalize_overdue(p, now=now) for p in processes]
    return RecurringStatus(
        slug=identity.slug,
        is_default=is_default,
        processes=finalized,
        service_scope_note=service_scope_note,
        backup_coverage=_backup_coverage(identity, workspace_index=workspace_index),
    )
