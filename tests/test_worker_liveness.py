from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from alan import worker_liveness, worker_records


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
        "pgid": None,
    }
    base.update(overrides)
    return worker_records.WorkerRecord(**base)  # type: ignore[arg-type]


def _unreachable_probe() -> int:
    raise AssertionError("probe_active_workers must not be called on the short-circuit path")


def test_encode_parse_round_trip() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=2, configured_loops=3, observed_at=1234.5
    )
    assert worker_liveness.parse(worker_liveness.encode(reading)) == reading


def test_parse_none_returns_none() -> None:
    assert worker_liveness.parse(None) is None


def test_parse_v1_blob_without_observed_at_returns_none() -> None:
    assert worker_liveness.parse(json.dumps({"alive": 0, "configured": 2})) is None


def test_parse_corrupt_json_returns_none() -> None:
    assert worker_liveness.parse("not-json{{{") is None


def test_parse_non_dict_json_returns_none() -> None:
    assert worker_liveness.parse(json.dumps([1, 2, 3])) is None


def test_parse_infinite_observed_at_returns_none() -> None:
    raw = json.dumps({"live_loops": 0, "configured_loops": 2, "observed_at": float("inf")})
    assert worker_liveness.parse(raw) is None


def test_parse_nan_observed_at_returns_none() -> None:
    raw = json.dumps({"live_loops": 0, "configured_loops": 2, "observed_at": float("nan")})
    assert worker_liveness.parse(raw) is None


def test_parse_out_of_range_observed_at_returns_none() -> None:
    raw = json.dumps({"live_loops": 0, "configured_loops": 2, "observed_at": 1e300})
    assert worker_liveness.parse(raw) is None


@pytest.mark.parametrize("observed_at", ["-Infinity", "-1e300"])
def test_parse_negative_unrepresentable_observed_at_returns_none(observed_at: str) -> None:
    raw = f'{{"live_loops": 0, "configured_loops": 2, "observed_at": {observed_at}}}'
    assert worker_liveness.parse(raw) is None


@pytest.mark.parametrize("missing", ["live_loops", "configured_loops", "observed_at"])
def test_parse_blob_missing_a_field_returns_none(missing: str) -> None:
    blob = {"live_loops": 0, "configured_loops": 2, "observed_at": 1234.5}
    del blob[missing]
    assert worker_liveness.parse(json.dumps(blob)) is None


@pytest.mark.parametrize("field", ["live_loops", "configured_loops", "observed_at"])
def test_parse_blob_with_non_numeric_field_returns_none(field: str) -> None:
    blob: dict[str, object] = {"live_loops": 0, "configured_loops": 2, "observed_at": 1234.5}
    blob[field] = "many"
    assert worker_liveness.parse(json.dumps(blob)) is None


def test_parse_ignores_unknown_keys() -> None:
    blob = {"live_loops": 1, "configured_loops": 2, "observed_at": 1234.5, "later": "x"}
    assert worker_liveness.parse(json.dumps(blob)) == worker_liveness.DispatchLoopReading(
        live_loops=1, configured_loops=2, observed_at=1234.5
    )


def test_parse_accepts_integer_observed_at_as_float() -> None:
    raw = json.dumps({"live_loops": 1, "configured_loops": 2, "observed_at": 1234})
    reading = worker_liveness.parse(raw)
    assert reading is not None
    assert reading.observed_at == 1234.0
    assert isinstance(reading.observed_at, float)


def test_encode_writes_the_established_keys_in_order() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=2, configured_loops=3, observed_at=1234.5
    )
    decoded = json.loads(worker_liveness.encode(reading))
    assert list(decoded) == ["live_loops", "configured_loops", "observed_at"]
    assert decoded == {"live_loops": 2, "configured_loops": 3, "observed_at": 1234.5}


def test_reading_construction_rejects_unrepresentable_observed_at() -> None:
    with pytest.raises(ValueError, match="representable"):
        worker_liveness.DispatchLoopReading(
            live_loops=0, configured_loops=1, observed_at=float("inf")
        )


def test_is_fresh_boundary_is_inclusive() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=0, observed_at=1000.0
    )
    assert worker_liveness.is_fresh(reading, now=1000.0 + worker_liveness.HEARTBEAT_TTL_S)


def test_is_fresh_just_past_ttl_is_false() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=0, observed_at=1000.0
    )
    assert not worker_liveness.is_fresh(
        reading, now=1000.0 + worker_liveness.HEARTBEAT_TTL_S + 0.001
    )


def test_is_fresh_future_observed_at_is_false() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=2, observed_at=2000.0
    )
    assert not worker_liveness.is_fresh(reading, now=1000.0)


def test_classify_future_dated_reading_is_silent() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=2, observed_at=2000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=0, probe_active_workers=_unreachable_probe
    )
    assert not blocked
    assert reason == ""


def test_classify_absent_reading_is_silent() -> None:
    blocked, reason = worker_liveness.classify(
        None, now=1000.0, running_jobs=0, probe_active_workers=_unreachable_probe
    )
    assert not blocked
    assert reason == ""


def test_classify_stale_reading_is_silent() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=2, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading,
        now=1000.0 + worker_liveness.HEARTBEAT_TTL_S + 1.0,
        running_jobs=0,
        probe_active_workers=_unreachable_probe,
    )
    assert not blocked
    assert reason == ""


def test_classify_no_deficit_is_silent() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=2, configured_loops=2, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=0, probe_active_workers=_unreachable_probe
    )
    assert not blocked
    assert reason == ""


def test_classify_zero_configured_is_silent() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=0, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=0, probe_active_workers=_unreachable_probe
    )
    assert not blocked
    assert reason == ""


def test_classify_deficit_silenced_by_running_jobs() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=2, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=1, probe_active_workers=_unreachable_probe
    )
    assert not blocked
    assert reason == ""


def test_classify_deficit_silenced_by_detached_survivor() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=0, configured_loops=2, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=0, probe_active_workers=lambda: 1
    )
    assert not blocked
    assert reason == ""


def test_classify_deficit_with_no_corroboration_is_blocked() -> None:
    reading = worker_liveness.DispatchLoopReading(
        live_loops=1, configured_loops=4, observed_at=1000.0
    )
    blocked, reason = worker_liveness.classify(
        reading, now=1000.0, running_jobs=0, probe_active_workers=lambda: 0
    )
    assert blocked
    assert reason == "worker_deficit:3"


def test_probe_active_workers_zero_when_no_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_records, "load_all", lambda: [])
    assert worker_liveness.probe_active_workers() == 0


def test_probe_active_workers_skips_none_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_records, "load_all", lambda: [(Path("s.json"), None)])
    assert worker_liveness.probe_active_workers() == 0


def test_probe_active_workers_counts_only_detached_survivors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    survivor = _make_record(session_id="s1", pgid=123)
    non_survivor = _make_record(session_id="s2", pgid=None)
    dead = _make_record(session_id="s3", pgid=456)
    monkeypatch.setattr(
        worker_records,
        "load_all",
        lambda: [
            (Path("s1.json"), survivor),
            (Path("s2.json"), non_survivor),
            (Path("s3.json"), dead),
        ],
    )

    def _fake_probe(
        record: worker_records.WorkerRecord, *, this_service: tuple[int, str]
    ) -> worker_records.ProbeVerdict:
        del this_service
        if record.session_id == "s3":
            return worker_records.ProbeVerdict.DEAD
        return worker_records.ProbeVerdict.LIVE

    monkeypatch.setattr(worker_records, "probe", _fake_probe)
    assert worker_liveness.probe_active_workers() == 1


def _fake_completed(
    returncode: int, stdout: str, stderr: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_probe_tool_missing_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: None)
    probe = worker_liveness.probe_tool("git")
    assert probe == worker_liveness.ToolProbe("git", None, False, "git not found on PATH")


def test_probe_tool_nonzero_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/git")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _fake_completed(1, "", "sudo xcodebuild -license accept"),
    )
    probe = worker_liveness.probe_tool("git")
    assert probe.ok is False
    assert probe.resolved_path == "/usr/bin/git"
    assert probe.message == "sudo xcodebuild -license accept"


def test_probe_tool_nonzero_with_stdout_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/python3")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: _fake_completed(1, "stdout detail", ""),
    )
    probe = worker_liveness.probe_tool("python3")
    assert probe.ok is False
    assert probe.message == "stdout detail"


def test_probe_tool_nonzero_with_neither_falls_back_to_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/git")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(7, "", "")
    )
    probe = worker_liveness.probe_tool("git")
    assert probe.ok is False
    assert probe.message == "exit 7"


def test_probe_tool_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/git")

    def _boom(*a: object, **kw: object) -> object:
        raise OSError("no such executable")

    monkeypatch.setattr(subprocess, "run", _boom)
    probe = worker_liveness.probe_tool("git")
    assert probe.ok is False
    assert probe.message == "no such executable"


def test_probe_tool_timeout_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/git")
    exc = subprocess.TimeoutExpired(cmd=["git", "--version"], timeout=5.0)

    def _boom(*a: object, **kw: object) -> object:
        raise exc

    monkeypatch.setattr(subprocess, "run", _boom)
    probe = worker_liveness.probe_tool("git")
    assert probe.ok is False
    assert probe.message == str(exc)


def test_probe_tool_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/git")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _fake_completed(0, "git version 2", "")
    )
    probe = worker_liveness.probe_tool("git")
    assert probe == worker_liveness.ToolProbe("git", "/usr/bin/git", True, "")


def test_probe_host_toolchain_order_is_git_then_python3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_probe(tool: str) -> worker_liveness.ToolProbe:
        calls.append(tool)
        return worker_liveness.ToolProbe(tool, f"/usr/bin/{tool}", True, "")

    monkeypatch.setattr(worker_liveness, "probe_tool", _fake_probe)
    probes = worker_liveness.probe_host_toolchain()
    assert calls == ["git", "python3"]
    assert [p.tool for p in probes] == ["git", "python3"]


def test_recovery_command_extracts_sudo_line() -> None:
    message = "xcrun: error: active developer path\nsudo xcodebuild -license accept"
    assert worker_liveness.recovery_command(message) == "sudo xcodebuild -license accept"


def test_recovery_command_falls_back_when_no_sudo_line() -> None:
    assert worker_liveness.recovery_command("some unrelated error") == (
        worker_liveness._LICENSE_RECOVERY_FALLBACK  # pyright: ignore[reportPrivateUsage]
    )


def test_recovery_command_finds_sudo_line_not_first() -> None:
    message = "line one\nline two\nsudo brew install foo\ntrailing"
    assert worker_liveness.recovery_command(message) == "sudo brew install foo"


def test_classify_host_toolchain_all_ok() -> None:
    probes = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", True, ""),
        worker_liveness.ToolProbe("python3", "/usr/bin/python3", True, ""),
    ]
    blocked, reason, obs, fp_obs = worker_liveness.classify_host_toolchain(probes)
    assert blocked is False
    assert reason == ""
    assert obs == {"resolved_paths": {"git": "/usr/bin/git", "python3": "/usr/bin/python3"}}
    assert fp_obs == {
        "resolved_paths": {"git": "/usr/bin/git", "python3": "/usr/bin/python3"},
        "failing_tools": [],
        "first_line": "",
    }


def test_classify_host_toolchain_git_fails() -> None:
    probes = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", False, "sudo xcodebuild -license accept"),
        worker_liveness.ToolProbe("python3", "/usr/bin/python3", True, ""),
    ]
    blocked, reason, obs, fp_obs = worker_liveness.classify_host_toolchain(probes)
    assert blocked is True
    assert reason == "host_toolchain_gated"
    assert obs["failing_tool"] == "git"
    assert obs["failing_tools"] == ["git"]
    assert obs["message"] == "sudo xcodebuild -license accept"
    assert obs["recovery_command"] == "sudo xcodebuild -license accept"
    assert fp_obs["failing_tools"] == ["git"]


def test_classify_host_toolchain_python3_fails() -> None:
    probes = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", True, ""),
        worker_liveness.ToolProbe(
            "python3", "/usr/bin/python3", False, "sudo xcodebuild -license accept"
        ),
    ]
    blocked, reason, obs, fp_obs = worker_liveness.classify_host_toolchain(probes)
    assert blocked is True
    assert obs["failing_tool"] == "python3"
    assert obs["failing_tools"] == ["python3"]


def test_classify_host_toolchain_both_fail_lists_both() -> None:
    probes = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", False, "sudo one"),
        worker_liveness.ToolProbe("python3", "/usr/bin/python3", False, "sudo two"),
    ]
    blocked, _reason, obs, _fp_obs = worker_liveness.classify_host_toolchain(probes)
    assert blocked is True
    assert obs["failing_tools"] == ["git", "python3"]
    assert obs["failing_tool"] == "git"


def test_classify_host_toolchain_fp_obs_stable_across_stderr_tail_change() -> None:
    probes_a = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", False, "sudo brew install foo\ntail A"),
        worker_liveness.ToolProbe("python3", "/usr/bin/python3", True, ""),
    ]
    probes_b = [
        worker_liveness.ToolProbe("git", "/usr/bin/git", False, "sudo brew install foo\ntail B"),
        worker_liveness.ToolProbe("python3", "/usr/bin/python3", True, ""),
    ]
    _, _, obs_a, fp_obs_a = worker_liveness.classify_host_toolchain(probes_a)
    _, _, obs_b, fp_obs_b = worker_liveness.classify_host_toolchain(probes_b)
    assert fp_obs_a == fp_obs_b
    assert obs_a != obs_b
