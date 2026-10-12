from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import park as park_mod
from alan import workspace as workspace_mod
from alan import workspace_dag
from alan.claude_cli import OnProcStarted
from alan.events import PubSub
from alan.handover import Handover, InjectedTask
from alan.queue import Job, JobStatus, Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Requirement, Status, Task, TaskDoDClause, TaskStatus, Workspace
from alan.workspace_index import WorkspaceIndex


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def _task(
    task_id: str,
    *,
    team: str | None = "research",
    status: TaskStatus = TaskStatus.PENDING,
    depends_on: list[str] | None = None,
    dod_clauses: list[TaskDoDClause] | None = None,
) -> Task:
    return Task(
        id=task_id,
        team=team,
        status=status,
        depends_on=depends_on or [],
        dod_clauses=dod_clauses or [],
    )


def _ws(
    root: Path,
    tasks: list[Task] | None,
    *,
    name: str = "dag-ws",
    requirements: list[Requirement] | None = None,
) -> Workspace:
    return workspace_mod.create(name, "d", root=root, tasks=tasks, requirements=requirements)


def _ts() -> datetime:
    return datetime.now(UTC)


def _terminal_handover(
    task_id: str, *, from_team: str = "retrospective", pr_number: int | None = None,
    injected_tasks: list[InjectedTask] | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_{from_team}",
        handover_type="terminal",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=_ts(),
        full_synthesis="done",
        pr_number=pr_number,
        injected_tasks=injected_tasks or [],
    )


class FakeTeam:
    supports_resume = False

    def __init__(self, name: str) -> None:
        self.name = name

    def plan_workflow(
        self, _w: Workspace, _p: Handover | None
    ) -> list[PendingTaskSpec]:
        return []  # pragma: no cover

    def run(
        self,
        workspace: Workspace,
        _p: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _terminal_handover(workspace.task_id, from_team=self.name)  # pragma: no cover


def _fake_job() -> Job:
    return Job(id=1, task_id="x", team="research", status=JobStatus.PENDING, enqueued_at=_ts())


def test_all_tasks_completed_true(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.COMPLETED)])
    assert workspace_dag.all_tasks_completed(ws) is True


def test_all_tasks_completed_false_when_empty(root: Path) -> None:
    ws = _ws(root, None)
    assert workspace_dag.all_tasks_completed(ws) is False


def test_all_tasks_completed_false_when_one_pending(root: Path) -> None:
    ws = _ws(
        root, [_task("a", status=TaskStatus.COMPLETED), _task("b", status=TaskStatus.PENDING)]
    )
    assert workspace_dag.all_tasks_completed(ws) is False


def test_has_awaiting_subtask_true(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.AWAITING_INPUT)])
    assert workspace_dag.has_awaiting_subtask(ws) is True


def test_has_awaiting_subtask_false(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)])
    assert workspace_dag.has_awaiting_subtask(ws) is False


def test_workspace_terminal_status_none_when_leaves_pending(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)])
    assert workspace_dag.workspace_terminal_status(ws) is None


def test_workspace_terminal_status_empty_tasks_completed(root: Path) -> None:
    ws = _ws(root, None)
    assert workspace_dag.workspace_terminal_status(ws) is Status.COMPLETED


def test_workspace_terminal_status_empty_tasks_failed(root: Path) -> None:
    ws = _ws(root, None, requirements=[])
    assert workspace_dag.workspace_terminal_status(ws) is Status.FAILED


def test_workspace_terminal_status_all_completed_is_completed(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.COMPLETED)])
    assert workspace_dag.workspace_terminal_status(ws) is Status.COMPLETED


def test_workspace_terminal_status_all_completed_requirements_empty_is_failed(
    root: Path,
) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.COMPLETED)], requirements=[])
    assert workspace_dag.workspace_terminal_status(ws) is Status.FAILED


def test_workspace_terminal_status_failed_when_no_merged_sibling(root: Path) -> None:
    ws = _ws(
        root,
        [_task("a", status=TaskStatus.FAILED), _task("b", status=TaskStatus.COMPLETED)],
    )
    assert workspace_dag.workspace_terminal_status(ws) is Status.FAILED


def test_workspace_terminal_status_partial_when_merged_sibling(root: Path) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.FAILED),
            _task(
                "b",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="ship it", evidence="PR #7")],
            ),
        ],
    )
    assert workspace_dag.workspace_terminal_status(ws) is Status.PARTIAL


def test_stamp_evidence_no_handover_no_evidence_is_noop(root: Path) -> None:
    ws = _ws(root, [_task("a")])
    assert workspace_dag.stamp_evidence(ws, "a", None) is ws


def test_stamp_evidence_handover_without_pr_number_is_noop(root: Path) -> None:
    ws = _ws(root, [_task("a")])
    h = _terminal_handover("a", pr_number=None)
    assert workspace_dag.stamp_evidence(ws, "a", h) is ws


def test_stamp_evidence_unknown_task_is_noop(root: Path) -> None:
    ws = _ws(root, [_task("a")])
    h = _terminal_handover("a", pr_number=5)
    assert workspace_dag.stamp_evidence(ws, "missing", h) is ws


def test_stamp_evidence_task_without_dod_clauses_is_noop(root: Path) -> None:
    ws = _ws(root, [_task("a")])
    h = _terminal_handover("a", pr_number=5)
    assert workspace_dag.stamp_evidence(ws, "a", h) is ws


def test_stamp_evidence_stamps_from_handover_pr_number(root: Path) -> None:
    ws = _ws(root, [_task("a", dod_clauses=[TaskDoDClause(text="ship it")])])
    h = _terminal_handover("a", pr_number=42)
    out = workspace_dag.stamp_evidence(ws, "a", h)
    task = next(t for t in out.meta.tasks if t.id == "a")
    assert task.dod_clauses[0].evidence == "PR #42"


def test_stamp_evidence_explicit_evidence_overrides_handover(root: Path) -> None:
    ws = _ws(root, [_task("a", dod_clauses=[TaskDoDClause(text="ship it")])])
    h = _terminal_handover("a", pr_number=42)
    out = workspace_dag.stamp_evidence(ws, "a", h, evidence="manual override")
    task = next(t for t in out.meta.tasks if t.id == "a")
    assert task.dod_clauses[0].evidence == "manual override"


def test_stamp_evidence_explicit_evidence_without_handover(root: Path) -> None:
    ws = _ws(root, [_task("a", dod_clauses=[TaskDoDClause(text="ship it")])])
    out = workspace_dag.stamp_evidence(ws, "a", None, evidence="PR #5")
    task = next(t for t in out.meta.tasks if t.id == "a")
    assert task.dod_clauses[0].evidence == "PR #5"


def test_propagate_task_failure_marks_direct_task(root: Path) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.RUNNING)])
    out = workspace_dag.propagate_task_failure(ws, "a")
    assert next(t.status for t in out.meta.tasks if t.id == "a") is TaskStatus.FAILED


def test_propagate_task_failure_cascades_transitively(root: Path) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.RUNNING),
            _task("b", status=TaskStatus.PENDING, depends_on=["a"]),
            _task("c", status=TaskStatus.PENDING, depends_on=["b"]),
            _task("d", status=TaskStatus.COMPLETED, depends_on=[]),
        ],
    )
    out = workspace_dag.propagate_task_failure(ws, "a")
    statuses = {t.id: t.status for t in out.meta.tasks}
    assert statuses["a"] is TaskStatus.FAILED
    assert statuses["b"] is TaskStatus.FAILED
    assert statuses["c"] is TaskStatus.FAILED
    assert statuses["d"] is TaskStatus.COMPLETED


def test_task_blocked_dep_not_completed(root: Path, queue: Queue) -> None:
    task = _task("b", depends_on=["a"])
    assert (
        workspace_dag.task_blocked(task, frozenset(), frozenset(), "ws", queue=queue) is True
    )


def test_task_blocked_dep_failed(root: Path, queue: Queue) -> None:
    task = _task("b", depends_on=["a"])
    assert (
        workspace_dag.task_blocked(task, frozenset({"a"}), frozenset({"a"}), "ws", queue=queue)
        is True
    )


def test_task_blocked_by_pending_job(root: Path, queue: Queue) -> None:
    queue.enqueue("b", "research", workspace_id="ws")
    task = _task("b")
    assert (
        workspace_dag.task_blocked(task, frozenset(), frozenset(), "ws", queue=queue) is True
    )


def test_task_blocked_by_running_job(root: Path, queue: Queue) -> None:
    queue.enqueue("b", "research", workspace_id="ws")
    queue.claim()
    task = _task("b")
    assert (
        workspace_dag.task_blocked(task, frozenset(), frozenset(), "ws", queue=queue) is True
    )


def test_task_blocked_false_when_clear(root: Path, queue: Queue) -> None:
    task = _task("b", depends_on=["a"])
    assert (
        workspace_dag.task_blocked(task, frozenset({"a"}), frozenset(), "ws", queue=queue)
        is False
    )


def test_ready_tasks_parent_pending_blocks_child(root: Path, queue: Queue) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.PENDING),
            _task("b", status=TaskStatus.PENDING, depends_on=["a"]),
        ],
    )
    ready_ids = [t.id for t in workspace_dag.ready_tasks(ws, queue=queue)]
    assert ready_ids == ["a"]


def test_ready_tasks_parent_completed_unblocks_child(root: Path, queue: Queue) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.COMPLETED),
            _task("b", status=TaskStatus.PENDING, depends_on=["a"]),
        ],
    )
    ready_ids = [t.id for t in workspace_dag.ready_tasks(ws, queue=queue)]
    assert ready_ids == ["b"]


def test_enqueue_ready_passes_instructions_to_enqueue_workflow(
    root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create(
        "eq-ws",
        "d",
        root=root,
        tasks=[Task(id="a", team="research", status=TaskStatus.PENDING, instructions="do X")],
    )
    calls: list[dict[str, object]] = []

    def fake_enqueue(**kwargs: object) -> tuple[Job, str]:
        calls.append(kwargs)
        return _fake_job(), "inv_1"

    pubsub = PubSub()
    workspace_dag.enqueue_ready(
        ws,
        queue=queue,
        pubsub=pubsub,
        teams={"research": FakeTeam("research")},
        enqueue_workflow=fake_enqueue,
    )
    assert len(calls) == 1
    assert calls[0]["instructions"] == "do X"
    assert calls[0]["task_id"] == "a"


def test_enqueue_ready_skips_unknown_team_and_warns(
    root: Path, queue: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    ws = workspace_mod.create(
        "eq-ws2",
        "d",
        root=root,
        tasks=[Task(id="a", team="ghost-team", status=TaskStatus.PENDING)],
    )
    calls: list[dict[str, object]] = []

    def fake_enqueue(**kwargs: object) -> tuple[Job, str]:
        calls.append(kwargs)
        return _fake_job(), "inv_1"

    pubsub = PubSub()
    with caplog.at_level("WARNING", logger="alan.workspace_dag"):
        workspace_dag.enqueue_ready(
            ws, queue=queue, pubsub=pubsub, teams={}, enqueue_workflow=fake_enqueue,
        )
    assert calls == []
    assert any("no team for task" in r.message for r in caplog.records)


def test_dag_has_active_work_false_when_all_terminal(root: Path, queue: Queue) -> None:
    ws = _ws(
        root,
        [_task("a", status=TaskStatus.COMPLETED), _task("b", status=TaskStatus.FAILED)],
    )
    assert workspace_dag.dag_has_active_work(ws, queue=queue) is False


def test_dag_has_active_work_true_when_job_pending(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="active-ws")
    queue.enqueue("a", "research", workspace_id=ws.workspace_id)
    assert workspace_dag.dag_has_active_work(ws, queue=queue) is True


def test_dag_has_active_work_false_when_no_matching_job(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="idle-ws")
    assert workspace_dag.dag_has_active_work(ws, queue=queue) is False


def _collecting_summary() -> (
    tuple[list[tuple[object, ...]], workspace_dag.ScheduleManagerSummary]
):
    calls: list[tuple[object, ...]] = []

    def _fn(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    return calls, _fn


def _workspace_capturing_summary() -> (
    tuple[list[Workspace], workspace_dag.ScheduleManagerSummary]
):
    captured: list[Workspace] = []

    def _fn(ws: Workspace, *_args: object, **_kwargs: object) -> None:
        captured.append(ws)

    return captured, _fn


def test_maybe_surface_stall_noop_no_tasks(root: Path, queue: Queue) -> None:
    ws = _ws(root, None)
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    workspace_dag.maybe_surface_stall(
        ws, "ignored", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
    )
    assert calls == []


def test_maybe_surface_stall_noop_when_awaiting_subtask(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.AWAITING_INPUT)])
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    workspace_dag.maybe_surface_stall(
        ws, "ignored", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
    )
    assert calls == []


def test_maybe_surface_stall_noop_when_active_work(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="stall-active")
    queue.enqueue("a", "research", workspace_id=ws.workspace_id)
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    workspace_dag.maybe_surface_stall(
        ws, "ignored", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
    )
    assert calls == []


def test_maybe_surface_stall_fires_and_publishes(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="stall-fire")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    events: list[str] = []
    pubsub.subscribe(ws.workspace_id, lambda e: events.append(e.kind))
    workspace_dag.maybe_surface_stall(
        ws, "root", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
    )
    assert len(calls) == 1
    assert events == ["dag_stall"]
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_maybe_surface_stall_oserror_logged(
    root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="stall-oserr")
    calls, summary = _collecting_summary()
    pubsub = PubSub()

    def boom(*_args: object, **_kwargs: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_mod, "transition", boom)
    with caplog.at_level(logging.ERROR, logger="alan.workspace_dag"):
        workspace_dag.maybe_surface_stall(
            ws, "root", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
        )
    assert calls == []
    assert any("transition failed" in r.message for r in caplog.records)


def _noop_apply_injection(ws: Workspace, _task_id: str, _handover: Handover) -> Workspace:
    return ws


def _noop_evaluate_requirements(
    ws: Workspace, _handover: Handover | None, *, precomputed_scenario_results: object = None,
) -> Workspace:
    return ws


def test_advance_workspace_dag_completes_legacy_single_task(root: Path, queue: Queue) -> None:
    ws = _ws(root, None, name="legacy-ws")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    handover = _terminal_handover(ws.task_id)
    workspace_dag.advance_workspace_dag(
        ws,
        ws.task_id,
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.COMPLETED
    assert len(calls) == 1


def test_advance_workspace_dag_applies_injection(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="inj-ws")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    injected = InjectedTask(id="sub-1", team="research", instructions="do it")
    handover = _terminal_handover("a", injected_tasks=[injected])
    applied: list[str] = []

    def apply_injection(ws2: Workspace, task_id: str, _h: Handover) -> Workspace:
        applied.append(task_id)
        return workspace_mod.inject_tasks(
            ws2,
            [
                Task(
                    id="sub-1", team="research", status=TaskStatus.PENDING,
                    parent_task_id=task_id, injection_depth=1,
                )
            ],
            [],
        )

    workspace_dag.advance_workspace_dag(
        ws,
        "a",
        queue=queue,
        pubsub=pubsub,
        teams={"research": FakeTeam("research")},
        handover=handover,
        apply_injection=apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    assert applied == ["a"]
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert any(t.id == "sub-1" for t in reloaded.meta.tasks)


def test_advance_workspace_dag_stalls_when_no_ready_successor(
    root: Path, queue: Queue
) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.PENDING),
            _task("b", status=TaskStatus.PENDING, depends_on=["missing-team-task"]),
        ],
        name="stall-adv",
    )
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    handover = _terminal_handover("a")
    workspace_dag.advance_workspace_dag(
        ws,
        "a",
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert len(calls) == 1


def test_advance_workspace_dag_recovers_frozen_chain_instead_of_stalling(
    root: Path, queue: Queue
) -> None:
    ws = _ws(
        root,
        [
            _task("root-done", status=TaskStatus.RUNNING),
            _task("ancestor", status=TaskStatus.PENDING),
            _task("descendant", status=TaskStatus.PENDING, depends_on=["ancestor"]),
        ],
        name="frozen-adv",
    )
    ws = workspace_mod.update_task_status(
        ws, "ancestor", TaskStatus.AWAITING_INPUT, blocked_handover_id="h-stuck"
    )
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    handover = _terminal_handover("root-done")
    workspace_dag.advance_workspace_dag(
        ws,
        "root-done",
        queue=queue,
        pubsub=pubsub,
        teams={"research": FakeTeam("research")},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    ancestor = next(t for t in reloaded.meta.tasks if t.id == "ancestor")
    assert ancestor.status is TaskStatus.PENDING
    assert ancestor.blocked_handover_id is None
    assert reloaded.meta.status is Status.ACTIVE
    assert reloaded.meta.park_reason is None
    assert calls == []


def _frozen_ws(
    root: Path,
    *,
    ws_status: Status = Status.ESCALATED,
    ancestor_status: TaskStatus = TaskStatus.AWAITING_INPUT,
    blocked_handover_id: str | None = "h1",
    extra_tasks: list[Task] | None = None,
    name: str = "frozen-ws",
) -> Workspace:
    tasks = [
        Task(
            id="ancestor",
            team="research",
            status=ancestor_status,
            blocked_handover_id=blocked_handover_id,
        ),
        Task(
            id="descendant",
            team="research",
            status=TaskStatus.PENDING,
            depends_on=["ancestor"],
        ),
        *(extra_tasks or []),
    ]
    ws = workspace_mod.create(name, "d", root=root, tasks=tasks)
    return workspace_mod.transition(ws, ws_status)


def test_frozen_chain_detects_stuck_ancestor_with_pending_descendant(
    root: Path, queue: Queue
) -> None:
    ws = _frozen_ws(root)
    finding = workspace_dag.frozen_chain(ws, queue=queue)
    assert finding is not None
    assert finding.ancestor_id == "ancestor"
    assert finding.blocked_descendant_ids == ("descendant",)


def test_frozen_chain_none_when_workspace_not_stuck(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(root, ws_status=Status.ACTIVE)
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_none_when_no_blocked_handover_id(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(root, blocked_handover_id=None)
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_none_for_leaf_with_no_dependents(root: Path, queue: Queue) -> None:
    ws = workspace_mod.create(
        "frozen-leaf",
        "d",
        root=root,
        tasks=[
            Task(
                id="ancestor",
                team="research",
                status=TaskStatus.AWAITING_INPUT,
                blocked_handover_id="h1",
            )
        ],
    )
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_none_when_ancestor_has_in_flight_job(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(root, name="frozen-inflight")
    queue.enqueue("ancestor", "research", workspace_id=ws.workspace_id)
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_none_when_merged_sibling_present(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(
        root,
        name="frozen-merged-sibling",
        extra_tasks=[
            Task(
                id="sibling",
                team="research",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="ship it", evidence="PR #9")],
            )
        ],
    )
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_none_when_descendant_blocked_by_other_pending_dep_too(
    root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create(
        "frozen-multi-dep",
        "d",
        root=root,
        tasks=[
            Task(
                id="ancestor",
                team="research",
                status=TaskStatus.AWAITING_INPUT,
                blocked_handover_id="h1",
            ),
            Task(id="other", team="research", status=TaskStatus.PENDING),
            Task(
                id="descendant",
                team="research",
                status=TaskStatus.PENDING,
                depends_on=["ancestor", "other"],
            ),
        ],
    )
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    assert workspace_dag.frozen_chain(ws, queue=queue) is None


def test_frozen_chain_finds_second_candidate_when_first_has_no_descendants(
    root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create(
        "frozen-second",
        "d",
        root=root,
        tasks=[
            Task(
                id="lone",
                team="research",
                status=TaskStatus.AWAITING_INPUT,
                blocked_handover_id="h1",
            ),
            Task(
                id="ancestor",
                team="research",
                status=TaskStatus.AWAITING_INPUT,
                blocked_handover_id="h2",
            ),
            Task(
                id="descendant",
                team="research",
                status=TaskStatus.PENDING,
                depends_on=["ancestor"],
            ),
        ],
    )
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    finding = workspace_dag.frozen_chain(ws, queue=queue)
    assert finding is not None
    assert finding.ancestor_id == "ancestor"


def test_frozen_chain_exception_wrapped_returns_none(
    root: Path, queue: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _frozen_ws(root, name="frozen-exc")
    queue.close()
    with caplog.at_level("ERROR", logger="alan.workspace_dag"):
        assert workspace_dag.frozen_chain(ws, queue=queue) is None
    assert any("detection failed" in r.message for r in caplog.records)


def test_maybe_recover_frozen_chain_false_when_not_frozen(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(root, ws_status=Status.ACTIVE)
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    fired = workspace_dag.maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams={},
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
        schedule_manager_summary=summary,
    )
    assert fired is False
    assert calls == []


def test_maybe_recover_frozen_chain_redispatches_under_cap(root: Path, queue: Queue) -> None:
    ws = _frozen_ws(root, name="frozen-redispatch")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    enqueued: list[dict[str, object]] = []

    def fake_enqueue(**kwargs: object) -> tuple[Job, str]:
        enqueued.append(kwargs)
        return _fake_job(), "inv_1"

    fired = workspace_dag.maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams={"research": FakeTeam("research")},
        enqueue_workflow=fake_enqueue,
        schedule_manager_summary=summary,
    )
    assert fired is True
    assert calls == []
    assert len(enqueued) == 1
    assert enqueued[0]["task_id"] == "ancestor"

    reloaded = workspace_mod.read(ws.task_id, root=root)
    ancestor = next(t for t in reloaded.meta.tasks if t.id == "ancestor")
    assert ancestor.status is TaskStatus.PENDING
    assert ancestor.blocked_handover_id is None
    assert reloaded.meta.status is Status.ACTIVE
    key = workspace_mod.revisit_key(
        ws.workspace_id,
        "ancestor",
        workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
        workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
    )
    assert reloaded.meta.revisit_counts[key] == 1


def test_maybe_recover_frozen_chain_keeps_status_when_other_subtask_awaits(
    root: Path, queue: Queue
) -> None:
    ws = _frozen_ws(
        root,
        name="frozen-other-awaits",
        extra_tasks=[Task(id="unrelated", team="research", status=TaskStatus.AWAITING_INPUT)],
    )
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    fired = workspace_dag.maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams={"research": FakeTeam("research")},
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
        schedule_manager_summary=summary,
    )
    assert fired is True
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ESCALATED


def test_maybe_recover_frozen_chain_parks_when_cap_exhausted(
    root: Path, queue: Queue
) -> None:
    from alan.loop_guard import REVISIT_CAP

    ws = _frozen_ws(root, name="frozen-park")
    for _ in range(REVISIT_CAP):
        ws = workspace_mod.increment_revisit_count(
            ws,
            workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            task_id="ancestor",
        )
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    events: list[str] = []
    pubsub.subscribe(ws.workspace_id, lambda e: events.append(e.kind))

    fired = workspace_dag.maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams={"research": FakeTeam("research")},
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
        schedule_manager_summary=summary,
    )
    assert fired is True
    assert len(calls) == 1
    assert events == ["frozen_chain_park"]

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is park_mod.ParkReason.FROZEN_CHAIN
    ancestor = next(t for t in reloaded.meta.tasks if t.id == "ancestor")
    assert ancestor.status is TaskStatus.AWAITING_INPUT
    blockers = reloaded.meta.blockers
    assert len(blockers) == 1
    assert blockers[0].name == "ancestor"
    assert blockers[0].instruction is not None
    assert "descendant" in blockers[0].instruction


def test_maybe_recover_frozen_chain_oserror_logged(
    root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ws = _frozen_ws(root, name="frozen-oserr")
    calls, summary = _collecting_summary()
    pubsub = PubSub()

    def boom(*_args: object, **_kwargs: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_mod, "update_task_status", boom)
    with caplog.at_level("ERROR", logger="alan.workspace_dag"):
        fired = workspace_dag.maybe_recover_frozen_chain(
            ws,
            queue=queue, pubsub=pubsub, teams={"research": FakeTeam("research")},
            enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
            schedule_manager_summary=summary,
        )
    assert fired is False
    assert calls == []
    assert any("recovery failed" in r.message for r in caplog.records)


def test_advance_workspace_dag_clears_merge_block_class_on_terminal_failed(
    root: Path, queue: Queue
) -> None:
    ws = _ws(
        root,
        [_task("a", status=TaskStatus.PENDING)],
        name="terminal-failed-clear",
        requirements=[],
    )
    ws = workspace_mod.transition(ws, Status.ACTIVE, merge_block_class="stale_review")
    captured, summary = _workspace_capturing_summary()
    pubsub = PubSub()
    handover = _terminal_handover("a")
    workspace_dag.advance_workspace_dag(
        ws,
        "a",
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.FAILED
    assert reloaded.meta.merge_block_class is None
    assert len(captured) == 1
    assert captured[0].meta.merge_block_class is None


def test_advance_workspace_dag_clears_merge_block_class_on_terminal_partial(
    root: Path, queue: Queue
) -> None:
    ws = _ws(
        root,
        [
            _task("a", status=TaskStatus.FAILED),
            _task("b", status=TaskStatus.RUNNING, dod_clauses=[TaskDoDClause(text="ship it")]),
        ],
        name="terminal-partial-clear",
    )
    ws = workspace_mod.transition(ws, Status.ACTIVE, merge_block_class="stale_review")
    captured, summary = _workspace_capturing_summary()
    pubsub = PubSub()
    handover = _terminal_handover("b", pr_number=7)
    workspace_dag.advance_workspace_dag(
        ws,
        "b",
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.PARTIAL
    assert reloaded.meta.merge_block_class is None
    assert len(captured) == 1
    assert captured[0].meta.merge_block_class is None


def test_advance_workspace_dag_clears_merge_block_class_on_terminal_completed(
    root: Path, queue: Queue
) -> None:
    ws = _ws(root, None, name="terminal-completed-clear")
    ws = workspace_mod.transition(ws, Status.ACTIVE, merge_block_class="stale_review")
    captured, summary = _workspace_capturing_summary()
    pubsub = PubSub()
    handover = _terminal_handover(ws.task_id)
    workspace_dag.advance_workspace_dag(
        ws,
        ws.task_id,
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.COMPLETED
    assert reloaded.meta.merge_block_class is None
    assert len(captured) == 1
    assert captured[0].meta.merge_block_class is None


def test_advance_workspace_dag_clears_merge_block_class_legacy_zero_task_failed_no_partial_case(
    root: Path, queue: Queue
) -> None:
    ws = _ws(root, None, name="legacy-zero-task-failed-clear", requirements=[])
    ws = workspace_mod.transition(ws, Status.ACTIVE, merge_block_class="stale_review")
    captured, summary = _workspace_capturing_summary()
    pubsub = PubSub()
    handover = _terminal_handover(ws.task_id)
    workspace_dag.advance_workspace_dag(
        ws,
        ws.task_id,
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
    )
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.FAILED
    assert reloaded.meta.merge_block_class is None
    assert len(captured) == 1
    assert captured[0].meta.merge_block_class is None


def test_advance_workspace_dag_forwards_workspace_index_to_terminal_summary(
    root: Path, queue: Queue
) -> None:
    ws = _ws(root, None, name="legacy-ws-wi")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    handover = _terminal_handover(ws.task_id)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    workspace_dag.advance_workspace_dag(
        ws,
        ws.task_id,
        queue=queue,
        pubsub=pubsub,
        teams={},
        handover=handover,
        apply_injection=_noop_apply_injection,
        evaluate_requirements=_noop_evaluate_requirements,
        schedule_manager_summary=summary,
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
        workspace_index=sentinel,
    )
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert isinstance(kwargs, dict)
    assert kwargs["workspace_index"] is sentinel


def test_maybe_surface_stall_forwards_workspace_index(root: Path, queue: Queue) -> None:
    ws = _ws(root, [_task("a", status=TaskStatus.PENDING)], name="stall-fire-wi")
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    sentinel = WorkspaceIndex(root=root, queue=queue)
    workspace_dag.maybe_surface_stall(
        ws, "root", queue=queue, pubsub=pubsub, schedule_manager_summary=summary,
        workspace_index=sentinel,
    )
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert isinstance(kwargs, dict)
    assert kwargs["workspace_index"] is sentinel


def test_maybe_recover_frozen_chain_forwards_workspace_index_to_park(
    root: Path, queue: Queue
) -> None:
    from alan.loop_guard import REVISIT_CAP

    ws = _frozen_ws(root, name="frozen-park-wi")
    for _ in range(REVISIT_CAP):
        ws = workspace_mod.increment_revisit_count(
            ws,
            workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            workspace_dag._FROZEN_CHAIN_NAMESPACE,  # pyright: ignore[reportPrivateUsage]
            task_id="ancestor",
        )
    calls, summary = _collecting_summary()
    pubsub = PubSub()
    sentinel = WorkspaceIndex(root=root, queue=queue)

    fired = workspace_dag.maybe_recover_frozen_chain(
        ws,
        queue=queue, pubsub=pubsub, teams={"research": FakeTeam("research")},
        enqueue_workflow=lambda **_kw: (_fake_job(), "inv"),
        schedule_manager_summary=summary,
        workspace_index=sentinel,
    )
    assert fired is True
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert isinstance(kwargs, dict)
    assert kwargs["workspace_index"] is sentinel
