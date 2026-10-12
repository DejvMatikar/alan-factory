from __future__ import annotations

from pathlib import Path

import pytest

from tests._setup_support import (
    AMBIENT_SENTINEL,
    ENGINE_SLUG,
    TOKEN_SENTINEL,
    TRANSPORT_SENTINEL,
    Sandbox,
    Scenario,
    SetupRun,
    assert_no_secret,
    make_sandbox,
    run_setup,
    seed_bound_service,
    stored_secrets,
)

BIND_STEPS = ["credential", "harden", "protect", "confirm"]
POLICY_STUCK = Scenario(pr_policy="all", pr_policy_sticks=False)
UNBOUND = Scenario(
    transport_stored=False,
    scanning="disabled",
    push_protection="disabled",
    interaction_limit=None,
    pr_policy="all",
    protection_matching=False,
)


def _ready(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path)
    seed_bound_service(sandbox)
    return sandbox


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _resume(run: SetupRun) -> str:
    return run.stderr.strip().splitlines()[-1]


def test_a_failing_step_does_not_stop_the_later_steps(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = run_setup(sandbox, "--from", "credential", scenario=POLICY_STUCK)
    assert run.returncode == 1
    assert run.headers() == BIND_STEPS
    assert _resume(run) == "Resume with: scripts/setup_macos.sh --from harden"
    assert "setup: incomplete: harden did not complete" in run.stderr
    marks = _marks(sandbox)
    assert "harden" not in marks
    assert {"credential", "protect", "confirm"} <= set(marks)
    assert "step harden did not complete, so it is not marked done" in run.stdout


def test_the_resume_command_names_the_first_failed_step_and_the_summary_lists_all(
    tmp_path: Path,
) -> None:
    scenario = Scenario(
        pr_policy="all",
        pr_policy_sticks=False,
        protection_matching=False,
        protect_result="deferred",
    )
    run = run_setup(_ready(tmp_path), "--from", "credential", scenario=scenario)
    assert run.returncode == 1
    assert "setup: incomplete: harden, protect, confirm did not complete" in run.stderr
    assert _resume(run) == "Resume with: scripts/setup_macos.sh --from harden"


def test_a_later_failure_alone_names_that_step_in_the_resume_command(tmp_path: Path) -> None:
    scenario = Scenario(protection_matching=False, protect_result="deferred")
    run = run_setup(_ready(tmp_path), "--from", "credential", scenario=scenario)
    assert run.returncode == 1
    assert _resume(run) == "Resume with: scripts/setup_macos.sh --from protect"
    assert run.headers() == BIND_STEPS


def test_a_step_that_failed_and_passes_on_the_rerun_is_then_marked(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert run_setup(sandbox, "--only", "harden", scenario=POLICY_STUCK).returncode == 1
    fixed = run_setup(sandbox, "--only", "harden", scenario=Scenario(pr_policy="all"))
    assert fixed.returncode == 0, fixed.output
    assert "harden" in _marks(sandbox)
    assert "Resume with" not in fixed.stderr


def test_a_run_without_a_failure_prints_no_resume_command(tmp_path: Path) -> None:
    run = run_setup(_ready(tmp_path), "--from", "credential")
    assert run.returncode == 0, run.output
    assert run.headers() == BIND_STEPS
    assert "Resume with" not in run.output
    assert run.stdout.count("[already]") == 6


@pytest.mark.parametrize("entry", ["scripts/setup_macos.sh", "setup.sh"])
@pytest.mark.parametrize("step", BIND_STEPS)
def test_both_entry_points_accept_each_new_step_for_only(
    tmp_path: Path, entry: str, step: str
) -> None:
    run = run_setup(_ready(tmp_path), "--only", step, entry=entry)
    assert run.returncode == 0, run.output
    assert run.headers() == [step]


@pytest.mark.parametrize("entry", ["scripts/setup_macos.sh", "setup.sh"])
def test_both_entry_points_accept_the_first_new_step_for_from(tmp_path: Path, entry: str) -> None:
    run = run_setup(_ready(tmp_path), "--from", "credential", entry=entry)
    assert run.returncode == 0, run.output
    assert run.headers() == BIND_STEPS


def test_an_unknown_step_is_still_a_usage_error(tmp_path: Path) -> None:
    run = run_setup(_ready(tmp_path), "--only", "bind")
    assert run.returncode == 2
    assert "unknown step for --only: bind" in run.stderr
    assert run.calls == ()


def test_the_use_gh_token_flag_is_a_known_flag(tmp_path: Path) -> None:
    run = run_setup(_ready(tmp_path), "--only", "confirm", "--use-gh-token")
    assert run.returncode == 0, run.output


def test_a_fresh_full_run_binds_hardens_protects_and_confirms_without_leaking_a_secret(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, scenario=UNBOUND, stdin=TRANSPORT_SENTINEL + "\n")
    assert run.returncode == 0, run.output
    assert run.headers() == [
        "preflight",
        "install",
        "build",
        "profile",
        "start",
        *BIND_STEPS,
    ]
    assert f"[done] transport credential stored for {ENGINE_SLUG}" in run.stdout
    assert "[done] branch protection applied" in run.stdout
    assert [row["engine"] for row in stored_secrets(sandbox)] == [ENGINE_SLUG]
    closing = run.stdout.strip().splitlines()[-2:]
    assert "P1.9b" not in closing[1]
    assert "P1.9c" not in closing[1]
    assert "the final report" in closing[1]
    assert_no_secret(run, TRANSPORT_SENTINEL, AMBIENT_SENTINEL, TOKEN_SENTINEL)


def test_the_closing_line_lists_binding_as_pending_after_a_failure_and_drops_it_once_fixed(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    failed = run_setup(sandbox, scenario=POLICY_STUCK)
    assert failed.returncode == 1
    assert "P1.9b" in failed.stdout.strip().splitlines()[-1]
    fixed = run_setup(sandbox, "--from", "start", scenario=Scenario(pr_policy="all"))
    assert fixed.returncode == 0, fixed.output
    closing = fixed.stdout.strip().splitlines()[-2:]
    assert "P1.9b" not in closing[1]
    assert "P1.9c" not in closing[1]
    assert "the final report" in closing[1]
