from __future__ import annotations

import stat
from pathlib import Path

import pytest

from tests._setup_api_stubs import sha256
from tests._setup_support import (
    BOT_LOGIN,
    BOT_SENTINEL,
    ENGINE_SLUG,
    OWNER,
    TRANSPORT_SENTINEL,
    Sandbox,
    Scenario,
    SetupRun,
    assert_no_secret,
    gh_writes,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_running_service,
    seed_secrets_env,
    snapshot_files,
    stored_secrets,
    stub_log,
)

PLANTED_SECRET = "never-echoed-never-echoed"  # noqa: S105
PLANTED = (
    f"EXAMPLE_KEY=planted-value-1\nEXAMPLE_SECRET={PLANTED_SECRET}\n# a note\nEXAMPLE_LAST=x\n"
)
LOGIN_LINE = f"ALAN_VERIFIER_LOGIN={BOT_LOGIN}\n"
TYPED = BOT_SENTINEL + "\n"
RESUME_VERIFIER = "Resume with: scripts/setup_macos.sh --from verifier"


def _ready(tmp_path: Path, planted: str | None = PLANTED) -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    seed_running_service(sandbox)
    if planted is not None:
        seed_secrets_env(sandbox, planted)
    return sandbox


def _verify(
    sandbox: Sandbox, scenario: Scenario | None = None, stdin: str | None = TYPED
) -> SetupRun:
    return run_setup(sandbox, "--only", "verifier", scenario=scenario, stdin=stdin)


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _secrets_text(sandbox: Sandbox) -> str:
    return sandbox.secrets_file.read_text(encoding="utf-8")


def _assert_nothing_was_stored(sandbox: Sandbox, run: SetupRun, planted: str = PLANTED) -> None:
    assert stored_secrets(sandbox) == []
    assert _secrets_text(sandbox) == planted
    assert not sandbox.restart_marker.exists()
    assert run.called("launchctl", "kickstart") == []
    assert "verifier" not in _marks(sandbox)
    assert_no_secret(run, BOT_SENTINEL, PLANTED_SECRET)


def test_a_classic_distinct_write_capable_bot_token_is_stored_as_the_reviewer_record(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox)
    assert run.returncode == 0, run.output
    assert stored_secrets(sandbox) == [
        {
            "engine": ENGINE_SLUG,
            "purpose": "reviewer",
            "sha256": sha256(BOT_SENTINEL),
            "expires_at": "2099-06-30",
        }
    ]
    assert f"[done] reviewer credential stored for {ENGINE_SLUG}" in run.stdout
    assert "verifier" in _marks(sandbox)
    assert {row["sha256"] for row in stub_log(sandbox, "bot_probes.jsonl")} == {
        sha256(BOT_SENTINEL)
    }
    assert_no_secret(run, BOT_SENTINEL, PLANTED_SECRET)


def test_a_writable_bot_is_checked_for_identity_and_repository_access_only(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _verify(sandbox).returncode == 0
    probes = [row["call"] for row in stub_log(sandbox, "bot_probes.jsonl")]
    assert probes == ["identity", "repository"]


def test_the_instructions_are_printed_before_the_token_is_asked_for(tmp_path: Path) -> None:
    run = _verify(_ready(tmp_path))
    assert "classic personal access token" in run.stdout
    assert "Write role" in run.stdout
    assert "A fine-grained token does not work" in run.stdout
    assert run.stdout.index("classic personal access token") < run.stdout.index("[done]")


@pytest.mark.parametrize(
    ("reported", "stored"),
    [
        ("2099-06-30 18:30:00 UTC", "2099-06-30"),
        ("2099-01-01 01:30:00 +0300", "2098-12-31"),
        ("2099-01-01 23:30:00 -03:00", "2099-01-02"),
    ],
    ids=["utc", "east-offset-crosses-midnight", "west-offset-crosses-midnight"],
)
def test_the_stored_expiry_is_the_utc_calendar_date_githubs_answer_reports(
    tmp_path: Path, reported: str, stored: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(bot_expiry=reported))
    assert run.returncode == 0, run.output
    assert [row["expires_at"] for row in stored_secrets(sandbox)] == [stored]
    assert f"The token expires on {stored}" in run.stdout
    assert "no expiry reminder will fire" not in run.stdout


def test_a_token_with_no_expiry_stores_none_and_says_no_reminder_will_fire(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(bot_expiry=None))
    assert run.returncode == 0, run.output
    assert len(stored_secrets(sandbox)) == 1
    assert "expires_at" not in stored_secrets(sandbox)[0]
    assert "no expiry reminder will fire" in run.stdout


def test_an_expiry_setup_cannot_read_stores_nothing(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(bot_expiry="someday"))
    assert run.returncode == 1
    assert "[failed] GitHub reported an expiry for the token that setup cannot read" in run.stdout
    _assert_nothing_was_stored(sandbox, run)


@pytest.mark.parametrize(
    ("scenario", "cause"),
    [
        (Scenario(bot_identity_kind="rejected"), "GitHub rejected the token"),
        (Scenario(bot_identity_kind="fine-grained"), "the token is a fine-grained personal access"),
        (Scenario(bot_login=OWNER), "authenticates as the signed-in GitHub account"),
        (Scenario(bot_login=OWNER.upper()), "authenticates as the signed-in GitHub account"),
        (Scenario(bot_permission="read"), "the bot account has no write access"),
        (Scenario(bot_permission="read", bot_invited=True), "has not accepted its invitation"),
        (Scenario(bot_repo_visible=False), "the bot account has no write access"),
        (
            Scenario(bot_repo_visible=False, bot_invited=True),
            "has not accepted its invitation",
        ),
    ],
    ids=[
        "rejected",
        "fine-grained",
        "authors-own-account",
        "authors-own-account-any-case",
        "read-only",
        "invitation-pending",
        "repository-not-visible",
        "invitation-pending-on-a-private-repository",
    ],
)
def test_a_token_that_fails_a_check_stores_nothing_and_names_the_cause_and_the_resume_step(
    tmp_path: Path, scenario: Scenario, cause: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, scenario)
    assert run.returncode == 1
    failed = [line for line in run.stdout.splitlines() if line.startswith("  [failed] ")]
    assert len(failed) == 1
    assert cause in failed[0]
    assert run.stderr.strip().splitlines()[-1] == RESUME_VERIFIER
    assert "setup: incomplete: verifier did not complete" in run.stderr
    assert "step verifier did not complete, so it is not marked done" in run.stdout
    _assert_nothing_was_stored(sandbox, run)


def test_a_failure_never_names_either_account(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(bot_login="some-other-bot", bot_permission="read"))
    assert "some-other-bot" not in run.output
    assert OWNER not in run.output.replace("example-owner/example-repo", "")


@pytest.mark.parametrize("stdin", ["\n", "  \n", "", None], ids=["blank", "spaces", "eof", "none"])
def test_no_token_on_standard_input_fails_the_step_with_the_way_out(
    tmp_path: Path, stdin: str | None
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, stdin=stdin)
    assert run.returncode == 1
    assert "[failed] no verifier token was entered" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_VERIFIER
    assert run.called("gh", "api", "-i") == []
    _assert_nothing_was_stored(sandbox, run)


def test_the_secrets_file_gets_exactly_one_login_line_and_every_other_line_is_untouched(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    seed_secrets_env(sandbox, PLANTED, mode=0o644)
    run = _verify(sandbox)
    assert run.returncode == 0, run.output
    text = _secrets_text(sandbox)
    assert text == PLANTED + LOGIN_LINE
    assert text.count("ALAN_VERIFIER_LOGIN=") == 1
    assert stat.S_IMODE(sandbox.secrets_file.stat().st_mode) == 0o600
    assert "planted-value-1" not in run.output
    assert PLANTED_SECRET not in run.output
    assert "[done] recorded the verifier login in the secrets file" in run.stdout
    assert_no_secret(run, BOT_SENTINEL, PLANTED_SECRET)


def test_an_existing_login_line_is_replaced_in_place_and_duplicates_are_dropped(
    tmp_path: Path,
) -> None:
    planted = "A=1\nALAN_VERIFIER_LOGIN=old-bot\nB=2\nALAN_VERIFIER_LOGIN=older-bot\nC=3\n"
    sandbox = _ready(tmp_path, planted)
    run = _verify(sandbox)
    assert run.returncode == 0, run.output
    assert _secrets_text(sandbox) == f"A=1\n{LOGIN_LINE}B=2\nC=3\n"


def test_a_secrets_file_that_does_not_exist_yet_is_created_with_mode_600(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, planted=None)
    assert not sandbox.secrets_file.exists()
    run = _verify(sandbox)
    assert run.returncode == 0, run.output
    assert _secrets_text(sandbox) == LOGIN_LINE
    assert stat.S_IMODE(sandbox.secrets_file.stat().st_mode) == 0o600


def test_a_secrets_file_that_is_a_symbolic_link_is_refused_before_any_store(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, planted=None)
    target = sandbox.elsewhere() / "linked-secrets.env"
    target.write_text(PLANTED, encoding="utf-8")
    sandbox.secrets_file.symlink_to(target)
    run = _verify(sandbox)
    assert run.returncode == 1
    assert "refused at verifier: secrets-file:" in run.stderr
    assert "symbolic link" in run.stderr
    assert target.read_text(encoding="utf-8") == PLANTED
    assert stored_secrets(sandbox) == []
    assert_no_secret(run, BOT_SENTINEL, PLANTED_SECRET)


def test_a_stored_reviewer_with_a_recorded_login_is_reported_already_and_nothing_is_asked(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, PLANTED + LOGIN_LINE)
    before = snapshot_files(sandbox.secrets_file)
    run = _verify(sandbox, Scenario(reviewer_stored=True), "another-value\n")
    assert run.returncode == 0, run.output
    assert f"[already] reviewer credential and verifier login for {ENGINE_SLUG}" in run.stdout
    assert mutating_calls(run) == []
    assert run.called("gh") == []
    assert snapshot_files(sandbox.secrets_file) == before
    assert "verifier" in _marks(sandbox)
    assert "classic personal access token" not in run.stdout
    assert_no_secret(run, "another-value")


def test_a_stored_reviewer_without_a_recorded_login_is_asked_for_again(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(reviewer_stored=True))
    assert run.returncode == 0, run.output
    assert _secrets_text(sandbox) == PLANTED + LOGIN_LINE
    assert [row["purpose"] for row in stored_secrets(sandbox)] == ["reviewer"]


def test_two_logins_on_separate_lines_count_as_no_recorded_login(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path, PLANTED + LOGIN_LINE + "ALAN_VERIFIER_LOGIN=other\n")
    run = _verify(sandbox, Scenario(reviewer_stored=True))
    assert run.returncode == 0, run.output
    assert _secrets_text(sandbox) == PLANTED + LOGIN_LINE


def test_a_rejected_store_reports_the_status_and_never_the_response_body(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(secret_post_status=422))
    assert run.returncode == 1
    assert "HTTP 422" in run.stderr
    assert "storing the reviewer credential" in run.stderr
    assert stored_secrets(sandbox) == []
    assert_no_secret(run, BOT_SENTINEL, PLANTED_SECRET)


def test_an_unreadable_signed_in_user_is_refused_before_the_token_is_taken(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(gh_api_fails=True))
    assert run.returncode == 1
    assert "refused at verifier: signed-in-user:" in run.stderr
    assert run.called("gh", "api", "-i") == []
    _assert_nothing_was_stored(sandbox, run)


@pytest.mark.parametrize("status", [401, 403])
def test_a_control_token_the_service_does_not_accept_is_refused(
    tmp_path: Path, status: int
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(control_token_status=status))
    assert run.returncode == 1
    assert "refused at verifier: control-token:" in run.stderr
    assert f"HTTP {status}" in run.stderr
    assert stored_secrets(sandbox) == []


def test_a_service_that_does_not_answer_is_refused_before_anything_is_asked(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _verify(sandbox, Scenario(api_reachable=False))
    assert run.returncode == 1
    assert "refused at verifier: local-api:" in run.stderr
    assert run.called("gh", "api", "-i") == []


def test_the_verifier_step_never_writes_to_github(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert _verify(sandbox).returncode == 0
    assert gh_writes(sandbox) == []


def test_one_piped_standard_input_carries_the_transport_and_the_bot_token_in_order(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, PLANTED)
    scenario = Scenario(transport_stored=False, token_login=OWNER)
    run = run_setup(
        sandbox, "--from", "credential", scenario=scenario,
        stdin=TRANSPORT_SENTINEL + "\n" + TYPED,
    )
    assert run.returncode == 0, run.output
    assert run.headers() == ["credential", "harden", "protect", "confirm", "verifier", "updater"]
    assert stored_secrets(sandbox) == [
        {"engine": ENGINE_SLUG, "purpose": "transport", "sha256": sha256(TRANSPORT_SENTINEL)},
        {
            "engine": ENGINE_SLUG,
            "purpose": "reviewer",
            "sha256": sha256(BOT_SENTINEL),
            "expires_at": "2099-06-30",
        },
    ]
    assert_no_secret(run, BOT_SENTINEL, TRANSPORT_SENTINEL, PLANTED_SECRET)
