from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from alan import dispatch_provenance as provenance_mod
from alan import lifecycle, roots, store
from alan.engine_config import EngineConfig
from alan.engine_config_validation import Posture
from alan.lifecycle import Blocker, ParkReason
from alan.lifecycle import Status as Status
from alan.scope_audit import ScopeGapFinding

RequirementStatus = Literal["PASS", "FAIL", "PENDING"]
ClauseKind = Literal["behaviour", "record"]


def as_utc(dt: datetime) -> datetime:
    return dt if dt.utcoffset() is not None else dt.replace(tzinfo=UTC)


class EngineSnapshot(BaseModel, frozen=True):
    posture: Posture
    transition_graph: dict[str, list[str]]
    config: EngineConfig | None = None


def _clause_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.strip().encode()).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _fill_clause_id(prefix: str, data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    d = cast(dict[str, Any], data)
    if d.get("id"):
        return d
    text = d.get("text")
    if not isinstance(text, str) or not text:
        return d
    return {**d, "id": _clause_id(prefix, text)}


class Requirement(BaseModel):
    id: str = Field(default="")
    text: str = Field(min_length=1)
    test_ref: str | None = Field(default=None, min_length=1)
    status: RequirementStatus = "PENDING"
    merged_pr_url: str | None = None
    withdrawn_by_question_id: str | None = None
    kind: ClauseKind | None = None

    @model_validator(mode="before")
    @classmethod
    def _heal_id(cls, data: Any) -> Any:
        return _fill_clause_id("req", data)

    @model_validator(mode="after")
    def _record_has_no_test_ref(self) -> Requirement:
        if self.kind == "record" and self.test_ref is not None:
            raise ValueError("a record clause cannot name a test_ref")
        return self


class TaskDoDClause(BaseModel):
    id: str = Field(default="")
    text: str = Field(min_length=1)
    covers: list[str] = Field(default_factory=list, max_length=64)
    evidence: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _heal_id(cls, data: Any) -> Any:
        return _fill_clause_id("dod", data)


class TestScenario(BaseModel):
    __test__: ClassVar[bool] = False
    id: str
    description: str
    given: str
    when: str
    then: str
    test_ref: str | None = None


class ScenarioBinding(BaseModel):
    when: str | None = None
    test_ref: str | None = None
    waived: bool = False
    reason: str = Field(min_length=1)
    written_by: Literal["operator", "fix_rebind"] = "operator"


class StickyInstruction(BaseModel):
    text: str
    origin: Literal["dispatch", "generated"]


_META_FILE = "meta.json"
_HISTORY_FILE = "history.json"
_HANDOVERS_DIR = "handovers"
_TASKS_DIR = "tasks"
_SLUG_MAX_LEN = 32
_MAX_SLUG_SUFFIX = 100
_TASK_TITLE_MAX_LEN = 500
_MAX_DISPATCH_INSTRUCTIONS = 200


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanningState(StrEnum):
    OUTLINE = "outline"
    DETAILED = "detailed"


class InterventionAction(StrEnum):
    ANSWER = "answer"
    REDISPATCH = "redispatch"
    RESUME = "resume"
    FORCE_COMPLETE = "force_complete"
    SCENARIO_OVERRIDE = "scenario_override"
    HEALTH_COMPLETE = "health_complete"
    ATTACH_PR = "attach_pr"
    OPERATOR_TERMINAL = "operator_terminal"


class Intervention(BaseModel):
    action: InterventionAction
    timestamp_utc: str


_LEGACY_STATUS_HEAL = {"plan_needs_revision"}


class Task(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    team: str | None = Field(default=None, min_length=1, max_length=64)
    status: TaskStatus
    title: str | None = Field(default=None, max_length=500)
    instructions: str | None = None
    injection_depth: int = Field(default=0, ge=0)
    description: str | None = None
    dod_clauses: list[TaskDoDClause] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    planning_state: PlanningState = PlanningState.DETAILED
    split_rationale: str | None = None
    parent_task_id: str | None = None
    child_task_ids: list[str] = Field(default_factory=list, max_length=64)
    scenarios: list[TestScenario] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    scenarios_waived: bool = False
    blocked_handover_id: str | None = None
    files: list[str] = Field(default_factory=list)


class TaskDependency(BaseModel):
    from_id: str
    to_id: str


def _deps_have_unknown_ids(deps: list[TaskDependency], ids: set[str]) -> bool:
    return any(dep.from_id not in ids or dep.to_id not in ids for dep in deps)


def _build_adj(deps: list[TaskDependency], ids: set[str]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {tid: [] for tid in ids}
    for dep in deps:
        adj[dep.from_id].append(dep.to_id)
    return adj


def _has_cycle(
    adj: dict[str, list[str]], node: str, visited: set[str], stack: set[str]
) -> bool:
    visited.add(node)
    stack.add(node)
    for neighbor in adj.get(node, []):
        if neighbor not in visited and _has_cycle(adj, neighbor, visited, stack):
            return True
        if neighbor in stack:
            return True
    stack.discard(node)
    return False


def _adj_has_cycle(adj: dict[str, list[str]]) -> bool:
    visited: set[str] = set()
    for node in adj:
        if node not in visited and _has_cycle(adj, node, visited, set()):
            return True
    return False


def validate_dag_inputs(ids: set[str], deps: list[TaskDependency]) -> None:
    if _deps_have_unknown_ids(deps, ids):
        raise ValueError("dependency references unknown task id")
    if _adj_has_cycle(_build_adj(deps, ids)):
        raise ValueError("dependency cycle detected")


def apply_dependencies(tasks: list[Task], deps: list[TaskDependency]) -> list[Task]:
    result: list[Task] = []
    for t in tasks:
        extra = [d.from_id for d in deps if d.to_id == t.id]
        merged = list(dict.fromkeys([*t.depends_on, *extra]))
        result.append(t.model_copy(update={"depends_on": merged}))
    return result


_PR_NUMBER_RE = re.compile(r"PR #(\d+)")


def pr_numbers_from_tasks(tasks: list[Task]) -> list[int]:
    numbers: list[int] = []
    for task in tasks:
        for clause in task.dod_clauses:
            if clause.evidence:
                numbers.extend(int(m.group(1)) for m in _PR_NUMBER_RE.finditer(clause.evidence))
    return numbers


def _empty_scope_audit() -> list[ScopeGapFinding]:
    return []


class Meta(BaseModel):
    task_id: str
    title: str
    description: str
    status: Status
    revisit_counts: dict[str, int] = Field(default_factory=dict)
    team: str | None = None
    tasks: list[Task] = Field(default_factory=list[Task])
    scope_audit: list[ScopeGapFinding] = Field(
        default_factory=_empty_scope_audit, max_length=20
    )
    requirements: list[Requirement] | None = None
    assumptions: list[str] = Field(default_factory=list)
    pending_first_hop: str | None = None
    injection_overflow: list[str] = Field(default_factory=list)
    merge_block_class: str | None = None
    park_reason: ParkReason | None = None
    pre_archive_status: Status | None = None
    blockers: list[Blocker] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    interventions: list[Intervention] = Field(default_factory=list[Intervention])
    scenario_bindings: dict[str, ScenarioBinding] = Field(default_factory=dict)
    dispatch_instructions: dict[str, StickyInstruction] = Field(
        default_factory=dict, max_length=_MAX_DISPATCH_INSTRUCTIONS
    )
    note: str | None = None
    engine_snapshot: EngineSnapshot | None = None
    engine_key: str | None = None
    project_id: str | None = None
    roadmap_item_id: str | None = None
    dispatch_provenance: provenance_mod.DispatchProvenance = Field(
        default_factory=lambda: provenance_mod.UNRECORDED, frozen=True
    )
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None = None
    settled_active_seconds: float | None = None
    live_at: datetime | None = None
    terminal_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _synthesize_tasks_for_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = cast(dict[str, Any], data)
        if "tasks" in d:
            return d
        task_id = d.get("task_id", "")
        return {**d, "tasks": [{"id": task_id, "status": "ready"}]}

    @field_validator("status", mode="before")
    @classmethod
    def _heal_legacy_status(cls, value: Any) -> Any:
        if isinstance(value, str) and value in _LEGACY_STATUS_HEAL:
            return Status.AWAITING_INPUT
        return value

    @field_validator("revisit_counts", mode="before")
    @classmethod
    def _heal_revisit_counts_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            k: v
            for k, v in cast(dict[str, int], value).items()
            if isinstance(k, str) and k.count("|") in (1, 3)  # pyright: ignore[reportUnnecessaryIsInstance]
        }

    @field_validator("updated_at", mode="after")
    @classmethod
    def _coerce_updated_at_utc(cls, value: datetime) -> datetime:
        return as_utc(value)


class Workspace(BaseModel):
    task_id: str
    path: Path
    meta: Meta

    model_config = {"arbitrary_types_allowed": True}

    @property
    def workspace_id(self) -> str:
        return self.meta.task_id

    @property
    def tasks(self) -> list[Task]:
        return self.meta.tasks


class WorkspaceError(RuntimeError):
    pass


_HistoryAdapter = TypeAdapter(list[dict[str, Any]])


def _stamp_root_titles(tasks: list[Task], title: str) -> list[Task]:
    clamped = title[:_TASK_TITLE_MAX_LEN]
    return [
        t.model_copy(update={"title": clamped})
        if t.title is None and not t.depends_on and t.parent_task_id is None
        else t
        for t in tasks
    ]


def create(
    title: str,
    description: str,
    *,
    root: Path | None = None,
    team: str | None = None,
    tasks: list[Task] | None = None,
    dependencies: list[TaskDependency] | None = None,
    scope_audit: list[ScopeGapFinding] | None = None,
    requirements: list[Requirement] | None = None,
    engine_key: str | None = None,
    project_id: str | None = None,
    roadmap_item_id: str | None = None,
    provenance: provenance_mod.DispatchProvenance = provenance_mod.UNRECORDED,
    pending_first_hop: str | None = None,
) -> Workspace:
    base = _root(root)
    base.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    task_id, outer = _claim_workspace_dir(slug, base)
    ws_path = outer / _TASKS_DIR / task_id
    ws_path.mkdir(parents=True)
    (ws_path / _HANDOVERS_DIR).mkdir()
    now = datetime.now(UTC)
    if tasks:
        validate_dag_inputs({t.id for t in tasks}, dependencies or [])
        applied = apply_dependencies(tasks, dependencies or [])
        persisted_tasks = _stamp_root_titles(applied, title)
    else:
        persisted_tasks = []
    safe_reqs = _sanitize_requirements(requirements)
    meta = Meta(
        task_id=task_id,
        title=title,
        description=description,
        status=Status.ACTIVE,
        team=team,
        tasks=persisted_tasks,
        scope_audit=scope_audit or [],
        requirements=safe_reqs,
        engine_key=engine_key,
        project_id=project_id,
        roadmap_item_id=roadmap_item_id,
        dispatch_provenance=provenance,
        pending_first_hop=pending_first_hop,
        created_at=now,
        updated_at=now,
    )
    _write_meta(ws_path, meta)
    _write_json(ws_path / _HISTORY_FILE, [])
    return Workspace(task_id=task_id, path=ws_path, meta=meta)


def _sanitize_requirements(
    requirements: list[Requirement] | None,
) -> list[Requirement] | None:
    if requirements is None:
        return None
    survivors: dict[str, Requirement] = {}
    for r in requirements:
        rebuilt = Requirement(text=r.text, test_ref=r.test_ref, kind=r.kind)
        held = survivors.get(rebuilt.id)
        if held is None or (held.kind == "record" and rebuilt.kind != "record"):
            survivors[rebuilt.id] = rebuilt
    return list(survivors.values())


def _withdrawal_markers(fresh: list[Requirement] | None) -> dict[str, str]:
    return {
        r.id: r.withdrawn_by_question_id
        for r in (fresh or [])
        if r.withdrawn_by_question_id is not None
    }


def _carry_forward_marker(req: Requirement, markers: dict[str, str]) -> Requirement:
    if req.withdrawn_by_question_id is not None:
        return req
    marker = markers.get(req.id)
    if marker is None:
        return req
    return req.model_copy(update={"withdrawn_by_question_id": marker})


def _apply_clear_withdrawn(
    markers: dict[str, str], clear_withdrawn: tuple[frozenset[str], str] | None
) -> dict[str, str]:
    if clear_withdrawn is None:
        return markers
    ids, expected = clear_withdrawn
    return {k: v for k, v in markers.items() if not (k in ids and v == expected)}


def _merge_withdrawal_markers(
    fresh: list[Requirement] | None,
    incoming: list[Requirement],
    *,
    clear_withdrawn: tuple[frozenset[str], str] | None = None,
) -> list[Requirement]:
    markers = _apply_clear_withdrawn(_withdrawal_markers(fresh), clear_withdrawn)
    if not markers:
        return incoming
    return [_carry_forward_marker(r, markers) for r in incoming]


def update_requirements(
    ws: Workspace,
    requirements: list[Requirement],
    *,
    clear_withdrawn: tuple[frozenset[str], str] | None = None,
) -> Workspace:
    fresh_meta = _read_meta(ws.path)
    merged = _merge_withdrawal_markers(
        fresh_meta.requirements, requirements, clear_withdrawn=clear_withdrawn
    )
    new_meta = fresh_meta.model_copy(
        update={"requirements": merged, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def update_assumptions(ws: Workspace, assumptions: list[str]) -> Workspace:
    new_meta = _read_meta(ws.path).model_copy(
        update={"assumptions": assumptions, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def update_scenario_binding(
    ws: Workspace, scenario_id: str, binding: ScenarioBinding | None
) -> Workspace:
    bindings = dict(ws.meta.scenario_bindings)
    if binding is None:
        if scenario_id not in bindings:
            return ws
        del bindings[scenario_id]
    else:
        bindings[scenario_id] = binding
    new_meta = ws.meta.model_copy(
        update={"scenario_bindings": bindings, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def _relink_parents(existing: list[Task], added: list[Task]) -> list[Task]:
    children: dict[str, list[str]] = {}
    for t in added:
        if t.parent_task_id is not None:
            children.setdefault(t.parent_task_id, []).append(t.id)
    if not children:
        return existing
    return [
        t.model_copy(update={"child_task_ids": [*t.child_task_ids, *children[t.id]]})
        if t.id in children
        else t
        for t in existing
    ]


def inject_tasks(
    ws: Workspace,
    tasks_to_add: list[Task],
    overflow_ids: list[str],
) -> Workspace:
    if not tasks_to_add and not overflow_ids:
        return ws
    new_tasks = [*_relink_parents(ws.meta.tasks, tasks_to_add), *tasks_to_add]
    existing_overflow = set(ws.meta.injection_overflow)
    new_overflow = [
        *ws.meta.injection_overflow,
        *(oid for oid in overflow_ids if oid not in existing_overflow),
    ]
    new_meta = ws.meta.model_copy(
        update={
            "tasks": new_tasks,
            "injection_overflow": new_overflow,
            "updated_at": datetime.now(UTC),
        }
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def read(task_id: str, *, root: Path | None = None) -> Workspace:
    outer = _root(root) / task_id
    if not outer.is_dir():
        raise WorkspaceError(f"workspace not found: {task_id}")
    ws_path = _task_leaf(outer)
    if not (ws_path / _META_FILE).is_file():
        raise WorkspaceError(f"workspace not found: {task_id}")
    return Workspace(task_id=task_id, path=ws_path, meta=_read_meta(ws_path))


def list_workspaces(*, root: Path | None = None) -> list[Workspace]:
    base = _root(root)
    if not base.is_dir():
        return []
    out: list[Workspace] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        task_path = _task_leaf(entry)
        if not (task_path / _META_FILE).is_file():
            continue
        try:
            meta = _read_meta_cached(task_path)
        except WorkspaceError:
            continue
        out.append(Workspace(task_id=entry.name, path=task_path, meta=meta))
    return out


def append_history(ws: Workspace, entry: dict[str, Any]) -> None:
    path = ws.path / _HISTORY_FILE
    history = _read_history(path, ws.task_id)
    history.append(entry)
    _write_json(path, history)


def transition(
    ws: Workspace,
    status: Status,
    *,
    merge_block_class: str | None = None,
    clear_merge_block_class: bool = False,
    park_reason: ParkReason | None = None,
    blockers: list[Blocker] | None = None,
    clear_park_reason: bool = False,
    clear_blockers: bool = False,
) -> Workspace:
    now = datetime.now(UTC)
    on_disk = _read_meta_or_snapshot(ws)
    stamped = lifecycle.stamps_finish(on_disk.status, status, on_disk.terminal_at)
    update = {
        **lifecycle.transition(
            status,
            merge_block_class=merge_block_class,
            clear_merge_block_class=clear_merge_block_class,
            park_reason=park_reason,
            blockers=blockers,
            clear_park_reason=clear_park_reason,
            clear_blockers=clear_blockers,
        ),
        "pre_archive_status": lifecycle.pre_archive_status(
            on_disk.status, status, on_disk.pre_archive_status
        ),
        "updated_at": now,
        "terminal_at": now if stamped else on_disk.terminal_at,
    }
    new_meta = ws.meta.model_copy(update=update)
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def _blocked_handover_update(
    blocked_handover_id: str | None, *, clear_blocked_handover_id: bool
) -> dict[str, Any]:
    if blocked_handover_id is not None:
        return {"blocked_handover_id": blocked_handover_id}
    if clear_blocked_handover_id:
        return {"blocked_handover_id": None}
    return {}


def update_task_status(
    ws: Workspace,
    task_id: str,
    status: TaskStatus,
    *,
    blocked_handover_id: str | None = None,
    clear_blocked_handover_id: bool = False,
) -> Workspace:
    matched = [t for t in ws.meta.tasks if t.id == task_id]
    if len(matched) != 1:
        raise WorkspaceError(
            f"task {task_id!r} not found in workspace {ws.workspace_id!r}"
        )
    task_update: dict[str, Any] = {
        "status": status,
        **_blocked_handover_update(
            blocked_handover_id, clear_blocked_handover_id=clear_blocked_handover_id
        ),
    }
    new_tasks = [
        t.model_copy(update=task_update) if t.id == task_id else t
        for t in ws.meta.tasks
    ]
    new_meta = ws.meta.model_copy(update={"tasks": new_tasks, "updated_at": datetime.now(UTC)})
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def _stamp_dod_evidence(dods: list[TaskDoDClause], evidence: str) -> list[TaskDoDClause]:
    return [
        d.model_copy(update={"evidence": evidence}) if d.evidence is None else d
        for d in dods
    ]


def record_task_evidence(ws: Workspace, task_id: str, evidence: str) -> Workspace:
    new_tasks = [
        t.model_copy(update={"dod_clauses": _stamp_dod_evidence(t.dod_clauses, evidence)})
        if t.id == task_id
        else t
        for t in ws.meta.tasks
    ]
    if new_tasks == ws.meta.tasks:
        return ws
    new_meta = ws.meta.model_copy(
        update={"tasks": new_tasks, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def update_task_files(ws: Workspace, task_id: str, files: list[str]) -> Workspace:
    new_tasks = [
        t.model_copy(update={"files": files}) if t.id == task_id else t
        for t in ws.meta.tasks
    ]
    if new_tasks == ws.meta.tasks:
        return ws
    new_meta = ws.meta.model_copy(
        update={"tasks": new_tasks, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def set_sticky_instructions(
    ws: Workspace, task_id: str, text: str, *, origin: Literal["dispatch", "generated"]
) -> Workspace:
    fresh_meta = _read_meta(ws.path)
    instructions = dict(fresh_meta.dispatch_instructions)
    if task_id not in instructions and len(instructions) >= _MAX_DISPATCH_INSTRUCTIONS:
        raise WorkspaceError(
            f"workspace {ws.task_id!r} already holds {_MAX_DISPATCH_INSTRUCTIONS} sticky "
            "instructions; clear one before adding another"
        )
    instructions[task_id] = StickyInstruction(text=text, origin=origin)
    new_meta = fresh_meta.model_copy(
        update={"dispatch_instructions": instructions, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def clear_sticky_instructions(ws: Workspace, task_id: str) -> Workspace:
    if task_id not in ws.meta.dispatch_instructions:
        return ws
    instructions = dict(ws.meta.dispatch_instructions)
    del instructions[task_id]
    new_meta = ws.meta.model_copy(
        update={"dispatch_instructions": instructions, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def update_task_scenarios(
    ws: Workspace,
    task_id: str,
    *,
    scenarios: list[TestScenario] | None = None,
    waived: bool | None = None,
) -> Workspace:
    task_update: dict[str, Any] = {}
    if scenarios is not None:
        task_update["scenarios"] = scenarios
    if waived is not None:
        task_update["scenarios_waived"] = waived
    new_tasks = [
        t.model_copy(update=task_update) if t.id == task_id else t for t in ws.meta.tasks
    ]
    if new_tasks == ws.meta.tasks:
        return ws
    new_meta = ws.meta.model_copy(
        update={"tasks": new_tasks, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def revisit_key(workspace_id: str, task_id: str, team: str, chain_id: str) -> str:
    return f"{workspace_id}|{task_id}|{team}|{chain_id}"


def increment_revisit_count(
    ws: Workspace, team: str, chain_id: str, *, task_id: str | None = None
) -> Workspace:
    _task_id = task_id if task_id is not None else ws.meta.task_id
    key = revisit_key(ws.workspace_id, _task_id, team, chain_id)
    counts = dict(ws.meta.revisit_counts)
    counts[key] = counts.get(key, 0) + 1
    new_meta = ws.meta.model_copy(
        update={"revisit_counts": counts, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def decay_revisit_count(
    ws: Workspace, team: str, chain_id: str, *, task_id: str | None = None
) -> Workspace:
    _task_id = task_id if task_id is not None else ws.meta.task_id
    key = revisit_key(ws.workspace_id, _task_id, team, chain_id)
    counts = dict(ws.meta.revisit_counts)
    counts[key] = max(0, counts.get(key, 0) - 1)
    new_meta = ws.meta.model_copy(
        update={"revisit_counts": counts, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def reset_revisit_chain(
    ws: Workspace, chain_id: str, *, task_id: str | None = None
) -> Workspace:
    _task_id = task_id if task_id is not None else ws.meta.task_id
    suffix = f"|{chain_id}"
    task_seg = f"|{_task_id}|"
    counts = {
        k: v
        for k, v in ws.meta.revisit_counts.items()
        if not (k.endswith(suffix) and task_seg in k)
    }
    new_meta = ws.meta.model_copy(
        update={"revisit_counts": counts, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def _revisit_key_team(key: str) -> str:
    parts = key.split("|")
    return parts[2] if len(parts) == 4 else parts[0]


def prune_revisit_counts_for_team(ws: Workspace, team: str) -> int:
    kept = {
        k: v for k, v in ws.meta.revisit_counts.items() if _revisit_key_team(k) != team
    }
    removed = len(ws.meta.revisit_counts) - len(kept)
    if not removed:
        return 0
    new_meta = ws.meta.model_copy(
        update={"revisit_counts": kept, "updated_at": datetime.now(UTC)}
    )
    _write_meta(ws.path, new_meta)
    return removed


def stamp_engine_snapshot(ws: Workspace, snapshot: EngineSnapshot) -> Workspace:
    new_meta = ws.meta.model_copy(update={"engine_snapshot": snapshot})
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def mark_delivered(
    ws: Workspace,
    *,
    settled_active_seconds: float | None,
    live_at: datetime | None = None,
) -> Workspace:
    fresh_meta = _read_meta(ws.path)
    if fresh_meta.delivered_at is not None:
        return Workspace(task_id=ws.task_id, path=ws.path, meta=fresh_meta)
    new_meta = fresh_meta.model_copy(
        update={
            "delivered_at": datetime.now(UTC),
            "settled_active_seconds": settled_active_seconds,
            "live_at": live_at,
            "updated_at": datetime.now(UTC),
        }
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def record_intervention(ws: Workspace, action: InterventionAction) -> Workspace:
    fresh_meta = _read_meta(ws.path)
    now = datetime.now(UTC)
    stamped = Intervention(action=action, timestamp_utc=now.isoformat())
    new_meta = fresh_meta.model_copy(
        update={
            "interventions": [*fresh_meta.interventions, stamped],
            "updated_at": now,
        }
    )
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def update_note(ws: Workspace, note: str | None) -> Workspace:
    normalized = (note.strip() if note else None) or None
    new_meta = ws.meta.model_copy(update={"note": normalized})
    _write_meta(ws.path, new_meta)
    return Workspace(task_id=ws.task_id, path=ws.path, meta=new_meta)


def exists(task_id: str, *, root: Path | None = None) -> bool:
    outer = _root(root) / task_id
    if not outer.is_dir():
        return False
    return (_task_leaf(outer) / _META_FILE).is_file()


def delete(task_id: str, *, root: Path | None = None) -> None:
    ws_path = _root(root) / task_id
    if not ws_path.is_dir():
        raise WorkspaceError(f"workspace not found: {task_id}")
    try:
        shutil.rmtree(ws_path)
    except OSError as exc:
        raise WorkspaceError(f"failed to delete workspace {task_id}: {exc}") from exc


def _root(root: Path | None) -> Path:
    if root is not None:
        return root
    return roots.workspaces_root()


def _task_leaf(outer: Path) -> Path:
    new_layout = outer / _TASKS_DIR / outer.name
    return new_layout if new_layout.is_dir() else outer


def _slugify(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:_SLUG_MAX_LEN] or "task"


def _claim_workspace_dir(slug: str, base: Path) -> tuple[str, Path]:
    for i in range(1, _MAX_SLUG_SUFFIX + 1):
        candidate_id = slug if i == 1 else f"{slug}-{i}"
        candidate = base / candidate_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate_id, candidate
    raise WorkspaceError(f"no unique task_id for {slug}")


def _write_meta(ws_path: Path, meta: Meta) -> None:
    _write_json(ws_path / _META_FILE, meta.model_dump(mode="json"))


def _read_meta(ws_path: Path) -> Meta:
    raw = _read_text(ws_path / _META_FILE)
    try:
        return Meta.model_validate_json(raw)
    except ValidationError as exc:
        raise WorkspaceError(f"{_META_FILE} malformed in {ws_path.name}") from exc


def _read_meta_or_snapshot(ws: Workspace) -> Meta:
    try:
        return _read_meta(ws.path)
    except WorkspaceError:
        return ws.meta


_META_CACHE: dict[Path, tuple[int, int, Meta]] = {}


def _read_meta_cached(ws_path: Path) -> Meta:
    meta_path = ws_path / _META_FILE
    try:
        st = meta_path.stat()
    except OSError:
        return _read_meta(ws_path)
    hit = store.stat_cache_hit(_META_CACHE, meta_path, st)
    if hit is not None:
        return copy.deepcopy(hit[0])
    meta = _read_meta(ws_path)
    store.stat_cache_put(_META_CACHE, meta_path, st, meta)
    return copy.deepcopy(meta)


def _read_history(path: Path, task_id: str) -> list[dict[str, Any]]:
    raw = _read_text(path)
    try:
        return _HistoryAdapter.validate_json(raw)
    except ValidationError as exc:
        raise WorkspaceError(f"{_HISTORY_FILE} malformed in {task_id}") from exc


def _write_json(path: Path, data: Any) -> None:
    store.write_text_atomic(path, json.dumps(data, indent=2, sort_keys=False))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkspaceError(f"missing file: {path.name}") from exc
