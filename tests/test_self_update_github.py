from __future__ import annotations

import subprocess
import traceback
from pathlib import Path

import pytest

from alan import self_update, self_update_github
from alan.self_update import Reason, Verdict, check
from tests.self_update_fixtures import (
    CHECKS_NEEDLE,
    OWNER,
    REPO,
    RUNS_NEEDLE,
    SUITE_ID,
    Scenario,
    build_scenario,
    completed,
    ok,
    push_run,
    redirect_env,
)

Failure = subprocess.CompletedProcess[str] | BaseException
SENTINEL = "SENTINEL-not-a-token"
FIELD_OR_INPUT_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def assert_read_failed(scenario: Scenario) -> None:
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.GITHUB_READ_FAILED)
    assert record.fetched_sha == scenario.fetched
    assert record.head_sha == scenario.head


TRANSPORT_FAILURES: dict[str, Failure] = {
    "gh_missing": FileNotFoundError("gh"),
    "timeout": subprocess.TimeoutExpired(["gh"], 30.0),
    "os_error": OSError("spawn failed"),
    "non_zero_exit": completed(returncode=1, stderr="HTTP 502"),
    "non_json": completed("<html>not json</html>"),
    "empty_stdout": completed(""),
    "list_envelope": ok([]),
    "scalar_envelope": ok(7),
    "null_envelope": ok(None),
}

RUNS_ENVELOPE_FAILURES = {
    "missing_workflow_runs": {"total_count": 0},
    "null_workflow_runs": {"workflow_runs": None},
    "dict_workflow_runs": {"workflow_runs": {}},
    "string_workflow_runs": {"workflow_runs": "none"},
    "non_dict_item": {"workflow_runs": [1]},
    "null_item": {"workflow_runs": [None]},
    "mixed_items": {"workflow_runs": [{"id": 1}, "run"]},
}

CHECKS_ENVELOPE_FAILURES = {
    "missing_check_runs": {"total_count": 0},
    "null_check_runs": {"check_runs": None},
    "dict_check_runs": {"check_runs": {}},
    "string_check_runs": {"check_runs": "none"},
    "non_dict_item": {"check_runs": [1]},
    "null_item": {"check_runs": [None]},
    "mixed_items": {"check_runs": [{"id": 1}, "check"]},
}


@pytest.mark.parametrize(
    "failure", TRANSPORT_FAILURES.values(), ids=TRANSPORT_FAILURES.keys()
)
def test_a_failed_first_read_is_a_github_read_error(scenario: Scenario, failure: Failure) -> None:
    scenario.router.respond(RUNS_NEEDLE, failure)
    assert_read_failed(scenario)
    assert len(scenario.router.calls) == 1


@pytest.mark.parametrize(
    "failure", TRANSPORT_FAILURES.values(), ids=TRANSPORT_FAILURES.keys()
)
def test_a_failed_second_read_is_a_github_read_error(scenario: Scenario, failure: Failure) -> None:
    scenario.set_runs(push_run(scenario.fetched))
    scenario.router.respond(CHECKS_NEEDLE, failure)
    assert_read_failed(scenario)
    assert len(scenario.router.calls) == 2


@pytest.mark.parametrize(
    "payload", RUNS_ENVELOPE_FAILURES.values(), ids=RUNS_ENVELOPE_FAILURES.keys()
)
def test_a_malformed_runs_envelope_is_a_github_read_error(
    scenario: Scenario, payload: object
) -> None:
    scenario.router.respond(RUNS_NEEDLE, ok(payload))
    assert_read_failed(scenario)


@pytest.mark.parametrize(
    "payload", CHECKS_ENVELOPE_FAILURES.values(), ids=CHECKS_ENVELOPE_FAILURES.keys()
)
def test_a_malformed_check_runs_envelope_is_a_github_read_error(
    scenario: Scenario, payload: object
) -> None:
    scenario.set_runs(push_run(scenario.fetched))
    scenario.router.respond(CHECKS_NEEDLE, ok(payload))
    assert_read_failed(scenario)


def test_gh_output_never_reaches_the_record_or_the_exception(scenario: Scenario) -> None:
    scenario.router.respond(
        RUNS_NEEDLE, completed(SENTINEL, returncode=1, stderr=f"{SENTINEL} stderr")
    )
    assert_read_failed(scenario)
    assert SENTINEL not in self_update.status_path().read_text(encoding="utf-8")
    scenario.router.respond(RUNS_NEEDLE, completed(f"{SENTINEL} not json"))
    with pytest.raises(self_update_github.GithubReadError) as caught:
        self_update_github.push_runs(OWNER, REPO, scenario.fetched)
    rendered = "".join(traceback.format_exception(caught.value))
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in rendered


def test_a_valid_but_hostile_payload_is_not_echoed_into_the_record(scenario: Scenario) -> None:
    scenario.set_runs(push_run(scenario.fetched, name=SENTINEL, conclusion=SENTINEL))
    check(scenario.repo)
    assert SENTINEL not in self_update.status_path().read_text(encoding="utf-8")


def test_every_gh_command_line_the_check_runs_is_a_read_only_get(scenario: Scenario) -> None:
    scenario.set_green()
    check(scenario.repo)
    assert len(scenario.router.calls) == 2
    for argv in scenario.router.calls:
        assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        assert len(argv) == 7
        assert argv[argv.index("--method") + 1] == "GET"
        assert FIELD_OR_INPUT_FLAGS.isdisjoint(argv)
        assert not argv[6].startswith("-")


def test_the_read_helpers_pass_the_suite_id_and_commit_into_the_path(scenario: Scenario) -> None:
    scenario.router.respond(RUNS_NEEDLE, ok({"workflow_runs": []}))
    scenario.router.respond(CHECKS_NEEDLE, ok({"check_runs": []}))
    assert self_update_github.push_runs(OWNER, REPO, scenario.fetched) == []
    assert self_update_github.gate_check_runs(OWNER, REPO, SUITE_ID) == []
    assert scenario.fetched in scenario.router.calls[0][-1]
    assert str(SUITE_ID) in scenario.router.calls[1][-1]
