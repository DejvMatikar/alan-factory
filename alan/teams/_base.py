from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

from alan import agent_key, store, turn_policy
from alan import workflow as workflow_mod
from alan.claude_cli import (
    UNTRUSTED_CONTENT_NOTICE as UNTRUSTED_CONTENT_NOTICE,
)
from alan.claude_cli import (
    AgentStreamingResult,
    OnEvent,
    OnProcStarted,
    ProcessConfig,
    StreamEvent,
    format_user_text,
    run_agent_streaming,
)
from alan.compression import DEFAULT_MAX_CHARS, load_cross_team_prior_outputs
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig
from alan.fleet_load import FLEET_IDLE_TIMEOUT_SECONDS
from alan.handover import ArtifactRef, Handover, OpenQuestion
from alan.launch_spec import resolve_effort
from alan.roots import logs_root
from alan.teams import _node_launch
from alan.teams._audit import append_jsonl_audit
from alan.teams._node_launch import PreparedNodeContext as _PreparedNodeContext
from alan.teams._node_launch import TurnBudget as _TurnsDecision
from alan.teams._usage import node_usage_from_dict as _node_usage_from_dict
from alan.teams._worker_record_hook import worker_record
from alan.workflow import PendingTaskSpec, WorkflowGraph, WorkflowTask
from alan.workspace import Workspace, revisit_key
from alan.worktree import is_repo_checkout

_HANDOVER_ID_PREFIX = "handover"
_DEFAULT_INVOCATION_ID = "inv_001"

MAX_TURNS: int = 150

_ADAPTIVE_TURNS_ENV = "ALAN_ADAPTIVE_TURNS"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

_NODE_TURNS_AUDIT_FILE = "node_turns_audit.jsonl"

BASE_ALLOWED_TOOLS: tuple[str, ...] = (*_ENGINE_DEFAULT.base_tools,)


def render_base_tools(cfg: EngineConfig) -> tuple[str, ...]:
    return tuple(cfg.base_tools) if cfg.base_tools else BASE_ALLOWED_TOOLS


def render_allowed_tools(cfg: EngineConfig, team_name: str) -> tuple[str, ...]:
    if not cfg.base_tools and not cfg.team_tools:
        return BASE_ALLOWED_TOOLS + tuple(_ENGINE_DEFAULT.team_tools.get(team_name, []))
    return render_base_tools(cfg) + tuple(cfg.team_tools.get(team_name, []))


TERSENESS_BLOCK = (
    "OUTPUT STYLE — be terse: short sentences, no preamble, no filler phrases "
    '("I will now...", "In summary...", "As mentioned..."), no repeating the task '
    "description. Use headers and bullets where they save space. "
    "PRESERVE ALL machine-parsed structured blocks EXACTLY as specified: "
    "fenced ```yaml <name>``` blocks (decisions.yaml, test_scenarios.yaml, "
    "decomposition.yaml, non_actionable_feedback.yaml, findings, patterns, advisory), "
    "VERDICT: / DIFF_FINDINGS_CRITICAL: / EXTRACT_DISPATCH_YAML "
    "/ INLINE_FINDINGS_YAML sentinels, AGENT_STATUS / CONFIDENCE / COMPLETENESS / GAPS "
    "fields, and JSON orchestrator output."
)

UX_LENS = (
    "UX LENS — when touching `alan/web/`, think from the user's "
    "experience: visual hierarchy, affordances, empty/loading/error "
    "states, keyboard and focus handling, and WCAG-AA parity. Reuse the "
    "shared primitives named in `docs/DESIGN_SYSTEM.md` (`Button`, the "
    "shared chat primitives, the modal-key convention) rather than "
    "hand-rolling. If this PR adds a new top-level route/screen, add it "
    "to `alan/web/e2e/mobile-audit/screens.ts` and seed its counts in "
    "`alan/web/e2e/mobile-audit/baseline.json` in the same PR."
)

PLANNING_UX_LENS = (
    "PLANNING UX LENS — when the task concerns `alan/web/`, ground UX "
    "reasoning in `docs/DESIGN_SYSTEM.md`: its tokens, shared primitives, "
    "modal-key convention, and new-screen ratchet rule. Name which shared "
    "primitives the proposal builds on versus what genuinely warrants a "
    "new component."
)

PLANNING_SCREEN_CAPTURE_INSTRUCTION = (
    "PLANNING SCREEN CAPTURE (advisory, best-effort, time-boxed) — when "
    "the task concerns `alan/web/`, before proposing, make one time-boxed "
    "attempt to see the app's current rendered screens: list "
    "`alan/web/screen-captures/pid-*`, run `npm --prefix alan/web run "
    "capture:screens`, then list again — the pid directory that is new is "
    "this invocation's own rendered output. Never read a directory that "
    "was already there: several planners can run in this shared checkout "
    "at once, and a pre-existing pid directory may belong to, and still "
    "be written by, another run. If zero or more than one new directory "
    "appears, or the run is slow or fails, treat capture as unavailable. "
    "Read the PNGs from your own run's directory and ground the proposal "
    "in the app's real layout, density, and existing affordances instead "
    "of inferring everything from markup. If capture is unavailable, "
    "write exactly `capture unavailable — reviewed from markup` and "
    "continue from the code — a static screenshot can only evidence "
    "composition and visual density, never the empty, loading, or error "
    "states, modals, or focus behaviour the UX vocabulary names, for "
    "which component source remains the ground truth. A missing, failed, "
    "or skipped capture must never block, escalate, delay, raise "
    "severity, or change a proposal's content, and must never become a "
    "plan step, a decision entry, an open-question blocker, or a "
    "finding — keep this observation as free prose, never inside a "
    "fenced yaml block or any other structured, machine-parsed field."
)


def compose_system_prompt(
    static_block: str,
    *,
    terse: bool = False,
    dynamic_block: str = "",
) -> str:
    parts = [static_block.strip()]
    if terse:
        parts.append(TERSENESS_BLOCK)
    if dynamic_block.strip():
        parts.append(dynamic_block.strip())
    return "\n\n".join(parts)


class TeamEvent(NamedTuple):
    kind: str
    payload: dict[str, str]


EmitFn = Callable[[TeamEvent], None]


_DISPATCH_INSTRUCTIONS_HEADER = "## User dispatch instructions"


def format_dispatch_instructions(instructions: str | None) -> str:
    if instructions is None:
        return ""
    cleaned = instructions.strip()
    if not cleaned:
        return ""
    return f"\n\n{_DISPATCH_INSTRUCTIONS_HEADER}\n\n{cleaned}\n"


def is_revisit(workspace: Workspace, team: str, chain_id: str) -> bool:
    key = revisit_key(workspace.workspace_id, workspace.meta.task_id, team, chain_id)
    return workspace.meta.revisit_counts.get(key, 0) > 0


def build_prior_context_user_text(
    team: str,
    workspace: Workspace,
    prior: Handover | None,
    instructions: str | None = None,
) -> str:
    base = format_user_text(workspace.meta.title, workspace.meta.description)
    base = base + format_dispatch_instructions(instructions)
    if prior is None:
        return base
    prior_block = (
        f"Prior handover from {prior.from_team} "
        f"(iteration {prior.iteration}):\n{prior.full_synthesis[:DEFAULT_MAX_CHARS]}"
    )
    if prior.iteration <= 1:
        return f"{base}\n\n{prior_block}"
    cross_team = load_cross_team_prior_outputs(
        workspace,
        team,
        decisions_only=is_revisit(workspace, team, prior.handover_chain_id),
    )
    if not cross_team:
        return f"{base}\n\n{prior_block}"
    return f"{base}\n\nPrior context for {team}:\n{cross_team}\n\n{prior_block}"


class Team(Protocol):
    @property
    def name(self) -> str: ...  # pragma: no cover

    @property
    def supports_resume(self) -> bool: ...  # pragma: no cover

    def plan_workflow(  # pragma: no cover
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]: ...

    def run(  # pragma: no cover
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover: ...


def make_handover_id(task_id: str) -> str:
    return f"{_HANDOVER_ID_PREFIX}_{task_id}_{uuid.uuid4().hex[:8]}"


def chain_id_for(prior: Handover | None, new_id: str) -> str:
    return prior.handover_chain_id if prior is not None else new_id


def build_forward_handover(
    workspace: Workspace,
    *,
    from_team: str,
    to_team_suggestions: list[str],
    summary: str,
    prior: Handover | None,
    invocation_id: str = _DEFAULT_INVOCATION_ID,
    open_questions: list[OpenQuestion] | None = None,
    artifacts_produced: list[ArtifactRef] | None = None,
    verifier_completeness: float | None = None,
    verifier_confidence: float | None = None,
    verifier_gaps: list[str] | None = None,
) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=from_team,
        from_invocation_id=invocation_id,
        to_team_suggestions=list(to_team_suggestions),
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        test_scenarios=list(prior.test_scenarios) if prior is not None else [],
        open_questions=open_questions or [],
        artifacts_produced=artifacts_produced or [],
        verifier_completeness=verifier_completeness,
        verifier_confidence=verifier_confidence,
        verifier_gaps=verifier_gaps or [],
    )


def build_terminal_handover(
    workspace: Workspace,
    *,
    from_team: str,
    summary: str,
    prior: Handover | None,
    invocation_id: str = _DEFAULT_INVOCATION_ID,
    pr_number: int | None = None,
    artifacts_produced: list[ArtifactRef] | None = None,
) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="terminal",
        task_id=workspace.task_id,
        from_team=from_team,
        from_invocation_id=invocation_id,
        to_team_suggestions=[],
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        pr_number=pr_number,
        artifacts_produced=artifacts_produced or [],
    )


@contextmanager
def workflow_lifecycle(
    workspace: Workspace,
    *,
    team_name: str,
    inv_id: str,
    emit: EmitFn,
) -> Generator[WorkflowGraph, None, None]:
    graph = workflow_mod.load_graph(workspace, inv_id)
    if graph.doc.team != team_name:
        raise workflow_mod.WorkflowError(
            f"team mismatch for {inv_id}: {graph.doc.team!r} != {team_name!r}"
        )
    workflow_mod.save_workflow(workspace, graph.doc)
    try:
        yield graph
    except BaseException as exc:
        _finalize_failed(workspace, graph, emit, inv_id, error=str(exc))
        raise
    _finalize_success(workspace, graph)


def _finalize_success(workspace: Workspace, graph: WorkflowGraph) -> None:
    graph.finalize(status="success")
    workflow_mod.save_workflow(workspace, graph.doc)


def _finalize_failed(
    workspace: Workspace,
    graph: WorkflowGraph,
    emit: EmitFn,
    inv_id: str,
    *,
    error: str,
) -> None:
    failed = mark_running_nodes_failed(graph, error=error)
    graph.finalize(status="failed")
    workflow_mod.save_workflow(workspace, graph.doc)
    for task in failed:
        emit_node(emit, "node_completed", inv_id, task)


def mark_running_nodes_failed(
    graph: WorkflowGraph, *, error: str | None = None
) -> list[WorkflowTask]:
    failed: list[WorkflowTask] = []
    for task in graph.doc.tasks:
        if task.status == "running":
            failed.append(graph.fail_node(task.id, error=error))
    return failed


def emit_node(emit: EmitFn, kind: str, inv_id: str, task: WorkflowTask) -> None:
    emit(
        TeamEvent(
            kind,
            {
                "inv_id": inv_id,
                "role": task.role,
                "node": task.model_dump_json(),
            },
        ),
    )


def _stamp_task_id(emit: EmitFn, task_id: str) -> EmitFn:
    def _stamped(event: TeamEvent) -> None:
        emit(TeamEvent(event.kind, {**event.payload, "task_id": task_id}))

    return _stamped


def stream_emitter(emit: EmitFn, inv_id: str, role: str) -> OnEvent:
    def _emit(event: StreamEvent) -> None:
        emit(
            TeamEvent(
                "stream",
                {"inv_id": inv_id, "role": role, "event": json.dumps(event)},
            ),
        )

    return _emit


@dataclass(frozen=True)
class WorkerOverrides:
    cwd: Path | None = None
    allowed_tools: list[str] | None = None
    add_dirs: list[str] | None = None
    permission_mode: str | None = None
    max_turns: int | None = None


def node_turns_audit_dir() -> Path:
    return logs_root()


_GROUNDING_AUDIT_FILE = "grounding_audit.jsonl"


def grounding_audit_path() -> Path:
    return node_turns_audit_dir() / _GROUNDING_AUDIT_FILE


class UngroundedRunError(RuntimeError):
    pass


def _require_grounded_cwd(
    overrides: WorkerOverrides | None,
    *,
    task_id: str,
    inv_id: str,
    team: str,
    role: str,
    node_id: str,
) -> None:
    if overrides is None or overrides.cwd is None or is_repo_checkout(overrides.cwd):
        return
    reason = "cwd override is not a valid git repository checkout"
    append_jsonl_audit(
        grounding_audit_path(),
        {
            "event": "ungrounded_cwd_blocked",
            "task_id": task_id,
            "inv_id": inv_id,
            "team": team,
            "role": role,
            "node_id": node_id,
            "cwd": str(overrides.cwd),
            "reason": reason,
        },
        log_label="grounding",
    )
    raise UngroundedRunError(
        f"{team}/{role} node {node_id!r}: {reason} ({overrides.cwd})"
    )


def _coerce_turns(raw: object) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return None


def _turns_entry_fields(node: WorkflowTask | None, *, hit_ceiling: bool) -> dict[str, object]:
    fields: dict[str, object] = {}
    if node is not None:
        fields["role"] = node.role
    if hit_ceiling:
        fields["hit_ceiling"] = True
        return fields
    if node is None or node.usage_raw is None:
        return fields
    turns = _coerce_turns(node.usage_raw.get("num_turns"))
    if turns is not None:
        fields["turns"] = turns
    return fields


def emit_node_turns_audit(
    task_id: str,
    inv_id: str,
    team: str,
    graph: WorkflowGraph,
    node_id: str,
    *,
    hit_ceiling: bool = False,
) -> None:
    node = next((t for t in graph.doc.tasks if t.id == node_id), None)
    entry: dict[str, object] = {
        "task_id": task_id,
        "inv_id": inv_id,
        "team": team,
        **_turns_entry_fields(node, hit_ceiling=hit_ceiling),
    }
    append_jsonl_audit(
        node_turns_audit_dir() / _NODE_TURNS_AUDIT_FILE, entry, log_label="node_turns"
    )


def _adaptive_turns_enabled() -> bool:
    return os.environ.get(_ADAPTIVE_TURNS_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


def _parse_turns_row(line: str) -> tuple[str, int] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    row = cast(dict[str, Any], parsed)
    if row.get("hit_ceiling") is True:
        return None
    role = row.get("role")
    turns = _coerce_turns(row.get("turns"))
    if not isinstance(role, str) or turns is None:
        return None
    return role, turns


def _read_role_turns(path: Path) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return result
    with handle:
        for line in handle:
            parsed = _parse_turns_row(line)
            if parsed is None:
                continue
            role, turns = parsed
            result.setdefault(role, []).append(turns)
    return result


_ROLE_TURNS_CACHE: dict[Path, tuple[int, int, dict[str, list[int]]]] = {}


def _cached_role_turns(path: Path) -> dict[str, list[int]]:
    try:
        st = path.stat()
    except OSError:
        return {}
    hit = store.stat_cache_hit(_ROLE_TURNS_CACHE, path, st)
    if hit is not None:
        return hit[0]
    data = _read_role_turns(path)
    store.stat_cache_put(_ROLE_TURNS_CACHE, path, st, data)
    return data


def _max_turns_for_run(opts: WorkerOverrides, *, role: str) -> _TurnsDecision:
    role_cap = opts.max_turns if opts.max_turns is not None else MAX_TURNS
    if not _adaptive_turns_enabled():
        return _TurnsDecision(applied=role_cap, derived=role_cap, source="static")
    turns_by_role = _cached_role_turns(node_turns_audit_dir() / _NODE_TURNS_AUDIT_FILE)
    history = turn_policy.role_history_from_turns(role, turns_by_role.get(role, ()))
    derived = turn_policy.turn_ceiling(role, role_cap, history)
    return _TurnsDecision(applied=derived, derived=derived, source="adaptive")


def _prepare_node_context(
    workspace: Workspace,
    inv_id: str,
    node_id: str,
    *,
    role: str,
    system_prompt: str,
    user_text: str,
    overrides: WorkerOverrides | None,
    model: str,
) -> _PreparedNodeContext:
    opts = overrides or WorkerOverrides()
    return _node_launch.prepare_node_context(
        workspace, inv_id, node_id,
        opts=opts,
        effective_kwargs=_overrides_to_kwargs(opts),
        budget=_max_turns_for_run(opts, role=role),
        system_prompt=system_prompt, user_text=user_text, model=model,
    )


def _start_node_locked(
    workspace: Workspace,
    graph: WorkflowGraph,
    node_id: str,
    *,
    role: str,
    model: str,
    effort: str | None,
    depends_on: list[str],
) -> WorkflowTask:
    # mutate-graph-and-save under lock; spawn-subprocess outside lock — a
    # concurrent stage-group sibling races this same graph/workflow.yaml.
    with graph.lock:
        node = graph.start_node(
            node_id, role=role, model=model, effort=resolve_effort(effort, model),
            depends_on=depends_on,
        )
        workflow_mod.save_workflow(workspace, graph.doc)
    return node


def _node_role_or_id(graph: WorkflowGraph, node_id: str) -> str:
    existing = next((t for t in graph.doc.tasks if t.id == node_id), None)
    return existing.role if existing is not None else node_id


def _begin_node_run(
    workspace: Workspace,
    graph: WorkflowGraph,
    task_id: str | None,
    inv_id: str,
    emit: EmitFn,
    *,
    role: str,
    node_id: str,
    overrides: WorkerOverrides | None,
) -> tuple[str, EmitFn]:
    _task_id = task_id if task_id is not None else workspace.task_id
    _require_grounded_cwd(
        overrides, task_id=_task_id, inv_id=inv_id,
        team=graph.doc.team, role=role, node_id=node_id,
    )
    return _task_id, _stamp_task_id(emit, _task_id)


@dataclass(frozen=True)
class _NodeRunSpec:
    task_id: str
    role: str
    model: str
    effort: str | None


def _stream_node(
    workspace: Workspace, graph: WorkflowGraph, inv_id: str, node_id: str,
    *,
    spec: _NodeRunSpec, emit: EmitFn,
    session_id: str, stream_path: Path, prepared: _PreparedNodeContext,
    user_text: str, on_proc_started: OnProcStarted | None,
    on_session_id: Callable[[str], None] | None = None,
    resume_session_id: str | None = None,
) -> AgentStreamingResult:
    with worker_record(
        workspace, task_id=spec.task_id, team=graph.doc.team, inv_id=inv_id, node_id=node_id,
        role=spec.role, model=spec.model, session_id=session_id, stream_path=stream_path,
        cwd=prepared.opts.cwd, max_turns=prepared.effective_max_turns,
        on_proc_started=on_proc_started,
    ) as hooks:
        return run_agent_streaming(
            prepared.augmented_prompt, user_text, model=spec.model, effort=spec.effort,
            on_event=stream_emitter(emit, inv_id, spec.role), stream_path=stream_path,
            max_turns=prepared.effective_max_turns,
            proc_config=ProcessConfig(
                cwd=prepared.opts.cwd, timeout_seconds=FLEET_IDLE_TIMEOUT_SECONDS
            ),
            session_id=session_id if resume_session_id is None else None,
            on_session_id=on_session_id, resume_session_id=resume_session_id,
            on_proc_started=hooks.on_proc_started, journal_stream=hooks.journal_stream,
            on_journal_opened=hooks.on_journal_opened, fleet=True,
            agent=agent_key.team_role(graph.doc.team, spec.role),
            on_spec=_node_launch.launch_recorder(workspace, inv_id, node_id),
            **prepared.effective_kwargs,
        )


def _stream_and_complete(
    workspace: Workspace, graph: WorkflowGraph, inv_id: str, emit: EmitFn, node_id: str,
    *,
    spec: _NodeRunSpec, prepared: _PreparedNodeContext, user_text: str,
    session_id: str, resume_session_id: str | None,
    on_proc_started: OnProcStarted | None,
    on_session_id: Callable[[str], None] | None = None,
) -> str:
    result = _stream_node(
        workspace, graph, inv_id, node_id,
        spec=spec, emit=emit,
        session_id=session_id,
        stream_path=workflow_mod.stream_path(workspace, inv_id, node_id),
        prepared=prepared, user_text=user_text,
        on_proc_started=on_proc_started, on_session_id=on_session_id,
        resume_session_id=resume_session_id,
    )
    return complete_node(
        workspace, graph, inv_id, emit, node_id, result, role=spec.role, model=spec.model
    )


def _run_fresh_node(
    workspace: Workspace, graph: WorkflowGraph, inv_id: str, emit: EmitFn,
    *,
    node_id: str, role: str, model: str, effort: str | None = None,
    depends_on: list[str], system_prompt: str, user_text: str,
    overrides: WorkerOverrides | None = None, on_proc_started: OnProcStarted | None = None,
) -> str:
    _task_id, _emit = _begin_node_run(
        workspace, graph, None, inv_id, emit, role=role, node_id=node_id, overrides=overrides,
    )
    node = _start_node_locked(
        workspace, graph, node_id, role=role, model=model, effort=effort, depends_on=depends_on
    )
    emit_node(_emit, "node_started", inv_id, node)
    prepared = _prepare_node_context(
        workspace, inv_id, node_id, role=role,
        system_prompt=system_prompt, user_text=user_text, overrides=overrides, model=model,
    )
    on_session_id = _persist_session_id(workspace, graph, node_id)
    return _stream_and_complete(
        workspace, graph, inv_id, _emit, node_id,
        spec=_NodeRunSpec(task_id=_task_id, role=role, model=model, effort=effort),
        prepared=prepared, user_text=user_text,
        session_id=str(uuid.uuid4()), resume_session_id=None,
        on_proc_started=on_proc_started, on_session_id=on_session_id,
    )


def _persist_session_id(
    workspace: Workspace, graph: WorkflowGraph, node_id: str
) -> Callable[[str], None]:
    def _on(sid: str) -> None:
        with graph.lock:
            graph.set_session_id(node_id, sid)
            workflow_mod.save_workflow(workspace, graph.doc)

    return _on


def complete_node(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: EmitFn,
    node_id: str,
    result: AgentStreamingResult,
    *,
    role: str,
    model: str,
) -> str:
    workflow_mod.save_node_result(
        workspace, inv_id, node_id, role=role, model=model, result_text=result.result_text
    )
    with graph.lock:
        done = graph.complete_node(node_id, usage=_node_usage_from_dict(result.usage))
        workflow_mod.save_workflow(workspace, graph.doc)
    emit_node_turns_audit(workspace.task_id, inv_id, graph.doc.team, graph, node_id)
    emit_node(emit, "node_completed", inv_id, done)
    return result.result_text


class ResumeUnsupportedError(RuntimeError):
    """Raised when a team that does not implement multi-node resume is asked
    to resume a prior invocation. The reconciler must guard against this."""


def reject_resume_inv_id(team_name: str, resume_inv_id: str | None) -> None:
    if resume_inv_id is not None:
        raise ResumeUnsupportedError(
            f"{team_name} does not support resume; resume_inv_id={resume_inv_id}"
        )


def _attach_or_resume(
    workspace: Workspace, graph: WorkflowGraph, inv_id: str, emit: EmitFn, *,
    node_id: str, system_prompt: str, user_text: str,
    overrides: WorkerOverrides | None, on_proc_started: OnProcStarted | None,
    task_id: str | None,
) -> str:
    from alan.teams import _node_attach

    resolved_task_id = task_id if task_id is not None else workspace.task_id
    attached = _node_attach.attach(
        workspace, graph, inv_id, emit, node_id=node_id, task_id=resolved_task_id,
    )
    if attached is not None and attached.completed:
        return cast(str, attached.result_text)
    return resume_workflow_node(
        workspace, graph, inv_id, emit, node_id=node_id, system_prompt=system_prompt,
        user_text=user_text, overrides=overrides, on_proc_started=on_proc_started,
        task_id=task_id,
    )


def run_or_resume_node(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: EmitFn,
    *,
    node_id: str,
    role: str,
    model: str,
    depends_on: list[str],
    system_prompt: str,
    user_text: str,
    effort: str | None = None,
    overrides: WorkerOverrides | None = None,
    on_proc_started: OnProcStarted | None = None,
) -> str:
    """Multi-node-team primitive: cached -> attach -> resume -> fresh (see
    `_attach_or_resume` for the middle two branches). `effort` is forwarded
    only to the fresh arm — attach/resume read `node.effort` off the graph.
    No caller threads a non-default `task_id`, so it is not accepted here;
    the two branches below are called with `task_id=None` explicitly."""
    cached = _cached_result_for(workspace, graph, inv_id, node_id)
    if cached is not None:
        return cached
    if node_is_interrupted(graph, node_id):
        return _attach_or_resume(
            workspace, graph, inv_id, emit, node_id=node_id, system_prompt=system_prompt,
            user_text=user_text, overrides=overrides, on_proc_started=on_proc_started,
            task_id=None,
        )
    return _run_fresh_node(
        workspace, graph, inv_id, emit,
        node_id=node_id, role=role, model=model, effort=effort, depends_on=depends_on,
        system_prompt=system_prompt, user_text=user_text,
        overrides=overrides, on_proc_started=on_proc_started,
    )


def _cached_result_for(
    workspace: Workspace, graph: WorkflowGraph, inv_id: str, node_id: str
) -> str | None:
    existing = next((t for t in graph.doc.tasks if t.id == node_id), None)
    if existing is None or existing.status != "success":
        return None
    try:
        doc = workflow_mod.load_node_result_doc(workspace, inv_id, node_id)
    except OSError:
        doc = None
    if doc is None:
        raise workflow_mod.WorkflowError(
            f"node {node_id} recorded success but its result is unreadable (inv={inv_id})"
        )
    return doc["result_text"]


def node_is_interrupted(graph: WorkflowGraph, node_id: str) -> bool:
    existing = next((t for t in graph.doc.tasks if t.id == node_id), None)
    return existing is not None and existing.status == "interrupted"


def resume_workflow_node(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: EmitFn,
    *,
    node_id: str,
    system_prompt: str,
    user_text: str,
    overrides: WorkerOverrides | None = None,
    on_proc_started: OnProcStarted | None = None,
    task_id: str | None = None,
) -> str:
    # Fail fast BEFORE mutating the workflow or emitting `node_started` —
    # an inconsistent on-disk state shouldn't bleed a false node_started
    # into the SSE stream.
    captured_session_id = _require_captured_session_id(graph, node_id)
    _task_id, _emit = _begin_node_run(
        workspace, graph, task_id, inv_id, emit,
        role=_node_role_or_id(graph, node_id), node_id=node_id, overrides=overrides,
    )
    with graph.lock:
        node = graph.resume_node(node_id)
        workflow_mod.save_workflow(workspace, graph.doc)
    emit_node(_emit, "node_started", inv_id, node)
    prepared = _prepare_node_context(
        workspace, inv_id, node_id, role=node.role,
        system_prompt=system_prompt, user_text=user_text, overrides=overrides, model=node.model,
    )
    return _stream_and_complete(
        workspace, graph, inv_id, _emit, node_id,
        spec=_NodeRunSpec(task_id=_task_id, role=node.role, model=node.model, effort=node.effort),
        prepared=prepared, user_text=user_text,
        session_id=captured_session_id, resume_session_id=captured_session_id,
        on_proc_started=on_proc_started,
    )


def _require_captured_session_id(graph: WorkflowGraph, node_id: str) -> str:
    existing = next((t for t in graph.doc.tasks if t.id == node_id), None)
    if existing is None:
        raise workflow_mod.WorkflowError(f"unknown node: {node_id}")
    if not existing.claude_session_id:
        raise workflow_mod.WorkflowError(
            f"cannot resume node {node_id!r}: no claude_session_id captured"
        )
    return existing.claude_session_id


def _overrides_to_kwargs(opts: WorkerOverrides) -> dict[str, Any]:
    tools = opts.allowed_tools if opts.allowed_tools is not None else BASE_ALLOWED_TOOLS
    kwargs: dict[str, Any] = {"allowed_tools": list(tools)}
    if opts.add_dirs is not None:
        kwargs["add_dirs"] = list(opts.add_dirs)
    if opts.permission_mode is not None:
        kwargs["permission_mode"] = opts.permission_mode
    return kwargs


@dataclass(frozen=True)
class SingleAgentTeam:
    name: str
    system_prompt: str
    to_team_suggestions: tuple[str, ...]
    model: str = "sonnet"
    supports_resume: bool = True

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [PendingTaskSpec(id=self.name, role=self.name, model=self.model)]

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del task_id, resume_inv_id
        user_text = self._build_user_text(workspace, prior, instructions)
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            output = run_or_resume_node(
                workspace, graph, inv_id, emit,
                node_id=self.name, role=self.name, model=self.model, depends_on=[],
                system_prompt=self.system_prompt, user_text=user_text,
                on_proc_started=on_proc_started,
            )
        return build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=list(self.to_team_suggestions),
            summary=output,
            prior=prior,
            invocation_id=inv_id,
        )

    def _build_user_text(
        self,
        workspace: Workspace,
        prior: Handover | None,
        instructions: str | None = None,
    ) -> str:
        return build_prior_context_user_text(self.name, workspace, prior, instructions)


__all__ = ["UNTRUSTED_CONTENT_NOTICE"]
