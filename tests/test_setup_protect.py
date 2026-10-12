from __future__ import annotations

from pathlib import Path

import pytest

from tests._setup_support import (
    ENGINE_SLUG,
    Sandbox,
    Scenario,
    SetupRun,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_bound_service,
)

API = "http://127.0.0.1:8585"
PREFLIGHT_URL = f"{API}/api/engines/{ENGINE_SLUG}/preflight"
PROTECT_URL = f"{API}/api/engines/{ENGINE_SLUG}/protect"
UNPROTECTED = Scenario(protection_matching=False)
RESUME_PROTECT = "Resume with: scripts/setup_macos.sh --from protect"
RESUME_CONFIRM = "Resume with: scripts/setup_macos.sh --from confirm"


def _ready(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path)
    seed_bound_service(sandbox)
    return sandbox


def _protect(sandbox: Sandbox, scenario: Scenario = UNPROTECTED) -> SetupRun:
    return run_setup(sandbox, "--only", "protect", scenario=scenario)


def _confirm(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "confirm", scenario=scenario)


def _urls(run: SetupRun) -> list[str]:
    return [call.args[-1] for call in run.called("curl")]


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_protection_is_probed_first_and_applied_through_the_protect_action(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _protect(sandbox)
    assert run.returncode == 0, run.output
    urls = _urls(run)
    assert urls.index(PREFLIGHT_URL) < urls.index(PROTECT_URL)
    assert [call.args[-1] for call in mutating_calls(run)] == [PROTECT_URL]
    assert f"[done] branch protection applied to {ENGINE_SLUG}" in run.stdout
    assert "gate required from GitHub Actions, admins enforced" in run.stdout
    assert "protect" in _marks(sandbox)


def test_matching_protection_skips_the_protect_action_and_makes_no_write(
    tmp_path: Path,
) -> None:
    run = _protect(_ready(tmp_path), Scenario())
    assert run.returncode == 0, run.output
    assert f"[already] branch protection on {ENGINE_SLUG} already matches" in run.stdout
    assert PROTECT_URL not in _urls(run)
    assert mutating_calls(run) == []


def test_an_immediate_second_run_skips_the_protect_action(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert _protect(sandbox).returncode == 0
    second = _protect(sandbox)
    assert second.returncode == 0, second.output
    assert PROTECT_URL not in _urls(second)
    assert mutating_calls(second) == []
    assert "[already] branch protection" in second.stdout


def test_protection_that_cannot_be_read_is_applied(tmp_path: Path) -> None:
    run = _protect(_ready(tmp_path), Scenario(protection_unreadable=True, protect_result="applied"))
    assert run.returncode == 0, run.output
    assert PROTECT_URL in _urls(run)


@pytest.mark.parametrize("result", ["deferred", "partial"])
def test_deferred_settings_fail_the_step_even_though_the_http_status_is_200(
    tmp_path: Path, result: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _protect(sandbox, Scenario(protection_matching=False, protect_result=result))
    assert run.returncode == 1
    assert "[problem] " in run.stdout
    assert "token lacks admin scope" in run.stdout
    assert "[failed] GitHub did not accept every branch-protection setting" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_PROTECT
    assert "protect" not in _marks(sandbox)


@pytest.mark.parametrize("result", ["unprocessable", "server-error"])
def test_a_protect_answer_that_reports_no_applied_setting_is_a_failure_not_a_refusal(
    tmp_path: Path, result: str
) -> None:
    sandbox = _ready(tmp_path)
    run = _protect(sandbox, Scenario(protection_matching=False, protect_result=result))
    assert run.returncode == 1
    assert "the protect action reported no applied settings" in run.stdout
    assert "refused at" not in run.stderr
    assert "protect" not in _marks(sandbox)


def test_an_unreadable_protect_answer_is_a_failure_that_names_the_status(tmp_path: Path) -> None:
    run = _protect(_ready(tmp_path), Scenario(protection_matching=False, protect_result="garbled"))
    assert run.returncode == 1
    assert "returned no readable outcome (HTTP 200)" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_PROTECT


def test_a_missing_transport_credential_is_refused_before_any_preflight(tmp_path: Path) -> None:
    run = _protect(_ready(tmp_path), Scenario(transport_stored=False))
    assert run.returncode == 1
    assert "refused at protect: transport-token:" in run.stderr
    assert "run the credential step first" in run.stderr
    assert PREFLIGHT_URL not in _urls(run)


def test_an_unreadable_preflight_answer_is_refused(tmp_path: Path) -> None:
    run = _protect(_ready(tmp_path), Scenario(preflight_garbled=True))
    assert run.returncode == 1
    assert "refused at protect: preflight:" in run.stderr


@pytest.mark.parametrize("status", [401, 403])
def test_a_control_token_the_service_rejects_stops_the_protect_step(
    tmp_path: Path, status: int
) -> None:
    run = _protect(_ready(tmp_path), Scenario(control_token_status=status))
    assert run.returncode == 1
    assert "refused at protect: control-token:" in run.stderr
    assert PROTECT_URL not in _urls(run)


def test_a_service_that_does_not_answer_stops_the_protect_step(tmp_path: Path) -> None:
    run = _protect(_ready(tmp_path), Scenario(api_reachable=False))
    assert run.returncode == 1
    assert "refused at protect: local-api:" in run.stderr


def test_confirm_passes_when_the_owned_findings_pass_and_reports_the_rest_as_pending(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _confirm(sandbox)
    assert run.returncode == 0, run.output
    for check in (
        "author_token_valid",
        "repo_clonable",
        "conversation_resolution",
        "required_approvals",
        "squash_merge_enabled",
        "delete_branch_on_merge",
        "required_checks",
    ):
        assert f"[ok] preflight {check}" in run.stdout
    assert "[pending] preflight reviewer_distinct_from_author is unknown" in run.stdout
    assert "[pending] preflight dispatch_readiness is fail" in run.stdout
    assert "the final report checks it" in run.stdout
    assert "P1.9c" not in run.stdout
    assert "confirm" in _marks(sandbox)
    assert mutating_calls(run) == []


def test_reviewer_and_code_quality_findings_never_fail_the_confirm_step(tmp_path: Path) -> None:
    scenario = Scenario(
        preflight_status={"reviewer_distinct_from_author": "fail", "dispatch_readiness": "fail"}
    )
    run = _confirm(_ready(tmp_path), scenario)
    assert run.returncode == 0, run.output
    assert "[pending] preflight reviewer_distinct_from_author is fail" in run.stdout


@pytest.mark.parametrize(
    "check", ["author_token_valid", "repo_clonable", "required_checks", "delete_branch_on_merge"]
)
def test_an_owned_finding_that_fails_fails_the_confirm_step(tmp_path: Path, check: str) -> None:
    sandbox = _ready(tmp_path)
    run = _confirm(sandbox, Scenario(preflight_status={check: "fail"}))
    assert run.returncode == 1
    assert f"[problem] preflight {check} is fail" in run.stdout
    assert "[failed] the preflight found a problem this setup item owns" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_CONFIRM
    assert "confirm" not in _marks(sandbox)


def test_an_owned_finding_that_is_only_unknown_is_not_treated_as_confirmed(tmp_path: Path) -> None:
    run = _confirm(_ready(tmp_path), Scenario(preflight_status={"repo_clonable": "unknown"}))
    assert run.returncode == 1
    assert "[problem] preflight repo_clonable is unknown" in run.stdout


def test_protection_that_does_not_match_fails_the_confirm_step(tmp_path: Path) -> None:
    run = _confirm(_ready(tmp_path), UNPROTECTED)
    assert run.returncode == 1
    assert "[problem] preflight required_checks is fail" in run.stdout


def test_protection_that_cannot_be_read_fails_the_confirm_step(tmp_path: Path) -> None:
    run = _confirm(_ready(tmp_path), Scenario(protection_unreadable=True))
    assert run.returncode == 1
    assert "[problem] preflight branch_protection_readable is fail" in run.stdout


def test_a_missing_transport_credential_fails_the_confirm_step_on_its_own_findings(
    tmp_path: Path,
) -> None:
    run = _confirm(_ready(tmp_path), Scenario(transport_stored=False))
    assert run.returncode == 1
    assert "[problem] preflight author_token_valid is fail" in run.stdout
    assert "[problem] preflight repo_clonable is fail" in run.stdout


def test_a_finding_that_setup_does_not_own_is_shown_but_does_not_fail_the_step(
    tmp_path: Path,
) -> None:
    scenario = Scenario(preflight_status={"flow_graph_and_posture_valid": "fail"})
    run = _confirm(_ready(tmp_path), scenario)
    assert run.returncode == 0, run.output
    assert "[info] preflight flow_graph_and_posture_valid is fail" in run.stdout


def test_an_unreadable_preflight_answer_is_refused_by_the_confirm_step(tmp_path: Path) -> None:
    run = _confirm(_ready(tmp_path), Scenario(preflight_garbled=True))
    assert run.returncode == 1
    assert "refused at confirm: preflight:" in run.stderr


def test_the_confirm_step_runs_the_preflight_once_and_writes_nothing_to_github(
    tmp_path: Path,
) -> None:
    run = _confirm(_ready(tmp_path))
    assert _urls(run).count(PREFLIGHT_URL) == 1
    assert run.called("gh", "api") == []
