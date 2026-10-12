from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alan import self_update
from alan.self_update import Reason, StatusRecord, Verdict, check
from tests.self_update_fixtures import (
    ACTIONS_APP,
    FOREIGN_APP,
    MISSING,
    OTHER_SHA,
    OWNER,
    REPO,
    SUITE_ID,
    Scenario,
    build_scenario,
    gate_check,
    push_run,
    redirect_env,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def verdict_of(record: StatusRecord) -> tuple[Verdict, Reason]:
    return record.verdict, record.reason


def run_for(scenario: Scenario, **overrides: Any) -> dict[str, Any]:
    return push_run(scenario.fetched, **overrides)


def gate_for(scenario: Scenario, **overrides: Any) -> dict[str, Any]:
    return gate_check(scenario.fetched, **overrides)


def stored_status() -> dict[str, Any]:
    stored: dict[str, Any] = json.loads(self_update.status_path().read_text(encoding="utf-8"))
    return stored


NON_SUCCESS_CONCLUSIONS = [
    "failure",
    "cancelled",
    "skipped",
    "timed_out",
    "neutral",
    "action_required",
    "startup_failure",
    "stale",
    None,
]


def test_a_successful_push_run_and_gate_are_green_after_exactly_two_reads(
    scenario: Scenario,
) -> None:
    scenario.set_green()
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    assert record.fetched_sha == scenario.fetched
    assert record.head_sha == scenario.head
    assert len(scenario.router.calls) == 2


def test_the_newest_qualifying_run_decides_whatever_the_list_order(scenario: Scenario) -> None:
    older_failure = run_for(scenario, run_number=10, conclusion="failure")
    newer_success = run_for(scenario, run_number=11, conclusion="success")
    scenario.set_checks(gate_for(scenario))
    scenario.set_runs(older_failure, newer_success)
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    scenario.set_runs(newer_success, older_failure)
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


def test_a_newer_failed_run_beats_an_older_success_whatever_the_list_order(
    scenario: Scenario,
) -> None:
    older_success = run_for(scenario, run_number=10, conclusion="success")
    newer_failure = run_for(scenario, run_number=11, conclusion="failure")
    scenario.set_runs(older_success, newer_failure)
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.RUN_NOT_SUCCESSFUL)
    scenario.set_runs(newer_failure, older_success)
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.RUN_NOT_SUCCESSFUL)


def test_the_newest_gate_decides_whatever_the_list_order(scenario: Scenario) -> None:
    scenario.set_runs(run_for(scenario))
    older_failure = gate_for(scenario, id=100, conclusion="failure")
    newer_success = gate_for(scenario, id=101, conclusion="success")
    scenario.set_checks(older_failure, newer_success)
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    scenario.set_checks(newer_success, older_failure)
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


def test_a_newer_failed_gate_beats_an_older_success_whatever_the_list_order(
    scenario: Scenario,
) -> None:
    scenario.set_runs(run_for(scenario))
    older_success = gate_for(scenario, id=100, conclusion="success")
    newer_failure = gate_for(scenario, id=101, conclusion="failure")
    scenario.set_checks(older_success, newer_failure)
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)
    scenario.set_checks(newer_failure, older_success)
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)


def test_no_push_run_yet_is_pending_after_a_single_read(scenario: Scenario) -> None:
    scenario.set_runs()
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert len(scenario.router.calls) == 1


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_a_run_that_is_not_completed_is_pending_and_the_gate_is_not_read(
    scenario: Scenario, status: str
) -> None:
    scenario.set_runs(run_for(scenario, status=status, conclusion=None))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.RUN_IN_PROGRESS)
    assert len(scenario.router.calls) == 1


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_a_gate_that_is_not_completed_is_pending(scenario: Scenario, status: str) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, status=status, conclusion=None))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.GATE_IN_PROGRESS)
    assert len(scenario.router.calls) == 2


@pytest.mark.parametrize("conclusion", NON_SUCCESS_CONCLUSIONS)
def test_a_completed_run_without_success_is_red_and_the_gate_is_not_read(
    scenario: Scenario, conclusion: str | None
) -> None:
    scenario.set_runs(run_for(scenario, conclusion=conclusion))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.RED, Reason.RUN_NOT_SUCCESSFUL)
    assert len(scenario.router.calls) == 1


def test_a_completed_run_with_no_gate_at_all_is_red(scenario: Scenario) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks()
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)


@pytest.mark.parametrize("conclusion", NON_SUCCESS_CONCLUSIONS)
def test_a_completed_gate_without_success_is_red(
    scenario: Scenario, conclusion: str | None
) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, conclusion=conclusion))
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)


def test_a_gate_from_another_app_carrying_the_push_suite_id_is_not_green(
    scenario: Scenario,
) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, app=dict(FOREIGN_APP)))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)


def test_a_github_actions_gate_from_a_different_suite_is_not_green(scenario: Scenario) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, check_suite={"id": SUITE_ID + 1}))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)


@pytest.mark.parametrize("foreign_first", [True, False])
def test_a_foreign_gate_next_to_the_qualifying_one_leaves_it_green(
    scenario: Scenario, foreign_first: bool
) -> None:
    scenario.set_runs(run_for(scenario))
    qualifying = gate_for(scenario, id=100)
    foreign = gate_for(scenario, id=101, app=dict(FOREIGN_APP))
    scenario.set_checks(*((foreign, qualifying) if foreign_first else (qualifying, foreign)))
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


def test_a_newer_successful_foreign_gate_does_not_override_a_failed_qualifying_gate(
    scenario: Scenario,
) -> None:
    scenario.set_runs(run_for(scenario))
    qualifying = gate_for(scenario, id=100, conclusion="failure")
    foreign_suite = gate_for(scenario, id=200, check_suite={"id": SUITE_ID + 1})
    foreign_app = gate_for(scenario, id=201, app=dict(FOREIGN_APP))
    scenario.set_checks(qualifying, foreign_suite, foreign_app)
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)


def test_a_newer_failed_foreign_gate_does_not_override_a_successful_qualifying_gate(
    scenario: Scenario,
) -> None:
    scenario.set_runs(run_for(scenario))
    qualifying = gate_for(scenario, id=100)
    foreign = gate_for(scenario, id=200, app=dict(FOREIGN_APP), conclusion="failure")
    scenario.set_checks(qualifying, foreign)
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


@pytest.mark.parametrize("app", [None, MISSING, "github-actions", {"id": 15368}, []])
def test_a_gate_whose_app_is_not_the_github_actions_object_is_ignored(
    scenario: Scenario, app: object
) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, app=app))
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)


RUN_CONTRADICTIONS = {
    "event_pull_request": {"event": "pull_request"},
    "event_dispatch": {"event": "workflow_dispatch"},
    "event_null": {"event": None},
    "branch_other": {"head_branch": "feature"},
    "branch_missing": {"head_branch": MISSING},
    "sha_other": {"head_sha": OTHER_SHA},
    "sha_missing": {"head_sha": MISSING},
    "path_other_workflow": {"path": ".github/workflows/other.yml"},
    "path_backup_suffix": {"path": ".github/workflows/tests.yml.bak"},
    "path_prefixed": {"path": "x/.github/workflows/tests.yml"},
    "path_null": {"path": None},
    "path_missing": {"path": MISSING},
    "run_number_missing": {"run_number": MISSING},
    "run_number_bool": {"run_number": True},
    "run_number_string": {"run_number": "10"},
    "run_number_zero": {"run_number": 0},
    "suite_id_missing": {"check_suite_id": MISSING},
    "suite_id_bool": {"check_suite_id": True},
    "suite_id_string": {"check_suite_id": str(SUITE_ID)},
}


@pytest.mark.parametrize("overrides", RUN_CONTRADICTIONS.values(), ids=RUN_CONTRADICTIONS.keys())
def test_a_run_whose_own_fields_contradict_the_query_is_never_counted(
    scenario: Scenario, overrides: dict[str, Any]
) -> None:
    scenario.set_runs(run_for(scenario, **overrides))
    record = check(scenario.repo)
    assert verdict_of(record) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert len(scenario.router.calls) == 1


def test_a_contradicting_newer_run_does_not_hide_the_qualifying_one(scenario: Scenario) -> None:
    scenario.set_checks(gate_for(scenario))
    scenario.set_runs(
        run_for(scenario, run_number=10),
        run_for(scenario, run_number=99, event="pull_request", conclusion="failure"),
        run_for(scenario, run_number=98, head_sha=OTHER_SHA, conclusion="failure"),
    )
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


GATE_CONTRADICTIONS = {
    "name_other": {"name": "build"},
    "name_null": {"name": None},
    "name_missing": {"name": MISSING},
    "sha_other": {"head_sha": OTHER_SHA},
    "sha_missing": {"head_sha": MISSING},
    "suite_string": {"check_suite": {"id": str(SUITE_ID)}},
    "suite_bool": {"check_suite": {"id": True}},
    "suite_null": {"check_suite": None},
    "suite_missing": {"check_suite": MISSING},
    "suite_id_missing": {"check_suite": {}},
    "id_missing": {"id": MISSING},
    "id_string": {"id": "100"},
    "id_bool": {"id": True},
}


@pytest.mark.parametrize("overrides", GATE_CONTRADICTIONS.values(), ids=GATE_CONTRADICTIONS.keys())
def test_a_gate_whose_own_fields_contradict_the_query_is_never_counted(
    scenario: Scenario, overrides: dict[str, Any]
) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, **overrides))
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)


@pytest.mark.parametrize(
    "path",
    [".github/workflows/tests.yml@refs/heads/main", ".github/workflows/tests.yml@refs/tags/v1"],
)
def test_a_workflow_path_carrying_a_ref_suffix_is_accepted(
    scenario: Scenario, path: str
) -> None:
    scenario.set_runs(run_for(scenario, path=path))
    scenario.set_checks(gate_for(scenario))
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)


def test_the_two_reads_are_scoped_by_owner_repo_commit_and_suite(scenario: Scenario) -> None:
    scenario.set_green()
    check(scenario.repo)
    runs_argv, checks_argv = scenario.router.calls
    runs_path = runs_argv[-1]
    checks_path = checks_argv[-1]
    assert runs_path.startswith(f"repos/{OWNER}/{REPO}/")
    assert "event=push" in runs_path
    assert "branch=main" in runs_path
    assert f"head_sha={scenario.fetched}" in runs_path
    assert checks_path.startswith(f"repos/{OWNER}/{REPO}/")
    assert f"/check-suites/{SUITE_ID}/" in checks_path
    assert "check_name=gate" in checks_path
    assert "filter=latest" in checks_path


def test_a_pending_check_is_replaced_by_the_next_green_one(scenario: Scenario) -> None:
    scenario.set_runs()
    assert verdict_of(check(scenario.repo)) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert stored_status()["verdict"] == "pending"
    scenario.set_green()
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    assert stored_status()["verdict"] == "green"
    assert stored_status()["reason"] == "ci_proved_green"


def test_a_red_gate_turns_green_after_a_rerun_in_the_same_suite(scenario: Scenario) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(gate_for(scenario, id=100, conclusion="failure"))
    assert verdict_of(check(scenario.repo)) == (Verdict.RED, Reason.GATE_NOT_SUCCESSFUL)
    scenario.set_checks(
        gate_for(scenario, id=100, conclusion="failure"),
        gate_for(scenario, id=101, conclusion="success"),
    )
    assert verdict_of(check(scenario.repo)) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    assert stored_status()["verdict"] == "green"


def test_the_forged_green_gates_together_are_never_green(scenario: Scenario) -> None:
    scenario.set_runs(run_for(scenario))
    scenario.set_checks(
        gate_for(scenario, id=101, app=dict(FOREIGN_APP)),
        gate_for(scenario, id=102, app=dict(ACTIONS_APP), check_suite={"id": SUITE_ID + 1}),
        gate_for(scenario, id=103, head_sha=OTHER_SHA),
        gate_for(scenario, id=104, name="build"),
    )
    record = check(scenario.repo)
    assert record.verdict is not Verdict.GREEN
    assert verdict_of(record) == (Verdict.RED, Reason.NO_QUALIFYING_GATE)
    assert stored_status()["verdict"] == "red"
