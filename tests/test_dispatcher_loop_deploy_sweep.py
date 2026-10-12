from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import alan._dispatcher_loop as loop_mod
from alan import deploy_gate_sweep
from alan.dispatcher import Dispatcher
from alan.queue import Queue
from tests.fake_clock import FakeClock


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    yield q
    q.close()


def _dispatcher(queue: Queue, tmp_path: Path, clock: FakeClock) -> Dispatcher:
    return Dispatcher(queue, {}, workspaces_root=tmp_path, clock=clock)


def _counting_sweep(calls: list[Queue]) -> Callable[[Queue], int]:
    def _fake(q: Queue) -> int:
        calls.append(q)
        return 0

    return _fake


def test_construction_does_not_sweep_immediately(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(calls))
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    assert len(calls) == 0


def test_sweeps_once_the_interval_elapses(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(calls))
    clock.advance(loop_mod._DEPLOY_GATE_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    assert len(calls) == 1


def test_second_tick_within_interval_does_not_sweep_again(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(calls))
    clock.advance(loop_mod._DEPLOY_GATE_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    clock.advance(1.0)
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    assert len(calls) == 1


def test_sweep_fires_again_after_a_second_interval_elapses(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    calls: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(calls))
    clock.advance(loop_mod._DEPLOY_GATE_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    clock.advance(loop_mod._DEPLOY_GATE_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    assert len(calls) == 2


def test_sweep_receives_the_dispatcher_queue(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(monotonic=1000.0)
    dispatcher = _dispatcher(queue, tmp_path, clock)
    received: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(received))
    clock.advance(loop_mod._DEPLOY_GATE_SWEEP_INTERVAL_S)  # noqa: SLF001
    dispatcher._maybe_sweep_deploy_gates()  # noqa: SLF001
    assert received == [queue]


def test_loop_invokes_sweep_once_the_interval_elapses_while_running(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_mod, "_DEPLOY_GATE_SWEEP_INTERVAL_S", 0.0)
    calls: list[Queue] = []
    monkeypatch.setattr(deploy_gate_sweep, "promote_covered_jobs", _counting_sweep(calls))
    dispatcher = Dispatcher(queue, {}, workspaces_root=tmp_path, poll_interval=0.01)
    dispatcher.start()
    try:
        deadline = time.time() + 2.0
        while not calls and time.time() < deadline:
            time.sleep(0.01)
    finally:
        dispatcher.stop()
    assert calls
