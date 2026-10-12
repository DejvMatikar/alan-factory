from __future__ import annotations

from pathlib import Path

from tests._setup_stubs import GIB
from tests._setup_support import (
    SETUP_SCRIPT,
    Sandbox,
    Scenario,
    SetupRun,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    script_constant,
)

INSTALL_TOOLS = ("brew", "gh", "curl", "uv", "npm", "claude", "pre-commit", "node")


def _run(
    tmp_path: Path, scenario: Scenario, *args: str, absent: frozenset[str] | None = None
) -> tuple[Sandbox, SetupRun]:
    sandbox = make_sandbox(tmp_path, absent=absent or frozenset({"claude"}))
    return sandbox, run_setup(sandbox, *args, scenario=scenario)


def _assert_refused_before_any_mutation(run: SetupRun, probe: str) -> None:
    assert run.returncode == 1, run.output
    assert f"refused at preflight: {probe}:" in run.stderr
    assert run.headers() == ["preflight"]
    assert mutating_calls(run) == []
    assert [call for call in run.calls if call.tool in INSTALL_TOOLS] == []
    assert not run.sandbox.state_file.exists()


def test_a_healthy_mac_passes_every_probe_and_reports_ok(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(), "--only", "preflight")
    assert run.returncode == 0, run.output
    for line in (
        "[ok] architecture arm64",
        "[ok] GUI login session",
        "[ok] port 8585 is free",
        "[ok] memory 32 GB (minimum 16 GB)",
        "[ok] free disk 500 GB (minimum 20 GB)",
        "[ok] energy settings",
    ):
        assert line in run.stdout
    assert mutating_calls(run) == []


def test_a_mac_that_is_not_arm64_is_refused(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(arch="x86_64"))
    _assert_refused_before_any_mutation(run, "arch")
    assert "x86_64" in run.stderr


def test_a_login_without_a_gui_session_is_refused(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(gui_session=False))
    _assert_refused_before_any_mutation(run, "gui-session")


def test_port_8585_held_by_another_process_is_refused(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(port_pids=(777,)))
    _assert_refused_before_any_mutation(run, "port")
    assert "pid 777" in run.stderr


def test_ram_below_the_stated_minimum_is_refused(tmp_path: Path) -> None:
    minimum = script_constant("MIN_RAM_GB")
    _, run = _run(tmp_path, Scenario(ram_bytes=(minimum - 1) * GIB))
    _assert_refused_before_any_mutation(run, "ram")
    assert f"{minimum - 1} GB of memory" in run.stderr
    assert f"at least {minimum} GB" in run.stderr


def test_free_disk_below_the_stated_minimum_is_refused(tmp_path: Path) -> None:
    minimum = script_constant("MIN_DISK_GB")
    _, run = _run(tmp_path, Scenario(disk_free_kb=(minimum - 1) * 1024 * 1024))
    _assert_refused_before_any_mutation(run, "disk")
    assert f"only {minimum - 1} GB of disk" in run.stderr
    assert f"at least {minimum} GB" in run.stderr


def test_ram_exactly_at_the_minimum_passes(tmp_path: Path) -> None:
    minimum = script_constant("MIN_RAM_GB")
    _, run = _run(tmp_path, Scenario(ram_bytes=minimum * GIB), "--only", "preflight")
    assert run.returncode == 0, run.output
    assert f"[ok] memory {minimum} GB" in run.stdout


def test_free_disk_exactly_at_the_minimum_passes(tmp_path: Path) -> None:
    minimum = script_constant("MIN_DISK_GB")
    scenario = Scenario(disk_free_kb=minimum * 1024 * 1024)
    _, run = _run(tmp_path, scenario, "--only", "preflight")
    assert run.returncode == 0, run.output
    assert f"[ok] free disk {minimum} GB" in run.stdout


def test_an_unreadable_memory_size_is_refused(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(ram_bytes="unknown"))
    _assert_refused_before_any_mutation(run, "ram")
    assert "cannot read the installed memory size" in run.stderr


def test_an_unreadable_free_disk_figure_is_refused(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(disk_free_kb="unknown"))
    _assert_refused_before_any_mutation(run, "disk")
    assert "cannot read the free disk space" in run.stderr


def test_a_listener_that_is_this_checkouts_service_is_accepted(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    preload_agent(sandbox, pid=4242)
    run = run_setup(sandbox, "--only", "preflight", scenario=Scenario(port_pids=(4242,)))
    assert run.returncode == 0, run.output
    assert "[ok] port 8585 is held by this checkout's ai.alan service" in run.stdout


def test_a_listener_other_than_the_loaded_service_pid_is_refused(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    preload_agent(sandbox, pid=4242)
    run = run_setup(sandbox, "--only", "preflight", scenario=Scenario(port_pids=(4242, 9999)))
    assert run.returncode == 1
    assert "refused at preflight: port:" in run.stderr
    assert "pid 9999" in run.stderr


def test_a_missing_lsof_is_refused_instead_of_being_read_as_a_free_port(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(), absent=frozenset({"claude", "lsof"}))
    _assert_refused_before_any_mutation(run, "port")
    assert "lsof is not available" in run.stderr


def test_energy_guidance_is_text_only_and_never_refuses(tmp_path: Path) -> None:
    _, run = _run(tmp_path, Scenario(), "--only", "preflight")
    assert run.returncode == 0
    assert "turn off automatic sleeping when the display is off" in run.stdout
    assert "pmset" not in SETUP_SCRIPT.read_text(encoding="utf-8")
    assert [call.tool for call in run.calls if call.tool == "pmset"] == []
