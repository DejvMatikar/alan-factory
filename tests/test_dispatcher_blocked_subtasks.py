from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.queue import Queue
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import _write_multi_task_meta
from tests._dispatcher_support_teams import _make_handover


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


def test_finalize_blocked_stamps_task_awaiting_input_on_multitask(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("fb-stamp", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "running", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="escalate", reason="stuck")
    d._handle_decision(ws, handover, decision, "sub-b")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    sub_b = next(t for t in reloaded.meta.tasks if t.id == "sub-b")
    assert sub_b.status is workspace_mod.TaskStatus.AWAITING_INPUT
    sub_a = next(t for t in reloaded.meta.tasks if t.id == "sub-a")
    assert sub_a.status is not workspace_mod.TaskStatus.AWAITING_INPUT
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.ESCALATED
    assert reloaded.meta.blockers[0].detail == "stuck"


def test_finalize_blocked_stamps_blocked_handover_id_on_task(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("fb-hid", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "sub-b", "status": "running", "depends_on": []}],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="await_human", reason="need input")
    d._handle_decision(ws, handover, decision, "sub-b")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    sub_b = next(t for t in reloaded.meta.tasks if t.id == "sub-b")
    assert sub_b.blocked_handover_id == handover.handover_id
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.QUESTION
    assert reloaded.meta.blockers[0].detail == "need input"


def test_finalize_blocked_skips_task_stamp_for_single_task_workspace(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("fb-single", "d", root=root)
    assert ws.meta.tasks == []
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="escalate", reason="stuck")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert reloaded.meta.tasks == []


def test_sibling_done_does_not_clear_escalated_when_subtask_awaiting_input(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("sib-esc", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "running", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover_b = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(ws, handover_b, RouterDecision(kind="escalate", reason="stuck"), "sub-b")  # noqa: SLF001

    ws_fresh = workspace_mod.read(ws.task_id, root=root)
    assert ws_fresh.meta.status is workspace_mod.Status.ESCALATED

    handover_a = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(ws_fresh, handover_a, RouterDecision(kind="done", reason="done"), "sub-a")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_sibling_done_does_not_clear_awaiting_input_when_subtask_blocked(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("sib-await", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "running", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover_b = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(  # noqa: SLF001
        ws, handover_b, RouterDecision(kind="await_human", reason="need input"), "sub-b"
    )

    ws_fresh = workspace_mod.read(ws.task_id, root=root)
    assert ws_fresh.meta.status is workspace_mod.Status.AWAITING_INPUT

    handover_a = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(ws_fresh, handover_a, RouterDecision(kind="done", reason="done"), "sub-a")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_stall_not_surfaced_when_subtask_awaiting_input(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("stall-await", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "running", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover_b = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(ws, handover_b, RouterDecision(kind="escalate", reason="stuck"), "sub-b")  # noqa: SLF001

    ws_fresh = workspace_mod.read(ws.task_id, root=root)
    assert ws_fresh.meta.status is workspace_mod.Status.ESCALATED

    d._advance_workspace_dag(ws_fresh, "sub-a")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_blocked_handover_id_preserved_after_sibling_completes(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("hid-sib", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "running", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover_b = _make_handover(ws.task_id, from_team="impl").model_copy(
        update={"handover_id": "hov-b-distinct"}
    )
    d._handle_decision(ws, handover_b, RouterDecision(kind="escalate", reason="stuck"), "sub-b")  # noqa: SLF001

    ws_fresh = workspace_mod.read(ws.task_id, root=root)
    handover_a = _make_handover(ws.task_id, from_team="impl").model_copy(
        update={"handover_id": "hov-a-distinct"}
    )
    d._handle_decision(ws_fresh, handover_a, RouterDecision(kind="done", reason="done"), "sub-a")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    sub_b = next(t for t in reloaded.meta.tasks if t.id == "sub-b")
    assert sub_b.blocked_handover_id == handover_b.handover_id
    assert sub_b.blocked_handover_id != handover_a.handover_id


def test_workspace_reaches_terminal_after_awaiting_subtask_resolves(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("resolve-term", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {"id": "sub-a", "status": "completed", "depends_on": []},
            {"id": "sub-b", "status": "running", "depends_on": []},
        ],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover_b = _make_handover(ws.task_id, from_team="impl")
    d._handle_decision(ws, handover_b, RouterDecision(kind="escalate", reason="stuck"), "sub-b")  # noqa: SLF001

    ws_fresh = workspace_mod.read(ws.task_id, root=root)
    assert ws_fresh.meta.status is workspace_mod.Status.ESCALATED

    d._advance_workspace_dag(ws_fresh, "sub-b")  # noqa: SLF001

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_finalize_blocked_warns_on_missing_task_id(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("fb-warn", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "sub-x", "status": "running", "depends_on": []}],
    )
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="escalate", reason="stuck")
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d._handle_decision(ws, handover, decision, "nonexistent-task")  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert any("not found" in r.message for r in caplog.records)


def test_update_task_status_with_blocked_handover_id(root: Path) -> None:
    from alan.workspace import TaskStatus, update_task_status

    ws = workspace_mod.create("ts-bhid", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [{"id": "t1", "status": "running", "depends_on": []}],
    )
    ws2 = update_task_status(ws, "t1", TaskStatus.AWAITING_INPUT, blocked_handover_id="hov-123")
    assert ws2.meta.tasks[0].status is TaskStatus.AWAITING_INPUT
    assert ws2.meta.tasks[0].blocked_handover_id == "hov-123"
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].blocked_handover_id == "hov-123"


def test_update_task_status_clear_blocked_handover_id(root: Path) -> None:
    from alan.workspace import TaskStatus, update_task_status

    ws = workspace_mod.create("ts-bhid-clear", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "t1",
                "status": "awaiting_input",
                "depends_on": [],
                "blocked_handover_id": "hov-1",
            }
        ],
    )
    ws2 = update_task_status(
        ws, "t1", TaskStatus.PENDING, clear_blocked_handover_id=True
    )
    assert ws2.meta.tasks[0].status is TaskStatus.PENDING
    assert ws2.meta.tasks[0].blocked_handover_id is None
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].blocked_handover_id is None


def test_update_task_status_without_clear_flag_leaves_blocked_handover_id(
    root: Path,
) -> None:
    from alan.workspace import TaskStatus, update_task_status

    ws = workspace_mod.create("ts-bhid-noclear", "d", root=root)
    ws = _write_multi_task_meta(
        ws,
        [
            {
                "id": "t1",
                "status": "awaiting_input",
                "depends_on": [],
                "blocked_handover_id": "hov-1",
            }
        ],
    )
    ws2 = update_task_status(ws, "t1", TaskStatus.PENDING)
    assert ws2.meta.tasks[0].status is TaskStatus.PENDING
    assert ws2.meta.tasks[0].blocked_handover_id == "hov-1"
