from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

import alan._dispatcher_usage_limit as usage_limit_mod
import alan.dispatcher as dispatcher_mod
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted, ServerErrorRetryable
from alan.clock import Monotonic
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests.fake_clock import FakeClock


class _RecordingEvent(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.waited: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waited.append(timeout)
        self.set()
        return True


class _WrongDomainClock:
    def monotonic(self) -> Monotonic:
        return Monotonic(0.0)

    def now(self) -> Monotonic:
        return Monotonic(0.0)


class _RecordingThread:
    def __init__(self, on_join: Callable[[], None] | None = None) -> None:
        self.joined_with: list[float | None] = []
        self._on_join = on_join

    def join(self, timeout: float | None = None) -> None:
        self.joined_with.append(timeout)
        if self._on_join is not None:
            self._on_join()


class _ServerErrorTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise ServerErrorRetryable("overloaded: 529")


def test_loop_pause_wait_uses_injected_clock(tmp_path: Path) -> None:
    fake = FakeClock(epoch=1_000.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    queue.pause_until(1_002.0)
    d = Dispatcher(queue, {}, poll_interval=5.0, clock=fake)
    d._stop = _RecordingEvent()  # noqa: SLF001
    d._loop()  # noqa: SLF001
    assert d._stop.waited == [2.0]  # noqa: SLF001
    queue.close()


def test_loop_pause_wait_caps_at_poll_interval_when_pause_exceeds_it(
    tmp_path: Path,
) -> None:
    fake = FakeClock(epoch=1_000.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    queue.pause_until(1_050.0)
    d = Dispatcher(queue, {}, poll_interval=5.0, clock=fake)
    d._stop = _RecordingEvent()  # noqa: SLF001
    d._loop()  # noqa: SLF001
    assert d._stop.waited == [5.0]  # noqa: SLF001
    queue.close()


def test_loop_pause_wait_cross_domain_guard_fires(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeClock(epoch=1_000.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    queue.pause_until(1_002.0)
    d = Dispatcher(
        queue,
        {},
        poll_interval=5.0,
        clock=_WrongDomainClock(),  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    )
    d._stop = _RecordingEvent()  # noqa: SLF001
    with caplog.at_level(logging.ERROR, logger="alan.dispatcher"):
        d._loop()  # noqa: SLF001
    assert d._stop.waited == [5.0]  # noqa: SLF001
    assert any(
        r.exc_info is not None and r.exc_info[0] is TypeError for r in caplog.records
    )
    queue.close()


def test_join_workers_deadline_reflects_elapsed_time_between_threads(
    tmp_path: Path,
) -> None:
    fake = FakeClock(monotonic=100.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    d = Dispatcher(queue, {}, clock=fake)
    t1 = _RecordingThread(on_join=lambda: fake.advance(2.0))
    t2 = _RecordingThread()
    d._threads = [t1, t2]  # type: ignore[list-item]  # noqa: SLF001
    d._join_workers(5.0)  # noqa: SLF001
    assert t1.joined_with == [5.0]
    assert t2.joined_with == [3.0]
    queue.close()


def test_join_workers_deadline_floors_at_zero_once_elapsed_exceeds_timeout(
    tmp_path: Path,
) -> None:
    fake = FakeClock(monotonic=100.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    d = Dispatcher(queue, {}, clock=fake)
    t1 = _RecordingThread(on_join=lambda: fake.advance(10.0))
    t2 = _RecordingThread()
    d._threads = [t1, t2]  # type: ignore[list-item]  # noqa: SLF001
    d._join_workers(5.0)  # noqa: SLF001
    assert t1.joined_with == [5.0]
    assert t2.joined_with == [0.0]
    queue.close()


def test_delay_for_ci_reset_uses_injected_clock() -> None:
    fake = FakeClock(epoch=2_000.0)
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    updated = dispatcher_mod._delay_for_ci_reset(  # noqa: SLF001
        decision, MergeBlockClass.TRANSIENT_FLAKE, clock=fake
    )
    assert updated.eligible_at == pytest.approx(
        2_000.0 + dispatcher_mod._DEFAULT_PR_MERGE_REMEDIATION_RETRY_DELAY_S  # noqa: SLF001
    )


def test_maybe_retry_pr_merge_uses_injected_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_state_paths

    monkeypatch.setattr(_pr_state_paths, "load_remediation_retry_count", lambda _p, _f: 0)
    monkeypatch.setattr(_pr_state_paths, "bump_remediation_retry_count", lambda _p, _f: 1)
    fake = FakeClock(epoch=3_000.0)
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    d = Dispatcher(queue, {}, clock=fake)
    decision = d._maybe_retry_pr_merge(42, "reason", "stale_review", DEFAULT_FORGE_TARGET)  # noqa: SLF001
    assert decision is not None
    assert decision.eligible_at == pytest.approx(
        3_000.0 + dispatcher_mod._DEFAULT_PR_MERGE_REMEDIATION_RETRY_DELAY_S  # noqa: SLF001
    )
    queue.close()


def test_server_error_backoff_uses_injected_clock_within_jittered_bound(
    tmp_path: Path,
) -> None:
    fake = FakeClock(epoch=5_000.0)
    root = tmp_path / "workspaces"
    queue = Queue(db_path=tmp_path / "queue.db", clock=fake)
    ws = workspace_mod.create("t1", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d = Dispatcher(
        queue, {"research": _ServerErrorTeam()}, workspaces_root=root, clock=fake
    )
    d.run_once()
    job = queue.list_jobs()[0]
    assert job.eligible_at is not None
    base = usage_limit_mod._DEFAULT_SERVER_ERROR_BASE_DELAY  # noqa: SLF001
    assert 5_000.0 + base <= job.eligible_at <= 5_000.0 + base * 1.25
    queue.close()
