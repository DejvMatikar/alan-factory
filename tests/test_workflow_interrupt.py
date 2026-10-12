from __future__ import annotations

from pathlib import Path

from alan.workflow import (
    INTERRUPTED_BY_RESTART_ERROR,
    WorkflowGraph,
    WorkflowTask,
    create_invocation_dir,
    load_graph,
    save_workflow,
    seed_pending_workflow,
    workflow_path,
)
from alan.workflow import PendingTaskSpec as _PendingTaskSpec
from alan.workflow_interrupt import interrupt_workflow_on_disk
from alan.workspace import Workspace, create


def _make_ws(ws_root: Path, title: str = "t1") -> Workspace:
    return create(title, "desc", root=ws_root)


def test_interrupt_workflow_on_disk_marks_running_failed(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id)
    assert doc is not None
    task = doc.tasks[0]
    assert task.status == "failed"
    assert task.error == INTERRUPTED_BY_RESTART_ERROR
    assert task.completed_at is not None
    assert doc.status == "failed"
    assert doc.completed_at is not None


def test_interrupt_workflow_on_disk_preserves_completed_nodes(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("a", role="a", model="opus")
    g.complete_node("a")
    g.start_node("b", role="b", model="sonnet", depends_on=["a"])
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id)
    assert doc is not None
    statuses = {t.id: t.status for t in doc.tasks}
    assert statuses == {"a": "success", "b": "failed"}


def test_interrupt_workflow_on_disk_marks_pending_workflow_failed(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="research",
        tasks=[_PendingTaskSpec(id="o", role="o", model="opus")],
    )
    doc = interrupt_workflow_on_disk(ws, inv_id)
    assert doc is not None
    assert doc.status == "failed"
    assert doc.tasks[0].status == "pending"


def test_interrupt_workflow_on_disk_noop_when_already_finalized(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("a", role="a", model="opus")
    g.complete_node("a")
    g.finalize(status="success")
    save_workflow(ws, g.doc)
    mtime_before = workflow_path(ws, inv_id).stat().st_mtime_ns
    doc = interrupt_workflow_on_disk(ws, inv_id)
    assert doc is not None
    assert doc.status == "success"
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime_before


def test_interrupt_workflow_on_disk_missing_returns_none(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    create_invocation_dir(ws)
    assert interrupt_workflow_on_disk(ws, "inv_001") is None


def test_interrupt_workflow_on_disk_custom_error_string(ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, error="custom-reason")
    assert doc is not None
    assert doc.tasks[0].error == "custom-reason"


def test_interrupt_workflow_on_disk_resumable_flips_node_to_interrupted(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-xyz")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    assert doc.tasks[0].status == "interrupted"
    assert doc.tasks[0].claude_session_id == "sid-xyz"
    assert doc.status == "running"
    assert doc.completed_at is None


def test_interrupt_workflow_on_disk_resumable_falls_back_without_session_id(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    assert doc.tasks[0].status == "failed"
    assert doc.status == "failed"


def test_interrupt_workflow_on_disk_mixed_session_ids_defers_sessionless_sibling(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("a", role="a", model="opus")
    g.start_node("b", role="b", model="sonnet")
    g.set_session_id("a", "sid-a")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    statuses = {t.id: t.status for t in doc.tasks}
    assert statuses == {"a": "interrupted", "b": "pending"}
    assert doc.status == "running"
    settled = next(t for t in doc.tasks if t.id == "b")
    assert settled.error == INTERRUPTED_BY_RESTART_ERROR
    assert settled.started_at is None
    assert settled.completed_at is None
    assert settled.claude_session_id is None


def test_interrupt_workflow_on_disk_recover_finalized_running_doc_emits_interrupted(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-live")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(
        ws, inv_id, error="user paused", resumable=True, recover_finalized=True
    )
    assert doc is not None
    assert doc.tasks[0].status == "interrupted"
    assert doc.status == "interrupted"
    assert doc.completed_at is None


def test_interrupt_workflow_on_disk_recover_finalized_second_call_noop(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-live")
    save_workflow(ws, g.doc)
    interrupt_workflow_on_disk(
        ws, inv_id, error="user paused", resumable=True, recover_finalized=True
    )
    mtime = workflow_path(ws, inv_id).stat().st_mtime_ns
    doc2 = interrupt_workflow_on_disk(
        ws, inv_id, error="user paused", resumable=True, recover_finalized=True
    )
    assert doc2 is not None
    assert doc2.status == "interrupted"
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime


def test_interrupt_workflow_on_disk_recover_finalized_rescues_failed_with_session(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid-race")
    g.fail_node("w", error="claude CLI exited 143: stderr=<empty>; stdout=<empty>")
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(
        ws, inv_id, error="user paused", resumable=True, recover_finalized=True
    )
    assert doc is not None
    assert doc.tasks[0].status == "interrupted"
    assert doc.tasks[0].claude_session_id == "sid-race"
    assert doc.tasks[0].completed_at is None
    assert doc.tasks[0].error == "user paused"
    assert doc.status == "interrupted"
    assert doc.completed_at is None


def test_interrupt_workflow_on_disk_recover_finalized_skips_failed_without_session(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.fail_node("w", error="prior failure")
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(
        ws, inv_id, resumable=True, recover_finalized=True
    )
    assert doc is not None
    assert doc.tasks[0].status == "failed"
    assert doc.status == "failed"


def test_interrupt_workflow_on_disk_recover_finalized_mixed_pipeline(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="implementation_plan")
    g.start_node("architect", role="architect", model="opus")
    g.set_session_id("architect", "sid-arch")
    g.complete_node("architect")
    g.start_node("devil_advocate", role="devil_advocate", model="sonnet")
    g.set_session_id("devil_advocate", "sid-devil")
    g.fail_node("devil_advocate", error="claude CLI exited 143")
    g.doc.tasks.append(
        WorkflowTask(
            id="synthesizer",
            role="synthesizer",
            model="opus",
            depends_on=["devil_advocate"],
            status="pending",
            stream_path="agents/synthesizer.stream.jsonl",
        )
    )
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(
        ws, inv_id, error="user paused", resumable=True, recover_finalized=True
    )
    assert doc is not None
    statuses = {t.id: t.status for t in doc.tasks}
    assert statuses == {
        "architect": "success",
        "devil_advocate": "interrupted",
        "synthesizer": "pending",
    }
    assert doc.status == "interrupted"
    assert doc.completed_at is None


def test_interrupt_workflow_on_disk_recover_finalized_default_false_does_not_rescue(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    g.fail_node("w", error="prior")
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    mtime_before = workflow_path(ws, inv_id).stat().st_mtime_ns
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    assert doc.tasks[0].status == "failed"
    assert doc.status == "failed"
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime_before


def test_interrupt_workflow_on_disk_non_resumable_does_not_rescue_failed(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    g.fail_node("w", error="prior")
    g.finalize(status="failed")
    save_workflow(ws, g.doc)
    mtime_before = workflow_path(ws, inv_id).stat().st_mtime_ns
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=False, recover_finalized=True)
    assert doc is not None
    assert doc.tasks[0].status == "failed"
    assert doc.status == "failed"
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime_before


def test_interrupt_workflow_on_disk_resumable_flips_pending_doc_to_running(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    g.doc.status = "pending"
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    assert doc.tasks[0].status == "interrupted"
    assert doc.status == "running"


def test_interrupt_workflow_on_disk_resumable_idempotent_on_already_interrupted(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    save_workflow(ws, g.doc)
    first = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert first is not None
    assert first.tasks[0].status == "interrupted"
    assert first.status == "running"
    mtime_after_first = workflow_path(ws, inv_id).stat().st_mtime_ns
    second = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert second is not None
    assert second.tasks[0].status == "interrupted"
    assert second.status == "running"
    assert workflow_path(ws, inv_id).stat().st_mtime_ns == mtime_after_first


def test_interrupt_workflow_on_disk_non_resumable_promotes_prior_interrupted(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("w", role="w", model="sonnet")
    g.set_session_id("w", "sid")
    save_workflow(ws, g.doc)
    doc1 = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc1 is not None
    assert doc1.tasks[0].status == "interrupted"
    assert doc1.status == "running"
    doc2 = interrupt_workflow_on_disk(ws, inv_id, resumable=False)
    assert doc2 is not None
    assert doc2.tasks[0].status == "failed"
    assert doc2.tasks[0].completed_at is not None
    assert doc2.status == "failed"


def test_interrupt_workflow_on_disk_non_resumable_ignores_terminal_nodes(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="research")
    g.start_node("a", role="a", model="opus")
    g.complete_node("a")
    g.start_node("b", role="b", model="sonnet", depends_on=["a"])
    g.fail_node("b", error="prior failure")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=False)
    assert doc is not None
    statuses = {t.id: t.status for t in doc.tasks}
    errors = {t.id: t.error for t in doc.tasks}
    assert statuses == {"a": "success", "b": "failed"}
    assert errors["b"] == "prior failure"


def test_interrupt_workflow_on_disk_resumable_multiple_sessionless_siblings_all_recover(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="verification")
    g.start_node("diff_reviewer", role="diff_reviewer", model="sonnet")
    g.start_node("scenario_runner", role="scenario_runner", model="sonnet")
    g.start_node("judge", role="judge", model="opus")
    g.set_session_id("judge", "sid-judge")
    save_workflow(ws, g.doc)
    doc = interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    assert doc is not None
    statuses = {t.id: t.status for t in doc.tasks}
    assert statuses == {
        "diff_reviewer": "pending",
        "scenario_runner": "pending",
        "judge": "interrupted",
    }
    assert doc.status == "running"


def test_settled_pending_sibling_reactivates_via_start_node_without_duplicate_error(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root)
    inv_id = create_invocation_dir(ws)
    g = WorkflowGraph(task_id=ws.task_id, inv_id=inv_id, team="verification")
    g.start_node("diff_reviewer", role="diff_reviewer", model="sonnet")
    g.set_session_id("diff_reviewer", "sid-diff")
    g.start_node("scenario_runner", role="scenario_runner", model="sonnet")
    save_workflow(ws, g.doc)
    interrupt_workflow_on_disk(ws, inv_id, resumable=True)

    reloaded = load_graph(ws, inv_id)
    settled = next(t for t in reloaded.doc.tasks if t.id == "scenario_runner")
    assert settled.status == "pending"
    reactivated = reloaded.start_node(
        "scenario_runner", role="scenario_runner", model="sonnet"
    )
    assert reactivated.status == "running"
    assert reactivated.error is None
