from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import _reconciler_never_started as never_started_mod
from alan import reconciler as reconciler_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.events import PubSub
from alan.handover import Handover, save_handover
from alan.queue import Job, JobStatus, Queue
from alan.workflow import PendingTaskSpec, save_workflow, seed_pending_workflow
from alan.workspace import Status, Workspace, create


def _running_job(ws: Workspace, *, team: str) -> Job:
    return Job(
        id=1, task_id=ws.task_id, workspace_id=ws.workspace_id, team=team,
        status=JobStatus.RUNNING, enqueued_at=datetime.now(UTC),
    )


def _seed_running_job(
    queue: Queue, ws: Workspace, *, team: str = "verification"
) -> tuple[int, str]:
    inv_id = seed_pending_workflow(
        ws, team=team, tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    job = queue.enqueue(ws.task_id, team, triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    return job.id, inv_id


def _seed_running_node_with_session(ws: Workspace, inv_id: str) -> None:
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    graph.set_session_id("w", "sid-existing")
    save_workflow(ws, graph.doc)


def test_resume_would_lose_captured_session_blocks_when_never_attempted(
    ws_root: Path,
) -> None:
    ws = create("guard-never-attempted", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    _seed_running_node_with_session(ws, inv_id)
    job = _running_job(ws, team="verification")
    assert reconciler_mod._resume_would_lose_captured_session(  # noqa: SLF001
        job, ws, inv_id, resume_was_attempted=False
    ) is True


def test_resume_would_lose_captured_session_allows_when_attempted(ws_root: Path) -> None:
    ws = create("guard-attempted", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws, team="verification", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    _seed_running_node_with_session(ws, inv_id)
    job = _running_job(ws, team="verification")
    assert reconciler_mod._resume_would_lose_captured_session(  # noqa: SLF001
        job, ws, inv_id, resume_was_attempted=True
    ) is False


def test_try_resume_reports_not_attempted_when_team_does_not_support_resume(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("attempt-flag-none", "d", root=ws_root)
    job_id, _inv_id = _seed_running_job(queue, ws, team="dependency_sweep")
    job = queue.list_jobs(task_id=ws.task_id)[0]
    assert job.id == job_id
    report = reconciler_mod.ReconciliationReport()
    attempt = reconciler_mod._try_resume(job, queue, ws, pubsub, report)  # noqa: SLF001
    assert attempt.succeeded is False
    assert attempt.attempted is False


def test_try_resume_reports_attempted_true_when_interrupt_finds_no_interrupted_node(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("attempt-flag-interrupt-fail", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="verification")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    save_workflow(ws, graph.doc)
    job = queue.list_jobs(task_id=ws.task_id)[0]
    assert job.id == job_id
    report = reconciler_mod.ReconciliationReport()
    attempt = reconciler_mod._try_resume(job, queue, ws, pubsub, report)  # noqa: SLF001
    assert attempt.succeeded is False
    assert attempt.attempted is True


def test_try_resume_reports_succeeded_and_attempted_on_full_resume(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("attempt-flag-success", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="verification")
    _seed_running_node_with_session(ws, inv_id)
    job = queue.list_jobs(task_id=ws.task_id)[0]
    assert job.id == job_id
    report = reconciler_mod.ReconciliationReport()
    attempt = reconciler_mod._try_resume(job, queue, ws, pubsub, report)  # noqa: SLF001
    assert attempt.succeeded is True
    assert attempt.attempted is True


def test_fail_workspace_message_names_the_restart_as_cause(ws_root: Path) -> None:
    ws = create("restart-park-message", "d", root=ws_root)
    reconciler_mod._fail_workspace(ws)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert len(reloaded.meta.blockers) == 1
    detail = reloaded.meta.blockers[0].detail
    assert "restart" in detail.lower()
    assert "re-dispatch" in detail.lower()


def test_rederive_eligible_blocked_by_guard_when_resume_never_attempted(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("rederive-guard-direct", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="verification")
    _seed_running_node_with_session(ws, inv_id)
    job = queue.list_jobs(task_id=ws.task_id)[0]
    assert job.id == job_id
    assert reconciler_mod._rederive_eligible(  # noqa: SLF001
        job, queue, ws, resume_was_attempted=False
    ) is None


def test_terminal_fallback_leaves_resume_inv_id_absent_for_non_resumable_team(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("terminal-non-resumable", "d", root=ws_root)
    job_id, _inv_id = _seed_running_job(queue, ws, team="dependency_sweep")
    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.id == job_id
    assert row.status is JobStatus.FAILED
    assert row.resume_inv_id is None


def test_reconcile_resumes_parallel_stage_when_one_sibling_never_reported_session_id(
    queue: Queue, pubsub: PubSub, ws_root: Path
) -> None:
    ws = create("mixed-stage-recovery", "d", root=ws_root)
    inv_id = seed_pending_workflow(
        ws,
        team="verification",
        tasks=[
            PendingTaskSpec(id="diff_reviewer", role="diff_reviewer", model="sonnet"),
            PendingTaskSpec(id="scenario_runner", role="scenario_runner", model="sonnet"),
        ],
    )
    job = queue.enqueue(ws.task_id, "verification", triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("diff_reviewer", role="diff_reviewer", model="sonnet")
    graph.set_session_id("diff_reviewer", "sid-diff")
    graph.start_node("scenario_runner", role="scenario_runner", model="sonnet")
    save_workflow(ws, graph.doc)

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.resumed == [job.id]
    assert report.interrupted == []
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    statuses = {t.id: t.status for t in doc.tasks}
    assert statuses == {"diff_reviewer": "interrupted", "scenario_runner": "pending"}
    reloaded_graph = workflow_mod.load_graph(ws, inv_id)
    reactivated = reloaded_graph.start_node(
        "scenario_runner", role="scenario_runner", model="sonnet"
    )
    assert reactivated.status == "running"
    assert reactivated.error is None


def test_reconcile_never_started_job_reruns_automatically(
    queue: Queue, pubsub: PubSub, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("never-started-e2e", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("w", role="w", model="sonnet")
    save_workflow(ws, graph.doc)

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.rerun == [job_id]
    assert report.interrupted == []
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id is None
    assert row.triggered_by_invocation_id == inv_id
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "pending"
    assert doc.status == "running"
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.ACTIVE
    audit_path = tmp_path / "logs" / "resume_audit.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert rows[-1]["outcome"] == "rerun_never_started"
    assert rows[-1]["inv_id"] == inv_id


def test_reconcile_never_started_job_exhausted_parks_visibly(
    queue: Queue, pubsub: PubSub, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("never-started-exhausted", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    for _ in range(never_started_mod.RERUN_CAP):
        queue.requeue_pending(job_id)
        claimed = queue.claim()
        assert claimed is not None

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.rerun == []
    assert report.interrupted == [job_id]
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.FAILED
    assert queue.list_resumable_failed_by_workspace(ws.workspace_id) == []
    ws_reload = workspace_mod.read(ws.task_id, root=ws_root)
    assert ws_reload.meta.status is Status.AWAITING_INPUT
    assert len(ws_reload.meta.blockers) == 1
    assert ws_reload.meta.blockers[0].detail == never_started_mod.RERUN_CAP_MESSAGE
    audit_path = tmp_path / "logs" / "resume_audit.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert rows[-1]["outcome"] == "rerun_cap_exhausted"
    assert rows[-1]["inv_id"] == inv_id


def test_reconcile_started_and_resumable_job_still_resumes(
    queue: Queue, pubsub: PubSub, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("started-resumable", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    _seed_running_node_with_session(ws, inv_id)

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=pubsub
    )

    assert report.rerun == []
    assert report.resumed == [job_id]
    assert report.interrupted == []
    row = queue.list_jobs(task_id=ws.task_id)[0]
    assert row.status is JobStatus.PENDING
    assert row.resume_inv_id == inv_id


def test_reconcile_terminal_fallback_audit_carries_real_invocation_id(
    queue: Queue, pubsub: PubSub, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("terminal-real-inv-id", "d", root=ws_root)
    _job_id, inv_id = _seed_running_job(queue, ws, team="dependency_sweep")

    reconciler_mod.reconcile_on_startup(queue=queue, workspaces_root=ws_root, pubsub=pubsub)

    audit_path = tmp_path / "logs" / "resume_audit.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert rows[-1]["outcome"] == "interrupted_failed"
    assert rows[-1]["inv_id"] == inv_id
    assert rows[-1]["inv_id"] != ""


def test_replay_unrouted_handover_wins_over_never_started_rerun(
    queue: Queue, ws_root: Path
) -> None:
    ws = create("replay-vs-rerun", "d", root=ws_root)
    job_id, inv_id = _seed_running_job(queue, ws, team="implementation_fix")
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.doc.status = "success"
    save_workflow(ws, graph.doc)
    save_handover(
        ws,
        Handover(
            handover_id="h-replay",
            handover_type="forward",
            task_id=ws.task_id,
            from_team="implementation_fix",
            from_invocation_id=inv_id,
            to_team_suggestions=["pr_merge"],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="done",
        ),
    )
    calls: list[tuple[Job, Handover]] = []

    def replay_routing(job: Job, handover: Handover) -> None:
        calls.append((job, handover))

    report = reconciler_mod.reconcile_on_startup(
        queue=queue, workspaces_root=ws_root, pubsub=PubSub(), replay_routing=replay_routing
    )

    assert report.replayed == [job_id]
    assert report.rerun == []
    assert len(calls) == 1
