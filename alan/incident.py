from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from alan import store
from alan.handover import Handover
from alan.health import HealthFinding, HealthState
from alan.park import ParkReason
from alan.teams.triage_pregate import _AUDIT_TAIL_BYTES  # pyright: ignore[reportPrivateUsage]
from alan.terminal_outcome import resolve_terminal_outcome
from alan.workspace import Status, Workspace


class IncidentClass(StrEnum):
    PARK_CRASH = "park_crash"
    PARK_ROUTING_ERROR = "park_routing_error"
    PARK_STALL = "park_stall"
    PARK_FROZEN_CHAIN = "park_frozen_chain"
    PARK_CREDENTIAL = "park_credential"
    MERGE_BLOCK = "merge_block"
    ESCALATION = "escalation"
    TERMINAL_FAILED = "terminal_failed"
    TERMINAL_PARTIAL = "terminal_partial"
    HEALTH_DEGRADED = "health_degraded"
    JOB_FAILURE = "job_failure"
    SELF_HEAL = "self_heal"
    MERGE_GATE = "merge_gate"
    RESIDUE = "residue"
    DIGEST_OVERDUE = "digest_overdue"


class Disposition(StrEnum):
    ROOT_CAUSED = "root_caused"
    MECHANICALLY_RECOVERED = "mechanically_recovered"
    KNOWN_FLAKE = "known_flake"
    TRANSIENT_EXTERNAL = "transient_external"
    BY_DESIGN = "by_design"
    DUPLICATE_OF = "duplicate_of"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True)
class Suppression:
    reason: str
    ttl_epoch: float
    recurrence_threshold: int


@dataclass
class Incident:
    fingerprint: str
    incident_class: IncidentClass
    scope_ref: str
    workspace_id: str | None
    pr_number: int | None
    title: str
    evidence: dict[str, Any]
    last_seen: str
    engine_key: str | None = None


@dataclass(frozen=True)
class AuditRow:
    incident_class: IncidentClass
    raw: dict[str, Any]


@dataclass(frozen=True)
class _WsContext:
    ws: Workspace
    handovers: list[Handover]


@dataclass(frozen=True)
class IncidentSource:
    name: str
    predicate: Callable[[_WsContext], bool]
    incident_class: IncidentClass
    fingerprint_fn: Callable[[_WsContext], str]
    evidence_fn: Callable[[_WsContext], dict[str, Any]]


def incident_fingerprint(
    incident_class: IncidentClass | str, scope_ref: str, block_class: str | None
) -> str:
    class_value = (
        incident_class.value if isinstance(incident_class, IncidentClass) else incident_class
    )
    raw = "|".join([class_value, scope_ref, block_class or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _pr_number_for(ctx: _WsContext) -> int | None:
    for ho in reversed(ctx.handovers):
        if ho.pr_number is not None:
            return ho.pr_number
    return None


def _scope_ref_for_ws(ctx: _WsContext) -> str:
    pr = _pr_number_for(ctx)
    return f"pr:{pr}" if pr is not None else f"ws:{ctx.ws.task_id}"


def _ws_and_pr(ctx: _WsContext) -> tuple[str | None, int | None]:
    pr = _pr_number_for(ctx)
    workspace_id = None if pr is not None else ctx.ws.task_id
    return workspace_id, pr


def _block_class_none(_ctx: _WsContext) -> str | None:
    return None


def _block_class_from_meta(ctx: _WsContext) -> str | None:
    return ctx.ws.meta.merge_block_class


def _fingerprint_for(
    incident_class: IncidentClass, block_class_fn: Callable[[_WsContext], str | None]
) -> Callable[[_WsContext], str]:
    def fn(ctx: _WsContext) -> str:
        return incident_fingerprint(incident_class, _scope_ref_for_ws(ctx), block_class_fn(ctx))

    return fn


def _base_evidence(ctx: _WsContext) -> dict[str, Any]:
    return {
        "status": ctx.ws.meta.status.value,
        "team": ctx.ws.meta.team,
        "note": ctx.ws.meta.note,
        "blockers": [b.model_dump(mode="json") for b in ctx.ws.meta.blockers],
        "merge_block_class": ctx.ws.meta.merge_block_class,
    }


def _merge_block_evidence(ctx: _WsContext) -> dict[str, Any]:
    blockers = ctx.ws.meta.blockers
    instruction = next((b.instruction for b in blockers if b.instruction), None)
    action_url = next((b.action_url for b in blockers if b.action_url), None)
    return {
        **_base_evidence(ctx),
        "instruction": instruction,
        "action_url": action_url,
    }


def _terminal_evidence(ctx: _WsContext) -> dict[str, Any]:
    outcome = resolve_terminal_outcome(ctx.ws, ctx.handovers, [])
    return {
        **_base_evidence(ctx),
        "outcome_reason": outcome.reason if outcome is not None else None,
    }


def _is_current_park(ctx: _WsContext, reason: ParkReason) -> bool:
    return ctx.ws.meta.status == Status.AWAITING_INPUT and ctx.ws.meta.park_reason == reason


def _make_park_source(
    name: str, reason: ParkReason, incident_class: IncidentClass
) -> IncidentSource:
    def predicate(ctx: _WsContext) -> bool:
        return _is_current_park(ctx, reason)

    return IncidentSource(
        name=name,
        predicate=predicate,
        incident_class=incident_class,
        fingerprint_fn=_fingerprint_for(incident_class, _block_class_none),
        evidence_fn=_base_evidence,
    )


_SOURCES: tuple[IncidentSource, ...] = (
    _make_park_source("park_crash", ParkReason.CRASH, IncidentClass.PARK_CRASH),
    _make_park_source(
        "park_routing_error", ParkReason.ROUTING_ERROR, IncidentClass.PARK_ROUTING_ERROR
    ),
    _make_park_source("park_stall", ParkReason.STALL, IncidentClass.PARK_STALL),
    _make_park_source(
        "park_credential", ParkReason.CREDENTIAL, IncidentClass.PARK_CREDENTIAL
    ),
    IncidentSource(
        name="park_frozen_chain",
        predicate=lambda ctx: _is_current_park(ctx, ParkReason.FROZEN_CHAIN),
        incident_class=IncidentClass.PARK_FROZEN_CHAIN,
        fingerprint_fn=_fingerprint_for(IncidentClass.PARK_FROZEN_CHAIN, _block_class_none),
        evidence_fn=_merge_block_evidence,
    ),
    IncidentSource(
        name="merge_block",
        predicate=lambda ctx: _is_current_park(ctx, ParkReason.MERGE_BLOCK),
        incident_class=IncidentClass.MERGE_BLOCK,
        fingerprint_fn=_fingerprint_for(IncidentClass.MERGE_BLOCK, _block_class_from_meta),
        evidence_fn=_merge_block_evidence,
    ),
    IncidentSource(
        name="escalation",
        predicate=lambda ctx: ctx.ws.meta.status == Status.ESCALATED,
        incident_class=IncidentClass.ESCALATION,
        fingerprint_fn=_fingerprint_for(IncidentClass.ESCALATION, _block_class_from_meta),
        evidence_fn=_base_evidence,
    ),
    IncidentSource(
        name="terminal_failed",
        predicate=lambda ctx: ctx.ws.meta.status == Status.FAILED,
        incident_class=IncidentClass.TERMINAL_FAILED,
        fingerprint_fn=_fingerprint_for(IncidentClass.TERMINAL_FAILED, _block_class_from_meta),
        evidence_fn=_terminal_evidence,
    ),
    IncidentSource(
        name="terminal_partial",
        predicate=lambda ctx: ctx.ws.meta.status == Status.PARTIAL,
        incident_class=IncidentClass.TERMINAL_PARTIAL,
        fingerprint_fn=_fingerprint_for(IncidentClass.TERMINAL_PARTIAL, _block_class_from_meta),
        evidence_fn=_terminal_evidence,
    ),
)

STUCK_WS_STATUSES: frozenset[Status] = frozenset(
    {Status.AWAITING_INPUT, Status.ESCALATED, Status.FAILED, Status.PARTIAL}
)


def stuck_workspace_keys(pairs: Iterable[tuple[str, Status]]) -> frozenset[str]:
    return frozenset(key for key, status in pairs if status in STUCK_WS_STATUSES)

_EXEMPT_PARK_REASONS: frozenset[ParkReason] = frozenset(
    {ParkReason.QUESTION, ParkReason.UNARCHIVED}
)

DESIGN_FAMILY_TEAMS: frozenset[str] = frozenset(
    {"architecture", "brainstorming", "opportunity_scout", "research"}
)


def _is_awaiting_harvest(ctx: _WsContext) -> bool:
    if ctx.ws.meta.status != Status.AWAITING_INPUT:
        return False
    if ctx.ws.meta.team not in DESIGN_FAMILY_TEAMS:
        return False
    if not ctx.handovers:
        return True
    return not ctx.handovers[-1].to_team_suggestions


def _is_exempt(ctx: _WsContext) -> bool:
    if (
        ctx.ws.meta.status == Status.AWAITING_INPUT
        and ctx.ws.meta.park_reason in _EXEMPT_PARK_REASONS
    ):
        return True
    return _is_awaiting_harvest(ctx)


def _residue_incident(ctx: _WsContext) -> Incident:
    scope = _scope_ref_for_ws(ctx)
    workspace_id, pr = _ws_and_pr(ctx)
    return Incident(
        fingerprint=incident_fingerprint(IncidentClass.RESIDUE, scope, None),
        incident_class=IncidentClass.RESIDUE,
        scope_ref=scope,
        workspace_id=workspace_id,
        pr_number=pr,
        title=f"Unclassified anomaly: {ctx.ws.task_id} (status={ctx.ws.meta.status.value})",
        evidence=_base_evidence(ctx),
        last_seen=ctx.ws.meta.updated_at.isoformat(),
        engine_key=ctx.ws.meta.engine_key,
    )


def _derive_from_workspace(ctx: _WsContext) -> Incident | None:
    if ctx.ws.meta.status not in STUCK_WS_STATUSES:
        return None
    if _is_exempt(ctx):
        return None
    for source in _SOURCES:
        if source.predicate(ctx):
            workspace_id, pr = _ws_and_pr(ctx)
            return Incident(
                fingerprint=source.fingerprint_fn(ctx),
                incident_class=source.incident_class,
                scope_ref=_scope_ref_for_ws(ctx),
                workspace_id=workspace_id,
                pr_number=pr,
                title=f"{source.name.replace('_', ' ').title()}: {ctx.ws.task_id}",
                evidence=source.evidence_fn(ctx),
                last_seen=ctx.ws.meta.updated_at.isoformat(),
                engine_key=ctx.ws.meta.engine_key,
            )
    return _residue_incident(ctx)


def _health_scope_ref(finding: HealthFinding) -> str:
    if finding.pr_number is not None:
        return f"pr:{finding.pr_number}"
    if finding.workspace_id is not None:
        return f"ws:{finding.workspace_id}"
    return f"health:{finding.finding_id}"


def _incident_from_health_finding(finding: HealthFinding, computed_at: str) -> Incident:
    scope = _health_scope_ref(finding)
    return Incident(
        fingerprint=incident_fingerprint(IncidentClass.HEALTH_DEGRADED, scope, None),
        incident_class=IncidentClass.HEALTH_DEGRADED,
        scope_ref=scope,
        workspace_id=finding.workspace_id,
        pr_number=finding.pr_number,
        title=f"Health degraded: {finding.description}",
        evidence={
            "finding_id": finding.finding_id,
            "kind": finding.kind,
            "severity": finding.severity,
            "blocker_flags": finding.blocker_flags,
            "action": finding.action,
            "in_flight": finding.in_flight,
        },
        last_seen=computed_at,
        engine_key=finding.engine_key,
    )


def _incidents_from_health(health_state: HealthState | None) -> list[Incident]:
    if health_state is None:
        return []
    return [
        _incident_from_health_finding(f, health_state.computed_at) for f in health_state.findings
    ]


_DIGEST_OVERDUE_SCOPE = "system:digest"


def digest_overdue(digest_last_date: str | None, now: datetime, deadline: datetime) -> bool:
    if now < deadline:
        return False
    return digest_last_date != deadline.date().isoformat()


def _incidents_from_digest_overdue(
    digest_last_date: str | None, now: datetime | None, digest_deadline: datetime | None
) -> list[Incident]:
    if now is None or digest_deadline is None:
        return []
    if not digest_overdue(digest_last_date, now, digest_deadline):
        return []
    return [
        Incident(
            fingerprint=incident_fingerprint(
                IncidentClass.DIGEST_OVERDUE, _DIGEST_OVERDUE_SCOPE, None
            ),
            incident_class=IncidentClass.DIGEST_OVERDUE,
            scope_ref=_DIGEST_OVERDUE_SCOPE,
            workspace_id=None,
            pr_number=None,
            title="Daily digest overdue",
            evidence={
                "digest_last_date": digest_last_date,
                "deadline": digest_deadline.isoformat(),
            },
            last_seen=now.isoformat(),
        )
    ]


def _opt_str(d: dict[str, Any], key: str) -> str | None:
    val: Any = d.get(key)
    return str(val) if val is not None else None


def _opt_int(d: dict[str, Any], key: str) -> int | None:
    val: Any = d.get(key)
    return val if isinstance(val, int) else None


def _audit_row_scope(
    incident_class: IncidentClass, pr_number: int | None, workspace_id: str | None
) -> str:
    if pr_number is not None:
        return f"pr:{pr_number}"
    if workspace_id is not None:
        return f"ws:{workspace_id}"
    return f"audit:{incident_class.value}"


def _incident_from_audit_row(
    row: AuditRow, engine_key_by_workspace: dict[str, str | None] | None = None
) -> Incident:
    raw = row.raw
    workspace_id = _opt_str(raw, "workspace_id") or _opt_str(raw, "task_id")
    pr_number = _opt_int(raw, "pr_number")
    scope = _audit_row_scope(row.incident_class, pr_number, workspace_id)
    block_class = _opt_str(raw, "block_class")
    last_seen = _opt_str(raw, "timestamp_utc") or ""
    title = f"{row.incident_class.value.replace('_', ' ').title()}: {workspace_id or scope}"
    engine_key = (
        (engine_key_by_workspace or {}).get(workspace_id) if workspace_id is not None else None
    )
    return Incident(
        fingerprint=incident_fingerprint(row.incident_class, scope, block_class),
        incident_class=row.incident_class,
        scope_ref=scope,
        workspace_id=workspace_id,
        pr_number=pr_number,
        title=title,
        evidence=dict(raw),
        last_seen=last_seen,
        engine_key=engine_key,
    )


_AUDIT_SOURCE_FILES: dict[IncidentClass, str] = {
    IncidentClass.JOB_FAILURE: "job_failure_audit.jsonl",
    IncidentClass.SELF_HEAL: "self_heal_audit.jsonl",
    IncidentClass.MERGE_GATE: "merge_gate_audit.jsonl",
}

_AUDIT_INCIDENT_MAX_AGE_DAYS = 14


def _rows_from_file(path: Path, incident_class: IncidentClass) -> list[AuditRow]:
    try:
        text = store.read_tail_text(path, _AUDIT_TAIL_BYTES)
    except OSError:
        return []
    rows: list[AuditRow] = []
    for line in text.splitlines():
        parsed = store.parse_jsonl_row(line)
        if parsed is not None:
            rows.append(AuditRow(incident_class=incident_class, raw=parsed))
    return rows


_AUDIT_ROWS_CACHE: dict[tuple[Path, IncidentClass], tuple[int, int, list[AuditRow]]] = {}


def _rows_from_file_cached(path: Path, incident_class: IncidentClass) -> list[AuditRow]:
    try:
        st = path.stat()
    except OSError:
        return []
    key = (path, incident_class)
    hit = store.stat_cache_hit(_AUDIT_ROWS_CACHE, key, st)
    if hit is not None:
        return hit[0]
    rows = _rows_from_file(path, incident_class)
    store.stat_cache_put(_AUDIT_ROWS_CACHE, key, st, rows)
    return rows


def gather_audit_rows(audit_dir: Path) -> list[AuditRow]:
    rows: list[AuditRow] = []
    for incident_class, filename in _AUDIT_SOURCE_FILES.items():
        rows.extend(_rows_from_file_cached(audit_dir / filename, incident_class))
    return rows


def _within_audit_window(row: AuditRow, cutoff_iso: str) -> bool:
    ts = row.raw.get("timestamp_utc")
    if not isinstance(ts, str) or not ts:
        return True
    return ts >= cutoff_iso


def _audit_rows_in_window(rows: list[AuditRow], now: datetime | None) -> list[AuditRow]:
    if now is None:
        return rows
    cutoff_iso = (now - timedelta(days=_AUDIT_INCIDENT_MAX_AGE_DAYS)).isoformat()
    return [row for row in rows if _within_audit_window(row, cutoff_iso)]


def dedupe_by_fingerprint(incidents: list[Incident]) -> list[Incident]:
    survivors: dict[str, Incident] = {}
    for incident in incidents:
        current = survivors.get(incident.fingerprint)
        if current is None or incident.last_seen > current.last_seen:
            survivors[incident.fingerprint] = incident
    return list(survivors.values())


def derive_incidents(
    *,
    workspaces: list[Workspace],
    handovers_by_workspace: dict[str, list[Handover]] | None = None,
    health_state: HealthState | None = None,
    audit_rows: list[AuditRow] | None = None,
    digest_last_date: str | None = None,
    now: datetime | None = None,
    digest_deadline: datetime | None = None,
    engine_key_by_workspace: dict[str, str | None] | None = None,
) -> list[Incident]:
    handovers_map = handovers_by_workspace or {}
    incidents: list[Incident] = []
    for ws in workspaces:
        ctx = _WsContext(ws=ws, handovers=handovers_map.get(ws.task_id, []))
        incident = _derive_from_workspace(ctx)
        if incident is not None:
            incidents.append(incident)
    incidents.extend(_incidents_from_health(health_state))
    incidents.extend(
        _incident_from_audit_row(row, engine_key_by_workspace)
        for row in _audit_rows_in_window(audit_rows or [], now)
    )
    incidents.extend(_incidents_from_digest_overdue(digest_last_date, now, digest_deadline))
    return dedupe_by_fingerprint(incidents)


__all__ = [
    "AuditRow",
    "DESIGN_FAMILY_TEAMS",
    "Disposition",
    "Incident",
    "IncidentClass",
    "IncidentSource",
    "STUCK_WS_STATUSES",
    "Suppression",
    "dedupe_by_fingerprint",
    "derive_incidents",
    "digest_overdue",
    "gather_audit_rows",
    "incident_fingerprint",
    "stuck_workspace_keys",
]
