from __future__ import annotations

import inspect
import json
import subprocess
import typing
from pathlib import Path

import pytest

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import ApiResult, ForgePort
from alan.forge_gh import (
    _FEEDBACK_QUERY,  # pyright: ignore[reportPrivateUsage]
    _RESOLVE_THREAD_MUTATION,  # pyright: ignore[reportPrivateUsage]
    _REVIEW_THREADS_QUERY,  # pyright: ignore[reportPrivateUsage]
    GhForge,
)
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
_NEW_OPS = (
    "feedback", "unresolved_threads", "resolve_thread", "post_review",
    "update_pr_body", "reply_to_thread", "comment_on_pr",
)
_FORBIDDEN_PARAMETER_NAMES = frozenset({
    "method", "path", "endpoint", "env", "environ", "credentials", "headers", "token",
})


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.payloads: list[object] = []
        self.stdout: str | None = ""
        self.stderr: str | None = ""
        self.returncode = 0
        self.raises: BaseException | None = None

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(cmd), dict(kwargs)))
        if "--input" in cmd:
            self.payloads.append(json.loads(Path(cmd[cmd.index("--input") + 1]).read_text()))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr,
        )


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Transport:
    fake = _Transport()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: {"GH_TOKEN": "verifier-tok"})
    monkeypatch.setattr(secrets_store, "transport_credential", lambda _t: "transport-tok")
    return fake


def _pr_fields(pr_number: int, target: ForgeTarget) -> list[str]:
    return [
        "-F", f"owner={target.repo.owner}", "-F", f"name={target.repo.repo}",
        "-F", f"n={pr_number}",
    ]


def test_feedback_builds_the_feedback_graphql_argv_without_credentials(
    transport: _Transport,
) -> None:
    _FORGE.feedback(42, forge_target=_TARGET, timeout=12.5)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "graphql", "-f", f"query={_FEEDBACK_QUERY}", *_pr_fields(42, _TARGET),
    ]
    assert kwargs == {"cwd": None, "timeout": 12.5}


def test_unresolved_threads_builds_the_threads_graphql_argv(transport: _Transport) -> None:
    _FORGE.unresolved_threads(7, forge_target=_TARGET, as_verifier=False)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "graphql", "-f", f"query={_REVIEW_THREADS_QUERY}", *_pr_fields(7, _TARGET),
    ]
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": None}


def test_unresolved_threads_as_verifier_derives_the_verifier_environment(
    transport: _Transport,
) -> None:
    _FORGE.unresolved_threads(7, forge_target=_TARGET, as_verifier=True)
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "verifier-tok"}


def test_unresolved_threads_argv_is_identical_for_both_identities(
    transport: _Transport,
) -> None:
    _FORGE.unresolved_threads(7, forge_target=_TARGET, as_verifier=False)
    _FORGE.unresolved_threads(7, forge_target=_TARGET, as_verifier=True)
    assert transport.calls[0][0] == transport.calls[1][0]


def test_resolve_thread_builds_the_mutation_argv(transport: _Transport) -> None:
    _FORGE.resolve_thread("PRTI_abc", forge_target=_TARGET, as_verifier=False)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "graphql", "-f", f"query={_RESOLVE_THREAD_MUTATION}",
        "-f", "threadId=PRTI_abc",
    ]
    assert kwargs["env"] is None


def test_resolve_thread_as_verifier_derives_the_verifier_environment(
    transport: _Transport,
) -> None:
    _FORGE.resolve_thread("PRTI_abc", forge_target=_TARGET, as_verifier=True)
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "verifier-tok"}


def test_resolve_thread_argv_is_identical_for_two_forge_targets_without_verifier(
    transport: _Transport,
) -> None:
    _FORGE.resolve_thread("PRTI_abc", forge_target=_TARGET, as_verifier=False)
    _FORGE.resolve_thread("PRTI_abc", forge_target=_OTHER_TARGET, as_verifier=False)
    assert transport.calls[0][0] == transport.calls[1][0]


def test_empty_verifier_environment_is_equivalent_to_no_environment(
    transport: _Transport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets_store, "verifier_gh_env", lambda _t: {})
    _FORGE.resolve_thread("PRTI_abc", forge_target=_TARGET, as_verifier=True)
    _FORGE.resolve_thread("PRTI_abc", forge_target=_TARGET, as_verifier=False)
    assert transport.calls[0][1]["env"] is None
    assert transport.calls[1][1]["env"] is None


def test_post_review_posts_the_payload_through_a_stdin_input_file(
    transport: _Transport,
) -> None:
    payload: dict[str, object] = {"body": "b", "event": "COMMENT", "comments": []}
    _FORGE.post_review(9, payload, forge_target=_TARGET, as_verifier=False)
    argv, kwargs = transport.calls[0]
    assert argv[:5] == ["gh", "api", "-X", "POST", "/repos/acme/widget/pulls/9/reviews"]
    assert argv[5] == "--input"
    assert len(argv) == 7
    assert transport.payloads == [payload]
    assert kwargs["env"] is None


def test_post_review_as_verifier_derives_the_verifier_environment(
    transport: _Transport,
) -> None:
    _FORGE.post_review(9, {}, forge_target=_TARGET, as_verifier=True)
    assert transport.calls[0][1]["env"] == {"GH_TOKEN": "verifier-tok"}


def test_post_review_removes_the_temporary_payload_file(transport: _Transport) -> None:
    _FORGE.post_review(9, {}, forge_target=_TARGET, as_verifier=False)
    input_path = Path(transport.calls[0][0][-1])
    assert not input_path.exists()


def test_update_pr_body_patches_the_pull_request_through_a_stdin_input_file(
    transport: _Transport,
) -> None:
    _FORGE.update_pr_body(9, "new body", forge_target=_TARGET)
    argv, kwargs = transport.calls[0]
    assert argv[:5] == ["gh", "api", "-X", "PATCH", "/repos/acme/widget/pulls/9"]
    assert argv[5] == "--input"
    assert len(argv) == 7
    assert transport.payloads == [{"body": "new body"}]
    assert kwargs["env"] is None


def test_update_pr_body_removes_the_temporary_payload_file(transport: _Transport) -> None:
    _FORGE.update_pr_body(9, "new body", forge_target=_TARGET)
    assert not Path(transport.calls[0][0][-1]).exists()


def test_reply_to_thread_builds_the_reply_mutation_argv_without_credentials(
    transport: _Transport,
) -> None:
    _FORGE.reply_to_thread("PRTI_abc", "ack text", forge_target=_TARGET)
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "graphql", "-f",
        "query=mutation($threadId:ID!,$body:String!){addPullRequestReviewThreadReply("
        "input:{pullRequestReviewThreadId:$threadId,body:$body}){comment{id}}}",
        "-f", "threadId=PRTI_abc", "-f", "body=ack text",
    ]
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": None}


def test_reply_to_thread_argv_is_identical_for_two_forge_targets(
    transport: _Transport,
) -> None:
    _FORGE.reply_to_thread("PRTI_abc", "ack text", forge_target=_TARGET)
    _FORGE.reply_to_thread("PRTI_abc", "ack text", forge_target=_OTHER_TARGET)
    assert transport.calls[0][0] == transport.calls[1][0]


def test_comment_on_pr_builds_the_pr_comment_argv_without_credentials(
    transport: _Transport,
) -> None:
    _FORGE.comment_on_pr(9, "hello", forge_target=_TARGET)
    argv, kwargs = transport.calls[0]
    assert argv == ["gh", "pr", "comment", "9", "--repo", "acme/widget", "--body", "hello"]
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": None}


@pytest.mark.parametrize("op", _NEW_OPS)
def test_new_operations_map_a_successful_process_to_an_ok_result(
    op: str, transport: _Transport,
) -> None:
    transport.stdout = "payload"
    result = _invoke(op)
    assert result == ApiResult(ok=True, stdout="payload", returncode=0, stderr="")


@pytest.mark.parametrize("op", _NEW_OPS)
def test_new_operations_map_a_failed_process_to_a_not_ok_result(
    op: str, transport: _Transport,
) -> None:
    transport.returncode = 3
    transport.stderr = "denied"
    result = _invoke(op)
    assert result == ApiResult(ok=False, stdout="", returncode=3, stderr="denied")


@pytest.mark.parametrize("op", _NEW_OPS)
def test_new_operations_treat_missing_output_streams_as_empty(
    op: str, transport: _Transport,
) -> None:
    transport.stdout = None
    transport.stderr = None
    result = _invoke(op)
    assert (result.stdout, result.stderr) == ("", "")


@pytest.mark.parametrize("op", _NEW_OPS)
@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_new_operations_propagate_transport_failures_to_their_callers(
    op: str, error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    with pytest.raises(type(error)):
        _invoke(op)


def test_reviews_builds_the_paginated_stream_argv_as_the_verifier(
    transport: _Transport,
) -> None:
    transport.stdout = "line1\nline2\n"
    assert _FORGE.reviews(42, forge_target=_TARGET) == "line1\nline2\n"
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "--paginate", "--jq", ".[]", "/repos/acme/widget/pulls/42/reviews",
    ]
    assert kwargs == {"cwd": None, "timeout": GH_TIMEOUT_S, "env": {"GH_TOKEN": "verifier-tok"}}


def test_reviews_returns_empty_text_for_a_successful_empty_stream(
    transport: _Transport,
) -> None:
    transport.stdout = None
    assert _FORGE.reviews(42, forge_target=_TARGET) == ""


def test_reviews_returns_none_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    assert _FORGE.reviews(42, forge_target=_TARGET) is None


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_reviews_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _FORGE.reviews(42, forge_target=_TARGET) is None


def test_dismiss_review_builds_the_dismissal_argv_as_the_verifier(
    transport: _Transport,
) -> None:
    assert _FORGE.dismiss_review(42, 7, forge_target=_TARGET, message="stale") is True
    argv, kwargs = transport.calls[0]
    assert argv == [
        "gh", "api", "-X", "PUT", "/repos/acme/widget/pulls/42/reviews/7/dismissals",
        "-f", "message=stale", "-f", "event=DISMISS",
    ]
    assert kwargs["env"] == {"GH_TOKEN": "verifier-tok"}


def test_dismiss_review_reports_false_on_nonzero_exit(transport: _Transport) -> None:
    transport.returncode = 1
    assert _FORGE.dismiss_review(42, 7, forge_target=_TARGET, message="m") is False


@pytest.mark.parametrize(
    "error", [FileNotFoundError("gh"), subprocess.TimeoutExpired(cmd="gh", timeout=1)],
)
def test_dismiss_review_swallows_transport_failures(
    error: BaseException, transport: _Transport,
) -> None:
    transport.raises = error
    assert _FORGE.dismiss_review(42, 7, forge_target=_TARGET, message="m") is False


def _forge_operations(owner: type) -> list[tuple[str, typing.Callable[..., object]]]:
    return [
        (name, member)
        for name, member in inspect.getmembers(owner, inspect.isfunction)
        if not name.startswith("_")
    ]


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
def test_no_operation_accepts_an_environment_credential_or_transport_shaped_parameter(
    owner: type,
) -> None:
    operations = _forge_operations(owner)
    assert len(operations) == 52
    for name, member in operations:
        parameters = set(inspect.signature(member).parameters) - {"self"}
        assert parameters.isdisjoint(_FORBIDDEN_PARAMETER_NAMES), name


_SCAFFOLD_PR_OPERATIONS = ("prs_by_head", "create_pr", "pr_state")


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
@pytest.mark.parametrize("name", _SCAFFOLD_PR_OPERATIONS)
def test_scaffold_pr_operations_take_timeout_as_a_required_keyword(
    owner: type, name: str,
) -> None:
    member = dict(_forge_operations(owner))[name]
    param = inspect.signature(member).parameters["timeout"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
@pytest.mark.parametrize("name", ["prs_by_author", "prs_by_head_prefix"])
def test_state_filtered_pr_lists_type_state_as_a_closed_open_or_all_literal(
    owner: type, name: str,
) -> None:
    hints = typing.get_type_hints(dict(_forge_operations(owner))[name])
    assert hints["state"] == typing.Literal["open", "all"]


@pytest.mark.parametrize("owner", [ForgePort, GhForge], ids=["port", "adapter"])
def test_no_operation_parameter_is_typed_as_a_string_mapping(owner: type) -> None:
    for name, member in _forge_operations(owner):
        hints = typing.get_type_hints(member)
        hints.pop("return", None)
        assert all("str, str" not in str(hint) for hint in hints.values()), name


def _invoke(op: str) -> ApiResult:
    if op == "feedback":
        return _FORGE.feedback(1, forge_target=_TARGET, timeout=1.0)
    if op == "unresolved_threads":
        return _FORGE.unresolved_threads(1, forge_target=_TARGET, as_verifier=False)
    if op == "resolve_thread":
        return _FORGE.resolve_thread("t", forge_target=_TARGET, as_verifier=False)
    if op == "update_pr_body":
        return _FORGE.update_pr_body(1, "b", forge_target=_TARGET)
    if op == "reply_to_thread":
        return _FORGE.reply_to_thread("t", "b", forge_target=_TARGET)
    if op == "comment_on_pr":
        return _FORGE.comment_on_pr(1, "b", forge_target=_TARGET)
    return _FORGE.post_review(1, {}, forge_target=_TARGET, as_verifier=False)
