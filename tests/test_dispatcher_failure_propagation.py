from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.queue import JobStatus, Queue
from alan.workflow import PendingTaskSpec
from alan.workspace import TaskStatus, Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _make_collector, _write_multi_task_meta
from tests._dispatcher_support_teams import (
    _make_resumable_team,
    _NoopTeam,
    _RaisingTeam,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_team_exception_marks_job_failed(queue: Queue, root: Path) -> None:
    workspace_mod.create("blow up", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root)
    d.pubsub.subscribe("blow-up", collect)
    queue.enqueue("blow-up", "research")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert finished.error == "kaboom"
    kinds = [e.kind for e in events]
    assert kinds == ["job_started", "job_failed"]
    reloaded = workspace_mod.read("blow-up", root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_fail_job_logs_warning_with_error_text(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_mod.create("blow-up", "d", root=root)
    d = Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root)
    queue.enqueue("blow-up", "research")
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d.run_once()
    assert any(
        "job failed" in r.message and "kaboom" in r.message
        for r in caplog.records
    )


def test_fail_job_writes_failure_audit(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    workspace_mod.create("blow-up", "d", root=root)
    d = Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root)
    queue.enqueue("blow-up", "research")
    d.run_once()
    audit = logs_root / "job_failure_audit.jsonl"
    lines = [
        ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task_id"] == "blow-up"
    assert entry["team"] == "research"
    assert entry["error"] == "kaboom"
    assert entry["inv_id"] == "inv_001"


def test_fail_workspace_swallows_oserror_and_logs(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace_mod.create("ws-write-fail", "d", root=root)

    def raising_transition(*_args: object, **_kwargs: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr(
        "alan.dispatcher.workspace_mod.transition", raising_transition
    )
    d = Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root)
    queue.enqueue("ws-write-fail", "research")
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d.run_once()
    assert any("failed to flip workspace" in r.message for r in caplog.records)
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED


def test_fail_job_marks_multi_task_meta_task_failed(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("obs-fail", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "something broke")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    step_a = next(t for t in reloaded.meta.tasks if t.id == "step-a")
    assert step_a.status is TaskStatus.FAILED


def test_fail_job_propagates_failure_to_transitive_dependents(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("obs-prop", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
            {"id": "step-c", "effort": "medium", "status": "pending", "depends_on": ["step-b"]},
        ],
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    step_b = next(t for t in reloaded.meta.tasks if t.id == "step-b")
    step_c = next(t for t in reloaded.meta.tasks if t.id == "step-c")
    assert step_b.status is TaskStatus.FAILED
    assert step_c.status is TaskStatus.FAILED


def test_fail_job_transitions_all_failed_workspace_to_failed(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-all-fail", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.FAILED


def test_fail_job_does_not_update_meta_tasks_for_single_task_workspace(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-single", "d", root=root)
    assert ws.meta.tasks == []
    job = queue.enqueue(ws.task_id, "research")
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks == []


def test_fail_workspace_tasks_guard_uses_fresh_read_not_stale_snapshot(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-decomposed-race", "d", root=root)
    stale = ws
    assert stale.meta.tasks == []
    _write_multi_task_meta(
        ws,
        [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_workspace(stale, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE
    assert reloaded.meta.park_reason is None


def test_fail_job_warns_when_task_id_not_in_meta_tasks(
    queue: Queue,
    root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("obs-nomatch", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    job = queue.enqueue("unknown-task-id", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._fail_job(job, "boom")  # noqa: SLF001
    assert any("not in workspace" in r.message for r in caplog.records)


def test_fail_job_workspace_stays_active_when_non_failed_tasks_remain(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-partial", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "stepa", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "stepb", "effort": "medium", "status": "pending", "depends_on": ["stepa"]},
            {"id": "stepc", "effort": "medium", "status": "pending", "depends_on": []},
        ],
    )
    job = queue.enqueue("stepa", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE


def test_observe_task_failure_recovers_frozen_chain_when_not_terminal(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-frozen", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "fail-me", "status": "running", "depends_on": []},
            {
                "id": "stuck-ancestor",
                "status": "awaiting_input",
                "depends_on": [],
                "blocked_handover_id": "h1",
                "team": "research",
            },
            {
                "id": "stuck-descendant",
                "status": "pending",
                "depends_on": ["stuck-ancestor"],
                "team": "research",
            },
        ],
    )
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    job = queue.enqueue("fail-me", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    fail_me = next(t for t in reloaded.meta.tasks if t.id == "fail-me")
    ancestor = next(t for t in reloaded.meta.tasks if t.id == "stuck-ancestor")
    assert fail_me.status is TaskStatus.FAILED
    assert ancestor.status is TaskStatus.PENDING
    assert ancestor.blocked_handover_id is None
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE


def test_observe_task_failure_parks_frozen_chain_when_cap_exhausted(
    queue: Queue, root: Path
) -> None:
    from alan import workspace_dag as workspace_dag_mod
    from alan.loop_guard import REVISIT_CAP
    from alan.park import ParkReason

    ws = workspace_mod.create("obs-frozen-park", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "fail-me", "status": "running", "depends_on": []},
            {
                "id": "stuck-ancestor",
                "status": "awaiting_input",
                "depends_on": [],
                "blocked_handover_id": "h1",
            },
            {
                "id": "stuck-descendant",
                "status": "pending",
                "depends_on": ["stuck-ancestor"],
            },
        ],
    )
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    for _ in range(REVISIT_CAP):
        ws = workspace_mod.increment_revisit_count(
            ws,
            workspace_dag_mod._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            workspace_dag_mod._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            task_id="stuck-ancestor",
        )
    job = queue.enqueue("fail-me", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.FROZEN_CHAIN


def test_observe_task_failure_swallows_propagation_exception(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("obs-exc", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "stepa", "effort": "medium", "status": "running", "depends_on": []}],
    )
    job = queue.enqueue("stepa", "research", workspace_id=ws.workspace_id)
    queue.claim()

    def boom(_ws: Any, _tid: str) -> Any:
        raise RuntimeError("propagation exploded")

    monkeypatch.setattr(dispatcher_mod, "_propagate_task_failure", boom)
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._fail_job(job, "boom")  # noqa: SLF001
    assert any("_observe_task_failure: failed" in r.message for r in caplog.records)


def test_handle_team_exception_independent_tasks_workspace_stays_active(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("exc-indep", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": []},
        ],
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._handle_team_exception(job, ws, RuntimeError("boom"), "inv_001", _NoopTeam())  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    step_b = next(t for t in reloaded.meta.tasks if t.id == "step-b")
    assert step_b.status is TaskStatus.PENDING
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE


def test_fail_genuine_marks_recoverable_when_team_supports_resume(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-recoverable", "d", root=root)
    ResumableTeam = _make_resumable_team()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    d._fail_genuine(job, ws, RuntimeError("genuine node failure"), inv_id, ResumableTeam())  # noqa: SLF001
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert finished.resume_inv_id == inv_id
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_fail_genuine_not_recoverable_when_team_does_not_support_resume(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-no-resume", "d", root=root)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    graph = wf_mod.load_graph(ws, inv_id)
    graph.start_node("research", role="research", model="sonnet")
    graph.complete_node("research")
    wf_mod.save_workflow(ws, graph.doc)
    wf_mod.save_node_result(
        ws, inv_id, "research", role="research", model="sonnet", result_text="done"
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, _NoopTeam())  # noqa: SLF001
    finished = queue.list_jobs()[0]
    assert finished.resume_inv_id is None


def test_requeue_failed_for_resume_sets_pending_and_preserves_completed_cache(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-full-flow", "d", root=root)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    graph = wf_mod.load_graph(ws, inv_id)
    graph.start_node("research", role="research", model="sonnet")
    graph.complete_node("research")
    wf_mod.save_workflow(ws, graph.doc)
    wf_mod.save_node_result(
        ws, inv_id, "research", role="research", model="sonnet", result_text="cached"
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "genuine node failure", resume_inv_id=inv_id)

    loaded_doc = wf_mod.load_workflow(wf_mod.workflow_path(ws, inv_id))
    assert loaded_doc.tasks[0].status == "success"

    queue.requeue_failed_for_resume(job.id)
    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    assert requeued.resume_inv_id == inv_id
    assert requeued.requeue_count == 1

    cached = wf_mod.load_node_result(ws, inv_id, "research")
    assert cached == "cached"


def test_requeue_double_activation_is_idempotent(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-double", "d", root=root)
    workspace_mod.transition(ws, workspace_mod.Status.AWAITING_INPUT)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    queue.mark_interrupted_and_fail(job.id, "failure", resume_inv_id=inv_id)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.resume_task(ws.task_id)
    requeued = queue.list_jobs()[0]
    assert requeued.status is JobStatus.PENDING
    d.resume_task(ws.task_id)
    still_pending = queue.list_jobs()[0]
    assert still_pending.status is JobStatus.PENDING
    assert still_pending.requeue_count == 1


def test_fail_genuine_mark_interrupted_queue_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod
    from alan.queue import QueueError

    ws = workspace_mod.create("rq-qerr", "d", root=root)
    ResumableTeam = _make_resumable_team()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    events, collect = _make_collector()
    d.pubsub.subscribe(ws.task_id, collect)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    graph = wf_mod.load_graph(ws, inv_id)
    graph.start_node("research", role="research", model="sonnet")
    graph.complete_node("research")
    wf_mod.save_workflow(ws, graph.doc)
    wf_mod.save_node_result(
        ws, inv_id, "research", role="research", model="sonnet", result_text="done"
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()

    def _raise(*_a: object, **_kw: object) -> None:
        raise QueueError("db locked")

    monkeypatch.setattr(queue, "mark_interrupted_and_fail", _raise)
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    finished = queue.list_jobs()[0]
    assert finished.resume_inv_id is None
    assert any(e.kind == "job_failed" for e in events)


def test_fail_genuine_resets_poisoned_node_session_id_to_none(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-session-reset", "d", root=root)
    ResumableTeam = _make_resumable_team()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[
            PendingTaskSpec(id="orchestrator", role="research", model="sonnet"),
            PendingTaskSpec(id="worker", role="research", model="sonnet"),
        ],
    )
    graph = wf_mod.load_graph(ws, inv_id)
    graph.start_node("orchestrator", role="research", model="sonnet")
    graph.complete_node("orchestrator")
    wf_mod.save_node_result(
        ws, inv_id, "orchestrator", role="research", model="sonnet", result_text="cached"
    )
    graph.start_node("worker", role="research", model="sonnet")
    graph.set_session_id("worker", "sid-poisoned")
    graph.fail_node("worker", error="node exploded")
    wf_mod.save_workflow(ws, graph.doc)
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    d._fail_genuine(job, ws, RuntimeError("genuine node failure"), inv_id, ResumableTeam())  # noqa: SLF001
    loaded = wf_mod.load_workflow(wf_mod.workflow_path(ws, inv_id))
    worker_task = next(t for t in loaded.tasks if t.id == "worker")
    assert worker_task.status == "pending"
    assert worker_task.claude_session_id is None


def test_fail_genuine_recoverable_subtask_for_multi_task_workspace(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-multi-task", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    ResumableTeam = _make_resumable_team()
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(
        "step-a", "research",
        workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.claim()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    finished = queue.list_jobs()[0]
    assert finished.resume_inv_id == inv_id
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.CRASH
    assert len(reloaded.meta.blockers) == 1
    assert reloaded.meta.blockers[0].detail == "boom"


def test_fail_recoverable_subtask_does_not_propagate_to_dependents(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-subtask-dep", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    ResumableTeam = _make_resumable_team()
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job_a = queue.enqueue(
        "step-a", "research",
        workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.enqueue("step-b", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    d._fail_genuine(job_a, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    step_b_jobs = queue.list_jobs(task_id="step-b")
    assert step_b_jobs, "step-b job must not be cancelled"
    assert step_b_jobs[0].status is JobStatus.PENDING


def test_fail_recoverable_subtask_oserror_falls_back_to_fail_job(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-oserr", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    ResumableTeam = _make_resumable_team()
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(
        "step-a", "research",
        workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.enqueue("step-b", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)

    def _raise_oserr(*_a: object, **_kw: object) -> workspace_mod.Workspace:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_mod, "transition", _raise_oserr)
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    finished = queue.list_jobs(task_id="step-a")[0]
    assert finished.status is JobStatus.FAILED
    assert finished.resume_inv_id is None
    assert not queue.list_jobs(task_id="step-b"), "propagation must cancel step-b"


def test_fail_recoverable_subtask_queue_error_publishes_job_failed(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, root: Path
) -> None:
    from alan import workflow as wf_mod
    from alan.queue import QueueError

    ws = workspace_mod.create("rq-subtask-qerr", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    ResumableTeam = _make_resumable_team()
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(
        "step-a", "research",
        workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.claim()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    events, collect = _make_collector()
    d.pubsub.subscribe(ws.task_id, collect)

    def _raise(*_a: object, **_kw: object) -> None:
        raise QueueError("db locked")

    monkeypatch.setattr(queue, "mark_interrupted_and_fail", _raise)
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    assert any(e.kind == "job_failed" for e in events)


def test_fail_recoverable_subtask_schedule_manager_summary_called(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, root: Path
) -> None:
    import alan._dispatcher_base as disp_mod
    from alan import workflow as wf_mod

    ws = workspace_mod.create("rq-subtask-mgr", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}],
    )
    ResumableTeam = _make_resumable_team()
    inv_id = wf_mod.seed_pending_workflow(
        ws,
        team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(
        "step-a", "research",
        workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.claim()
    d = Dispatcher(queue, {"research": ResumableTeam()}, workspaces_root=root)
    called: list[str] = []
    monkeypatch.setattr(
        disp_mod, "_schedule_manager_summary",
        lambda w, _h, _ps, _q, **_kw: called.append(w.task_id),
    )
    d._fail_genuine(job, ws, RuntimeError("boom"), inv_id, ResumableTeam())  # noqa: SLF001
    assert ws.task_id in called


def test_fail_job_cancels_pending_queue_rows_for_propagated_descendants(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("cancel-prop", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
            {"id": "step-c", "effort": "medium", "status": "pending", "depends_on": ["step-b"]},
        ],
    )
    job_a = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.enqueue("step-b", "research", workspace_id=ws.workspace_id)
    queue.enqueue("step-c", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job_a, "boom")  # noqa: SLF001
    assert queue.list_jobs(task_id="step-b") == []
    assert queue.list_jobs(task_id="step-c") == []


def test_fail_job_transitions_partial_workspace_when_sibling_merged(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-partial", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "step-done",
                "status": "completed",
                "depends_on": [],
                "dod_clauses": [{"text": "shipped", "evidence": "PR #9"}],
            },
            {"id": "step-fail", "status": "running", "depends_on": []},
        ],
    )
    job = queue.enqueue("step-fail", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.PARTIAL


def test_fail_job_clears_merge_block_class_on_terminal_failed(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("obs-all-fail-clear", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ACTIVE, merge_block_class="stale_review"
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._fail_job(job, "boom")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.FAILED
    assert reloaded.meta.merge_block_class is None


def test_fail_job_terminal_summary_handle_has_cleared_merge_block_class(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, root: Path
) -> None:
    import alan._dispatcher_base as disp_mod

    ws = workspace_mod.create("obs-all-fail-handle", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    ws = workspace_mod.transition(
        ws, workspace_mod.Status.ACTIVE, merge_block_class="stale_review"
    )
    job = queue.enqueue("step-a", "research", workspace_id=ws.workspace_id)
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=root)
    captured: list[Workspace] = []
    monkeypatch.setattr(
        disp_mod, "_schedule_manager_summary",
        lambda w, _h, _ps, _q, **_kw: captured.append(w),
    )
    d._fail_job(job, "boom")  # noqa: SLF001
    assert len(captured) == 1
    assert captured[0].meta.merge_block_class is None
