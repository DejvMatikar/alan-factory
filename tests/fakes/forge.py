from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast

import alan.forge as _forge_mod
from alan.engine_config import ForgeTarget
from alan.forge import (
    ApiResult,
    ChecksRead,
    CompareRead,
    FileRead,
    JsonObject,
    LoginRead,
    MergeAttempt,
    PermissionRead,
    PrRead,
    RowsRead,
)
from alan.forge_gh import GhForge

_FORGE_METHOD_NAMES = frozenset({
    "read_pr", "read_checks", "merge_pr", "compare_base",
    "head_sha", "changed_files", "reviews", "dismiss_review",
    "feedback", "unresolved_threads", "resolve_thread", "post_review",
    "pr_patch", "search_open_issues", "create_label", "create_issue", "open_prs_by_author",
    "update_pr_body", "reply_to_thread", "comment_on_pr",
    "open_prs", "workflow_runs", "viewer_login", "repo_file",
    "prs_by_head", "create_pr", "create_pr_from_checkout", "pr_state",
    "default_branch", "commit_check_runs", "latest_workflow_run", "workflows", "prs_by_author",
    "open_issues_by_title", "create_numbered_issue", "comment_on_issue",
    "prs_by_head_prefix", "latest_branch_run",
    "pr_head_branch", "request_branch_update", "pr_open_state", "open_pr_titles",
    "commit_check_runs_page", "check_run_annotations", "transport_check_run_annotations",
    "rerequest_check_suite", "rerun_failed_jobs", "status_check_rollup", "failed_job_log",
    "resolve_thread_checked", "unresolve_thread", "repo_permission",
})
_ADAPTER_BACKED_BY_DEFAULT = (
    "feedback", "unresolved_threads", "resolve_thread", "post_review",
    "pr_patch", "search_open_issues", "create_label", "create_issue",
    "head_sha", "pr_head_branch", "request_branch_update", "pr_open_state", "open_pr_titles",
    "commit_check_runs_page", "check_run_annotations", "transport_check_run_annotations",
    "rerequest_check_suite", "rerun_failed_jobs", "status_check_rollup", "failed_job_log",
    "create_pr_from_checkout",
)
_SCOPED_CALLERS = frozenset({"alan.teams.triage_pregate", "alan.teams.unblock_issue"})


def _caller_module() -> str:
    frame = inspect.currentframe()
    try:
        while frame is not None and frame.f_globals.get("__name__") == __name__:
            frame = frame.f_back
        return "" if frame is None else str(frame.f_globals.get("__name__", ""))
    finally:
        del frame


class FakeForge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.scoped_unstubbed: list[str] = []
        self._explicit: set[str] = set()
        adapter = GhForge()
        self._stubs: dict[str, Callable[..., Any]] = {
            name: getattr(adapter, name) for name in _ADAPTER_BACKED_BY_DEFAULT
        }

    def stub(self, name: str, fn: Callable[..., Any]) -> None:
        if name not in _FORGE_METHOD_NAMES:
            raise AssertionError(f"unknown forge method: {name}")
        self._stubs[name] = fn
        self._explicit.add(name)

    def _call(self, name: str, *args: object, **kwargs: object) -> Any:
        self.calls.append((name, args, kwargs))
        if name not in self._explicit and _caller_module() in _SCOPED_CALLERS:
            self.scoped_unstubbed.append(name)
            raise AssertionError(f"unstubbed forge call from a scoped path: {name}")
        fn = self._stubs.get(name)
        if fn is None:
            raise AssertionError(f"unstubbed forge call: {name}")
        return fn(*args, **kwargs)

    def read_pr(
        self, pr_number: int, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, timeout: float,
    ) -> PrRead:
        return cast(
            PrRead,
            self._call("read_pr", pr_number, fields, forge_target=forge_target, timeout=timeout),
        )

    def read_checks(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ChecksRead:
        return cast(
            ChecksRead,
            self._call("read_checks", pr_number, forge_target=forge_target, timeout=timeout),
        )

    def merge_pr(
        self, pr_number: int, expected_head_sha: str, *,
        forge_target: ForgeTarget, timeout: float,
    ) -> MergeAttempt:
        return cast(
            MergeAttempt,
            self._call(
                "merge_pr", pr_number, expected_head_sha,
                forge_target=forge_target, timeout=timeout,
            ),
        )

    def compare_base(
        self, head_sha: str, base_ref: str, *,
        forge_target: ForgeTarget, timeout: float,
    ) -> CompareRead:
        return cast(
            CompareRead,
            self._call(
                "compare_base", head_sha, base_ref,
                forge_target=forge_target, timeout=timeout,
            ),
        )

    def head_sha(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(ApiResult, self._call("head_sha", pr_number, forge_target=forge_target))

    def changed_files(
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> frozenset[str] | None:
        return cast(
            "frozenset[str] | None",
            self._call("changed_files", pr_number, forge_target=forge_target),
        )

    def pr_head_branch(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("pr_head_branch", pr_number, forge_target=forge_target),
        )

    def request_branch_update(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("request_branch_update", pr_number, forge_target=forge_target),
        )

    def pr_open_state(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("pr_open_state", pr_number, forge_target=forge_target),
        )

    def open_pr_titles(self, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(ApiResult, self._call("open_pr_titles", forge_target=forge_target))

    def commit_check_runs_page(self, sha: str, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("commit_check_runs_page", sha, forge_target=forge_target),
        )

    def check_run_annotations(
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "check_run_annotations", check_run_id, forge_target=forge_target, timeout=timeout,
            ),
        )

    def transport_check_run_annotations(
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "transport_check_run_annotations", check_run_id,
                forge_target=forge_target, timeout=timeout,
            ),
        )

    def rerequest_check_suite(
        self, check_suite_id: int, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("rerequest_check_suite", check_suite_id, forge_target=forge_target),
        )

    def rerun_failed_jobs(self, run_id: int, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("rerun_failed_jobs", run_id, forge_target=forge_target),
        )

    def status_check_rollup(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "status_check_rollup", pr_number, forge_target=forge_target, timeout=timeout,
            ),
        )

    def failed_job_log(
        self, job_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("failed_job_log", job_id, forge_target=forge_target, timeout=timeout),
        )

    def pr_patch(self, pr_number: int, *, forge_target: ForgeTarget) -> str | None:
        return cast("str | None", self._call("pr_patch", pr_number, forge_target=forge_target))

    def open_prs_by_author(
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int,
    ) -> tuple[JsonObject, ...]:
        return cast(
            "tuple[JsonObject, ...]",
            self._call(
                "open_prs_by_author", author, fields, forge_target=forge_target, limit=limit,
            ),
        )

    def search_open_issues(
        self, query: str, *, forge_target: ForgeTarget, label: str,
    ) -> tuple[JsonObject, ...]:
        return cast(
            "tuple[JsonObject, ...]",
            self._call("search_open_issues", query, forge_target=forge_target, label=label),
        )

    def create_label(
        self, name: str, *, forge_target: ForgeTarget, color: str, description: str,
    ) -> bool:
        return cast(
            bool,
            self._call(
                "create_label", name,
                forge_target=forge_target, color=color, description=description,
            ),
        )

    def create_issue(
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> bool:
        return cast(
            bool,
            self._call("create_issue", title, body, forge_target=forge_target, label=label),
        )

    def reviews(self, pr_number: int, *, forge_target: ForgeTarget) -> str | None:
        return cast(
            "str | None", self._call("reviews", pr_number, forge_target=forge_target),
        )

    def dismiss_review(
        self, pr_number: int, review_id: int, *, forge_target: ForgeTarget, message: str,
    ) -> bool:
        return cast(
            bool,
            self._call(
                "dismiss_review", pr_number, review_id,
                forge_target=forge_target, message=message,
            ),
        )

    def feedback(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("feedback", pr_number, forge_target=forge_target, timeout=timeout),
        )

    def unresolved_threads(
        self, pr_number: int, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "unresolved_threads", pr_number,
                forge_target=forge_target, as_verifier=as_verifier,
            ),
        )

    def resolve_thread(
        self, thread_id: str, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "resolve_thread", thread_id,
                forge_target=forge_target, as_verifier=as_verifier,
            ),
        )

    def resolve_thread_checked(
        self, thread_id: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("resolve_thread_checked", thread_id, forge_target=forge_target),
        )

    def unresolve_thread(self, thread_id: str, *, forge_target: ForgeTarget) -> ApiResult:
        return cast(
            ApiResult, self._call("unresolve_thread", thread_id, forge_target=forge_target),
        )

    def repo_permission(
        self, login: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> PermissionRead:
        return cast(
            PermissionRead,
            self._call("repo_permission", login, forge_target=forge_target, timeout=timeout),
        )

    def post_review(
        self, pr_number: int, payload: JsonObject, *, forge_target: ForgeTarget,
        as_verifier: bool,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "post_review", pr_number, payload,
                forge_target=forge_target, as_verifier=as_verifier,
            ),
        )

    def update_pr_body(
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("update_pr_body", pr_number, body, forge_target=forge_target),
        )

    def reply_to_thread(
        self, thread_id: str, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("reply_to_thread", thread_id, body, forge_target=forge_target),
        )

    def comment_on_pr(
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("comment_on_pr", pr_number, body, forge_target=forge_target),
        )

    def open_prs(self, *, forge_target: ForgeTarget, limit: int, timeout: float) -> RowsRead:
        return cast(
            RowsRead,
            self._call("open_prs", forge_target=forge_target, limit=limit, timeout=timeout),
        )

    def workflow_runs(
        self, workflow: str, *, forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "workflow_runs", workflow,
                forge_target=forge_target, limit=limit, timeout=timeout,
            ),
        )

    def viewer_login(
        self, *, forge_target: ForgeTarget, as_verifier: bool, timeout: float,
    ) -> LoginRead:
        return cast(
            LoginRead,
            self._call(
                "viewer_login",
                forge_target=forge_target, as_verifier=as_verifier, timeout=timeout,
            ),
        )

    def repo_file(
        self, repo_path: str, *, forge_target: ForgeTarget, timeout: float,
        ref: str | None = None,
    ) -> FileRead:
        return cast(
            FileRead,
            self._call(
                "repo_file", repo_path, forge_target=forge_target, timeout=timeout, ref=ref,
            ),
        )

    def prs_by_head(
        self, head_branch: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "prs_by_head", head_branch, fields,
                forge_target=forge_target, limit=limit, timeout=timeout,
            ),
        )

    def create_pr(
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "create_pr", title, body, head_branch,
                forge_target=forge_target, workdir=workdir, timeout=timeout,
            ),
        )

    def create_pr_from_checkout(
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "create_pr_from_checkout", title, body, head_branch,
                forge_target=forge_target, workdir=workdir, timeout=timeout,
            ),
        )

    def pr_state(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("pr_state", pr_number, forge_target=forge_target, timeout=timeout),
        )

    def default_branch(self, *, forge_target: ForgeTarget, timeout: float) -> str | None:
        return cast(
            "str | None",
            self._call("default_branch", forge_target=forge_target, timeout=timeout),
        )

    def commit_check_runs(
        self, ref: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call("commit_check_runs", ref, forge_target=forge_target, timeout=timeout),
        )

    def latest_workflow_run(
        self, workflow: str, *, forge_target: ForgeTarget, timeout: float,
        head_sha: str | None = None,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "latest_workflow_run", workflow,
                forge_target=forge_target, timeout=timeout, head_sha=head_sha,
            ),
        )

    def workflows(self, *, forge_target: ForgeTarget, timeout: float) -> RowsRead:
        return cast(
            RowsRead, self._call("workflows", forge_target=forge_target, timeout=timeout),
        )

    def prs_by_author(
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "prs_by_author", author, fields,
                forge_target=forge_target, state=state, limit=limit, timeout=timeout,
            ),
        )

    def open_issues_by_title(
        self, title: str, *, forge_target: ForgeTarget, label: str,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "open_issues_by_title", title, forge_target=forge_target, label=label,
            ),
        )

    def create_numbered_issue(
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call(
                "create_numbered_issue", title, body, forge_target=forge_target, label=label,
            ),
        )

    def comment_on_issue(
        self, number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return cast(
            ApiResult,
            self._call("comment_on_issue", number, body, forge_target=forge_target),
        )

    def prs_by_head_prefix(
        self, prefix: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "prs_by_head_prefix", prefix, fields,
                forge_target=forge_target, state=state, limit=limit, timeout=timeout,
            ),
        )

    def latest_branch_run(
        self, workflow: str, branch: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead:
        return cast(
            RowsRead,
            self._call(
                "latest_branch_run", workflow, branch,
                forge_target=forge_target, timeout=timeout,
            ),
        )


@contextmanager
def installed_fake_forge() -> Iterator[FakeForge]:
    saved = _forge_mod._active_forge  # pyright: ignore[reportPrivateUsage]
    fake = FakeForge()
    _forge_mod.set_active_forge(fake)
    try:
        yield fake
    finally:
        _forge_mod._active_forge = saved  # pyright: ignore[reportPrivateUsage]
    if fake.scoped_unstubbed:
        raise AssertionError(
            f"unstubbed forge call from a scoped path: {sorted(set(fake.scoped_unstubbed))}"
        )
