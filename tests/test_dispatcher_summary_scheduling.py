from __future__ import annotations

import functools
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher, PubSub
from alan.queue import Job, JobStatus, Queue
from alan.workspace import Task, TaskStatus
from alan.workspace_index import WorkspaceIndex
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_teams import _NOOP_TEAMS, _make_handover, _RaisingTeam


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


def test_schedule_manager_summary_called_on_await_human(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.router import RouterDecision

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("sms-await", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id)
    d._handle_decision(ws, handover, RouterDecision(kind="await_human", reason="q"), ws.task_id)  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert synthesis == "ok"
    assert _status == "awaiting_input"


def test_schedule_manager_summary_called_on_advance_dag_terminal(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("sms-terminal", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id)
    handover = handover.model_copy(update={"full_synthesis": "terminal synth"})
    d._advance_workspace_dag(ws, ws.task_id, handover=handover)  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert synthesis == "terminal synth"
    assert _status == "completed"


def test_schedule_manager_summary_none_synthesis_when_no_handover(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("sms-no-ho", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, ws.task_id, handover=None)  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert synthesis is None
    assert _status == "completed"


def test_schedule_manager_summary_not_called_when_surface_routing_update_fails(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )

    def raising(*_a: object, **_k: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr("alan.dispatcher.workspace_mod.transition", raising)
    ws = workspace_mod.create("sms-surf-fail", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    job_obj = queue.list_jobs()[0]
    d._surface_routing_error(ws, job_obj, "route fail")  # noqa: SLF001
    assert len(calls) == 0


def test_schedule_manager_summary_called_on_surface_routing_success(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("sms-surf-ok", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    job_obj = queue.list_jobs()[0]
    d._surface_routing_error(ws, job_obj, "route err")  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert synthesis is None
    assert _status == "awaiting_input"
    from alan.park import ParkReason

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.park_reason is ParkReason.ROUTING_ERROR
    assert reloaded.meta.blockers[0].detail == "route err"


def test_on_ready_publishes_manager_summary_ready_event(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    ws = workspace_mod.create("sms-pubsub", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, events.append)

    captured_on_ready: list[Any] = []

    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: captured_on_ready.append(args[-1]),
    )
    d._advance_workspace_dag(ws, ws.task_id, handover=None)  # noqa: SLF001

    assert len(captured_on_ready) == 1
    captured_on_ready[0]()
    assert any(e.kind == "manager_summary_ready" for e in events)


def test_schedule_manager_summary_called_on_task_failure_cascade(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    task_t1 = Task(id="t1", team="research", status=TaskStatus.PENDING, depends_on=[])
    task_t2 = Task(id="t2", team="research", status=TaskStatus.PENDING, depends_on=["t1"])
    ws = workspace_mod.create("sms-cascade", "d", root=root, tasks=[task_t1, task_t2])
    job1 = queue.enqueue("t1", "research", workspace_id=ws.workspace_id)
    d = Dispatcher(queue, {}, workspaces_root=root)
    fake_job = Job(
        id=job1.id,
        task_id="t1",
        workspace_id=ws.workspace_id,
        team="research",
        status=JobStatus.RUNNING,
        enqueued_at=datetime.now(UTC),
    )
    d._observe_task_failure(fake_job)  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert _status == "failed"
    assert synthesis is None


def test_fail_workspace_triggers_manager_summary(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("fail-ws-sms", "d", root=root)
    d = Dispatcher(queue, {"research": _RaisingTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert _status == "awaiting_input"
    assert synthesis is None
    from alan.park import ParkReason

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.park_reason is ParkReason.CRASH
    assert len(reloaded.meta.blockers) == 1
    assert reloaded.meta.blockers[0].detail == "kaboom"


def test_schedule_manager_summary_skips_triage_sweep(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create(
        "Failure Triage Sweep", "d", root=root, team="triage"
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    dispatcher_mod._schedule_manager_summary(ws, "synth", d.pubsub, queue)  # noqa: SLF001
    assert calls == []


def test_schedule_manager_summary_fires_for_non_triage_workspace(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "alan.manager_summary.trigger_manager_summary",
        lambda *args, **_kw: calls.append(args),
    )
    ws = workspace_mod.create("ordinary-ws", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    dispatcher_mod._schedule_manager_summary(ws, "synth", d.pubsub, queue)  # noqa: SLF001
    assert len(calls) == 1
    _ws_path, _workspace_id, _title, _status, synthesis, _on_ready = calls[0]
    assert synthesis == "synth"


def test_schedule_manager_summary_supersedes_investigator_when_auto_continued(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    import alan.auto_continue as auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr(auto_continue_mod, "maybe_auto_continue", lambda *_a, **_kw: True)
    investigator_calls: list[object] = []
    monkeypatch.setattr(
        dispatcher_mod,
        "_maybe_trigger_investigator",
        lambda *a, **kw: investigator_calls.append((a, kw)),
    )
    ws = workspace_mod.create("auto-continued-ws", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    dispatcher_mod._schedule_manager_summary(ws, "synth", d.pubsub, queue)  # noqa: SLF001
    assert investigator_calls == []


def test_schedule_manager_summary_triggers_investigator_when_not_auto_continued(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    import alan.auto_continue as auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr(auto_continue_mod, "maybe_auto_continue", lambda *_a, **_kw: False)
    investigator_calls: list[object] = []
    monkeypatch.setattr(
        dispatcher_mod,
        "_maybe_trigger_investigator",
        lambda *a, **kw: investigator_calls.append((a, kw)),
    )
    ws = workspace_mod.create("not-auto-continued-ws", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    dispatcher_mod._schedule_manager_summary(ws, "synth", d.pubsub, queue)  # noqa: SLF001
    assert len(investigator_calls) == 1


def test_dispatcher_stores_workspace_index(queue: Queue, root: Path) -> None:
    sentinel = WorkspaceIndex(root=root, queue=queue)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root, workspace_index=sentinel)
    assert d._workspace_index is sentinel  # noqa: SLF001


def test_dispatcher_workspace_index_defaults_to_none(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    assert d._workspace_index is None  # noqa: SLF001


def test_schedule_manager_summary_forwards_workspace_index_to_auto_continue(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    import alan.auto_continue as auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    captured: list[object] = []

    def fake_maybe_auto_continue(*_a: object, **kw: object) -> bool:
        captured.append(kw.get("workspace_index"))
        return False

    monkeypatch.setattr(auto_continue_mod, "maybe_auto_continue", fake_maybe_auto_continue)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    ws = workspace_mod.create("sms-wi-forward", "d", root=root)
    dispatcher_mod._schedule_manager_summary(  # noqa: SLF001
        ws, "synth", PubSub(), queue, workspace_index=sentinel
    )
    assert captured == [sentinel]


def test_dispatcher_forwards_workspace_index_through_advance_workspace_dag(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_advance(*_a: object, **kw: object) -> None:
        captured["workspace_index"] = kw.get("workspace_index")

    monkeypatch.setattr(workspace_dag_mod, "advance_workspace_dag", fake_advance)
    ws = workspace_mod.create("adv-wi-forward", "d", root=root)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    d = Dispatcher(queue, {}, workspaces_root=root, workspace_index=sentinel)
    d._advance_workspace_dag(ws, ws.task_id, handover=None)  # noqa: SLF001
    assert captured["workspace_index"] is sentinel


def test_dispatcher_forwards_workspace_index_through_maybe_surface_stall(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_stall(*_a: object, **kw: object) -> None:
        captured["workspace_index"] = kw.get("workspace_index")

    monkeypatch.setattr(workspace_dag_mod, "maybe_surface_stall", fake_stall)
    ws = workspace_mod.create("stall-wi-forward", "d", root=root)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    d = Dispatcher(queue, {}, workspaces_root=root, workspace_index=sentinel)
    d._maybe_surface_stall(ws, "a")  # noqa: SLF001
    assert captured["workspace_index"] is sentinel


def test_dispatcher_forwards_workspace_index_through_maybe_recover_frozen_chain(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_recover(*_a: object, **kw: object) -> bool:
        captured["workspace_index"] = kw.get("workspace_index")
        return False

    monkeypatch.setattr(workspace_dag_mod, "maybe_recover_frozen_chain", fake_recover)
    ws = workspace_mod.create("frozen-wi-forward", "d", root=root)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    d = Dispatcher(queue, {}, workspaces_root=root, workspace_index=sentinel)
    d._maybe_recover_frozen_chain(ws)  # noqa: SLF001
    assert captured["workspace_index"] is sentinel


def test_dispatcher_forwards_workspace_index_via_fail_workspace(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod

    captured: list[object] = []

    def fake_schedule(*_a: object, **kw: object) -> None:
        captured.append(kw.get("workspace_index"))

    monkeypatch.setattr(dispatcher_mod, "_schedule_manager_summary", fake_schedule)
    ws = workspace_mod.create("fail-ws-wi-forward", "d", root=root)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    d = Dispatcher(
        queue, {"research": _RaisingTeam()}, workspaces_root=root, workspace_index=sentinel
    )
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert captured == [sentinel]


def test_schedule_manager_summary_considers_cross_item_advance_when_not_auto_continued(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    from alan import project_auto_continue as project_auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "alan.auto_continue.maybe_auto_continue", lambda *_a, **_kw: False
    )
    monkeypatch.setattr(dispatcher_mod, "_maybe_trigger_investigator", lambda *_a, **_kw: None)
    captured: list[dict[str, object]] = []

    def fake_maybe_advance(*_a: object, **kw: object) -> bool:
        captured.append(kw)
        return True

    monkeypatch.setattr(
        project_auto_continue_mod, "maybe_advance_project", fake_maybe_advance
    )
    ws = workspace_mod.create("cross-item-considered-ws", "d", root=root)
    sentinel = WorkspaceIndex(root=root, queue=queue)
    dispatcher_mod._schedule_manager_summary(  # noqa: SLF001
        ws, "synth", PubSub(), queue, workspace_index=sentinel, workspaces_root=root
    )
    assert len(captured) == 1
    assert captured[0]["workspaces_root"] == root
    assert captured[0]["workspace_index"] is sentinel


def test_schedule_manager_summary_skips_cross_item_advance_when_auto_continued(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    from alan import project_auto_continue as project_auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr("alan.auto_continue.maybe_auto_continue", lambda *_a, **_kw: True)
    captured: list[object] = []

    def fake_maybe_advance(*_a: object, **kw: object) -> bool:
        captured.append(kw)
        return False

    monkeypatch.setattr(project_auto_continue_mod, "maybe_advance_project", fake_maybe_advance)
    ws = workspace_mod.create("cross-item-skipped-ws", "d", root=root)
    dispatcher_mod._schedule_manager_summary(  # noqa: SLF001
        ws, "synth", PubSub(), queue, workspaces_root=root
    )
    assert captured == []


def test_schedule_manager_summary_swallows_cross_item_advance_errors(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    from alan import project_auto_continue as project_auto_continue_mod

    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)
    monkeypatch.setattr("alan.auto_continue.maybe_auto_continue", lambda *_a, **_kw: False)
    monkeypatch.setattr(dispatcher_mod, "_maybe_trigger_investigator", lambda *_a, **_kw: None)

    def boom(*_a: object, **_kw: object) -> bool:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(project_auto_continue_mod, "maybe_advance_project", boom)
    ws = workspace_mod.create("cross-item-boom-ws", "d", root=root)
    dispatcher_mod._schedule_manager_summary(  # noqa: SLF001
        ws, "synth", PubSub(), queue, workspaces_root=root
    )


def test_advance_workspace_dag_binds_workspaces_root_into_schedule_manager_summary(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_advance(*_a: object, **kw: object) -> None:
        captured["schedule_manager_summary"] = kw.get("schedule_manager_summary")

    monkeypatch.setattr(workspace_dag_mod, "advance_workspace_dag", fake_advance)
    ws = workspace_mod.create("adv-root-bind", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._advance_workspace_dag(ws, ws.task_id, handover=None)  # noqa: SLF001
    bound = captured["schedule_manager_summary"]
    assert isinstance(bound, functools.partial)
    assert bound.keywords["workspaces_root"] == root


def test_maybe_surface_stall_binds_workspaces_root_into_schedule_manager_summary(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_stall(*_a: object, **kw: object) -> None:
        captured["schedule_manager_summary"] = kw.get("schedule_manager_summary")

    monkeypatch.setattr(workspace_dag_mod, "maybe_surface_stall", fake_stall)
    ws = workspace_mod.create("stall-root-bind", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._maybe_surface_stall(ws, "a")  # noqa: SLF001
    bound = captured["schedule_manager_summary"]
    assert isinstance(bound, functools.partial)
    assert bound.keywords["workspaces_root"] == root


def test_maybe_recover_frozen_chain_binds_workspaces_root_into_schedule_manager_summary(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import workspace_dag as workspace_dag_mod

    captured: dict[str, object] = {}

    def fake_recover(*_a: object, **kw: object) -> bool:
        captured["schedule_manager_summary"] = kw.get("schedule_manager_summary")
        return False

    monkeypatch.setattr(workspace_dag_mod, "maybe_recover_frozen_chain", fake_recover)
    ws = workspace_mod.create("frozen-root-bind", "d", root=root)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d._maybe_recover_frozen_chain(ws)  # noqa: SLF001
    bound = captured["schedule_manager_summary"]
    assert isinstance(bound, functools.partial)
    assert bound.keywords["workspaces_root"] == root
