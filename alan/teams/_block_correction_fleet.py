from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from alan import workflow as workflow_mod
from alan.block_correction import CorrectionOutcome, ReAsk, run_correction
from alan.claude_cli import ClaudeCliError, OnProcStarted
from alan.teams._base import EmitFn, WorkerOverrides, resume_workflow_node
from alan.teams._block_correction_audit import record_from_outcome
from alan.workspace import Workspace

T = TypeVar("T")

FLEET_MAX_ATTEMPTS = 3

CorrectionPrompt = Callable[[str], str]


def _captured_session_id(workspace: Workspace, inv_id: str, node_id: str) -> str | None:
    try:
        graph = workflow_mod.load_graph(workspace, inv_id)
    except workflow_mod.WorkflowError:
        return None
    node = next((t for t in graph.doc.tasks if t.id == node_id), None)
    return node.claude_session_id if node is not None else None


def _refinalize(workspace: Workspace, graph: workflow_mod.WorkflowGraph) -> None:
    with graph.lock:
        graph.finalize(status="success")
        workflow_mod.save_workflow(workspace, graph.doc)


def build_re_ask(
    workspace: Workspace,
    inv_id: str,
    node_id: str,
    emit: EmitFn,
    *,
    system_prompt: str,
    overrides: WorkerOverrides | None,
    on_proc_started: OnProcStarted | None,
    correction_prompt: CorrectionPrompt,
) -> ReAsk:
    def _re_ask(attempt_number: int, prior_text: str, error: str) -> str | None:
        del attempt_number, prior_text
        if _captured_session_id(workspace, inv_id, node_id) is None:
            return None
        try:
            graph = workflow_mod.load_graph(workspace, inv_id)
        except workflow_mod.WorkflowError:
            return None
        with graph.lock:
            graph.mark_interrupted(node_id, error=error)
            workflow_mod.save_workflow(workspace, graph.doc)
        try:
            corrected = resume_workflow_node(
                workspace,
                graph,
                inv_id,
                emit,
                node_id=node_id,
                system_prompt=system_prompt,
                user_text=correction_prompt(error),
                overrides=overrides,
                on_proc_started=on_proc_started,
            )
        except (ClaudeCliError, workflow_mod.WorkflowError):
            corrected = None
        _refinalize(workspace, graph)
        return corrected

    return _re_ask


def run_fleet_correction(
    text: str,
    *,
    validate: Callable[[str], T],
    re_ask: ReAsk,
    site: str,
    role: str,
    task_id: str,
    max_attempts: int = FLEET_MAX_ATTEMPTS,
    detect_repair: Callable[[str], bool] | None = None,
) -> CorrectionOutcome[T]:
    outcome = run_correction(text, validate=validate, re_ask=re_ask, max_attempts=max_attempts)
    label = outcome.outcome
    if label == "clean" and detect_repair is not None and detect_repair(text):
        label = "repaired"
    record_from_outcome(
        site=site, block=site, role=role, outcome=outcome, task_id=task_id, outcome_override=label
    )
    return outcome


__all__ = ["FLEET_MAX_ATTEMPTS", "build_re_ask", "run_fleet_correction"]
