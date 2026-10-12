from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._setup_ci_stubs import (
    FIRST_RUN_ID,
    RUN_CREATED,
    STILL_RUNNING,
    dispatches,
    read_runs,
    run_url,
    seed_run,
)
from tests._setup_support import (
    ENGINE_SLUG,
    SONAR_ORGANIZATION,
    SONAR_PROJECT_KEY,
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    assert_no_secret,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_latch,
    seed_profile,
    seed_running_service,
    seed_secrets_env,
    stub_log,
)

RESUME_TRIGGER = "Resume with: scripts/setup_macos.sh --from trigger"
RESUME_LATCH = "Resume with: scripts/setup_macos.sh --from latch"
WORKFLOW_RUN = ("workflow", "run", "tests.yml", "--repo", ENGINE_SLUG, "--ref", "main")
RED = Scenario(ci_run_result="failure", ci_polls_before_done=0)
SONAR_JOB = "SonarCloud analysis"


def _ready(tmp_path: Path, *, block: bool = True) -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True, ci_steps=True)
    seed_running_service(sandbox)
    seed_profile(sandbox, organization=SONAR_ORGANIZATION if block else None)
    return sandbox


def _trigger(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "trigger", scenario=scenario)


def _latch(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "latch", scenario=scenario)


def _seed(
    sandbox: Sandbox,
    run_id: int = FIRST_RUN_ID,
    *,
    result: str = "success",
    done_after: int = 0,
    failed_job: str = "test-backend",
) -> None:
    seed_run(
        sandbox.stub_root,
        sandbox.ci_run_file,
        run_id,
        result=result,
        done_after=done_after,
        failed_job=failed_job,
    )


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _record(sandbox: Sandbox) -> str:
    return sandbox.ci_run_file.read_text(encoding="utf-8").strip()


def _resume(run: SetupRun) -> str:
    return run.stderr.strip().splitlines()[-1]


def _latched(sandbox: Sandbox) -> dict[str, object]:
    held: dict[str, object] = json.loads(sandbox.latch_file.read_text(encoding="utf-8"))
    return held


def _reads(run: SetupRun, kind: str) -> list[int]:
    return [
        index
        for index, call in enumerate(run.calls)
        if call.matches("gh", "api") and f"/{kind}?" in call.args[1]
    ]


def test_the_trigger_step_dispatches_exactly_one_tests_run_on_main_and_records_it(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _trigger(sandbox)
    assert run.returncode == 0, run.output
    assert [call.args for call in run.called("gh", "workflow")] == [WORKFLOW_RUN]
    assert dispatches(sandbox.stub_root) == [{"workflow": "tests.yml", "ref": "main"}]
    assert _record(sandbox) == f"{FIRST_RUN_ID} {RUN_CREATED} {run_url(ENGINE_SLUG, FIRST_RUN_ID)}"
    assert f"[done] started tests.yml on main for {ENGINE_SLUG}: run {FIRST_RUN_ID}" in run.stdout
    assert "trigger" in _marks(sandbox)


def test_the_only_mutating_call_of_the_trigger_step_is_the_one_workflow_dispatch(
    tmp_path: Path,
) -> None:
    run = _trigger(_ready(tmp_path))
    assert [call.args[:2] for call in mutating_calls(run)] == [("workflow", "run")]
    assert run.called("launchctl", "kickstart") == []


def test_the_trigger_step_waits_for_the_new_run_to_appear_in_the_run_list(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _trigger(sandbox, Scenario(ci_run_visible_after=2))
    assert run.returncode == 0, run.output
    assert len(dispatches(sandbox.stub_root)) == 1
    assert len(run.called("sleep")) == 2
    assert _record(sandbox).startswith(f"{FIRST_RUN_ID} ")


def test_a_run_that_never_appears_fails_the_trigger_step_after_the_bounded_wait(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _trigger(sandbox, Scenario(ci_run_visible_after=50))
    assert run.returncode == 1
    assert f"no new run of tests.yml appeared after {WAIT_BUDGET} checks" in run.stdout
    assert len(dispatches(sandbox.stub_root)) == 1
    assert not sandbox.ci_run_file.exists()
    assert _resume(run) == RESUME_TRIGGER
    assert "trigger" not in _marks(sandbox)


def test_a_dispatch_that_github_refuses_fails_the_step_and_records_no_run(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _trigger(sandbox, Scenario(ci_dispatch_fails=True))
    assert run.returncode == 1
    assert "[failed] GitHub refused to start tests.yml on main" in run.stdout
    assert _resume(run) == RESUME_TRIGGER
    assert not sandbox.ci_run_file.exists()


def test_a_run_list_that_cannot_be_read_starts_no_run(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _trigger(sandbox, Scenario(ci_list_fails=True))
    assert run.returncode == 1
    assert "[failed] GitHub would not list the tests.yml runs" in run.stdout
    assert run.called("gh", "workflow") == []
    assert _resume(run) == RESUME_TRIGGER


def test_a_resumed_run_starts_no_second_run_while_the_recorded_run_is_in_progress(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=STILL_RUNNING)
    run = _trigger(sandbox)
    assert run.returncode == 0, run.output
    assert run.called("gh", "workflow") == []
    still = f"[already] CI run {FIRST_RUN_ID} of tests.yml on main is still in progress"
    assert still in run.stdout
    assert mutating_calls(run) == []
    assert "trigger" in _marks(sandbox)


def test_a_resumed_run_starts_no_second_run_when_the_recorded_run_succeeded(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=0, result="success")
    run = _trigger(sandbox)
    assert run.returncode == 0, run.output
    assert run.called("gh", "workflow") == []
    assert f"[already] CI run {FIRST_RUN_ID} of tests.yml on main succeeded" in run.stdout
    assert dispatches(sandbox.stub_root) == []


def test_after_a_red_run_a_resumed_run_starts_exactly_one_fresh_run(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=0, result="failure")
    run = _trigger(sandbox)
    assert run.returncode == 0, run.output
    assert [call.args for call in run.called("gh", "workflow")] == [WORKFLOW_RUN]
    assert len(dispatches(sandbox.stub_root)) == 1
    assert _record(sandbox).startswith(f"{FIRST_RUN_ID + 1} ")
    assert "the recorded CI run 1001 was red; starting a fresh run" in run.stdout


def test_a_recorded_run_that_github_will_not_show_starts_no_run_and_fails_the_step(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=0)
    run = _trigger(sandbox, Scenario(ci_view_fails=True))
    assert run.returncode == 1
    assert f"GitHub would not show the recorded CI run {FIRST_RUN_ID}" in run.stdout
    assert run.called("gh", "workflow") == []
    assert _resume(run) == RESUME_TRIGGER


def test_running_the_trigger_step_twice_starts_one_run_and_reports_the_second_as_already(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _trigger(sandbox).returncode == 0
    second = _trigger(sandbox)
    assert second.returncode == 0, second.output
    assert "[already] CI run" in second.stdout
    assert len(dispatches(sandbox.stub_root)) == 1
    assert len(read_runs(sandbox.stub_root)) == 1


def test_the_latch_is_written_with_the_profiles_organization_and_key_after_gate_and_sonarcloud(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=1)
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    held = _latched(sandbox)
    assert (held["organization"], held["project_key"]) == (SONAR_ORGANIZATION, SONAR_PROJECT_KEY)
    assert sandbox.latch_file.stat().st_mode & 0o777 == 0o600
    views = [i for i, call in enumerate(run.calls) if call.matches("gh", "run", "view")]
    assert len(views) == 2
    assert _reads(run, "check-runs")[0] > views[1]
    assert "[done] SonarCloud reported on the CI run: latch written" in run.stdout
    assert "latch" in _marks(sandbox)


def test_the_latch_follows_the_profile_when_the_stubbed_key_changes(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    seed_profile(sandbox, organization="changed-org", key="changed-org_changed")
    _seed(sandbox)
    assert _latch(sandbox).returncode == 0
    held = _latched(sandbox)
    assert (held["organization"], held["project_key"]) == ("changed-org", "changed-org_changed")


def test_the_step_reads_the_run_and_the_commit_but_writes_nothing_to_github(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox)
    assert mutating_calls(run) == []
    assert run.called("gh", "workflow") == []
    assert dispatches(sandbox.stub_root) == []
    reads = [call.args[1] for call in run.called("gh", "api")]
    assert any(f"commits/{'a' * 40}/check-runs" in read for read in reads)
    assert any(f"commits/{'a' * 40}/statuses" in read for read in reads)


@pytest.mark.parametrize("evidence", ["app-check", "renamed-app-check", "app-status"])
def test_a_sonarcloud_app_check_run_or_commit_status_after_the_trigger_latches(
    tmp_path: Path, evidence: str
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox, Scenario(sonar_evidence=evidence))
    assert run.returncode == 0, run.output
    assert sandbox.latch_file.exists()


@pytest.mark.parametrize(
    "evidence",
    ["actions-check", "before-trigger", "actions-status", "status-before-trigger", "none"],
)
def test_evidence_that_is_not_the_sonarcloud_app_after_the_trigger_never_latches(
    tmp_path: Path, evidence: str
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox, Scenario(sonar_evidence=evidence))
    assert run.returncode == 1
    assert not sandbox.latch_file.exists()
    assert "latch" not in _marks(sandbox)
    assert "nothing was latched" in run.stdout


def test_when_sonarcloud_never_reports_the_wait_is_bounded_and_the_run_is_incomplete(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox, Scenario(sonar_evidence="none"))
    assert run.returncode == 1
    assert (
        f"[failed] SonarCloud posted no check or status on the commit of CI run {FIRST_RUN_ID} "
        f"after it started, within {WAIT_BUDGET} checks; nothing was latched"
    ) in run.stdout
    assert len(_reads(run, "check-runs")) == WAIT_BUDGET
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert "setup: incomplete: latch did not complete" in run.stderr
    assert _resume(run) == RESUME_LATCH
    assert not sandbox.latch_file.exists()


def test_a_run_that_is_still_going_when_the_wait_ends_writes_no_latch(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, done_after=STILL_RUNNING)
    run = _latch(sandbox)
    assert run.returncode == 1
    assert f"CI run {FIRST_RUN_ID} did not finish within {WAIT_BUDGET} checks" in run.stdout
    assert _reads(run, "check-runs") == []
    assert _resume(run) == RESUME_LATCH
    assert not sandbox.latch_file.exists()


def test_sonarcloud_evidence_that_arrives_during_the_wait_latches(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox, Scenario(sonar_evidence_after_reads=1))
    assert run.returncode == 0, run.output
    assert len(_reads(run, "check-runs")) == 2
    assert sandbox.latch_file.exists()


def test_transient_read_failures_during_the_wait_are_retried_until_the_bound(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox)
    run = _latch(sandbox, Scenario(commit_reads_fail=True))
    assert run.returncode == 1
    assert len(_reads(run, "check-runs")) == WAIT_BUDGET
    assert not sandbox.latch_file.exists()
    unreadable = _latch(sandbox, Scenario(ci_view_fails=True))
    assert unreadable.returncode == 1
    assert "did not finish within" in unreadable.stdout


def test_a_red_run_writes_no_latch_names_the_failing_job_and_url_and_resumes_at_the_trigger(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, result="failure")
    run = _latch(sandbox)
    assert run.returncode == 1
    assert not sandbox.latch_file.exists()
    url = run_url(ENGINE_SLUG, FIRST_RUN_ID)
    red = f"[failed] CI run {FIRST_RUN_ID} is red: job test-backend did not succeed in the CI run"
    assert f"{red} {url}" in run.stdout
    assert "Free plan" not in run.output
    assert "setup: incomplete: trigger, latch did not complete" in run.stderr
    assert _resume(run) == RESUME_TRIGGER
    assert _reads(run, "check-runs") == []
    marks = _marks(sandbox)
    assert "latch" not in marks
    assert "trigger" not in marks


def test_a_red_sonar_job_on_a_private_repository_names_the_free_plan_private_project_limit(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, result="failure", failed_job=SONAR_JOB)
    run = _latch(sandbox, Scenario(visibility="private"))
    assert run.returncode == 1
    assert f"job {SONAR_JOB} did not succeed" in run.stdout
    assert "SonarCloud's Free plan does not analyse private projects" in run.stdout
    assert _resume(run) == RESUME_TRIGGER


@pytest.mark.parametrize(
    ("visibility", "failed_job"),
    [("public", SONAR_JOB), ("private", "test-backend"), ("internal", SONAR_JOB)],
)
def test_the_free_plan_limit_is_named_only_for_a_private_repository_with_a_red_sonar_job(
    tmp_path: Path, visibility: str, failed_job: str
) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, result="failure", failed_job=failed_job)
    run = _latch(sandbox, Scenario(visibility=visibility))
    assert run.returncode == 1
    assert "Free plan" not in run.output


def test_an_unreadable_visibility_never_claims_the_free_plan_limit(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _seed(sandbox, result="failure", failed_job=SONAR_JOB)
    run = _latch(sandbox, Scenario(visibility="private", repo_read_fails=True))
    assert run.returncode == 1
    assert "Free plan" not in run.output


def test_after_a_red_run_the_resume_starts_one_fresh_run_and_the_latch_step_then_latches(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    first = _trigger(sandbox, RED)
    assert first.returncode == 0, first.output
    red = _latch(sandbox, RED)
    assert red.returncode == 1
    assert _resume(red) == RESUME_TRIGGER
    resumed = _trigger(sandbox, Scenario(ci_polls_before_done=0))
    assert resumed.returncode == 0, resumed.output
    assert len(resumed.called("gh", "workflow")) == 1
    assert len(dispatches(sandbox.stub_root)) == 2
    assert _record(sandbox).startswith(f"{FIRST_RUN_ID + 1} ")
    fixed = _latch(sandbox, Scenario(ci_polls_before_done=0))
    assert fixed.returncode == 0, fixed.output
    assert sandbox.latch_file.exists()
    assert len(dispatches(sandbox.stub_root)) == 2


def test_after_a_timeout_the_resume_waits_again_on_the_same_run_and_starts_no_new_one(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    assert _trigger(sandbox).returncode == 0
    silent = _latch(sandbox, Scenario(sonar_evidence="none"))
    assert silent.returncode == 1
    assert _resume(silent) == RESUME_LATCH
    resumed = _latch(sandbox)
    assert resumed.returncode == 0, resumed.output
    assert len(dispatches(sandbox.stub_root)) == 1
    assert sandbox.latch_file.exists()


def test_a_latch_that_already_matches_the_profile_is_reported_already_and_nothing_is_read(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    seed_latch(sandbox)
    _seed(sandbox, result="failure")
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    assert f"[already] SonarCloud latch for {ENGINE_SLUG}" in run.stdout
    assert run.called("gh") == []
    assert "latch" in _marks(sandbox)


def test_a_latch_for_another_project_is_not_reused(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    seed_latch(sandbox, "other-org", "other-org_other")
    _seed(sandbox)
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    assert _latched(sandbox)["organization"] == SONAR_ORGANIZATION


def test_without_a_recorded_run_the_latch_step_fails_and_resumes_at_the_trigger(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _latch(sandbox)
    assert run.returncode == 1
    assert "no CI run is recorded" in run.stdout
    assert run.called("gh") == []
    assert _resume(run) == RESUME_TRIGGER


def test_a_profile_without_a_sonarcloud_project_cannot_be_latched(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path, block=False)
    _seed(sandbox)
    run = _latch(sandbox)
    assert run.returncode == 1
    assert "run the sonar step first" in run.stdout
    assert not sandbox.latch_file.exists()
    assert _resume(run) == RESUME_LATCH


def test_the_ci_steps_never_put_a_token_or_the_bots_login_on_a_command_line(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    seed_secrets_env(sandbox, "EXAMPLE_KEY=planted-value\n")
    assert _trigger(sandbox).returncode == 0
    run = _latch(sandbox)
    assert run.returncode == 0, run.output
    assert_no_secret(run, "planted-value")
    assert stub_log(sandbox, "gh_writes.jsonl") == []
