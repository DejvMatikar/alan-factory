from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, FileRead, LoginRead, RowsRead
from alan.forge_gh import (
    GhForge,
    _attempt,  # pyright: ignore[reportPrivateUsage]
    _file_read,  # pyright: ignore[reportPrivateUsage]
    _rows_read,  # pyright: ignore[reportPrivateUsage]
)

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)
_FORGE = GhForge()


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout: str | None = ""
        self.stderr: str | None = ""
        self.returncode = 0
        self.raises: BaseException | None = None

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr,
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Transport:
    fake = _Transport()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "transport_credential", lambda _t: "transport-tok")
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: {"GH_TOKEN": "verifier-tok"})
    return fake


def test_open_prs_builds_the_list_argv_with_repo_state_and_limit(transport: _Transport) -> None:
    transport.stdout = '[{"number": 1, "title": "t", "url": "u"}]'
    result = _FORGE.open_prs(forge_target=_TARGET, limit=7, timeout=12.0)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "list", "--repo", "acme/widget", "--state", "open",
        "--json", "number,title,url", "--limit", "7",
    ]
    assert kwargs == {"cwd": None, "timeout": 12.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == RowsRead(
        outcome="ok", rows=({"number": 1, "title": "t", "url": "u"},), returncode=0, stderr="",
    )


def test_open_prs_ok_with_empty_list_reports_empty_rows(transport: _Transport) -> None:
    transport.stdout = "[]"
    result = _FORGE.open_prs(forge_target=_TARGET, limit=7, timeout=12.0)
    assert result.outcome == "ok"
    assert result.rows == ()


def test_open_prs_ok_with_non_list_payload_reports_rows_as_none(transport: _Transport) -> None:
    transport.stdout = '{"not": "a list"}'
    result = _FORGE.open_prs(forge_target=_TARGET, limit=7, timeout=12.0)
    assert result.outcome == "ok"
    assert result.rows is None


def test_open_prs_nonzero_exit_reports_failed(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stderr = "denied"
    result = _FORGE.open_prs(forge_target=_TARGET, limit=7, timeout=12.0)
    assert result == RowsRead(outcome="failed", rows=None, returncode=1, stderr="denied")


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_open_prs_transport_failure_reports_raised(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _FORGE.open_prs(forge_target=_TARGET, limit=7, timeout=12.0)
    assert result.outcome == "raised"
    assert result.rows is None
    assert str(error) in result.stderr


def test_workflow_runs_builds_the_run_list_argv_with_repo_workflow_and_limit(
    transport: _Transport,
) -> None:
    transport.stdout = '[{"databaseId": 1}]'
    result = _FORGE.workflow_runs("ci-deploy.yml", forge_target=_TARGET, limit=15, timeout=20.0)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "run", "list", "--repo", "acme/widget", "--workflow", "ci-deploy.yml",
        "--json", "databaseId,createdAt,status,conclusion", "--limit", "15",
    ]
    assert kwargs == {"cwd": None, "timeout": 20.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result.outcome == "ok"
    assert result.rows == ({"databaseId": 1},)


def test_workflow_runs_ok_with_empty_list_reports_empty_rows(transport: _Transport) -> None:
    transport.stdout = "[]"
    result = _FORGE.workflow_runs("ci-deploy.yml", forge_target=_TARGET, limit=15, timeout=20.0)
    assert result.outcome == "ok"
    assert result.rows == ()


def test_workflow_runs_ok_with_non_list_payload_reports_rows_as_none(
    transport: _Transport,
) -> None:
    transport.stdout = "not json"
    result = _FORGE.workflow_runs("ci-deploy.yml", forge_target=_TARGET, limit=15, timeout=20.0)
    assert result.outcome == "ok"
    assert result.rows is None


def test_workflow_runs_nonzero_exit_reports_failed(transport: _Transport) -> None:
    transport.returncode = 2
    transport.stderr = "boom"
    result = _FORGE.workflow_runs("ci-deploy.yml", forge_target=_TARGET, limit=15, timeout=20.0)
    assert result == RowsRead(outcome="failed", rows=None, returncode=2, stderr="boom")


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_workflow_runs_transport_failure_reports_raised(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _FORGE.workflow_runs("ci-deploy.yml", forge_target=_TARGET, limit=15, timeout=20.0)
    assert result.outcome == "raised"
    assert result.rows is None


def test_viewer_login_builds_the_user_argv_under_the_transport_identity(
    transport: _Transport,
) -> None:
    transport.stdout = "octocat\n"
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "user", "-q", ".login"]
    assert kwargs == {"cwd": None, "timeout": 5.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == LoginRead(outcome="ok", login="octocat", returncode=0, stderr="")


def test_viewer_login_as_verifier_selects_the_verifier_environment(
    transport: _Transport,
) -> None:
    transport.stdout = "verifier-bot\n"
    _FORGE.viewer_login(forge_target=_TARGET, as_verifier=True, timeout=5.0)
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "verifier-tok"}


def test_viewer_login_empty_stdout_reports_failed(transport: _Transport) -> None:
    transport.stdout = ""
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0)
    assert result.outcome == "failed"
    assert result.login is None


def test_viewer_login_nonzero_exit_reports_failed(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stderr = "bad credentials"
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0)
    assert result == LoginRead(
        outcome="failed", login=None, returncode=1, stderr="bad credentials",
    )


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_viewer_login_transport_failure_reports_raised(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0)
    assert result.outcome == "raised"
    assert result.login is None


def test_viewer_login_reports_no_credential_and_never_invokes_transport(
    monkeypatch: pytest.MonkeyPatch, transport: _Transport,
) -> None:
    monkeypatch.setattr(secrets_store, "transport_credential", lambda _t: None)
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0)
    assert result == LoginRead(outcome="no_credential", login=None, returncode=-1, stderr="")
    assert transport.calls == []


def test_viewer_login_as_verifier_reports_no_credential_when_verifier_env_is_empty(
    monkeypatch: pytest.MonkeyPatch, transport: _Transport,
) -> None:
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: {})
    result = _FORGE.viewer_login(forge_target=_TARGET, as_verifier=True, timeout=5.0)
    assert result.outcome == "no_credential"
    assert transport.calls == []


def test_repo_file_builds_the_contents_argv_under_the_transport_identity(
    transport: _Transport,
) -> None:
    transport.stdout = '{"content": "base64"}'
    result = _FORGE.repo_file(".github/dependabot.yml", forge_target=_TARGET, timeout=30.0)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "repos/acme/widget/contents/.github%2Fdependabot.yml"]
    assert kwargs == {"cwd": None, "timeout": 30.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == FileRead(outcome="ok", text='{"content": "base64"}', returncode=0, stderr="")


def test_repo_file_http_404_stderr_reports_absent(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stderr = "HTTP 404: Not Found"
    result = _FORGE.repo_file(".github/dependabot.yml", forge_target=_TARGET, timeout=30.0)
    assert result == FileRead(
        outcome="absent", text=None, returncode=1, stderr="HTTP 404: Not Found",
    )


def test_repo_file_appends_quoted_ref_query(transport: _Transport) -> None:
    transport.stdout = '{"content": "base64"}'
    result = _FORGE.repo_file(
        ".github/CODEOWNERS", forge_target=_TARGET, timeout=30.0, ref="release/1.0",
    )
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "repos/acme/widget/contents/.github%2FCODEOWNERS?ref=release%2F1.0",
    ]
    assert kwargs == {"cwd": None, "timeout": 30.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == FileRead(outcome="ok", text='{"content": "base64"}', returncode=0, stderr="")


def test_repo_file_other_nonzero_exit_reports_failed(transport: _Transport) -> None:
    transport.returncode = 1
    transport.stderr = "HTTP 403: Forbidden"
    result = _FORGE.repo_file(".github/dependabot.yml", forge_target=_TARGET, timeout=30.0)
    assert result == FileRead(
        outcome="failed", text=None, returncode=1, stderr="HTTP 403: Forbidden",
    )


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_repo_file_transport_failure_reports_raised(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    result = _FORGE.repo_file(".github/dependabot.yml", forge_target=_TARGET, timeout=30.0)
    assert result.outcome == "raised"
    assert result.text is None


def test_prs_by_head_builds_the_list_argv_with_head_state_all_and_limit(
    transport: _Transport,
) -> None:
    transport.stdout = '[{"number": 11, "url": "u"}]'
    result = _FORGE.prs_by_head(
        "alan/repo-scaffold", ("number", "url"), forge_target=_TARGET, limit=1, timeout=60.0,
    )
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "list", "--repo", "acme/widget", "--head", "alan/repo-scaffold",
        "--state", "all", "--json", "number,url", "--limit", "1",
    ]
    assert kwargs == {"cwd": None, "timeout": 60.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == RowsRead(
        outcome="ok", rows=({"number": 11, "url": "u"},), returncode=0, stderr="",
    )


def test_prs_by_head_absorbs_a_raised_transport_fault(transport: _Transport) -> None:
    transport.raises = PermissionError("denied")
    result = _FORGE.prs_by_head(
        "alan/repo-scaffold", ("number", "url"), forge_target=_TARGET, limit=1, timeout=60.0,
    )
    assert result.outcome == "raised"
    assert result.rows is None


def test_create_pr_builds_the_create_argv_in_the_clone_with_the_transport_credential(
    transport: _Transport, tmp_path: Path,
) -> None:
    transport.stdout = "https://github.com/acme/widget/pull/7\n"
    workdir = tmp_path / "repo"
    result = _FORGE.create_pr(
        "title", "body", "alan/repo-scaffold",
        forge_target=_TARGET, workdir=workdir, timeout=60.0,
    )
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "create", "--repo", "acme/widget",
        "--title", "title", "--body", "body", "--head", "alan/repo-scaffold",
    ]
    assert kwargs == {"cwd": str(workdir), "timeout": 60.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == ApiResult(
        ok=True, stdout="https://github.com/acme/widget/pull/7\n", returncode=0, stderr="",
    )


def test_create_pr_propagates_a_transport_fault(transport: _Transport, tmp_path: Path) -> None:
    transport.raises = FileNotFoundError("gh")
    with pytest.raises(FileNotFoundError):
        _FORGE.create_pr(
            "title", "body", "alan/repo-scaffold",
            forge_target=_TARGET, workdir=tmp_path, timeout=60.0,
        )


def test_create_pr_from_checkout_runs_the_ambient_argv_without_an_environment(
    transport: _Transport, tmp_path: Path,
) -> None:
    transport.stdout = "https://github.com/acme/widget/pull/7\n"
    workdir = tmp_path / "worktree"
    result = _FORGE.create_pr_from_checkout(
        "title", "body", "teams/x", forge_target=_TARGET, workdir=workdir, timeout=60.0,
    )
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "create", "--title", "title", "--body", "body",
        "--base", "main", "--head", "teams/x",
    ]
    assert kwargs == {"cwd": str(workdir), "timeout": 60.0}
    assert result == ApiResult(
        ok=True, stdout="https://github.com/acme/widget/pull/7\n", returncode=0, stderr="",
    )


def test_create_pr_from_checkout_ignores_the_forge_target(
    transport: _Transport, tmp_path: Path,
) -> None:
    other = ForgeTarget(
        repo=RepoIdentity(owner="other", repo="elsewhere"),
        credential_env_name="OTHER_GH_TOKEN",  # noqa: S106  # nosec B106
    )
    for target in (_TARGET, other):
        _FORGE.create_pr_from_checkout(
            "title", "body", "teams/x", forge_target=target, workdir=tmp_path, timeout=60.0,
        )
    first, second = (argv for argv, _kwargs in transport.calls)
    assert first == second
    assert "--repo" not in first


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=60.0)],
)
def test_create_pr_from_checkout_propagates_a_transport_fault(
    error: BaseException, transport: _Transport, tmp_path: Path,
) -> None:
    transport.raises = error
    with pytest.raises(type(error)):
        _FORGE.create_pr_from_checkout(
            "title", "body", "teams/x", forge_target=_TARGET, workdir=tmp_path, timeout=60.0,
        )


def test_create_pr_from_checkout_maps_a_nonzero_exit_to_a_failed_result(
    transport: _Transport, tmp_path: Path,
) -> None:
    transport.returncode = 1
    transport.stderr = "x"
    result = _FORGE.create_pr_from_checkout(
        "title", "body", "teams/x", forge_target=_TARGET, workdir=tmp_path, timeout=60.0,
    )
    assert result == ApiResult(ok=False, stdout="", returncode=1, stderr="x")


def test_pr_state_builds_the_view_argv_with_server_side_state_extraction(
    transport: _Transport,
) -> None:
    transport.stdout = "MERGED\n"
    result = _FORGE.pr_state(7, forge_target=_TARGET, timeout=60.0)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "pr", "view", "7", "--repo", "acme/widget", "--json", "state", "-q", ".state",
    ]
    assert kwargs == {"cwd": None, "timeout": 60.0, "env": {"GH_TOKEN": "transport-tok"}}
    assert result == ApiResult(ok=True, stdout="MERGED\n", returncode=0, stderr="")


def test_pr_state_propagates_a_transport_fault(transport: _Transport) -> None:
    transport.raises = subprocess.TimeoutExpired(cmd="gh", timeout=1)
    with pytest.raises(subprocess.TimeoutExpired):
        _FORGE.pr_state(7, forge_target=_TARGET, timeout=60.0)


def test_attempt_returns_the_completed_process_on_success(transport: _Transport) -> None:
    transport.stdout = "ok"
    attempt = _attempt(["gh", "api", "user"], forge_target=_TARGET, env=None, timeout=5.0)
    assert isinstance(attempt, subprocess.CompletedProcess)
    assert attempt.stdout == "ok"


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_attempt_returns_the_exception_text_on_a_transport_fault(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    attempt = _attempt(["gh", "api", "user"], forge_target=_TARGET, env=None, timeout=5.0)
    assert attempt == str(error)


def test_rows_read_wraps_a_transport_fault_as_raised(transport: _Transport) -> None:
    transport.raises = FileNotFoundError("gh")
    result = _rows_read(["gh", "pr", "list"], forge_target=_TARGET, env=None, timeout=5.0)
    assert result.outcome == "raised"
    assert result.rows is None


def test_file_read_wraps_a_transport_fault_as_raised(transport: _Transport) -> None:
    transport.raises = FileNotFoundError("gh")
    result = _file_read(
        ["gh", "api", "repos/acme/widget/contents/x"],
        forge_target=_TARGET, env=None, timeout=5.0,
    )
    assert result.outcome == "raised"
    assert result.text is None
