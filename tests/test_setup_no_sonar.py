from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import default_profile_writer
from tests._setup_api_stubs import sha256
from tests._setup_ci_stubs import FIRST_RUN_ID, STILL_RUNNING, seed_run
from tests._setup_support import (
    BOT_LOGIN,
    BOT_SENTINEL,
    BOUND_MARKS,
    OWNER,
    SONAR_ADMIN_SENTINEL,
    SONAR_ANALYSIS_SENTINEL,
    SONAR_ORGANIZATION,
    SONAR_PROJECT_KEY,
    UPDATE_LABEL,
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    assert_no_secret,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    seed_latch,
    seed_profile,
    seed_running_service,
    seed_secrets_env,
    seed_state,
    snapshot_files,
    stored_secrets,
)

REPO = "example-repo"
TYPED = f"{SONAR_ADMIN_SENTINEL}\n{SONAR_ANALYSIS_SENTINEL}\n"
PLANTED = "EXAMPLE_KEY=planted-value-1\n"
SKIPPED_BY_FLAG = "[skipped] SonarCloud is optional and --no-sonar was given"
NOT_CONNECTED = "[ok] SonarCloud: not connected (optional)"
RESUME_TRIGGER = "Resume with: scripts/setup_macos.sh --from trigger"
RESUME_LATCH = "Resume with: scripts/setup_macos.sh --from latch"
BOUND = (*BOUND_MARKS, "credential", "harden", "protect", "confirm")
GOOD = Scenario(reviewer_stored=True)


def _sonar_sandbox(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, PLANTED)
    default_profile_writer.write_profile(sandbox.alan_dir / "engines", OWNER, REPO)
    return sandbox


def _connected(tmp_path: Path) -> Sandbox:
    sandbox = _sonar_sandbox(tmp_path)
    connecting = run_setup(sandbox, "--only", "sonar", stdin=TYPED)
    assert connecting.returncode == 0, connecting.output
    return sandbox


def _skip_flag(sandbox: Sandbox, *flags: str) -> SetupRun:
    return run_setup(sandbox, "--only", "sonar", "--no-sonar", *flags, stdin=None)


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _sonarcloud_calls(run: SetupRun) -> list[str]:
    return [call.args[-1] for call in run.called("curl") if "sonarcloud.io" in call.args[-1]]


def _profile_text(sandbox: Sandbox) -> str:
    return sandbox.profile_file.read_text(encoding="utf-8")


def test_the_no_sonar_flag_takes_no_value_and_does_not_swallow_the_next_argument(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--no-sonar", "--only", "preflight")
    assert run.returncode == 0, run.output
    assert run.headers() == ["preflight"]


@pytest.mark.parametrize("lookalike", ["--no-sonarcloud", "--no-sonar=1", "--nosonar"])
def test_a_lookalike_of_the_no_sonar_flag_is_a_usage_error(
    tmp_path: Path, lookalike: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "preflight", lookalike)
    assert run.returncode == 2
    assert f"unknown flag: {lookalike}" in run.stderr
    assert run.calls == ()


def test_the_no_sonar_flag_and_the_sonar_user_token_flag_can_be_given_together(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "preflight", "--no-sonar", "--accept-sonar-user-token")
    assert run.returncode == 0, run.output


def test_the_no_sonar_flag_skips_the_step_without_a_prompt_and_records_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _sonar_sandbox(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _skip_flag(sandbox)
    assert run.returncode == 0, run.output
    assert SKIPPED_BY_FLAG in run.stdout
    assert "Paste the admin token" not in run.stdout
    assert "[failed]" not in run.stdout
    assert "code_quality" not in _profile_text(sandbox)
    assert sandbox.profile_file.read_bytes() == profile
    assert mutating_calls(run) == []
    assert _sonarcloud_calls(run) == []
    assert stored_secrets(sandbox) == []
    assert not sandbox.restart_marker.exists()
    assert run.called("launchctl", "kickstart") == []
    assert "sonar" in _marks(sandbox)


def test_the_no_sonar_flag_leaves_the_standard_input_to_the_steps_after_it(
    tmp_path: Path,
) -> None:
    sandbox = _sonar_sandbox(tmp_path)
    run = run_setup(sandbox, "--from", "sonar", "--no-sonar", stdin=f"{BOT_SENTINEL}\n")
    assert run.returncode == 0, run.output
    assert run.headers() == ["sonar", "verifier", "updater"]
    assert stored_secrets(sandbox)[0]["sha256"] == sha256(BOT_SENTINEL)
    assert_no_secret(run, BOT_SENTINEL)


def test_the_no_sonar_flag_keeps_the_ci_run_recorded_before(tmp_path: Path) -> None:
    sandbox = _sonar_sandbox(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    assert _skip_flag(sandbox).returncode == 0
    assert sandbox.ci_run_file.exists()


def test_the_no_sonar_flag_on_a_connected_install_says_it_stays_connected_and_changes_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _connected(tmp_path)
    watched = snapshot_files(sandbox.profile_file, sandbox.state_file, sandbox.secrets_file)
    run = _skip_flag(sandbox)
    assert run.returncode == 0, run.output
    assert "[already] SonarCloud stays connected" in run.stdout
    assert "--no-sonar changed nothing" in run.stdout
    assert "Paste the admin token" not in run.stdout
    assert mutating_calls(run) == []
    assert _sonarcloud_calls(run) == []
    assert run.called("launchctl", "kickstart") == []
    assert snapshot_files(*watched) == watched
    assert "code_quality" in _profile_text(sandbox)


def test_a_rerun_on_a_connected_install_reports_it_already_configured_and_asks_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _connected(tmp_path)
    run = run_setup(sandbox, "--only", "sonar", stdin=None)
    assert run.returncode == 0, run.output
    assert "already configured" in run.stdout
    assert "Paste the admin token" not in run.stdout
    assert "stays connected" not in run.stdout
    assert mutating_calls(run) == []


def _ci_sandbox(tmp_path: Path, *, connected: bool = False) -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True, ci_steps=True)
    seed_running_service(sandbox)
    organization = SONAR_ORGANIZATION if connected else None
    seed_profile(sandbox, organization=organization, quality_required=False)
    return sandbox


def _latch(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "latch", scenario=scenario)


def _reads(run: SetupRun, kind: str) -> int:
    return len(
        [call for call in run.called("gh", "api") if f"/{kind}?" in call.args[1]]
    )


def _resume(run: SetupRun) -> str:
    return run.stderr.strip().splitlines()[-1]


def test_the_latch_step_waits_for_the_gate_alone_when_sonarcloud_is_not_connected(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file, done_after=1)
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    assert len(run.called("gh", "run", "view")) == 2
    assert _reads(run, "check-runs") == 0
    assert _reads(run, "statuses") == 0
    assert not sandbox.latch_file.exists()
    assert f"[ok] CI run {FIRST_RUN_ID} passed its gate; SonarCloud: not connected (optional)" in (
        run.stdout
    )
    assert "latch" in _marks(sandbox)
    assert mutating_calls(run) == []


def test_a_red_run_leaves_the_latch_step_incomplete_when_sonarcloud_is_not_connected(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file, result="failure")
    run = _latch(sandbox)
    assert run.returncode == 1
    assert f"[failed] CI run {FIRST_RUN_ID} is red" in run.stdout
    assert _resume(run) == RESUME_TRIGGER
    assert "latch" not in _marks(sandbox)
    assert _reads(run, "check-runs") == 0


def test_a_run_that_never_finishes_leaves_the_latch_step_incomplete_without_sonarcloud(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file, done_after=STILL_RUNNING)
    run = _latch(sandbox)
    assert run.returncode == 1
    assert f"CI run {FIRST_RUN_ID} did not finish within {WAIT_BUDGET} checks" in run.stdout
    assert _resume(run) == RESUME_LATCH
    assert not sandbox.latch_file.exists()


def test_without_a_recorded_run_the_latch_step_fails_even_when_sonarcloud_is_not_connected(
    tmp_path: Path,
) -> None:
    run = _latch(_ci_sandbox(tmp_path))
    assert run.returncode == 1
    assert "no CI run is recorded" in run.stdout
    assert _resume(run) == RESUME_TRIGGER


def test_a_connected_sonarcloud_that_reports_is_latched_although_the_profile_does_not_require_it(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path, connected=True)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    held = json.loads(sandbox.latch_file.read_text(encoding="utf-8"))
    assert (held["organization"], held["project_key"]) == (SONAR_ORGANIZATION, SONAR_PROJECT_KEY)
    assert "[done] SonarCloud reported on the CI run: latch written" in run.stdout
    assert _reads(run, "check-runs") == 1


def test_a_connected_sonarcloud_that_posts_nothing_is_a_warning_and_the_step_completes(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path, connected=True)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    run = _latch(sandbox, Scenario(sonar_evidence="none"))
    assert run.returncode == 0, run.output
    assert "[warning] SonarCloud has not reported: SonarCloud posted no check or status" in (
        run.stdout
    )
    assert f"within {WAIT_BUDGET} checks; nothing was latched" in run.stdout
    assert "it is optional, so setup carries on" in run.stdout
    assert _reads(run, "check-runs") == WAIT_BUDGET
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert not sandbox.latch_file.exists()
    assert "latch" in _marks(sandbox)
    assert "[failed]" not in run.stdout
    assert "incomplete" not in run.stderr
    assert "Resume with" not in run.stderr


def test_a_red_run_still_leaves_the_latch_step_incomplete_when_sonarcloud_is_connected(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path, connected=True)
    seed_run(sandbox.stub_root, sandbox.ci_run_file, result="failure")
    run = _latch(sandbox, Scenario(sonar_evidence="none"))
    assert run.returncode == 1
    assert _resume(run) == RESUME_TRIGGER
    assert _reads(run, "check-runs") == 0


def test_a_connected_latch_that_matches_the_project_is_already_done_and_reads_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _ci_sandbox(tmp_path, connected=True)
    seed_latch(sandbox)
    seed_run(sandbox.stub_root, sandbox.ci_run_file, result="failure")
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    assert f"[already] SonarCloud latch for {OWNER}/{REPO}" in run.stdout
    assert run.called("gh") == []


def _finished(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(
        tmp_path, absent=frozenset(), finish_steps=True, sonar_step=True, ci_steps=True
    )
    seed_running_service(sandbox, *BOUND)
    seed_profile(sandbox, organization=None, quality_required=False)
    seed_secrets_env(sandbox, f"{PLANTED}ALAN_VERIFIER_LOGIN={BOT_LOGIN}\n")
    preload_agent(sandbox, UPDATE_LABEL)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    return sandbox


def _gh_writes(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in mutating_calls(run) if call.tool == "gh"]


@pytest.mark.parametrize(
    ("flags", "stdin"), [(("--no-sonar",), None), ((), "\n")], ids=["flag", "enter"]
)
def test_a_full_run_that_skips_sonarcloud_ends_complete_with_exit_zero(
    tmp_path: Path, flags: tuple[str, ...], stdin: str | None
) -> None:
    sandbox = _finished(tmp_path)
    run = run_setup(sandbox, "--from", "sonar", *flags, scenario=GOOD, stdin=stdin)
    assert run.returncode == 0, run.output
    assert run.headers() == ["sonar", "verifier", "trigger", "claude", "updater", "latch", "report"]
    assert NOT_CONNECTED in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")
    assert "incomplete" not in run.stderr
    assert _gh_writes(run) == []
    assert "code_quality" not in _profile_text(sandbox)
    assert not sandbox.latch_file.exists()
    assert {"sonar", "latch", "report"} <= set(_marks(sandbox))


def test_a_full_run_that_connects_sonarcloud_after_a_skip_waits_for_it_and_latches(
    tmp_path: Path,
) -> None:
    sandbox = _finished(tmp_path)
    skipped = run_setup(sandbox, "--from", "sonar", "--no-sonar", scenario=GOOD, stdin=None)
    assert skipped.returncode == 0, skipped.output
    run = run_setup(sandbox, "--from", "sonar", scenario=GOOD, stdin=TYPED)
    assert run.returncode == 0, run.output
    assert len(run.called("gh", "workflow", "run")) == 1
    assert sandbox.latch_file.exists()
    assert "[ok] SonarCloud has reported and its latch matches the profile's project" in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


def test_the_final_report_warns_about_a_connected_sonarcloud_that_has_not_reported(
    tmp_path: Path,
) -> None:
    sandbox = _finished(tmp_path)
    seed_profile(sandbox, organization=SONAR_ORGANIZATION, quality_required=False)
    seed_state(sandbox, *BOUND, "sonar", "verifier", "trigger", "claude", "updater", "latch")
    run = run_setup(sandbox, "--only", "report", scenario=GOOD)
    assert run.returncode == 0, run.output
    assert (
        "[warning] SonarCloud is connected but has not reported for this install" in run.stdout
    )
    assert NOT_CONNECTED not in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")
