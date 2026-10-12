from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from alan import forge_gh, process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, PermissionRead
from alan.forge_gh import GhForge
from alan.teams._pr_forge import GH_TIMEOUT_S

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)
_FORGE = GhForge()
_REVIEWER_ENV = {"GH_TOKEN": "reviewer-tok"}
_RESOLVE_MUTATION = (
    "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved,comments{totalCount}}}}"
)
_UNRESOLVE_MUTATION = (
    "mutation($threadId:ID!){unresolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved,comments{totalCount}}}}"
)
_MUTATIONS: dict[str, tuple[Callable[[], ApiResult], str]] = {
    "resolve": (
        lambda: _FORGE.resolve_thread_checked("PRRT_1", forge_target=_TARGET),
        _RESOLVE_MUTATION,
    ),
    "unresolve": (
        lambda: _FORGE.unresolve_thread("PRRT_1", forge_target=_TARGET),
        _UNRESOLVE_MUTATION,
    ),
}


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
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _ft: dict(_REVIEWER_ENV))
    return fake


def _permission(login: str = "octocat") -> PermissionRead:
    return _FORGE.repo_permission(login, forge_target=_TARGET, timeout=9.0)


def test_repo_permission_reads_the_collaborator_permission_endpoint_with_ambient_env(
    transport: _Transport,
) -> None:
    transport.stdout = "write\n"
    _permission()
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "repos/acme/widget/collaborators/octocat/permission", "-q", ".permission",
    ]
    assert kwargs == {"cwd": None, "timeout": 9.0, "env": None}


def test_repo_permission_quotes_the_login_into_the_url_path(transport: _Transport) -> None:
    transport.stdout = "read"
    _permission("a/b c?d")
    argv, _ = transport.calls[0]
    assert argv[2] == "repos/acme/widget/collaborators/a%2Fb%20c%3Fd/permission"


def test_repo_permission_returns_the_stripped_permission_on_success(
    transport: _Transport,
) -> None:
    transport.stdout = "  triage \n"
    transport.stderr = "warn"
    assert _permission() == PermissionRead(
        outcome="ok", permission="triage", returncode=0, stderr="warn",
    )


@pytest.mark.parametrize("stdout", ["", "  \n", None])
def test_repo_permission_is_failed_when_the_permission_is_empty(
    stdout: str | None, transport: _Transport,
) -> None:
    transport.stdout = stdout
    transport.stderr = None
    assert _permission() == PermissionRead(
        outcome="failed", permission=None, returncode=0, stderr="",
    )


def test_repo_permission_is_failed_on_a_nonzero_exit_even_with_stdout(
    transport: _Transport,
) -> None:
    transport.returncode = 1
    transport.stdout = "write"
    transport.stderr = "HTTP 404: Not Found"
    assert _permission() == PermissionRead(
        outcome="failed", permission=None, returncode=1, stderr="HTTP 404: Not Found",
    )


_FAULTS = [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1), OSError("x")]


@pytest.mark.parametrize("fault", _FAULTS)
def test_repo_permission_is_raised_when_the_transport_faults(
    fault: BaseException, transport: _Transport,
) -> None:
    transport.raises = fault
    assert _permission() == PermissionRead(
        outcome="raised", permission=None, returncode=-1, stderr=str(fault),
    )


@pytest.mark.parametrize("op", sorted(_MUTATIONS))
def test_thread_mutation_runs_the_exact_mutation_under_the_reviewer_env(
    op: str, transport: _Transport,
) -> None:
    call, mutation = _MUTATIONS[op]
    transport.stdout = '{"data": {}}'
    result = call()
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "api", "graphql", "-f", f"query={mutation}", "-f", "threadId=PRRT_1"]
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": _REVIEWER_ENV}
    assert result == ApiResult(ok=True, stdout='{"data": {}}', returncode=0, stderr="")


@pytest.mark.parametrize("op", sorted(_MUTATIONS))
def test_thread_mutation_reports_a_nonzero_exit_as_a_failed_result(
    op: str, transport: _Transport,
) -> None:
    call, _ = _MUTATIONS[op]
    transport.returncode = 1
    transport.stderr = "Resource not accessible by personal access token"
    result = call()
    assert result == ApiResult(
        ok=False, stdout="", returncode=1,
        stderr="Resource not accessible by personal access token",
    )


@pytest.mark.parametrize("op", sorted(_MUTATIONS))
def test_thread_mutation_without_a_reviewer_credential_never_spawns_gh(
    op: str, transport: _Transport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, _ = _MUTATIONS[op]
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _ft: {})
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *_a, **_k: "ambient-tok")
    result = call()
    assert transport.calls == []
    assert result == forge_gh._NO_VERIFIER_CREDENTIAL  # noqa: SLF001
    assert result.ok is False


def test_the_legacy_resolve_mutation_is_left_untouched() -> None:
    assert forge_gh._RESOLVE_THREAD_MUTATION == (  # noqa: SLF001
        "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
        "{thread{isResolved}}}"
    )


def test_feedback_query_reads_the_thread_comment_total_count() -> None:
    query = forge_gh._FEEDBACK_QUERY  # noqa: SLF001
    assert "comments(first:1){totalCount,nodes{body,databaseId,author{login,__typename}}}" in query
