from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel

from alan.branch_protection_compat import (
    _ADMIN_SCOPE_MESSAGE,  # pyright: ignore[reportPrivateUsage]
    Finding,
    _GhApiCallError,  # pyright: ignore[reportPrivateUsage]
    _invoke_gh_api,  # pyright: ignore[reportPrivateUsage]
)

GH_API_TIMEOUT_S = 30.0
_PER_PAGE = 100
_MAX_PAGES = 10
_REPO_LIST_PATH_TEMPLATE = (
    "user/repos?per_page={per_page}&page={page}&affiliation=owner,collaborator,organization_member"
)
_EXPIRY_HEADER = "github-authentication-token-expiration"
_EXPIRY_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (UTC|[+-]\d{2}:?\d{2})")
_WRITE_PERMISSIONS = ("push", "maintain", "admin")
_INVITATIONS_PATH = "user/repository_invitations"


class RepoCapability(BaseModel, frozen=True):
    slug: str
    read: bool
    write: bool
    admin: bool
    finding: Finding | None = None


class RequiredPermission(BaseModel, frozen=True):
    token_type: str
    scope: str
    description: str


REQUIRED_PERMISSIONS: tuple[RequiredPermission, ...] = (
    RequiredPermission(
        token_type="classic",  # noqa: S106  # nosec B106
        scope="repo",
        description=(
            "Full control of private repositories — grants the read, write, and "
            "admin access needed to list, push to, and read branch protection on "
            "the selected repository."
        ),
    ),
    RequiredPermission(
        token_type="fine-grained",  # noqa: S106  # nosec B106
        scope="contents:write",
        description="Read and write repository contents — required to push commits.",
    ),
    RequiredPermission(
        token_type="fine-grained",  # noqa: S106  # nosec B106
        scope="pull_requests:write",
        description=(
            "Read and write pull requests — required to open and merge PRs. This "
            "scope is verified at first dispatch, not by this probe: a fine-grained "
            "token can hold contents:write without pull_requests:write."
        ),
    ),
    RequiredPermission(
        token_type="fine-grained",  # noqa: S106  # nosec B106
        scope="administration:read",
        description=(
            "Read repository administration settings — required to read branch "
            "protection rules."
        ),
    ),
)


class GithubReposResult(BaseModel, frozen=True):
    repos: tuple[RepoCapability, ...] = ()
    findings: tuple[Finding, ...] = ()
    required_permissions: tuple[RequiredPermission, ...] = REQUIRED_PERMISSIONS


def _invalid_token_finding(message: str) -> Finding:
    return Finding(
        check="token_valid",
        status="fail",
        message=message or "gh api user failed",
        remedy="paste a valid, non-expired GitHub token",
    )


def _check_token_valid(token: str) -> Finding | None:
    try:
        result = _invoke_gh_api(["user"], token=token, timeout=GH_API_TIMEOUT_S)
    except _GhApiCallError as exc:
        return _invalid_token_finding(str(exc))
    if result.returncode != 0:
        return _invalid_token_finding((result.stderr or "")[:300])
    return None


def _parse_repo_list(stdout: str) -> list[dict[str, Any]]:
    try:
        parsed: object = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _GhApiCallError(f"gh api user/repos returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise _GhApiCallError("gh api user/repos did not return a JSON array")
    return [item for item in cast("list[object]", parsed) if isinstance(item, dict)]


def _list_repos(token: str) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        path = _REPO_LIST_PATH_TEMPLATE.format(per_page=_PER_PAGE, page=page)
        result = _invoke_gh_api([path], token=token, timeout=GH_API_TIMEOUT_S)
        if result.returncode != 0:
            raise _GhApiCallError(
                (result.stderr or "")[:300] or f"gh api user/repos page {page} failed"
            )
        page_rows = _parse_repo_list(result.stdout or "")
        rows.extend(page_rows)
        if len(page_rows) < _PER_PAGE:
            return rows, False
    return rows, True


def _list_error_finding(message: str) -> Finding:
    if "403" in message or "429" in message:
        return Finding(
            check="rate_limited",
            status="fail",
            message=message,
            remedy="wait for the GitHub API rate limit to reset and retry",
        )
    return Finding(
        check="repo_list_failed",
        status="fail",
        message=message,
        remedy="retry once GitHub API access is restored",
    )


def _permissions(item: dict[str, Any]) -> dict[str, Any]:
    perms_raw = item.get("permissions")
    if isinstance(perms_raw, dict):
        return cast("dict[str, Any]", perms_raw)
    return {}


def _to_capability(item: dict[str, Any]) -> RepoCapability | None:
    slug = item.get("full_name")
    if not isinstance(slug, str) or not slug:
        return None
    perms = _permissions(item)
    admin = bool(perms.get("admin"))
    finding = (
        None
        if admin
        else Finding(
            check="repo_admin",
            status="fail",
            message=_ADMIN_SCOPE_MESSAGE,
            remedy=(
                "grant the token admin access to this repository, or select a "
                "repository where it already has admin rights"
            ),
        )
    )
    return RepoCapability(
        slug=slug,
        read=bool(perms.get("pull")),
        write=bool(perms.get("push")),
        admin=admin,
        finding=finding,
    )


_TRUNCATION_FINDING = Finding(
    check="repo_list_truncated",
    status="unknown",
    message=f"repository list truncated at {_MAX_PAGES * _PER_PAGE} repositories",
    remedy=(
        f"narrow the token's repository access to fewer than {_MAX_PAGES * _PER_PAGE} "
        "repositories"
    ),
)


def _header_block(stdout: str) -> list[str]:
    header_lines: list[str] = []
    for line in stdout.splitlines():
        if line.strip() == "":
            break
        header_lines.append(line)
    return header_lines


def _header_value(stdout: str, name: str) -> str | None:
    prefix = f"{name}:"
    for line in _header_block(stdout):
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.partition(":")[2].strip()
    return None


def _oauth_scopes(stdout: str) -> tuple[str, ...] | None:
    value = _header_value(stdout, "x-oauth-scopes")
    if value is None:
        return None
    return tuple(s.strip() for s in value.split(",") if s.strip())


def probe_workflow_scope(
    token: str, *, timeout: float = GH_API_TIMEOUT_S
) -> Literal["present", "absent", "unverifiable"]:
    try:
        result = _invoke_gh_api(["-i", "user"], token=token, timeout=timeout)
    except _GhApiCallError:
        return "unverifiable"
    if result.returncode != 0:
        return "unverifiable"
    scopes = _oauth_scopes(result.stdout or "")
    if scopes is None:
        return "unverifiable"
    if "workflow" in scopes:
        return "present"
    return "absent"


class TokenIdentity(BaseModel, frozen=True):
    login: str
    fine_grained: bool
    expires_on: date | None = None


class TokenIdentityError(RuntimeError):
    pass


class RepoAccessError(RuntimeError):
    pass


def _body_of(stdout: str) -> str | None:
    lines = stdout.splitlines()
    blank = next((index for index, line in enumerate(lines) if not line.strip()), None)
    return None if blank is None else "\n".join(lines[blank + 1 :])


def _login_from_response(stdout: str) -> str | None:
    body = _body_of(stdout)
    if body is None:
        return None
    try:
        parsed: object = json.loads(body)
    except json.JSONDecodeError:
        return None
    login = cast("dict[str, object]", parsed).get("login") if isinstance(parsed, dict) else None
    return login if isinstance(login, str) and login else None


def _expiry_date(value: str) -> date | None:
    matched = _EXPIRY_PATTERN.fullmatch(value.strip())
    if matched is None:
        return None
    stamp, zone = matched.groups()
    offset = "+0000" if zone == "UTC" else zone
    try:
        parsed = datetime.strptime(f"{stamp} {offset}", "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(UTC).date()


def _token_expiry(stdout: str) -> date | None:
    reported = _header_value(stdout, _EXPIRY_HEADER)
    if reported is None:
        return None
    expires_on = _expiry_date(reported)
    if expires_on is None:
        raise TokenIdentityError("GitHub reported an expiry for the token that setup cannot read")
    return expires_on


def probe_token_identity(token: str, *, timeout: float = GH_API_TIMEOUT_S) -> TokenIdentity:
    try:
        result = _invoke_gh_api(["-i", "user"], token=token, timeout=timeout)
    except _GhApiCallError:
        raise TokenIdentityError("GitHub could not be reached to check the token") from None
    if result.returncode != 0:
        raise TokenIdentityError(
            "GitHub rejected the token; it may be mistyped, expired or revoked"
        )
    stdout = result.stdout or ""
    login = _login_from_response(stdout)
    if login is None:
        raise TokenIdentityError("GitHub's answer to the identity request named no account")
    return TokenIdentity(
        login=login, fine_grained=_oauth_scopes(stdout) is None, expires_on=_token_expiry(stdout)
    )


RepoAccess = Literal["write", "invited", "none"]


def _dict_of(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _answer(token: str, path: str, timeout: float) -> object | None:
    result = _invoke_gh_api([path], token=token, timeout=timeout)
    if result.returncode != 0:
        return None
    try:
        return cast("object", json.loads(result.stdout or ""))
    except json.JSONDecodeError:
        return None


def _can_write(repo: object) -> bool:
    permissions = _dict_of(_dict_of(repo).get("permissions"))
    return any(permissions.get(name) is True for name in _WRITE_PERMISSIONS)


def _invited_to(invitations: object, slug: str) -> bool:
    items = cast("list[object]", invitations) if isinstance(invitations, list) else []
    wanted = slug.casefold()
    names = (_dict_of(_dict_of(item).get("repository")).get("full_name") for item in items)
    return any(isinstance(name, str) and name.casefold() == wanted for name in names)


def probe_repo_access(token: str, slug: str, *, timeout: float = GH_API_TIMEOUT_S) -> RepoAccess:
    try:
        if _can_write(_answer(token, f"repos/{slug}", timeout)):
            return "write"
        invitations = _answer(token, _INVITATIONS_PATH, timeout)
    except _GhApiCallError:
        raise RepoAccessError(
            "GitHub could not be reached to check the token's access to the repository"
        ) from None
    return "invited" if _invited_to(invitations, slug) else "none"


def probe_repos(token: str) -> GithubReposResult:
    token_finding = _check_token_valid(token)
    if token_finding is not None:
        return GithubReposResult(findings=(token_finding,))
    try:
        rows, truncated = _list_repos(token)
    except _GhApiCallError as exc:
        return GithubReposResult(findings=(_list_error_finding(str(exc)),))
    repos = tuple(cap for cap in (_to_capability(row) for row in rows) if cap is not None)
    return GithubReposResult(repos=repos, findings=(_TRUNCATION_FINDING,) if truncated else ())
