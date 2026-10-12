from __future__ import annotations

from pathlib import Path

import pytest

from alan.setup_transport import PERMISSIONS_TEXT, RISK_TEXT
from tests._setup_api_stubs import sha256
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
    mutating_calls,
    run_setup,
    seed_bound_service,
    stored_secrets,
    stub_log,
)

FRESH = Scenario(transport_stored=False)
TYPED = TRANSPORT_SENTINEL + "\n"
CLASSIC_SHAPED = "gh" + "p_" + "0123456789abcdef"
FINE_GRAINED_SHAPED = "github" + "_pat_" + "0123456789abcdef"
ALL_SENTINELS = (TRANSPORT_SENTINEL, AMBIENT_SENTINEL, TOKEN_SENTINEL)


def _ready(tmp_path: Path, origin: str | None = None) -> Sandbox:
    sandbox = make_sandbox(tmp_path) if origin is None else make_sandbox(tmp_path, origin=origin)
    seed_bound_service(sandbox)
    return sandbox


def _credential(
    sandbox: Sandbox,
    scenario: Scenario = FRESH,
    stdin: str | None = TYPED,
    *args: str,
) -> SetupRun:
    return run_setup(sandbox, "--only", "credential", *args, scenario=scenario, stdin=stdin)


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _posts(run: SetupRun) -> list[str]:
    return [call.args[-1] for call in mutating_calls(run) if call.tool == "curl"]


def test_a_fine_grained_token_is_verified_stored_and_marked(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox)
    assert run.returncode == 0, run.output
    assert f"[done] transport credential stored for {ENGINE_SLUG}" in run.stdout
    assert stored_secrets(sandbox) == [
        {"engine": ENGINE_SLUG, "purpose": "transport", "sha256": sha256(TRANSPORT_SENTINEL)}
    ]
    assert _posts(run) == ["http://127.0.0.1:8585/api/secrets"]
    assert "credential" in _marks(sandbox)


def test_the_permissions_and_the_reason_for_administration_are_printed_first(
    tmp_path: Path,
) -> None:
    run = _credential(_ready(tmp_path))
    assert PERMISSIONS_TEXT in run.stdout
    assert run.stdout.index(PERMISSIONS_TEXT) < run.stdout.index("[done]")
    assert RISK_TEXT not in run.stdout


def test_the_token_is_checked_once_and_the_checked_value_is_the_typed_one(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox)
    assert [call.args for call in run.called("gh", "api", "-i")] == [("api", "-i", "user")]
    assert stub_log(sandbox, "identity_probes.jsonl") == [{"sha256": sha256(TRANSPORT_SENTINEL)}]


def test_no_secret_reaches_any_output_argument_or_stub_log(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path))
    assert run.returncode == 0, run.output
    assert_no_secret(run, *ALL_SENTINELS)


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        (Scenario(transport_stored=False, identity_kind="classic"), "not a fine-grained"),
        (Scenario(transport_stored=False, token_login="someone-else"), "different GitHub account"),
        (Scenario(transport_stored=False, identity_kind="rejected"), "GitHub rejected the token"),
        (Scenario(transport_stored=False, identity_kind="no-login"), "named no account"),
    ],
    ids=["classic", "other-account", "rejected", "no-login"],
)
def test_a_refused_token_stores_nothing_and_names_only_the_reason(
    tmp_path: Path, scenario: Scenario, reason: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, scenario)
    assert run.returncode == 1
    assert "refused at credential: transport-token:" in run.stderr
    assert reason in run.stderr
    assert stored_secrets(sandbox) == []
    assert _posts(run) == []
    assert "credential" not in _marks(sandbox)
    assert "someone-else" not in run.output
    assert_no_secret(run, *ALL_SENTINELS)


@pytest.mark.parametrize("stdin", ["\n", "   \n", ""], ids=["blank-line", "spaces", "eof"])
def test_empty_input_and_end_of_file_are_refused_with_the_way_out(
    tmp_path: Path, stdin: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, FRESH, stdin)
    assert run.returncode == 1
    assert "no token was entered" in run.stderr
    assert "--use-gh-token" in run.stderr
    assert stored_secrets(sandbox) == []
    assert run.called("gh", "api", "-i") == []


def test_end_of_file_from_a_closed_stdin_is_refused(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path), FRESH, None)
    assert run.returncode == 1
    assert "no token was entered" in run.stderr


def test_the_token_type_comes_from_githubs_answer_and_never_from_the_prefix(
    tmp_path: Path,
) -> None:
    accepted = _ready(tmp_path / "accepted")
    fine = _credential(accepted, FRESH, CLASSIC_SHAPED + "\n")
    assert fine.returncode == 0, fine.output
    refused = _ready(tmp_path / "refused")
    scenario = Scenario(transport_stored=False, identity_kind="classic")
    classic = _credential(refused, scenario, FINE_GRAINED_SHAPED + "\n")
    assert classic.returncode == 1
    assert "not a fine-grained" in classic.stderr
    assert_no_secret(classic, FINE_GRAINED_SHAPED)
    assert_no_secret(fine, CLASSIC_SHAPED)


def test_a_stored_credential_is_reported_already_and_nothing_is_asked_or_written(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(), TYPED)
    assert run.returncode == 0, run.output
    assert f"[already] transport credential for {ENGINE_SLUG}" in run.stdout
    assert PERMISSIONS_TEXT not in run.stdout
    assert mutating_calls(run) == []
    assert run.called("gh", "api", "-i") == []
    assert stored_secrets(sandbox) == []


def test_a_second_run_after_a_store_asks_for_nothing_and_overwrites_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _credential(sandbox).returncode == 0
    second = _credential(sandbox, FRESH, "another-value\n")
    assert second.returncode == 0, second.output
    assert f"[already] transport credential for {ENGINE_SLUG}" in second.stdout
    assert mutating_calls(second) == []
    assert len(stored_secrets(sandbox)) == 1
    assert_no_secret(second, "another-value")


def test_the_gh_token_is_stored_only_with_the_flag_and_the_risk_is_printed_first(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, FRESH, None, "--use-gh-token")
    assert run.returncode == 0, run.output
    assert RISK_TEXT in run.stdout
    assert run.stdout.index(RISK_TEXT) < run.stdout.index("[done]")
    assert stored_secrets(sandbox) == [
        {"engine": ENGINE_SLUG, "purpose": "transport", "sha256": sha256(AMBIENT_SENTINEL)}
    ]
    assert run.called("gh", "api", "-i") == []
    assert PERMISSIONS_TEXT not in run.stdout
    assert_no_secret(run, *ALL_SENTINELS)


def test_the_gh_session_token_is_piped_from_gh_and_the_signed_in_user_is_never_asked(
    tmp_path: Path,
) -> None:
    run = _credential(_ready(tmp_path), FRESH, None, "--use-gh-token")
    assert [call.args for call in run.called("gh", "auth")] == [("auth", "token")]
    assert run.called("gh", "api", "user") == []


def test_an_unreadable_signed_in_user_is_refused_before_the_token_is_taken(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(transport_stored=False, gh_api_fails=True))
    assert run.returncode == 1
    assert "refused at credential: signed-in-user:" in run.stderr
    assert run.called("gh", "api", "-i") == []
    assert stored_secrets(sandbox) == []
    assert_no_secret(run, *ALL_SENTINELS)


def test_the_flag_changes_nothing_when_a_credential_is_already_stored(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path), Scenario(), None, "--use-gh-token")
    assert run.returncode == 0, run.output
    assert RISK_TEXT not in run.stdout
    assert mutating_calls(run) == []


def test_an_unreadable_gh_session_is_refused_when_the_flag_is_used(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(transport_stored=False, ambient_token_fails=True)
    run = _credential(sandbox, scenario, None, "--use-gh-token")
    assert run.returncode == 1
    assert "the gh session token cannot be read" in run.stderr
    assert stored_secrets(sandbox) == []


def test_the_token_and_header_files_are_private_and_the_directory_is_removed(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox)
    assert run.returncode == 0, run.output
    rows = stub_log(sandbox, "private_file_modes.jsonl")
    assert {"kind": "stdin", "mode": 0o600} in rows
    headers = [row for row in rows if row["kind"] == "header"]
    assert headers
    assert {(row["mode"], row["dir_mode"]) for row in headers} == {(0o600, 0o700)}
    assert list(sandbox.tmp_dir.iterdir()) == []


def test_the_directory_is_removed_after_a_refusal_too(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(transport_stored=False, identity_kind="classic"))
    assert run.returncode == 1
    assert list(sandbox.tmp_dir.iterdir()) == []


def test_the_secret_is_stored_under_the_engine_slug_in_the_case_the_service_holds(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, "https://github.com/Example-Owner/Example-Repo.git")
    run = _credential(sandbox)
    assert run.returncode == 0, run.output
    assert [row["engine"] for row in stored_secrets(sandbox)] == ["Example-Owner/Example-Repo"]


def test_a_rejected_store_reports_the_status_and_never_the_response_body(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(transport_stored=False, secret_post_status=422))
    assert run.returncode == 1
    assert "HTTP 422" in run.stderr
    assert "storing the transport credential" in run.stderr
    assert_no_secret(run, *ALL_SENTINELS)


def test_an_unreadable_secret_list_is_refused_before_anything_is_asked_or_written(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(secrets_list_garbled=True))
    assert run.returncode == 1
    assert "will not risk overwriting" in run.stderr
    assert mutating_calls(run) == []
    assert PERMISSIONS_TEXT not in run.stdout


def test_a_service_that_does_not_answer_is_refused(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path), Scenario(api_reachable=False))
    assert run.returncode == 1
    assert "refused at credential: local-api:" in run.stderr
    assert "run the start step first" in run.stderr


def test_an_engine_the_service_does_not_know_is_refused(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path), Scenario(engine_registered=False))
    assert run.returncode == 1
    assert "refused at credential: engine:" in run.stderr
    assert "run the profile step" in run.stderr


def test_an_engine_without_a_forge_is_refused(tmp_path: Path) -> None:
    run = _credential(_ready(tmp_path), Scenario(engine_has_forge=False))
    assert run.returncode == 1
    assert "names no forge repository" in run.stderr


@pytest.mark.parametrize("status", [401, 403])
def test_a_control_token_the_service_does_not_accept_is_refused(
    tmp_path: Path, status: int
) -> None:
    sandbox = _ready(tmp_path)
    run = _credential(sandbox, Scenario(transport_stored=False, control_token_status=status))
    assert run.returncode == 1
    assert "refused at credential: control-token:" in run.stderr
    assert f"HTTP {status}" in run.stderr
    assert stored_secrets(sandbox) == []
    assert_no_secret(run, *ALL_SENTINELS)


def test_a_missing_control_token_is_refused_with_the_way_forward(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    sandbox.token_file.unlink()
    run = _credential(sandbox)
    assert run.returncode == 1
    assert "refused at credential: control-token:" in run.stderr
    assert "run the start step" in run.stderr
    assert run.called("curl") == []


def test_a_missing_virtualenv_is_refused(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    (sandbox.checkout / ".venv" / "bin" / "python").unlink()
    run = _credential(sandbox)
    assert run.returncode == 1
    assert "refused at credential: venv:" in run.stderr


def test_an_origin_that_is_not_a_github_repository_is_refused(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path, "https://gitlab.example/x/y.git")
    run = _credential(sandbox)
    assert run.returncode == 1
    assert "refused at credential: origin:" in run.stderr
    assert run.called("curl") == []
