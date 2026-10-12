from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from alan.fleet_load import (
    FLEET_ASYNC_STALL_TIMEOUT_MS,
    FLEET_BASH_DEFAULT_TIMEOUT_MS,
    FLEET_BASH_MAX_OUTPUT_LENGTH,
    FLEET_BASH_MAX_TIMEOUT_MS,
    FLEET_LOAD_THRESHOLD_MULTIPLIER,
    FLEET_NICE_DELTA,
    FLEET_PYTEST_WORKER_CAP,
    FLEET_VITEST_WORKER_CAP,
    apply_fleet_priority,
    fleet_env,
    host_cores,
    load_threshold,
    read_load1,
)


def test_fleet_nice_delta_is_a_positive_niceness_increment() -> None:
    assert 0 < FLEET_NICE_DELTA <= 19


def test_worker_caps_are_two() -> None:
    assert FLEET_PYTEST_WORKER_CAP == 2
    assert FLEET_VITEST_WORKER_CAP == 2


def test_apply_fleet_priority_lowers_priority_of_given_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        os, "setpriority", lambda which, pid, value: calls.append((which, pid, value))
    )
    apply_fleet_priority(4321)
    assert calls == [(os.PRIO_PROCESS, 4321, FLEET_NICE_DELTA)]


def test_fleet_env_sets_addopts_and_max_workers_from_empty_env() -> None:
    result = fleet_env({})
    assert result["PYTEST_ADDOPTS"] == f"-n {FLEET_PYTEST_WORKER_CAP}"
    assert result["VITEST_MAX_WORKERS"] == str(FLEET_VITEST_WORKER_CAP)


def test_fleet_env_sets_bash_and_stall_ceilings_from_empty_env() -> None:
    result = fleet_env({})
    assert result["BASH_DEFAULT_TIMEOUT_MS"] == str(FLEET_BASH_DEFAULT_TIMEOUT_MS)
    assert result["BASH_MAX_TIMEOUT_MS"] == str(FLEET_BASH_MAX_TIMEOUT_MS)
    assert result["BASH_MAX_OUTPUT_LENGTH"] == str(FLEET_BASH_MAX_OUTPUT_LENGTH)
    assert result["CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS"] == str(FLEET_ASYNC_STALL_TIMEOUT_MS)


def test_fleet_env_overrides_a_caller_supplied_bash_timeout_value() -> None:
    result = fleet_env({"BASH_MAX_TIMEOUT_MS": "600000"})
    assert result["BASH_MAX_TIMEOUT_MS"] == str(FLEET_BASH_MAX_TIMEOUT_MS)


def test_fleet_env_prepends_without_overwriting_existing_addopts() -> None:
    result = fleet_env({"PYTEST_ADDOPTS": "--maxfail=1"})
    assert result["PYTEST_ADDOPTS"] == f"-n {FLEET_PYTEST_WORKER_CAP} --maxfail=1"


def test_fleet_env_does_not_mutate_input() -> None:
    original = {"PYTEST_ADDOPTS": "--maxfail=1"}
    fleet_env(original)
    assert original == {"PYTEST_ADDOPTS": "--maxfail=1"}


def test_fleet_env_preserves_unrelated_keys() -> None:
    result = fleet_env({"PATH": "/usr/bin"})
    assert result["PATH"] == "/usr/bin"


def test_fleet_env_sets_pythondontwritebytecode_from_empty_env() -> None:
    result = fleet_env({})
    assert result["PYTHONDONTWRITEBYTECODE"] == "1"


def test_fleet_env_overrides_a_caller_supplied_pythondontwritebytecode_value() -> None:
    result = fleet_env({"PYTHONDONTWRITEBYTECODE": "0"})
    assert result["PYTHONDONTWRITEBYTECODE"] == "1"


def test_host_cores_returns_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 14)
    assert host_cores() == 14


def test_host_cores_falls_back_to_one_when_cpu_count_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert host_cores() == 1


def test_load_threshold_is_multiplier_times_host_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 14)
    assert load_threshold() == 14 * FLEET_LOAD_THRESHOLD_MULTIPLIER


def test_read_load1_returns_first_loadavg_element(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getloadavg", lambda: (105.0, 90.0, 80.0))
    assert read_load1() == 105.0


def _write_probe_project(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -n 6\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "def pytest_configure(config):\n"
        "    (config.rootpath / 'resolved_n.txt').write_text(str(config.option.numprocesses))\n",
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "def test_noop() -> None:\n    assert True\n", encoding="utf-8"
    )


def _run_probe(tmp_path: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "--collect-only", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "resolved_n.txt").read_text(encoding="utf-8")


def test_pytest_addopts_cap_overrides_ini_worker_count(tmp_path: Path) -> None:
    _write_probe_project(tmp_path)
    env = fleet_env(dict(os.environ))
    assert _run_probe(tmp_path, env) == str(FLEET_PYTEST_WORKER_CAP)


def test_ini_addopts_alone_resolves_to_the_ini_value(tmp_path: Path) -> None:
    _write_probe_project(tmp_path)
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    assert _run_probe(tmp_path, env) == "6"
