from __future__ import annotations

from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan._dispatcher_retire import (
    WORKSPACE_DELETE_BLOCKED_DETAIL,
    RetireResult,
    _settle_for_delete,
    retire_workspace,
)
from alan.clock import SYSTEM_CLOCK
from alan.dispatcher import Dispatcher
from alan.queue import JobStatus, Queue


def _noop_sleep(_seconds: float) -> None:
    return None


def test_retire_result_is_frozen_with_default_blocked_reason() -> None:
    result = RetireResult(deleted=True)
    assert result.blocked_reason is None
    with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError, no need to over-specify
        result.deleted = False  # type: ignore[misc]


def test_retire_workspace_deletes_when_nothing_active(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    result = retire_workspace(d, ws.task_id, queue, ws_root, sleep=_noop_sleep)
    assert result == RetireResult(deleted=True)
    assert not workspace_mod.exists(ws.task_id, root=ws_root)


def test_retire_workspace_deletes_job_rows(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    j = queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.complete(j.id)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    retire_workspace(d, ws.task_id, queue, ws_root, sleep=_noop_sleep)
    assert queue.list_jobs(task_id=ws.task_id) == []


def test_retire_workspace_blocks_on_genuinely_pending_work(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    result = retire_workspace(
        d, ws.task_id, queue, ws_root, settle_timeout_s=0.05,
        poll_interval_s=0.01, sleep=_noop_sleep,
    )
    assert result == RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
    assert workspace_mod.exists(ws.task_id, root=ws_root)


def test_retire_workspace_settles_a_running_job_that_clears_in_time(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    j = queue.enqueue(ws.task_id, "research")
    queue.claim()
    calls: list[float] = []

    def clearing_sleep(seconds: float) -> None:
        calls.append(seconds)
        queue.complete(j.id)

    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    result = retire_workspace(
        d, ws.task_id, queue, ws_root, settle_timeout_s=5.0,
        poll_interval_s=0.01, sleep=clearing_sleep,
    )
    assert result == RetireResult(deleted=True)
    assert calls == [0.01]


def test_retire_workspace_times_out_when_running_job_never_clears(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    result = retire_workspace(
        d, ws.task_id, queue, ws_root, settle_timeout_s=0.03,
        poll_interval_s=0.01, sleep=_noop_sleep, clock=SYSTEM_CLOCK,
    )
    assert result == RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
    assert workspace_mod.exists(ws.task_id, root=ws_root)
    assert queue.list_jobs(task_id=ws.task_id)[0].status is JobStatus.RUNNING


def test_retire_workspace_with_no_dispatcher_skips_settle_and_checks_immediately(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    calls: list[float] = []
    result = retire_workspace(
        None, ws.task_id, queue, ws_root, settle_timeout_s=5.0,
        sleep=lambda s: calls.append(s),
    )
    assert result == RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
    assert calls == []


def test_retire_workspace_serializes_under_the_per_workspace_lock(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    with d._get_workspace_lock(ws.task_id):  # noqa: SLF001
        with pytest.raises(RuntimeError, match="re-entry"):
            retire_workspace(d, ws.task_id, queue, ws_root, sleep=_noop_sleep)


def test_retire_workspace_reports_blocked_when_delete_is_a_no_op(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)
    monkeypatch.setattr(workspace_mod, "delete", lambda *_a, **_kw: None)
    result = retire_workspace(d, ws.task_id, queue, ws_root, sleep=_noop_sleep)
    assert result == RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)


def test_retire_workspace_swallows_workspace_error_and_reports_blocked(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    d = Dispatcher(queue, {}, workspaces_root=ws_root)

    def raising(*_args: object, **_kwargs: object) -> None:
        raise workspace_mod.WorkspaceError("racing OSError")

    monkeypatch.setattr(workspace_mod, "delete", raising)
    result = retire_workspace(d, ws.task_id, queue, ws_root, sleep=_noop_sleep)
    assert result == RetireResult(deleted=False, blocked_reason=WORKSPACE_DELETE_BLOCKED_DETAIL)
    assert workspace_mod.exists(ws.task_id, root=ws_root)


def test_settle_for_delete_returns_true_immediately_with_no_running_jobs(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    calls: list[float] = []
    assert _settle_for_delete(
        queue, ws.task_id, clock=SYSTEM_CLOCK, timeout_s=5.0,
        poll_interval_s=0.01, sleep=lambda s: calls.append(s),
    )
    assert calls == []


def test_settle_for_delete_bypasses_the_paused_tasks_exclusion(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    queue.enqueue(ws.task_id, "research")
    queue.claim()
    queue.pause_task(ws.task_id)
    assert queue.has_active_work(ws.task_id) is False
    assert _settle_for_delete(
        queue, ws.task_id, clock=SYSTEM_CLOCK, timeout_s=0.03,
        poll_interval_s=0.01, sleep=_noop_sleep,
    ) is False
