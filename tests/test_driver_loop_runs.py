from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from alan import driver_loop_runs as runs_mod
from alan import worker_records
from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome, RunRecord


@pytest.fixture(autouse=True)
def _redirect_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "command": "/alan-issue-driver",
        "cwd": "/opt/checkouts/alan-issue-driver",
        "self_paced": False,
        "interval_seconds": 1800,
        "timeout_seconds": 3600,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


def _record(**overrides: object) -> RunRecord:
    fields: dict[str, object] = {
        "run_id": "1",
        "loop_name": "alan-issue-driver",
        "started_at": 1000.0,
        "deadline_at": 4600.0,
        "stdout_log": "/opt/checkouts/x.stdout.log",
        "stderr_log": "/opt/checkouts/x.stderr.log",
    }
    fields.update(overrides)
    return RunRecord(**fields)  # type: ignore[arg-type]


def test_new_run_id_is_unique() -> None:
    assert runs_mod.new_run_id() != runs_mod.new_run_id()


def test_write_and_load_round_trip() -> None:
    record = _record(pid=123, pgid=123, lstart="foo", cmdline="bar")
    runs_mod._write_record(record)
    loaded = runs_mod.load_run("alan-issue-driver", "1")
    assert loaded == record


def test_load_run_missing_returns_none() -> None:
    assert runs_mod.load_run("nope", "1") is None


def test_load_run_corrupt_json_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    path = runs_mod._run_path("loop", "1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert runs_mod.load_run("loop", "1") is None


def test_load_run_non_dict_json_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    path = runs_mod._run_path("loop", "1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert runs_mod.load_run("loop", "1") is None


def test_load_run_missing_required_field_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    path = runs_mod._run_path("loop", "1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"loop_name": "loop"}), encoding="utf-8")
    assert runs_mod.load_run("loop", "1") is None


def test_list_runs_sorted_by_run_id() -> None:
    runs_mod._write_record(_record(run_id="2"))
    runs_mod._write_record(_record(run_id="1"))
    result = runs_mod.list_runs("alan-issue-driver")
    assert [r.run_id for r in result] == ["1", "2"]


def test_list_runs_missing_dir_is_empty() -> None:
    assert runs_mod.list_runs("nothing-here") == []


def test_latest_run_returns_last() -> None:
    runs_mod._write_record(_record(run_id="1"))
    runs_mod._write_record(_record(run_id="2"))
    result = runs_mod.latest_run("alan-issue-driver")
    assert result is not None
    assert result.run_id == "2"


def test_latest_run_none_when_empty() -> None:
    assert runs_mod.latest_run("empty-loop") is None


def test_prune_old_runs_keeps_only_last_n(tmp_path: Path) -> None:
    for i in range(25):
        stdout = tmp_path / f"{i:03d}.stdout.log"
        stderr = tmp_path / f"{i:03d}.stderr.log"
        stdout.write_text("x", encoding="utf-8")
        stderr.write_text("x", encoding="utf-8")
        runs_mod._write_record(
            _record(run_id=f"{i:03d}", stdout_log=str(stdout), stderr_log=str(stderr))
        )
    runs_mod.prune_old_runs("alan-issue-driver", keep=20)
    remaining = runs_mod.list_runs("alan-issue-driver")
    assert len(remaining) == 20
    assert remaining[0].run_id == "005"
    assert not (tmp_path / "000.stdout.log").exists()
    assert (tmp_path / "005.stdout.log").exists()


def test_prune_old_runs_noop_when_under_cap() -> None:
    runs_mod._write_record(_record(run_id="1"))
    runs_mod.prune_old_runs("alan-issue-driver", keep=20)
    assert len(runs_mod.list_runs("alan-issue-driver")) == 1


@pytest.mark.parametrize(
    ("timed_out", "succeeded", "usage_limit", "denials", "expected"),
    [
        (False, None, False, 0, RunOutcome.UNKNOWN_AFTER_RESTART),
        (True, True, False, 0, RunOutcome.TIMED_OUT),
        (False, True, True, 0, RunOutcome.HELD_USAGE_PAUSE),
        (False, False, False, 0, RunOutcome.FAILED),
        (False, True, False, 3, RunOutcome.RAN_WITH_DENIALS),
        (False, True, False, 0, RunOutcome.SUCCEEDED),
    ],
)
def test_derive_outcome_precedence(
    timed_out: bool,
    succeeded: bool | None,
    usage_limit: bool,
    denials: int,
    expected: RunOutcome,
) -> None:
    outcome = runs_mod.derive_outcome(
        timed_out=timed_out,
        succeeded=succeeded,
        usage_limit_detected=usage_limit,
        denial_count=denials,
    )
    assert outcome == expected


def _jsonl(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(e) for e in events)


def test_final_result_event_returns_last_result_type() -> None:
    log = _jsonl({"type": "system"}, {"type": "result", "result": "done"})
    assert runs_mod.final_result_event(log) == {"type": "result", "result": "done"}


def test_final_result_event_none_when_absent() -> None:
    assert runs_mod.final_result_event(_jsonl({"type": "system"})) is None


def test_final_result_event_ignores_malformed_lines() -> None:
    log = "not json\n" + _jsonl({"type": "result", "result": "ok"})
    assert runs_mod.final_result_event(log) == {"type": "result", "result": "ok"}


def test_final_result_event_ignores_blank_lines() -> None:
    log = "\n\n" + _jsonl({"type": "result", "result": "ok"})
    assert runs_mod.final_result_event(log) == {"type": "result", "result": "ok"}


def test_final_result_event_ignores_non_object_json_lines() -> None:
    log = "[1, 2, 3]\n" + _jsonl({"type": "result", "result": "ok"})
    assert runs_mod.final_result_event(log) == {"type": "result", "result": "ok"}


def test_permission_denial_count_reads_result_field() -> None:
    log = _jsonl({"type": "result", "permission_denials": 4})
    assert runs_mod.permission_denial_count(log) == 4


def test_permission_denial_count_defaults_to_zero() -> None:
    assert runs_mod.permission_denial_count(_jsonl({"type": "result"})) == 0


def test_permission_denial_count_zero_without_result_event() -> None:
    assert runs_mod.permission_denial_count("") == 0


def test_log_usage_limit_detected_true_for_usage_message() -> None:
    log = _jsonl({
        "type": "result", "is_error": True, "subtype": "error_during_execution",
        "result": "5-hour usage limit reached",
    })
    assert runs_mod.log_usage_limit_detected(log) is True


def test_log_usage_limit_detected_false_for_success() -> None:
    log = _jsonl({"type": "result", "result": "all good"})
    assert runs_mod.log_usage_limit_detected(log) is False


def test_log_succeeded_true_for_clean_result() -> None:
    assert runs_mod.log_succeeded(_jsonl({"type": "result", "result": "ok"})) is True


def test_log_succeeded_false_for_error_result() -> None:
    log = _jsonl({"type": "result", "is_error": True, "subtype": "error_generic"})
    assert runs_mod.log_succeeded(log) is False


def test_log_succeeded_none_when_no_result_event() -> None:
    assert runs_mod.log_succeeded("") is None


def test_final_message_text_reads_result_string() -> None:
    log = _jsonl({"type": "result", "result": "final words"})
    assert runs_mod.final_message_text(log) == "final words"


def test_final_message_text_empty_when_no_result() -> None:
    assert runs_mod.final_message_text("") == ""


def test_parse_next_wake_marker_defaults_when_missing() -> None:
    assert runs_mod.parse_next_wake_marker("just some text") == runs_mod.NEXT_WAKE_DEFAULT_S


def test_parse_next_wake_marker_defaults_on_empty_text() -> None:
    assert runs_mod.parse_next_wake_marker("") == runs_mod.NEXT_WAKE_DEFAULT_S


def test_parse_next_wake_marker_parses_last_line() -> None:
    text = "some prose\nALAN_LOOP_NEXT_WAKE=900"
    assert runs_mod.parse_next_wake_marker(text) == 900.0


def test_parse_next_wake_marker_clamps_low() -> None:
    text = "ALAN_LOOP_NEXT_WAKE=1"
    assert runs_mod.parse_next_wake_marker(text) == runs_mod.NEXT_WAKE_MIN_S


def test_parse_next_wake_marker_clamps_high() -> None:
    text = "ALAN_LOOP_NEXT_WAKE=999999"
    assert runs_mod.parse_next_wake_marker(text) == runs_mod.NEXT_WAKE_MAX_S


def test_parse_next_wake_marker_defaults_on_unparseable_value() -> None:
    assert runs_mod.parse_next_wake_marker("ALAN_LOOP_NEXT_WAKE=not-a-number") == (
        runs_mod.NEXT_WAKE_DEFAULT_S
    )


def test_parse_next_wake_marker_ignores_non_marker_last_line() -> None:
    text = "ALAN_LOOP_NEXT_WAKE=900\nsome trailing prose"
    assert runs_mod.parse_next_wake_marker(text) == runs_mod.NEXT_WAKE_DEFAULT_S


def test_finalize_from_log_marks_unknown_when_log_missing(tmp_path: Path) -> None:
    record = _record(stdout_log=str(tmp_path / "missing.log"))
    runs_mod._write_record(record)
    updated = runs_mod.finalize_from_log(record, timed_out=False, exit_code_unavailable=True)
    assert updated.outcome == RunOutcome.UNKNOWN_AFTER_RESTART
    assert updated.exit_code_unavailable is True
    assert updated.exit_code is None
    assert updated.is_finished


def test_finalize_from_log_reads_success_result(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "done well"}), encoding="utf-8")
    record = _record(stdout_log=str(log_path))
    updated = runs_mod.finalize_from_log(record, timed_out=False, exit_code_unavailable=True)
    assert updated.outcome == RunOutcome.SUCCEEDED
    assert updated.final_message == "done well"


def test_finalize_live_run_uses_real_exit_code(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "ok"}), encoding="utf-8")
    record = _record(stdout_log=str(log_path))
    updated = runs_mod.finalize_live_run(record, exit_code=1, timed_out=False)
    assert updated.exit_code == 1
    assert updated.exit_code_unavailable is False
    assert updated.outcome == RunOutcome.FAILED


def test_reconcile_after_restart_none_when_no_runs() -> None:
    assert runs_mod.reconcile_after_restart("no-such-loop") is None


def test_reconcile_after_restart_none_when_already_finished() -> None:
    runs_mod._write_record(_record(ended_at=100.0, outcome=RunOutcome.SUCCEEDED))
    assert runs_mod.reconcile_after_restart("alan-issue-driver") is None


def test_reconcile_after_restart_finalizes_when_pid_never_stamped(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text("", encoding="utf-8")
    runs_mod._write_record(_record(stdout_log=str(log_path)))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert result is not None
    assert result.exit_code_unavailable is True
    assert result.outcome == RunOutcome.UNKNOWN_AFTER_RESTART


def test_reconcile_after_restart_holds_while_still_alive_before_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1e15)
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert result is not None
    assert not result.is_finished


def test_reconcile_after_restart_kills_and_finalizes_past_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "late"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1.0, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))
    killed: list[int] = []
    monkeypatch.setattr(
        worker_records, "terminate_group", lambda pgid, **_kw: killed.append(pgid)
    )
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert killed == [999999]
    assert result is not None
    assert result.outcome == RunOutcome.TIMED_OUT
    assert result.exit_code_unavailable is True


def test_reconcile_after_restart_past_deadline_without_pgid_falls_back_to_terminate_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "late"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=None, lstart="L", cmdline="C", deadline_at=1.0, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))
    killed: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_pid", lambda pid: killed.append(pid))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert killed == [999999]
    assert result is not None
    assert result.outcome == RunOutcome.TIMED_OUT
    assert result.exit_code_unavailable is True


def test_reconcile_after_restart_past_deadline_without_pgid_and_ambiguous_identity_sends_no_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "late"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=None, lstart="L", cmdline="C", deadline_at=1.0, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    killed: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_pid", lambda pid: killed.append(pid))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert killed == []
    assert result is not None
    assert result.outcome == RunOutcome.TIMED_OUT
    assert result.exit_code_unavailable is True


def test_reconcile_after_restart_past_deadline_guard_error_skips_terminate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "late"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1.0, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("L", "C"))

    def _raise_guard(pid: int) -> None:
        raise worker_records.GroupSignalGuardError("nope")

    monkeypatch.setattr(worker_records, "guard_no_group_signal", _raise_guard)
    killed: list[int] = []
    monkeypatch.setattr(worker_records, "terminate_group", lambda pgid, **_kw: killed.append(pgid))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert killed == []
    assert result is not None
    assert result.outcome == RunOutcome.TIMED_OUT


def test_reconcile_after_restart_dead_process_finalizes_from_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "gone"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1e15, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: False)
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert result is not None
    assert result.outcome == RunOutcome.SUCCEEDED
    assert result.exit_code_unavailable is True


def test_reconcile_after_restart_holds_when_identity_is_unreadable_but_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1e15)
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert result is not None
    assert not result.is_finished


def test_reconcile_after_restart_treats_identity_mismatch_as_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "out.log"
    log_path.write_text(_jsonl({"type": "result", "result": "gone"}), encoding="utf-8")
    record = _record(
        pid=999999, pgid=999999, lstart="L", cmdline="C", deadline_at=1e15, stdout_log=str(log_path)
    )
    runs_mod._write_record(record)
    monkeypatch.setattr(worker_records, "is_pid_alive_or_ambiguous", lambda pid: True)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("OTHER", "OTHER"))
    result = runs_mod.reconcile_after_restart("alan-issue-driver")
    assert result is not None
    assert result.is_finished


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_spawn_run_writes_record_before_popen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written_before_popen: list[bool] = []

    def _fake_popen(argv: list[str], **kwargs: object) -> _FakeProc:
        written_before_popen.append(
            runs_mod._run_path("alan-issue-driver", "run").exists()
            or bool(runs_mod.list_runs("alan-issue-driver"))
        )
        return _FakeProc(pid=4242)

    monkeypatch.setattr(runs_mod, "new_run_id", lambda: "run")
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: ("LSTART", "CMD"))
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    entry = _entry(cwd=str(tmp_path))
    record, proc = runs_mod.spawn_run(entry)
    assert written_before_popen == [True]
    assert record.pid == 4242
    assert record.pgid == 4242
    assert record.lstart == "LSTART"
    assert record.cmdline == "CMD"
    assert proc.pid == 4242
    loaded = runs_mod.load_run("alan-issue-driver", "run")
    assert loaded == record


def test_spawn_run_handles_missing_pgid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runs_mod, "new_run_id", lambda: "run2")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: _FakeProc(pid=1))
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)

    def _raise_lookup(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", _raise_lookup)
    entry = _entry(cwd=str(tmp_path))
    record, _proc = runs_mod.spawn_run(entry)
    assert record.pgid is None
    assert record.lstart is None
    assert record.cmdline is None


def test_spawn_run_expands_tilde_in_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    captured: dict[str, object] = {}

    def _fake_popen(argv: list[str], **kwargs: object) -> _FakeProc:
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc(pid=1)

    monkeypatch.setattr(runs_mod, "new_run_id", lambda: "run3")
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(worker_records, "ps_identity", lambda pid: None)

    def _raise_lookup(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", _raise_lookup)
    entry = _entry(cwd="~/some-clone")
    runs_mod.spawn_run(entry)
    assert captured["cwd"] == str(tmp_path / "some-clone")
