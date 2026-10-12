from __future__ import annotations

import copy
import errno
import json
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError
from typing_extensions import NotRequired, TypedDict

from alan import store
from alan.node_launch_record import LaunchRecord, parse_launch
from alan.workspace import Workspace

SCHEMA_VERSION: Literal["1.7"] = "1.7"
LLMSTEP_TYPE: Literal["io.alan.LLMStep"] = "io.alan.LLMStep"
INTERRUPTED_BY_RESTART_ERROR = "interrupted_by_restart"
SENTINEL_MODEL = "none"

_INVOCATIONS_DIR = "invocations"
_AGENTS_DIR = "agents"
_WORKFLOW_FILE = "workflow.yaml"
_STREAM_SUFFIX = ".stream.jsonl"
_META_SUFFIX = ".meta.yaml"
_RESULT_SUFFIX = ".result.json"
_CONFIG_SUFFIX = ".config.json"
_INV_SLOT_TEMPLATE = "inv_{seq:03d}"
_MAX_INV_SLOT = 999
_INV_SLOT_KEEP = 100
_INV_ID_RE = re.compile(r"^inv_\d{3}$")
_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

NodeStatus = Literal["pending", "running", "success", "failed", "interrupted"]
WorkflowStatus = Literal["pending", "running", "success", "failed", "escalated", "interrupted"]


@dataclass(frozen=True)
class NodeUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    total_cost_usd: float | None = None
    usage_raw: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.usage_raw is not None:
            object.__setattr__(self, "usage_raw", MappingProxyType(dict(self.usage_raw)))


class WorkflowTask(BaseModel):
    id: str
    type: Literal["io.alan.LLMStep"] = LLMSTEP_TYPE
    role: str
    model: str
    effort: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: NodeStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stream_path: str | None = None
    dispatched: list[str] = Field(default_factory=list)
    error: str | None = None
    claude_session_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    total_cost_usd: float | None = None
    usage_raw: dict[str, Any] | None = None


class WorkflowDoc(BaseModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"] = (
        SCHEMA_VERSION
    )
    id: str
    namespace: str
    team: str
    source_task_id: str | None = None
    status: WorkflowStatus
    started_at: datetime
    completed_at: datetime | None = None
    slot_index: int | None = None
    base_ref_sha: str | None = None
    tasks: list[WorkflowTask] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


class WorkflowError(RuntimeError):
    pass


class PendingTaskSpec(BaseModel):
    id: str
    role: str
    model: str
    effort: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class WorkflowGraph:
    def __init__(
        self, *, task_id: str, inv_id: str, team: str, source_task_id: str | None = None
    ) -> None:
        self._doc = WorkflowDoc(
            id=inv_id,
            namespace=f"alan.workspace.{task_id}",
            team=team,
            status="running",
            started_at=datetime.now(UTC),
            source_task_id=source_task_id,
        )
        self._by_id: dict[str, WorkflowTask] = {}
        # Guards only the graph-mutate-and-save seam; subprocess spawn stays outside the lock.
        self.lock = threading.Lock()

    @classmethod
    def from_doc(cls, doc: WorkflowDoc) -> WorkflowGraph:
        graph = cls.__new__(cls)
        graph._doc = doc
        graph._doc.status = "running"
        graph._by_id = {t.id: t for t in doc.tasks}
        graph.lock = threading.Lock()
        return graph

    @property
    def doc(self) -> WorkflowDoc:
        return self._doc

    @property
    def slot_index(self) -> int | None:
        return self._doc.slot_index

    def set_slot_index(self, idx: int) -> None:
        self._doc.slot_index = idx

    def set_base_ref_sha(self, sha: str) -> None:
        self._doc.base_ref_sha = sha
        self._doc.schema_version = "1.7"

    def start_node(
        self,
        node_id: str,
        *,
        role: str,
        model: str,
        effort: str | None = None,
        depends_on: list[str] | None = None,
    ) -> WorkflowTask:
        _validate_node_id(node_id)
        existing = self._by_id.get(node_id)
        if existing is not None:
            return _activate_pending(existing, role=role, model=model, effort=effort)
        task = WorkflowTask(
            id=node_id,
            role=role,
            model=model,
            effort=effort,
            depends_on=list(depends_on or []),
            status="running",
            started_at=datetime.now(UTC),
            stream_path=f"{_AGENTS_DIR}/{node_id}{_STREAM_SUFFIX}",
        )
        self._by_id[node_id] = task
        self._doc.tasks.append(task)
        return task

    def complete_node(
        self,
        node_id: str,
        *,
        dispatched: list[str] | None = None,
        usage: NodeUsage | None = None,
    ) -> WorkflowTask:
        task = self._require(node_id)
        task.status = "success"
        task.completed_at = datetime.now(UTC)
        if dispatched is not None:
            task.dispatched = list(dispatched)
        if usage is not None:
            task.input_tokens = usage.input_tokens
            task.output_tokens = usage.output_tokens
            task.cache_creation_input_tokens = usage.cache_creation_input_tokens
            task.cache_read_input_tokens = usage.cache_read_input_tokens
            task.total_cost_usd = usage.total_cost_usd
            task.usage_raw = dict(usage.usage_raw) if usage.usage_raw is not None else None
        return task

    def fail_node(
        self, node_id: str, *, error: str | None = None
    ) -> WorkflowTask:
        task = self._require(node_id)
        task.status = "failed"
        task.completed_at = datetime.now(UTC)
        if error is not None:
            task.error = error
        return task

    def set_session_id(self, node_id: str, session_id: str) -> WorkflowTask:
        task = self._require(node_id)
        task.claude_session_id = session_id
        return task

    def set_dispatched(self, node_id: str, dispatched: list[str]) -> WorkflowTask:
        task = self._require(node_id)
        task.dispatched = list(dispatched)
        return task

    def mark_interrupted(
        self, node_id: str, *, error: str | None = None
    ) -> WorkflowTask:
        task = self._require(node_id)
        task.status = "interrupted"
        if error is not None:
            task.error = error
        return task

    def resume_node(self, node_id: str) -> WorkflowTask:
        task = self._require(node_id)
        if task.status != "interrupted":
            raise WorkflowError(
                f"cannot resume node {node_id!r}: status={task.status!r}"
            )
        task.status = "running"
        task.completed_at = None
        task.error = None
        return task

    def finalize(self, *, status: WorkflowStatus) -> WorkflowDoc:
        self._doc.status = status
        self._doc.completed_at = datetime.now(UTC)
        return self._doc

    def _require(self, node_id: str) -> WorkflowTask:
        try:
            return self._by_id[node_id]
        except KeyError as exc:
            raise WorkflowError(f"unknown node: {node_id}") from exc


def _activate_pending(
    task: WorkflowTask, *, role: str, model: str, effort: str | None = None
) -> WorkflowTask:
    if task.status != "pending":
        raise WorkflowError(
            f"duplicate node id: {task.id} (status={task.status})"
        )
    task.status = "running"
    task.role = role
    task.model = model
    task.effort = effort
    task.started_at = datetime.now(UTC)
    task.error = None
    return task


def seed_pending_workflow(
    workspace: Workspace,
    *,
    team: str,
    tasks: list[PendingTaskSpec],
    source_task_id: str | None = None,
) -> str:
    inv_id = create_invocation_dir(workspace)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{workspace.task_id}",
        team=team,
        source_task_id=source_task_id,
        status="pending",
        started_at=datetime.now(UTC),
        tasks=[_pending_task(spec) for spec in tasks],
    )
    save_workflow(workspace, doc)
    return inv_id


def reset_failed_nodes_for_rerun(workspace: Workspace, inv_id: str) -> None:
    try:
        doc = load_workflow(workflow_path(workspace, inv_id))
    except (WorkflowError, OSError):
        return
    mutated = False
    for task in doc.tasks:
        if task.status in ("failed", "running"):
            task.status = "pending"
            task.error = None
            task.started_at = None
            task.completed_at = None
            task.claude_session_id = None
            mutated = True
    if not mutated:
        return
    try:
        save_workflow(workspace, doc)
    except OSError:
        return


def load_graph(workspace: Workspace, inv_id: str) -> WorkflowGraph:
    doc = load_workflow(workflow_path(workspace, inv_id))
    return WorkflowGraph.from_doc(doc)


def _pending_task(spec: PendingTaskSpec) -> WorkflowTask:
    _validate_node_id(spec.id)
    return WorkflowTask(
        id=spec.id,
        role=spec.role,
        model=spec.model,
        effort=spec.effort,
        depends_on=list(spec.depends_on),
        status="pending",
        stream_path=f"{_AGENTS_DIR}/{spec.id}{_STREAM_SUFFIX}",
    )


def create_invocation_dir(ws: Workspace) -> str:
    if not ws.path.is_dir():
        raise WorkflowError(f"workspace directory missing: {ws.task_id}")
    base = ws.path / _INVOCATIONS_DIR
    # A delete racing the is_dir() guard raises WorkflowError, never re-creates the tree.
    try:
        base.mkdir(exist_ok=True)
        for seq in range(1, _MAX_INV_SLOT + 1):
            candidate = base / _INV_SLOT_TEMPLATE.format(seq=seq)
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            (candidate / _AGENTS_DIR).mkdir()
            return candidate.name
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.EINVAL, errno.EEXIST):
            raise
        raise WorkflowError(f"workspace directory missing: {ws.task_id}") from exc
    raise WorkflowError(f"no free invocation slot in {ws.task_id}")


def prune_invocation_dirs(ws: Workspace) -> int:
    base = ws.path / _INVOCATIONS_DIR
    removed = 0
    try:
        stamped = sorted(
            (path.stat().st_mtime, path)
            for path in base.iterdir()
            if _INV_ID_RE.match(path.name)
        )
        for _, stale in stamped[: max(len(stamped) - _INV_SLOT_KEEP, 0)]:
            shutil.rmtree(stale)
            removed += 1
    except OSError:
        return removed
    return removed


def workflow_path(ws: Workspace, inv_id: str) -> Path:
    _validate_inv_id(inv_id)
    return _inv_dir(ws, inv_id) / _WORKFLOW_FILE


def stream_path(ws: Workspace, inv_id: str, node_id: str) -> Path:
    _validate_inv_id(inv_id)
    _validate_node_id(node_id)
    return _inv_dir(ws, inv_id) / _AGENTS_DIR / f"{node_id}{_STREAM_SUFFIX}"


def agent_meta_path(ws: Workspace, inv_id: str, node_id: str) -> Path:
    _validate_inv_id(inv_id)
    _validate_node_id(node_id)
    return _inv_dir(ws, inv_id) / _AGENTS_DIR / f"{node_id}{_META_SUFFIX}"


def result_path(ws: Workspace, inv_id: str, node_id: str) -> Path:
    _validate_inv_id(inv_id)
    _validate_node_id(node_id)
    return _inv_dir(ws, inv_id) / _AGENTS_DIR / f"{node_id}{_RESULT_SUFFIX}"


_RESULT_FILE_MODE = 0o600


def save_node_result(
    ws: Workspace,
    inv_id: str,
    node_id: str,
    *,
    role: str,
    model: str,
    result_text: str,
) -> Path:
    """Persist the node's final `result_text` atomically with owner-only
    permissions. The cache contents can carry task/code details, so the
    on-disk mode is locked to `0o600` (set via `Path.chmod` after the
    initial write — a momentary window where the file has the process
    umask is acceptable for files inside `~/.alan/workspaces`)."""
    if not ws.path.is_dir():
        raise WorkflowError(f"workspace directory missing: {ws.task_id}")
    path = result_path(ws, inv_id, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "role": role,
        "model": model,
        "result_text": result_text,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    store.write_text_atomic(
        path, json.dumps(payload, ensure_ascii=False), mode=_RESULT_FILE_MODE
    )
    return path


def invalidate_node_result(ws: Workspace, inv_id: str, node_id: str) -> None:
    """Best-effort delete of a cached `<role>.result.json` — used when a
    downstream validator (e.g. orchestrator JSON parser) rejects the
    cached output and we don't want the bad cache replayed on the next
    resume."""
    try:
        result_path(ws, inv_id, node_id).unlink(missing_ok=True)
    except OSError:
        return


def load_node_result(ws: Workspace, inv_id: str, node_id: str) -> str | None:
    """Return the cached `result_text` for a completed node, or None when
    the cache file is missing/malformed/empty."""
    path = result_path(ws, inv_id, node_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    payload = cast(dict[str, Any], parsed)
    text = payload.get("result_text")
    if not isinstance(text, str) or not text:
        return None
    return text


class NodeResultDoc(TypedDict):
    role: str
    model: str
    result_text: str
    completed_at: str


class NodeConfigDoc(TypedDict):
    system_prompt: str
    user_text: str
    allowed_tools: list[str]
    max_turns: int
    permission_mode: str
    model: str
    turns_source: str
    derived_max_turns: int
    launch: NotRequired[LaunchRecord]


def read_node_result_doc(path: Path) -> NodeResultDoc | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    doc = cast(dict[str, Any], parsed)
    str_keys_r = ("role", "model", "result_text", "completed_at")
    if not all(isinstance(doc.get(k), str) for k in str_keys_r):
        return None
    return NodeResultDoc(
        role=doc["role"],
        model=doc["model"],
        result_text=doc["result_text"],
        completed_at=doc["completed_at"],
    )


def load_node_result_doc(
    ws: Workspace, inv_id: str, node_id: str
) -> NodeResultDoc | None:
    return read_node_result_doc(result_path(ws, inv_id, node_id))


def config_path(ws: Workspace, inv_id: str, node_id: str) -> Path:
    _validate_inv_id(inv_id)
    _validate_node_id(node_id)
    return _inv_dir(ws, inv_id) / _AGENTS_DIR / f"{node_id}{_CONFIG_SUFFIX}"


def save_node_config(
    ws: Workspace,
    inv_id: str,
    node_id: str,
    *,
    system_prompt: str,
    user_text: str,
    allowed_tools: list[str],
    max_turns: int,
    permission_mode: str,
    model: str,
    turns_source: str,
    derived_max_turns: int,
    launch: LaunchRecord | None = None,
) -> Path:
    if not ws.path.is_dir():
        raise WorkflowError(f"workspace directory missing: {ws.task_id}")
    path = config_path(ws, inv_id, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: NodeConfigDoc = {
        "system_prompt": system_prompt,
        "user_text": user_text,
        "allowed_tools": allowed_tools,
        "max_turns": max_turns,
        "permission_mode": permission_mode,
        "model": model,
        "turns_source": turns_source,
        "derived_max_turns": derived_max_turns,
    }
    if launch is not None:
        payload["launch"] = launch
    store.write_text_atomic(
        path, json.dumps(payload, ensure_ascii=False), mode=_RESULT_FILE_MODE
    )
    return path


def save_node_launch(
    ws: Workspace, inv_id: str, node_id: str, launch: LaunchRecord
) -> Path | None:
    path = config_path(ws, inv_id, node_id)
    doc = read_node_config(path)
    if doc is None:
        return None
    doc["launch"] = launch
    store.write_text_atomic(path, json.dumps(doc, ensure_ascii=False), mode=_RESULT_FILE_MODE)
    return path


_REPORT_FILE = "report.md"


def report_path(ws: Workspace, inv_id: str) -> Path:
    _validate_inv_id(inv_id)
    return _inv_dir(ws, inv_id) / _REPORT_FILE


def save_invocation_report(ws: Workspace, inv_id: str, text: str) -> Path:
    """Persist the full synthesis report atomically with owner-only
    permissions, matching `save_node_result`/`save_node_config` — the
    report can carry task/code details."""
    if not ws.path.is_dir():
        raise WorkflowError(f"workspace directory missing: {ws.task_id}")
    path = report_path(ws, inv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(path, text, mode=_RESULT_FILE_MODE)
    return path


def _tools_are_valid(tools: object) -> bool:
    if not isinstance(tools, list):
        return False
    return all(isinstance(t, str) for t in tools)  # pyright: ignore[reportUnknownVariableType]


def _node_config_fields_valid(doc: dict[str, Any]) -> bool:
    str_keys = ("system_prompt", "user_text", "permission_mode", "model", "turns_source")
    if not all(isinstance(doc.get(k), str) for k in str_keys):
        return False
    if not _tools_are_valid(doc.get("allowed_tools")):
        return False
    return isinstance(doc.get("max_turns"), int) and isinstance(doc.get("derived_max_turns"), int)


def _parse_node_config(raw: str) -> NodeConfigDoc | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    doc = cast(dict[str, Any], parsed)
    if not _node_config_fields_valid(doc):
        return None
    config = NodeConfigDoc(
        system_prompt=doc["system_prompt"],
        user_text=doc["user_text"],
        allowed_tools=list(doc["allowed_tools"]),
        max_turns=int(doc["max_turns"]),
        permission_mode=doc["permission_mode"],
        model=doc["model"],
        turns_source=doc["turns_source"],
        derived_max_turns=int(doc["derived_max_turns"]),
    )
    launch = parse_launch(doc.get("launch"))
    if launch is not None:
        config["launch"] = launch
    return config


def read_node_config(path: Path) -> NodeConfigDoc | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return _parse_node_config(raw)


def load_node_config(ws: Workspace, inv_id: str, node_id: str) -> NodeConfigDoc | None:
    return read_node_config(config_path(ws, inv_id, node_id))


def _validate_inv_id(inv_id: str) -> None:
    if not _INV_ID_RE.fullmatch(inv_id):
        raise WorkflowError(f"invalid invocation id: {inv_id!r}")


def _validate_node_id(node_id: str) -> None:
    if not _NODE_ID_RE.fullmatch(node_id):
        raise WorkflowError(f"invalid node id: {node_id!r}")


def invocations_dir(ws_path: Path) -> Path:
    return ws_path / _INVOCATIONS_DIR


def workflow_file(ws_path: Path, inv_id: str) -> Path:
    return ws_path / _INVOCATIONS_DIR / inv_id / _WORKFLOW_FILE


def list_invocation_ids(ws_path: Path) -> list[str]:
    return [
        entry.name
        for entry in store.list_dir(invocations_dir(ws_path))
        if (entry / _WORKFLOW_FILE).is_file()
    ]


def list_invocations(ws: Workspace) -> list[str]:
    return list_invocation_ids(ws.path)


def save_workflow(ws: Workspace, doc: WorkflowDoc) -> Path:
    if not ws.path.is_dir():
        raise WorkflowError(f"workspace directory missing: {ws.task_id}")
    path = workflow_path(ws, doc.id)
    _write_yaml(path, doc.model_dump(mode="json"))
    return path


def load_workflow(path: Path) -> WorkflowDoc:
    raw = _read_text(path)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path.name}: invalid YAML") from exc
    if not isinstance(data, dict):
        raise WorkflowError(f"{path.name}: expected mapping at root")
    try:
        return WorkflowDoc.model_validate(data)
    except ValidationError as exc:
        raise WorkflowError(f"{path.name}: schema mismatch") from exc


_WORKFLOW_CACHE: dict[Path, tuple[int, int, WorkflowDoc]] = {}


def load_workflow_cached(path: Path) -> WorkflowDoc:
    try:
        st = path.stat()
    except OSError:
        return load_workflow(path)
    hit = store.stat_cache_hit(_WORKFLOW_CACHE, path, st)
    if hit is not None:
        return copy.deepcopy(hit[0])
    doc = load_workflow(path)
    store.stat_cache_put(_WORKFLOW_CACHE, path, st, doc)
    return copy.deepcopy(doc)


ActiveInterval = tuple[datetime, datetime | None]


def _node_active_interval(
    task: WorkflowTask, doc_completed_at: datetime | None
) -> ActiveInterval | None:
    if task.model == SENTINEL_MODEL or task.started_at is None:
        return None
    if task.completed_at is not None:
        return (task.started_at, task.completed_at)
    if doc_completed_at is not None:
        return (task.started_at, doc_completed_at)
    return (task.started_at, None)


def _doc_active_intervals(doc: WorkflowDoc) -> tuple[ActiveInterval, ...]:
    return tuple(
        interval
        for interval in (_node_active_interval(t, doc.completed_at) for t in doc.tasks)
        if interval is not None
    )


@dataclass(frozen=True)
class WorkflowRowSummary:
    team: str
    status: WorkflowStatus
    cost: float | None
    active_intervals: tuple[ActiveInterval, ...] = ()


def _doc_row_summary(doc: WorkflowDoc) -> WorkflowRowSummary:
    additions = [t.total_cost_usd for t in doc.tasks if t.total_cost_usd is not None]
    cost = sum(additions) if additions else None
    return WorkflowRowSummary(
        team=doc.team,
        status=doc.status,
        cost=cost,
        active_intervals=_doc_active_intervals(doc),
    )


_WORKFLOW_SUMMARY_CACHE: dict[Path, tuple[int, int, WorkflowRowSummary]] = {}


def load_workflow_summary_cached(path: Path) -> WorkflowRowSummary:
    try:
        st = path.stat()
    except OSError:
        return _doc_row_summary(load_workflow(path))
    hit = store.stat_cache_hit(_WORKFLOW_SUMMARY_CACHE, path, st)
    if hit is not None:
        return hit[0]
    summary = _doc_row_summary(load_workflow(path))
    store.stat_cache_put(_WORKFLOW_SUMMARY_CACHE, path, st, summary)
    return summary


def _inv_dir(ws: Workspace, inv_id: str) -> Path:
    return ws.path / _INVOCATIONS_DIR / inv_id


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(
        path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True), mode=_RESULT_FILE_MODE
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WorkflowError(f"missing file: {path.name}") from exc
