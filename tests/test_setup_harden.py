from __future__ import annotations

from pathlib import Path

import pytest

from tests._setup_support import (
    ENGINE_SLUG,
    Sandbox,
    Scenario,
    SetupRun,
    gh_writes,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_bound_service,
)

REPO_ENDPOINT = f"repos/{ENGINE_SLUG}"
LIMITS_ENDPOINT = f"repos/{ENGINE_SLUG}/interaction-limits"
SCANNING_BODY = {"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}
PUSH_PROTECTION_BODY = {
    "security_and_analysis": {"secret_scanning_push_protection": {"status": "enabled"}}
}
LIMITS_BODY = {"limit": "collaborators_only", "expiry": "six_months"}
POLICY_BODY = {"pull_request_creation_policy": "collaborators_only"}
UNSET = Scenario(
    scanning="disabled", push_protection="disabled", interaction_limit=None, pr_policy="all"
)
SCANNING = "secret scanning"
PUSH_PROTECTION = "push protection"
LIMITS = "collaborator-only interaction limits (six months)"
POLICY = "pull-request creation limited to collaborators"
RESUME_HARDEN = "Resume with: scripts/setup_macos.sh --from harden"


def _ready(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path)
    seed_bound_service(sandbox)
    return sandbox


def _harden(sandbox: Sandbox, scenario: Scenario = UNSET, *args: str) -> SetupRun:
    return run_setup(sandbox, "--only", "harden", *args, scenario=scenario)


def _writes(sandbox: Sandbox) -> list[tuple[str, str, object]]:
    return [(str(w["method"]), str(w["endpoint"]), w["body"]) for w in gh_writes(sandbox)]


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_a_public_copy_gets_all_four_settings_written_in_order_and_read_back(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox)
    assert run.returncode == 0, run.output
    assert _writes(sandbox) == [
        ("PATCH", REPO_ENDPOINT, SCANNING_BODY),
        ("PATCH", REPO_ENDPOINT, PUSH_PROTECTION_BODY),
        ("PUT", LIMITS_ENDPOINT, LIMITS_BODY),
        ("PATCH", REPO_ENDPOINT, POLICY_BODY),
    ]
    for label in (SCANNING, PUSH_PROTECTION, LIMITS, POLICY):
        assert f"[done] {label} set and read back" in run.stdout
    assert "harden" in _marks(sandbox)


def test_the_settings_are_read_before_any_write_and_again_after_the_writes(
    tmp_path: Path,
) -> None:
    run = _harden(_ready(tmp_path))
    kinds = ["write" if call in mutating_calls(run) else "read" for call in run.called("gh", "api")]
    assert kinds[0] == "read"
    assert kinds[-1] == "read"
    assert kinds.count("write") == 4
    endpoints = [call.args[1] for call in run.called("gh", "api")]
    assert endpoints[:2] == [REPO_ENDPOINT, LIMITS_ENDPOINT]


def test_an_immediate_second_run_makes_no_github_write(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert _harden(sandbox).returncode == 0
    writes_after_first = len(gh_writes(sandbox))
    second = _harden(sandbox)
    assert second.returncode == 0, second.output
    assert mutating_calls(second) == []
    assert len(gh_writes(sandbox)) == writes_after_first
    for label in (SCANNING, PUSH_PROTECTION, LIMITS, POLICY):
        assert f"[already] {label}" in second.stdout


def test_a_bound_copy_with_every_setting_in_place_is_left_alone(tmp_path: Path) -> None:
    run = _harden(_ready(tmp_path), Scenario())
    assert run.returncode == 0, run.output
    assert mutating_calls(run) == []
    assert run.stdout.count("[already]") == 4


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_public_only_settings_are_never_attempted_on_a_copy_that_is_not_public(
    tmp_path: Path, visibility: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(visibility=visibility, pr_policy="all"))
    assert run.returncode == 0, run.output
    for label in (SCANNING, PUSH_PROTECTION, LIMITS):
        assert f"[unavailable] {label}: the repository is not public" in run.stdout
    assert _writes(sandbox) == [("PATCH", REPO_ENDPOINT, POLICY_BODY)]
    assert run.called("gh", "api", LIMITS_ENDPOINT) == []


def test_the_pr_policy_is_attempted_and_read_back_on_a_private_copy(tmp_path: Path) -> None:
    run = _harden(_ready(tmp_path), Scenario(visibility="private", pr_policy="all"))
    assert f"[done] {POLICY} set and read back" in run.stdout


def test_a_setting_github_does_not_offer_is_reported_unavailable_and_never_written(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(
        offers_security_settings=False, interaction_limit="collaborators_only", pr_policy="all"
    )
    run = _harden(sandbox, scenario)
    assert run.returncode == 0, run.output
    assert (
        f"[unavailable] {SCANNING}: GitHub does not offer secret scanning for this "
        "repository" in run.stdout
    )
    assert (
        f"[unavailable] {PUSH_PROTECTION}: GitHub does not offer push protection for this "
        "repository" in run.stdout
    )
    assert _writes(sandbox) == [("PATCH", REPO_ENDPOINT, POLICY_BODY)]


def test_only_the_missing_feature_is_unavailable_when_github_omits_just_that_one(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(push_protection=None))
    assert run.returncode == 0, run.output
    assert f"[unavailable] {PUSH_PROTECTION}" in run.stdout
    assert f"[already] {SCANNING}" in run.stdout
    assert mutating_calls(run) == []


@pytest.mark.parametrize("held", [None, "existing_users"])
def test_interaction_limits_are_renewed_only_when_they_are_missing_or_not_collaborators_only(
    tmp_path: Path, held: str | None
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(interaction_limit=held))
    assert run.returncode == 0, run.output
    assert _writes(sandbox) == [("PUT", LIMITS_ENDPOINT, LIMITS_BODY)]


def test_interaction_limits_that_hold_are_not_renewed(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(interaction_limit="collaborators_only"))
    assert run.returncode == 0, run.output
    assert _writes(sandbox) == []


def test_a_pr_policy_that_will_not_read_back_fails_the_run_with_a_browser_instruction(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(pr_policy="all", pr_policy_sticks=False))
    assert run.returncode == 1
    assert f"https://github.com/{ENGINE_SLUG}/settings" in run.stdout
    assert f"[failed] {POLICY} is unverified" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_HARDEN
    assert "harden" not in _marks(sandbox)


def test_the_resume_re_attempts_only_the_setting_whose_read_back_failed(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    scenario = Scenario(pr_policy="all", pr_policy_sticks=False)
    assert _harden(sandbox, scenario).returncode == 1
    first = len(gh_writes(sandbox))
    again = _harden(sandbox, scenario)
    assert again.returncode == 1
    assert len(gh_writes(sandbox)) == first + 1
    assert _writes(sandbox)[-1] == ("PATCH", REPO_ENDPOINT, POLICY_BODY)
    assert f"[already] {SCANNING}" in again.stdout


def test_a_refused_write_is_reported_and_the_read_back_fails_the_setting(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(scanning="disabled", writes_fail=("secret_scanning",)))
    assert run.returncode == 1
    assert f"[failed] GitHub refused the write for {SCANNING}" in run.stdout
    assert f"[failed] {SCANNING} is not confirmed" in run.stdout
    assert f"[already] {PUSH_PROTECTION}" in run.stdout
    assert "harden" not in _marks(sandbox)


def test_a_refused_pr_policy_write_still_prints_the_browser_instruction(tmp_path: Path) -> None:
    run = _harden(_ready(tmp_path), Scenario(pr_policy="all", writes_fail=("pr_policy",)))
    assert run.returncode == 1
    assert "GitHub refused the write" in run.stdout
    assert f"https://github.com/{ENGINE_SLUG}/settings" in run.stdout


def test_interaction_limits_that_cannot_be_read_are_treated_as_missing_and_fail_the_read_back(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(limits_read_fails=True))
    assert run.returncode == 1
    assert _writes(sandbox) == [("PUT", LIMITS_ENDPOINT, LIMITS_BODY)]
    assert f"[failed] {LIMITS} is not confirmed" in run.stdout


def test_a_repository_gh_cannot_read_fails_the_step_without_any_write(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _harden(sandbox, Scenario(repo_read_fails=True))
    assert run.returncode == 1
    assert "would not show the settings" in run.stdout
    assert _writes(sandbox) == []
    assert "harden" not in _marks(sandbox)


def test_hardening_targets_the_repository_the_service_is_bound_to(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, origin="https://github.com/Example-Owner/Example-Repo.git")
    seed_bound_service(sandbox)
    run = _harden(sandbox, UNSET)
    assert run.returncode == 0, run.output
    assert {endpoint for _, endpoint, _ in _writes(sandbox)} == {
        "repos/Example-Owner/Example-Repo",
        "repos/Example-Owner/Example-Repo/interaction-limits",
    }


def test_the_step_needs_the_running_service_to_learn_which_repository_it_binds(
    tmp_path: Path,
) -> None:
    run = _harden(_ready(tmp_path), Scenario(api_reachable=False))
    assert run.returncode == 1
    assert "refused at harden: local-api:" in run.stderr
    assert run.called("gh", "api") == []
