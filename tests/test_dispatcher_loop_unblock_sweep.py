from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import alan._dispatcher_loop as loop_mod
from alan import unblock_sweep as unblock_sweep_mod
from alan.dispatcher import Dispatcher
from alan.queue import Queue
from alan.workspace_index import WorkspaceIndex
from tests.fake_clock import FakeClock


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    yield q
    q.close()


def _dispatcher(
    queue: Queue, tmp_path: Path, clock: FakeClock, *, with_index: bool = True
) -> Dispatcher:
    workspace_index = WorkspaceIndex(root=tmp_path / "ws", queue=queue) if with_index else None
    return Dispatcher(
        queue, {}, workspaces_root=tmp_path, clock=clock, workspace_index=workspace_index
    )


def _counting_sweep(calls: list[dict[str, object]]) -> Callable[..., int]:
    def _fake(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    return _fake


class _TrackingLock:
    def __init__(self) -> None:
        self.entered = 0

    def __enter__(self) -> "_TrackingLock":
        self.entered += 1
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_construction_defaults_unblock_sweep_next_at_to_zero(queue: Queue, tmp_path: Path) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0))
    assert dispatcher._unblock_sweep_next_at == 0.0  # noqa: SLF001


def test_sweeps_on_first_tick_since_next_at_starts_at_zero(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0))
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert len(calls) == 1


def test_second_tick_within_interval_does_not_sweep_again(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    clock.advance(1.0)
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert len(calls) == 1


def test_sweep_fires_again_after_interval_elapses(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    clock.advance(loop_mod._UNBLOCK_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert len(calls) == 2


def test_sweep_compare_and_set_uses_deploy_gate_sweep_lock(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0))
    tracking = _TrackingLock()
    dispatcher._deploy_gate_sweep_lock = tracking  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep([]))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert tracking.entered == 1


def test_sweep_noop_when_workspace_index_missing(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0), with_index=False)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert calls == []


def test_sweep_noop_when_workspaces_root_missing(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_index = WorkspaceIndex(root=tmp_path / "never-created", queue=queue)
    dispatcher = Dispatcher(
        queue,
        {},
        workspaces_root=None,
        clock=FakeClock(monotonic=1000.0),
        workspace_index=workspace_index,
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert calls == []


def test_sweep_receives_dispatcher_state(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0))
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001
    assert calls[0]["queue"] is queue
    assert calls[0]["pubsub"] is dispatcher.pubsub
    assert calls[0]["workspace_index"] is dispatcher._workspace_index  # noqa: SLF001
    assert calls[0]["workspaces_root"] == tmp_path
    assert callable(calls[0]["lock_for"])
    assert callable(calls[0]["enqueue_workflow"])


def test_sweep_exception_does_not_propagate(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher = _dispatcher(queue, tmp_path, FakeClock(monotonic=1000.0))

    def _raise(**_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(unblock_sweep_mod, "sweep", _raise)
    dispatcher._maybe_sweep_unblocks()  # noqa: SLF001


def test_loop_invokes_unblock_sweep_while_running(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_mod, "_UNBLOCK_SWEEP_INTERVAL_S", 0.0)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(unblock_sweep_mod, "sweep", _counting_sweep(calls))
    dispatcher = Dispatcher(
        queue,
        {},
        workspaces_root=tmp_path,
        poll_interval=0.01,
        workspace_index=WorkspaceIndex(root=tmp_path / "ws", queue=queue),
    )
    dispatcher.start()
    try:
        deadline = time.time() + 2.0
        while not calls and time.time() < deadline:
            time.sleep(0.01)
    finally:
        dispatcher.stop()
    assert calls
