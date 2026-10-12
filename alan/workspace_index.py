from __future__ import annotations

import itertools
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import alan.fleet_timing as fleet_timing_mod
import alan.handover as handover_mod
import alan.workflow as workflow_mod
import alan.workspace as workspace_mod
import alan.workspace_cost as workspace_cost_mod
import alan.workspace_size as workspace_size_mod
from alan import store
from alan.handover import HandoverError
from alan.park import ParkReason
from alan.queue import Queue
from alan.workflow import WorkflowError, WorkflowRowSummary, WorkflowStatus
from alan.workspace import Status, WorkspaceError, as_utc

logger = logging.getLogger("alan.workspace_index")

ARCHIVED_ROWS_META_KEY = "workspace_index_archived_v1"
_BLOB_VERSION = 6
_META_FILE = "meta.json"


class WorkspaceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    title: str
    status: Status
    team: str | None = None
    note: str | None = None
    park_reason: ParkReason | None = None
    team_runs_total: int = 0
    created_at: datetime
    updated_at: datetime
    latest_team: str | None = None
    latest_invocation_status: WorkflowStatus | None = None
    total_cost_usd: float | None = None
    cost_totals: workspace_cost_mod.WorkspaceCostTotals = Field(
        default_factory=workspace_cost_mod.WorkspaceCostTotals
    )
    scope_ref: str | None = None
    handovers_mtime: int | None = None
    project_id: str | None = None
    engine_key: str | None = None
    roadmap_item_id: str | None = None
    tasks_total: int = 0
    tasks_done: int = 0
    pr_number: int | None = None
    active_closed_seconds: float | None = None
    active_open_since: datetime | None = None
    delivered_at: datetime | None = None
    size_class: workspace_size_mod.SizeClass | None = None

    @field_validator("updated_at", mode="after")
    @classmethod
    def _coerce_updated_at_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    def resolved_scope_ref(self) -> str:
        return self.scope_ref if self.scope_ref is not None else f"ws:{self.task_id}"

    def live_active_seconds(self, now: datetime) -> float | None:
        return fleet_timing_mod.ActiveFleetTime(
            closed_seconds=self.active_closed_seconds, open_since=self.active_open_since
        ).live_seconds(now)


class ProjectBurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    burn_usd: float
    budget_usd: float | None = None
    remaining_usd: float | None = None


def project_burn_usd(
    rows: list[WorkspaceRow], archived: list[WorkspaceRow], project_id: str
) -> float:
    costs = (
        row.total_cost_usd if row.total_cost_usd is not None else 0.0
        for row in itertools.chain(rows, archived)
        if row.project_id == project_id
    )
    return sum(costs, 0.0)


class ProjectAggregate(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_count: int
    total_spend_usd: float


def project_aggregates(
    rows: list[WorkspaceRow], archived: list[WorkspaceRow]
) -> dict[str, ProjectAggregate]:
    """One O(W) pass over already-loaded rows — zero new disk reads.

    Folds `total_cost_usd` the same way `project_burn_usd` does (None -> 0.0), but
    for every `project_id` at once instead of one project per call, so `GET
    /api/projects` can attach per-project counts/spend without an N+1 scan.
    """
    counts: dict[str, int] = {}
    spend: dict[str, float] = {}
    for row in itertools.chain(rows, archived):
        if row.project_id is None:
            continue
        counts[row.project_id] = counts.get(row.project_id, 0) + 1
        cost = row.total_cost_usd if row.total_cost_usd is not None else 0.0
        spend[row.project_id] = spend.get(row.project_id, 0.0) + cost
    return {
        project_id: ProjectAggregate(
            workspace_count=count, total_spend_usd=spend[project_id]
        )
        for project_id, count in counts.items()
    }


DELIVERED_STATUSES: frozenset[Status] = frozenset({Status.COMPLETED, Status.ARCHIVED})


def delivered_roadmap_item_ids(
    rows: list[WorkspaceRow], archived: list[WorkspaceRow]
) -> dict[str, set[str]]:
    delivered: dict[str, set[str]] = {}
    for row in itertools.chain(rows, archived):
        if row.project_id is None or row.roadmap_item_id is None:
            continue
        if row.status not in DELIVERED_STATUSES or row.pr_number is None:
            continue
        delivered.setdefault(row.project_id, set()).add(row.roadmap_item_id)
    return delivered


@dataclass
class _Guards:
    ws_path: Path
    meta_sig: tuple[int, int]
    inv_summaries: dict[str, WorkflowRowSummary] = field(
        default_factory=dict[str, WorkflowRowSummary]
    )
    inv_failures: dict[str, tuple[int, int] | None] = field(
        default_factory=dict[str, tuple[int, int] | None]
    )
    handovers_dir_mtime: int | None = None
    handovers_listing: tuple[store.ListingEntry, ...] | None = None
    scope_ref: str | None = None
    plan_files_to_edit: list[str] | None = None
    architecture_files_to_edit: list[str] | None = None
    cost_guards: workspace_cost_mod.ChatCostGuards = field(
        default_factory=workspace_cost_mod.ChatCostGuards
    )


def _dir_mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _update_cost_totals(
    guards: _Guards, *, include_new_categories: bool = True
) -> workspace_cost_mod.WorkspaceCostTotals:
    return workspace_cost_mod.derive_cost_totals(
        inv_summaries=guards.inv_summaries,
        ws_path=guards.ws_path,
        chat_guards=guards.cost_guards,
        include_new_categories=include_new_categories,
    )


_PLAN_TEAM = "implementation_plan"
_ARCHITECTURE_TEAMS: frozenset[str] = frozenset({"architecture", "decomposer"})


def _pick_pr_scope_ref(current: str | None, handover: handover_mod.Handover) -> str | None:
    if current is not None:
        return current
    if handover.pr_number is None:
        return None
    return f"pr:{handover.pr_number}"


def _pick_files_to_edit(
    current: list[str] | None, handover: handover_mod.Handover, *, team_match: bool
) -> list[str] | None:
    if current is not None:
        return current
    if not team_match:
        return None
    return list(handover.files_to_edit)


def _derive_handover_signals(
    paths: list[Path], task_id: str
) -> tuple[str, list[str] | None, list[str] | None]:
    scope_ref: str | None = None
    plan_files_to_edit: list[str] | None = None
    architecture_files_to_edit: list[str] | None = None
    for path in reversed(paths):
        try:
            handover = handover_mod.load_handover_cached(path)
        except (HandoverError, OSError):
            continue
        scope_ref = _pick_pr_scope_ref(scope_ref, handover)
        plan_files_to_edit = _pick_files_to_edit(
            plan_files_to_edit, handover, team_match=handover.from_team == _PLAN_TEAM
        )
        architecture_files_to_edit = _pick_files_to_edit(
            architecture_files_to_edit,
            handover,
            team_match=handover.from_team in _ARCHITECTURE_TEAMS,
        )
    return scope_ref or f"ws:{task_id}", plan_files_to_edit, architecture_files_to_edit


def _update_handover_signals(guards: _Guards, task_id: str) -> None:
    guards.handovers_dir_mtime = _dir_mtime(handover_mod.handovers_dir(guards.ws_path))
    paths = handover_mod.handover_paths_under(guards.ws_path, task_id)
    listing = store.listing_fingerprint(paths)
    if listing == guards.handovers_listing:
        return
    guards.handovers_listing = listing
    scope_ref, plan_files_to_edit, architecture_files_to_edit = _derive_handover_signals(
        paths, task_id
    )
    guards.scope_ref = scope_ref
    guards.plan_files_to_edit = plan_files_to_edit
    guards.architecture_files_to_edit = architecture_files_to_edit


def _load_inv_summary(guards: _Guards, inv_id: str) -> None:
    path = workflow_mod.workflow_file(guards.ws_path, inv_id)
    sig = store.stat_signature(path)
    if inv_id in guards.inv_failures and guards.inv_failures[inv_id] == sig:
        return
    try:
        guards.inv_summaries[inv_id] = workflow_mod.load_workflow_summary_cached(path)
    except WorkflowError:
        guards.inv_summaries.pop(inv_id, None)
        guards.inv_failures[inv_id] = sig
    else:
        guards.inv_failures.pop(inv_id, None)


def _update_inv_summaries(guards: _Guards) -> None:
    inv_ids = workflow_mod.list_invocation_ids(guards.ws_path)
    live = set(inv_ids)
    for gone in set(guards.inv_summaries) - live:
        del guards.inv_summaries[gone]
    for gone in set(guards.inv_failures) - live:
        del guards.inv_failures[gone]
    for inv_id in inv_ids:
        _load_inv_summary(guards, inv_id)


def _active_time_fields(
    meta: workspace_mod.Meta, guards: _Guards
) -> tuple[float | None, datetime | None]:
    if meta.delivered_at is not None:
        return meta.settled_active_seconds, None
    active = fleet_timing_mod.active_fleet_time_for_summaries(guards.inv_summaries.values())
    return active.closed_seconds, active.open_since


def _row_from_meta(
    meta: workspace_mod.Meta, guards: _Guards, *, compute_costs: bool = True
) -> WorkspaceRow:
    inv_ids = sorted(guards.inv_summaries)
    latest = guards.inv_summaries[inv_ids[-1]] if inv_ids else None
    totals = _update_cost_totals(guards, include_new_categories=compute_costs)
    pr_numbers = workspace_mod.pr_numbers_from_tasks(meta.tasks)
    active_closed_seconds, active_open_since = _active_time_fields(meta, guards)
    return WorkspaceRow(
        task_id=meta.task_id,
        title=meta.title,
        status=meta.status,
        team=meta.team,
        note=meta.note,
        park_reason=meta.park_reason,
        team_runs_total=sum(meta.revisit_counts.values()),
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        latest_team=latest.team if latest else None,
        latest_invocation_status=latest.status if latest else None,
        total_cost_usd=workspace_cost_mod.total_cost_usd(totals),
        cost_totals=totals,
        scope_ref=guards.scope_ref,
        handovers_mtime=guards.handovers_dir_mtime,
        project_id=meta.project_id,
        engine_key=meta.engine_key,
        roadmap_item_id=meta.roadmap_item_id,
        tasks_total=len(meta.tasks),
        tasks_done=sum(
            1 for t in meta.tasks if t.status == workspace_mod.TaskStatus.COMPLETED
        ),
        pr_number=pr_numbers[-1] if pr_numbers else None,
        active_closed_seconds=active_closed_seconds,
        active_open_since=active_open_since,
        delivered_at=meta.delivered_at,
        size_class=workspace_size_mod.derive_size_class(
            plan_files_to_edit=guards.plan_files_to_edit,
            architecture_files_to_edit=guards.architecture_files_to_edit,
            dag_task_count=len(meta.tasks),
        ),
    )


def _row_with_updated_costs(row: WorkspaceRow, guards: _Guards) -> WorkspaceRow:
    inv_ids = sorted(guards.inv_summaries)
    latest = guards.inv_summaries[inv_ids[-1]] if inv_ids else None
    totals = _update_cost_totals(guards)
    active_closed_seconds, active_open_since = row.active_closed_seconds, row.active_open_since
    if row.delivered_at is None:
        active = fleet_timing_mod.active_fleet_time_for_summaries(guards.inv_summaries.values())
        active_closed_seconds, active_open_since = active.closed_seconds, active.open_since
    size_class = workspace_size_mod.derive_size_class(
        plan_files_to_edit=guards.plan_files_to_edit,
        architecture_files_to_edit=guards.architecture_files_to_edit,
        dag_task_count=row.tasks_total,
    )
    return row.model_copy(
        update={
            "latest_team": latest.team if latest else None,
            "latest_invocation_status": latest.status if latest else None,
            "total_cost_usd": workspace_cost_mod.total_cost_usd(totals),
            "cost_totals": totals,
            "scope_ref": guards.scope_ref,
            "size_class": size_class,
            "handovers_mtime": guards.handovers_dir_mtime,
            "active_closed_seconds": active_closed_seconds,
            "active_open_since": active_open_since,
        }
    )


def _parse_blob_rows(raw: str) -> tuple[dict[str, WorkspaceRow], bool]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    payload = cast(dict[str, Any], data)
    if payload.get("version") != _BLOB_VERSION:
        return {}, False
    rows_raw = payload.get("rows")
    if not isinstance(rows_raw, dict):
        return {}, False
    rows: dict[str, WorkspaceRow] = {}
    for task_id, row_raw in cast(dict[str, Any], rows_raw).items():
        try:
            rows[task_id] = WorkspaceRow.model_validate(row_raw)
        except ValidationError:
            logger.warning("dropping malformed archived row %s", task_id)
    return rows, bool(payload.get("complete"))


class WorkspaceIndex:
    def __init__(self, *, root: Path, queue: Queue) -> None:
        self._root = root
        self._queue = queue
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._rows: dict[str, WorkspaceRow] = {}
        self._guards: dict[str, _Guards] = {}
        self._archived: dict[str, WorkspaceRow] = {}
        self._archived_complete = False
        self._prep_session_index: dict[str, str] = {}
        self._prep_session_index_complete = False

    def rebuild(self) -> None:
        with self._refresh_lock:
            raw = self._queue.get_meta(ARCHIVED_ROWS_META_KEY)
            archived, complete = _parse_blob_rows(raw) if raw is not None else ({}, False)
            with self._lock:
                self._rows = {}
                self._guards = {}
                self._archived = archived
                self._archived_complete = complete
                self._prep_session_index = {}
                self._prep_session_index_complete = False
            self._scan()

    def refresh(self) -> None:
        with self._refresh_lock:
            self._scan()

    def rows(self) -> list[WorkspaceRow]:
        self.refresh()
        with self._lock:
            return sorted(self._rows.values(), key=lambda r: r.task_id)

    def refresh_one(self, task_id: str) -> WorkspaceRow | None:
        with self._lock:
            archived = self._archived.get(task_id)
        if archived is not None:
            return archived
        with self._refresh_lock:
            if not (self._root / task_id).is_dir():
                with self._lock:
                    self._rows.pop(task_id, None)
                    self._guards.pop(task_id, None)
                return None
            if self._refresh_one(task_id):
                self._save_blob()
        with self._lock:
            return self._rows.get(task_id) or self._archived.get(task_id)

    def archived_rows(self) -> list[WorkspaceRow]:
        self.refresh()
        return self.archived_snapshot()

    def validated_rows(self) -> tuple[list[WorkspaceRow], list[WorkspaceRow]]:
        self.refresh()
        self.backfill_archived()
        with self._lock:
            rows = sorted(self._rows.values(), key=lambda r: r.task_id)
            archived = sorted(self._archived.values(), key=lambda r: r.task_id)
        return rows, archived

    def archived_snapshot(self) -> list[WorkspaceRow]:
        with self._lock:
            return sorted(self._archived.values(), key=lambda r: r.task_id)

    def resolve_archived_prep_chat(self, session_id: str) -> str | None:
        self.refresh()
        self.backfill_archived()
        return self._prep_chat_task_id(session_id)

    def _prep_chat_task_id(self, session_id: str) -> str | None:
        with self._lock:
            return self._prep_session_index.get(session_id)

    def _record_prep_session(self, task_id: str, guards: _Guards) -> None:
        session_ids = guards.cost_guards.prep_session_ids
        if not session_ids:
            return
        with self._lock:
            self._prep_session_index[session_ids[0]] = task_id

    def invalidate(self, task_id: str) -> None:
        with self._refresh_lock:
            with self._lock:
                self._rows.pop(task_id, None)
                self._guards.pop(task_id, None)
                dropped = self._archived.pop(task_id, None)
            if dropped is not None:
                self._save_blob()

    def reindex(self) -> None:
        self.rebuild()
        with self._lock:
            self._archived_complete = False
        self.backfill_archived()

    def backfill_archived(self) -> None:
        if self._archived_complete and self._prep_session_index_complete:
            return
        with self._lock:
            pending = sorted(self._archived)
        for task_id in pending:
            row = self._derive_archived_row(task_id)
            if row is None:
                continue
            with self._lock:
                if task_id in self._archived:
                    self._archived[task_id] = row
        with self._lock:
            self._archived_complete = True
            self._prep_session_index_complete = True
        self._save_blob()

    def _derive_archived_row(self, task_id: str) -> WorkspaceRow | None:
        try:
            ws = workspace_mod.read(task_id, root=self._root)
        except WorkspaceError:
            return None
        sig = store.stat_signature(ws.path / _META_FILE)
        guards = _Guards(ws_path=ws.path, meta_sig=sig or (0, 0))
        _update_inv_summaries(guards)
        _update_handover_signals(guards, ws.task_id)
        row = _row_from_meta(ws.meta, guards)
        self._record_prep_session(task_id, guards)
        return row

    def _save_blob(self) -> None:
        with self._lock:
            payload = {
                "version": _BLOB_VERSION,
                "complete": self._archived_complete,
                "rows": {
                    task_id: row.model_dump(mode="json")
                    for task_id, row in self._archived.items()
                },
            }
        self._queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(payload))

    def _scan(self) -> None:
        present = self._present_dirs()
        blob_dirty = self._drop_vanished(present)
        for task_id in present:
            if task_id in self._archived:
                continue
            blob_dirty = self._refresh_one(task_id) or blob_dirty
        if blob_dirty:
            self._save_blob()

    def _present_dirs(self) -> set[str]:
        if not self._root.is_dir():
            return set()
        return {entry.name for entry in self._root.iterdir() if entry.is_dir()}

    def _drop_vanished(self, present: set[str]) -> bool:
        with self._lock:
            for gone in set(self._rows) - present:
                del self._rows[gone]
                self._guards.pop(gone, None)
            vanished_archived = set(self._archived) - present
            for gone in vanished_archived:
                del self._archived[gone]
        return bool(vanished_archived)

    def _refresh_one(self, task_id: str) -> bool:
        guards = self._guards.get(task_id)
        if guards is None:
            return self._classify_new(task_id)
        sig = store.stat_signature(guards.ws_path / _META_FILE)
        if sig is None:
            return False
        if sig != guards.meta_sig:
            return self._reread_meta(task_id, guards, sig)
        _update_inv_summaries(guards)
        _update_handover_signals(guards, task_id)
        with self._lock:
            self._rows[task_id] = _row_with_updated_costs(self._rows[task_id], guards)
        self._record_prep_session(task_id, guards)
        return False

    def _classify_new(self, task_id: str) -> bool:
        try:
            ws = workspace_mod.read(task_id, root=self._root)
        except WorkspaceError:
            return False
        sig = store.stat_signature(ws.path / _META_FILE)
        if sig is None:
            return False
        guards = _Guards(ws_path=ws.path, meta_sig=sig)
        return self._store_row(task_id, ws.meta, guards, refresh_archived=False)

    def _reread_meta(self, task_id: str, guards: _Guards, sig: tuple[int, int]) -> bool:
        try:
            ws = workspace_mod.read(task_id, root=self._root)
        except WorkspaceError:
            return False
        guards.meta_sig = sig
        return self._store_row(task_id, ws.meta, guards, refresh_archived=True)

    def _store_row(
        self,
        task_id: str,
        meta: workspace_mod.Meta,
        guards: _Guards,
        *,
        refresh_archived: bool,
    ) -> bool:
        if meta.status == Status.ARCHIVED:
            if refresh_archived:
                _update_inv_summaries(guards)
            _update_handover_signals(guards, task_id)
            row = _row_from_meta(meta, guards, compute_costs=refresh_archived)
            self._record_prep_session(task_id, guards)
            with self._lock:
                self._rows.pop(task_id, None)
                self._guards.pop(task_id, None)
                self._archived[task_id] = row
                if not refresh_archived:
                    # Cold-discovered row is provisional (costs/size held back above);
                    # clearing _archived_complete makes the next backfill_archived() heal
                    # it instead of treating an already-"complete" blob as done. Racing
                    # concurrent readers may both re-enter backfill — idempotent, so safe.
                    self._archived_complete = False
            return True
        _update_inv_summaries(guards)
        _update_handover_signals(guards, task_id)
        row = _row_from_meta(meta, guards)
        with self._lock:
            self._guards[task_id] = guards
            self._rows[task_id] = row
        self._record_prep_session(task_id, guards)
        return False
