from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import reconciler as reconciler_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan._reconciler_paused import requeue_paused_job
from alan.claude_cli import OnProcStarted, UsageLimitError
from alan.detached_mode import resolve_detached_mode
from alan.dispatcher import Dispatcher
from alan.events import PubSub
from alan.handover import Handover
from alan.queue import Job, JobStatus, Queue, QueueError
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec, save_workflow, seed_pending_workflow
from alan.workspace import Status, Workspace, create


class _NonResumeTeam:
    name = "pr_merge"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        raise UsageLimitError("hit your limit", time.time() + 3600.0)


class _ResumeTeam(_NonResumeTeam):
    supports_resume = True


class _NonResumeRunningNodeTeam(_NonResumeTeam):
    def run(
        self,
        ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        graph = workflow_mod.load_graph(ws, inv_id)
        graph.start_node("pr_merge", role="pr_merge", model="sonnet")
        save_workflow(ws, graph.doc)
        raise UsageLimitError("hit your limit", time.time() + 3600.0)


def _seed_failed_node(ws: Workspace, node_id: str) -> str:
    inv_id = seed_pending_workflow(
        ws, team="pr_merge", tasks=[PendingTaskSpec(id=node_id, role=node_id, model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(node_id, role=node_id, model="sonnet")
    graph.fail_node(node_id, error="claude CLI exited 1: hit your limit")
    graph.finalize(status="failed")
    save_workflow(ws, graph.doc)
    return inv_id


def test_usage_limit_pause_requeue_resets_failed_node_so_reentry_activates(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("pause-reentry-fix", "d", root=ws_root)
    inv_id = _seed_failed_node(ws, "pr_merge")
    job = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()
    team = _NonResumeTeam()
    d = Dispatcher(queue, {"pr_merge": team}, workspaces_root=ws_root)

    d._handle_usage_limit(  # noqa: SLF001
        job, ws, team, UsageLimitError("hit your limit", time.time() + 3600.0), inv_id
    )

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node(
        "pr_merge", role="pr_merge", model="sonnet"
    )
    assert reactivated.status == "running"


def test_usage_limit_pause_requeue_without_reset_raises_duplicate_node_id(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("pause-reentry-pin", "d", root=ws_root)
    inv_id = _seed_failed_node(ws, "pr_merge")
    job = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()
    team = _NonResumeTeam()
    d = Dispatcher(queue, {"pr_merge": team}, workspaces_root=ws_root)
    monkeypatch.setattr(
        "alan._dispatcher_usage_limit.workflow_mod.reset_failed_nodes_for_rerun",
        lambda *_a, **_k: None,
    )

    d._handle_usage_limit(  # noqa: SLF001
        job, ws, team, UsageLimitError("hit your limit", time.time() + 3600.0), inv_id
    )

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    with pytest.raises(workflow_mod.WorkflowError, match="duplicate node id"):
        workflow_mod.load_graph(ws, inv_id).start_node(
            "pr_merge", role="pr_merge", model="sonnet"
        )


def test_user_stop_pause_requeue_resets_failed_node_for_non_resume_team(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("user-stop-reentry", "d", root=ws_root)
    inv_id = _seed_failed_node(ws, "pr_merge")
    job = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()
    team = _NonResumeTeam()
    d = Dispatcher(queue, {"pr_merge": team}, workspaces_root=ws_root)

    d._pause_job(job, ws, inv_id, team)  # noqa: SLF001

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node(
        "pr_merge", role="pr_merge", model="sonnet"
    )
    assert reactivated.status == "running"


def test_pause_requeue_resume_branch_skips_reset(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("resume-branch-skips-reset", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()
    team = _ResumeTeam()
    d = Dispatcher(queue, {"verification": team}, workspaces_root=ws_root)
    calls: list[object] = []
    monkeypatch.setattr(
        "alan._dispatcher_usage_limit.workflow_mod.reset_failed_nodes_for_rerun",
        lambda *a, **k: calls.append((a, k)),
    )

    d._handle_usage_limit(  # noqa: SLF001
        job, ws, team, UsageLimitError("hit your limit", time.time() + 3600.0), inv_id
    )

    assert calls == []
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "interrupted"


def test_reconciler_paused_job_clears_running_node_before_reentry(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("paused-clears-running", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "dependency_sweep", triggered_by_invocation_id=inv_id)
    queue.claim()

    requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node("w", role="w", model="sonnet")
    assert reactivated.status == "running"


def test_reconciler_paused_job_clears_failed_node_before_reentry(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("paused-clears-failed", "d", root=ws_root)
    inv_id = _seed_failed_node(ws, "w")
    job = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()

    requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node("w", role="w", model="sonnet")
    assert reactivated.status == "running"


def test_reconciler_paused_job_preserves_resumable_session_for_reentry(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("paused-preserves-session", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()

    requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id == inv_id
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "interrupted"
    assert doc.tasks[0].claude_session_id == "sid-1"


def test_reconciler_paused_job_resumable_team_without_session_falls_back_to_reset(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("paused-resumable-no-session", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()

    requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id is None
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node("w", role="w", model="sonnet")
    assert reactivated.status == "running"


def test_reconciler_paused_job_resume_interrupt_failure_falls_back_to_reset(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("paused-resume-interrupt-explodes", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()

    def explode(*_a: object, **_k: object) -> None:
        raise OSError("workflow disk gone")

    monkeypatch.setattr("alan._reconciler_paused.interrupt_workflow_on_disk", explode)
    with caplog.at_level("ERROR", logger="alan.reconciler"):
        requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id is None
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    assert any("resumable interrupt failed" in r.message for r in caplog.records)


def test_reconciler_paused_job_resume_requeue_failure_logs_warning(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("paused-resume-requeue-fails", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()

    def raising_requeue(_job_id: int, _inv_id: str) -> Job:
        raise QueueError("already claimed")

    monkeypatch.setattr(queue, "requeue_for_resume", raising_requeue)
    with caplog.at_level("WARNING", logger="alan.reconciler"):
        requeue_paused_job(job, queue, ws)

    assert any("failed to park paused job" in r.message for r in caplog.records)


def test_reconciler_paused_job_without_reset_raises_duplicate_node_id(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("paused-pin", "d", root=ws_root)
    inv_id = _seed_failed_node(ws, "w")
    job = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()
    monkeypatch.setattr(
        "alan._reconciler_paused.reset_failed_nodes_for_rerun", lambda *_a, **_k: None
    )

    requeue_paused_job(job, queue, ws)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    with pytest.raises(workflow_mod.WorkflowError, match="duplicate node id"):
        workflow_mod.load_graph(ws, inv_id).start_node("w", role="w", model="sonnet")


def test_reconciler_paused_job_with_missing_workspace_still_requeues(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("paused-missing-ws", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    job = queue.enqueue(ws.task_id, "dependency_sweep", triggered_by_invocation_id=inv_id)
    queue.claim()

    requeue_paused_job(job, queue, None)

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING


def test_reconciler_paused_job_without_triggered_invocation_skips_reset(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("paused-no-inv-id", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "dependency_sweep")
    queue.claim()
    calls: list[object] = []
    monkeypatch.setattr(
        "alan._reconciler_paused.reset_failed_nodes_for_rerun",
        lambda *a, **_k: calls.append(a),
    )

    requeue_paused_job(job, queue, ws)

    assert calls == []
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING


def test_reconciler_paused_job_requeue_error_logs_warning(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("paused-requeue-error", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "dependency_sweep")

    def raising_requeue(_job_id: int) -> Job:
        raise QueueError("already claimed")

    monkeypatch.setattr(queue, "requeue_pending", raising_requeue)
    with caplog.at_level("WARNING", logger="alan.reconciler"):
        requeue_paused_job(job, queue, ws)

    assert any("failed to park paused job" in r.message for r in caplog.records)


def test_reconciler_paused_job_appends_no_report_entry(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("paused-no-report", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    queue.enqueue(ws.task_id, "dependency_sweep", triggered_by_invocation_id=inv_id)
    queue.claim()
    queue.pause_task(ws.task_id)

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.interrupted == []
    assert report.resumed == []
    assert report.replayed == []
    assert report.rerun == []
    assert report.errors == []
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING


def test_interrupted_node_survives_reset_and_start_node_still_refuses(
    ws_root: Path,
) -> None:
    ws = create("interrupted-not-reset", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    graph.mark_interrupted("w", error="interrupted_by_restart")
    save_workflow(ws, graph.doc)

    workflow_mod.reset_failed_nodes_for_rerun(ws, inv_id)

    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "interrupted"
    with pytest.raises(workflow_mod.WorkflowError, match="duplicate node id"):
        workflow_mod.load_graph(ws, inv_id).start_node("w", role="w", model="sonnet")


def test_rederive_decline_parks_without_start_node_reentry(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("rederive-cap-exhausted", "d", root=ws_root)
    for _ in range(reconciler_mod._REDERIVE_CAP):  # noqa: SLF001
        prior = queue.enqueue(ws.task_id, "pr_merge")
        queue.claim()
        queue.mark_interrupted_and_fail(prior.id, "prior failure")
    inv_id = seed_pending_workflow(
        ws, team="pr_merge", tasks=[PendingTaskSpec(id="pr_merge", role="pr_merge", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("pr_merge", role="pr_merge", model="sonnet")
    save_workflow(ws, graph.doc)
    orphan = queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    queue.claim()

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.interrupted == [orphan.id]
    row = queue.get_job(orphan.id)
    assert row is not None
    assert row.status is JobStatus.FAILED
    assert row.resume_inv_id is None
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "failed"


def test_non_resume_team_pause_reentry_never_parks_on_duplicate_node(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("pause-e2e-never-parks", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="pr_merge", tasks=[PendingTaskSpec(id="pr_merge", role="pr_merge", model="sonnet")]
    )
    queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv_id)
    team = _NonResumeRunningNodeTeam()
    d = Dispatcher(queue, {"pr_merge": team}, workspaces_root=ws_root)

    d.run_once()

    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    reactivated = workflow_mod.load_graph(ws, inv_id).start_node(
        "pr_merge", role="pr_merge", model="sonnet"
    )
    assert reactivated.status == "running"
    ws_after = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_after.meta.status is not Status.AWAITING_INPUT


def _pending_resume_requeue_count(
    queue: Queue, pubsub: PubSub, ws_root: Path, label: str
) -> int:
    ws = create(f"detached-{label}", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-1")
    save_workflow(ws, graph.doc)
    queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    queue.claim()

    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)

    rows = queue.list_jobs(task_id=ws.task_id)
    count = sum(1 for r in rows if r.status is JobStatus.PENDING and r.resume_inv_id == inv_id)
    queue.delete_jobs(ws.task_id)
    return count


def test_detached_restart_produces_one_resume_requeue_regardless_of_detached_mode(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "on")
    assert resolve_detached_mode() is True
    detached_requeue_count = _pending_resume_requeue_count(queue, pubsub, ws_root, "on")

    monkeypatch.setenv("ALAN_DETACHED_WORKERS", "off")
    assert resolve_detached_mode() is False
    non_detached_requeue_count = _pending_resume_requeue_count(queue, pubsub, ws_root, "off")

    assert detached_requeue_count == 1
    assert non_detached_requeue_count == 1
