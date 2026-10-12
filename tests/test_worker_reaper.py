from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from alan import worker_reaper, worker_records
from alan.queue import Queue
from alan.slot_pool import Reservation


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


@dataclass
class _FakeJob:
    id: int


class _FakeQueue:
    def __init__(self, running_ids: list[int] | None = None) -> None:
        self._running_ids = running_ids or []

    def list_running(self) -> list[_FakeJob]:
        return [_FakeJob(id=i) for i in self._running_ids]


def _queue(running_ids: list[int] | None = None) -> Queue:
    return cast(Queue, _FakeQueue(running_ids))


def _write_record(session_id: str, **overrides: object) -> None:
    base: dict[str, object] = {
        "schema_version": worker_records.RECORD_VERSION,
        "job_id": 1,
        "task_id": "task-1",
        "workspace_id": "ws-1",
        "team": "implementation",
        "inv_id": "inv_001",
        "node_id": "implementer",
        "role": "implementer",
        "model": "sonnet",
        "engine": "default",
        "slot_path": "/fake/slot_0",
        "session_id": session_id,
        "output_path": "/fake/slot_0/stream.jsonl",
        "pid": None,
        "started_at": None,
        "lstart": None,
        "cmdline": None,
        "deadline_at": None,
        "idle_timeout_s": None,
        "max_turns": 150,
        "service_pid": 1,
        "service_started_at": "Thu Jan  1 00:00:00 1970",
        "host": worker_records.current_host(),
    }
    base.update(overrides)
    worker_records.write_initial(worker_records.WorkerRecord(**base))  # type: ignore[arg-type]


def test_reap_and_reserve_returns_empty_mapping_when_no_records() -> None:
    assert dict(worker_reaper.reap_and_reserve(_queue())) == {}


def test_reap_and_reserve_removes_unparseable_record(_state_root: Path) -> None:
    d = worker_records.records_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("not json", encoding="utf-8")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert not (d / "garbage.json").exists()


def test_reap_and_reserve_removes_dead_record() -> None:
    _write_record("sess-dead", pid=999999, lstart="x", cmdline="y")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert worker_records.load_all() == []


def test_reap_and_reserve_reserves_unknown_record_by_slot_path() -> None:
    _write_record("sess-unknown", slot_path="/fake/slot_7", pid=None)
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {Path("/fake/slot_7"): Reservation("sess-unknown", adoptable=False)}
    assert worker_records.load_all()[0][1] is not None


def test_reap_and_reserve_terminates_live_record_and_removes_it() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        _write_record(
            "sess-live", slot_path="/fake/slot_9", pid=proc.pid, lstart=lstart, cmdline=cmdline,
        )
        reserved = worker_reaper.reap_and_reserve(_queue())
        assert reserved == {}
        assert worker_records.load_all() == []
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_reap_and_reserve_ignores_current_run_records() -> None:
    this_pid, this_lstart = worker_records.this_service_identity()
    _write_record(
        "sess-same-run", pid=this_pid, service_pid=this_pid, service_started_at=this_lstart,
    )
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert worker_records.load_all() == []


def test_reap_and_reserve_multiple_records_mixed_verdicts() -> None:
    _write_record("sess-a", slot_path="/fake/slot_a", pid=None)
    _write_record("sess-b", pid=999999, lstart="x", cmdline="y")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {Path("/fake/slot_a"): Reservation("sess-a", adoptable=False)}
    remaining = {p.name for p, _r in worker_records.load_all()}
    assert remaining == {"sess-a.json"}


def test_reap_and_reserve_logs_loaded_reserved_dropped_and_running_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_record("sess-a", slot_path="/fake/slot_a", pid=None)
    with caplog.at_level("INFO", logger="alan.worker_reaper"):
        worker_reaper.reap_and_reserve(_queue(running_ids=[1, 2, 3]))
    message = next(r.message for r in caplog.records if "RUNNING in queue" in r.message)
    assert "1 record(s) loaded" in message
    assert "1 reservation(s) made" in message
    assert "0 dropped for empty slot_path" in message
    assert "3 job(s) RUNNING in queue" in message
    assert "cross-check only" not in message


def test_reap_and_reserve_reserves_fresh_slot_hold() -> None:
    worker_records.write_slot_hold("ws-held", "/fake/slot_3")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {Path("/fake/slot_3"): Reservation("hold:ws-held", adoptable=False)}


def test_reap_and_reserve_keeps_worker_record_reservation_over_hold_for_same_path() -> None:
    _write_record("sess-unknown", slot_path="/fake/slot_7", pid=None)
    worker_records.write_slot_hold("ws-held", "/fake/slot_7")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {Path("/fake/slot_7"): Reservation("sess-unknown", adoptable=False)}


def test_reap_and_reserve_drops_stale_slot_hold(_state_root: Path) -> None:
    worker_records.write_slot_hold("ws-stale", "/fake/slot_0")
    hold_path = worker_records.holds_dir() / "ws-stale.json"
    data = json.loads(hold_path.read_text(encoding="utf-8"))
    data["written_at"] = data["written_at"] - worker_records.HOLD_STALE_TIMEOUT_S - 1.0
    hold_path.write_text(json.dumps(data), encoding="utf-8")

    reserved = worker_reaper.reap_and_reserve(_queue())

    assert reserved == {}
    assert worker_records.load_all_slot_holds() == []


def test_reap_and_reserve_removes_unparseable_slot_hold(_state_root: Path) -> None:
    d = worker_records.holds_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("not json", encoding="utf-8")

    reserved = worker_reaper.reap_and_reserve(_queue())

    assert reserved == {}
    assert not (d / "garbage.json").exists()


def test_reap_and_reserve_removes_only_the_files_the_models_reject(_state_root: Path) -> None:
    _write_record("sess-good", slot_path="/fake/slot_good", pid=None)
    _write_record("sess-bool-pid", slot_path="/fake/slot_bad", pid=None)
    bool_pid_path = worker_records.records_dir() / "sess-bool-pid.json"
    bool_pid = json.loads(bool_pid_path.read_text(encoding="utf-8"))
    bool_pid["pid"] = True
    bool_pid_path.write_text(json.dumps(bool_pid), encoding="utf-8")
    worker_records.write_slot_hold("ws-good", "/fake/slot_hold_good")
    worker_records.write_slot_hold("ws-bad", "/fake/slot_hold_bad")
    bad_hold_path = worker_records.holds_dir() / "ws-bad.json"
    bad_hold = json.loads(bad_hold_path.read_text(encoding="utf-8"))
    bad_hold["slot_path"] = 12
    bad_hold_path.write_text(json.dumps(bad_hold), encoding="utf-8")

    reserved = worker_reaper.reap_and_reserve(_queue())

    assert reserved == {
        Path("/fake/slot_good"): Reservation("sess-good", adoptable=False),
        Path("/fake/slot_hold_good"): Reservation("hold:ws-good", adoptable=False),
    }
    assert {p.name for p, _r in worker_records.load_all()} == {"sess-good.json"}
    assert not bool_pid_path.exists()
    assert {p.name for p in worker_records.holds_dir().glob("*.json")} == {"ws-good.json"}


def test_reap_and_reserve_logs_warning_when_stale_hold_unlink_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    worker_records.write_slot_hold("ws-stuck", "/fake/slot_0")
    hold_path = worker_records.holds_dir() / "ws-stuck.json"
    data = json.loads(hold_path.read_text(encoding="utf-8"))
    data["written_at"] = data["written_at"] - worker_records.HOLD_STALE_TIMEOUT_S - 1.0
    hold_path.write_text(json.dumps(data), encoding="utf-8")

    def _boom(self: Path, missing_ok: bool = False) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", _boom)
    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert any("failed to remove slot hold" in r.message for r in caplog.records)


def test_reap_and_reserve_logs_slot_hold_count(caplog: pytest.LogCaptureFixture) -> None:
    worker_records.write_slot_hold("ws-held", "/fake/slot_3")
    with caplog.at_level("INFO", logger="alan.worker_reaper"):
        worker_reaper.reap_and_reserve(_queue())
    message = next(r.message for r in caplog.records if "RUNNING in queue" in r.message)
    assert "1 slot hold(s) reserved" in message


def test_reap_and_reserve_logs_running_count_as_zero_when_list_running_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _RaisingQueue:
        def list_running(self) -> list[_FakeJob]:
            raise RuntimeError("db locked")

    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        reserved = worker_reaper.reap_and_reserve(cast(Queue, _RaisingQueue()))
    assert reserved == {}
    assert any(
        "list_running() failed while assembling the boot log" in r.message
        for r in caplog.records
    )


def test_reap_and_reserve_drops_reservable_record_with_empty_slot_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_record("sess-empty-unknown", slot_path="", pid=None)
    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert worker_records.load_all()[0][1] is not None
    assert any("sess-empty-unknown" in r.message for r in caplog.records)


def test_reap_and_reserve_drops_detached_survivor_record_with_empty_slot_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        _write_record(
            "sess-empty-detached", slot_path="", pid=proc.pid, lstart=lstart,
            cmdline=cmdline, pgid=424242,
        )
        with caplog.at_level("WARNING", logger="alan.worker_reaper"):
            reserved = worker_reaper.reap_and_reserve(_queue())
        assert reserved == {}
        assert worker_records.load_all()[0][1] is not None
        assert any("sess-empty-detached" in r.message for r in caplog.records)
    finally:
        proc.kill()
        proc.wait()


def test_boot_clean_allowed_true_when_no_running_jobs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="alan.worker_reaper"):
        allowed = worker_reaper.boot_clean_allowed(_queue())
    assert allowed is True
    assert any("startup clean permitted" in r.message for r in caplog.records)


def test_boot_clean_allowed_false_when_jobs_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        allowed = worker_reaper.boot_clean_allowed(_queue(running_ids=[7, 9]))
    assert allowed is False
    assert any("7" in r.message and "9" in r.message for r in caplog.records)


def test_boot_clean_allowed_false_when_list_running_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _RaisingQueue:
        def list_running(self) -> list[_FakeJob]:
            raise RuntimeError("db locked")

    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        allowed = worker_reaper.boot_clean_allowed(cast(Queue, _RaisingQueue()))
    assert allowed is False
    assert any("refusing startup clean" in r.message for r in caplog.records)


def test_reap_and_reserve_logs_warning_when_unparseable_record_unlink_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, _state_root: Path
) -> None:
    d = worker_records.records_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("not json", encoding="utf-8")

    def _boom(self: Path, missing_ok: bool = False) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", _boom)
    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert any("failed to remove record" in r.message for r in caplog.records)


def test_reap_and_reserve_reserves_detached_survivor_never_terminates(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        _write_record(
            "sess-detached", slot_path="/fake/slot_5", pid=proc.pid, lstart=lstart,
            cmdline=cmdline, pgid=424242,
        )
        reserved = worker_reaper.reap_and_reserve(_queue())
        assert reserved == {Path("/fake/slot_5"): Reservation("sess-detached", adoptable=True)}
        assert worker_records.load_all()[0][1] is not None
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_reap_and_reserve_marks_detached_survivor_adoptable() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        _write_record(
            "sess-detached-2", slot_path="/fake/slot_6", pid=proc.pid, lstart=lstart,
            cmdline=cmdline, pgid=555555,
        )
        reserved = worker_reaper.reap_and_reserve(_queue())
        assert reserved[Path("/fake/slot_6")].adoptable is True
    finally:
        proc.kill()
        proc.wait()


def test_reap_and_reserve_marks_never_stamped_record_not_adoptable() -> None:
    _write_record("sess-never-stamped", slot_path="/fake/slot_8", pid=None, pgid=None)
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved[Path("/fake/slot_8")].adoptable is False


def test_reap_and_reserve_marks_unknown_record_with_pgid_adoptable() -> None:
    _write_record("sess-unknown-pgid", slot_path="/fake/slot_9", pid=None, pgid=777777)
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved[Path("/fake/slot_9")].adoptable is True


def test_reap_and_reserve_marks_hold_not_adoptable() -> None:
    worker_records.write_slot_hold("ws-marked", "/fake/slot_10")
    reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved[Path("/fake/slot_10")].adoptable is False


def test_never_stamped_record_never_takes_the_detached_survivor_adoptable_value() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        identity = worker_records.ps_identity(proc.pid)
        assert identity is not None
        lstart, cmdline = identity
        _write_record(
            "sess-survivor", slot_path="/fake/slot_aa", pid=proc.pid, lstart=lstart,
            cmdline=cmdline, pgid=333333,
        )
        _write_record("sess-never-stamped-2", slot_path="/fake/slot_bb", pid=None, pgid=None)
        reserved = worker_reaper.reap_and_reserve(_queue())
        assert reserved[Path("/fake/slot_aa")].adoptable is True
        assert reserved[Path("/fake/slot_bb")].adoptable is False
    finally:
        proc.kill()
        proc.wait()


def test_terminate_all_detached_terminates_pgid_records_only(
    monkeypatch: pytest.MonkeyPatch, _state_root: Path
) -> None:
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    _write_record("sess-detached", pid=None, pgid=424242)
    _write_record("sess-plain", pid=None, pgid=None)
    worker_reaper.terminate_all_detached()
    assert terminated == [424242]
    remaining = {p.name for p, _r in worker_records.load_all()}
    assert remaining == {"sess-plain.json"}


def test_terminate_all_detached_guards_against_own_process_group(
    monkeypatch: pytest.MonkeyPatch, _state_root: Path
) -> None:
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)

    def _raise_guard(pid: int) -> None:
        raise worker_records.GroupSignalGuardError("nope")

    monkeypatch.setattr(worker_records, "guard_no_group_signal", _raise_guard)
    _write_record("sess-own-group", pid=42, pgid=424242)
    worker_reaper.terminate_all_detached()
    assert terminated == []
    assert worker_records.load_all()[0][1] is not None


def test_terminate_all_detached_terminates_when_guard_passes_and_pid_present(
    monkeypatch: pytest.MonkeyPatch, _state_root: Path
) -> None:
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "guard_no_group_signal", lambda pid: None)
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    _write_record("sess-guarded", pid=42, pgid=424242)
    worker_reaper.terminate_all_detached()
    assert terminated == [424242]
    assert worker_records.load_all() == []


def test_terminate_all_detached_skips_foreign_host_record(
    monkeypatch: pytest.MonkeyPatch, _state_root: Path
) -> None:
    terminated: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", terminated.append)
    _write_record("sess-foreign", pid=None, pgid=424242, host="some-other-host")
    worker_reaper.terminate_all_detached()
    assert terminated == []
    assert worker_records.load_all()[0][1] is not None


def test_terminate_all_detached_skips_unparseable_records(_state_root: Path) -> None:
    d = worker_records.records_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("not json", encoding="utf-8")
    worker_reaper.terminate_all_detached()
    assert (d / "garbage.json").exists()


def test_reap_and_reserve_logs_warning_when_dead_record_removal_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    _write_record("sess-dead-noremove", pid=999999, lstart="x", cmdline="y")

    def _boom(session_id: str) -> None:
        raise OSError("locked")

    monkeypatch.setattr(worker_records, "remove_record", _boom)
    with caplog.at_level("WARNING", logger="alan.worker_reaper"):
        reserved = worker_reaper.reap_and_reserve(_queue())
    assert reserved == {}
    assert any("failed to remove record" in r.message for r in caplog.records)
