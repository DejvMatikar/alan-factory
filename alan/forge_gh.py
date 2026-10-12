from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Required, TypeAlias, TypedDict, Unpack, cast
from urllib.parse import quote

from alan import default_profile, process_runner, secrets_store
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
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_json import safe_json

logger = logging.getLogger(__name__)

_HTTP_404_MARKER = "HTTP 404"
_NO_TRANSPORT_CREDENTIAL = "no transport credential"

_JsonList: TypeAlias = list[object]


class _RunKwargs(TypedDict, total=False):
    cwd: Required[str | None]
    timeout: Required[float]
    env: Mapping[str, str] | None

_FEEDBACK_QUERY = (
    "query($owner:String!,$name:String!,$n:Int!){"
    "rateLimit{limit,cost,remaining,resetAt},"
    "repository(owner:$owner,"
    "name:$name){pullRequest(number:$n){"
    "reviewThreads(first:100){totalCount,nodes{id,isResolved,"
    "comments(first:1){totalCount,nodes{body,databaseId,author{login,__typename}}}}},"
    "comments(first:100){totalCount,nodes{databaseId,body,author{login,__typename}}},"
    "reviews(first:100){totalCount,nodes{databaseId,body,author{login,__typename},state}}"
    "}}}"
)

_REVIEW_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,"
    "name:$name){pullRequest(number:$n){"
    "reviewThreads(first:100){nodes{id,isResolved,isOutdated,path,line,"
    "comments(first:1){totalCount,nodes{body,author{login}}}}}"
    "}}}"
)

_RESOLVE_THREAD_MUTATION = (
    "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved}}}"
)

_RESOLVE_THREAD_CHECKED_MUTATION = (
    "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved,comments{totalCount}}}}"
)

_UNRESOLVE_THREAD_MUTATION = (
    "mutation($threadId:ID!){unresolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved,comments{totalCount}}}}"
)

_NO_VERIFIER_CREDENTIAL = ApiResult(
    ok=False, stdout="", returncode=-1, stderr="no reviewer credential",
)

_THREAD_REPLY_MUTATION = (
    "mutation($threadId:ID!,$body:String!){"
    "addPullRequestReviewThreadReply("
    "input:{pullRequestReviewThreadId:$threadId,body:$body})"
    "{comment{id}}}"
)


_COMPARE_FILES_PAGE_CAP = 300

_GH_PAGE_SIZE = 100


def _compare_filenames(payload: JsonObject) -> list[str]:
    files = payload.get("files")
    if not isinstance(files, list):
        return []
    names: list[str] = []
    for item in cast(_JsonList, files):
        if isinstance(item, dict):
            filename = cast(JsonObject, item).get("filename")
            if isinstance(filename, str):
                names.append(filename)
    return names


def _behind_by(payload: JsonObject) -> int | None:
    ahead_by = payload.get("ahead_by")
    if isinstance(ahead_by, int) and not isinstance(ahead_by, bool):
        return ahead_by
    return None


def _api_result(result: subprocess.CompletedProcess[str]) -> ApiResult:
    return ApiResult(
        ok=result.returncode == 0, stdout=result.stdout or "",
        returncode=result.returncode, stderr=result.stderr or "",
    )


def run_gh_api_json_input(
    args: Sequence[str], payload: Mapping[str, Any], *, forge_target: ForgeTarget,
    reviewer: bool = False, env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    fd, path = tempfile.mkstemp(prefix="alan_gh_payload_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return _forge_run(
            [*args, "--input", path], forge_target=forge_target, reviewer=reviewer,
            cwd=None, timeout=GH_TIMEOUT_S, env=env,
        )
    finally:
        Path(path).unlink(missing_ok=True)


def _check_run_annotations_argv(forge_target: ForgeTarget, check_run_id: int) -> list[str]:
    return [
        "gh", "api",
        f"repos/{forge_target.repo.slug}/check-runs/{check_run_id}/annotations"
        f"?per_page={_GH_PAGE_SIZE}",
    ]


def _view_scalar(pr_number: int, forge_target: ForgeTarget, field: str) -> ApiResult:
    result = _forge_run(
        [
            "gh", "pr", "view", str(pr_number), "--repo", forge_target.repo.slug,
            "--json", field, "-q", f".{field}",
        ],
        forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
    )
    return _api_result(result)


def _verifier_env(as_verifier: bool, forge_target: ForgeTarget) -> dict[str, str] | None:
    if not as_verifier:
        return None
    return secrets_store.verifier_gh_env(forge_target) or None


def _transport_env(forge_target: ForgeTarget) -> dict[str, str] | None:
    token = secrets_store.transport_credential(forge_target)
    return {"GH_TOKEN": token} if token else None


def _copy_env(forge_target: ForgeTarget, reviewer: bool) -> dict[str, str] | None:
    reviewer_env = secrets_store.verifier_gh_env(forge_target) if reviewer else None
    return reviewer_env or _transport_env(forge_target)


def _forge_run(
    argv: list[str], *, forge_target: ForgeTarget, reviewer: bool = False,
    **kwargs: Unpack[_RunKwargs],
) -> subprocess.CompletedProcess[str]:
    if default_profile.resolved().source is not default_profile.Source.profile:
        return process_runner.run_subprocess(argv, **kwargs)
    env = _copy_env(forge_target, reviewer)
    if env is None:
        logger.warning(
            "%s for %s; gh was not invoked", _NO_TRANSPORT_CREDENTIAL, forge_target.repo.slug,
        )
        return subprocess.CompletedProcess(argv, -1, "", _NO_TRANSPORT_CREDENTIAL)
    return process_runner.run_subprocess(
        argv, cwd=kwargs["cwd"], timeout=kwargs["timeout"], env=env,
    )


def _run_gh(
    argv: list[str], *, forge_target: ForgeTarget, as_verifier: bool,
    timeout: float = GH_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    return _forge_run(
        argv, forge_target=forge_target, reviewer=as_verifier, cwd=None, timeout=timeout,
        env=_verifier_env(as_verifier, forge_target),
    )


def _gh_or_none(
    argv: list[str], forge_target: ForgeTarget, env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        if env is None:
            return _forge_run(argv, forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S)
        return _forge_run(argv, forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _gh_succeeded(
    argv: list[str], forge_target: ForgeTarget, env: Mapping[str, str] | None = None,
) -> bool:
    result = _gh_or_none(argv, forge_target, env)
    return result is not None and result.returncode == 0


def _attempt(
    argv: list[str], *, forge_target: ForgeTarget | None, env: Mapping[str, str] | None,
    timeout: float,
) -> subprocess.CompletedProcess[str] | str:
    try:
        if forge_target is None:
            return process_runner.run_subprocess(argv, cwd=None, timeout=timeout, env=env)
        return _forge_run(argv, forge_target=forge_target, cwd=None, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def _rows_read(
    argv: list[str], forge_target: ForgeTarget, *, env: Mapping[str, str] | None, timeout: float,
) -> RowsRead:
    attempt = _attempt(argv, forge_target=forge_target, env=env, timeout=timeout)
    if isinstance(attempt, str):
        return RowsRead(outcome="raised", rows=None, returncode=-1, stderr=attempt)
    if attempt.returncode != 0:
        return RowsRead(
            outcome="failed", rows=None,
            returncode=attempt.returncode, stderr=attempt.stderr or "",
        )
    parsed = safe_json(attempt.stdout or "")
    rows = _json_objects(cast(_JsonList, parsed)) if isinstance(parsed, list) else None
    return RowsRead(
        outcome="ok", rows=rows, returncode=attempt.returncode, stderr=attempt.stderr or "",
    )


def _envelope_read(
    argv: list[str], key: str, forge_target: ForgeTarget, *,
    env: Mapping[str, str] | None, timeout: float,
) -> RowsRead:
    attempt = _attempt(argv, forge_target=forge_target, env=env, timeout=timeout)
    if isinstance(attempt, str):
        return RowsRead(outcome="raised", rows=None, returncode=-1, stderr=attempt)
    stderr = attempt.stderr or ""
    parsed = safe_json(attempt.stdout or "")
    if attempt.returncode != 0 or not isinstance(parsed, dict):
        return RowsRead(outcome="failed", rows=None, returncode=attempt.returncode, stderr=stderr)
    items = cast(JsonObject, parsed).get(key)
    rows = _json_objects(cast(_JsonList, items)) if isinstance(items, list) else None
    return RowsRead(outcome="ok", rows=rows, returncode=attempt.returncode, stderr=stderr)


def _default_branch_name(payload: JsonObject) -> str:
    branch = payload.get("default_branch")
    return branch if isinstance(branch, str) and branch else "main"


def _login_env(as_verifier: bool, forge_target: ForgeTarget) -> Mapping[str, str] | None:
    if as_verifier:
        return _verifier_env(True, forge_target)
    return _transport_env(forge_target)


def _login_read(attempt: subprocess.CompletedProcess[str] | str) -> LoginRead:
    if isinstance(attempt, str):
        return LoginRead(outcome="raised", login=None, returncode=-1, stderr=attempt)
    login = (attempt.stdout or "").strip()
    if attempt.returncode != 0 or not login:
        return LoginRead(
            outcome="failed", login=None,
            returncode=attempt.returncode, stderr=attempt.stderr or "",
        )
    return LoginRead(
        outcome="ok", login=login, returncode=attempt.returncode, stderr=attempt.stderr or "",
    )


def _permission_read(attempt: subprocess.CompletedProcess[str] | str) -> PermissionRead:
    if isinstance(attempt, str):
        return PermissionRead(outcome="raised", permission=None, returncode=-1, stderr=attempt)
    permission = (attempt.stdout or "").strip()
    if attempt.returncode != 0 or not permission:
        return PermissionRead(
            outcome="failed", permission=None,
            returncode=attempt.returncode, stderr=attempt.stderr or "",
        )
    return PermissionRead(
        outcome="ok", permission=permission,
        returncode=attempt.returncode, stderr=attempt.stderr or "",
    )


def _thread_mutation(mutation: str, thread_id: str, forge_target: ForgeTarget) -> ApiResult:
    env = secrets_store.verifier_gh_env(forge_target)
    if not env:
        return _NO_VERIFIER_CREDENTIAL
    argv = _graphql_argv(mutation, "-f", f"threadId={thread_id}")
    return _api_result(
        process_runner.run_subprocess(argv, cwd=None, timeout=GH_TIMEOUT_S, env=env)
    )


def _file_read(
    argv: list[str], forge_target: ForgeTarget, *, env: Mapping[str, str] | None, timeout: float,
) -> FileRead:
    attempt = _attempt(argv, forge_target=forge_target, env=env, timeout=timeout)
    if isinstance(attempt, str):
        return FileRead(outcome="raised", text=None, returncode=-1, stderr=attempt)
    if attempt.returncode != 0:
        stderr = attempt.stderr or ""
        if _HTTP_404_MARKER in stderr:
            return FileRead(
                outcome="absent", text=None, returncode=attempt.returncode, stderr=stderr,
            )
        return FileRead(outcome="failed", text=None, returncode=attempt.returncode, stderr=stderr)
    return FileRead(
        outcome="ok", text=attempt.stdout or "",
        returncode=attempt.returncode, stderr=attempt.stderr or "",
    )


def _json_objects(raw: object) -> tuple[JsonObject, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        cast(JsonObject, item) for item in cast(_JsonList, raw) if isinstance(item, dict)
    )


def _graphql_argv(query: str, *fields: str) -> list[str]:
    return ["gh", "api", "graphql", "-f", f"query={query}", *fields]


def _pr_graphql_argv(query: str, pr_number: int, forge_target: ForgeTarget) -> list[str]:
    return _graphql_argv(
        query,
        "-F", f"owner={forge_target.repo.owner}",
        "-F", f"name={forge_target.repo.repo}",
        "-F", f"n={pr_number}",
    )


class GhForge:
    def read_pr(
        self, pr_number: int, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, timeout: float = GH_TIMEOUT_S,
    ) -> PrRead:
        result = _forge_run(
            [
                "gh", "pr", "view", str(pr_number), "--repo", forge_target.repo.slug,
                "--json", ",".join(fields),
            ],
            forge_target=forge_target, cwd=None, timeout=timeout,
        )
        parsed = safe_json(result.stdout or "")
        fields_obj = cast(JsonObject, parsed) if isinstance(parsed, dict) else {}
        return PrRead(
            ok=result.returncode == 0, fields=fields_obj,
            returncode=result.returncode, stderr=result.stderr or "",
        )

    def read_checks(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float = GH_TIMEOUT_S,
    ) -> ChecksRead:
        result = _forge_run(
            [
                "gh", "pr", "checks", str(pr_number), "--repo", forge_target.repo.slug,
                "--json", "name,state",
            ],
            forge_target=forge_target, cwd=None, timeout=timeout,
        )
        return ChecksRead(
            ok=result.returncode == 0, checks=_json_objects(safe_json(result.stdout or "")),
            returncode=result.returncode, stderr=result.stderr or "",
        )

    def merge_pr(
        self, pr_number: int, expected_head_sha: str, *,
        forge_target: ForgeTarget, timeout: float = GH_TIMEOUT_S,
    ) -> MergeAttempt:
        result = _forge_run(
            [
                "gh", "pr", "merge", str(pr_number), "--repo", forge_target.repo.slug,
                "--squash", "--delete-branch", "--match-head-commit", expected_head_sha,
            ],
            forge_target=forge_target, cwd=None, timeout=timeout,
        )
        return MergeAttempt(
            ok=result.returncode == 0, returncode=result.returncode, stderr=result.stderr or "",
        )

    def compare_base(
        self, head_sha: str, base_ref: str, *,
        forge_target: ForgeTarget, timeout: float = GH_TIMEOUT_S,
    ) -> CompareRead:
        result = _forge_run(
            [
                "gh", "api",
                f"repos/{forge_target.repo.slug}/compare/{head_sha}...{quote(base_ref, safe='')}",
            ],
            forge_target=forge_target, cwd=None, timeout=timeout,
        )
        parsed = safe_json(result.stdout or "")
        payload = cast(JsonObject, parsed) if isinstance(parsed, dict) else {}
        names = _compare_filenames(payload)
        return CompareRead(
            ok=result.returncode == 0, behind_by=_behind_by(payload),
            files=frozenset(names), truncated=len(names) >= _COMPARE_FILES_PAGE_CAP,
            returncode=result.returncode, stderr=result.stderr or "",
        )

    def head_sha(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return _view_scalar(pr_number, forge_target, "headRefOid")

    def changed_files(
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> frozenset[str] | None:
        result = _gh_or_none(
            ["gh", "pr", "diff", "--name-only", str(pr_number), "--repo", forge_target.repo.slug],
            forge_target,
        )
        if result is None or result.returncode != 0:
            return None
        return frozenset(filter(None, map(str.strip, (result.stdout or "").splitlines())))

    def pr_head_branch(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return _view_scalar(pr_number, forge_target, "headRefName")

    def request_branch_update(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        result = _forge_run(
            ["gh", "pr", "update-branch", str(pr_number), "--repo", forge_target.repo.slug],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
        )
        return _api_result(result)

    def pr_open_state(self, pr_number: int, *, forge_target: ForgeTarget) -> ApiResult:
        return _view_scalar(pr_number, forge_target, "state")

    def open_pr_titles(self, *, forge_target: ForgeTarget) -> ApiResult:
        result = _forge_run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,title", "--limit", "100"],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
        )
        return _api_result(result)

    def commit_check_runs_page(self, sha: str, *, forge_target: ForgeTarget) -> ApiResult:
        result = _forge_run(
            [
                "gh", "api",
                f"repos/{forge_target.repo.slug}/commits/{sha}/check-runs"
                f"?per_page={_GH_PAGE_SIZE}",
            ],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
        )
        return _api_result(result)

    def check_run_annotations(
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            _check_run_annotations_argv(forge_target, check_run_id),
            forge_target=forge_target, cwd=None, timeout=timeout,
        )
        return _api_result(result)

    def transport_check_run_annotations(
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            _check_run_annotations_argv(forge_target, check_run_id),
            forge_target=forge_target, cwd=None, timeout=timeout, env=_transport_env(forge_target),
        )
        return _api_result(result)

    def rerequest_check_suite(
        self, check_suite_id: int, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "api", "-X", "POST",
                f"repos/{forge_target.repo.slug}/check-suites/{check_suite_id}/rerequest",
            ],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
        )
        return _api_result(result)

    def rerun_failed_jobs(self, run_id: int, *, forge_target: ForgeTarget) -> ApiResult:
        result = _forge_run(
            ["gh", "run", "rerun", str(run_id), "--failed", "--repo", forge_target.repo.slug],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
        )
        return _api_result(result)

    def status_check_rollup(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "pr", "view", str(pr_number), "--repo", forge_target.repo.slug,
                "--json", "statusCheckRollup",
            ],
            forge_target=forge_target, cwd=None, timeout=timeout, env=_transport_env(forge_target),
        )
        return _api_result(result)

    def failed_job_log(
        self, job_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "run", "view", "--repo", forge_target.repo.slug,
                "--job", str(job_id), "--log-failed",
            ],
            forge_target=forge_target, cwd=None, timeout=timeout, env=_transport_env(forge_target),
        )
        return _api_result(result)

    def pr_patch(self, pr_number: int, *, forge_target: ForgeTarget) -> str | None:
        result = _gh_or_none(
            ["gh", "pr", "diff", str(pr_number), "--repo", forge_target.repo.slug],
            forge_target,
        )
        if result is None or result.returncode != 0:
            return None
        return result.stdout or ""

    def open_prs_by_author(
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int,
    ) -> tuple[JsonObject, ...]:
        result = _gh_or_none(
            [
                "gh", "pr", "list", "--repo", forge_target.repo.slug,
                "--author", author, "--state", "open",
                "--json", ",".join(fields), "--limit", str(limit),
            ],
            forge_target, _transport_env(forge_target),
        )
        if result is None or result.returncode != 0:
            return ()
        return _json_objects(safe_json(result.stdout or ""))

    def search_open_issues(
        self, query: str, *, forge_target: ForgeTarget, label: str,
    ) -> tuple[JsonObject, ...]:
        result = _gh_or_none(
            [
                "gh", "issue", "list", "--repo", forge_target.repo.slug,
                "--label", label, "--state", "open", "--search", query, "--json", "title",
            ],
            forge_target, _transport_env(forge_target),
        )
        if result is None or result.returncode != 0:
            return ()
        return _json_objects(safe_json(result.stdout or ""))

    def create_label(
        self, name: str, *, forge_target: ForgeTarget, color: str, description: str,
    ) -> bool:
        return _gh_succeeded(
            [
                "gh", "label", "create", name, "--repo", forge_target.repo.slug,
                "--color", color, "--description", description, "--force",
            ],
            forge_target, _transport_env(forge_target),
        )

    def create_issue(
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> bool:
        return _gh_succeeded(
            [
                "gh", "issue", "create", "--repo", forge_target.repo.slug,
                "--title", title, "--body", body, "--label", label,
            ],
            forge_target, _transport_env(forge_target),
        )

    def reviews(self, pr_number: int, *, forge_target: ForgeTarget) -> str | None:
        try:
            result = _run_gh(
                [
                    "gh", "api", "--paginate", "--jq", ".[]",
                    f"/repos/{forge_target.repo.slug}/pulls/{pr_number}/reviews",
                ],
                forge_target=forge_target, as_verifier=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        return (result.stdout or "") if result.returncode == 0 else None

    def dismiss_review(
        self, pr_number: int, review_id: int, *, forge_target: ForgeTarget, message: str,
    ) -> bool:
        try:
            result = _run_gh(
                [
                    "gh", "api", "-X", "PUT",
                    f"/repos/{forge_target.repo.slug}/pulls/{pr_number}"
                    f"/reviews/{review_id}/dismissals",
                    "-f", f"message={message}", "-f", "event=DISMISS",
                ],
                forge_target=forge_target, as_verifier=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def feedback(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float = GH_TIMEOUT_S,
    ) -> ApiResult:
        argv = _pr_graphql_argv(_FEEDBACK_QUERY, pr_number, forge_target)
        return _api_result(_forge_run(argv, forge_target=forge_target, cwd=None, timeout=timeout))

    def unresolved_threads(
        self, pr_number: int, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult:
        argv = _pr_graphql_argv(_REVIEW_THREADS_QUERY, pr_number, forge_target)
        return _api_result(_run_gh(argv, forge_target=forge_target, as_verifier=as_verifier))

    def resolve_thread(
        self, thread_id: str, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult:
        argv = _graphql_argv(_RESOLVE_THREAD_MUTATION, "-f", f"threadId={thread_id}")
        return _api_result(_run_gh(argv, forge_target=forge_target, as_verifier=as_verifier))

    def resolve_thread_checked(
        self, thread_id: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        return _thread_mutation(_RESOLVE_THREAD_CHECKED_MUTATION, thread_id, forge_target)

    def unresolve_thread(self, thread_id: str, *, forge_target: ForgeTarget) -> ApiResult:
        return _thread_mutation(_UNRESOLVE_THREAD_MUTATION, thread_id, forge_target)

    def repo_permission(
        self, login: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> PermissionRead:
        path = f"repos/{forge_target.repo.slug}/collaborators/{quote(login, safe='')}/permission"
        attempt = _attempt(
            ["gh", "api", path, "-q", ".permission"],
            forge_target=forge_target, env=None, timeout=timeout,
        )
        return _permission_read(attempt)

    def post_review(
        self, pr_number: int, payload: JsonObject, *, forge_target: ForgeTarget,
        as_verifier: bool,
    ) -> ApiResult:
        path = f"/repos/{forge_target.repo.slug}/pulls/{pr_number}/reviews"
        return _api_result(run_gh_api_json_input(
            ["gh", "api", "-X", "POST", path], payload, forge_target=forge_target,
            reviewer=as_verifier, env=_verifier_env(as_verifier, forge_target),
        ))

    def update_pr_body(
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        path = f"/repos/{forge_target.repo.slug}/pulls/{pr_number}"
        return _api_result(run_gh_api_json_input(
            ["gh", "api", "-X", "PATCH", path], {"body": body}, forge_target=forge_target,
        ))

    def reply_to_thread(
        self, thread_id: str, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        argv = _graphql_argv(
            _THREAD_REPLY_MUTATION, "-f", f"threadId={thread_id}", "-f", f"body={body}",
        )
        return _api_result(_run_gh(argv, forge_target=forge_target, as_verifier=False))

    def comment_on_pr(
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        argv = [
            "gh", "pr", "comment", str(pr_number), "--repo", forge_target.repo.slug,
            "--body", body,
        ]
        return _api_result(_run_gh(argv, forge_target=forge_target, as_verifier=False))

    def open_prs(self, *, forge_target: ForgeTarget, limit: int, timeout: float) -> RowsRead:
        argv = [
            "gh", "pr", "list", "--repo", forge_target.repo.slug, "--state", "open",
            "--json", "number,title,url", "--limit", str(limit),
        ]
        return _rows_read(argv, forge_target, env=_transport_env(forge_target), timeout=timeout)

    def workflow_runs(
        self, workflow: str, *, forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead:
        argv = [
            "gh", "run", "list", "--repo", forge_target.repo.slug, "--workflow", workflow,
            "--json", "databaseId,createdAt,status,conclusion", "--limit", str(limit),
        ]
        return _rows_read(argv, forge_target, env=_transport_env(forge_target), timeout=timeout)

    def viewer_login(
        self, *, forge_target: ForgeTarget, as_verifier: bool, timeout: float,
    ) -> LoginRead:
        env = _login_env(as_verifier, forge_target)
        if env is None:
            return LoginRead(outcome="no_credential", login=None, returncode=-1, stderr="")
        attempt = _attempt(
            ["gh", "api", "user", "-q", ".login"], forge_target=None, env=env, timeout=timeout,
        )
        return _login_read(attempt)

    def repo_file(
        self, repo_path: str, *, forge_target: ForgeTarget, timeout: float,
        ref: str | None = None,
    ) -> FileRead:
        path = f"repos/{forge_target.repo.slug}/contents/{quote(repo_path, safe='')}"
        if ref is not None:
            path += f"?ref={quote(ref, safe='')}"
        return _file_read(
            ["gh", "api", path], forge_target, env=_transport_env(forge_target), timeout=timeout,
        )

    def prs_by_head(
        self, head_branch: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead:
        argv = [
            "gh", "pr", "list", "--repo", forge_target.repo.slug, "--head", head_branch,
            "--state", "all", "--json", ",".join(fields), "--limit", str(limit),
        ]
        return _rows_read(argv, forge_target, env=_transport_env(forge_target), timeout=timeout)

    def create_pr(
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "pr", "create", "--repo", forge_target.repo.slug,
                "--title", title, "--body", body, "--head", head_branch,
            ],
            forge_target=forge_target, cwd=str(workdir), timeout=timeout,
            env=_transport_env(forge_target),
        )
        return _api_result(result)

    def create_pr_from_checkout(
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "pr", "create", "--title", title, "--body", body,
                "--base", "main", "--head", head_branch,
            ],
            forge_target=forge_target, cwd=str(workdir), timeout=timeout,
        )
        return _api_result(result)

    def pr_state(
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "pr", "view", str(pr_number), "--repo", forge_target.repo.slug,
                "--json", "state", "-q", ".state",
            ],
            forge_target=forge_target, cwd=None, timeout=timeout, env=_transport_env(forge_target),
        )
        return _api_result(result)

    def default_branch(self, *, forge_target: ForgeTarget, timeout: float) -> str | None:
        attempt = _attempt(
            ["gh", "api", f"repos/{forge_target.repo.slug}"],
            forge_target=forge_target, env=_transport_env(forge_target), timeout=timeout,
        )
        if isinstance(attempt, str) or attempt.returncode != 0:
            return None
        parsed = safe_json(attempt.stdout or "")
        return _default_branch_name(cast(JsonObject, parsed)) if isinstance(parsed, dict) else None

    def commit_check_runs(
        self, ref: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead:
        argv = ["gh", "api", f"repos/{forge_target.repo.slug}/commits/{ref}/check-runs"]
        return _envelope_read(
            argv, "check_runs", forge_target, env=_transport_env(forge_target), timeout=timeout,
        )

    def latest_workflow_run(
        self, workflow: str, *, forge_target: ForgeTarget, timeout: float,
        head_sha: str | None = None,
    ) -> RowsRead:
        query = "per_page=1"
        if head_sha is not None:
            query = f"head_sha={quote(head_sha, safe='')}&{query}"
        path = (
            f"repos/{forge_target.repo.slug}/actions/workflows/{quote(workflow, safe='')}"
            f"/runs?{query}"
        )
        return _envelope_read(
            ["gh", "api", path], "workflow_runs", forge_target,
            env=_transport_env(forge_target), timeout=timeout,
        )

    def workflows(self, *, forge_target: ForgeTarget, timeout: float) -> RowsRead:
        argv = ["gh", "api", f"repos/{forge_target.repo.slug}/actions/workflows"]
        return _envelope_read(
            argv, "workflows", forge_target, env=_transport_env(forge_target), timeout=timeout,
        )

    def prs_by_author(
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead:
        argv = [
            "gh", "pr", "list", "--repo", forge_target.repo.slug, "--author", author,
            "--state", state, "--json", ",".join(fields), "--limit", str(limit),
        ]
        return _rows_read(argv, forge_target, env=_transport_env(forge_target), timeout=timeout)

    def open_issues_by_title(
        self, title: str, *, forge_target: ForgeTarget, label: str,
    ) -> RowsRead:
        argv = [
            "gh", "issue", "list", "--repo", forge_target.repo.slug,
            "--label", label, "--state", "open",
            "--search", f"in:title {title}", "--json", "number,title",
        ]
        return _rows_read(
            argv, forge_target, env=_transport_env(forge_target), timeout=GH_TIMEOUT_S,
        )

    def create_numbered_issue(
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> ApiResult:
        result = _forge_run(
            [
                "gh", "issue", "create", "--repo", forge_target.repo.slug,
                "--title", title, "--body", body, "--label", label,
            ],
            forge_target=forge_target, cwd=None, timeout=GH_TIMEOUT_S,
            env=_transport_env(forge_target),
        )
        return _api_result(result)

    def comment_on_issue(
        self, number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult:
        path = f"repos/{forge_target.repo.slug}/issues/{number}/comments"
        return _api_result(run_gh_api_json_input(
            ["gh", "api", "--method", "POST", path], {"body": body},
            forge_target=forge_target, env=_transport_env(forge_target),
        ))

    def prs_by_head_prefix(
        self, prefix: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead:
        argv = [
            "gh", "pr", "list", "--repo", forge_target.repo.slug, "--search", f"head:{prefix}",
            "--state", state, "--json", ",".join(fields), "--limit", str(limit),
        ]
        return _rows_read(argv, forge_target, env=None, timeout=timeout)

    def latest_branch_run(
        self, workflow: str, branch: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead:
        argv = [
            "gh", "run", "list", "--repo", forge_target.repo.slug, "--workflow", workflow,
            "--branch", branch, "--limit", "1", "--json", "databaseId,status,conclusion,headSha",
        ]
        return _rows_read(argv, forge_target, env=None, timeout=timeout)
