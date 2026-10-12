from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alan import driver_loop_runner as runner_mod
from alan import driver_loop_runs as runs_mod
from alan import driver_loop_status as status_mod
from alan import worker_records
from alan.detached_mode import DIAL_ENV
from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome, RunRecord
from alan.driver_loop_schedule import HoldReason
from alan.queue import Queue


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "command": "/alan-issue-driver",
        "cwd": "/opt/checkouts/alan-issue-driver",
        "self_paced": False,
        "interval_seconds": 1800,
        "timeout_seconds": 3600,
        "enabled": True,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


class _FakeProc:
    def __init__(self, pid: int = 1, rc: int | None = None) -> None:
        self.pid = pid
        self._rc = rc

    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        if self._rc is None:
            raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout or 0.0)
        return self._rc


class _FakeProcThatDiesOnWait(_FakeProc):
    def wait(self, timeout: float | None = None) -> int:
        return 137


@pytest.fixture(autouse=True)
def _cleanup_runner_state() -> Iterator[None]:
    runner_mod._live_procs.clear()
    yield
    runner_mod.stop()
    runner_mod._live_procs.clear()


@pytest.fixture(autouse=True)
def _redirect_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")


def test_tick_one_holds_when_disabled(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(enabled=False)
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert spawned == []


def test_tick_one_spawns_when_never_run_before(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry()
    fake_record = RunRecord(
        run_id="1", loop_name=entry.name, started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y",
    )
    fake_proc = _FakeProc(pid=42)
    calls: list[str] = []

    def _fake_spawn(e: DriverLoopEntry) -> tuple[RunRecord, _FakeProc]:
        calls.append(e.name)
        return fake_record, fake_proc

    monkeypatch.setattr(runs_mod, "spawn_run", _fake_spawn)
    changed: list[bool] = []
    runner_mod._tick_one(entry, queue, lambda: changed.append(True))
    assert calls == [entry.name]
    assert changed == [True]
    assert runner_mod._live_procs[entry.name] is fake_proc


def test_tick_one_no_spawn_when_not_due(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry()
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10,
                stdout_log="x", stderr_log="y", ended_at=now, outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert spawned == []


def test_tick_one_run_now_forces_spawn_even_when_not_due(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry()
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10,
                stdout_log="x", stderr_log="y", ended_at=now, outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    fake_record = RunRecord(
        run_id="2", loop_name=entry.name, started_at=now, deadline_at=now + 10,
        stdout_log="x", stderr_log="y",
    )
    fake_proc = _FakeProc(pid=42)
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: (fake_record, fake_proc))
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([entry], []))
    status_mod.request_run_now(entry.name, queue=queue)
    runner_mod._tick_one(entry, queue, None)
    assert runner_mod._live_procs[entry.name] is fake_proc
    status_mod.clear_run_now_request(entry.name)


def test_tick_one_does_not_spawn_a_second_run_while_one_is_live(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry()
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10000,
                stdout_log="x", stderr_log="y",
            )
        ],
    )
    runner_mod._live_procs[entry.name] = _FakeProc(pid=1, rc=None)
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert spawned == []


def test_kill_for_timeout_noop_when_pid_is_none() -> None:
    record = RunRecord(
        run_id="1", loop_name="a", started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y", pid=None, pgid=None,
    )
    runner_mod._kill_for_timeout(record)


def test_kill_for_timeout_uses_terminate_pid_when_no_pgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RunRecord(
        run_id="1", loop_name="a", started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y", pid=123, pgid=None,
    )
    calls: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_pid", lambda pid: calls.append(pid))
    runner_mod._kill_for_timeout(record)
    assert calls == [123]


def test_kill_for_timeout_skips_terminate_group_on_guard_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RunRecord(
        run_id="1", loop_name="a", started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y", pid=123, pgid=123,
    )

    def _raise_guard(pid: int) -> None:
        raise worker_records.GroupSignalGuardError("nope")

    monkeypatch.setattr(worker_records, "guard_no_group_signal", _raise_guard)
    calls: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", lambda pgid, **_kw: calls.append(pgid))
    runner_mod._kill_for_timeout(record)
    assert calls == []


def test_reap_live_proc_pops_when_no_record_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: None)
    runner_mod._live_procs["a"] = _FakeProc(pid=1, rc=None)
    runner_mod._reap_live_proc("a", runner_mod._live_procs["a"])
    assert "a" not in runner_mod._live_procs


def test_reap_live_proc_pops_when_record_already_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    record = RunRecord(
        run_id="1", loop_name="a", started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y", ended_at=2.0, outcome=RunOutcome.SUCCEEDED,
    )
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: record)
    runner_mod._live_procs["a"] = _FakeProc(pid=1, rc=None)
    runner_mod._reap_live_proc("a", runner_mod._live_procs["a"])
    assert "a" not in runner_mod._live_procs


def test_reap_live_proc_leaves_pending_when_wait_times_out_after_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RunRecord(
        run_id="1", loop_name="a", started_at=1.0, deadline_at=1.0,
        stdout_log="x", stderr_log="y", pid=999999, pgid=999999,
    )
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: record)
    monkeypatch.setattr(worker_records, "terminate_group", lambda pgid, **_kw: None)
    finalized: list[bool] = []
    monkeypatch.setattr(
        runs_mod, "finalize_live_run", lambda rec, *, exit_code, timed_out: finalized.append(True)
    )
    proc = _FakeProc(pid=1, rc=None)
    runner_mod._live_procs["a"] = proc
    runner_mod._reap_live_proc("a", proc)
    assert finalized == []
    assert "a" in runner_mod._live_procs


def test_tick_one_holds_when_fast_forward_fails(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: "git fetch failed: boom")
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert spawned == []


def test_tick_one_persists_fast_forward_failure_reason(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: "git fetch failed: boom")
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert status_mod.last_fast_forward_failure(entry.name) == "git fetch failed: boom"


def test_tick_one_clears_fast_forward_failure_on_success(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    status_mod.record_fast_forward_failure(entry.name, "stale failure")
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: None)
    fake_record = RunRecord(
        run_id="1", loop_name=entry.name, started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y",
    )
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: (fake_record, _FakeProc(pid=1)))
    runner_mod._tick_one(entry, queue, None)
    assert status_mod.last_fast_forward_failure(entry.name) is None


def test_tick_one_fast_forward_failure_reaches_snapshot(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: "git fetch failed: boom")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([entry], []))
    runner_mod._tick_one(entry, queue, None)
    views = status_mod.snapshot(queue=queue)
    assert views[0].decision.reason == HoldReason.FAST_FORWARD_FAILED
    assert views[0].decision.detail == "git fetch failed: boom"


def test_tick_one_skips_fast_forward_when_not_due(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10,
                stdout_log="x", stderr_log="y", ended_at=now, outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    calls: list[str] = []
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: calls.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert calls == []


def test_tick_one_run_now_still_attempts_fast_forward_when_not_due(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10,
                stdout_log="x", stderr_log="y", ended_at=now, outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    calls: list[str] = []

    def _fake_fast_forward(e: DriverLoopEntry) -> str | None:
        calls.append(e.name)
        return None

    monkeypatch.setattr(runner_mod, "_fast_forward", _fake_fast_forward)
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([entry], []))
    fake_record = RunRecord(
        run_id="1", loop_name=entry.name, started_at=now, deadline_at=now + 10,
        stdout_log="x", stderr_log="y",
    )
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: (fake_record, _FakeProc(pid=1)))
    status_mod.request_run_now(entry.name, queue=queue)
    runner_mod._tick_one(entry, queue, None)
    assert calls == [entry.name]
    status_mod.clear_run_now_request(entry.name)


def test_tick_one_skips_spawn_when_run_now_request_disappears_after_fast_forward(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    entry = _entry(dedicated_clone=True)
    now = time.time()
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=now, deadline_at=now + 10,
                stdout_log="x", stderr_log="y", ended_at=now, outcome=RunOutcome.SUCCEEDED,
            )
        ],
    )
    monkeypatch.setattr(runner_mod, "_fast_forward", lambda e: None)
    monkeypatch.setattr(status_mod, "run_now_pending", lambda name: True)
    monkeypatch.setattr(status_mod, "pop_run_now_request", lambda name: False)
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod._tick_one(entry, queue, None)
    assert spawned == []


def test_reap_if_finished_reaps_and_finalizes_completed_proc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text('{"type": "result", "result": "ok"}', encoding="utf-8")
    record = RunRecord(
        run_id="1", loop_name="alan-issue-driver", started_at=1.0, deadline_at=99999.0,
        stdout_log=str(log_path), stderr_log=str(tmp_path / "err.log"),
    )
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: record)
    finalized: list[int] = []
    monkeypatch.setattr(
        runs_mod,
        "finalize_live_run",
        lambda rec, *, exit_code, timed_out: finalized.append(exit_code),
    )
    runner_mod._live_procs["alan-issue-driver"] = _FakeProc(pid=1, rc=0)
    runner_mod._reap_if_finished("alan-issue-driver")
    assert finalized == [0]
    assert "alan-issue-driver" not in runner_mod._live_procs


def test_reap_if_finished_leaves_still_running_proc_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RunRecord(
        run_id="1", loop_name="alan-issue-driver", started_at=1.0, deadline_at=time.time() + 9999,
        stdout_log="x", stderr_log="y",
    )
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: record)
    runner_mod._live_procs["alan-issue-driver"] = _FakeProc(pid=1, rc=None)
    runner_mod._reap_if_finished("alan-issue-driver")
    assert "alan-issue-driver" in runner_mod._live_procs


def test_reap_if_finished_kills_past_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RunRecord(
        run_id="1", loop_name="alan-issue-driver", started_at=1.0, deadline_at=1.0,
        stdout_log="x", stderr_log="y", pid=999999, pgid=999999,
    )
    monkeypatch.setattr(runs_mod, "latest_run", lambda name: record)
    finalized: list[bool] = []
    monkeypatch.setattr(
        runs_mod,
        "finalize_live_run",
        lambda rec, *, exit_code, timed_out: finalized.append(timed_out),
    )
    killed: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", lambda pgid, **_kw: killed.append(pgid))
    runner_mod._live_procs["alan-issue-driver"] = _FakeProcThatDiesOnWait(pid=1, rc=None)
    runner_mod._reap_if_finished("alan-issue-driver")
    assert killed == [999999]
    assert finalized == [True]


def test_reap_if_finished_falls_back_to_reconcile_when_no_live_proc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        runs_mod, "reconcile_after_restart", lambda name: called.append(name)
    )
    runner_mod._reap_if_finished("some-loop")
    assert called == ["some-loop"]


def test_fast_forward_returns_none_for_non_dedicated_clone() -> None:
    entry = _entry(dedicated_clone=False)
    assert runner_mod._fast_forward(entry) is None


def test_fast_forward_runs_git_fetch_and_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(dedicated_clone=True)
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert runner_mod._fast_forward(entry) is None
    assert calls[0][:4] == ["git", "-C", entry.cwd, "fetch"]
    assert calls[1][:4] == ["git", "-C", entry.cwd, "merge"]


def test_fast_forward_reports_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(dedicated_clone=True)

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="network unreachable")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    error = runner_mod._fast_forward(entry)
    assert error is not None
    assert "fetch failed" in error


def test_fast_forward_reports_merge_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(dedicated_clone=True)
    call_count = 0

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        rc = 0 if call_count == 1 else 1
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="diverged")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    error = runner_mod._fast_forward(entry)
    assert error is not None
    assert "merge --ff-only failed" in error


def test_fast_forward_reports_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry(dedicated_clone=True)

    def _raise(*args: object, **kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", _raise)
    error = runner_mod._fast_forward(entry)
    assert error is not None
    assert "fast-forward error" in error


def test_fast_forward_expands_tilde_in_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = _entry(dedicated_clone=True, cwd="~/some-clone")
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert runner_mod._fast_forward(entry) is None
    expected_cwd = str(tmp_path / "some-clone")
    assert calls[0][2] == expected_cwd
    assert calls[1][2] == expected_cwd


def test_start_is_noop_when_not_supervised(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    runner_mod.start(queue=queue)
    assert runner_mod.is_running() is False


def test_start_and_stop_lifecycle(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([], []))
    runner_mod.start(queue=queue, tick_interval_s=0.05)
    assert runner_mod.is_running() is True
    runner_mod.stop()
    assert runner_mod.is_running() is False


def test_start_is_idempotent_while_already_running(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([], []))
    runner_mod.start(queue=queue, tick_interval_s=0.05)
    first_thread = runner_mod._thread
    runner_mod.start(queue=queue, tick_interval_s=0.05)
    assert runner_mod._thread is first_thread


def test_stop_when_never_started_is_safe() -> None:
    runner_mod.stop()
    assert runner_mod.is_running() is False


def test_run_loop_ticks_and_spawns(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    entry = _entry()
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([entry], []))
    fake_record = RunRecord(
        run_id="1", loop_name=entry.name, started_at=1.0, deadline_at=2.0,
        stdout_log="x", stderr_log="y",
    )
    calls: list[str] = []

    def _fake_spawn(e: DriverLoopEntry) -> tuple[RunRecord, _FakeProc]:
        calls.append(e.name)
        runs_mod._write_record(fake_record)
        return fake_record, _FakeProc(pid=1, rc=0)

    monkeypatch.setattr(runs_mod, "spawn_run", _fake_spawn)
    monkeypatch.setattr(runs_mod, "reconcile_after_restart", lambda name: None)
    runner_mod.start(queue=queue, tick_interval_s=0.02)
    deadline = time.time() + 2.0
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    runner_mod.stop()
    assert calls == [entry.name]


def test_run_loop_logs_and_continues_when_one_tick_raises(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = _entry()
    second = _entry(
        name="sample-issue-driver",
        command="/sample-issue-driver",
        cwd="/opt/checkouts/sample-issue-driver",
    )
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([first, second], []))
    stop_event = threading.Event()
    ticked: list[str] = []

    def _fake_tick_one(
        entry: DriverLoopEntry,
        _queue: Queue,
        _on_change: Callable[[], None] | None,
    ) -> None:
        ticked.append(entry.name)
        if entry.name == first.name:
            raise RuntimeError("tick exploded")
        stop_event.set()

    monkeypatch.setattr(runner_mod, "_tick_one", _fake_tick_one)
    caplog.set_level(logging.ERROR, logger="alan.driver_loop_runner")

    runner_mod._run_loop(queue, None, 0.0, stop_event)

    assert ticked == [first.name, second.name]
    failures = [
        record
        for record in caplog.records
        if record.getMessage() == f"driver loop tick failed for {first.name}"
    ]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
