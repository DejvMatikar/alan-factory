from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.queue import JobStatus, Queue
from alan.teams._base import Team, TeamEvent
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _make_collector,
    _make_injected_task,
    _write_inj_task_meta,
    _write_multi_task_meta,
    _ws_with_dod,
    _ws_with_partial_tasks,
)
from tests._dispatcher_support_teams import _NOOP_TEAMS, _make_handover, _NoopTeam


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


def test_multi_task_partial_done_surfaces_stall_when_team_missing(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("mt-partial", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "plan", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "impl", "effort": "medium", "status": "pending", "depends_on": ["plan"]},
        ],
    )
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "plan")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    impl_task = next(t for t in reloaded.meta.tasks if t.id == "impl")
    assert impl_task.status is not TaskStatus.COMPLETED
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.STALL
    assert len(reloaded.meta.blockers) == 1
    assert "DAG stalled" in reloaded.meta.blockers[0].detail


def test_multi_task_partial_done_stays_active_when_team_available(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("mt-partial-team", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "plan", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "impl", "team": "impl", "effort": "medium",
             "status": "pending", "depends_on": ["plan"]},
        ],
    )
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {"impl": _NoopTeam()}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "plan")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE
    impl_task = next(t for t in reloaded.meta.tasks if t.id == "impl")
    assert impl_task.status is not TaskStatus.COMPLETED


def test_multi_task_all_done_reaches_completed(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("mt-all", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "plan", "effort": "medium", "status": "completed", "depends_on": []},
            {"id": "impl", "effort": "medium", "status": "running", "depends_on": ["plan"]},
        ],
    )
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "impl")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED
    assert all(t.status == TaskStatus.COMPLETED for t in reloaded.meta.tasks)


def test_update_task_status_marks_task_and_persists(root: Path) -> None:
    from alan.workspace import TaskStatus, update_task_status

    ws = workspace_mod.create("uts", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": ws.task_id, "effort": "medium", "status": "ready", "depends_on": []}],
    )
    assert ws.meta.tasks[0].status == TaskStatus.READY
    ws2 = update_task_status(ws, ws.task_id, TaskStatus.COMPLETED)
    assert ws2.meta.tasks[0].status == TaskStatus.COMPLETED
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].status == TaskStatus.COMPLETED


def test_update_task_status_raises_for_unknown_task_id(root: Path) -> None:
    from alan.workspace import TaskStatus, WorkspaceError, update_task_status

    ws = workspace_mod.create("uts-err", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "t1", "effort": "medium", "status": "ready", "depends_on": []}],
    )
    with pytest.raises(WorkspaceError, match="not found"):
        update_task_status(ws, "no-such-task", TaskStatus.COMPLETED)


def test_advance_workspace_dag_enqueues_unblocked_dependent_task(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("dag-dep", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "plan", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "impl", "effort": "medium", "status": "pending", "depends_on": ["plan"]},
        ],
    )

    class ImplTeam:
        name = "impl-team"
        supports_resume = False

        def plan_workflow(self, _w: Workspace, _p: Handover | None) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(
        queue, {"impl-team": ImplTeam()}, workspaces_root=root
    )
    ws_with_tasks = workspace_mod.read(ws.task_id, root=root)
    plan_task = next(t for t in ws_with_tasks.meta.tasks if t.id == "plan")
    impl_task = next(t for t in ws_with_tasks.meta.tasks if t.id == "impl")
    impl_task_obj = impl_task.model_copy(update={"team": "impl-team"})
    new_meta = ws_with_tasks.meta.model_copy(
        update={"tasks": [plan_task, impl_task_obj]}
    )
    meta_path = ws_with_tasks.path / "meta.json"
    meta_path.write_text(json.dumps(new_meta.model_dump(mode="json")), encoding="utf-8")
    ws_final = workspace_mod.read(ws.task_id, root=root)
    d._advance_workspace_dag(ws_final, "plan")  # noqa: SLF001
    jobs = queue.list_jobs()
    assert any(j.task_id == "impl" for j in jobs), "impl task should be enqueued"


def test_advance_workspace_dag_skips_task_with_no_team_and_warns(
    queue: Queue,
    root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("dag-noteam", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "step-a", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "step-b", "effort": "medium", "status": "pending", "depends_on": ["step-a"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws2 = workspace_mod.read(ws.task_id, root=root)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._advance_workspace_dag(ws2, "step-a")  # noqa: SLF001
    assert any("no team for task" in r.message for r in caplog.records)
    assert not queue.list_jobs()


def test_advance_workspace_dag_does_not_enqueue_already_queued_task(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("dag-skip", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "p", "effort": "medium", "status": "completed", "depends_on": []},
            {"id": "q", "effort": "medium", "status": "pending", "depends_on": []},
        ],
    )

    class QTeam:
        name = "q-team"
        supports_resume = False

        def plan_workflow(self, _w: Workspace, _p: Handover | None) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id)

    d = Dispatcher(queue, {"q-team": QTeam()}, workspaces_root=root)
    queue.enqueue("q", "q-team", workspace_id=ws.workspace_id)
    ws2 = workspace_mod.read(ws.task_id, root=root)
    meta2 = ws2.meta.model_copy(
        update={"tasks": [
            next(t for t in ws2.meta.tasks if t.id == "p"),
            next(t for t in ws2.meta.tasks if t.id == "q").model_copy(update={"team": "q-team"}),
        ]}
    )
    (ws2.path / "meta.json").write_text(
        json.dumps(meta2.model_dump(mode="json")), encoding="utf-8"
    )
    ws3 = workspace_mod.read(ws.task_id, root=root)
    d._advance_workspace_dag(ws3, "p")  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.task_id == "q"]
    assert len(jobs) == 1, "q must not be enqueued twice"


def test_dispatcher_drives_two_subtask_workspace_to_completion(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("two-sub", "d", root=root)
    meta_path = ws.path / "meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = [
        {
            "id": "sub-1", "status": "pending", "depends_on": [],
            "team": "impl-team", "injection_depth": 1,
        },
        {
            "id": "sub-2", "status": "pending", "depends_on": ["sub-1"],
            "team": "impl-team", "injection_depth": 1,
        },
    ]
    meta_path.write_text(json.dumps(data), encoding="utf-8")
    ws = workspace_mod.read(ws.task_id, root=root)

    class _TerminalImplTeam:
        name = "impl-team"
        supports_resume = False

        def plan_workflow(
            self, _w: Workspace, _p: Handover | None
        ) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id=f"impl-done-{workspace.task_id}",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="impl-team",
                from_invocation_id=inv_id,
                to_team_suggestions=[],
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="done",
            )

    d = Dispatcher(queue, {"impl-team": _TerminalImplTeam()}, workspaces_root=root)
    queue.enqueue("sub-1", "impl-team", workspace_id=ws.workspace_id)

    assert d.run_once() is True
    ws1 = workspace_mod.read(ws.task_id, root=root)
    sub1 = next(t for t in ws1.meta.tasks if t.id == "sub-1")
    assert sub1.status == workspace_mod.TaskStatus.COMPLETED
    assert ws1.meta.status != workspace_mod.Status.COMPLETED, (
        "workspace must not be COMPLETED while sub-2 is still pending"
    )

    sub2_jobs = queue.list_jobs(
        task_id="sub-2", workspace_id=ws.workspace_id, status=JobStatus.PENDING
    )
    assert len(sub2_jobs) == 1, "sub-2 must be auto-enqueued after sub-1 completes"

    assert d.run_once() is True
    ws2 = workspace_mod.read(ws.task_id, root=root)
    sub2 = next(t for t in ws2.meta.tasks if t.id == "sub-2")
    assert sub2.status == workspace_mod.TaskStatus.COMPLETED
    assert ws2.meta.status == workspace_mod.Status.COMPLETED, (
        "workspace must be COMPLETED only after all subtasks finish"
    )


def test_task_blocked_returns_true_when_dep_not_completed(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import Task, TaskStatus

    d = Dispatcher(queue, {}, workspaces_root=root)
    task = Task(id="c", depends_on=["b"], status=TaskStatus.PENDING)
    assert d._task_blocked(task, frozenset({"a"}), frozenset(), "some-workspace") is True


def test_task_blocked_not_blocked_by_same_task_id_in_different_workspace(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import Task, TaskStatus

    d = Dispatcher(queue, {}, workspaces_root=root)
    queue.enqueue("plan", "research", workspace_id="ws-a")
    queue.claim()
    task = Task(id="plan", depends_on=[], status=TaskStatus.PENDING)
    assert d._task_blocked(task, frozenset(), frozenset(), "ws-b") is False


def test_ready_tasks_parent_pending_blocks_child_not_parent(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("rt-pend", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "parent", "effort": "medium", "status": "pending", "depends_on": []},
            {"id": "child", "effort": "medium", "status": "pending", "depends_on": ["parent"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    ready_ids = [t.id for t in d._ready_tasks(ws)]  # noqa: SLF001
    assert "parent" in ready_ids
    assert "child" not in ready_ids


def test_ready_tasks_parent_running_blocks_child(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("rt-run", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "parent", "effort": "medium", "status": "running", "depends_on": []},
            {"id": "child", "effort": "medium", "status": "pending", "depends_on": ["parent"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    ready_ids = [t.id for t in d._ready_tasks(ws)]  # noqa: SLF001
    assert "parent" not in ready_ids
    assert "child" not in ready_ids


def test_ready_tasks_parent_failed_blocks_child(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("rt-fail", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "parent", "effort": "medium", "status": "failed", "depends_on": []},
            {"id": "child", "effort": "medium", "status": "pending", "depends_on": ["parent"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    ready_ids = [t.id for t in d._ready_tasks(ws)]  # noqa: SLF001
    assert "parent" not in ready_ids
    assert "child" not in ready_ids


def test_ready_tasks_parent_completed_unblocks_child(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("rt-done", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "parent", "effort": "medium", "status": "completed", "depends_on": []},
            {"id": "child", "effort": "medium", "status": "pending", "depends_on": ["parent"]},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    ready_ids = [t.id for t in d._ready_tasks(ws)]  # noqa: SLF001
    assert "child" in ready_ids


def test_task_blocked_returns_true_when_dep_failed(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import Task, TaskStatus

    d = Dispatcher(queue, {}, workspaces_root=root)
    task = Task(id="c", depends_on=["b"], status=TaskStatus.PENDING)
    assert d._task_blocked(task, frozenset(), frozenset({"b"}), "ws") is True  # noqa: SLF001


def test_advance_dag_mix_completed_failed_with_one_completed_transitions_failed(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("dag-mix", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "effort": "medium", "status": "failed", "depends_on": []},
            {"id": "b", "effort": "medium", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "b")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.FAILED


def test_advance_dag_stays_active_when_non_failed_tasks_remain_pending(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("dag-active", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "effort": "medium", "status": "failed", "depends_on": []},
            {"id": "b", "team": "research", "effort": "medium",
             "status": "pending", "depends_on": []},
            {"id": "c", "effort": "medium", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, "c")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE


def test_workspace_terminal_status_all_failed_returns_failed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-fail", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "effort": "medium", "status": "failed", "depends_on": []},
            {"id": "b", "effort": "medium", "status": "failed", "depends_on": ["a"]},
        ],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED


def test_workspace_terminal_status_empty_tasks_returns_completed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-empty", "d", root=root)
    assert ws.meta.tasks == []
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.COMPLETED


def test_workspace_terminal_status_all_completed_no_requirements_is_completed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-all-comp", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "effort": "medium", "status": "completed", "depends_on": []},
            {"id": "b", "effort": "medium", "status": "completed", "depends_on": ["a"]},
        ],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.COMPLETED


def test_workspace_terminal_status_all_completed_requirements_all_pass_is_completed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.workspace import Requirement, update_requirements

    ws = workspace_mod.create(
        "ts-req-pass", "d", root=root,
        requirements=[Requirement(text="r", test_ref="t")]
    )
    ws = update_requirements(ws, [Requirement(text="r", test_ref="t", status="PASS")])
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "completed", "depends_on": []}],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.COMPLETED


def test_workspace_terminal_status_all_completed_requirements_pending_is_failed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod
    from alan.workspace import Requirement

    ws = workspace_mod.create(
        "ts-req-pend", "d", root=root,
        requirements=[Requirement(text="r", test_ref="t")]
    )
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "completed", "depends_on": []}],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED


def test_workspace_terminal_status_all_completed_empty_requirements_is_failed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-req-empty", "d", root=root, requirements=[])
    ws = _write_multi_task_meta(
        ws,
        [{"id": "a", "effort": "medium", "status": "completed", "depends_on": []}],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED


def test_workspace_terminal_status_empty_tasks_empty_requirements_is_failed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-empty-reqs", "d", root=root, requirements=[])
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED


def test_workspace_terminal_status_pending_injected_task_blocks_completion(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-inj-block", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "orig", "status": "completed", "depends_on": []},
            {"id": "inj-a", "status": "pending", "depends_on": ["orig"], "injection_depth": 1},
        ],
    )
    assert dispatcher_mod._workspace_terminal_status(ws) is None  # noqa: SLF001

    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "orig", "status": "completed", "depends_on": []},
            {"id": "inj-a", "status": "completed", "depends_on": ["orig"], "injection_depth": 1},
        ],
    )
    assert dispatcher_mod._workspace_terminal_status(ws) is workspace_mod.Status.COMPLETED  # noqa: SLF001


def test_stamp_evidence_no_handover_is_noop(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    assert dispatcher_mod._stamp_evidence(ws, "sub", None) is ws  # noqa: SLF001


def test_stamp_evidence_no_pr_number_is_noop(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    h = _make_handover(ws.task_id)
    assert dispatcher_mod._stamp_evidence(ws, "sub", h) is ws  # noqa: SLF001


def test_stamp_evidence_unknown_task_is_noop(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    h = _make_handover(ws.task_id).model_copy(update={"pr_number": 7})
    assert dispatcher_mod._stamp_evidence(ws, "nope", h) is ws  # noqa: SLF001


def test_stamp_evidence_task_without_dod_is_noop(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("nodod", "d", root=root)
    task = workspace_mod.Task(id="sub", status=workspace_mod.TaskStatus.COMPLETED)
    workspace_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [task]}))  # noqa: SLF001
    ws = workspace_mod.read(ws.task_id, root=root)
    h = _make_handover(ws.task_id).model_copy(update={"pr_number": 7})
    assert dispatcher_mod._stamp_evidence(ws, "sub", h) is ws  # noqa: SLF001


def test_stamp_evidence_default_path_unchanged(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    h = _make_handover(ws.task_id).model_copy(update={"pr_number": 7})
    out = dispatcher_mod._stamp_evidence(ws, "sub", h)  # noqa: SLF001
    assert out.meta.tasks[0].dod_clauses[0].evidence == "PR #7"


def test_workspace_terminal_status_covered_clause_unevidenced_fails(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    clause = workspace_mod.Requirement(text="c")
    ws = _ws_with_dod(root, covers=[clause.id], requirements=[clause], task_id="t1")
    assert dispatcher_mod._workspace_terminal_status(ws) is workspace_mod.Status.FAILED  # noqa: SLF001


def test_workspace_terminal_status_covered_clause_evidenced_completes(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    clause = workspace_mod.Requirement(text="c")
    ws = _ws_with_dod(
        root, covers=[clause.id], evidence="PR #1", requirements=[clause], task_id="t1"
    )
    assert dispatcher_mod._workspace_terminal_status(ws) is workspace_mod.Status.COMPLETED  # noqa: SLF001


def test_stamp_evidence_explicit_evidence_overrides_handover(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    h = _make_handover(ws.task_id).model_copy(update={"pr_number": 7})
    out = dispatcher_mod._stamp_evidence(  # noqa: SLF001
        ws, "sub", h, evidence="PR #99 (out-of-band)"
    )
    assert out.meta.tasks[0].dod_clauses[0].evidence == "PR #99 (out-of-band)"


def test_stamp_evidence_explicit_evidence_without_handover(root: Path) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = _ws_with_dod(root, covers=["req-x"])
    out = dispatcher_mod._stamp_evidence(ws, "sub", None, evidence="PR #5")  # noqa: SLF001
    assert out.meta.tasks[0].dod_clauses[0].evidence == "PR #5"


def test_advance_workspace_dag_stamps_explicit_evidence_without_handover(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("adv-oob-evidence", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "sub", "status": "pending", "depends_on": [],
                "dod_clauses": [{"text": "x", "covers": ["req-x"]}],
            },
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "sub", evidence="PR #123 (out-of-band)")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    task = next(t for t in reloaded.meta.tasks if t.id == "sub")
    assert task.status == workspace_mod.TaskStatus.COMPLETED
    assert task.dod_clauses[0].evidence == "PR #123 (out-of-band)"


def test_advance_dag_empty_injected_tasks_is_noop(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("inj-empty", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {"id": "plan", "status": "running", "depends_on": [], "injection_depth": 0},
    ])
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": []}
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "plan", handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert all(t.id == "plan" for t in reloaded.meta.tasks)


def test_advance_dag_injects_tasks_from_handover(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("inj-basic", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {"id": "plan", "status": "running", "depends_on": [], "team": "research"},
    ])
    inj = _make_injected_task("sub-a", team="research", instructions="do X")
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": [inj]}
    )
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._advance_workspace_dag(ws, "plan", handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    injected = [t for t in reloaded.meta.tasks if t.id.startswith("inj-")]
    assert len(injected) == 1
    assert injected[0].team == "research"
    assert injected[0].instructions == "do X"
    assert injected[0].injection_depth == 1


def test_advance_dag_injection_replay_is_idempotent(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("inj-replay", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {"id": "plan", "status": "running", "depends_on": [], "team": "research"},
    ])
    inj = _make_injected_task("sub-r")
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": [inj]}
    )
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._advance_workspace_dag(ws, "plan", handover=handover)  # noqa: SLF001
    ws_after = workspace_mod.read(ws.task_id, root=root)
    ids_first = {t.id for t in ws_after.meta.tasks if t.id.startswith("inj-")}
    d._advance_workspace_dag(ws_after, "plan", handover=handover)  # noqa: SLF001
    ws_after2 = workspace_mod.read(ws.task_id, root=root)
    ids_second = {t.id for t in ws_after2.meta.tasks if t.id.startswith("inj-")}
    assert ids_first == ids_second
    assert ws_after2.meta.injection_overflow == []


def test_advance_dag_injection_overflow_publishes_event_and_persists(
    queue: Queue,
    root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("inj-overflow", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {
            "id": "deep-parent", "status": "completed",
            "depends_on": [], "team": "r", "injection_depth": 2,
        },
    ])
    inj = _make_injected_task("over-task", team="research")
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": [inj]}
    )
    events, collect = _make_collector()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, collect)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._advance_workspace_dag(ws, "deep-parent", handover=handover)  # noqa: SLF001
    overflow_events = [e for e in events if e.kind == "injection_overflow"]
    assert len(overflow_events) == 1
    assert overflow_events[0].payload["dropped_count"] == "1"
    assert overflow_events[0].payload["task_id"] == "deep-parent"
    assert any("overflow" in r.message for r in caplog.records)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert len(reloaded.meta.injection_overflow) == 1
    assert reloaded.meta.injection_overflow[0] == "over-task"


def test_advance_dag_injected_task_enqueued_when_no_deps(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("inj-enqueue", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {"id": "root-t", "status": "running", "depends_on": [], "team": "research"},
    ])
    inj = _make_injected_task("sub-enq", team="research")
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": [inj]}
    )
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._advance_workspace_dag(ws, "root-t", handover=handover)  # noqa: SLF001
    inj_jobs = [j for j in queue.list_jobs() if j.task_id.startswith("inj-")]
    assert len(inj_jobs) == 1


def test_advance_dag_injected_task_with_dep_not_enqueued_immediately(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("inj-dep-wait", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {"id": "root-dep", "status": "running", "depends_on": [], "team": "research"},
    ])
    inj_a = _make_injected_task("a", team="research")
    inj_b = _make_injected_task("b", team="research", depends_on=["a"])
    handover = _make_handover(ws.task_id, from_team="research").model_copy(
        update={"injected_tasks": [inj_a, inj_b]}
    )
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._advance_workspace_dag(ws, "root-dep", handover=handover)  # noqa: SLF001
    inj_jobs = [j for j in queue.list_jobs() if j.task_id.startswith("inj-")]
    assert len(inj_jobs) == 1


def test_advance_dag_handover_without_injected_tasks_field_is_noop(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("inj-no-field", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "task-nf", "effort": "medium", "status": "running", "depends_on": []}],
    )
    handover = _make_handover(ws.task_id, from_team="research")
    assert handover.injected_tasks == []
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, "task-nf", handover=handover)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.injection_overflow == []


def test_enqueue_ready_passes_instructions_to_queue(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("enq-instr", "d", root=root)
    ws = _write_inj_task_meta(ws, [
        {
            "id": "inj-task-with-instr", "status": "pending",
            "depends_on": [], "team": "research",
            "instructions": "run tests", "injection_depth": 1,
        },
    ])
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    d._enqueue_ready(ws)  # noqa: SLF001
    jobs = queue.list_jobs(task_id="inj-task-with-instr")
    assert len(jobs) == 1
    assert jobs[0].instructions == "run tests"


def test_handle_decision_enqueue_preserves_subtask_task_id(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("subtask-route", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "inj-first", "status": "running", "depends_on": [], "team": "research"},
            {"id": "inj-second", "status": "pending", "depends_on": ["inj-first"]},
        ],
    )
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    d._handle_decision(ws, handover, decision, "inj-first")  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "implementation_plan"]
    assert len(jobs) == 1
    assert jobs[0].task_id == "inj-first"
    assert jobs[0].workspace_id == ws.workspace_id


def test_subtask_task_id_preserved_across_two_team_pipeline(
    queue: Queue, root: Path
) -> None:
    from alan.workspace import TaskStatus

    ws = workspace_mod.create("two-team-sub", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "inj-a", "status": "pending", "depends_on": [], "team": "research"},
            {"id": "inj-b", "status": "pending", "depends_on": ["inj-a"], "team": "research"},
        ],
    )

    class _ForwardingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self, _w: Workspace, _p: Handover | None
        ) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id=f"fwd-{workspace.task_id}",
                handover_type="forward",
                task_id=workspace.task_id,
                from_team="research",
                from_invocation_id=inv_id,
                to_team_suggestions=["implementation_plan"],
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="forwarding",
            )

    class _TerminalTeam:
        name = "implementation_plan"
        supports_resume = False

        def plan_workflow(
            self, _w: Workspace, _p: Handover | None
        ) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id=f"term-{workspace.task_id}",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="implementation_plan",
                from_invocation_id=inv_id,
                to_team_suggestions=[],
                iteration=2,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="done",
            )

    teams: dict[str, Team] = {
        "research": _ForwardingTeam(),
        "implementation_plan": _TerminalTeam(),
    }
    d = Dispatcher(queue, teams, workspaces_root=root)

    queue.enqueue("inj-a", "research", workspace_id=ws.workspace_id)

    d.run_once()

    impl_plan_jobs = [
        j for j in queue.list_jobs()
        if j.team == "implementation_plan" and j.status == JobStatus.PENDING
    ]
    assert len(impl_plan_jobs) == 1, "implementation_plan job must be enqueued"
    assert impl_plan_jobs[0].task_id == "inj-a", "impl_plan job must carry the subtask task_id"

    d.run_once()

    ws_after = workspace_mod.read(ws.task_id, root=root)
    task_a = next(t for t in ws_after.meta.tasks if t.id == "inj-a")
    assert task_a.status == TaskStatus.COMPLETED, "inj-a must be COMPLETED after full pipeline"

    inj_b_jobs = [
        j for j in queue.list_jobs()
        if j.task_id == "inj-b" and j.status == JobStatus.PENDING
    ]
    assert len(inj_b_jobs) == 1, "inj-b must be enqueued after inj-a completes"


def test_workspace_terminal_status_partial_when_merged_sibling_and_failed(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-partial", "d", root=root)
    ws = _ws_with_partial_tasks(ws, ["PR #334"], extra_failed=True)
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.PARTIAL


def test_workspace_terminal_status_failed_when_no_merged_evidence(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-no-ev", "d", root=root)
    ws = _ws_with_partial_tasks(ws, [None], extra_failed=True)
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED


def test_workspace_terminal_status_failed_when_completed_no_dod(
    root: Path,
) -> None:
    import alan._dispatcher_reconcile as dispatcher_mod

    ws = workspace_mod.create("ts-no-dod", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "a", "status": "completed", "depends_on": []},
            {"id": "b", "status": "failed", "depends_on": []},
        ],
    )
    status = dispatcher_mod._workspace_terminal_status(ws)  # noqa: SLF001
    assert status is workspace_mod.Status.FAILED
