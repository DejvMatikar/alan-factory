from __future__ import annotations

from pathlib import Path

import pytest

from alan import _reconciler_never_started as never_started_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.queue import Job, JobStatus, Queue, QueueError
from alan.workflow import PendingTaskSpec, save_workflow, seed_pending_workflow
from alan.workspace import Status, Workspace, create


def _seed_running_job(
    queue: Queue, ws: Workspace, *, team: str, node_id: str = "implementer"
) -> tuple[Job, str]:
    inv_id = seed_pending_workflow(
        ws, team=team, tasks=[PendingTaskSpec(id=node_id, role=node_id, model="sonnet")]
    )
    queue.enqueue(ws.task_id, team, triggered_by_invocation_id=inv_id)
    job = queue.claim()
    assert job is not None
    return job, inv_id


def test_team_supports_resume_false_for_unknown_team() -> None:
    assert never_started_mod.team_supports_resume("ghost") is False


def test_team_supports_rederive_false_for_unknown_team() -> None:
    assert never_started_mod.team_supports_rederive("ghost") is False


def test_team_supports_resume_true_for_resume_capable_team() -> None:
    assert never_started_mod.team_supports_resume("implementation_fix") is True


def test_team_supports_rederive_true_for_rederivable_team() -> None:
    assert never_started_mod.team_supports_rederive("pr_merge") is True


def test_has_captured_session_load_failure_returns_true(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("captured-load-fail", "d", root=ws_root)

    def _boom(*_a: object, **_k: object) -> object:
        raise workflow_mod.WorkflowError("corrupt")

    monkeypatch.setattr(workflow_mod, "load_workflow", _boom)
    assert never_started_mod.has_captured_session(ws, "inv_001") is True


def test_has_completed_node_load_failure_returns_true(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("completed-load-fail", "d", root=ws_root)

    def _boom(*_a: object, **_k: object) -> object:
        raise workflow_mod.WorkflowError("corrupt")

    monkeypatch.setattr(workflow_mod, "load_workflow", _boom)
    assert never_started_mod.has_completed_node(ws, "inv_001") is True


def test_prepare_rerun_returns_false_when_workflow_missing(ws_root: Path) -> None:
    ws = create("prepare-missing", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="implementation_fix",
        tasks=[PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    workflow_mod.workflow_path(ws, inv_id).unlink()
    assert never_started_mod._prepare_rerun(ws, inv_id) is False  # noqa: SLF001


def test_try_rerun_reruns_when_never_started(queue: Queue, ws_root: Path) -> None:
    ws = create("never-started", "d", root=ws_root)
    job, inv_id = _seed_running_job(queue, ws, team="implementation_fix")

    outcome = never_started_mod.try_rerun(job, queue, ws)

    assert outcome == "rerun"
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id is None
    assert row.triggered_by_invocation_id == inv_id
    assert row.interruption_count == 1
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"


def test_try_rerun_resets_running_node_without_session(queue: Queue, ws_root: Path) -> None:
    ws = create("running-no-session", "d", root=ws_root)
    job, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    save_workflow(ws, graph.doc)

    outcome = never_started_mod.try_rerun(job, queue, ws)

    assert outcome == "rerun"
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    assert doc.tasks[0].claude_session_id is None


def test_try_rerun_not_applicable_when_workspace_none(queue: Queue, ws_root: Path) -> None:
    ws = create("no-ws-job", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="implementation_fix")
    assert never_started_mod.try_rerun(job, queue, None) == "not_applicable"


def test_try_rerun_not_applicable_when_archived(queue: Queue, ws_root: Path) -> None:
    ws = create("archived-job", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="implementation_fix")
    workspace_mod.transition(ws, Status.ARCHIVED)
    archived_ws = workspace_mod.read(ws.task_id, root=ws_root)

    assert never_started_mod.try_rerun(job, queue, archived_ws) == "not_applicable"


def test_try_rerun_not_applicable_when_no_invocation_id(queue: Queue, ws_root: Path) -> None:
    ws = create("no-inv-job", "d", root=ws_root)
    queue.enqueue(ws.task_id, "implementation_fix")
    claimed = queue.claim()
    assert claimed is not None

    assert never_started_mod.try_rerun(claimed, queue, ws) == "not_applicable"


def test_try_rerun_not_applicable_for_restart_rederivable_team(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("rederivable-team", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="verification")

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"


def test_try_rerun_not_applicable_for_non_resume_team(queue: Queue, ws_root: Path) -> None:
    ws = create("non-resume-team", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="dependency_sweep")

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"


def test_try_rerun_not_applicable_when_session_captured(queue: Queue, ws_root: Path) -> None:
    ws = create("captured-session", "d", root=ws_root)
    job, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sid-1")
    save_workflow(ws, graph.doc)

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"


def test_try_rerun_not_applicable_when_node_completed(queue: Queue, ws_root: Path) -> None:
    ws = create("completed-node", "d", root=ws_root)
    job, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.complete_node("implementer")
    save_workflow(ws, graph.doc)

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"


def test_try_rerun_not_applicable_when_node_remains_non_pending_after_reset(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("stuck-interrupted", "d", root=ws_root)
    job, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.mark_interrupted("implementer")
    save_workflow(ws, graph.doc)

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"


def test_try_rerun_exhausted_at_cap(queue: Queue, ws_root: Path) -> None:
    ws = create("cap-exhausted", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="implementation_fix")
    for _ in range(never_started_mod.RERUN_CAP):
        queue.requeue_pending(job.id)
        job = queue.claim()  # type: ignore[assignment]
        assert job is not None
    assert job.interruption_count == never_started_mod.RERUN_CAP

    assert never_started_mod.try_rerun(job, queue, ws) == "exhausted"


def test_try_rerun_below_cap_still_reruns(queue: Queue, ws_root: Path) -> None:
    ws = create("below-cap", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="implementation_fix")
    for _ in range(never_started_mod.RERUN_CAP - 1):
        queue.requeue_pending(job.id)
        job = queue.claim()  # type: ignore[assignment]
        assert job is not None
    assert job.interruption_count == never_started_mod.RERUN_CAP - 1

    assert never_started_mod.try_rerun(job, queue, ws) == "rerun"


def test_try_rerun_not_applicable_when_requeue_pending_raises(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("requeue-raises", "d", root=ws_root)
    job, _ = _seed_running_job(queue, ws, team="implementation_fix")

    def explode(self: Queue, job_id: int) -> Job:
        del self, job_id
        raise QueueError("boom")

    monkeypatch.setattr(Queue, "requeue_pending", explode)

    assert never_started_mod.try_rerun(job, queue, ws) == "not_applicable"
