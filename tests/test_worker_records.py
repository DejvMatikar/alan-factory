from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from alan import worker_records
from alan.worker_records import ProbeVerdict, probe


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def _make_record(**overrides: object) -> worker_records.WorkerRecord:
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
        "session_id": "sess-1",
        "output_path": "/fake/slot_0/stream.jsonl",
        "pid": None,
        "started_at": None,
        "lstart": None,
        "cmdline": None,
        "deadline_at": None,
        "idle_timeout_s": None,
        "max_turns": 150,
        "service_pid": 999999,
        "service_started_at": "Thu Jan  1 00:00:00 1970",
        "host": worker_records.current_host(),
    }
    base.update(overrides)
    return worker_records.WorkerRecord(**base)  # type: ignore[arg-type]


def test_records_dir_under_state_root(_state_root: Path) -> None:
    assert worker_records.records_dir() == _state_root / "worker_records"


def test_write_initial_creates_dir_and_file(_state_root: Path) -> None:
    record = _make_record(session_id="sess-write")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-write.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-write"
    assert data["pid"] is None


def test_write_initial_lets_oserror_propagate(
    monkeypatch: pytest.MonkeyPatch, _state_root: Path
) -> None:
    record = _make_record(session_id="sess-fail")

    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        worker_records.write_initial(record)


def test_stamp_started_updates_identity_fields(_state_root: Path) -> None:
    record = _make_record(session_id="sess-stamp")
    worker_records.write_initial(record)
    worker_records.stamp_started(
        "sess-stamp", pid=4242, started_at=123.5, lstart="Thu Jan  1 00:00:00 1970",
        cmdline="claude -p foo",
    )
    path, loaded = worker_records.load_all()[0]
    assert loaded is not None
    assert loaded.pid == 4242
    assert loaded.started_at == 123.5
    assert loaded.lstart == "Thu Jan  1 00:00:00 1970"
    assert loaded.cmdline == "claude -p foo"
    del path


def test_stamp_started_raises_when_record_missing(_state_root: Path) -> None:
    with pytest.raises(OSError):
        worker_records.stamp_started(
            "does-not-exist", pid=1, started_at=1.0, lstart=None, cmdline=None
        )


def test_remove_record_is_idempotent(_state_root: Path) -> None:
    record = _make_record(session_id="sess-remove")
    worker_records.write_initial(record)
    worker_records.remove_record("sess-remove")
    assert worker_records.load_all() == []
    worker_records.remove_record("sess-remove")


def test_holds_dir_under_state_root(_state_root: Path) -> None:
    assert worker_records.holds_dir() == _state_root / "slot_holds"


def test_write_slot_hold_creates_file_load_all_slot_holds_parses_it(
    _state_root: Path,
) -> None:
    worker_records.write_slot_hold("ws-1", "/fake/slot_0")
    [(path, hold)] = worker_records.load_all_slot_holds()
    assert path.name == "ws-1.json"
    assert hold is not None
    assert hold.workspace_id == "ws-1"
    assert hold.slot_path == "/fake/slot_0"
    assert isinstance(hold.written_at, float)


def test_write_slot_hold_overwrites_existing_for_same_workspace(
    _state_root: Path,
) -> None:
    worker_records.write_slot_hold("ws-1", "/fake/slot_0")
    worker_records.write_slot_hold("ws-1", "/fake/slot_1")
    holds = worker_records.load_all_slot_holds()
    assert len(holds) == 1
    _path, hold = holds[0]
    assert hold is not None
    assert hold.slot_path == "/fake/slot_1"


def test_load_all_slot_holds_empty_when_dir_missing(_state_root: Path) -> None:
    assert worker_records.load_all_slot_holds() == []


def test_load_all_slot_holds_classifies_invalid_json_as_none(_state_root: Path) -> None:
    d = worker_records.holds_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("{not valid json", encoding="utf-8")
    [(_path, hold)] = worker_records.load_all_slot_holds()
    assert hold is None


def test_load_all_slot_holds_classifies_non_object_json_as_none(_state_root: Path) -> None:
    d = worker_records.holds_dir()
    d.mkdir(parents=True)
    (d / "array.json").write_text("[1, 2, 3]", encoding="utf-8")
    [(_path, hold)] = worker_records.load_all_slot_holds()
    assert hold is None


def test_load_all_slot_holds_classifies_missing_field_as_none(_state_root: Path) -> None:
    worker_records.write_slot_hold("ws-missing", "/fake/slot_0")
    path = worker_records.holds_dir() / "ws-missing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["slot_path"]
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, hold)] = worker_records.load_all_slot_holds()
    assert hold is None


def test_load_all_slot_holds_classifies_wrong_type_written_at_as_none(
    _state_root: Path,
) -> None:
    worker_records.write_slot_hold("ws-wrongtype", "/fake/slot_0")
    path = worker_records.holds_dir() / "ws-wrongtype.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["written_at"] = "not-a-float"
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, hold)] = worker_records.load_all_slot_holds()
    assert hold is None


def test_is_slot_hold_fresh_true_within_timeout() -> None:
    hold = worker_records.SlotHold(workspace_id="ws-1", slot_path="/fake/slot_0", written_at=100.0)
    assert worker_records.is_slot_hold_fresh(hold, now=100.0 + worker_records.HOLD_STALE_TIMEOUT_S)


def test_is_slot_hold_fresh_false_after_timeout() -> None:
    hold = worker_records.SlotHold(workspace_id="ws-1", slot_path="/fake/slot_0", written_at=100.0)
    now = 100.0 + worker_records.HOLD_STALE_TIMEOUT_S + 1.0
    assert not worker_records.is_slot_hold_fresh(hold, now=now)


def test_full_record_lifecycle_write_stamp_remove(_state_root: Path) -> None:
    record = _make_record(session_id="sess-lifecycle")
    worker_records.write_initial(record)
    _path, loaded = worker_records.load_all()[0]
    assert loaded is not None and loaded.pid is None

    worker_records.stamp_started(
        "sess-lifecycle", pid=os.getpid(), started_at=time.time(), lstart="x", cmdline="y",
    )
    _path, loaded = worker_records.load_all()[0]
    assert loaded is not None and loaded.pid == os.getpid()

    worker_records.remove_record("sess-lifecycle")
    assert worker_records.load_all() == []


def test_load_all_empty_when_dir_missing(_state_root: Path) -> None:
    assert worker_records.load_all() == []


def test_load_all_parses_valid_record(_state_root: Path) -> None:
    record = _make_record(session_id="sess-valid")
    worker_records.write_initial(record)
    [(path, loaded)] = worker_records.load_all()
    assert loaded == record
    assert path.name == "sess-valid.json"


def test_load_all_classifies_invalid_json_as_none(_state_root: Path) -> None:
    d = worker_records.records_dir()
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("{not valid json", encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_non_object_json_as_none(_state_root: Path) -> None:
    d = worker_records.records_dir()
    d.mkdir(parents=True)
    (d / "array.json").write_text("[1, 2, 3]", encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_unknown_schema_version_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-future")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-future.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_missing_required_field_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-missing")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-missing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["team"]
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_wrong_type_field_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-wrongtype")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-wrongtype.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pid"] = "not-an-int"
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_bool_where_int_expected_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-boolpid")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-boolpid.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pid"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_non_string_optional_field_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-badtaskid")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-badtaskid.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["task_id"] = 12345
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_non_numeric_optional_float_field_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-baddeadline")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-baddeadline.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["deadline_at"] = "not-a-number"
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_wrong_type_required_str_field_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-badteam")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-badteam.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["team"] = 123
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_bool_service_pid_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-boolservicepid")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-boolservicepid.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["service_pid"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_load_all_classifies_non_string_service_started_at_as_none(_state_root: Path) -> None:
    record = _make_record(session_id="sess-badservicestarted")
    worker_records.write_initial(record)
    path = worker_records.records_dir() / "sess-badservicestarted.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["service_started_at"] = 12345
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    assert loaded is None


def test_is_alive_true_for_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", _boom)
    assert worker_records._is_alive(1) is True  # noqa: SLF001


def test_is_alive_false_for_other_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(pid: int, sig: int) -> None:
        raise OSError("unexpected failure")

    monkeypatch.setattr(os, "kill", _boom)
    assert worker_records._is_alive(12345) is False  # noqa: SLF001


_THIS_SERVICE = (424242, "Thu Jan  1 00:00:00 1970")


def test_probe_other_machine_record_is_dead() -> None:
    record = _make_record(host="some-other-host.local")
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.DEAD


def test_probe_same_run_record_is_dead_even_with_live_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record(
        service_pid=_THIS_SERVICE[0], service_started_at=_THIS_SERVICE[1],
        pid=os.getpid(),
    )
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("x", "y"))
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.DEAD


def test_probe_null_pid_is_unknown() -> None:
    record = _make_record(pid=None)
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.UNKNOWN


def test_probe_dead_pid_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=999999)
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: False)
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.DEAD


def test_probe_ps_unreadable_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=12345, lstart="a", cmdline="b")
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.UNKNOWN


def test_probe_recycled_pid_mismatched_lstart_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=12345, lstart="old-start-time", cmdline="claude -p x")
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(
        worker_records, "ps_identity", lambda pid: ("new-start-time", "claude -p x")
    )
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.DEAD


def test_probe_mismatched_cmdline_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=12345, lstart="same-start-time", cmdline="claude -p original")
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(
        worker_records, "ps_identity", lambda pid: ("same-start-time", "some-other-process")
    )
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.DEAD


def test_probe_matching_identity_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=12345, lstart="same-start-time", cmdline="claude -p x")
    monkeypatch.setattr(worker_records, "_is_alive", lambda pid: True)
    monkeypatch.setattr(
        worker_records, "ps_identity", lambda pid: ("same-start-time", "claude -p x")
    )
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.LIVE


def test_probe_identity_validated_against_the_real_test_process() -> None:
    identity = worker_records.ps_identity(os.getpid())
    assert identity is not None, "ps must be able to read this test process"
    lstart, cmdline = identity
    record = _make_record(pid=os.getpid(), lstart=lstart, cmdline=cmdline)
    assert probe(record, this_service=_THIS_SERVICE) is ProbeVerdict.LIVE


@pytest.mark.parametrize(
    "verdict", [ProbeVerdict.LIVE, ProbeVerdict.UNKNOWN]
)
def test_is_reservable_true_for_live_and_unknown(verdict: worker_records.ProbeVerdict) -> None:
    assert worker_records.is_reservable(verdict) is True


def test_is_reservable_false_for_dead() -> None:
    assert worker_records.is_reservable(ProbeVerdict.DEAD) is False


def test_is_terminable_true_only_for_live() -> None:
    assert worker_records.is_terminable(ProbeVerdict.LIVE) is True
    assert worker_records.is_terminable(ProbeVerdict.UNKNOWN) is False
    assert worker_records.is_terminable(ProbeVerdict.DEAD) is False


def test_terminate_pid_sends_sigterm_then_sigkill_when_still_alive() -> None:
    calls: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    worker_records.terminate_pid(4242, kill=fake_kill, sleep=sleeps.append)
    assert calls == [(4242, signal.SIGTERM), (4242, 0), (4242, signal.SIGKILL)]
    assert sleeps == [worker_records.SIGTERM_GRACE_S]


def test_terminate_pid_skips_sigkill_when_process_already_exited() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    worker_records.terminate_pid(4242, kill=fake_kill, sleep=lambda _s: None)
    assert calls == [(4242, signal.SIGTERM), (4242, 0)]


def test_terminate_pid_returns_when_initial_sigterm_fails() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        raise ProcessLookupError

    def fail_sleep(_s: float) -> None:
        pytest.fail("must not sleep when the initial SIGTERM already failed")

    worker_records.terminate_pid(4242, kill=fake_kill, sleep=fail_sleep)
    assert calls == [(4242, signal.SIGTERM)]


def test_terminate_pid_swallows_oserror_on_final_sigkill() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == signal.SIGKILL:
            raise OSError("gone")

    worker_records.terminate_pid(4242, kill=fake_kill, sleep=lambda _s: None)
    assert calls == [(4242, signal.SIGTERM), (4242, 0), (4242, signal.SIGKILL)]


def test_terminate_pid_real_process() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        worker_records.terminate_pid(proc.pid, sleep=lambda _s: None)
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_guard_no_group_signal_raises_for_own_process_group() -> None:
    with pytest.raises(worker_records.GroupSignalGuardError):
        worker_records.guard_no_group_signal(os.getpid())


def test_guard_no_group_signal_falls_through_when_pid_vanished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", _boom)
    worker_records.guard_no_group_signal(999999)


def test_guard_no_group_signal_noop_for_different_group() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True, start_new_session=True,
    )
    try:
        worker_records.guard_no_group_signal(proc.pid)
    finally:
        proc.kill()
        proc.wait()


def test_terminate_group_sends_sigterm_then_sigkill_when_still_alive() -> None:
    calls: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def fake_kill(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))

    worker_records.terminate_group(4242, kill=fake_kill, sleep=sleeps.append)
    assert calls == [(4242, signal.SIGTERM), (4242, 0), (4242, signal.SIGKILL)]
    assert sleeps == [worker_records.SIGTERM_GRACE_S]


def test_terminate_group_skips_sigkill_when_group_already_gone() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        if sig == 0:
            raise ProcessLookupError

    worker_records.terminate_group(4242, kill=fake_kill, sleep=lambda _s: None)
    assert calls == [(4242, signal.SIGTERM), (4242, 0)]


def test_terminate_group_returns_when_initial_sigterm_fails() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        raise ProcessLookupError

    def fail_sleep(_s: float) -> None:
        pytest.fail("must not sleep when the initial SIGTERM already failed")

    worker_records.terminate_group(4242, kill=fake_kill, sleep=fail_sleep)
    assert calls == [(4242, signal.SIGTERM)]


def test_terminate_group_swallows_oserror_on_final_sigkill() -> None:
    calls: list[tuple[int, int]] = []

    def fake_kill(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        if sig == signal.SIGKILL:
            raise OSError("gone")

    worker_records.terminate_group(4242, kill=fake_kill, sleep=lambda _s: None)
    assert calls == [(4242, signal.SIGTERM), (4242, 0), (4242, signal.SIGKILL)]


def test_terminate_group_real_process_group() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True, start_new_session=True,
    )
    try:
        worker_records.terminate_group(os.getpgid(proc.pid), sleep=lambda _s: None)
        proc.wait(timeout=5.0)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_is_pid_alive_or_ambiguous_false_for_process_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _boom)
    assert worker_records.is_pid_alive_or_ambiguous(999999) is False


def test_is_pid_alive_or_ambiguous_true_for_other_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", _boom)
    assert worker_records.is_pid_alive_or_ambiguous(1) is True


def test_is_pid_alive_or_ambiguous_true_for_live_process() -> None:
    assert worker_records.is_pid_alive_or_ambiguous(os.getpid()) is True


def test_reservation_is_live_true_for_null_pid() -> None:
    record = _make_record(pid=None)
    assert worker_records.reservation_is_live(record) is True


def test_reservation_is_live_false_for_confirmed_dead_pid() -> None:
    record = _make_record(pid=999999)
    assert worker_records.reservation_is_live(record) is False


def test_reservation_is_live_true_when_ps_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _make_record(pid=12345)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    assert worker_records.reservation_is_live(record) is True


def test_reservation_is_live_true_when_identity_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record(pid=12345, lstart="a", cmdline="b")
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("a", "b"))
    assert worker_records.reservation_is_live(record) is True


def test_reservation_is_live_false_when_identity_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _make_record(pid=12345, lstart="a", cmdline="b")
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("a", "different"))
    assert worker_records.reservation_is_live(record) is False


def test_this_service_identity_matches_own_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_records, "_this_service_identity_cache", None)
    pid, _lstart = worker_records.this_service_identity()
    assert pid == os.getpid()


def test_this_service_identity_is_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_records, "_this_service_identity_cache", None)
    first = worker_records.this_service_identity()
    calls: list[int] = []
    orig = worker_records.ps_identity

    def spy(pid: int) -> tuple[str, str] | None:
        calls.append(pid)
        return orig(pid)

    monkeypatch.setattr(worker_records, "ps_identity", spy)
    second = worker_records.this_service_identity()
    assert first == second
    assert calls == []


def test_this_service_identity_falls_back_to_empty_lstart_when_ps_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_records, "_this_service_identity_cache", None)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    pid, lstart = worker_records.this_service_identity()
    assert pid == os.getpid()
    assert lstart == ""


def test_current_host_matches_socket_gethostname() -> None:
    import socket

    assert worker_records.current_host() == socket.gethostname()


def test_ps_identity_returns_none_for_nonexistent_pid() -> None:
    assert worker_records.ps_identity(999999) is None


def test_ps_identity_returns_none_when_line_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 0
        stdout = "short\n"

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _FakeResult()
    )
    assert worker_records.ps_identity(12345) is None


def test_ps_identity_returns_none_when_command_is_all_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResult:
        returncode = 0
        stdout = ("x" * worker_records._LSTART_WIDTH) + "     \n"  # noqa: SLF001

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: _FakeResult())
    assert worker_records.ps_identity(12345) is None


def test_ps_identity_returns_none_on_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_kw: _FakeResult()
    )
    assert worker_records.ps_identity(12345) is None


def test_ps_identity_returns_none_when_command_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _boom)
    assert worker_records.ps_identity(12345) is None


def test_ps_identity_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_kw: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ps", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert worker_records.ps_identity(12345) is None


_RECORD_KEYS_IN_WRITE_ORDER = [
    "schema_version",
    "job_id",
    "task_id",
    "workspace_id",
    "team",
    "inv_id",
    "node_id",
    "role",
    "model",
    "engine",
    "slot_path",
    "session_id",
    "output_path",
    "pid",
    "started_at",
    "lstart",
    "cmdline",
    "deadline_at",
    "idle_timeout_s",
    "max_turns",
    "service_pid",
    "service_started_at",
    "host",
    "pgid",
    "journal_offset",
    "err_offset",
]
_OPTIONAL_RECORD_KEYS = [
    "job_id",
    "task_id",
    "pid",
    "started_at",
    "lstart",
    "cmdline",
    "deadline_at",
    "idle_timeout_s",
    "max_turns",
    "pgid",
    "journal_offset",
    "err_offset",
]


def _stored_record(session_id: str) -> dict[str, Any]:
    worker_records.write_initial(_make_record(session_id=session_id))
    path = worker_records.records_dir() / f"{session_id}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _load_stored(session_id: str, data: dict[str, Any]) -> worker_records.WorkerRecord | None:
    path = worker_records.records_dir() / f"{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    [(_path, loaded)] = worker_records.load_all()
    return loaded


def _store_hold(workspace_id: str, data: dict[str, Any]) -> worker_records.SlotHold | None:
    directory = worker_records.holds_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{workspace_id}.json").write_text(json.dumps(data), encoding="utf-8")
    [(_path, hold)] = worker_records.load_all_slot_holds()
    return hold


def test_write_initial_writes_every_key_in_the_established_order() -> None:
    worker_records.write_initial(_make_record(session_id="sess-order"))
    path = worker_records.records_dir() / "sess-order.json"
    assert list(json.loads(path.read_text(encoding="utf-8"))) == _RECORD_KEYS_IN_WRITE_ORDER


def test_write_initial_writes_unset_optional_fields_as_explicit_nulls() -> None:
    worker_records.write_initial(_make_record(session_id="sess-nulls"))
    path = worker_records.records_dir() / "sess-nulls.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pgid"] is None
    assert data["journal_offset"] is None
    assert data["err_offset"] is None


@pytest.mark.parametrize("key", _OPTIONAL_RECORD_KEYS)
def test_load_all_loads_record_missing_optional_key_with_none(key: str) -> None:
    data = _stored_record("sess-legacy")
    del data[key]
    loaded = _load_stored("sess-legacy", data)
    assert loaded is not None
    assert getattr(loaded, key) is None


def test_load_all_ignores_unknown_keys() -> None:
    data = _stored_record("sess-future-key")
    data["added_by_a_later_release"] = {"nested": [1, 2]}
    loaded = _load_stored("sess-future-key", data)
    assert loaded == _make_record(session_id="sess-future-key")


def test_load_all_accepts_true_schema_version_as_record_version() -> None:
    data = _stored_record("sess-true-version")
    data["schema_version"] = True
    loaded = _load_stored("sess-true-version", data)
    assert loaded is not None
    assert loaded.schema_version == worker_records.RECORD_VERSION
    assert type(loaded.schema_version) is int


def test_load_all_accepts_float_schema_version_equal_to_record_version() -> None:
    data = _stored_record("sess-float-version")
    data["schema_version"] = 1.0
    loaded = _load_stored("sess-float-version", data)
    assert loaded is not None
    assert type(loaded.schema_version) is int


@pytest.mark.parametrize("version", ["1", None, 0, 2, [1], {"v": 1}])
def test_load_all_classifies_other_schema_versions_as_none(version: object) -> None:
    data = _stored_record("sess-odd-version")
    data["schema_version"] = version
    assert _load_stored("sess-odd-version", data) is None


def test_load_all_classifies_missing_schema_version_as_none() -> None:
    data = _stored_record("sess-no-version")
    del data["schema_version"]
    assert _load_stored("sess-no-version", data) is None


@pytest.mark.parametrize(
    "key", ["job_id", "max_turns", "pgid", "journal_offset", "err_offset"]
)
def test_load_all_classifies_bool_in_optional_int_field_as_none(key: str) -> None:
    data = _stored_record("sess-bool-int")
    data[key] = True
    assert _load_stored("sess-bool-int", data) is None


@pytest.mark.parametrize("key", ["started_at", "deadline_at", "idle_timeout_s"])
def test_load_all_classifies_bool_in_optional_float_field_as_none(key: str) -> None:
    data = _stored_record("sess-bool-float")
    data[key] = False
    assert _load_stored("sess-bool-float", data) is None


@pytest.mark.parametrize("key", ["pid", "job_id", "max_turns", "pgid", "journal_offset"])
def test_load_all_classifies_fractional_float_in_optional_int_field_as_none(key: str) -> None:
    data = _stored_record("sess-float-int")
    data[key] = 5.5
    assert _load_stored("sess-float-int", data) is None


@pytest.mark.parametrize("key", ["started_at", "deadline_at", "idle_timeout_s"])
def test_load_all_loads_integer_in_optional_float_field_as_float(key: str) -> None:
    data = _stored_record("sess-int-float")
    data[key] = 7
    loaded = _load_stored("sess-int-float", data)
    assert loaded is not None
    assert getattr(loaded, key) == 7.0
    assert isinstance(getattr(loaded, key), float)


@pytest.mark.parametrize("key", ["team", "workspace_id", "slot_path", "host", "output_path"])
def test_load_all_classifies_null_required_str_field_as_none(key: str) -> None:
    data = _stored_record("sess-null-str")
    data[key] = None
    assert _load_stored("sess-null-str", data) is None


@pytest.mark.parametrize("key", ["service_pid", "service_started_at"])
def test_load_all_classifies_null_service_identity_as_none(key: str) -> None:
    data = _stored_record("sess-null-service")
    data[key] = None
    assert _load_stored("sess-null-service", data) is None


def test_load_all_classifies_missing_service_pid_as_none() -> None:
    data = _stored_record("sess-no-service-pid")
    del data["service_pid"]
    assert _load_stored("sess-no-service-pid", data) is None


def test_load_all_classifies_numeric_lstart_as_none() -> None:
    data = _stored_record("sess-num-lstart")
    data["lstart"] = 5
    assert _load_stored("sess-num-lstart", data) is None


def test_load_all_round_trips_a_stamped_detached_record() -> None:
    worker_records.write_initial(_make_record(session_id="sess-detached"))
    worker_records.stamp_started(
        "sess-detached", pid=4242, started_at=12.5, lstart="lstart", cmdline="cmd"
    )
    worker_records.stamp_detached("sess-detached", pgid=4242, journal_offset=10, err_offset=20)
    [(_path, loaded)] = worker_records.load_all()
    assert loaded == _make_record(
        session_id="sess-detached", pid=4242, started_at=12.5, lstart="lstart",
        cmdline="cmd", pgid=4242, journal_offset=10, err_offset=20,
    )


def test_stamp_started_keeps_unknown_keys() -> None:
    data = _stored_record("sess-keep-started")
    data["added_by_a_later_release"] = {"nested": [1, 2]}
    path = worker_records.records_dir() / "sess-keep-started.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    worker_records.stamp_started(
        "sess-keep-started", pid=7, started_at=1.5, lstart="l", cmdline="c"
    )
    stamped = json.loads(path.read_text(encoding="utf-8"))
    assert stamped["added_by_a_later_release"] == {"nested": [1, 2]}
    assert stamped["pid"] == 7


def test_stamp_detached_keeps_unknown_keys_and_key_order() -> None:
    data = _stored_record("sess-keep-detached")
    data["added_by_a_later_release"] = "kept"
    path = worker_records.records_dir() / "sess-keep-detached.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    worker_records.stamp_detached("sess-keep-detached", pgid=9, journal_offset=1, err_offset=2)
    stamped = json.loads(path.read_text(encoding="utf-8"))
    assert stamped["added_by_a_later_release"] == "kept"
    assert list(stamped) == [*_RECORD_KEYS_IN_WRITE_ORDER, "added_by_a_later_release"]
    assert (stamped["pgid"], stamped["journal_offset"], stamped["err_offset"]) == (9, 1, 2)


def test_slot_hold_written_keys_keep_established_order() -> None:
    worker_records.write_slot_hold("ws-order", "/fake/slot_0")
    path = worker_records.holds_dir() / "ws-order.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == ["workspace_id", "slot_path", "written_at"]


def test_load_all_slot_holds_loads_integer_written_at_as_float() -> None:
    hold = _store_hold("ws-int", {"workspace_id": "ws-int", "slot_path": "/s", "written_at": 5})
    assert hold is not None
    assert hold.written_at == 5.0
    assert isinstance(hold.written_at, float)


def test_load_all_slot_holds_loads_numeric_string_written_at() -> None:
    hold = _store_hold(
        "ws-numstr", {"workspace_id": "ws-numstr", "slot_path": "/s", "written_at": "123.5"}
    )
    assert hold is not None
    assert hold.written_at == 123.5


def test_load_all_slot_holds_classifies_non_string_slot_path_as_none() -> None:
    hold = _store_hold(
        "ws-badslot", {"workspace_id": "ws-badslot", "slot_path": 12, "written_at": 1.0}
    )
    assert hold is None


def test_load_all_slot_holds_classifies_non_string_workspace_id_as_none() -> None:
    hold = _store_hold("ws-badid", {"workspace_id": 7, "slot_path": "/s", "written_at": 1.0})
    assert hold is None


def test_load_all_slot_holds_classifies_null_written_at_as_none() -> None:
    hold = _store_hold(
        "ws-nullat", {"workspace_id": "ws-nullat", "slot_path": "/s", "written_at": None}
    )
    assert hold is None


def test_load_all_slot_holds_ignores_unknown_keys() -> None:
    hold = _store_hold(
        "ws-extra",
        {"workspace_id": "ws-extra", "slot_path": "/s", "written_at": 2.0, "later": True},
    )
    assert hold == worker_records.SlotHold(
        workspace_id="ws-extra", slot_path="/s", written_at=2.0
    )
