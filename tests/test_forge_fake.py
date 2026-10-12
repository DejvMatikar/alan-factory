from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from tests.fakes.forge import _ADAPTER_BACKED_BY_DEFAULT, _FORGE_METHOD_NAMES, FakeForge

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
)

_INVOCATIONS: dict[str, Callable[[FakeForge], Any]] = {
    "read_pr": lambda f: f.read_pr(1, ("a",), forge_target=_TARGET, timeout=5.0),
    "read_checks": lambda f: f.read_checks(1, forge_target=_TARGET, timeout=5.0),
    "merge_pr": lambda f: f.merge_pr(1, "a" * 40, forge_target=_TARGET, timeout=5.0),
    "compare_base": lambda f: f.compare_base(
        "a" * 40, "main", forge_target=_TARGET, timeout=5.0,
    ),
    "head_sha": lambda f: f.head_sha(1, forge_target=_TARGET),
    "changed_files": lambda f: f.changed_files(1, forge_target=_TARGET),
    "reviews": lambda f: f.reviews(1, forge_target=_TARGET),
    "dismiss_review": lambda f: f.dismiss_review(1, 2, forge_target=_TARGET, message="m"),
    "update_pr_body": lambda f: f.update_pr_body(1, "b", forge_target=_TARGET),
    "reply_to_thread": lambda f: f.reply_to_thread("PRTI_1", "b", forge_target=_TARGET),
    "comment_on_pr": lambda f: f.comment_on_pr(1, "b", forge_target=_TARGET),
    "open_prs": lambda f: f.open_prs(forge_target=_TARGET, limit=5, timeout=5.0),
    "workflow_runs": lambda f: f.workflow_runs(
        "ci-deploy.yml", forge_target=_TARGET, limit=5, timeout=5.0,
    ),
    "viewer_login": lambda f: f.viewer_login(forge_target=_TARGET, as_verifier=False, timeout=5.0),
    "repo_file": lambda f: f.repo_file("x.yml", forge_target=_TARGET, timeout=5.0),
    "open_issues_by_title": lambda f: f.open_issues_by_title(
        "t", forge_target=_TARGET, label="unblocker",
    ),
    "create_numbered_issue": lambda f: f.create_numbered_issue(
        "t", "b", forge_target=_TARGET, label="unblocker",
    ),
    "comment_on_issue": lambda f: f.comment_on_issue(1, "b", forge_target=_TARGET),
    "prs_by_head_prefix": lambda f: f.prs_by_head_prefix(
        "teams/", ("number",), forge_target=_TARGET, state="all", limit=5, timeout=5.0,
    ),
    "latest_branch_run": lambda f: f.latest_branch_run(
        "tests.yml", "main", forge_target=_TARGET, timeout=5.0,
    ),
    "pr_head_branch": lambda f: f.pr_head_branch(1, forge_target=_TARGET),
    "request_branch_update": lambda f: f.request_branch_update(1, forge_target=_TARGET),
    "pr_open_state": lambda f: f.pr_open_state(1, forge_target=_TARGET),
    "open_pr_titles": lambda f: f.open_pr_titles(forge_target=_TARGET),
    "commit_check_runs_page": lambda f: f.commit_check_runs_page("a" * 40, forge_target=_TARGET),
    "check_run_annotations": lambda f: f.check_run_annotations(
        7, forge_target=_TARGET, timeout=5.0,
    ),
    "transport_check_run_annotations": lambda f: f.transport_check_run_annotations(
        7, forge_target=_TARGET, timeout=5.0,
    ),
    "rerequest_check_suite": lambda f: f.rerequest_check_suite(9, forge_target=_TARGET),
    "rerun_failed_jobs": lambda f: f.rerun_failed_jobs(11, forge_target=_TARGET),
    "status_check_rollup": lambda f: f.status_check_rollup(1, forge_target=_TARGET, timeout=5.0),
    "failed_job_log": lambda f: f.failed_job_log(13, forge_target=_TARGET, timeout=5.0),
    "create_pr_from_checkout": lambda f: f.create_pr_from_checkout(
        "t", "b", "branch", forge_target=_TARGET, workdir=Path("w"), timeout=5.0,
    ),
    "resolve_thread_checked": lambda f: f.resolve_thread_checked("PRTI_1", forge_target=_TARGET),
    "unresolve_thread": lambda f: f.unresolve_thread("PRTI_1", forge_target=_TARGET),
    "repo_permission": lambda f: f.repo_permission("octocat", forge_target=_TARGET, timeout=5.0),
}
_NEW_OPERATIONS = (
    "open_issues_by_title", "create_numbered_issue", "comment_on_issue",
    "prs_by_head_prefix", "latest_branch_run",
    "resolve_thread_checked", "unresolve_thread", "repo_permission",
)
_ADAPTER_BACKED_PR_VIEW_OPERATIONS = (
    "head_sha", "pr_head_branch", "request_branch_update", "pr_open_state", "open_pr_titles",
)
_ADAPTER_BACKED_CHECK_RUN_OPERATIONS = (
    "commit_check_runs_page", "check_run_annotations", "transport_check_run_annotations",
    "rerequest_check_suite", "rerun_failed_jobs", "status_check_rollup", "failed_job_log",
)
_STRICT_INVOCATIONS = sorted(set(_INVOCATIONS) - set(_ADAPTER_BACKED_BY_DEFAULT))


@pytest.mark.parametrize("name", _STRICT_INVOCATIONS)
def test_unstubbed_call_raises(name: str) -> None:
    fake = FakeForge()
    with pytest.raises(AssertionError, match=f"unstubbed forge call: {name}"):
        _INVOCATIONS[name](fake)


def test_unstubbed_changed_files_stays_strict() -> None:
    fake = FakeForge()
    with pytest.raises(AssertionError, match="unstubbed forge call: changed_files"):
        fake.changed_files(1, forge_target=_TARGET)
    assert "changed_files" not in _ADAPTER_BACKED_BY_DEFAULT


@pytest.mark.parametrize("name", sorted(_INVOCATIONS))
def test_stubbed_call_receives_exact_args_and_returns_result(name: str) -> None:
    fake = FakeForge()
    received: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}

    def _stub(*args: object, **kwargs: object) -> str:
        received["call"] = (args, kwargs)
        return "sentinel"

    fake.stub(name, _stub)
    result = _INVOCATIONS[name](fake)
    assert result == "sentinel"
    call_args, call_kwargs = received["call"]
    assert fake.calls[-1] == (name, call_args, call_kwargs)


@pytest.mark.parametrize("name", sorted(_INVOCATIONS))
def test_stub_exception_propagates(name: str) -> None:
    fake = FakeForge()

    def _raise(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    fake.stub(name, _raise)
    with pytest.raises(RuntimeError, match="boom"):
        _INVOCATIONS[name](fake)


def test_calls_recorded_in_order() -> None:
    fake = FakeForge()
    fake.stub("head_sha", lambda *_a, **_k: "sha")
    fake.stub("reviews", lambda *_a, **_k: None)
    fake.head_sha(1, forge_target=_TARGET)
    fake.reviews(2, forge_target=_TARGET)
    assert [c[0] for c in fake.calls] == ["head_sha", "reviews"]


@pytest.mark.parametrize("name", _NEW_OPERATIONS)
def test_new_operations_are_never_adapter_backed_by_default(name: str) -> None:
    assert name not in _ADAPTER_BACKED_BY_DEFAULT
    assert name in _FORGE_METHOD_NAMES


@pytest.mark.parametrize("name", _ADAPTER_BACKED_PR_VIEW_OPERATIONS)
def test_pr_view_operations_are_adapter_backed_by_default(name: str) -> None:
    assert name in _ADAPTER_BACKED_BY_DEFAULT
    assert name in _FORGE_METHOD_NAMES
    assert name in _INVOCATIONS


@pytest.mark.parametrize("name", _ADAPTER_BACKED_CHECK_RUN_OPERATIONS)
def test_check_run_operations_are_adapter_backed_by_default(name: str) -> None:
    assert name in _ADAPTER_BACKED_BY_DEFAULT
    assert name in _FORGE_METHOD_NAMES
    assert name in _INVOCATIONS


def test_create_pr_from_checkout_is_adapter_backed_while_create_pr_stays_strict() -> None:
    assert "create_pr_from_checkout" in _ADAPTER_BACKED_BY_DEFAULT
    assert "create_pr_from_checkout" in _FORGE_METHOD_NAMES
    assert "create_pr_from_checkout" in _INVOCATIONS
    assert "create_pr" not in _ADAPTER_BACKED_BY_DEFAULT


def test_stub_unknown_method_name_raises() -> None:
    fake = FakeForge()
    with pytest.raises(AssertionError, match="unknown forge method: nope"):
        fake.stub("nope", lambda *_a, **_k: None)
