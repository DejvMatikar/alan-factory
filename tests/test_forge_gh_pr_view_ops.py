from __future__ import annotations

import subprocess
import typing
from collections.abc import Callable

import pytest

from alan import process_runner
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, ForgePort
from alan.forge_gh import GhForge
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

_API_OPERATIONS: dict[str, Callable[[], ApiResult]] = {
    "head_sha": lambda: _FORGE.head_sha(42, forge_target=_TARGET),
    "pr_head_branch": lambda: _FORGE.pr_head_branch(42, forge_target=_TARGET),
    "request_branch_update": lambda: _FORGE.request_branch_update(42, forge_target=_TARGET),
    "pr_open_state": lambda: _FORGE.pr_open_state(42, forge_target=_TARGET),
    "open_pr_titles": lambda: _FORGE.open_pr_titles(forge_target=_TARGET),
}
_ALL_OPERATIONS: dict[str, Callable[[], object]] = {
    **_API_OPERATIONS,
    "changed_files": lambda: _FORGE.changed_files(42, forge_target=_TARGET),
}
_TRANSPORT_FAULTS = [
    FileNotFoundError("gh not on PATH"),
    subprocess.TimeoutExpired(cmd="gh", timeout=1.0),
]
_FAULT_IDS = ["missing-binary", "timeout"]


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout: str | None = ""
        self.stderr: str | None = ""
        self.returncode = 0
        self.raises: BaseException | None = None

    def __call__(
        self, argv: list[str], *, cwd: str | None, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), {"cwd": cwd, "timeout": timeout}))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=self.stdout, stderr=self.stderr,
        )


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> _Runner:
    fake = _Runner()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return fake


def test_pr_head_branch_builds_exact_argv(runner: _Runner) -> None:
    _FORGE.pr_head_branch(42, forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "view", "42", "--repo", "acme/widget",
        "--json", "headRefName", "-q", ".headRefName",
    ]


def test_head_sha_builds_exact_argv(runner: _Runner) -> None:
    _FORGE.head_sha(42, forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "view", "42", "--repo", "acme/widget",
        "--json", "headRefOid", "-q", ".headRefOid",
    ]


def test_pr_open_state_builds_exact_argv(runner: _Runner) -> None:
    _FORGE.pr_open_state(42, forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "view", "42", "--repo", "acme/widget",
        "--json", "state", "-q", ".state",
    ]


def test_request_branch_update_builds_exact_argv(runner: _Runner) -> None:
    _FORGE.request_branch_update(42, forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "update-branch", "42", "--repo", "acme/widget",
    ]


def test_changed_files_builds_exact_argv_with_name_only_before_the_number(
    runner: _Runner,
) -> None:
    _FORGE.changed_files(42, forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "diff", "--name-only", "42", "--repo", "acme/widget",
    ]


def test_open_pr_titles_builds_exact_argv_with_no_repository_flag(runner: _Runner) -> None:
    _FORGE.open_pr_titles(forge_target=_TARGET)
    assert runner.calls[0][0] == [
        "gh", "pr", "list", "--state", "open", "--json", "number,title", "--limit", "100",
    ]


def test_open_pr_titles_argv_ignores_the_forge_target(runner: _Runner) -> None:
    _FORGE.open_pr_titles(forge_target=_TARGET)
    _FORGE.open_pr_titles(forge_target=_OTHER_TARGET)
    assert runner.calls[0][0] == runner.calls[1][0]
    assert "--repo" not in runner.calls[1][0]


def test_view_operations_target_the_repository_of_the_forge_target(runner: _Runner) -> None:
    _FORGE.pr_head_branch(1, forge_target=_OTHER_TARGET)
    _FORGE.head_sha(1, forge_target=_OTHER_TARGET)
    _FORGE.pr_open_state(1, forge_target=_OTHER_TARGET)
    _FORGE.request_branch_update(1, forge_target=_OTHER_TARGET)
    _FORGE.changed_files(1, forge_target=_OTHER_TARGET)
    slugs = [argv[argv.index("--repo") + 1] for argv, _kwargs in runner.calls]
    assert slugs == ["other/repo"] * 5


@pytest.mark.parametrize("name", sorted(_ALL_OPERATIONS))
def test_operation_reaches_the_runner_with_only_cwd_and_timeout(
    runner: _Runner, name: str,
) -> None:
    _ALL_OPERATIONS[name]()
    assert [kwargs for _argv, kwargs in runner.calls] == [
        {"cwd": None, "timeout": GH_TIMEOUT_S},
    ]


@pytest.mark.parametrize("name", sorted(_API_OPERATIONS))
@pytest.mark.parametrize("fault", _TRANSPORT_FAULTS, ids=_FAULT_IDS)
def test_api_operation_lets_transport_faults_propagate(
    runner: _Runner, name: str, fault: BaseException,
) -> None:
    runner.raises = fault
    with pytest.raises(type(fault)):
        _API_OPERATIONS[name]()


@pytest.mark.parametrize("fault", _TRANSPORT_FAULTS, ids=_FAULT_IDS)
def test_changed_files_reports_none_on_a_transport_fault(
    runner: _Runner, fault: BaseException,
) -> None:
    runner.raises = fault
    assert _FORGE.changed_files(42, forge_target=_TARGET) is None


@pytest.mark.parametrize("name", sorted(_API_OPERATIONS))
def test_api_operation_reports_a_nonzero_exit_without_raising(
    runner: _Runner, name: str,
) -> None:
    runner.returncode = 3
    runner.stdout = "partial"
    runner.stderr = "boom"
    assert _API_OPERATIONS[name]() == ApiResult(
        ok=False, stdout="partial", returncode=3, stderr="boom",
    )


def test_changed_files_reports_none_on_a_nonzero_exit(runner: _Runner) -> None:
    runner.returncode = 1
    runner.stdout = "alan/forge.py\n"
    assert _FORGE.changed_files(42, forge_target=_TARGET) is None


@pytest.mark.parametrize("name", sorted(_API_OPERATIONS))
def test_api_operation_reads_none_output_as_empty_strings(
    runner: _Runner, name: str,
) -> None:
    runner.stdout = None
    runner.stderr = None
    assert _API_OPERATIONS[name]() == ApiResult(ok=True, stdout="", returncode=0, stderr="")


@pytest.mark.parametrize("name", sorted(_API_OPERATIONS))
def test_api_operation_returns_the_raw_unstripped_output(runner: _Runner, name: str) -> None:
    runner.stdout = "teams/x\n"
    assert _API_OPERATIONS[name]() == ApiResult(
        ok=True, stdout="teams/x\n", returncode=0, stderr="",
    )


def test_changed_files_returns_the_stripped_non_blank_names(runner: _Runner) -> None:
    runner.stdout = " alan/foo.py \n\nalan/bar.py\n   \n"
    assert _FORGE.changed_files(42, forge_target=_TARGET) == frozenset(
        {"alan/foo.py", "alan/bar.py"},
    )


def test_changed_files_reads_none_output_as_an_empty_set(runner: _Runner) -> None:
    runner.stdout = None
    assert _FORGE.changed_files(42, forge_target=_TARGET) == frozenset()


@pytest.mark.parametrize(
    "method", [ForgePort.head_sha, GhForge.head_sha], ids=["port", "adapter"],
)
def test_head_sha_is_typed_to_return_an_api_result(
    method: Callable[..., object],
) -> None:
    assert typing.get_type_hints(method)["return"] is ApiResult
