from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import JsonObject, active_forge

_GH_VIEW_FIELDS: tuple[str, ...] = ("title", "mergedAt", "files", "commits", "url")
_GH_TIMEOUT_S = 60.0


class CommitInfo(BaseModel):
    sha: str
    message: str


class WorkspaceDelivery(BaseModel):
    pr_number: int
    repo: str
    pr_url: str | None = None
    pr_title: str | None = None
    merged_at: datetime | None = None
    files_changed: list[str] = Field(default_factory=list)
    commits: list[CommitInfo] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


def _empty(pr_number: int, repo: RepoIdentity) -> WorkspaceDelivery:
    return WorkspaceDelivery(pr_number=pr_number, repo=repo.slug)


def _parse_merged_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized).astimezone(UTC)
    except ValueError:
        return None


def _extract_file_paths(data: dict[str, Any]) -> list[str]:
    files = data.get("files")
    if not isinstance(files, list):
        return []
    files_list: list[Any] = cast(list[Any], files)  # type: ignore[redundant-cast]
    paths: list[str] = []
    for f in files_list:
        if isinstance(f, dict):
            fd = cast(dict[str, Any], f)
            path = fd.get("path")
            if isinstance(path, str):
                paths.append(path)
    return paths


def _extract_commits(data: dict[str, Any]) -> list[CommitInfo]:
    commits = data.get("commits")
    if not isinstance(commits, list):
        return []
    commits_list: list[Any] = cast(list[Any], commits)  # type: ignore[redundant-cast]
    result: list[CommitInfo] = []
    for c in commits_list:
        if not isinstance(c, dict):
            continue
        cd = cast(dict[str, Any], c)
        oid = cd.get("oid")
        headline = cd.get("messageHeadline")
        if isinstance(oid, str) and isinstance(headline, str):
            result.append(CommitInfo(sha=oid, message=headline))
    return result


def _parse_delivery_payload(
    pr_number: int, repo: RepoIdentity, payload: JsonObject
) -> WorkspaceDelivery:
    url = payload.get("url")
    title = payload.get("title")
    return WorkspaceDelivery(
        pr_number=pr_number,
        repo=repo.slug,
        pr_url=url if isinstance(url, str) else None,
        pr_title=title if isinstance(title, str) else None,
        merged_at=_parse_merged_at(payload.get("mergedAt")),
        files_changed=_extract_file_paths(cast(dict[str, Any], payload)),
        commits=_extract_commits(cast(dict[str, Any], payload)),
    )


def fetch_pr_delivery(
    pr_number: int, forge_target: ForgeTarget
) -> WorkspaceDelivery:
    try:
        read = active_forge().read_pr(
            pr_number, _GH_VIEW_FIELDS, forge_target=forge_target, timeout=_GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _empty(pr_number, forge_target.repo)
    if not read.ok:
        return _empty(pr_number, forge_target.repo)
    return _parse_delivery_payload(pr_number, forge_target.repo, read.fields)
