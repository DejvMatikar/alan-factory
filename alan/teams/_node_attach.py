from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from alan import worker_records
from alan import workflow as workflow_mod
from alan.claude_cli import (
    AgentStreamingResult,
    ClaudeCliError,
    abs_timeout_seconds,
    idle_timeout_seconds,
)
from alan.claude_cli_journal import (
    AttachSpec,
    OnEvent,
    PidHandle,
    ProcessHandle,
    StreamEvent,
    extract_stream_usage,
    is_error_result,
    journal_err_path,
    last_result_event,
    result_detail,
    tail_to_result,
)
from alan.clock import SYSTEM_CLOCK, Clock
from alan.roots import logs_root
from alan.teams._base import EmitFn, complete_node, emit_node, stream_emitter
from alan.teams._resume_audit import ResumeOutcome, record_resume_outcome
from alan.workflow import WorkflowGraph
from alan.workspace import Workspace

logger = logging.getLogger("alan.teams._node_attach")

_RESUME_AUDIT_FILE = "resume_audit.jsonl"
_ERR_SNIPPET_CHARS = 500


@dataclass(frozen=True)
class AttachOutcome:
    completed: bool
    result_text: str | None
    session_id: str


class _AttachConsumer:
    def __init__(self, on_event: OnEvent) -> None:
        self._on_event = on_event

    def handle(self, line: str, events: list[tuple[str, StreamEvent]]) -> None:
        parsed = json.loads(line)
        events.append((line, parsed))
        self._on_event(parsed)


def _matches_survivor(
    record: worker_records.WorkerRecord, task_id: str, inv_id: str, node_id: str
) -> bool:
    if record.pgid is None:
        return False
    return record.task_id == task_id and record.inv_id == inv_id and record.node_id == node_id


def find_survivor_records(
    task_id: str, inv_id: str, node_id: str
) -> list[worker_records.WorkerRecord]:
    matches = [
        record
        for _path, record in worker_records.load_all()
        if record is not None and _matches_survivor(record, task_id, inv_id, node_id)
    ]
    return sorted(matches, key=lambda r: r.started_at or 0.0, reverse=True)


def _record_attach_audit(
    task_id: str, inv_id: str, node_id: str, outcome: ResumeOutcome, *, team: str
) -> None:
    record_resume_outcome(
        logs_root() / _RESUME_AUDIT_FILE,
        task_id=task_id, team=team, inv_id=inv_id, outcome=outcome, node_id=node_id,
    )


def _remove_record_best_effort(session_id: str) -> None:
    try:
        worker_records.remove_record(session_id)
    except OSError:
        logger.warning("attach: failed to remove record %s", session_id, exc_info=True)


def _terminate_duplicate(record: worker_records.WorkerRecord) -> None:
    # find_survivor_records() only ever returns pgid-not-None records.
    if record.pid is not None:
        try:
            worker_records.guard_no_group_signal(record.pid)
        except worker_records.GroupSignalGuardError:
            logger.warning(
                "attach: refusing to signal own process group for pid=%d", record.pid
            )
            return
    worker_records.terminate_group(cast(int, record.pgid))
    _remove_record_best_effort(record.session_id)


def _kill_on_deadline(
    record: worker_records.WorkerRecord, this_service: tuple[int, str]
) -> Callable[[ProcessHandle], None]:
    def _kill(_handle: ProcessHandle) -> None:
        verdict = worker_records.probe(record, this_service=this_service)
        if verdict is not worker_records.ProbeVerdict.LIVE:
            return
        if record.pid is None or record.pgid is None:
            return
        try:
            worker_records.guard_no_group_signal(record.pid)
        except worker_records.GroupSignalGuardError:
            logger.warning(
                "attach: refusing to signal own process group for pid=%d", record.pid
            )
            return
        worker_records.terminate_group(record.pgid)

    return _kill


def _abs_start(record: worker_records.WorkerRecord, *, clock: Clock, abs_timeout: float) -> float:
    now_mono = clock.monotonic().value
    if record.deadline_at is None:
        return now_mono
    wall_remaining = record.deadline_at - clock.now().value
    return now_mono + wall_remaining - abs_timeout


def _build_spec(
    record: worker_records.WorkerRecord, stream_path: Path, *, clock: Clock
) -> AttachSpec:
    idle_timeout = (
        record.idle_timeout_s if record.idle_timeout_s is not None else idle_timeout_seconds()
    )
    abs_timeout = abs_timeout_seconds()
    return AttachSpec(
        journal_path=stream_path,
        err_path=journal_err_path(stream_path),
        journal_offset=record.journal_offset or 0,
        err_offset=record.err_offset or 0,
        idle_timeout=idle_timeout,
        abs_timeout=abs_timeout,
        idle_start=clock.monotonic().value,
        abs_start=_abs_start(record, clock=clock, abs_timeout=abs_timeout),
    )


def attach(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: EmitFn,
    *,
    node_id: str,
    task_id: str,
    clock: Clock = SYSTEM_CLOCK,
) -> AttachOutcome | None:
    records = find_survivor_records(task_id, inv_id, node_id)
    if not records:
        return None
    primary, *duplicates = records
    for dup in duplicates:
        _terminate_duplicate(dup)
    try:
        return _attach_primary(workspace, graph, inv_id, emit, primary, node_id=node_id,
                                task_id=task_id, clock=clock)
    except OSError:
        logger.exception("attach: unexpected OSError for node=%s inv=%s", node_id, inv_id)
        return _degrade(workspace, graph, primary, node_id=node_id, inv_id=inv_id, task_id=task_id)


def _attach_primary(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: EmitFn,
    primary: worker_records.WorkerRecord,
    *,
    node_id: str,
    task_id: str,
    clock: Clock,
) -> AttachOutcome:
    with graph.lock:
        node = graph.resume_node(node_id)
        workflow_mod.save_workflow(workspace, graph.doc)
    emit_node(emit, "node_started", inv_id, node)

    stream_path = workflow_mod.stream_path(workspace, inv_id, node_id)
    spec = _build_spec(primary, stream_path, clock=clock)
    this_service = worker_records.this_service_identity()
    handle = PidHandle(record=primary, this_service=this_service)
    events: list[tuple[str, StreamEvent]] = []
    consumer = _AttachConsumer(stream_emitter(emit, inv_id, node.role))
    outcome = tail_to_result(
        spec, handle, events, consumer, kill=_kill_on_deadline(primary, this_service),
    )

    last = last_result_event(outcome.events)
    if last is None:
        return _degrade(workspace, graph, primary, node_id=node_id, inv_id=inv_id, task_id=task_id)

    if is_error_result(last):
        _remove_record_best_effort(primary.session_id)
        _record_attach_audit(task_id, inv_id, node_id, "attached", team=graph.doc.team)
        detail = result_detail(last)
        stderr = outcome.stderr.strip()
        if stderr:
            detail = f"{detail}; stderr={stderr[:_ERR_SNIPPET_CHARS]}"
        raise ClaudeCliError(f"claude CLI reported error: {detail}")

    result = AgentStreamingResult(
        result_text=str(last.get("result") or "").strip(),
        usage=extract_stream_usage(outcome.events),
    )
    result_text = complete_node(
        workspace, graph, inv_id, emit, node_id, result, role=node.role, model=node.model,
    )
    _remove_record_best_effort(primary.session_id)
    _record_attach_audit(task_id, inv_id, node_id, "attached", team=graph.doc.team)
    return AttachOutcome(completed=True, result_text=result_text, session_id=primary.session_id)


def _degrade(
    workspace: Workspace,
    graph: WorkflowGraph,
    record: worker_records.WorkerRecord,
    *,
    node_id: str,
    inv_id: str,
    task_id: str,
) -> AttachOutcome:
    try:
        with graph.lock:
            graph.mark_interrupted(node_id, error="attach_lost")
            workflow_mod.save_workflow(workspace, graph.doc)
    except OSError:
        logger.exception("attach: failed to restore interrupted state for node=%s", node_id)
    verdict = worker_records.probe(record, this_service=worker_records.this_service_identity())
    if verdict is worker_records.ProbeVerdict.LIVE:
        _terminate_duplicate(record)
    else:
        _remove_record_best_effort(record.session_id)
    _record_attach_audit(task_id, inv_id, node_id, "attach_lost", team=graph.doc.team)
    return AttachOutcome(completed=False, result_text=None, session_id=record.session_id)


__all__ = ["AttachOutcome", "attach", "find_survivor_records"]
