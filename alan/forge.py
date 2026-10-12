from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from alan.engine_config import ForgeTarget

JsonObject = dict[str, object]

RowsOutcome = Literal["ok", "failed", "raised"]
LoginOutcome = Literal["ok", "failed", "raised", "no_credential"]
FileOutcome = Literal["ok", "absent", "failed", "raised"]
PermissionOutcome = Literal["ok", "failed", "raised"]


@dataclass(frozen=True)
class PrRead:
    ok: bool
    fields: JsonObject
    returncode: int
    stderr: str


@dataclass(frozen=True)
class ChecksRead:
    ok: bool
    checks: tuple[JsonObject, ...]
    returncode: int
    stderr: str


@dataclass(frozen=True)
class MergeAttempt:
    ok: bool
    returncode: int
    stderr: str


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    stdout: str
    returncode: int
    stderr: str


@dataclass(frozen=True)
class CompareRead:
    ok: bool
    behind_by: int | None
    files: frozenset[str]
    truncated: bool
    returncode: int
    stderr: str


@dataclass(frozen=True)
class RowsRead:
    outcome: RowsOutcome
    rows: tuple[JsonObject, ...] | None
    returncode: int
    stderr: str


@dataclass(frozen=True)
class LoginRead:
    outcome: LoginOutcome
    login: str | None
    returncode: int
    stderr: str


@dataclass(frozen=True)
class FileRead:
    outcome: FileOutcome
    text: str | None
    returncode: int
    stderr: str


@dataclass(frozen=True)
class PermissionRead:
    outcome: PermissionOutcome
    permission: str | None
    returncode: int
    stderr: str


class ForgePort(Protocol):
    def read_pr(  # pragma: no cover
        self, pr_number: int, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, timeout: float,
    ) -> PrRead: ...

    def read_checks(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ChecksRead: ...

    def merge_pr(  # pragma: no cover
        self, pr_number: int, expected_head_sha: str, *,
        forge_target: ForgeTarget, timeout: float,
    ) -> MergeAttempt: ...

    def compare_base(  # pragma: no cover
        self, head_sha: str, base_ref: str, *,
        forge_target: ForgeTarget, timeout: float,
    ) -> CompareRead: ...

    def head_sha(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def changed_files(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> frozenset[str] | None: ...

    def pr_head_branch(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def request_branch_update(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def pr_open_state(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def open_pr_titles(  # pragma: no cover
        self, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def commit_check_runs_page(  # pragma: no cover
        self, sha: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def check_run_annotations(  # pragma: no cover
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def transport_check_run_annotations(  # pragma: no cover
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def rerequest_check_suite(  # pragma: no cover
        self, check_suite_id: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def rerun_failed_jobs(  # pragma: no cover
        self, run_id: int, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def status_check_rollup(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def failed_job_log(  # pragma: no cover
        self, job_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def pr_patch(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> str | None: ...

    def open_prs_by_author(  # pragma: no cover
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int,
    ) -> tuple[JsonObject, ...]: ...

    def search_open_issues(  # pragma: no cover
        self, query: str, *, forge_target: ForgeTarget, label: str,
    ) -> tuple[JsonObject, ...]: ...

    def create_label(  # pragma: no cover
        self, name: str, *, forge_target: ForgeTarget, color: str, description: str,
    ) -> bool: ...

    def create_issue(  # pragma: no cover
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> bool: ...

    def reviews(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget,
    ) -> str | None: ...

    def dismiss_review(  # pragma: no cover
        self, pr_number: int, review_id: int, *, forge_target: ForgeTarget, message: str,
    ) -> bool: ...

    def feedback(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def unresolved_threads(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult: ...

    def resolve_thread(  # pragma: no cover
        self, thread_id: str, *, forge_target: ForgeTarget, as_verifier: bool,
    ) -> ApiResult: ...

    def resolve_thread_checked(  # pragma: no cover
        self, thread_id: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def unresolve_thread(  # pragma: no cover
        self, thread_id: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def repo_permission(  # pragma: no cover
        self, login: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> PermissionRead: ...

    def post_review(  # pragma: no cover
        self, pr_number: int, payload: JsonObject, *, forge_target: ForgeTarget,
        as_verifier: bool,
    ) -> ApiResult: ...

    def update_pr_body(  # pragma: no cover
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def reply_to_thread(  # pragma: no cover
        self, thread_id: str, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def comment_on_pr(  # pragma: no cover
        self, pr_number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def open_prs(  # pragma: no cover
        self, *, forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead: ...

    def workflow_runs(  # pragma: no cover
        self, workflow: str, *, forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead: ...

    def viewer_login(  # pragma: no cover
        self, *, forge_target: ForgeTarget, as_verifier: bool, timeout: float,
    ) -> LoginRead: ...

    def repo_file(  # pragma: no cover
        self, repo_path: str, *, forge_target: ForgeTarget, timeout: float,
        ref: str | None = None,
    ) -> FileRead: ...

    def prs_by_head(  # pragma: no cover
        self, head_branch: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, limit: int, timeout: float,
    ) -> RowsRead: ...

    def create_pr(  # pragma: no cover
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult: ...

    def create_pr_from_checkout(  # pragma: no cover
        self, title: str, body: str, head_branch: str, *,
        forge_target: ForgeTarget, workdir: Path, timeout: float,
    ) -> ApiResult: ...

    def pr_state(  # pragma: no cover
        self, pr_number: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult: ...

    def default_branch(  # pragma: no cover
        self, *, forge_target: ForgeTarget, timeout: float,
    ) -> str | None: ...

    def commit_check_runs(  # pragma: no cover
        self, ref: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead: ...

    def latest_workflow_run(  # pragma: no cover
        self, workflow: str, *, forge_target: ForgeTarget, timeout: float,
        head_sha: str | None = None,
    ) -> RowsRead: ...

    def workflows(  # pragma: no cover
        self, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead: ...

    def prs_by_author(  # pragma: no cover
        self, author: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead: ...

    def open_issues_by_title(  # pragma: no cover
        self, title: str, *, forge_target: ForgeTarget, label: str,
    ) -> RowsRead: ...

    def create_numbered_issue(  # pragma: no cover
        self, title: str, body: str, *, forge_target: ForgeTarget, label: str,
    ) -> ApiResult: ...

    def comment_on_issue(  # pragma: no cover
        self, number: int, body: str, *, forge_target: ForgeTarget,
    ) -> ApiResult: ...

    def prs_by_head_prefix(  # pragma: no cover
        self, prefix: str, fields: tuple[str, ...], *,
        forge_target: ForgeTarget, state: Literal["open", "all"], limit: int, timeout: float,
    ) -> RowsRead: ...

    def latest_branch_run(  # pragma: no cover
        self, workflow: str, branch: str, *, forge_target: ForgeTarget, timeout: float,
    ) -> RowsRead: ...


def decode_contents_payload(payload: str) -> str | None:
    try:
        parsed: object = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    content = cast(dict[str, object], parsed).get("content")
    encoding = cast(dict[str, object], parsed).get("encoding")
    if not isinstance(content, str) or encoding != "base64":
        return None
    stripped = content.replace("\r", "").replace("\n", "")
    try:
        return base64.b64decode(stripped, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


_active_forge: ForgePort | None = None


def set_active_forge(forge: ForgePort) -> None:
    global _active_forge
    _active_forge = forge


def active_forge() -> ForgePort:
    forge = _active_forge
    if forge is None:
        raise RuntimeError("no active forge installed")
    return forge
