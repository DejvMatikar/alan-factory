from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import driver_loop_runner as runner_mod
from alan import driver_loop_runs as runs_mod
from alan import driver_loop_status as status_mod
from alan import worker_records
from alan.detached_mode import DIAL_ENV
from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome, RunRecord
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


@pytest.fixture(autouse=True)
def _cleanup_runner_state() -> Iterator[None]:
    runner_mod._live_procs.clear()
    yield
    runner_mod.stop()
    runner_mod._live_procs.clear()


@pytest.fixture(autouse=True)
def _redirect_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv(DIAL_ENV, "on")


def test_restart_adoption_resolves_dead_process_from_log(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, tmp_path: Path
) -> None:
    entry = _entry()
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([entry], []))
    log_path = tmp_path / "prior.stdout.log"
    log_path.write_text(
        json.dumps({"type": "result", "result": "prior run finished cleanly"}),
        encoding="utf-8",
    )
    stale_record = RunRecord(
        run_id="stale", loop_name=entry.name, started_at=1.0, deadline_at=2.0,
        stdout_log=str(log_path), stderr_log=str(tmp_path / "prior.stderr.log"),
        pid=999999, pgid=999999, lstart="L", cmdline="C",
    )
    runs_mod._write_record(stale_record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: False)
    runner_mod.start(queue=queue, tick_interval_s=10.0)
    resolved = runs_mod.load_run(entry.name, "stale")
    assert resolved is not None
    assert resolved.is_finished
    assert resolved.exit_code_unavailable is True
    assert resolved.outcome == RunOutcome.SUCCEEDED


def test_restart_adoption_holds_one_run_per_loop_while_still_live(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, tmp_path: Path
) -> None:
    entry = _entry()
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([entry], []))
    stale_record = RunRecord(
        run_id="stale", loop_name=entry.name, started_at=1.0, deadline_at=time.time() + 9999,
        stdout_log=str(tmp_path / "x.log"), stderr_log=str(tmp_path / "y.log"),
        pid=999999, pgid=999999, lstart="L", cmdline="C",
    )
    runs_mod._write_record(stale_record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))
    spawned: list[str] = []
    monkeypatch.setattr(runs_mod, "spawn_run", lambda e: spawned.append(e.name))
    runner_mod.start(queue=queue, tick_interval_s=0.02)
    time.sleep(0.2)
    runner_mod.stop()
    assert spawned == []
    resolved = runs_mod.load_run(entry.name, "stale")
    assert resolved is not None
    assert not resolved.is_finished


def test_restart_adoption_kills_and_resolves_once_deadline_already_passed(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, tmp_path: Path
) -> None:
    entry = _entry()
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([entry], []))
    log_path = tmp_path / "prior.stdout.log"
    log_path.write_text(json.dumps({"type": "result", "result": "done"}), encoding="utf-8")
    stale_record = RunRecord(
        run_id="stale", loop_name=entry.name, started_at=1.0, deadline_at=1.0,
        stdout_log=str(log_path), stderr_log=str(tmp_path / "prior.stderr.log"),
        pid=999999, pgid=999999, lstart="L", cmdline="C",
    )
    runs_mod._write_record(stale_record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))
    killed: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", lambda pgid, **_kw: killed.append(pgid))
    runner_mod.start(queue=queue, tick_interval_s=10.0)
    resolved = runs_mod.load_run(entry.name, "stale")
    assert killed == [999999]
    assert resolved is not None
    assert resolved.outcome == RunOutcome.TIMED_OUT
    assert resolved.exit_code_unavailable is True


def test_restart_adoption_unblocks_the_loop_once_resolved(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, tmp_path: Path
) -> None:
    entry = _entry(interval_seconds=1)
    monkeypatch.setattr(runner_mod, "load_registry", lambda: ([entry], []))
    log_path = tmp_path / "prior.stdout.log"
    log_path.write_text(json.dumps({"type": "result", "result": "done"}), encoding="utf-8")
    stale_record = RunRecord(
        run_id="stale", loop_name=entry.name, started_at=1.0, deadline_at=2.0,
        stdout_log=str(log_path), stderr_log=str(tmp_path / "prior.stderr.log"),
        pid=999999, pgid=999999, lstart="L", cmdline="C",
    )
    runs_mod._write_record(stale_record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: False)
    runner_mod.start(queue=queue, tick_interval_s=10.0)
    runner_mod.stop()
    ctx = status_mod.build_context(entry, queue=queue)
    assert ctx.running is False
    assert ctx.recent_outcomes == (RunOutcome.SUCCEEDED,)
