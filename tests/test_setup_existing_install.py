from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests._setup_support import (
    BOT_SENTINEL,
    SONAR_ADMIN_SENTINEL,
    SONAR_ANALYSIS_SENTINEL,
    UPDATE_LABEL,
    Sandbox,
    Scenario,
    SetupRun,
    leaks,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    seed_existing_install,
    seed_state,
)

UNDECLARED_AGENT = "gui/501/ai.alan = {\n\tstate = running\n\n\tpid = 4242\n}"


def _queue_without_state(sandbox: Sandbox) -> None:
    seed_existing_install(sandbox)


def _loaded_elsewhere(sandbox: Sandbox) -> None:
    preload_agent(sandbox, checkout=sandbox.elsewhere())


def _loaded_undeterminable(sandbox: Sandbox) -> None:
    preload_agent(sandbox, raw=UNDECLARED_AGENT)


def _loaded_from_a_missing_directory(sandbox: Sandbox) -> None:
    preload_agent(sandbox, checkout=sandbox.base / "gone")


def _update_agent_loaded_elsewhere(sandbox: Sandbox) -> None:
    preload_agent(sandbox, UPDATE_LABEL, checkout=sandbox.elsewhere())


TRIGGERS: dict[str, Callable[[Sandbox], None]] = {
    "queue-db-without-state": _queue_without_state,
    "agent-loaded-elsewhere": _loaded_elsewhere,
    "agent-undeterminable": _loaded_undeterminable,
    "agent-directory-missing": _loaded_from_a_missing_directory,
    "update-agent-loaded-elsewhere": _update_agent_loaded_elsewhere,
}
MODES: dict[str, tuple[str, ...]] = {
    "plain": (),
    "from": ("--from", "build"),
    "from-credential": ("--from", "credential"),
    "only": ("--only", "preflight"),
    "only-confirm": ("--only", "confirm"),
    "uninstall": ("--uninstall",),
}


def _refused(run: SetupRun) -> None:
    assert run.returncode == 1, run.output
    assert "refused at preflight: existing-install:" in run.stderr
    assert mutating_calls(run) == []
    assert {call.tool for call in run.calls} <= {"launchctl"}
    assert all(call.args[0] == "print" for call in run.calls)
    assert run.headers() == []
    assert "Steps you do by hand" not in run.stdout


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("trigger", TRIGGERS)
def test_an_existing_install_is_refused_before_any_mutating_call(
    tmp_path: Path, trigger: str, mode: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    TRIGGERS[trigger](sandbox)
    _refused(run_setup(sandbox, *MODES[mode]))


def test_an_owner_shaped_install_is_refused_by_the_guard_before_any_github_or_local_api_call(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    seed_existing_install(sandbox)
    assert not sandbox.state_file.exists()
    run = run_setup(sandbox, "--from", "credential", scenario=Scenario(transport_stored=False))
    _refused(run)
    assert run.called("gh") == []
    assert run.called("curl") == []
    assert list(sandbox.tmp_dir.iterdir()) == []


FINISH_MODES: dict[str, tuple[str, ...]] = {
    "plain": (),
    "from-verifier": ("--from", "verifier"),
    "only-verifier": ("--only", "verifier"),
    "only-updater": ("--only", "updater"),
}


@pytest.mark.parametrize("mode", FINISH_MODES)
def test_an_owner_shaped_install_is_refused_before_the_finish_steps_make_any_call(
    tmp_path: Path, mode: str
) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    seed_existing_install(sandbox)
    assert not sandbox.state_file.exists()
    run = run_setup(
        sandbox, *FINISH_MODES[mode], scenario=Scenario(), stdin=BOT_SENTINEL + "\n"
    )
    _refused(run)
    assert run.called("gh") == []
    assert run.called("curl") == []
    assert list(sandbox.tmp_dir.iterdir()) == []
    assert not sandbox.restart_marker.exists()
    assert not sandbox.plist_for(UPDATE_LABEL).exists()


SONAR_FLAG_MODES: dict[str, tuple[str, ...]] = {
    "flag-alone": ("--accept-sonar-user-token",),
    "only-sonar": ("--only", "sonar", "--accept-sonar-user-token"),
    "from-sonar": ("--from", "sonar", "--accept-sonar-user-token"),
    "flag-after-step": ("--from", "confirm", "--accept-sonar-user-token"),
    "no-sonar-alone": ("--no-sonar",),
    "only-sonar-no-sonar": ("--only", "sonar", "--no-sonar"),
    "from-sonar-no-sonar": ("--from", "sonar", "--no-sonar"),
}


@pytest.mark.parametrize("mode", SONAR_FLAG_MODES)
def test_an_owner_shaped_install_is_refused_under_the_sonar_flag_before_any_call(
    tmp_path: Path, mode: str
) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True)
    seed_existing_install(sandbox)
    assert not sandbox.state_file.exists()
    tokens = f"{SONAR_ADMIN_SENTINEL}\n{SONAR_ANALYSIS_SENTINEL}\n"
    run = run_setup(sandbox, *SONAR_FLAG_MODES[mode], scenario=Scenario(), stdin=tokens)
    _refused(run)
    assert run.called("gh") == []
    assert run.called("curl") == []
    assert list(sandbox.tmp_dir.iterdir()) == []
    assert not sandbox.restart_marker.exists()
    assert not sandbox.plist_for(UPDATE_LABEL).exists()
    assert SONAR_ADMIN_SENTINEL not in run.output
    assert SONAR_ANALYSIS_SENTINEL not in run.output


CLAUDE_CHECK_MODES: dict[str, tuple[str, ...]] = {
    "check-claude-alone": ("--check-claude",),
    "check-claude-with-the-sonar-flag": ("--check-claude", "--accept-sonar-user-token"),
    "sonar-flag-then-check-claude": ("--accept-sonar-user-token", "--check-claude"),
    "check-claude-with-no-sonar": ("--check-claude", "--no-sonar"),
}


@pytest.mark.parametrize("mode", CLAUDE_CHECK_MODES)
def test_an_owner_shaped_install_is_refused_under_the_claude_check_before_any_call(
    tmp_path: Path, mode: str
) -> None:
    sandbox = make_sandbox(
        tmp_path, absent=frozenset(), finish_steps=True, sonar_step=True, ci_steps=True
    )
    seed_existing_install(sandbox)
    assert not sandbox.state_file.exists()
    run = run_setup(sandbox, *CLAUDE_CHECK_MODES[mode], scenario=Scenario())
    _refused(run)
    assert run.called("gh") == []
    assert run.called("curl") == []
    assert run.called("claude") == []
    assert list(sandbox.tmp_dir.iterdir()) == []
    assert not sandbox.restart_marker.exists()
    assert not sandbox.launch_agents_dir.exists()
    assert not sandbox.ci_run_file.exists()


def test_a_queue_database_with_a_setup_state_file_is_not_an_existing_install(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    seed_existing_install(sandbox)
    seed_state(sandbox, "preflight")
    run = run_setup(sandbox, "--only", "preflight")
    assert run.returncode == 0, run.output


def test_a_service_loaded_from_this_checkout_is_not_an_existing_install(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    preload_agent(sandbox)
    run = run_setup(sandbox, "--only", "preflight")
    assert run.returncode == 0, run.output


def test_a_service_loaded_through_a_symlinked_path_is_compared_by_physical_path(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    preload_agent(sandbox, checkout=sandbox.alias())
    run = run_setup(sandbox, "--only", "preflight")
    assert run.returncode == 0, run.output


def test_the_bootstrap_entry_refuses_an_existing_install_before_any_gh_call(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    seed_existing_install(sandbox)
    run = run_setup(
        sandbox, "--template", "example-owner/template-repo", entry="setup.sh"
    )
    _refused(run)
    assert run.called("gh") == []


def test_the_refusal_names_neither_the_foreign_checkout_path_nor_any_identity(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    _loaded_elsewhere(sandbox)
    run = run_setup(sandbox)
    assert str(sandbox.elsewhere()) not in run.output
    assert leaks(run.output) == {}


def test_a_loaded_service_from_another_checkout_is_left_loaded_by_the_refusal(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    _loaded_elsewhere(sandbox)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 1
    assert (sandbox.stub_root / "state" / "loaded" / "ai.alan.json").exists()
