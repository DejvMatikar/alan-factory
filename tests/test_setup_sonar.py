from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from alan import default_profile_writer
from tests._setup_api_stubs import sha256
from tests._setup_support import (
    BOT_SENTINEL,
    ENGINE_SLUG,
    OWNER,
    SONAR_ADMIN_LOGIN,
    SONAR_ADMIN_SENTINEL,
    SONAR_ANALYSIS_SENTINEL,
    TRANSPORT_SENTINEL,
    Sandbox,
    Scenario,
    SetupRun,
    assert_no_secret,
    leaks,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_running_service,
    seed_secrets_env,
    snapshot_files,
    stored_secrets,
    stub_log,
)

UID = os.getuid()
KICKSTART = ("kickstart", "-k", f"gui/{UID}/ai.alan")
TYPED = f"{SONAR_ADMIN_SENTINEL}\n{SONAR_ANALYSIS_SENTINEL}\n"
RESUME_SONAR = "Resume with: scripts/setup_macos.sh --from sonar"
PLANTED = "EXAMPLE_KEY=planted-value-1\nEXAMPLE_SECRET=never-echoed-never-echoed\n"
BROWSER = "In your browser:"
SECRET_ARGS = ("secret", "set", "SONAR_TOKEN", "--repo", ENGINE_SLUG)


def _ready(tmp_path: Path, owner: str = OWNER, repo: str = "example-repo") -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, PLANTED)
    default_profile_writer.write_profile(sandbox.alan_dir / "engines", owner, repo)
    return sandbox


def _sonar(
    sandbox: Sandbox,
    scenario: Scenario | None = None,
    stdin: str | None = TYPED,
    *flags: str,
) -> SetupRun:
    return run_setup(sandbox, "--only", "sonar", *flags, scenario=scenario, stdin=stdin)


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _block(sandbox: Sandbox) -> object:
    return yaml.safe_load(sandbox.profile_file.read_text(encoding="utf-8")).get("code_quality")


def _failed_lines(run: SetupRun) -> list[str]:
    return [line for line in run.stdout.splitlines() if line.startswith("  [failed] ")]


def _github_writes(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in mutating_calls(run) if call.tool == "gh"]


def _assert_nothing_was_written(sandbox: Sandbox, run: SetupRun, profile: bytes) -> None:
    assert _github_writes(run) == []
    assert sandbox.profile_file.read_bytes() == profile
    assert not sandbox.restart_marker.exists()
    assert run.called("launchctl", "kickstart") == []
    assert "sonar" not in _marks(sandbox)
    assert stored_secrets(sandbox) == []
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


@pytest.mark.parametrize(
    ("organization", "key"),
    [("example-org", "example-org_example-repo"), ("second-org", "second-org_renamed-project")],
    ids=["default", "changed"],
)
def test_one_bound_project_writes_the_profile_block_and_both_variables_with_its_values(
    tmp_path: Path, organization: str, key: str
) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(sonar_organization=organization, sonar_project_key=key)
    run = _sonar(sandbox, scenario)
    assert run.returncode == 0, run.output
    assert _block(sandbox) == {"organization": organization, "project_key": key}
    assert [call.args for call in run.called("gh", "variable", "set")] == [
        ("variable", "set", "SONAR_ORGANIZATION", "--repo", ENGINE_SLUG, "--body", organization),
        ("variable", "set", "SONAR_PROJECT_KEY", "--repo", ENGINE_SLUG, "--body", key),
    ]
    assert [call.args for call in run.called("gh", "secret", "set")] == [SECRET_ARGS]
    assert "sonar" in _marks(sandbox)
    assert f"SonarCloud project {key} of organization {organization} is bound" in run.stdout
    assert "[done] SonarCloud configured for" in run.stdout


def test_the_user_types_two_tokens_and_nothing_else(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox, stdin=TYPED)
    assert run.returncode == 0, run.output
    assert len(TYPED.splitlines()) == 2
    body = run.stdout[run.stdout.index("== sonar ==") :]
    assert "Paste the admin token first, then the analysis token." in body
    assert body.index("Paste the admin token first") < body.index("is bound to this repository")


def test_the_secret_reaches_github_on_standard_input_and_no_argument_carries_a_token(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox)
    assert run.returncode == 0, run.output
    writes = stub_log(sandbox, "sonar_gh_writes.jsonl")
    assert writes[-1] == {
        "kind": "secret",
        "name": "SONAR_TOKEN",
        "sha256": sha256(SONAR_ANALYSIS_SENTINEL),
    }
    for call in run.calls:
        assert SONAR_ADMIN_SENTINEL not in " ".join(call.args)
        assert SONAR_ANALYSIS_SENTINEL not in " ".join(call.args)
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


def test_the_admin_token_reaches_no_github_write_no_secret_record_and_no_output(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox)
    assert run.returncode == 0, run.output
    assert stored_secrets(sandbox) == []
    assert sha256(SONAR_ADMIN_SENTINEL) not in str(stub_log(sandbox, "sonar_gh_writes.jsonl"))
    assert SONAR_ADMIN_SENTINEL not in sandbox.profile_file.read_text(encoding="utf-8")
    assert leaks(run.output) == {}
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


def test_the_admin_token_finds_the_project_and_the_analysis_token_is_only_checked_for_its_user(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _sonar(sandbox).returncode == 0
    probes = [(row["path"], row["sha256"]) for row in stub_log(sandbox, "sonar_probes.jsonl")]
    admin, analysis = sha256(SONAR_ADMIN_SENTINEL), sha256(SONAR_ANALYSIS_SENTINEL)
    assert probes == [
        ("/api/users/current", admin),
        ("/api/organizations/search", admin),
        ("/api/projects/search", admin),
        ("/api/alm_settings/get_binding", admin),
        ("/api/settings/values", admin),
        ("/api/users/current", analysis),
    ]


@pytest.mark.parametrize(
    ("scenario", "cause", "instruction"),
    [
        (
            Scenario(sonar_admin_status=401),
            "SonarCloud rejected the admin token",
            "generate a new token as a user who administers the organization",
        ),
        (
            Scenario(sonar_bound_projects=0, sonar_unbound_projects=2),
            "no SonarCloud project is bound to this repository",
            "choose + > Analyze new project",
        ),
        (
            Scenario(sonar_bound_projects=2),
            "several SonarCloud projects are bound to this repository",
            "keep exactly one project bound to this repository",
        ),
    ],
    ids=["rejected-admin-token", "no-bound-project", "several-bound-projects"],
)
def test_a_failed_discovery_writes_nothing_prints_a_browser_instruction_and_resumes_at_sonar(
    tmp_path: Path, scenario: Scenario, cause: str, instruction: str
) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, scenario)
    assert run.returncode == 1
    assert BROWSER in run.stdout
    assert instruction in run.stdout
    assert "re-run scripts/setup_macos.sh --from sonar" in run.stdout.split(BROWSER)[1]
    failed = _failed_lines(run)
    assert len(failed) == 1
    assert cause in failed[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    assert "setup: incomplete: sonar did not complete" in run.stderr
    assert "step sonar did not complete, so it is not marked done" in run.stdout
    _assert_nothing_was_written(sandbox, run, profile)


def test_automatic_analysis_confirmed_off_prints_no_instruction(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox, Scenario(sonar_autoscan="false"))
    assert run.returncode == 0, run.output
    assert "Automatic Analysis" not in run.stdout


@pytest.mark.parametrize("value", ["true", None], ids=["on", "unreported"])
def test_automatic_analysis_not_confirmed_off_prints_the_instruction_without_failing(
    tmp_path: Path, value: str | None
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox, Scenario(sonar_autoscan=value))
    assert run.returncode == 0, run.output
    assert "did not confirm that Automatic Analysis is off" in run.stdout
    assert "turn Automatic Analysis off" in run.stdout
    assert "sonar" in _marks(sandbox)
    assert _failed_lines(run) == []
    assert [call.args for call in run.called("gh", "secret", "set")] == [SECRET_ARGS]


def test_a_token_from_a_different_sonarcloud_user_is_accepted_without_the_flag(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox)
    assert run.returncode == 0, run.output
    assert "same SonarCloud user" not in run.output
    assert "You asked setup to accept an analysis token" not in run.stdout


def test_a_token_from_the_admin_tokens_own_user_fails_the_step_without_the_flag(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, Scenario(sonar_analysis_login=SONAR_ADMIN_LOGIN))
    assert run.returncode == 1
    failed = _failed_lines(run)
    assert len(failed) == 1
    assert "belongs to the same SonarCloud user as the admin token" in failed[0]
    assert "--accept-sonar-user-token" in failed[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    _assert_nothing_was_written(sandbox, run, profile)


def test_the_flag_accepts_the_admin_tokens_own_user_and_prints_the_risk_text(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(sonar_analysis_login=SONAR_ADMIN_LOGIN)
    run = _sonar(sandbox, scenario, TYPED, "--accept-sonar-user-token")
    assert run.returncode == 0, run.output
    assert "You asked setup to accept an analysis token from the same SonarCloud user" in run.stdout
    assert "can do everything that user can" in run.stdout
    assert [call.args for call in run.called("gh", "secret", "set")] == [SECRET_ARGS]
    assert "sonar" in _marks(sandbox)
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


@pytest.mark.parametrize("flags", [(), ("--accept-sonar-user-token",)], ids=["no-flag", "flag"])
def test_a_token_byte_identical_to_the_admin_token_is_refused_even_with_the_flag(
    tmp_path: Path, flags: tuple[str, ...]
) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    twice = f"{SONAR_ADMIN_SENTINEL}\n{SONAR_ADMIN_SENTINEL}\n"
    run = _sonar(sandbox, Scenario(sonar_analysis_login=SONAR_ADMIN_LOGIN), twice, *flags)
    assert run.returncode == 1
    failed = _failed_lines(run)
    assert len(failed) == 1
    assert "the same token as the admin token" in failed[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    _assert_nothing_was_written(sandbox, run, profile)


def test_an_analysis_token_sonarcloud_rejects_writes_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, Scenario(sonar_analysis_status=401))
    assert run.returncode == 1
    assert "SonarCloud rejected the analysis token" in _failed_lines(run)[0]
    _assert_nothing_was_written(sandbox, run, profile)


def test_an_analysis_token_that_belongs_to_no_user_writes_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, Scenario(sonar_analysis_anonymous=True))
    assert run.returncode == 1
    assert "did not say which user the analysis token belongs to" in _failed_lines(run)[0]
    _assert_nothing_was_written(sandbox, run, profile)


@pytest.mark.parametrize(
    ("stdin", "cause"),
    [
        (f"{SONAR_ADMIN_SENTINEL}\n", "no SonarCloud analysis token was entered"),
        ("bad token\n", "does not look like a SonarCloud token"),
    ],
    ids=["no-analysis-token", "malformed"],
)
def test_a_missing_or_malformed_token_fails_the_step_and_writes_nothing(
    tmp_path: Path, stdin: str | None, cause: str
) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, stdin=stdin)
    assert run.returncode == 1
    assert cause in _failed_lines(run)[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    _assert_nothing_was_written(sandbox, run, profile)


def test_a_sonarcloud_that_cannot_be_reached_fails_the_step_and_writes_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, Scenario(sonar_unreachable=True))
    assert run.returncode == 1
    assert "SonarCloud could not be reached" in _failed_lines(run)[0]
    _assert_nothing_was_written(sandbox, run, profile)


def test_a_profile_for_another_repository_is_refused_and_left_byte_identical(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, "other-owner", "other-repo")
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox)
    assert run.returncode == 1
    assert "an installed profile names a different repository" in _failed_lines(run)[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    _assert_nothing_was_written(sandbox, run, profile)


def test_a_missing_profile_fails_the_step_and_writes_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    sandbox.profile_file.unlink()
    run = _sonar(sandbox)
    assert run.returncode == 1
    assert "no installed profile to add the code quality block to" in _failed_lines(run)[0]
    assert _github_writes(run) == []
    assert not sandbox.profile_file.exists()
    assert not sandbox.restart_marker.exists()


@pytest.mark.parametrize(
    ("scenario", "named"),
    [
        (Scenario(gh_variable_fails=True), "the repository variable SONAR_ORGANIZATION"),
        (Scenario(gh_secret_fails=True), "the repository secret SONAR_TOKEN"),
    ],
    ids=["variable", "secret"],
)
def test_a_github_write_that_is_refused_fails_the_step_names_it_and_leaks_no_token(
    tmp_path: Path, scenario: Scenario, named: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox, scenario)
    assert run.returncode == 1
    assert f"GitHub refused to store {named}" in _failed_lines(run)[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    assert "sonar" not in _marks(sandbox)
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


def test_a_refused_github_write_still_restarts_for_the_written_profile_and_the_rerun_finishes(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    failed = _sonar(sandbox, Scenario(gh_variable_fails=True))
    assert failed.returncode == 1
    assert _block(sandbox) is not None
    assert [call.args for call in failed.called("launchctl", "kickstart")] == [KICKSTART]
    assert not sandbox.restart_marker.exists()
    fixed = _sonar(sandbox)
    assert fixed.returncode == 0, fixed.output
    assert [call.args for call in fixed.called("gh", "secret", "set")] == [SECRET_ARGS]
    assert len(fixed.called("gh", "variable", "set")) == 2
    assert fixed.called("launchctl", "kickstart") == []
    assert "sonar" in _marks(sandbox)


def test_a_marker_left_by_an_interrupted_run_is_honoured_by_the_next_sonar_run(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    sandbox.restart_marker.write_text("", encoding="utf-8")
    run = _sonar(sandbox)
    assert run.returncode == 0, run.output
    assert [call.args for call in run.called("launchctl", "kickstart")] == [KICKSTART]
    assert not sandbox.restart_marker.exists()


def test_a_run_that_configures_sonar_restarts_the_service_exactly_once_after_the_write(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox)
    assert run.returncode == 0, run.output
    assert [call.args for call in run.called("launchctl", "kickstart")] == [KICKSTART]
    kick = next(i for i, call in enumerate(run.calls) if call.matches("launchctl", "kickstart"))
    last_write = max(i for i, call in enumerate(run.calls) if call.matches("gh", "secret", "set"))
    health = next(
        i
        for i, call in enumerate(run.calls)
        if i > kick and call.tool == "curl" and call.args[-1].endswith("/api/health")
    )
    preflight = next(
        i
        for i, call in enumerate(run.calls)
        if i > health and call.tool == "curl" and call.args[-1].endswith("/preflight")
    )
    assert last_write < kick < health < preflight
    assert not sandbox.restart_marker.exists()
    assert "[done] restarted ai.alan so it picks up the changed configuration" in run.stdout


def test_a_failed_discovery_leaves_no_marker_and_restarts_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _sonar(sandbox, Scenario(sonar_bound_projects=0))
    assert run.returncode == 1
    assert not sandbox.restart_marker.exists()
    assert run.called("launchctl", "kickstart") == []


def test_a_rerun_with_everything_configured_reports_already_asks_for_nothing_and_writes_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _sonar(sandbox).returncode == 0
    watched = snapshot_files(sandbox.profile_file, sandbox.state_file, sandbox.secrets_file)
    second = _sonar(sandbox, stdin=None)
    assert second.returncode == 0, second.output
    assert "[already] SonarCloud configuration for" in second.stdout
    assert "already configured" in second.stdout
    assert mutating_calls(second) == []
    assert second.called("launchctl", "kickstart") == []
    assert [call for call in second.called("curl") if "sonarcloud.io" in call.args[-1]] == []
    assert snapshot_files(*watched) == watched
    assert "Paste the admin token" not in second.stdout


def test_a_rerun_with_a_missing_secret_asks_for_the_tokens_again(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert _sonar(sandbox).returncode == 0
    state = sandbox.stub_root / "state" / "sonar_gh_state.json"
    state.write_text('{"variables": {}, "secrets": {}}', encoding="utf-8")
    second = _sonar(sandbox)
    assert second.returncode == 0, second.output
    assert [call.args for call in second.called("gh", "secret", "set")] == [SECRET_ARGS]
    assert len(second.called("gh", "variable", "set")) == 2


def test_from_sonar_leaves_the_restart_to_the_verifier_which_restarts_once_after_its_store(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = run_setup(sandbox, "--from", "sonar", stdin=f"{TYPED}{BOT_SENTINEL}\n")
    assert run.returncode == 0, run.output
    assert run.headers() == ["sonar", "verifier", "updater"]
    assert [call.args for call in run.called("launchctl", "kickstart")] == [KICKSTART]
    kick = next(i for i, call in enumerate(run.calls) if call.matches("launchctl", "kickstart"))
    reviewer = next(
        i
        for i, call in enumerate(run.calls)
        if call.tool == "curl" and call.args[-1].endswith("/api/secrets") and "POST" in call.args
    )
    secret = next(i for i, call in enumerate(run.calls) if call.matches("gh", "secret", "set"))
    assert secret < reviewer < kick
    assert not sandbox.restart_marker.exists()
    assert_no_secret(run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL, BOT_SENTINEL)


def test_a_failed_sonar_step_does_not_stop_the_verifier_which_finishes_the_owed_restart(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(gh_secret_fails=True)
    run = run_setup(sandbox, "--from", "sonar", scenario=scenario, stdin=f"{TYPED}{BOT_SENTINEL}\n")
    assert run.returncode == 1
    assert run.headers() == ["sonar", "verifier", "updater"]
    assert run.stderr.strip().splitlines()[-1] == RESUME_SONAR
    assert "verifier" in _marks(sandbox)
    assert "sonar" not in _marks(sandbox)
    assert [call.args for call in run.called("launchctl", "kickstart")] == [KICKSTART]
    assert not sandbox.restart_marker.exists()


def test_a_full_run_asks_for_the_transport_the_two_sonar_and_the_bot_token_in_that_order(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, PLANTED)
    default_profile_writer.write_profile(sandbox.alan_dir / "engines", OWNER, "example-repo")
    scenario = Scenario(transport_stored=False, token_login=OWNER)
    stdin = f"{TRANSPORT_SENTINEL}\n{TYPED}{BOT_SENTINEL}\n"
    run = run_setup(sandbox, "--from", "credential", scenario=scenario, stdin=stdin)
    assert run.returncode == 0, run.output
    assert run.headers() == [
        "credential",
        "harden",
        "protect",
        "confirm",
        "sonar",
        "verifier",
        "updater",
    ]
    assert [row["purpose"] for row in stored_secrets(sandbox)] == ["transport", "reviewer"]
    assert [call.args for call in run.called("gh", "secret", "set")] == [SECRET_ARGS]
    assert len(run.called("launchctl", "kickstart")) == 1
    assert_no_secret(
        run, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL, BOT_SENTINEL, TRANSPORT_SENTINEL
    )


def test_the_checklist_names_both_sonarcloud_tokens_before_the_first_step(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "preflight")
    checklist = run.stdout[: run.stdout.index("== preflight ==")]
    assert "create two SonarCloud tokens, an admin token and an analysis token" in checklist
    assert "the sonar step asks for both" in checklist
    assert "optionally configures SonarCloud" in checklist
    assert "4. Optional: import your repository into SonarCloud" in checklist
    assert "Press Enter at its first prompt, or pass --no-sonar, to skip SonarCloud" in checklist
    assert "mandatory" not in checklist


def test_the_uninstall_list_names_the_two_tokens_and_the_repository_copies(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert "the two SonarCloud tokens (revoke both in SonarCloud)" in run.stdout
    assert "Actions settings until you delete them" in run.stdout


def test_the_sonar_flag_is_accepted_by_the_engine_and_a_lookalike_is_a_usage_error(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    accepted = run_setup(sandbox, "--only", "preflight", "--accept-sonar-user-token")
    assert accepted.returncode == 0, accepted.output
    refused = run_setup(sandbox, "--only", "preflight", "--accept-sonar-token")
    assert refused.returncode == 2
    assert "unknown flag: --accept-sonar-token" in refused.stderr
    assert refused.calls == ()


def test_the_flag_needs_no_value_and_does_not_swallow_the_next_argument(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--accept-sonar-user-token", "--only", "preflight")
    assert run.returncode == 0, run.output
    assert run.headers() == ["preflight"]
