from __future__ import annotations

import inspect
import json
import logging
import subprocess
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from alan import forge_gh, process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, ForgePort
from alan.forge_gh import GhForge
from alan.teams import _pr_branch
from alan.teams._pr_forge import GH_TIMEOUT_S

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)
_OTHER_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="other", repo="repo"),
    credential_env_name="OTHER_GH_TOKEN",  # noqa: S106  # nosec B106
)
_FORGE = GhForge()

_ANNOTATIONS_ARGV = ["gh", "api", "repos/acme/widget/check-runs/7/annotations?per_page=100"]
_ROLLUP_ARGV = [
    "gh", "pr", "view", "42", "--repo", "acme/widget", "--json", "statusCheckRollup",
]
_AMBIENT_KWARGS: dict[str, object] = {"cwd": None, "timeout": GH_TIMEOUT_S}

_CALLS: dict[str, Callable[[GhForge], ApiResult]] = {
    "commit_check_runs_page": lambda f: f.commit_check_runs_page("abc123", forge_target=_TARGET),
    "check_run_annotations": lambda f: f.check_run_annotations(
        7, forge_target=_TARGET, timeout=9.0,
    ),
    "transport_check_run_annotations": lambda f: f.transport_check_run_annotations(
        7, forge_target=_TARGET, timeout=9.0,
    ),
    "rerequest_check_suite": lambda f: f.rerequest_check_suite(9, forge_target=_TARGET),
    "rerun_failed_jobs": lambda f: f.rerun_failed_jobs(11, forge_target=_TARGET),
    "status_check_rollup": lambda f: f.status_check_rollup(
        42, forge_target=_TARGET, timeout=9.0,
    ),
    "failed_job_log": lambda f: f.failed_job_log(13, forge_target=_TARGET, timeout=9.0),
}
_TRANSPORT_OPERATIONS = (
    "transport_check_run_annotations", "status_check_rollup", "failed_job_log",
)
_TIMEOUT_OPERATIONS = (
    "check_run_annotations", "transport_check_run_annotations",
    "status_check_rollup", "failed_job_log",
)
_TRANSPORT_ERRORS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)]


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout: str | Callable[[list[str]], str] = ""
        self.stderr = ""
        self.returncode = 0
        self.raises: BaseException | None = None

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        stdout = self.stdout(list(cmd)) if callable(self.stdout) else self.stdout
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=stdout, stderr=self.stderr,
        )


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    by_slug = {"acme/widget": "transport-tok", "other/repo": "other-tok"}
    monkeypatch.setattr(
        secrets_store, "transport_credential",
        lambda target: by_slug.get(target.repo.slug),
    )
    return by_slug


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str]) -> _Runner:
    del tokens
    fake = _Runner()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return fake


def _rollup_payload(job_ids: list[int]) -> str:
    return json.dumps({"statusCheckRollup": [
        {
            "conclusion": "FAILURE",
            "detailsUrl": f"https://github.com/acme/widget/actions/runs/1/job/{job_id}",
        }
        for job_id in job_ids
    ]})


def test_commit_check_runs_page_lists_one_hundred_runs_as_the_ambient_identity(
    runner: _Runner,
) -> None:
    _CALLS["commit_check_runs_page"](_FORGE)
    assert runner.calls == [(
        ["gh", "api", "repos/acme/widget/commits/abc123/check-runs?per_page=100"],
        _AMBIENT_KWARGS,
    )]


def test_check_run_annotations_reads_one_hundred_annotations_as_the_ambient_identity(
    runner: _Runner,
) -> None:
    _CALLS["check_run_annotations"](_FORGE)
    assert runner.calls == [(_ANNOTATIONS_ARGV, {"cwd": None, "timeout": 9.0})]


def test_transport_check_run_annotations_reads_the_same_argv_as_the_transport_identity(
    runner: _Runner,
) -> None:
    _CALLS["transport_check_run_annotations"](_FORGE)
    assert runner.calls == [(
        _ANNOTATIONS_ARGV,
        {"cwd": None, "timeout": 9.0, "env": {"GH_TOKEN": "transport-tok"}},
    )]


def test_rerequest_check_suite_posts_the_rerequest_as_the_ambient_identity(
    runner: _Runner,
) -> None:
    _CALLS["rerequest_check_suite"](_FORGE)
    assert runner.calls == [(
        ["gh", "api", "-X", "POST", "repos/acme/widget/check-suites/9/rerequest"],
        _AMBIENT_KWARGS,
    )]


def test_rerun_failed_jobs_reruns_only_the_failed_jobs_as_the_ambient_identity(
    runner: _Runner,
) -> None:
    _CALLS["rerun_failed_jobs"](_FORGE)
    assert runner.calls == [(
        ["gh", "run", "rerun", "11", "--failed", "--repo", "acme/widget"],
        _AMBIENT_KWARGS,
    )]


def test_status_check_rollup_reads_the_pr_rollup_as_the_transport_identity(
    runner: _Runner,
) -> None:
    _CALLS["status_check_rollup"](_FORGE)
    assert runner.calls == [(
        _ROLLUP_ARGV,
        {"cwd": None, "timeout": 9.0, "env": {"GH_TOKEN": "transport-tok"}},
    )]


def test_failed_job_log_reads_the_failed_log_as_the_transport_identity(
    runner: _Runner,
) -> None:
    _CALLS["failed_job_log"](_FORGE)
    assert runner.calls == [(
        ["gh", "run", "view", "--repo", "acme/widget", "--job", "13", "--log-failed"],
        {"cwd": None, "timeout": 9.0, "env": {"GH_TOKEN": "transport-tok"}},
    )]


@pytest.mark.parametrize("name", _TRANSPORT_OPERATIONS)
def test_transport_operations_pass_no_environment_when_no_token_resolves(
    name: str, runner: _Runner, tokens: dict[str, str],
) -> None:
    tokens.clear()
    _CALLS[name](_FORGE)
    [(_argv, kwargs)] = runner.calls
    assert kwargs.get("env") is None


@pytest.mark.parametrize("name", sorted(set(_CALLS) - set(_TRANSPORT_OPERATIONS)))
def test_ambient_operations_pass_neither_an_environment_nor_a_credential(
    name: str, runner: _Runner,
) -> None:
    _CALLS[name](_FORGE)
    [(_argv, kwargs)] = runner.calls
    assert "env" not in kwargs


@pytest.mark.parametrize("name", sorted(_CALLS))
def test_every_operation_returns_the_raw_result_of_a_failed_command(
    name: str, runner: _Runner,
) -> None:
    runner.returncode = 3
    runner.stdout = "out"
    runner.stderr = "err"
    assert _CALLS[name](_FORGE) == ApiResult(
        ok=False, stdout="out", returncode=3, stderr="err",
    )


@pytest.mark.parametrize("name", sorted(_CALLS))
def test_every_operation_returns_ok_with_the_stdout_of_a_succeeded_command(
    name: str, runner: _Runner,
) -> None:
    runner.stdout = "payload"
    assert _CALLS[name](_FORGE) == ApiResult(ok=True, stdout="payload", returncode=0, stderr="")


@pytest.mark.parametrize("error", _TRANSPORT_ERRORS, ids=["missing-executable", "timeout"])
@pytest.mark.parametrize("name", sorted(_CALLS))
def test_every_operation_lets_a_transport_failure_reach_the_caller(
    name: str, error: BaseException, runner: _Runner,
) -> None:
    runner.raises = error
    with pytest.raises(type(error)):
        _CALLS[name](_FORGE)


@pytest.mark.parametrize("name", sorted(_CALLS))
def test_every_operation_reads_the_runner_attribute_at_call_time(
    name: str, monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str],
) -> None:
    del tokens
    late = _Runner()
    monkeypatch.setattr(process_runner, "run_subprocess", late)
    _CALLS[name](_FORGE)
    assert len(late.calls) == 1


def test_no_operation_or_json_helper_reaches_process_runner_except_through_run_subprocess(
    monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str],
) -> None:
    del tokens
    double = _Runner()
    monkeypatch.setattr(forge_gh, "process_runner", SimpleNamespace(run_subprocess=double))
    for name in sorted(_CALLS):
        _CALLS[name](_FORGE)
    forge_gh.run_gh_api_json_input(
        ["gh", "api", "-X", "POST", "/x"], {"body": "b"}, forge_target=_TARGET,
    )
    assert len(double.calls) == len(_CALLS) + 1


def test_json_helper_reaches_the_runner_attribute_at_call_time_with_the_ambient_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = _Runner()
    monkeypatch.setattr(process_runner, "run_subprocess", late)
    forge_gh.run_gh_api_json_input(
        ["gh", "api", "-X", "POST", "/x"], {"body": "b"}, forge_target=_TARGET,
    )
    [(argv, kwargs)] = late.calls
    assert argv[:5] == ["gh", "api", "-X", "POST", "/x"]
    assert argv[5] == "--input"
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": None}


def test_failing_e2e_lookup_makes_one_rollup_read_before_five_annotation_reads(
    runner: _Runner,
) -> None:
    runner.stdout = lambda argv: (
        _rollup_payload([101, 102, 103, 104, 105, 106, 107])
        if argv[:3] == ["gh", "pr", "view"] else "[]"
    )
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=_TARGET, timeout_s=9.0) is None
    argvs = [argv for argv, _kwargs in runner.calls]
    assert argvs[0] == _ROLLUP_ARGV
    assert [a for a in argvs if "statusCheckRollup" in a] == [_ROLLUP_ARGV]
    assert argvs[1:] == [
        ["gh", "api", f"repos/acme/widget/check-runs/{job_id}/annotations?per_page=100"]
        for job_id in (101, 102, 103, 104, 105)
    ]
    assert all(kwargs["timeout"] == 9.0 for _argv, kwargs in runner.calls)
    assert all(
        kwargs["env"] == {"GH_TOKEN": "transport-tok"} for _argv, kwargs in runner.calls
    )


def test_failing_e2e_lookup_reads_no_annotation_when_the_rollup_names_no_failing_job(
    runner: _Runner,
) -> None:
    runner.stdout = _rollup_payload([])
    assert _pr_branch.fetch_failing_e2e_tests(42, forge_target=_TARGET) is None
    assert [argv for argv, _kwargs in runner.calls] == [_ROLLUP_ARGV]


def test_transport_annotation_read_uses_the_credential_of_the_forge_it_targets(
    runner: _Runner,
) -> None:
    _pr_branch.fetch_check_run_annotations(_OTHER_TARGET, 5, 42, as_transport=True)
    [(argv, kwargs)] = runner.calls
    assert argv == ["gh", "api", "repos/other/repo/check-runs/5/annotations?per_page=100"]
    assert kwargs["env"] == {"GH_TOKEN": "other-tok"}


def test_ambient_annotation_read_of_a_non_default_forge_passes_no_environment(
    runner: _Runner,
) -> None:
    _pr_branch.fetch_check_run_annotations(_OTHER_TARGET, 5, 42)
    [(_argv, kwargs)] = runner.calls
    assert "env" not in kwargs


def test_failing_job_ids_and_failed_log_read_through_the_port_as_the_transport_identity(
    runner: _Runner,
) -> None:
    runner.stdout = _rollup_payload([31])
    assert _pr_branch.fetch_failing_job_ids(42, 9.0, forge_target=_OTHER_TARGET) == [31]
    runner.stdout = "log text"
    assert _pr_branch.fetch_failed_log(31, 9.0, forge_target=_OTHER_TARGET) == "log text"
    assert [kwargs["env"] for _argv, kwargs in runner.calls] == [{"GH_TOKEN": "other-tok"}] * 2


_AUDITED_CALLS: dict[str, Callable[[Callable[[int, str], None]], object]] = {
    "_iter_check_runs": lambda on_fail: _pr_branch._iter_check_runs(  # noqa: SLF001
        _TARGET, "abc123", 42, on_fail=on_fail,
    ),
    "fetch_check_run_annotations": lambda on_fail: _pr_branch.fetch_check_run_annotations(
        _TARGET, 7, 42, on_fail=on_fail,
    ),
    "_rerequest_check_suite": lambda on_fail: _pr_branch._rerequest_check_suite(  # noqa: SLF001
        _TARGET, 9, 42, on_fail=on_fail,
    ),
    "_rerun_workflow_run": lambda on_fail: _pr_branch._rerun_workflow_run(  # noqa: SLF001
        _TARGET, 11, 42, on_fail=on_fail,
    ),
}


@pytest.mark.parametrize("label", sorted(_AUDITED_CALLS))
def test_a_raised_transport_failure_reports_the_subprocess_error_reason_and_label(
    label: str, runner: _Runner, caplog: pytest.LogCaptureFixture,
) -> None:
    reasons: list[tuple[int, str]] = []
    runner.raises = FileNotFoundError("gh")
    with caplog.at_level(logging.WARNING):
        result = _AUDITED_CALLS[label](lambda pr, reason: reasons.append((pr, reason)))
    assert not result
    assert reasons == [(42, "gh subprocess error: gh")]
    assert f"{label}: raised gh for PR #42" in caplog.text


@pytest.mark.parametrize("label", sorted(_AUDITED_CALLS))
def test_a_nonzero_exit_reports_the_returncode_and_first_two_hundred_stderr_characters(
    label: str, runner: _Runner, caplog: pytest.LogCaptureFixture,
) -> None:
    reasons: list[tuple[int, str]] = []
    runner.returncode = 1
    runner.stderr = "x" * 300
    with caplog.at_level(logging.WARNING):
        result = _AUDITED_CALLS[label](lambda pr, reason: reasons.append((pr, reason)))
    assert not result
    assert reasons == [(42, f"gh returncode=1: {'x' * 200}")]
    assert f"{label}: rc=1 stderr={'x' * 300}" in caplog.text


@pytest.mark.parametrize("label", ["_rerequest_check_suite", "_rerun_workflow_run"])
def test_a_zero_exit_reports_the_trigger_as_accepted_without_calling_on_fail(
    label: str, runner: _Runner,
) -> None:
    del runner
    reasons: list[tuple[int, str]] = []
    assert _AUDITED_CALLS[label](lambda pr, reason: reasons.append((pr, reason))) is True
    assert reasons == []


@pytest.mark.parametrize(
    "name",
    [
        "_gh_trigger_ok",
        "_transport_gh_env",
        "run_gh_api_json_input",
        "run_subprocess",
        "verifier_gh_env",
    ],
)
def test_the_removed_and_moved_helpers_are_gone_from_the_pr_branch_module(name: str) -> None:
    assert not hasattr(_pr_branch, name)


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
@pytest.mark.parametrize("name", _TIMEOUT_OPERATIONS)
def test_timeout_is_a_required_keyword_on_the_reads_that_carry_one(
    owner: type, name: str,
) -> None:
    param = inspect.signature(getattr(owner, name)).parameters["timeout"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
@pytest.mark.parametrize("name", sorted(set(_CALLS) - set(_TIMEOUT_OPERATIONS)))
def test_operations_without_a_caller_timeout_take_none(owner: type, name: str) -> None:
    assert "timeout" not in inspect.signature(getattr(owner, name)).parameters


def test_json_helper_is_a_module_function_and_not_a_public_adapter_operation() -> None:
    assert not hasattr(GhForge, "run_gh_api_json_input")
    assert inspect.isfunction(forge_gh.run_gh_api_json_input)
