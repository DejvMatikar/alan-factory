from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError, computed_field

import alan.fleet_timing as fleet_timing_mod
import alan.prep_chat as prep_chat_mod
import alan.workspace as workspace_mod
import alan.workspace_cost as workspace_cost_mod
from alan._reconciler_never_started import team_supports_resume
from alan.acceptance import unproven_clause_ids as _unproven_clause_ids
from alan.deploy_verify import GateRecord, waited_s
from alan.handover import Handover
from alan.incident import STUCK_WS_STATUSES
from alan.manager_summary import (
    read_manager_summary,
    read_manager_summary_usage,
    read_manager_summary_written_at,
)
from alan.park import Blocker, ParkReason
from alan.queue import Job, JobStatus, Queue
from alan.run_summary import RunSummary
from alan.scope_audit import ScopeGapFinding
from alan.terminal_outcome import TerminalOutcome, resolve_terminal_outcome
from alan.unblock_ledger import UnblockRecord
from alan.workspace import Requirement, Status, Task
from alan.workspace_index import WorkspaceRow

logger = logging.getLogger("alan.server")

_TERMINAL_OUTCOME_STATUSES = {Status.ESCALATED, Status.FAILED, Status.PARTIAL}
_DEPLOY_VERIFY_TEAM = "deploy_verify"

PendingGatePath = Literal["awaiting_restart", "awaiting_live"]
_PENDING_GATE_PATHS: frozenset[str] = frozenset({"awaiting_restart", "awaiting_live"})


class PendingDeployGate(BaseModel):
    path: PendingGatePath
    deadline: float | None
    race_deadline: float | None
    merged_at: float | None
    waited_s: float


class ManagerSummary(BaseModel):
    ok: bool
    text: str | None = None
    run_summary: RunSummary | None = None
    written_at: datetime | None = None
    stale: bool = False


class WorkspaceDetail(BaseModel):
    task_id: str
    title: str
    description: str
    status: Status
    revisit_counts: dict[str, int]
    prep_chat: prep_chat_mod.PrepChatRef | None = None
    created_at: datetime
    updated_at: datetime
    history: list[dict[str, Any]]
    latest_handover: Handover | None
    has_running_job: bool = False
    has_resumable_failed_job: bool = False
    requeue_count: int = 0
    tasks: list[Task] = Field(default_factory=list[Task])
    scope_audit: list[ScopeGapFinding] = Field(default_factory=list[ScopeGapFinding])
    requirements: list[Requirement] | None = None
    assumptions: list[str] | None = None
    note: str | None = None
    manager_summary: ManagerSummary | None = None
    terminal_outcome: TerminalOutcome | None = None
    park_reason: ParkReason | None = None
    blockers: list[Blocker] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    team: str | None = None
    project_id: str | None = None
    engine_key: str | None = None
    total_cost_usd: float | None = None
    cost_breakdown: list[workspace_cost_mod.WorkspaceCostBreakdownEntry] = Field(
        default_factory=list[workspace_cost_mod.WorkspaceCostBreakdownEntry]
    )
    active_seconds: float | None = None
    lead_seconds: float | None = None
    pending_deploy_gate: PendingDeployGate | None = None
    live_at: datetime | None = None
    unblock: UnblockRecord | None = None
    pr_number: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def operator_link(self) -> bool:
        return self.status in STUCK_WS_STATUSES

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unproven_clause_ids(self) -> list[str]:
        return sorted(_unproven_clause_ids(self.requirements, self.tasks))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unproven_clause_count(self) -> int:
        return len(_unproven_clause_ids(self.requirements, self.tasks))


async def fetch_terminal_outcome(
    ws: workspace_mod.Workspace,
    handovers: list[Handover],
    queue: Queue,
) -> TerminalOutcome | None:
    if ws.meta.status not in _TERMINAL_OUTCOME_STATUSES:
        return None
    try:
        failed_jobs: list[Job] = []
        if ws.meta.status in (Status.FAILED, Status.PARTIAL):
            failed_jobs = await run_in_threadpool(
                queue.list_jobs_for_workspace, ws.task_id, status=JobStatus.FAILED
            )
        return await run_in_threadpool(resolve_terminal_outcome, ws, handovers, failed_jobs)
    except Exception:  # noqa: BLE001
        logger.warning("terminal_outcome: derivation failed for %s", ws.task_id, exc_info=True)
        return None


def detail_timing(
    ws: workspace_mod.Workspace, row: WorkspaceRow | None
) -> tuple[float | None, float | None]:
    active_seconds = row.live_active_seconds(datetime.now(UTC)) if row is not None else None
    lead = fleet_timing_mod.lead_seconds(
        created_at=ws.meta.created_at, delivered_at=ws.meta.delivered_at
    )
    return active_seconds, lead


def has_resumable_failed_job(queue: Queue, task_id: str) -> bool:
    jobs = queue.list_jobs_for_workspace(task_id, status=JobStatus.FAILED)
    if not jobs:
        return False
    latest = jobs[-1]
    return bool(latest.resume_inv_id) and team_supports_resume(latest.team)


def resolve_pending_deploy_gate(queue: Queue, task_id: str) -> PendingDeployGate | None:
    jobs = queue.list_jobs_for_workspace(task_id, status=JobStatus.PENDING)
    for job in jobs:
        if job.team != _DEPLOY_VERIFY_TEAM:
            continue
        record = GateRecord.from_json(job.instructions)
        if record is None or record.path not in _PENDING_GATE_PATHS:
            continue
        return PendingDeployGate(
            path=cast(PendingGatePath, record.path),
            deadline=record.deadline,
            race_deadline=record.race_deadline,
            merged_at=record.merged_at,
            waited_s=waited_s(record, now=time.time()),
        )
    return None


def latest_pr_number(handovers: list[Handover]) -> int | None:
    return next((h.pr_number for h in reversed(handovers) if h.pr_number is not None), None)


def manager_summary_stale(
    written_at: datetime | None,
    ws_updated_at: datetime,
    handovers: list[Handover],
) -> bool:
    if written_at is None:
        return False
    if ws_updated_at > written_at:
        return True
    return any(h.timestamp_utc > written_at for h in handovers)


def _safe_manager_run_summary(ws_path: Path) -> RunSummary | None:
    try:
        return read_manager_summary_usage(ws_path)
    except Exception:  # noqa: BLE001 - best-effort, never blocks the host response
        logger.warning("manager_summary: usage read failed for %s", ws_path, exc_info=True)
        return None


async def fetch_manager_summary(
    ws_path: Path,
    ws_updated_at: datetime,
    handovers: list[Handover],
) -> ManagerSummary | None:
    raw = await run_in_threadpool(read_manager_summary, ws_path)
    if raw is None:
        return None
    try:
        summary = ManagerSummary.model_validate(raw)
    except ValidationError:
        logger.warning("manager_summary: invalid sidecar at %s", ws_path)
        summary = ManagerSummary(ok=False)
    summary.run_summary = await run_in_threadpool(_safe_manager_run_summary, ws_path)
    summary.written_at = await run_in_threadpool(read_manager_summary_written_at, ws_path)
    summary.stale = manager_summary_stale(summary.written_at, ws_updated_at, handovers)
    return summary


__all__ = [
    "ManagerSummary",
    "PendingDeployGate",
    "PendingGatePath",
    "WorkspaceDetail",
    "detail_timing",
    "fetch_manager_summary",
    "fetch_terminal_outcome",
    "has_resumable_failed_job",
    "latest_pr_number",
    "manager_summary_stale",
    "resolve_pending_deploy_gate",
]
