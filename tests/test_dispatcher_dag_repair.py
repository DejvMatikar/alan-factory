from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.queue import Queue
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, Task, TaskStatus, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def _ts() -> datetime:
    return datetime.now(UTC)


def _terminal_handover(task_id: str, from_team: str = "retrospective") -> Handover:
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
    )


class _TerminalTeam:
    supports_resume = False

    def __init__(self, name: str = "retrospective") -> None:
        self.name = name

    def plan_workflow(
        self, _w: Workspace, _p: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

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
        return _terminal_handover(workspace.task_id, from_team=self.name)


def test_dag_stall_surfaces_as_awaiting_input(queue: Queue, root: Path) -> None:
    task_a = Task(id="task-a", team="retrospective", status=TaskStatus.PENDING, depends_on=[])
    task_b = Task(
        id="task-b", team="missing-team", status=TaskStatus.PENDING, depends_on=["task-a"]
    )
    ws = workspace_mod.create(
        "stall-test", "d", root=root, tasks=[task_a, task_b]
    )
    events: list[TeamEvent] = []
    lock = threading.Lock()

    def collect(ev: TeamEvent) -> None:
        with lock:
            events.append(ev)

    teams: dict[str, Team] = {"retrospective": _TerminalTeam("retrospective")}
    d = Dispatcher(queue, teams, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, collect)

    enqueue_with_pending_workflow(
        queue=queue, pubsub=d.pubsub, workspace=ws,
        team=teams["retrospective"], prior=None, task_id="task-a",
    )
    d.run_once()

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT

    stall_events = [e for e in events if e.kind == "dag_stall"]
    assert len(stall_events) == 1
    assert "task-a" in stall_events[0].payload["reason"]
    assert "1 subtask(s)" in stall_events[0].payload["reason"]


def test_dag_has_active_work_returns_false_when_all_tasks_terminal(
    queue: Queue, root: Path
) -> None:
    task_a = Task(id="task-a", team="retrospective", status=TaskStatus.COMPLETED, depends_on=[])
    task_b = Task(
        id="task-b", team="retrospective", status=TaskStatus.COMPLETED, depends_on=["task-a"]
    )
    ws = workspace_mod.create(
        "all-terminal", "d", root=root, tasks=[task_a, task_b]
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    result = d._dag_has_active_work(ws)  # noqa: SLF001
    assert result is False


def test_maybe_surface_stall_no_op_for_no_tasks(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("no-tasks", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._maybe_surface_stall(ws, "ignored")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ACTIVE


def test_maybe_surface_stall_no_op_when_all_tasks_terminal(
    queue: Queue, root: Path
) -> None:
    task_a = Task(id="task-a", team="retrospective", status=TaskStatus.COMPLETED, depends_on=[])
    task_b = Task(
        id="task-b", team="retrospective", status=TaskStatus.COMPLETED, depends_on=["task-a"]
    )
    ws = workspace_mod.create(
        "all-done", "d", root=root, tasks=[task_a, task_b]
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._maybe_surface_stall(ws, "task-b")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ACTIVE


def test_maybe_surface_stall_oserror_logged(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    task_a = Task(id="task-a", team="retrospective", status=TaskStatus.PENDING, depends_on=[])
    ws = workspace_mod.create("oserr-stall", "d", root=root, tasks=[task_a])

    def boom(*_args: object, **_kwargs: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr("alan.dispatcher.workspace_mod.transition", boom)
    d = Dispatcher(queue, {}, workspaces_root=root)
    with caplog.at_level(logging.ERROR, logger="alan.dispatcher"):
        d._maybe_surface_stall(ws, "ignored")  # noqa: SLF001
    assert any("transition failed" in r.message for r in caplog.records)
