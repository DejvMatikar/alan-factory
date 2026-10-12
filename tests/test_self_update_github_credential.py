from __future__ import annotations

import ast
import inspect
import logging
import subprocess
import sys
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from alan import (
    process_runner,
    secrets_store,
    self_update,
    self_update_github,
)
from alan.self_update import Reason, Verdict, check
from tests.self_update_fixtures import (
    OWNER,
    REPO,
    RUNS_NEEDLE,
    SLUG,
    SUITE_ID,
    TRANSPORT_SENTINEL,
    UNUSABLE_RECORDS,
    Mutation,
    Scenario,
    build_scenario,
    completed,
    count_credential_reads,
    forbid_credential_reads,
    git,
    ok,
    record_path,
    redirect_env,
    store_transport_credential,
)

MIXED_SLUG = "MixedCase-Owner/Repo-Name"
MIXED_VALUE = "mixed-case-value-not-a-credential"
AMBIENT_VALUE = "ambient-value-not-a-credential"
NULL_VALUE = "embedded\x00null"
REFUSAL = f"no transport credential for {SLUG}"
STORED_ENV = {"GH_TOKEN": TRANSPORT_SENTINEL}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


LEAKY_FAILURES: dict[str, subprocess.CompletedProcess[str] | BaseException] = {
    "non_zero_exit_echoing_the_value": completed(
        TRANSPORT_SENTINEL, returncode=1, stderr=f"{TRANSPORT_SENTINEL} denied"
    ),
    "timeout_echoing_the_value": subprocess.TimeoutExpired(
        ["gh"], 30.0, output=TRANSPORT_SENTINEL.encode("utf-8")
    ),
}


def point_origin_at_mixed_case(repo: Path) -> None:
    git(repo, "remote", "set-url", "origin", f"https://github.com/{MIXED_SLUG}.git")


def assert_refused(scenario: Scenario) -> None:
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []
    assert scenario.router.calls == []


def test_both_ci_reads_carry_the_stored_credential_in_the_child_environment_only(
    scenario: Scenario,
) -> None:
    scenario.set_green()
    record = check(scenario.repo)
    assert record.verdict is Verdict.GREEN
    assert scenario.router.envs == [STORED_ENV] * 2


def test_the_command_line_of_each_read_is_the_unchanged_get_and_holds_no_credential(
    scenario: Scenario,
) -> None:
    scenario.set_green()
    check(scenario.repo)
    assert len(scenario.router.calls) == 2
    for argv in scenario.router.calls:
        assert len(argv) == 7
        assert argv[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        assert all(TRANSPORT_SENTINEL not in part for part in argv)


def test_push_runs_alone_carries_the_stored_credential(scenario: Scenario) -> None:
    scenario.router.respond(RUNS_NEEDLE, ok({"workflow_runs": []}))
    assert self_update_github.push_runs(OWNER, REPO, scenario.fetched) == []
    assert scenario.router.envs == [STORED_ENV]


def test_gate_check_runs_alone_carries_the_stored_credential(scenario: Scenario) -> None:
    scenario.set_checks()
    assert self_update_github.gate_check_runs(OWNER, REPO, SUITE_ID) == []
    assert scenario.router.envs == [STORED_ENV]


@pytest.mark.parametrize(
    "failure", LEAKY_FAILURES.values(), ids=LEAKY_FAILURES.keys()
)
def test_the_credential_reaches_no_command_line_status_record_error_or_captured_output(
    scenario: Scenario,
    failure: subprocess.CompletedProcess[str] | BaseException,
    capfd: pytest.CaptureFixture[str],
) -> None:
    scenario.router.respond(RUNS_NEEDLE, failure)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.GITHUB_READ_FAILED)
    assert scenario.router.envs == [STORED_ENV]
    assert all(TRANSPORT_SENTINEL not in part for argv in scenario.router.calls for part in argv)
    assert TRANSPORT_SENTINEL not in self_update.status_path().read_text(encoding="utf-8")
    with pytest.raises(self_update_github.GithubReadError) as caught:
        self_update_github.push_runs(OWNER, REPO, scenario.fetched)
    assert TRANSPORT_SENTINEL not in str(caught.value)
    assert TRANSPORT_SENTINEL not in "".join(traceback.format_exception(caught.value))
    captured = capfd.readouterr()
    assert TRANSPORT_SENTINEL not in captured.out + captured.err


@pytest.mark.parametrize("mutate", UNUSABLE_RECORDS.values(), ids=UNUSABLE_RECORDS.keys())
def test_an_unusable_record_refuses_without_invoking_the_cli_and_names_no_stored_bytes(
    scenario: Scenario, mutate: Mutation
) -> None:
    mutate(record_path())
    scenario.set_green()
    assert_refused(scenario)
    with pytest.raises(self_update_github.GithubReadError) as caught:
        self_update_github.push_runs(OWNER, REPO, scenario.fetched)
    assert REFUSAL in str(caught.value)
    assert TRANSPORT_SENTINEL not in str(caught.value)
    assert TRANSPORT_SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert scenario.router.calls == []


def test_gate_check_runs_also_refuses_without_invoking_the_cli(scenario: Scenario) -> None:
    secrets_store.delete_secret(SLUG, "transport")
    with pytest.raises(self_update_github.GithubReadError, match=REFUSAL):
        self_update_github.gate_check_runs(OWNER, REPO, SUITE_ID)
    assert scenario.router.calls == []


def test_a_refusal_records_and_prints_nothing(
    scenario: Scenario,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    secrets_store.delete_secret(SLUG, "transport")
    caplog.set_level(logging.DEBUG)
    assert_refused(scenario)
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []


def test_an_ambient_token_in_the_environment_is_never_a_fallback(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "ALAN_VERIFIER_GH_TOKEN"):
        monkeypatch.setenv(name, AMBIENT_VALUE)
    secrets_store.delete_secret(SLUG, "transport")
    assert_refused(scenario)


def test_a_stored_value_the_operating_system_cannot_pass_is_a_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_transport_credential(value="embedded\x00null")
    raised: list[type[BaseException]] = []
    real = process_runner.run_subprocess

    def recording(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cmd
        try:
            return real([sys.executable, "-c", "pass"], cwd=cwd, timeout=timeout, env=env)
        except BaseException as exc:
            raised.append(type(exc))
            raise

    monkeypatch.setattr(process_runner, "run_subprocess", recording)
    with pytest.raises(self_update_github.GithubReadError) as caught:
        self_update_github.push_runs(OWNER, REPO, "a" * 40)
    assert raised == [ValueError]
    assert "embedded" not in str(caught.value)


def test_a_mixed_case_origin_reads_the_record_the_service_store_wrote(
    scenario: Scenario,
) -> None:
    point_origin_at_mixed_case(scenario.repo)
    secrets_store.write_secret(MIXED_SLUG, "transport", MIXED_VALUE)
    scenario.router.expected = MIXED_VALUE
    scenario.set_green()
    record = check(scenario.repo)
    assert record.verdict is Verdict.GREEN, (
        "alan/self_update_github.py mirrors secrets_store._key_path and must read the record "
        "secrets_store.write_secret stored for the case-preserved origin slug"
    )
    assert scenario.router.envs == [{"GH_TOKEN": MIXED_VALUE}] * 2
    assert all(f"repos/{MIXED_SLUG}/" in argv[-1] for argv in scenario.router.calls)
    assert [argv[-2] for argv, _ in scenario.fetch_calls] == [
        f"https://github.com/{MIXED_SLUG}.git"
    ]


def test_a_record_stored_only_under_the_lowercased_slug_is_not_found(
    scenario: Scenario,
) -> None:
    point_origin_at_mixed_case(scenario.repo)
    secrets_store.write_secret(MIXED_SLUG.lower(), "transport", MIXED_VALUE)
    scenario.set_green()
    assert_refused(scenario)


def test_a_padded_stored_value_is_sent_stripped(scenario: Scenario) -> None:
    store_transport_credential(value=f"  {TRANSPORT_SENTINEL}\n")
    scenario.set_green()
    check(scenario.repo)
    assert scenario.router.envs == [STORED_ENV] * 2


def test_re_pointing_the_store_root_ignores_the_old_roots_record(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "elsewhere"))
    scenario.set_green()
    assert_refused(scenario)


def test_a_dirty_tree_decides_after_one_credential_read_for_the_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scenario.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    reads = count_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.DIRTY_TREE)
    assert len(reads) == len(scenario.fetch_calls) == 1
    assert scenario.router.calls == []


def test_an_off_main_checkout_decides_without_reading_the_credential(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(scenario.repo, "checkout", "-q", "-b", "topic")
    forbid_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert scenario.router.calls == []


def test_an_up_to_date_checkout_decides_after_one_credential_read_for_the_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = build_scenario(monkeypatch, tmp_path, behind=False)
    reads = count_credential_reads(monkeypatch)
    record = check(current.repo)
    assert (record.verdict, record.reason) == (Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    assert len(reads) == len(current.fetch_calls) == 1
    assert current.router.calls == []


def test_a_failed_fetch_decides_after_one_credential_read(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario.router.fail_fetch(completed(returncode=128))
    reads = count_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert len(reads) == len(scenario.fetch_calls) == 1
    assert scenario.router.calls == []


def test_a_forced_decision_reads_the_credential_once_for_the_fetch_and_never_runs_the_cli(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = count_credential_reads(monkeypatch)
    outcome, _ = self_update.decide(scenario.repo, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.OPERATOR_FORCED)
    assert len(reads) == len(scenario.fetch_calls) == 1
    assert scenario.router.calls == []


def test_a_pending_ci_path_reads_the_store_once_per_cli_spawn(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = count_credential_reads(monkeypatch)
    scenario.set_runs()
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert len(reads) == len(scenario.router.calls) + len(scenario.fetch_calls) == 2


def test_a_green_ci_path_reads_the_store_once_per_cli_spawn(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = count_credential_reads(monkeypatch)
    scenario.set_green()
    check(scenario.repo)
    assert len(reads) == len(scenario.router.calls) + len(scenario.fetch_calls) == 3


def test_a_stored_value_with_an_embedded_null_fetches_through_the_header_and_fails_the_cli_read(
    scenario: Scenario,
) -> None:
    store_transport_credential(value=NULL_VALUE)
    scenario.router.expected = NULL_VALUE
    scenario.router.respond(RUNS_NEEDLE, ValueError("embedded null byte"))
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.GITHUB_READ_FAILED)
    ((_, env),) = scenario.fetch_calls
    assert env is not None
    assert all("\x00" not in value for value in env.values() if value is not None)


def test_transport_token_returns_the_stripped_stored_value(scenario: Scenario) -> None:
    store_transport_credential(value=f"  {TRANSPORT_SENTINEL}\n")
    assert self_update_github.transport_token(OWNER, REPO) == TRANSPORT_SENTINEL


@pytest.mark.parametrize("mutate", UNUSABLE_RECORDS.values(), ids=UNUSABLE_RECORDS.keys())
def test_transport_token_is_none_for_every_unusable_record(
    scenario: Scenario, mutate: Mutation
) -> None:
    mutate(record_path())
    assert self_update_github.transport_token(OWNER, REPO) is None
    assert scenario.router.calls == []


def test_the_module_body_runs_no_call_outside_function_and_class_definitions() -> None:
    tree = ast.parse(inspect.getsource(self_update_github))
    top_level = [
        node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.ClassDef))
    ]
    assert top_level
    assert not any(
        isinstance(inner, ast.Call) for node in top_level for inner in ast.walk(node)
    )
