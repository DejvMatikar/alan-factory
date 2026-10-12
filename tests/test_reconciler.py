from __future__ import annotations

import json as _json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import reconciler as reconciler_mod
from alan import workflow as workflow_mod
from alan import workflow_interrupt
from alan import workspace as workspace_mod
from alan._reconciler_orphans import RESTART_PARK_MESSAGE
from alan.engine_config import (
    DependencySweepConfig,
    EngineConfig,
    RepoIdentity,
    default_engine_config,
    register,
)
from alan.events import PubSub
from alan.handover import Handover
from alan.queue import Job, JobStatus, Queue
from alan.teams._base import TeamEvent
from alan.teams.triage import ROLE_SPECS as _TRIAGE_ROLE_SPECS
from alan.workflow import (
    INTERRUPTED_BY_RESTART_ERROR,
    PendingTaskSpec,
    list_invocations,
    load_workflow,
    save_workflow,
    seed_pending_workflow,
    workflow_path,
)
from alan.workspace import Status, Workspace, create


def _seed_running_job(queue: Queue, ws: Workspace, *, team: str = "ghost") -> tuple[int, str]:
    inv_id = seed_pending_workflow(
        ws, team=team, tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    job = queue.enqueue(ws.task_id, team, triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    return job.id, inv_id


def _seed_running_node(ws: Workspace, inv_id: str) -> None:
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-existing")
    save_workflow(ws, graph.doc)


def _collect_events(pubsub: PubSub, task_id: str) -> list[TeamEvent]:
    seen: list[TeamEvent] = []
    pubsub.subscribe(task_id, seen.append)
    return seen


def test_reconcile_no_running_jobs_returns_empty_report(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    create("alpha", "d", root=ws_root)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == []
    assert report.errors == []


def test_reconcile_flips_running_job_to_failed_and_bumps_counter(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    _seed_running_node(ws, inv_id)
    events = _collect_events(pubsub, ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    assert report.errors == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED
    assert rows[0].error == INTERRUPTED_BY_RESTART_ERROR
    assert rows[0].interruption_count == 1
    assert any(e.kind == "workspace_interrupted" for e in events)


def test_reconcile_marks_workflow_nodes_failed_with_restart_error(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    _, inv_id = _seed_running_job(queue, ws)
    _seed_running_node(ws, inv_id)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.status == "failed"
    assert doc.tasks[0].status == "failed"
    assert doc.tasks[0].error == INTERRUPTED_BY_RESTART_ERROR


def test_reconcile_flips_workspace_meta_to_awaiting_input(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    _seed_running_job(queue, ws)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    refreshed = workspace_mod.read(ws.task_id, root=ws_root)
    assert refreshed.meta.status is Status.AWAITING_INPUT


def test_reconcile_zombie_park_records_typed_crash_reason_and_synthetic_blocker(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    from alan.park import ParkReason

    ws = create("alpha", "d", root=ws_root)
    _seed_running_job(queue, ws)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    refreshed = workspace_mod.read(ws.task_id, root=ws_root)
    assert refreshed.meta.park_reason is ParkReason.CRASH
    assert len(refreshed.meta.blockers) == 1
    assert refreshed.meta.blockers[0].detail == RESTART_PARK_MESSAGE


def test_reconcile_handles_missing_workspace(queue: Queue, pubsub: PubSub, ws_root: Path) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)
    import shutil

    shutil.rmtree(ws.path)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_reconcile_handles_missing_workflow_yaml(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    workflow_mod.workflow_path(ws, inv_id).unlink()
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]


def test_reconcile_terminal_marker_load_failure_leaves_resume_inv_id_absent(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    workflow_mod.workflow_path(ws, inv_id).unlink()
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.FAILED
    assert row.resume_inv_id is None


def test_reconcile_skips_workflow_update_when_no_inv_id(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [claimed.id]


def test_reconcile_isolates_per_row_queue_flip_errors(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_a = create("alpha", "d", root=ws_root)
    ws_b = create("beta", "d", root=ws_root)
    job_a, _ = _seed_running_job(queue, ws_a)
    job_b, _ = _seed_running_job(queue, ws_b)
    from alan.queue import Job as _Job
    from alan.queue import Queue as _Queue

    original_flip = _Queue.mark_interrupted_and_fail

    def explode_for_a(
        self: _Queue,
        job_id: int,
        error: str,
        *,
        resume_inv_id: str | None = None,
        clear_resume: bool = False,
    ) -> _Job:
        if job_id == job_a:
            raise RuntimeError("queue write failed for alpha")
        return original_flip(
            self, job_id, error, resume_inv_id=resume_inv_id, clear_resume=clear_resume
        )

    monkeypatch.setattr(_Queue, "mark_interrupted_and_fail", explode_for_a)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert [job_id for job_id, _ in report.errors] == [job_a]
    assert report.interrupted == [job_b]


def test_reconcile_swallows_workspace_status_write_failure(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)

    def explode(workspace: Workspace, status: Status) -> Workspace:
        del workspace, status
        raise OSError("disk gone")

    monkeypatch.setattr(workspace_mod, "transition", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.interrupted == [job_id]
    assert report.errors == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED
    assert any("failed to flip workspace" in rec.message for rec in caplog.records)


def test_reconcile_zombie_prevention_when_workspace_read_explodes(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)

    def explode(_task_id: str, *, root: Path | None = None) -> Workspace:
        del root
        raise RuntimeError("metadata corrupt")

    monkeypatch.setattr(workspace_mod, "read", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.interrupted == [job_id]
    assert report.errors == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED
    assert any("workspace read failed" in rec.message for rec in caplog.records)


def test_reconcile_swallows_workflow_interrupt_failure(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)

    def explode(workspace: Workspace, inv_id: str, *, error: str) -> None:
        del workspace, inv_id, error
        raise OSError("workflow disk gone")

    monkeypatch.setattr(workflow_interrupt, "interrupt_workflow_on_disk", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.interrupted == [job_id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED
    assert any("workflow interrupt failed" in rec.message for rec in caplog.records)


def test_reconcile_swallows_pubsub_publish_failure(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pubsub = PubSub()
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)

    def explode(self: PubSub, task_id: str, event: TeamEvent) -> None:
        del self, task_id, event
        raise RuntimeError("pubsub broken")

    monkeypatch.setattr(PubSub, "publish", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.interrupted == [job_id]
    assert any("publish workspace_interrupted failed" in rec.message for rec in caplog.records)


def _seed_resumable_running_job(
    queue: Queue, ws: Workspace, *, team: str = "implementation"
) -> tuple[int, str]:
    inv_id = seed_pending_workflow(
        ws, team=team, tasks=[PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
    )
    job = queue.enqueue(ws.task_id, team, triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    graph.set_session_id("implementer", "sid-recoverable")
    save_workflow(ws, graph.doc)
    return job.id, inv_id


def test_reconcile_requeues_resumable_job_with_session_id(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_resumable_running_job(queue, ws)
    events = _collect_events(pubsub, ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.resumed == [job_id]
    assert report.interrupted == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.PENDING
    assert rows[0].resume_inv_id == inv_id
    assert rows[0].interruption_count == 1
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.status == "running"
    assert doc.tasks[0].status == "interrupted"
    assert doc.tasks[0].claude_session_id == "sid-recoverable"
    assert any(e.kind == "workspace_resuming" for e in events)


def test_reconcile_does_not_flip_workspace_failed_when_resuming(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    _seed_resumable_running_job(queue, ws)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    refreshed = workspace_mod.read(ws.task_id, root=ws_root)
    assert refreshed.meta.status is Status.ACTIVE


def test_reconcile_falls_back_to_fail_when_team_does_not_support_resume(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    _seed_running_job(queue, ws, team="dependency_sweep")
    inv_id = workflow_mod.list_invocations(ws)[0]
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.set_session_id("w", "sid-yes")
    save_workflow(ws, graph.doc)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.resumed == []
    assert len(report.interrupted) == 1
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_reconcile_never_started_reruns_when_session_id_missing(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    save_workflow(ws, graph.doc)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.resumed == []
    assert report.interrupted == []
    assert report.rerun == [job_id]
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING


def test_reconcile_resume_falls_back_when_interrupt_helper_blows_up(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_resumable_running_job(queue, ws)
    original = workflow_interrupt.interrupt_workflow_on_disk

    def boom_when_resumable(
        workspace: Workspace, inv_id: str, *, error: str = "x", resumable: bool = False
    ) -> workflow_mod.WorkflowDoc | None:
        if resumable:
            raise OSError("disk gone")
        return original(workspace, inv_id, error=error, resumable=resumable)

    monkeypatch.setattr(workflow_interrupt, "interrupt_workflow_on_disk", boom_when_resumable)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.resumed == []
    assert report.interrupted == [job_id]


def test_reconcile_resume_falls_back_when_requeue_fails(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_resumable_running_job(queue, ws)

    def explode(self: Queue, job_id_: int, inv_id_: str) -> Job:
        del self, job_id_, inv_id_
        raise RuntimeError("requeue write failed")

    monkeypatch.setattr(Queue, "requeue_for_resume", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.resumed == []
    assert report.interrupted == [job_id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_reconcile_never_started_job_reruns_instead_of_terminal_fallback(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="implementation",
        tasks=[PendingTaskSpec(id="implementer", role="implementer", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    queue.claim()
    queue.requeue_for_resume(job.id, "prior-session-id")
    queue.claim()
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("implementer", role="implementer", model="sonnet")
    save_workflow(ws, graph.doc)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.rerun == [job.id]
    assert report.interrupted == []
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id is None
    assert row.triggered_by_invocation_id == inv_id
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"


def test_reconcile_terminal_fallback_sets_resume_inv_id_when_resume_attempted_but_fails(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    job_id, inv_id = _seed_resumable_running_job(queue, ws)

    def explode(self: Queue, job_id_: int, inv_id_: str) -> Job:
        del self, job_id_, inv_id_
        raise RuntimeError("requeue write failed")

    monkeypatch.setattr(Queue, "requeue_for_resume", explode)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.FAILED
    assert row.resume_inv_id == inv_id
    assert queue.list_resumable_failed_by_workspace(ws.workspace_id) == [row]


def test_reconcile_resume_audit_log_written(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    ws = create("alpha", "d", root=ws_root)
    _seed_resumable_running_job(queue, ws)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    audit_file = logs_dir / "resume_audit.jsonl"
    assert audit_file.exists()
    line = audit_file.read_text(encoding="utf-8").splitlines()[0]
    import json as _json

    entry = _json.loads(line)
    assert entry["task_id"] == ws.task_id
    assert entry["team"] == "implementation"
    assert entry["outcome"] == "requeued_for_resume"
    assert entry["interruption_count"] == 1


def test_rederive_running_pr_merge_job_enqueued_fresh(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("rd-happy", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="pr_merge")
    events = _collect_events(pubsub, ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id not in report.interrupted
    assert report.errors == []
    failed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.FAILED)
    assert any(j.id == job_id for j in failed)
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert any(j.team == "pr_merge" and j.triggered_by_invocation_id != inv_id for j in fresh)
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.ACTIVE
    assert any(e.kind == "pr_merge_rederived" for e in events)


def test_rederive_running_verification_job_enqueued_fresh(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("rd-verif", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="verification")
    events = _collect_events(pubsub, ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id not in report.interrupted
    assert report.errors == []
    failed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.FAILED)
    assert any(j.id == job_id for j in failed)
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert any(j.team == "verification" and j.triggered_by_invocation_id != inv_id for j in fresh)
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.ACTIVE
    assert any(e.kind == "verification_rederived" for e in events)


def test_rederive_proceeds_for_verification_when_resume_attempted_but_failed(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("rd-verif-session", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="verification")
    _seed_running_node(ws, inv_id)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("requeue boom")

    monkeypatch.setattr(queue, "requeue_for_resume", _boom)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert any(
        j.team == "verification" and j.triggered_by_invocation_id != inv_id for j in fresh
    )
    failed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.FAILED)
    assert any(j.id == job_id for j in failed)


def test_rederive_audit_log_written(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))
    ws = create("rd-audit", "d", root=ws_root)
    job_id, old_inv_id = _seed_running_job(queue, ws, team="pr_merge")
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    audit_file = logs_dir / "rederive_audit.jsonl"
    assert audit_file.exists()
    entry = _json.loads(audit_file.read_text(encoding="utf-8").splitlines()[0])
    assert entry["task_id"] == ws.task_id
    assert entry["team"] == "pr_merge"
    assert entry["job_id"] == job_id
    assert entry["old_inv_id"] == old_inv_id
    assert entry["outcome"] == "rederived"
    assert entry["new_inv_id"] != old_inv_id


def test_rederive_skipped_when_terminal_handover_exists(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
) -> None:
    from alan.handover import Handover, save_handover

    ws = create("rd-terminal", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="pr_merge")
    save_handover(
        ws,
        Handover(
            handover_id="h-done",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id=inv_id,
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #42 merged",
            pr_number=42,
        ),
    )
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id in report.interrupted
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.AWAITING_INPUT


def test_rederive_cap_exceeded_falls_through(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
) -> None:
    ws = create("rd-cap", "d", root=ws_root)
    nj = queue.enqueue(ws.task_id, "ghost", workspace_id=ws.workspace_id)
    claimed = queue.claim()
    assert claimed is not None and claimed.id == nj.id
    queue.mark_interrupted_and_fail(claimed.id, "ghost fail")
    for _ in range(reconciler_mod._REDERIVE_CAP):  # noqa: SLF001
        fj_inv = seed_pending_workflow(
            ws,
            team="pr_merge",
            tasks=[PendingTaskSpec(id="pr_merge", role="pr_merge", model="none")],
        )
        fj = queue.enqueue(
            ws.task_id,
            "pr_merge",
            workspace_id=ws.workspace_id,
            triggered_by_invocation_id=fj_inv,
        )
        fj_claimed = queue.claim()
        assert fj_claimed is not None and fj_claimed.id == fj.id
        queue.mark_interrupted_and_fail(fj_claimed.id, "prior failure")
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id in report.interrupted
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.AWAITING_INPUT


def test_failed_rederive_count_ignores_non_team_jobs(
    queue: Queue,
    ws_root: Path,
) -> None:
    ws = create("rd-count", "d", root=ws_root)
    gj = queue.enqueue(ws.task_id, "ghost", workspace_id=ws.workspace_id)
    claimed = queue.claim()
    assert claimed is not None and claimed.id == gj.id
    queue.mark_interrupted_and_fail(claimed.id, "ghost fail")
    count = reconciler_mod._failed_rederive_count(queue, ws.task_id, "pr_merge")  # noqa: SLF001
    assert count == 0


def test_rederive_skipped_when_workspace_archived(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
) -> None:
    ws = create("rd-archived", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")
    workspace_mod.transition(ws, Status.ARCHIVED)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id in report.interrupted


def test_rederive_skipped_when_no_inv_id(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
) -> None:
    ws = create("rd-no-inv", "d", root=ws_root)
    job = queue.enqueue(ws.task_id, "pr_merge", workspace_id=ws.workspace_id)
    claimed = queue.claim()
    assert claimed is not None and claimed.id == job.id
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job.id in report.interrupted


def test_rederive_mark_fail_exception_falls_through(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("rd-mif-fail", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")

    def explode(self: Queue, job_id_: int, error: str, **kwargs: object) -> "Job":
        del self, job_id_, error, kwargs
        raise RuntimeError("db error")

    monkeypatch.setattr(Queue, "mark_interrupted_and_fail", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert any("rederive failed to flip" in r.message for r in caplog.records)
    assert (job_id, "db error") in report.errors


def test_rederive_enqueue_failure_falls_back_to_workspace_interrupted(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("rd-enq-fail", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")

    def bad_seed(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise RuntimeError("seed failed")

    monkeypatch.setattr(workflow_mod, "seed_pending_workflow", bad_seed)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert job_id not in report.interrupted
    assert report.errors == []
    failed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.FAILED)
    assert any(j.id == job_id for j in failed)
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert not any(j.team == "pr_merge" for j in fresh)
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.AWAITING_INPUT
    assert any("rederive enqueue failed" in r.message for r in caplog.records)
    from alan.park import ParkReason

    assert ws_reload.meta.park_reason is ParkReason.CRASH
    assert len(ws_reload.meta.blockers) == 1


def test_rederive_publish_failure_swallowed(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("rd-pub-fail", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")

    def explode(self: "PubSub", task_id: str, event: TeamEvent) -> None:
        del self, task_id, event
        raise RuntimeError("pubsub broken")

    monkeypatch.setattr(PubSub, "publish", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert job_id not in report.interrupted
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert any(j.team == "pr_merge" for j in fresh)
    assert any("publish pr_merge_rederived failed" in r.message for r in caplog.records)


def test_rederive_publishes_on_workspace_channel_for_subtask_job(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("rd-sub-ws", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="pr_merge",
        tasks=[PendingTaskSpec(id="pr_merge", role="pr_merge", model="none")],
    )
    subtask_id = "subtask-rd-sub"
    job = queue.enqueue(
        subtask_id, "pr_merge", workspace_id=ws.task_id, triggered_by_invocation_id=inv_id
    )
    claimed = queue.claim()
    assert claimed is not None and claimed.id == job.id
    workspace_events = _collect_events(pubsub, ws.task_id)
    subtask_events = _collect_events(pubsub, subtask_id)
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    assert any(e.kind == "pr_merge_rederived" for e in workspace_events)
    assert not any(e.kind == "pr_merge_rederived" for e in subtask_events)


def test_terminal_pr_merge_handovers_oserror_swallowed(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.handover import Handover, save_handover

    ws = create("rd-oserr", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws, team="pr_merge")
    save_handover(
        ws,
        Handover(
            handover_id="h-perm",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="some-inv",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #1 merged",
            pr_number=1,
        ),
    )

    def raise_oserror(path: object) -> object:
        raise PermissionError("no permission")

    monkeypatch.setattr(reconciler_mod, "load_handover", raise_oserror)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id not in report.interrupted
    assert report.errors == []


def test_reconcile_unknown_team_falls_back_to_fail(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="ghost",
        tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "ghost", triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid")
    save_workflow(ws, graph.doc)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.resumed == []
    assert report.interrupted == [job.id]


def test_reconcile_publish_workspace_resuming_failure_is_swallowed(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    pubsub = PubSub()
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_resumable_running_job(queue, ws)

    def explode(self: PubSub, task_id: str, event: TeamEvent) -> None:
        del self, task_id, event
        raise RuntimeError("pubsub broken")

    monkeypatch.setattr(PubSub, "publish", explode)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        report = reconciler_mod.reconcile_on_startup(
            queue=queue, workspaces_root=ws_root, pubsub=pubsub
        )
    assert report.resumed == [job_id]
    assert any("publish workspace_resuming failed" in rec.message for rec in caplog.records)


def _get_triage_ws(ws_root: Path) -> Workspace:
    return next(
        ws for ws in workspace_mod.list_workspaces(root=ws_root) if ws.meta.team == "triage"
    )


def test_seed_triage_sweep_creates_workspace_and_enqueues(queue: Queue, ws_root: Path) -> None:
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    triage_ws = _get_triage_ws(ws_root)
    assert triage_ws.meta.title == "Failure Triage Sweep"
    jobs = queue.list_jobs(task_id=triage_ws.task_id)
    assert len(jobs) == 1
    assert jobs[0].team == "triage"
    assert jobs[0].status is JobStatus.PENDING
    inv_ids = list_invocations(triage_ws)
    assert len(inv_ids) == 1
    doc = load_workflow(workflow_path(triage_ws, inv_ids[0]))
    assert [t.id for t in doc.tasks] == [spec.id for spec in _TRIAGE_ROLE_SPECS]
    assert doc.source_task_id == triage_ws.task_id


def test_seed_triage_sweep_skips_when_pending_job_exists(queue: Queue, ws_root: Path) -> None:
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    triage_ws = _get_triage_ws(ws_root)
    assert len(queue.list_jobs(task_id=triage_ws.task_id)) == 1


def test_seed_triage_sweep_skips_when_deferred_pending_job_exists(
    queue: Queue, ws_root: Path
) -> None:
    from alan.health import SWEEP_CADENCE_SECONDS

    triage_ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    queue.enqueue(
        triage_ws.task_id,
        "triage",
        eligible_at=time.time() + SWEEP_CADENCE_SECONDS,
    )
    queue.set_meta("triage_last_scheduled_at", str(time.time()))

    before_count = len(queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING))
    assert before_count == 1

    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)

    after_count = len(queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING))
    assert after_count == before_count


def test_seed_triage_sweep_skips_when_running_job_exists(queue: Queue, ws_root: Path) -> None:
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    claimed = queue.claim()
    assert claimed is not None
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    triage_ws = _get_triage_ws(ws_root)
    assert len(queue.list_jobs(task_id=triage_ws.task_id)) == 1
    assert queue.list_jobs(task_id=triage_ws.task_id)[0].status is JobStatus.RUNNING


def test_seed_triage_sweep_reenqueues_when_workspace_exists_but_no_jobs(
    queue: Queue, ws_root: Path
) -> None:
    triage_ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    jobs = queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "triage"


def test_seed_triage_sweep_selects_canonical_when_multiple_exist(
    queue: Queue, ws_root: Path
) -> None:
    first = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    create("Failure Triage Sweep", "d", team="triage", root=ws_root)

    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)

    remaining = [
        ws for ws in workspace_mod.list_workspaces(root=ws_root) if ws.meta.team == "triage"
    ]
    assert len(remaining) == 1
    assert remaining[0].task_id == first.task_id
    jobs = queue.list_jobs(task_id=first.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "triage"


def test_seed_triage_sweep_archives_duplicate_triage_workspaces(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_dir))

    first = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    duplicate = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    duplicate_outer_dir = duplicate.path.parent.parent
    assert duplicate_outer_dir.is_dir()

    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)

    assert not duplicate_outer_dir.is_dir()
    with pytest.raises(workspace_mod.WorkspaceError):
        workspace_mod.read(duplicate.task_id, root=ws_root)

    archive_root = ws_root / "_archived_triage"
    archived_dirs = list(archive_root.iterdir())
    assert len(archived_dirs) == 1
    assert archived_dirs[0].name.startswith(duplicate.task_id)
    assert (archived_dirs[0] / "tasks" / duplicate.task_id / "meta.json").is_file()

    rows = [
        _json.loads(line) for line in (logs_dir / "triage_audit.jsonl").read_text().splitlines()
    ]
    archived_rows = [r for r in rows if r["action"] == "triage_workspace_archived"]
    assert len(archived_rows) == 1
    assert archived_rows[0]["task_id"] == duplicate.task_id

    remaining = [
        ws for ws in workspace_mod.list_workspaces(root=ws_root) if ws.meta.team == "triage"
    ]
    assert len(remaining) == 1
    assert remaining[0].task_id == first.task_id


def test_archive_triage_workspace_swallows_exception(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    duplicate = create("Failure Triage Sweep", "d", team="triage", root=ws_root)

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("disk error")

    monkeypatch.setattr(reconciler_mod, "_do_archive_triage_workspace", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        reconciler_mod._archive_triage_workspace(queue, duplicate)  # noqa: SLF001
    assert any("failed to archive duplicate triage workspace" in r.message for r in caplog.records)
    assert workspace_mod.read(duplicate.task_id, root=ws_root) is not None


def test_seed_triage_sweep_recovers_from_exhausted_slots(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    triage_ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_MAX_INV_SLOT", 3)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(3):
        workflow_mod.create_invocation_dir(triage_ws)
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    jobs = queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    inv_dirs = sorted(p.name for p in (triage_ws.path / "invocations").iterdir())
    assert inv_dirs == ["inv_001", "inv_003"]
    audit_path = tmp_path / "logs" / "triage_audit.jsonl"
    rows = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    pruned_rows = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["removed"] == 2


def test_seed_triage_sweep_recovers_from_exhausted_handover_slots(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan import handover as handover_mod
    from alan.handover import save_handover

    triage_ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    monkeypatch.setattr(handover_mod, "_MAX_SEQ", 2)
    for suffix in ("a", "b"):
        save_handover(
            triage_ws,
            Handover(
                handover_id=f"handover_{triage_ws.task_id}_{suffix}",
                handover_type="forward",
                task_id=triage_ws.task_id,
                from_team="triage",
                from_invocation_id="inv_001",
                to_team_suggestions=[],
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="[SENSOR_SWEEP] surfaces=1 blocked=0",
            ),
        )
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    jobs = queue.list_jobs(task_id=triage_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert list((triage_ws.path / "handovers").iterdir()) == []
    audit_path = tmp_path / "logs" / "triage_audit.jsonl"
    rows = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    pruned_rows = [r for r in rows if r["action"] == "handover_slots_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["removed"] == 2


def test_seed_triage_sweep_failure_writes_audit_row(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("no free invocation slot in failure-triage-sweep")

    monkeypatch.setattr(workflow_mod, "seed_pending_workflow", _boom)
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    audit_path = tmp_path / "logs" / "triage_audit.jsonl"
    rows = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    failure_rows = [r for r in rows if r["action"] == "seed_triage_failed"]
    assert len(failure_rows) == 1
    assert failure_rows[0]["source"] == "startup"
    assert "no free invocation slot" in failure_rows[0]["error"]


def test_seed_triage_sweep_skips_v0_workspace_with_pending_job(queue: Queue, ws_root: Path) -> None:
    v0_ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    inv_id = seed_pending_workflow(
        v0_ws,
        team="triage",
        tasks=[PendingTaskSpec(id="triage", role="triage", model="sonnet")],
    )
    queue.enqueue(v0_ws.task_id, "triage", triggered_by_invocation_id=inv_id)
    reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    jobs = queue.list_jobs(task_id=v0_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    doc = load_workflow(workflow_path(v0_ws, inv_id))
    assert len(doc.tasks) == 1
    assert doc.tasks[0].id == "triage"


def test_seed_triage_sweep_swallows_exception(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    def _boom(*, root: object = None) -> list[object]:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_mod, "list_workspaces", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        reconciler_mod.seed_triage_sweep(queue=queue, workspaces_root=ws_root)
    assert any("seed_triage_sweep failed" in r.message for r in caplog.records)


_DEFAULT_SLUG = "DejvMatikar/alan"


def _register_default_dep_sweep_engine() -> None:
    register(default_engine_config(), default=True, validate_env=False)


def _register_sprocket_engine(*, enabled: bool = True) -> None:
    register(EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        dependency_sweep=DependencySweepConfig(enabled=enabled),
    ))


def _get_dep_sweep_ws(
    ws_root: Path, *, slug: str | None = None
) -> workspace_mod.Workspace:
    all_ws = workspace_mod.list_workspaces(root=ws_root)
    ws = next(
        (
            w
            for w in all_ws
            if w.meta.team == "dependency_sweep"
            and w.meta.title == "Dependency Sweep"
            and (slug is None or w.meta.engine_key == slug)
        ),
        None,
    )
    assert ws is not None
    return ws


def test_seed_dep_sweep_creates_workspace_and_enqueues(queue: Queue, ws_root: Path) -> None:
    _register_default_dep_sweep_engine()
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = _get_dep_sweep_ws(ws_root)
    assert dep_ws.meta.title == "Dependency Sweep"
    assert dep_ws.meta.engine_key == _DEFAULT_SLUG
    jobs = queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "dependency_sweep"


def test_seed_dep_sweep_skips_disabled_engine(queue: Queue, ws_root: Path) -> None:
    _register_sprocket_engine(enabled=False)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    all_ws = workspace_mod.list_workspaces(root=ws_root)
    assert not any(w.meta.team == "dependency_sweep" for w in all_ws)


def test_seed_dep_sweep_fans_out_over_multiple_opted_in_engines(
    queue: Queue, ws_root: Path
) -> None:
    _register_default_dep_sweep_engine()
    _register_sprocket_engine(enabled=True)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    alan_ws = _get_dep_sweep_ws(ws_root, slug=_DEFAULT_SLUG)
    sprocket_ws = _get_dep_sweep_ws(ws_root, slug="sprocket-co/sprocket")
    assert alan_ws.task_id != sprocket_ws.task_id
    assert len(queue.list_jobs(task_id=alan_ws.task_id, status=JobStatus.PENDING)) == 1
    assert len(queue.list_jobs(task_id=sprocket_ws.task_id, status=JobStatus.PENDING)) == 1


def test_seed_dep_sweep_one_engine_failure_does_not_block_another(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _register_default_dep_sweep_engine()
    _register_sprocket_engine(enabled=True)
    real_impl = reconciler_mod._seed_dep_sweep_impl  # noqa: SLF001

    def _boom_for_sprocket(queue: Queue, workspaces_root: Path | None, cfg: EngineConfig) -> None:
        if cfg.repo.slug == "sprocket-co/sprocket":
            raise RuntimeError("boom")
        real_impl(queue, workspaces_root, cfg)

    monkeypatch.setattr(reconciler_mod, "_seed_dep_sweep_impl", _boom_for_sprocket)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    alan_ws = _get_dep_sweep_ws(ws_root, slug=_DEFAULT_SLUG)
    assert len(queue.list_jobs(task_id=alan_ws.task_id, status=JobStatus.PENDING)) == 1
    assert any("sprocket-co/sprocket" in r.message for r in caplog.records)


def test_seed_dep_sweep_opting_out_leaves_other_engines_running(
    queue: Queue, ws_root: Path
) -> None:
    _register_default_dep_sweep_engine()
    _register_sprocket_engine(enabled=True)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    sprocket_ws = _get_dep_sweep_ws(ws_root, slug="sprocket-co/sprocket")
    alan_ws = _get_dep_sweep_ws(ws_root, slug=_DEFAULT_SLUG)
    assert len(queue.list_jobs(task_id=sprocket_ws.task_id, status=JobStatus.PENDING)) == 1
    assert len(queue.list_jobs(task_id=alan_ws.task_id, status=JobStatus.PENDING)) == 1

    from alan.engine_config import unregister_and_persist

    unregister_and_persist(RepoIdentity(owner="sprocket-co", repo="sprocket"))
    _register_sprocket_engine(enabled=False)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)

    assert len(queue.list_jobs(task_id=alan_ws.task_id, status=JobStatus.PENDING)) == 1
    assert len(queue.list_jobs(task_id=sprocket_ws.task_id, status=JobStatus.PENDING)) == 1


def test_seed_dep_sweep_skips_when_pending_job_exists(queue: Queue, ws_root: Path) -> None:
    _register_default_dep_sweep_engine()
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = _get_dep_sweep_ws(ws_root)
    assert len(queue.list_jobs(task_id=dep_ws.task_id)) == 1


def test_seed_dep_sweep_skips_when_running_job_exists(queue: Queue, ws_root: Path) -> None:
    _register_default_dep_sweep_engine()
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    claimed = queue.claim()
    assert claimed is not None
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = _get_dep_sweep_ws(ws_root)
    assert len(queue.list_jobs(task_id=dep_ws.task_id)) == 1
    assert queue.list_jobs(task_id=dep_ws.task_id)[0].status is JobStatus.RUNNING


def test_seed_dep_sweep_reenqueues_when_workspace_exists_but_no_jobs(
    queue: Queue, ws_root: Path
) -> None:
    _register_default_dep_sweep_engine()
    dep_ws = create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root, engine_key=_DEFAULT_SLUG
    )
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    jobs = queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "dependency_sweep"


def test_seed_dep_sweep_prunes_exhausted_invocation_slots_via_registry(
    queue: Queue, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_default_dep_sweep_engine()
    dep_ws = create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root, engine_key=_DEFAULT_SLUG
    )
    monkeypatch.setattr(workflow_mod, "_MAX_INV_SLOT", 3)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(3):
        workflow_mod.create_invocation_dir(dep_ws)

    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)

    inv_dirs = sorted(p.name for p in (dep_ws.path / "invocations").iterdir())
    assert inv_dirs == ["inv_001", "inv_003"]
    audit_path = tmp_path / "logs" / "dep_sweep_audit.jsonl"
    rows = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    pruned_rows = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["removed"] == 2
    assert pruned_rows[0]["slug"] == _DEFAULT_SLUG


def test_seed_dep_sweep_swallows_exception(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _register_default_dep_sweep_engine()

    def _boom(*, root: object = None) -> list[object]:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_mod, "list_workspaces", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    assert any("seed_dep_sweep failed" in r.message for r in caplog.records)


def test_seed_dep_sweep_schedules_next_tuesday_after_seeding(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    _register_default_dep_sweep_engine()
    fixed_now = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    raw = queue.get_meta(reconciler_mod._dep_sweep_next_run_key(_DEFAULT_SLUG))
    assert raw is not None
    next_run = datetime.fromtimestamp(float(raw), tz=UTC)
    assert next_run == datetime(2026, 6, 23, 9, 0, 0, tzinfo=UTC)
    assert next_run > fixed_now
    assert next_run - fixed_now < timedelta(weeks=2)


def test_seed_dep_sweep_cadence_guard_skips_when_not_yet_due(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_default_dep_sweep_engine()
    future_epoch = (datetime(2026, 6, 23, 9, 0, 0, tzinfo=UTC)).timestamp()
    queue.set_meta(reconciler_mod._dep_sweep_next_run_key(_DEFAULT_SLUG), str(future_epoch))
    fixed_now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    all_jobs = queue.list_jobs()
    assert all_jobs == []
    all_ws = workspace_mod.list_workspaces(root=ws_root)
    dep_ws = next(
        (w for w in all_ws if w.meta.team == "dependency_sweep"),
        None,
    )
    assert dep_ws is None


def test_seed_dep_sweep_cadence_guard_honors_legacy_bare_key_for_default_engine(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_default_dep_sweep_engine()
    future_epoch = (datetime(2026, 6, 23, 9, 0, 0, tzinfo=UTC)).timestamp()
    queue.set_meta(reconciler_mod._DEP_SWEEP_NEXT_RUN_META_KEY, str(future_epoch))
    fixed_now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)

    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)

    all_ws = workspace_mod.list_workspaces(root=ws_root)
    assert not any(w.meta.team == "dependency_sweep" for w in all_ws)


def test_seed_dep_sweep_legacy_bare_key_fallback_never_applies_to_non_default_engine(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_sprocket_engine(enabled=True)
    future_epoch = (datetime(2026, 6, 23, 9, 0, 0, tzinfo=UTC)).timestamp()
    queue.set_meta(reconciler_mod._DEP_SWEEP_NEXT_RUN_META_KEY, str(future_epoch))
    fixed_now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)

    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)

    sprocket_ws = _get_dep_sweep_ws(ws_root, slug="sprocket-co/sprocket")
    assert len(queue.list_jobs(task_id=sprocket_ws.task_id, status=JobStatus.PENDING)) == 1


def test_seed_dep_sweep_cadence_guard_due_but_workspace_busy(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_default_dep_sweep_engine()
    past_epoch = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC).timestamp()
    fixed_now = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = _get_dep_sweep_ws(ws_root)
    assert len(queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)) == 1
    queue.set_meta(reconciler_mod._dep_sweep_next_run_key(_DEFAULT_SLUG), str(past_epoch))
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    assert len(queue.list_jobs(task_id=dep_ws.task_id)) == 1


def test_seed_dep_sweep_corrupted_meta_treated_as_no_schedule(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_default_dep_sweep_engine()
    queue.set_meta(reconciler_mod._dep_sweep_next_run_key(_DEFAULT_SLUG), "not-a-number")
    fixed_now = datetime(2026, 6, 16, 10, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    dep_ws = _get_dep_sweep_ws(ws_root)
    assert len(queue.list_jobs(task_id=dep_ws.task_id, status=JobStatus.PENDING)) == 1


def test_seed_dep_sweep_schedules_this_tuesday_when_before_slot(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    _register_default_dep_sweep_engine()
    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciler_mod, "_utc_now", lambda: fixed_now)
    reconciler_mod.seed_dep_sweep(queue=queue, workspaces_root=ws_root)
    raw = queue.get_meta(reconciler_mod._dep_sweep_next_run_key(_DEFAULT_SLUG))
    assert raw is not None
    next_run = datetime.fromtimestamp(float(raw), tz=UTC)
    assert next_run == datetime(2026, 6, 16, 9, 0, 0, tzinfo=UTC)
    assert next_run > fixed_now
    assert next_run - fixed_now < timedelta(weeks=1)


def test_reconcile_parks_user_paused_running_job_to_pending(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)
    queue.pause_task(ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == []
    assert report.errors == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.PENDING
    del job_id


def test_reconcile_paused_running_job_handles_requeue_error(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)
    queue.pause_task(ws.task_id)

    from alan.queue import QueueError

    def _raise(_id: int) -> "Job":
        raise QueueError("mock fail")

    monkeypatch.setattr(queue, "requeue_pending", _raise)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == []
    assert report.errors == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.RUNNING
    del job_id


def test_reconcile_skips_resume_for_archived_workspace(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(ws_root.parent / "logs"))
    ws = create("alpha", "d", root=ws_root)
    workspace_mod.transition(ws, Status.ARCHIVED)
    job_id, _ = _seed_resumable_running_job(queue, ws)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.resumed == []
    assert report.interrupted == [job_id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_load_workspace_uses_workspace_id_for_multi_task_job(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("multi-outer", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="ghost",
        tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    job = queue.enqueue(
        "stepa", "ghost", workspace_id=ws.task_id, triggered_by_invocation_id=inv_id
    )
    claimed = queue.claim()
    assert claimed is not None and claimed.id == job.id
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job.id]
    rows = queue.list_jobs(task_id="stepa")
    assert rows[0].status is JobStatus.FAILED


def test_load_workspace_falls_back_to_task_id_when_workspace_id_empty(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="ghost",
        tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "ghost", workspace_id="", triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None and claimed.id == job.id
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job.id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_load_workspace_legacy_workspace_id_equals_task_id_still_works(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def _create_lineage_job(
    queue: Queue,
    subtask_id: str,
    workspace_id: str,
    inv_id: str,
) -> None:
    job = queue.enqueue(
        subtask_id,
        "pr_merge",
        workspace_id=workspace_id,
        triggered_by_invocation_id=inv_id,
    )
    claimed = queue.claim()
    assert claimed is not None and claimed.id == job.id
    queue.complete(claimed.id)


def _make_stalled_multi_subtask_ws(
    ws_root: Path,
    name: str,
    queue: Queue,
    *,
    pr_number: int = 42,
) -> workspace_mod.Workspace:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create(name, "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-r1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-r2",
            "status": "pending",
            "depends_on": ["inj-r1"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    h = Handover(
        handover_id="pr-merge-rec",
        handover_type="terminal",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"PR #{pr_number} merged",
        pr_number=pr_number,
    )
    save_handover(ws, h)
    _create_lineage_job(queue, "inj-r1", ws.workspace_id, "inv_001")
    return ws


def test_reconcile_stalled_dags_no_workspaces(queue: Queue, ws_root: Path) -> None:
    repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 0


def test_reconcile_stalled_dags_skips_non_active_workspace(queue: Queue, ws_root: Path) -> None:
    ws = create("done-ws", "d", root=ws_root)
    workspace_mod.transition(ws, workspace_mod.Status.COMPLETED)
    repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 0


def test_reconcile_stalled_dags_skips_healthy_workspace(queue: Queue, ws_root: Path) -> None:
    create("single-task", "d", root=ws_root)
    repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 0


def test_reconcile_stalled_dags_repairs_stalled_workspace(
    queue: Queue, ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _make_stalled_multi_subtask_ws(ws_root, "stalled-rec", queue)
    with caplog.at_level(logging.INFO, logger="alan.reconciler"):
        repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 1
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    r1 = next(t for t in reloaded.meta.tasks if t.id == "inj-r1")
    assert r1.status == workspace_mod.TaskStatus.COMPLETED
    jobs = queue.list_jobs(task_id="inj-r2", workspace_id=ws.workspace_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert any("repaired" in r.message for r in caplog.records)


def test_reconcile_stalled_dags_does_not_reenqueue_just_repaired_subtask(
    queue: Queue, ws_root: Path
) -> None:
    ws = _make_stalled_multi_subtask_ws(ws_root, "no-reenqueue-rec", queue)
    reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    r1_jobs = queue.list_jobs(
        task_id="inj-r1", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert r1_jobs == []


def test_reconcile_stalled_dags_swallows_per_workspace_exception(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _make_stalled_multi_subtask_ws(ws_root, "explode-rec", queue)

    def _boom(ws: object, task_id: object, status: object) -> object:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_mod, "update_task_status", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.reconciler"):
        repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 0
    assert any("stalled-DAG repair failed" in r.message for r in caplog.records)


def test_repair_stalled_dag_no_injected_subtasks(queue: Queue, ws_root: Path) -> None:
    ws = create("no-subtasks", "d", root=ws_root)
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False


def test_repair_stalled_dag_not_stalled_when_already_completed(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("not-stalled", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-q1",
            "status": "completed",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="pr-ns-1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=99,
        ),
    )
    _create_lineage_job(queue, "inj-q1", ws.workspace_id, "inv_001")
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False


def test_terminal_pr_merge_handovers_skips_handover_error(ws_root: Path) -> None:
    ws = create("bad-handover", "d", root=ws_root)
    (ws.path / "handovers").mkdir(exist_ok=True)
    bad_name = f"handover_{ws.task_id}_001.yaml"
    (ws.path / "handovers" / bad_name).write_text("not: valid: yaml: [", encoding="utf-8")
    result = reconciler_mod._terminal_handovers_for_team(ws, "pr_merge")  # noqa: SLF001
    assert result == []


def test_terminal_pr_merge_handovers_skips_non_pr_merge_teams(ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("other-team", "d", root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="not-pr-merge",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="verification",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="approved",
            pr_number=77,
        ),
    )
    result = reconciler_mod._terminal_handovers_for_team(ws, "pr_merge")  # noqa: SLF001
    assert result == []


def test_terminal_pr_merge_handovers_skips_none_pr_number(ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("no-pr-num", "d", root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="pr-merge-no-num",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=None,
        ),
    )
    result = reconciler_mod._terminal_handovers_for_team(ws, "pr_merge")  # noqa: SLF001
    assert result == []


def test_terminal_pr_merge_handovers_skips_non_terminal_type(ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("escalated-ws", "d", root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="esc-1",
            handover_type="escalation",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="escalated",
            pr_number=99,
        ),
    )
    result = reconciler_mod._terminal_handovers_for_team(ws, "pr_merge")  # noqa: SLF001
    assert result == []


def test_injected_subtasks_topo_no_subtasks(ws_root: Path) -> None:
    ws = create("no-inj", "d", root=ws_root)
    result = reconciler_mod._injected_subtasks_topo(ws)  # noqa: SLF001
    assert result == []


def test_injected_subtasks_topo_with_cycle_remainder(ws_root: Path) -> None:
    ws = create("cycle-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {"id": "inj-x1", "status": "pending", "depends_on": ["inj-x2"], "injection_depth": 1},
        {"id": "inj-x2", "status": "pending", "depends_on": ["inj-x1"], "injection_depth": 1},
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    result = reconciler_mod._injected_subtasks_topo(ws)  # noqa: SLF001
    assert len(result) == 2


def test_injected_subtasks_topo_dep_outside_injected_set(ws_root: Path) -> None:
    ws = create("ext-dep-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {"id": "inj-e1", "status": "pending", "depends_on": ["root-task"], "injection_depth": 1},
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    result = reconciler_mod._injected_subtasks_topo(ws)  # noqa: SLF001
    assert len(result) == 1


def test_injected_subtasks_topo_multi_dep_stays_blocked(ws_root: Path) -> None:
    ws = create("multi-dep-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {"id": "inj-m1", "status": "pending", "depends_on": [], "injection_depth": 1},
        {"id": "inj-m2", "status": "pending", "depends_on": [], "injection_depth": 1},
        {
            "id": "inj-m3",
            "status": "pending",
            "depends_on": ["inj-m1", "inj-m2"],
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    result = reconciler_mod._injected_subtasks_topo(ws)  # noqa: SLF001
    assert len(result) == 3
    assert result[2].id == "inj-m3"


def test_repair_stalled_dag_skips_subtask_with_no_team(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("no-team-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-nt1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-nt2",
            "status": "pending",
            "depends_on": ["inj-nt1"],
            "team": None,
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="pr-no-team",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=50,
        ),
    )
    _create_lineage_job(queue, "inj-nt1", ws.workspace_id, "inv_001")
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is True
    research_jobs = [j for j in queue.list_jobs() if j.team == "research"]
    assert research_jobs == []


def test_repair_stalled_dag_skips_already_queued_subtask(queue: Queue, ws_root: Path) -> None:
    ws = _make_stalled_multi_subtask_ws(ws_root, "already-queued", queue)
    queue.enqueue("inj-r2", "research", workspace_id=ws.workspace_id)
    reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    jobs = queue.list_jobs(task_id="inj-r2", workspace_id=ws.workspace_id, status=JobStatus.PENDING)
    assert len(jobs) == 1


def test_repair_stalled_dag_skips_non_pending_subtask(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("non-pending-sub", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-np1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-np2",
            "status": "failed",
            "depends_on": ["inj-np1"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="pr-np",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=60,
        ),
    )
    _create_lineage_job(queue, "inj-np1", ws.workspace_id, "inv_001")
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is True
    research_jobs = [j for j in queue.list_jobs() if j.team == "research"]
    assert research_jobs == []


def test_repair_stalled_dag_skips_subtask_with_unmet_deps(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("unmet-deps", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-ud1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-ud2",
            "status": "pending",
            "depends_on": ["inj-ud1"],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-ud3",
            "status": "pending",
            "depends_on": ["inj-ud2"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="pr-ud",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="merged",
            pr_number=70,
        ),
    )
    _create_lineage_job(queue, "inj-ud1", ws.workspace_id, "inv_001")
    reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    ud3_jobs = queue.list_jobs(
        task_id="inj-ud3", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert ud3_jobs == []


def test_repair_dag_non_terminal_handover_not_counted(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("esc-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-e1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="esc-h1",
            handover_type="escalation",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="escalated before merge",
            pr_number=42,
        ),
    )
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    subtask = next(t for t in reloaded.meta.tasks if t.id == "inj-e1")
    assert subtask.status == workspace_mod.TaskStatus.PENDING


def test_repair_dag_duplicate_pr_same_subtask_counts_once(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("dup-pr-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-d1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-d2",
            "status": "pending",
            "depends_on": ["inj-d1"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    now = datetime.now(UTC)
    for hid, iid in [("h-dup-a", "inv_001"), ("h-dup-b", "inv_002")]:
        save_handover(
            ws,
            Handover(
                handover_id=hid,
                handover_type="terminal",
                task_id=ws.task_id,
                from_team="pr_merge",
                from_invocation_id=iid,
                to_team_suggestions=[],
                iteration=1,
                timestamp_utc=now,
                full_synthesis="PR #42 merged",
                pr_number=42,
            ),
        )
    _create_lineage_job(queue, "inj-d1", ws.workspace_id, "inv_001")
    _create_lineage_job(queue, "inj-d1", ws.workspace_id, "inv_002")
    reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    d1 = next(t for t in reloaded.meta.tasks if t.id == "inj-d1")
    d2 = next(t for t in reloaded.meta.tasks if t.id == "inj-d2")
    assert d1.status == workspace_mod.TaskStatus.COMPLETED
    assert d2.status == workspace_mod.TaskStatus.PENDING


def test_repair_dag_lineage_assigns_correct_pr_not_positional(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("lineage-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-la",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
            "dod_clauses": [{"id": "c-la", "text": "delivered"}],
        },
        {
            "id": "inj-lb",
            "status": "pending",
            "depends_on": ["inj-la"],
            "team": "research",
            "injection_depth": 1,
            "dod_clauses": [{"id": "c-lb", "text": "delivered"}],
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    now = datetime.now(UTC)
    save_handover(
        ws,
        Handover(
            handover_id="h-lb",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_b",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=now,
            full_synthesis="PR #43 merged",
            pr_number=43,
        ),
    )
    save_handover(
        ws,
        Handover(
            handover_id="h-la",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_a",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=now,
            full_synthesis="PR #42 merged",
            pr_number=42,
        ),
    )
    _create_lineage_job(queue, "inj-la", ws.workspace_id, "inv_a")
    _create_lineage_job(queue, "inj-lb", ws.workspace_id, "inv_b")
    reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    la = next(t for t in reloaded.meta.tasks if t.id == "inj-la")
    lb = next(t for t in reloaded.meta.tasks if t.id == "inj-lb")
    assert la.status == workspace_mod.TaskStatus.COMPLETED
    assert "PR #42" in (la.dod_clauses[0].evidence or "")
    assert lb.status == workspace_mod.TaskStatus.COMPLETED
    assert "PR #43" in (lb.dod_clauses[0].evidence or "")


def test_repair_dag_live_pending_subtask_skipped(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("live-pending-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-lp1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="h-lp1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #55 merged",
            pr_number=55,
        ),
    )
    _create_lineage_job(queue, "inj-lp1", ws.workspace_id, "inv_001")
    queue.enqueue("inj-lp1", "research", workspace_id=ws.workspace_id)
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    subtask = next(t for t in reloaded.meta.tasks if t.id == "inj-lp1")
    assert subtask.status == workspace_mod.TaskStatus.PENDING


def test_repair_dag_live_running_subtask_skipped(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("live-running-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-lr1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="h-lr1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #66 merged",
            pr_number=66,
        ),
    )
    _create_lineage_job(queue, "inj-lr1", ws.workspace_id, "inv_001")
    live_job = queue.enqueue("inj-lr1", "research", workspace_id=ws.workspace_id)
    claimed = queue.claim()
    assert claimed is not None and claimed.id == live_job.id
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    subtask = next(t for t in reloaded.meta.tasks if t.id == "inj-lr1")
    assert subtask.status == workspace_mod.TaskStatus.PENDING


def test_repair_dag_ambiguous_pr_both_subtasks_skipped(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("amb-pr-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-am1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-am2",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    now = datetime.now(UTC)
    save_handover(
        ws,
        Handover(
            handover_id="h-am1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_am1",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=now,
            full_synthesis="PR #77 merged",
            pr_number=77,
        ),
    )
    save_handover(
        ws,
        Handover(
            handover_id="h-am2",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_am2",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=now,
            full_synthesis="PR #77 merged again",
            pr_number=77,
        ),
    )
    _create_lineage_job(queue, "inj-am1", ws.workspace_id, "inv_am1")
    _create_lineage_job(queue, "inj-am2", ws.workspace_id, "inv_am2")
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    for tid in ["inj-am1", "inj-am2"]:
        subtask = next(t for t in reloaded.meta.tasks if t.id == tid)
        assert subtask.status == workspace_mod.TaskStatus.PENDING


def test_repair_dag_no_lineage_declines_repair(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("no-lineage-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-nl1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="h-nl1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_unknown",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #88 merged",
            pr_number=88,
        ),
    )
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False


def test_repair_dag_dep_guard_blocks_completion_with_unmet_dep(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("dep-guard-ws", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-g1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
        },
        {
            "id": "inj-g2",
            "status": "pending",
            "depends_on": ["inj-g1"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    now = datetime.now(UTC)
    save_handover(
        ws,
        Handover(
            handover_id="h-g2",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_g2",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=now,
            full_synthesis="PR #100 merged",
            pr_number=100,
        ),
    )
    _create_lineage_job(queue, "inj-g2", ws.workspace_id, "inv_g2")
    result = reconciler_mod._repair_stalled_dag(ws, queue)  # noqa: SLF001
    assert result is False
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    g2 = next(t for t in reloaded.meta.tasks if t.id == "inj-g2")
    assert g2.status == workspace_mod.TaskStatus.PENDING


def test_accum_delivery_first_assignment(ws_root: Path) -> None:
    deliveries: dict[str, int] = {}
    conflicted: set[str] = set()
    reconciler_mod._accum_delivery(deliveries, "sub-a", 10, conflicted)  # noqa: SLF001
    assert deliveries == {"sub-a": 10}
    assert conflicted == set()


def test_accum_delivery_second_different_pr_marks_conflicted(ws_root: Path) -> None:
    deliveries: dict[str, int] = {}
    conflicted: set[str] = set()
    reconciler_mod._accum_delivery(deliveries, "sub-a", 10, conflicted)  # noqa: SLF001
    reconciler_mod._accum_delivery(deliveries, "sub-a", 20, conflicted)  # noqa: SLF001
    assert "sub-a" not in deliveries
    assert "sub-a" in conflicted


def test_accum_delivery_conflicted_early_return(ws_root: Path) -> None:
    deliveries: dict[str, int] = {}
    conflicted: set[str] = set()
    reconciler_mod._accum_delivery(deliveries, "sub-a", 10, conflicted)  # noqa: SLF001
    reconciler_mod._accum_delivery(deliveries, "sub-a", 20, conflicted)  # noqa: SLF001
    assert "sub-a" in conflicted
    reconciler_mod._accum_delivery(deliveries, "sub-a", 30, conflicted)  # noqa: SLF001
    assert "sub-a" not in deliveries
    assert "sub-a" in conflicted


def test_repair_dag_idempotent_across_restart(queue: Queue, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    ws = create("idempotent-restart", "d", root=ws_root)
    meta_path = ws.path / "meta.json"
    data = _json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "inj-r1",
            "status": "pending",
            "depends_on": [],
            "team": "research",
            "injection_depth": 1,
            "dod_clauses": [{"id": "c-r1", "text": "delivered r1"}],
        },
        {
            "id": "inj-r2",
            "status": "pending",
            "depends_on": ["inj-r1"],
            "team": "research",
            "injection_depth": 1,
        },
    ]
    meta_path.write_text(_json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id="h-r1",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="pr_merge",
            from_invocation_id="inv_r1",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="PR #42 merged",
            pr_number=42,
        ),
    )
    _create_lineage_job(queue, "inj-r1", ws.workspace_id, "inv_r1")

    repaired = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired == 1
    after_first = workspace_mod.read(ws.task_id, root=ws_root)
    r1_first = next(t for t in after_first.meta.tasks if t.id == "inj-r1")
    assert r1_first.status == workspace_mod.TaskStatus.COMPLETED
    evidence_after_first = r1_first.dod_clauses[0].evidence if r1_first.dod_clauses else None
    r2_jobs_after_first = queue.list_jobs(
        task_id="inj-r2", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert len(r2_jobs_after_first) == 1

    repaired_second = reconciler_mod.reconcile_stalled_dags(queue=queue, workspaces_root=ws_root)
    assert repaired_second == 0

    after_second = workspace_mod.read(ws.task_id, root=ws_root)
    r1_second = next(t for t in after_second.meta.tasks if t.id == "inj-r1")
    assert r1_second.status == workspace_mod.TaskStatus.COMPLETED
    assert r1_second.dod_clauses[0].evidence == evidence_after_first
    r2_jobs_after_second = queue.list_jobs(
        task_id="inj-r2", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert len(r2_jobs_after_second) == 1


def _make_replay_handover(task_id: str, *, inv_id: str) -> Handover:
    return Handover(
        handover_id=f"h-replay-{task_id}",
        handover_type="forward",
        task_id=task_id,
        from_team="research",
        from_invocation_id=inv_id,
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="done",
    )


def _finalize_workflow_success(ws: Workspace, inv_id: str) -> None:
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.complete_node("w")
    graph.finalize(status="success")
    save_workflow(ws, graph.doc)


def _seed_replay_scenario(
    queue: Queue,
    ws: Workspace,
) -> tuple[int, str, Handover]:
    from alan.handover import save_handover

    job_id, inv_id = _seed_running_job(queue, ws)
    _finalize_workflow_success(ws, inv_id)
    h = _make_replay_handover(ws.task_id, inv_id=inv_id)
    save_handover(ws, h)
    return job_id, inv_id, h


def test_replay_handover_invokes_routing_on_success_workflow(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-main", "d", root=ws_root)
    job_id, inv_id, handover = _seed_replay_scenario(queue, ws)

    routing_calls: list[tuple[Job, object]] = []

    def fake_routing(job: Job, h: object) -> None:
        routing_calls.append((job, h))

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub, replay_routing=fake_routing
    )
    assert len(routing_calls) == 1
    assert routing_calls[0][0].id == job_id
    assert routing_calls[0][1] is handover or (
        getattr(routing_calls[0][1], "handover_id", None) == handover.handover_id
    )
    assert report.interrupted == []
    assert report.resumed == []
    assert report.replayed == [job_id]


def test_replay_handover_returns_false_when_workspace_deleted_but_routing_set(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    import shutil

    ws = create("replay-no-ws", "d", root=ws_root)
    job_id, inv_id, _ = _seed_replay_scenario(queue, ws)
    shutil.rmtree(ws.path)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, h: routing_calls.append(h),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_returns_false_when_no_inv_id_but_routing_set(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-no-inv", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    claimed = queue.claim()
    assert claimed is not None

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, h: routing_calls.append(h),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_returns_false_when_workflow_not_success(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    from alan.handover import save_handover

    ws = create("replay-not-success", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    h = _make_replay_handover(ws.task_id, inv_id=inv_id)
    save_handover(ws, h)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_returns_false_when_workflow_file_missing(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    from alan.handover import save_handover

    ws = create("replay-no-wf", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    workflow_path(ws, inv_id).unlink()
    h = _make_replay_handover(ws.task_id, inv_id=inv_id)
    save_handover(ws, h)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_returns_false_when_workflow_load_raises(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    from alan.handover import save_handover

    ws = create("replay-corrupt-wf", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    wf_path = workflow_path(ws, inv_id)
    wf_path.write_text("{corrupt: yaml: [[[", encoding="utf-8")
    h = _make_replay_handover(ws.task_id, inv_id=inv_id)
    save_handover(ws, h)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_returns_false_when_handover_inv_mismatch(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    from alan.handover import save_handover

    ws = create("replay-inv-mismatch", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    _finalize_workflow_success(ws, inv_id)
    h = _make_replay_handover(ws.task_id, inv_id="inv_999")
    save_handover(ws, h)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_skips_unreadable_handover_file_and_falls_through(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-corrupt-h", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws)
    _finalize_workflow_success(ws, inv_id)

    handover_dir = ws.path / "handovers"
    handover_dir.mkdir(parents=True, exist_ok=True)
    corrupt = handover_dir / f"handover_{ws.task_id}_001.yaml"
    corrupt.write_text("{bad: yaml: [[[", encoding="utf-8")

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED


def test_replay_handover_completes_zombie_for_terminal_workspace(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-terminal-ws", "d", root=ws_root)
    job_id, inv_id, _ = _seed_replay_scenario(queue, ws)
    workspace_mod.transition(ws, Status.COMPLETED)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.COMPLETED


def test_replay_handover_completes_zombie_when_successor_exists(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-successor", "d", root=ws_root)
    job_id, inv_id, handover = _seed_replay_scenario(queue, ws)

    create("replay-successor-child", "d", root=ws_root)
    queue.enqueue(
        ws.task_id,
        "implementation_plan",
        parent_handover_id=handover.handover_id,
    )

    routing_calls: list[object] = []
    report = reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []
    zombie_rows = queue.list_jobs(task_id=ws.task_id, status=JobStatus.COMPLETED)
    assert any(r.id == job_id for r in zombie_rows)
    assert report.replayed == [job_id]


def test_replay_handover_complete_zombie_swallows_queue_error(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.queue import Queue as _Queue

    ws = create("replay-zombie-err", "d", root=ws_root)
    job_id, inv_id, handover = _seed_replay_scenario(queue, ws)
    workspace_mod.transition(ws, Status.COMPLETED)

    def boom_complete(self: _Queue, job_id: int) -> object:
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(_Queue, "complete", boom_complete)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )
    assert routing_calls == []


def test_replay_handover_writes_audit_record(
    queue: Queue, pubsub: PubSub, ws_root: Path, tmp_path: Path
) -> None:
    import json
    import os

    ws = create("replay-audit", "d", root=ws_root)
    job_id, inv_id, handover = _seed_replay_scenario(queue, ws)

    routing_calls: list[object] = []
    reconciler_mod.reconcile_on_startup(
        queue=queue,
        workspaces_root=ws_root,
        pubsub=pubsub,
        replay_routing=lambda job, hv: routing_calls.append(hv),
    )

    audit_path = Path(os.environ["ALAN_LOGS_ROOT"]) / "handover_replay_audit.jsonl"
    assert audit_path.exists()
    lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    assert any(
        r["outcome"] == "handover_replay_invoked" and r["task_id"] == ws.task_id for r in lines
    )


def test_replay_routing_exception_falls_through_to_failure(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("replay-raises", "d", root=ws_root)
    job_id, inv_id, _ = _seed_replay_scenario(queue, ws)

    def raising_routing(job: Job, h: object) -> None:
        raise RuntimeError("routing exploded")

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub, replay_routing=raising_routing
    )
    rows = queue.list_jobs(task_id=ws.task_id)
    assert rows[0].status is JobStatus.FAILED
    assert report.replayed == []
    assert report.interrupted == [job_id]


def test_rederive_running_deploy_verify_job_enqueued_fresh(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("dv-rd-happy", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="deploy_verify")
    events = _collect_events(pubsub, ws.task_id)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id not in report.interrupted
    assert report.errors == []
    failed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.FAILED)
    assert any(j.id == job_id for j in failed)
    fresh = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert any(j.team == "deploy_verify" and j.triggered_by_invocation_id != inv_id for j in fresh)
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.ACTIVE
    assert any(e.kind == "deploy_verify_rederived" for e in events)


def test_rederive_deploy_verify_skipped_when_terminal_handover_exists(
    queue: Queue,
    pubsub: PubSub,
    ws_root: Path,
) -> None:
    from alan.handover import Handover, save_handover

    ws = create("dv-rd-terminal", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="deploy_verify")
    save_handover(
        ws,
        Handover(
            handover_id="h-dv-done",
            handover_type="terminal",
            task_id=ws.task_id,
            from_team="deploy_verify",
            from_invocation_id=inv_id,
            to_team_suggestions=[],
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="deploy confirmed PR #42",
            pr_number=42,
        ),
    )
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert job_id in report.interrupted
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.AWAITING_INPUT


def test_reconcile_health_fixture_runs_after_normal_sweep_and_report_unaffected(
    queue: Queue, pubsub: PubSub, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_E2E_HEALTH_FIXTURE", "1")
    from alan.health import HEALTH_META_KEY

    ws = create("alpha", "d", root=ws_root)
    job_id, _ = _seed_running_job(queue, ws)
    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )
    assert report.interrupted == [job_id]
    assert report.errors == []
    assert queue.get_meta(HEALTH_META_KEY) is not None
    assert queue.paused_until() is not None
