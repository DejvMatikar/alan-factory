from __future__ import annotations

import functools
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from alan import default_profile, process_runner
from alan.default_profile import Resolved, Source
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import (
    ApiResult,
    ChecksRead,
    CompareRead,
    FileRead,
    LoginRead,
    MergeAttempt,
    PermissionRead,
    PrRead,
    RowsRead,
)
from alan.forge_gh import (
    _FEEDBACK_QUERY,  # pyright: ignore[reportPrivateUsage]
    _NO_VERIFIER_CREDENTIAL,  # pyright: ignore[reportPrivateUsage]
    _RESOLVE_THREAD_CHECKED_MUTATION,  # pyright: ignore[reportPrivateUsage]
    _RESOLVE_THREAD_MUTATION,  # pyright: ignore[reportPrivateUsage]
    _REVIEW_THREADS_QUERY,  # pyright: ignore[reportPrivateUsage]
    _THREAD_REPLY_MUTATION,  # pyright: ignore[reportPrivateUsage]
    _UNRESOLVE_THREAD_MUTATION,  # pyright: ignore[reportPrivateUsage]
    GhForge,
)
from alan.teams._pr_forge import GH_TIMEOUT_S

Kwargs = dict[str, object]
Identity = Literal["transport", "reviewer", "strict"]

TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_FORGE_TOKEN",  # noqa: S106  # nosec B106
)
TRANSPORT_VALUE = "transport-sentinel-value"
FALLBACK_VALUE = "fallback-sentinel-value"
REVIEWER_VALUE = "reviewer-sentinel-value"
MISSING_TRANSPORT_STDERR = "no transport credential"
READ_TIMEOUT = 9.0
CHECKOUT = Path("checkout")


@dataclass(frozen=True)
class Row:
    method: str
    variant: str
    call: Callable[[GhForge], object]
    argv: tuple[str, ...]
    kwargs: Kwargs
    identity: Identity
    refusal: object

    @property
    def id(self) -> str:
        return f"{self.method}[{self.variant}]" if self.variant else self.method

    def kwargs_with(self, value: str) -> Kwargs:
        return {**self.kwargs, "env": {"GH_TOKEN": value}}


class Recorder:
    def __init__(self, stdout: str = "") -> None:
        self.calls: list[tuple[tuple[str, ...], Kwargs]] = []
        self.stdout = stdout

    def __call__(
        self, cmd: Sequence[str], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        argv = list(cmd)
        if "--input" in argv:
            slot = argv.index("--input") + 1
            argv[slot] = Path(argv[slot]).read_text()
        self.calls.append((tuple(argv), dict(kwargs)))
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


def install_source(monkeypatch: pytest.MonkeyPatch, source: Source) -> None:
    resolved = Resolved(default_profile.UNCONFIGURED_PROFILE, source)
    monkeypatch.setattr(default_profile, "resolved", functools.cache(lambda: resolved))


def prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Source, *, stdout: str = "",
) -> Recorder:
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(tmp_path / "secrets"))
    monkeypatch.delenv(TARGET.credential_env_name, raising=False)
    install_source(monkeypatch, source)
    recorder = Recorder(stdout)
    monkeypatch.setattr(process_runner, "run_subprocess", recorder)
    return recorder


def ambient(timeout: float = GH_TIMEOUT_S, cwd: str | None = None) -> Kwargs:
    return {"cwd": cwd, "timeout": timeout}


def explicit_none(timeout: float = GH_TIMEOUT_S, cwd: str | None = None) -> Kwargs:
    return {"cwd": cwd, "timeout": timeout, "env": None}


def with_env(value: str, timeout: float = GH_TIMEOUT_S, cwd: str | None = None) -> Kwargs:
    return {"cwd": cwd, "timeout": timeout, "env": {"GH_TOKEN": value}}


def _failed_api(stderr: str = MISSING_TRANSPORT_STDERR) -> ApiResult:
    return ApiResult(ok=False, stdout="", returncode=-1, stderr=stderr)


def _failed_rows() -> RowsRead:
    return RowsRead(
        outcome="failed", rows=None, returncode=-1, stderr=MISSING_TRANSPORT_STDERR,
    )


def _no_credential_login() -> LoginRead:
    return LoginRead(outcome="no_credential", login=None, returncode=-1, stderr="")


def _row(
    method: str, call: Callable[[GhForge], object], argv: Sequence[str], kwargs: Kwargs,
    refusal: object, *, variant: str = "", identity: Identity = "transport",
) -> Row:
    return Row(method, variant, call, tuple(argv), kwargs, identity, refusal)


def _pr_view(number: int, *tail: str) -> list[str]:
    return ["gh", "pr", "view", str(number), "--repo", "acme/widget", *tail]


def _pr_list(*tail: str) -> list[str]:
    return ["gh", "pr", "list", "--repo", "acme/widget", *tail]


def _graphql(query: str, *fields: str) -> list[str]:
    return ["gh", "api", "graphql", "-f", f"query={query}", *fields]


def _pr_fields(number: int) -> list[str]:
    return ["-F", "owner=acme", "-F", "name=widget", "-F", f"n={number}"]


def _json_input(payload: str) -> list[str]:
    return ["--input", payload]


_PR_READ_ROWS = (
    _row(
        "read_pr",
        lambda f: f.read_pr(7, ("number", "title"), forge_target=TARGET, timeout=READ_TIMEOUT),
        _pr_view(7, "--json", "number,title"), ambient(READ_TIMEOUT),
        PrRead(ok=False, fields={}, returncode=-1, stderr=MISSING_TRANSPORT_STDERR),
    ),
    _row(
        "read_checks",
        lambda f: f.read_checks(7, forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "pr", "checks", "7", "--repo", "acme/widget", "--json", "name,state"],
        ambient(READ_TIMEOUT),
        ChecksRead(ok=False, checks=(), returncode=-1, stderr=MISSING_TRANSPORT_STDERR),
    ),
    _row(
        "compare_base",
        lambda f: f.compare_base("abc123", "main", forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/compare/abc123...main"], ambient(READ_TIMEOUT),
        CompareRead(
            ok=False, behind_by=None, files=frozenset(), truncated=False,
            returncode=-1, stderr=MISSING_TRANSPORT_STDERR,
        ),
    ),
    _row(
        "head_sha", lambda f: f.head_sha(7, forge_target=TARGET),
        _pr_view(7, "--json", "headRefOid", "-q", ".headRefOid"), ambient(), _failed_api(),
    ),
    _row(
        "changed_files", lambda f: f.changed_files(7, forge_target=TARGET),
        ["gh", "pr", "diff", "--name-only", "7", "--repo", "acme/widget"], ambient(), None,
    ),
    _row(
        "pr_head_branch", lambda f: f.pr_head_branch(7, forge_target=TARGET),
        _pr_view(7, "--json", "headRefName", "-q", ".headRefName"), ambient(), _failed_api(),
    ),
    _row(
        "pr_open_state", lambda f: f.pr_open_state(7, forge_target=TARGET),
        _pr_view(7, "--json", "state", "-q", ".state"), ambient(), _failed_api(),
    ),
    _row(
        "pr_patch", lambda f: f.pr_patch(7, forge_target=TARGET),
        ["gh", "pr", "diff", "7", "--repo", "acme/widget"], ambient(), None,
    ),
    _row(
        "feedback", lambda f: f.feedback(7, forge_target=TARGET, timeout=READ_TIMEOUT),
        _graphql(_FEEDBACK_QUERY, *_pr_fields(7)), ambient(READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "open_pr_titles", lambda f: f.open_pr_titles(forge_target=TARGET),
        ["gh", "pr", "list", "--state", "open", "--json", "number,title", "--limit", "100"],
        ambient(), _failed_api(),
    ),
)

_PR_WRITE_ROWS = (
    _row(
        "merge_pr",
        lambda f: f.merge_pr(7, "abc123", forge_target=TARGET, timeout=READ_TIMEOUT),
        [
            "gh", "pr", "merge", "7", "--repo", "acme/widget", "--squash", "--delete-branch",
            "--match-head-commit", "abc123",
        ],
        ambient(READ_TIMEOUT),
        MergeAttempt(ok=False, returncode=-1, stderr=MISSING_TRANSPORT_STDERR),
    ),
    _row(
        "request_branch_update", lambda f: f.request_branch_update(7, forge_target=TARGET),
        ["gh", "pr", "update-branch", "7", "--repo", "acme/widget"], ambient(), _failed_api(),
    ),
    _row(
        "update_pr_body", lambda f: f.update_pr_body(7, "body text", forge_target=TARGET),
        ["gh", "api", "-X", "PATCH", "/repos/acme/widget/pulls/7"]
        + _json_input('{"body": "body text"}'),
        explicit_none(), _failed_api(),
    ),
    _row(
        "comment_on_pr", lambda f: f.comment_on_pr(7, "body text", forge_target=TARGET),
        ["gh", "pr", "comment", "7", "--repo", "acme/widget", "--body", "body text"],
        explicit_none(), _failed_api(),
    ),
    _row(
        "reply_to_thread", lambda f: f.reply_to_thread("T1", "body text", forge_target=TARGET),
        _graphql(_THREAD_REPLY_MUTATION, "-f", "threadId=T1", "-f", "body=body text"),
        explicit_none(), _failed_api(),
    ),
    _row(
        "create_pr",
        lambda f: f.create_pr(
            "title", "body", "feature", forge_target=TARGET,
            workdir=CHECKOUT, timeout=READ_TIMEOUT,
        ),
        [
            "gh", "pr", "create", "--repo", "acme/widget", "--title", "title",
            "--body", "body", "--head", "feature",
        ],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT, "checkout"), _failed_api(),
    ),
    _row(
        "create_pr_from_checkout",
        lambda f: f.create_pr_from_checkout(
            "title", "body", "feature", forge_target=TARGET,
            workdir=CHECKOUT, timeout=READ_TIMEOUT,
        ),
        [
            "gh", "pr", "create", "--title", "title", "--body", "body",
            "--base", "main", "--head", "feature",
        ],
        ambient(READ_TIMEOUT, "checkout"), _failed_api(),
    ),
)

_THREAD_AND_REVIEW_ROWS = (
    _row(
        "reviews", lambda f: f.reviews(7, forge_target=TARGET),
        ["gh", "api", "--paginate", "--jq", ".[]", "/repos/acme/widget/pulls/7/reviews"],
        with_env(FALLBACK_VALUE), None, identity="reviewer",
    ),
    _row(
        "dismiss_review",
        lambda f: f.dismiss_review(7, 99, forge_target=TARGET, message="msg"),
        [
            "gh", "api", "-X", "PUT", "/repos/acme/widget/pulls/7/reviews/99/dismissals",
            "-f", "message=msg", "-f", "event=DISMISS",
        ],
        with_env(FALLBACK_VALUE), False, identity="reviewer",
    ),
    _row(
        "post_review",
        lambda f: f.post_review(
            7, {"event": "COMMENT", "body": "b"}, forge_target=TARGET, as_verifier=True,
        ),
        ["gh", "api", "-X", "POST", "/repos/acme/widget/pulls/7/reviews"]
        + _json_input('{"event": "COMMENT", "body": "b"}'),
        with_env(FALLBACK_VALUE), _failed_api(), variant="reviewer", identity="reviewer",
    ),
    _row(
        "post_review",
        lambda f: f.post_review(
            7, {"event": "COMMENT", "body": "b"}, forge_target=TARGET, as_verifier=False,
        ),
        ["gh", "api", "-X", "POST", "/repos/acme/widget/pulls/7/reviews"]
        + _json_input('{"event": "COMMENT", "body": "b"}'),
        explicit_none(), _failed_api(), variant="author",
    ),
    _row(
        "unresolved_threads",
        lambda f: f.unresolved_threads(7, forge_target=TARGET, as_verifier=True),
        _graphql(_REVIEW_THREADS_QUERY, *_pr_fields(7)),
        with_env(FALLBACK_VALUE), _failed_api(), variant="reviewer", identity="reviewer",
    ),
    _row(
        "unresolved_threads",
        lambda f: f.unresolved_threads(7, forge_target=TARGET, as_verifier=False),
        _graphql(_REVIEW_THREADS_QUERY, *_pr_fields(7)),
        explicit_none(), _failed_api(), variant="author",
    ),
    _row(
        "resolve_thread",
        lambda f: f.resolve_thread("T1", forge_target=TARGET, as_verifier=True),
        _graphql(_RESOLVE_THREAD_MUTATION, "-f", "threadId=T1"),
        with_env(FALLBACK_VALUE), _failed_api(), variant="reviewer", identity="reviewer",
    ),
    _row(
        "resolve_thread",
        lambda f: f.resolve_thread("T1", forge_target=TARGET, as_verifier=False),
        _graphql(_RESOLVE_THREAD_MUTATION, "-f", "threadId=T1"),
        explicit_none(), _failed_api(), variant="author",
    ),
    _row(
        "resolve_thread_checked",
        lambda f: f.resolve_thread_checked("T1", forge_target=TARGET),
        _graphql(_RESOLVE_THREAD_CHECKED_MUTATION, "-f", "threadId=T1"),
        with_env(FALLBACK_VALUE), _NO_VERIFIER_CREDENTIAL, identity="strict",
    ),
    _row(
        "unresolve_thread", lambda f: f.unresolve_thread("T1", forge_target=TARGET),
        _graphql(_UNRESOLVE_THREAD_MUTATION, "-f", "threadId=T1"),
        with_env(FALLBACK_VALUE), _NO_VERIFIER_CREDENTIAL, identity="strict",
    ),
    _row(
        "viewer_login",
        lambda f: f.viewer_login(forge_target=TARGET, as_verifier=False, timeout=READ_TIMEOUT),
        ["gh", "api", "user", "-q", ".login"], with_env(TRANSPORT_VALUE, READ_TIMEOUT),
        _no_credential_login(), variant="author", identity="strict",
    ),
    _row(
        "viewer_login",
        lambda f: f.viewer_login(forge_target=TARGET, as_verifier=True, timeout=READ_TIMEOUT),
        ["gh", "api", "user", "-q", ".login"], with_env(FALLBACK_VALUE, READ_TIMEOUT),
        _no_credential_login(), variant="reviewer", identity="strict",
    ),
    _row(
        "repo_permission",
        lambda f: f.repo_permission("octo", forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/collaborators/octo/permission", "-q", ".permission"],
        explicit_none(READ_TIMEOUT),
        PermissionRead(
            outcome="failed", permission=None, returncode=-1, stderr=MISSING_TRANSPORT_STDERR,
        ),
    ),
)

_CI_ROWS = (
    _row(
        "commit_check_runs_page", lambda f: f.commit_check_runs_page("abc123", forge_target=TARGET),
        ["gh", "api", "repos/acme/widget/commits/abc123/check-runs?per_page=100"],
        ambient(), _failed_api(),
    ),
    _row(
        "check_run_annotations",
        lambda f: f.check_run_annotations(7, forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/check-runs/7/annotations?per_page=100"],
        ambient(READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "transport_check_run_annotations",
        lambda f: f.transport_check_run_annotations(7, forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/check-runs/7/annotations?per_page=100"],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "rerequest_check_suite", lambda f: f.rerequest_check_suite(9, forge_target=TARGET),
        ["gh", "api", "-X", "POST", "repos/acme/widget/check-suites/9/rerequest"],
        ambient(), _failed_api(),
    ),
    _row(
        "rerun_failed_jobs", lambda f: f.rerun_failed_jobs(11, forge_target=TARGET),
        ["gh", "run", "rerun", "11", "--failed", "--repo", "acme/widget"],
        ambient(), _failed_api(),
    ),
    _row(
        "status_check_rollup",
        lambda f: f.status_check_rollup(42, forge_target=TARGET, timeout=READ_TIMEOUT),
        _pr_view(42, "--json", "statusCheckRollup"),
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "failed_job_log",
        lambda f: f.failed_job_log(13, forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "run", "view", "--repo", "acme/widget", "--job", "13", "--log-failed"],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "pr_state", lambda f: f.pr_state(7, forge_target=TARGET, timeout=READ_TIMEOUT),
        _pr_view(7, "--json", "state", "-q", ".state"),
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_api(),
    ),
    _row(
        "workflow_runs",
        lambda f: f.workflow_runs(
            "tests.yml", forge_target=TARGET, limit=5, timeout=READ_TIMEOUT,
        ),
        [
            "gh", "run", "list", "--repo", "acme/widget", "--workflow", "tests.yml",
            "--json", "databaseId,createdAt,status,conclusion", "--limit", "5",
        ],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "commit_check_runs",
        lambda f: f.commit_check_runs("abc123", forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/commits/abc123/check-runs"],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "latest_workflow_run",
        lambda f: f.latest_workflow_run(
            "tests.yml", forge_target=TARGET, timeout=READ_TIMEOUT, head_sha="abc123",
        ),
        [
            "gh", "api",
            "repos/acme/widget/actions/workflows/tests.yml/runs?head_sha=abc123&per_page=1",
        ],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "workflows", lambda f: f.workflows(forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget/actions/workflows"],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "latest_branch_run",
        lambda f: f.latest_branch_run(
            "tests.yml", "main", forge_target=TARGET, timeout=READ_TIMEOUT,
        ),
        [
            "gh", "run", "list", "--repo", "acme/widget", "--workflow", "tests.yml",
            "--branch", "main", "--limit", "1",
            "--json", "databaseId,status,conclusion,headSha",
        ],
        explicit_none(READ_TIMEOUT), _failed_rows(),
    ),
)

_ISSUE_AND_LISTING_ROWS = (
    _row(
        "open_prs_by_author",
        lambda f: f.open_prs_by_author(
            "octo", ("number", "title"), forge_target=TARGET, limit=5,
        ),
        _pr_list("--author", "octo", "--state", "open", "--json", "number,title", "--limit", "5"),
        with_env(TRANSPORT_VALUE), (),
    ),
    _row(
        "search_open_issues",
        lambda f: f.search_open_issues("needle", forge_target=TARGET, label="bug"),
        [
            "gh", "issue", "list", "--repo", "acme/widget", "--label", "bug",
            "--state", "open", "--search", "needle", "--json", "title",
        ],
        with_env(TRANSPORT_VALUE), (),
    ),
    _row(
        "create_label",
        lambda f: f.create_label("bug", forge_target=TARGET, color="ff0000", description="d"),
        [
            "gh", "label", "create", "bug", "--repo", "acme/widget",
            "--color", "ff0000", "--description", "d", "--force",
        ],
        with_env(TRANSPORT_VALUE), False,
    ),
    _row(
        "create_issue",
        lambda f: f.create_issue("title", "body", forge_target=TARGET, label="bug"),
        [
            "gh", "issue", "create", "--repo", "acme/widget",
            "--title", "title", "--body", "body", "--label", "bug",
        ],
        with_env(TRANSPORT_VALUE), False,
    ),
    _row(
        "open_prs",
        lambda f: f.open_prs(forge_target=TARGET, limit=5, timeout=READ_TIMEOUT),
        _pr_list("--state", "open", "--json", "number,title,url", "--limit", "5"),
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "prs_by_head",
        lambda f: f.prs_by_head(
            "feature", ("number",), forge_target=TARGET, limit=5, timeout=READ_TIMEOUT,
        ),
        _pr_list("--head", "feature", "--state", "all", "--json", "number", "--limit", "5"),
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "prs_by_author",
        lambda f: f.prs_by_author(
            "octo", ("number",), forge_target=TARGET, state="open", limit=5,
            timeout=READ_TIMEOUT,
        ),
        _pr_list("--author", "octo", "--state", "open", "--json", "number", "--limit", "5"),
        with_env(TRANSPORT_VALUE, READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "prs_by_head_prefix",
        lambda f: f.prs_by_head_prefix(
            "alan/", ("number",), forge_target=TARGET, state="all", limit=5,
            timeout=READ_TIMEOUT,
        ),
        _pr_list("--search", "head:alan/", "--state", "all", "--json", "number", "--limit", "5"),
        explicit_none(READ_TIMEOUT), _failed_rows(),
    ),
    _row(
        "open_issues_by_title",
        lambda f: f.open_issues_by_title("needle", forge_target=TARGET, label="bug"),
        [
            "gh", "issue", "list", "--repo", "acme/widget", "--label", "bug",
            "--state", "open", "--search", "in:title needle", "--json", "number,title",
        ],
        with_env(TRANSPORT_VALUE), _failed_rows(),
    ),
    _row(
        "create_numbered_issue",
        lambda f: f.create_numbered_issue("title", "body", forge_target=TARGET, label="bug"),
        [
            "gh", "issue", "create", "--repo", "acme/widget",
            "--title", "title", "--body", "body", "--label", "bug",
        ],
        with_env(TRANSPORT_VALUE), _failed_api(),
    ),
    _row(
        "comment_on_issue", lambda f: f.comment_on_issue(5, "body text", forge_target=TARGET),
        ["gh", "api", "--method", "POST", "repos/acme/widget/issues/5/comments"]
        + _json_input('{"body": "body text"}'),
        with_env(TRANSPORT_VALUE), _failed_api(),
    ),
    _row(
        "repo_file",
        lambda f: f.repo_file(
            "docs/guide.md", forge_target=TARGET, timeout=READ_TIMEOUT, ref="main",
        ),
        ["gh", "api", "repos/acme/widget/contents/docs%2Fguide.md?ref=main"],
        with_env(TRANSPORT_VALUE, READ_TIMEOUT),
        FileRead(outcome="failed", text=None, returncode=-1, stderr=MISSING_TRANSPORT_STDERR),
    ),
    _row(
        "default_branch",
        lambda f: f.default_branch(forge_target=TARGET, timeout=READ_TIMEOUT),
        ["gh", "api", "repos/acme/widget"], with_env(TRANSPORT_VALUE, READ_TIMEOUT), None,
    ),
)

ROWS: tuple[Row, ...] = (
    *_PR_READ_ROWS, *_PR_WRITE_ROWS, *_THREAD_AND_REVIEW_ROWS, *_CI_ROWS,
    *_ISSUE_AND_LISTING_ROWS,
)


def rows_named(*methods: str) -> tuple[Row, ...]:
    return tuple(row for row in ROWS if row.method in methods)


def rows_with(identity: Identity) -> tuple[Row, ...]:
    return tuple(row for row in ROWS if row.identity == identity)
