from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.queue import JobStatus, Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _make_collector
from tests._dispatcher_support_teams import (
    _NOOP_TEAMS,
    _EmittingTeam,
    _make_handover,
    _NoopTeam,
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


def test_run_once_uses_pre_allocated_triggered_invocation(
    queue: Queue, root: Path
) -> None:
    from alan import workflow as workflow_mod

    ws = workspace_mod.create("pre-alloc", "d", root=root)
    pre_specs = [PendingTaskSpec(id="research", role="research", model="sonnet")]
    pre_inv = workflow_mod.seed_pending_workflow(
        ws, team="research", tasks=pre_specs
    )
    captured: list[str] = []

    class CapturingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return pre_specs

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str,
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            captured.append(inv_id)
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": CapturingTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=pre_inv)
    d.run_once()
    assert captured == [pre_inv]


def test_resolve_inv_id_fallback_seeds_source_task_id(
    queue: Queue, root: Path
) -> None:
    from alan.workflow import load_graph

    ws = workspace_mod.create("fallback-src", "d", root=root)
    captured_inv: list[str] = []

    class CapturingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return [PendingTaskSpec(id="research", role="research", model="sonnet")]

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str,
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            captured_inv.append(inv_id)
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": CapturingTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert len(captured_inv) == 1
    graph = load_graph(ws, captured_inv[0])
    assert graph.doc.source_task_id == ws.task_id


def test_run_once_empty_queue_returns_false(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert d.run_once() is False


def test_run_once_runs_team_and_completes_job(queue: Queue, root: Path) -> None:
    workspace_mod.create("task one", "d", root=root)
    invoked: list[Workspace] = []

    class RecordingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            ws: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            invoked.append(ws)
            return _make_handover(ws.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": RecordingTeam()}, workspaces_root=root)
    job = queue.enqueue("task-one", "research")
    assert d.run_once() is True
    refreshed = queue.list_jobs()[0]
    assert refreshed.id == job.id
    assert refreshed.status is JobStatus.COMPLETED
    assert len(invoked) == 1
    assert invoked[0].task_id == "task-one"


def test_call_team_forwards_task_id_to_run(queue: Queue, root: Path) -> None:
    workspace_mod.create("task-fwd", "d", root=root)
    captured_task_ids: list[str | None] = []

    class CapturingTaskIdTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            ws: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,
        ) -> Handover:
            captured_task_ids.append(task_id)
            return _make_handover(ws.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": CapturingTaskIdTeam()}, workspaces_root=root)
    queue.enqueue("task-fwd", "research")
    d.run_once()
    assert captured_task_ids == ["task-fwd"]


def test_unknown_team_fails_job_with_message(queue: Queue, root: Path) -> None:
    workspace_mod.create("task two", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.pubsub.subscribe("task-two", collect)
    queue.enqueue("task-two", "ghost")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert finished.error == "unknown team: ghost"
    assert any(e.kind == "job_failed" for e in events)


def test_missing_workspace_fails_job(queue: Queue, root: Path) -> None:
    root.mkdir()
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    queue.enqueue("vanished", "research")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert "workspace error" in (finished.error or "")


def test_run_team_logs_lifecycle_info(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_mod.create("lifecycle", "d", root=root)
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    queue.enqueue("lifecycle", "research")
    with caplog.at_level("INFO", logger="alan.dispatcher"):
        d.run_once()
    messages = [r.message for r in caplog.records]
    assert any("team start: task=lifecycle" in m for m in messages)
    assert any("team done: task=lifecycle" in m for m in messages)


def test_handover_save_failure_marks_workspace_failed(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("save-fail-status", "d", root=root)

    class WrongTaskTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            _ws: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover("wrong-id", from_team=self.name)

    d = Dispatcher(queue, {"research": WrongTaskTeam()}, workspaces_root=root)
    queue.enqueue("save-fail-status", "research")
    d.run_once()
    reloaded = workspace_mod.read("save-fail-status", root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_dispatcher_writes_handover_after_team_run(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("save-out", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    d.pubsub.subscribe(ws.task_id, collect)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    completed = [e for e in events if e.kind == "job_completed"]
    assert len(completed) == 1
    handover_path = Path(completed[0].payload["handover_path"])
    assert handover_path.exists()
    assert handover_path.parent == ws.path / "handovers"


def test_dispatcher_fails_job_when_handover_save_raises(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_mod.create("save-fail", "d", root=root)

    class WrongTaskTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            _ws: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover("wrong-id", from_team=self.name)

    events, collect = _make_collector()
    d = Dispatcher(queue, {"research": WrongTaskTeam()}, workspaces_root=root)
    d.pubsub.subscribe("save-fail", collect)
    queue.enqueue("save-fail", "research")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert "handover save" in (finished.error or "")


def test_dispatcher_passes_prior_handover_to_team(queue: Queue, root: Path) -> None:
    from alan.handover import save_handover

    ws = workspace_mod.create("with-prior", "d", root=root)
    prior = _make_handover(ws.task_id, from_team="research")
    save_handover(ws, prior)
    seen: list[Handover | None] = []

    class CapturingTeam:
        name = "implementation_plan"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            handover_prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            seen.append(handover_prior)
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(
        queue, {"implementation_plan": CapturingTeam()}, workspaces_root=root
    )
    queue.enqueue(
        ws.task_id, "implementation_plan", parent_handover_id=prior.handover_id
    )
    d.run_once()
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].handover_id == prior.handover_id


def test_dispatcher_passes_no_prior_when_parent_handover_id_missing(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("no-prior", "d", root=root)
    seen: list[Handover | None] = []

    class CapturingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            handover_prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            seen.append(handover_prior)
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": CapturingTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert seen == [None]


def test_dispatcher_fails_job_when_parent_handover_missing(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("orphan", "d", root=root)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    queue.enqueue(ws.task_id, "research", parent_handover_id="handover_x_ghost")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.FAILED
    assert "parent handover missing" in (finished.error or "")


def test_execute_loads_workspace_via_workspace_id(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("outer-ws", "d", root=root)
    loaded: list[str] = []

    class RecordingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self, _ws: Workspace, _prior: Handover | None
        ) -> list[Any]:
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
            loaded.append(workspace.task_id)
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(queue, {"research": RecordingTeam()}, workspaces_root=root)
    job = queue.enqueue(ws.task_id, "research", workspace_id=ws.workspace_id)
    assert job.workspace_id == ws.workspace_id
    d.run_once()
    assert loaded == [ws.task_id]


def test_publish_includes_task_id_in_event_payload(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("pub-tid", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    d.pubsub.subscribe("pub-tid", collect)
    queue.enqueue("pub-tid", "research")
    d.run_once()
    job_started = [e for e in events if e.kind == "job_started"]
    assert job_started
    assert job_started[0].payload.get("task_id") == "pub-tid"


def test_emitter_stamps_task_id_into_events(queue: Queue, root: Path) -> None:
    workspace_mod.create("emit-tid", "d", root=root)
    events, collect = _make_collector()
    d = Dispatcher(queue, {"research": _EmittingTeam()}, workspaces_root=root)
    d.pubsub.subscribe("emit-tid", collect)
    queue.enqueue("emit-tid", "research")
    d.run_once()
    team_events = [e for e in events if e.kind == "role_started"]
    assert team_events
    assert team_events[0].payload.get("task_id") == "emit-tid"


def test_stamp_task_id_injects_into_event_payload() -> None:
    from alan.teams._base import _stamp_task_id

    received: list[TeamEvent] = []
    wrapped = _stamp_task_id(received.append, "my-tid")
    wrapped(TeamEvent("kind", {"key": "val"}))
    assert received == [TeamEvent("kind", {"key": "val", "task_id": "my-tid"})]


def test_resume_workflow_node_stamps_explicit_task_id(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workflow as workflow_mod_inner
    from alan.claude_cli import AgentStreamingResult
    from alan.teams._base import resume_workflow_node
    from alan.workflow import PendingTaskSpec

    ws = workspace_mod.create("rrwn-tid", "d", root=root)
    inv_id = workflow_mod_inner.seed_pending_workflow(
        ws, team="rr", tasks=[PendingTaskSpec(id="rr", role="rr", model="sonnet")]
    )
    graph = workflow_mod_inner.load_graph(ws, inv_id)
    graph.start_node("rr", role="rr", model="sonnet", depends_on=[])
    graph.set_session_id("rr", "sid-xyz")
    graph.mark_interrupted("rr")
    workflow_mod_inner.save_workflow(ws, graph.doc)
    monkeypatch.setattr(
        "alan.teams._base.run_agent_streaming",
        lambda *_a, **_kw: AgentStreamingResult(result_text="ok", usage={}),
    )
    received: list[TeamEvent] = []
    resume_workflow_node(
        ws, graph, inv_id, received.append,
        node_id="rr", system_prompt="sp", user_text="ut",
        task_id="res-task",
    )
    started = [e for e in received if e.kind == "node_started"]
    assert started and started[0].payload.get("task_id") == "res-task"
