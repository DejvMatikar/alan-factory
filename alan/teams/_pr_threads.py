from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any, cast

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.forge import active_forge
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_json import (
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _nav_json_path,  # pyright: ignore[reportPrivateUsage]
    _safe_int,  # pyright: ignore[reportPrivateUsage]
    _safe_list,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

_THREAD_RESOLVE_ERROR_CAP = 500


def _nav_review_threads(stdout: str) -> _JsonObject | None:
    return _nav_json_path(stdout, "data", "repository", "pullRequest", "reviewThreads")


def _thread_author_login(node: _JsonObject) -> str:
    author = node.get("author")
    if not isinstance(author, dict):
        return ""
    login = cast(_JsonObject, author).get("login", "")
    return login if isinstance(login, str) else ""


def resolve_review_thread(thread_id: str) -> bool:
    try:
        result = active_forge().resolve_thread(
            thread_id, forge_target=DEFAULT_FORGE_TARGET, as_verifier=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.ok


@dataclass(frozen=True)
class ThreadResolveOutcome:
    ok: bool
    error: str | None = None


def resolve_thread_as(thread_id: str, *, forge_target: ForgeTarget) -> ThreadResolveOutcome:
    try:
        result = active_forge().resolve_thread(
            thread_id, forge_target=forge_target, as_verifier=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ThreadResolveOutcome(ok=False, error=str(exc)[:_THREAD_RESOLVE_ERROR_CAP])
    if not result.ok:
        return ThreadResolveOutcome(ok=False, error=result.stderr[:_THREAD_RESOLVE_ERROR_CAP])
    thread = _nav_json_path(result.stdout, "data", "resolveReviewThread", "thread")
    if thread is not None and thread.get("isResolved") is True:
        return ThreadResolveOutcome(ok=True)
    return ThreadResolveOutcome(ok=False, error="unexpected payload")


@dataclass(frozen=True)
class ReviewThread:
    thread_id: str
    author_login: str
    body: str
    path: str
    line: int
    is_outdated: bool
    reply_count: int | None = None


def fetch_unresolved_threads(
    pr_number: int, *, forge_target: ForgeTarget,
) -> tuple[ReviewThread, ...] | None:
    try:
        result = active_forge().unresolved_threads(
            pr_number, forge_target=forge_target, as_verifier=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not result.ok:
        return None
    if _nav_review_threads(result.stdout) is None:
        return None
    return _parse_review_threads(result.stdout)


def list_unresolved_threads(
    pr_number: int, *, forge_target: ForgeTarget,
) -> tuple[ReviewThread, ...]:
    try:
        result = active_forge().unresolved_threads(
            pr_number, forge_target=forge_target, as_verifier=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "list_unresolved_threads: gh api graphql raised %s for PR #%s",
            exc, pr_number,
        )
        _audit_threads_failure(pr_number, f"gh subprocess error: {exc}")
        return ()
    if not result.ok:
        logger.warning(
            "list_unresolved_threads: gh api graphql failed for PR #%s "
            "(rc=%s): %s",
            pr_number, result.returncode, result.stderr,
        )
        _audit_threads_failure(
            pr_number,
            f"gh returncode={result.returncode}: {result.stderr[:200]}",
        )
        return ()
    return _parse_review_threads(result.stdout)


def _audit_threads_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "list_unresolved_threads_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def _parse_review_threads(stdout: str) -> tuple[ReviewThread, ...]:
    review_threads = _nav_review_threads(stdout)
    if review_threads is None:
        return ()
    nodes = _safe_list(review_threads.get("nodes"))
    coerced = (_coerce_review_thread(raw) for raw in nodes)
    return tuple(t for t in coerced if t is not None)


def _reply_count(node: _JsonObject) -> int | None:
    raw_comments = node.get("comments")
    if not isinstance(raw_comments, dict):
        return None
    total = cast(_JsonObject, raw_comments).get("totalCount")
    if isinstance(total, int) and total >= 1:
        return total - 1
    return None


def _coerce_review_thread(raw: object) -> ReviewThread | None:
    if not isinstance(raw, dict):
        return None
    node = cast(_JsonObject, raw)
    if node.get("isResolved"):
        return None
    body, author = _extract_root_body_author(node)
    return ReviewThread(
        thread_id=str(node.get("id", "")),
        author_login=author,
        body=body,
        path=str(node.get("path", "")),
        line=_safe_int(node.get("line")),
        is_outdated=bool(node.get("isOutdated")),
        reply_count=_reply_count(node),
    )


def _extract_root_body_author(node: _JsonObject) -> tuple[str, str]:
    raw_comments = node.get("comments")
    if not isinstance(raw_comments, dict):
        return ("", "")
    nodes_raw = cast(_JsonObject, raw_comments).get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        return ("", "")
    first: Any = nodes_raw[0]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(first, dict):
        return ("", "")
    cn = cast(_JsonObject, first)
    body = str(cn.get("body", ""))
    return (body, _thread_author_login(cn))
