from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast

import alan.teams._pr_state_paths as _pr_state_paths
from alan.engine_config import ForgeTarget
from alan.forge import ApiResult, PermissionRead, active_forge
from alan.teams import (
    _forge_errors,
    _forge_quota,
    _pr_branch,
    _pr_feedback_failure,
    _pr_post,
)
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_json import (
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _nav_json_path,  # pyright: ignore[reportPrivateUsage]
    _safe_int,  # pyright: ignore[reportPrivateUsage]
    _safe_list,  # pyright: ignore[reportPrivateUsage]
    safe_json,
)

CommentSource = Literal["issue_comment", "review_body"]
ActorClass = Literal["human", "copilot", "other_bot"]


@dataclass(frozen=True)
class GraphQLActorLogin:
    login: str


_COPILOT_GRAPHQL_LOGIN = GraphQLActorLogin("copilot-pull-request-reviewer")

_SHADOW_CATEGORIES: frozenset[CommentSource] = frozenset({"issue_comment", "review_body"})


@dataclass(frozen=True)
class CopilotThread:
    thread_id: str
    body: str
    database_id: int


@dataclass(frozen=True)
class CopilotComment:
    database_id: int
    body: str
    source: CommentSource


@dataclass(frozen=True)
class HumanThread:
    thread_id: str
    login: str
    comment_count: int | None


@dataclass(frozen=True)
class ThreadInfo:
    copilot_threads: tuple[CopilotThread, ...]
    human_unresolved: int
    other_bot_unresolved: int
    total: int
    human_threads: tuple[HumanThread, ...] = ()


@dataclass(frozen=True)
class CommentInfo:
    copilot_items: tuple[CopilotComment, ...]
    human_items: int
    other_bot_items: int
    total: int


@dataclass(frozen=True)
class FeedbackSnapshot:
    ok: bool
    thread_info: ThreadInfo
    shadow_comment_info: CommentInfo
    blocking_comment_info: CommentInfo
    failure_reason: str


_EMPTY_THREAD_INFO = ThreadInfo(
    copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0
)
_EMPTY_COMMENT_INFO = CommentInfo(
    copilot_items=(), human_items=0, other_bot_items=0, total=0
)


def _nav_review_threads(stdout: str) -> _JsonObject | None:
    return _nav_json_path(stdout, "data", "repository", "pullRequest", "reviewThreads")


def _nav_pull_request(stdout: str) -> _JsonObject | None:
    return _nav_json_path(stdout, "data", "repository", "pullRequest")


@dataclass(frozen=True)
class _Actor:
    login: str
    typename: str


def _parse_actor(node: _JsonObject) -> _Actor | None:
    author = node.get("author")
    if not isinstance(author, dict):
        return None
    a = cast(_JsonObject, author)
    login = a.get("login", "")
    typename = a.get("__typename", "")
    if not isinstance(login, str) or not isinstance(typename, str):
        return None
    return _Actor(login=login, typename=typename)


def _classify_actor(actor: _Actor | None, copilot_login: GraphQLActorLogin) -> ActorClass:
    if actor is None or actor.typename != "Bot":
        return "human"
    if actor.login == copilot_login.login:
        return "copilot"
    return "other_bot"


def _thread_root_comment(node: _JsonObject) -> tuple[str, int, _Actor | None]:
    raw_comments = node.get("comments")
    if not isinstance(raw_comments, dict):
        return ("", 0, None)
    nodes = cast(_JsonObject, raw_comments).get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ("", 0, None)
    first: Any = nodes[0]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(first, dict):
        return ("", 0, None)
    cn = cast(_JsonObject, first)
    body = str(cn.get("body", ""))
    db_id = cn.get("databaseId", 0)
    return (body, db_id if isinstance(db_id, int) else 0, _parse_actor(cn))


def _comment_count(node: _JsonObject) -> int | None:
    raw_comments = node.get("comments")
    if not isinstance(raw_comments, dict):
        return None
    total = cast(_JsonObject, raw_comments).get("totalCount")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return None


def _human_thread(node: _JsonObject, actor: _Actor | None) -> HumanThread:
    return HumanThread(
        thread_id=str(node.get("id", "")),
        login=actor.login if actor is not None else "",
        comment_count=_comment_count(node),
    )


def _classify_thread_node(
    raw: object, copilot_login: GraphQLActorLogin
) -> tuple[CopilotThread | None, ActorClass | None, HumanThread | None]:
    if not isinstance(raw, dict):
        return (None, None, None)
    node = cast(_JsonObject, raw)
    if node.get("isResolved"):
        return (None, None, None)
    body, db_id, actor = _thread_root_comment(node)
    actor_class = _classify_actor(actor, copilot_login)
    if actor_class == "copilot":
        thread_id = str(node.get("id", ""))
        copilot = CopilotThread(thread_id=thread_id, body=body, database_id=db_id)
        return (copilot, actor_class, None)
    if actor_class == "human":
        return (None, actor_class, _human_thread(node, actor))
    return (None, actor_class, None)


def parse_thread_info(
    stdout: str, copilot_login: GraphQLActorLogin = _COPILOT_GRAPHQL_LOGIN
) -> ThreadInfo:
    review_threads = _nav_review_threads(stdout)
    if review_threads is None:
        return _EMPTY_THREAD_INFO
    total = _safe_int(review_threads.get("totalCount"))
    nodes = _safe_list(review_threads.get("nodes"))
    copilot: list[CopilotThread] = []
    humans: list[HumanThread] = []
    other_bot = 0
    for raw in nodes:
        thread, actor_class, human_thread = _classify_thread_node(raw, copilot_login)
        if thread is not None:
            copilot.append(thread)
        elif human_thread is not None:
            humans.append(human_thread)
        elif actor_class == "other_bot":
            other_bot += 1
    return ThreadInfo(
        copilot_threads=tuple(copilot), human_unresolved=len(humans),
        other_bot_unresolved=other_bot, total=total, human_threads=tuple(humans),
    )


def _is_skipped_non_thread_node(node: _JsonObject, source: CommentSource) -> bool:
    if source == "review_body" and str(node.get("state", "")).upper() == "APPROVED":
        return True
    return not str(node.get("body", "")).strip()


def _node_database_id(node: _JsonObject) -> int:
    db_id_raw = node.get("databaseId", 0)
    return db_id_raw if isinstance(db_id_raw, int) else 0


def _classify_non_thread_node(
    raw: object, copilot_login: GraphQLActorLogin, source: CommentSource,
) -> tuple[CopilotComment | None, ActorClass | None]:
    if not isinstance(raw, dict):
        return (None, None)
    node = cast(_JsonObject, raw)
    if _is_skipped_non_thread_node(node, source):
        return (None, None)
    actor_class = _classify_actor(_parse_actor(node), copilot_login)
    if actor_class == "copilot":
        item = CopilotComment(
            database_id=_node_database_id(node), body=str(node.get("body", "")), source=source,
        )
        return (item, actor_class)
    return (None, actor_class)


def _parse_comment_nodes(
    pr: _JsonObject, key: str, source: CommentSource, copilot_login: GraphQLActorLogin,
) -> CommentInfo:
    raw_section = pr.get(key)
    if not isinstance(raw_section, dict):
        return _EMPTY_COMMENT_INFO
    section = cast(_JsonObject, raw_section)
    total = _safe_int(section.get("totalCount"))
    nodes = _safe_list(section.get("nodes"))
    copilot: list[CopilotComment] = []
    human = 0
    other_bot = 0
    for raw in nodes:
        item, actor_class = _classify_non_thread_node(raw, copilot_login, source)
        if item is not None:
            copilot.append(item)
        elif actor_class == "human":
            human += 1
        elif actor_class == "other_bot":
            other_bot += 1
    return CommentInfo(
        copilot_items=tuple(copilot), human_items=human, other_bot_items=other_bot, total=total,
    )


_SOURCE_KEYS: tuple[tuple[CommentSource, str], ...] = (
    ("issue_comment", "comments"),
    ("review_body", "reviews"),
)


def _combine(parts: Iterable[CommentInfo]) -> CommentInfo:
    parts = list(parts)
    return CommentInfo(
        copilot_items=tuple(i for p in parts for i in p.copilot_items),
        human_items=sum(p.human_items for p in parts),
        other_bot_items=sum(p.other_bot_items for p in parts),
        total=sum(p.total for p in parts),
    )


def _comment_info_by_source(
    stdout: str, copilot_login: GraphQLActorLogin,
) -> dict[CommentSource, CommentInfo]:
    pr = _nav_pull_request(stdout)
    if pr is None:
        return dict.fromkeys((s for s, _ in _SOURCE_KEYS), _EMPTY_COMMENT_INFO)
    return {
        source: _parse_comment_nodes(pr, key, source, copilot_login)
        for source, key in _SOURCE_KEYS
    }


def parse_comment_info(
    stdout: str, copilot_login: GraphQLActorLogin = _COPILOT_GRAPHQL_LOGIN
) -> CommentInfo:
    return _combine(_comment_info_by_source(stdout, copilot_login).values())


def _blocking_comment_info(
    stdout: str, copilot_login: GraphQLActorLogin,
) -> CommentInfo:
    by_source = _comment_info_by_source(stdout, copilot_login)
    return _combine(v for k, v in by_source.items() if k not in _SHADOW_CATEGORIES)


_QUOTA_CALL_SITE = _forge_quota.PR_FEEDBACK_CALL_SITE


def _null_quota_sample(outcome: _forge_quota.QuotaOutcome) -> _forge_quota.QuotaSample:
    return _forge_quota.QuotaSample(
        resource="graphql", call_site=_QUOTA_CALL_SITE,
        limit=None, cost=None, remaining=None, reset_at=None, outcome=outcome,
    )


def _fetch_feedback_raw(pr_number: int, *, forge_target: ForgeTarget) -> tuple[str, str]:
    try:
        result = active_forge().feedback(
            pr_number, forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        _forge_quota.record_quota_sample(_null_quota_sample("error"))
        return ("", f"gh api graphql timed out: {exc}")
    except FileNotFoundError as exc:
        _forge_quota.record_quota_sample(_null_quota_sample("error"))
        return ("", f"gh api graphql raised {exc}")
    if result.returncode != 0:
        return _handle_feedback_fetch_failure(result)
    return (result.stdout, "")


def _handle_feedback_fetch_failure(result: ApiResult) -> tuple[str, str]:
    reason = f"gh api graphql rc={result.returncode}: {result.stderr[:200]}"
    failure_class = _pr_feedback_failure.classify_feedback_failure(
        returncode=result.returncode, stderr=result.stderr,
    )
    if failure_class == "rate_limited":
        _forge_quota.record_quota_sample(_null_quota_sample("rate_limited"))
        raise _forge_errors.ForgeRateLimited(
            reason, _forge_errors.forge_rate_limit_reset_at(result.stderr)
        )
    if failure_class == "transient":
        _forge_quota.record_quota_sample(_null_quota_sample("transient"))
        raise _forge_errors.ForgeTransientError(reason)
    _forge_quota.record_quota_sample(_null_quota_sample("error"))
    return ("", reason)


def _record_feedback_quota_sample(stdout: str, *, outcome: _forge_quota.QuotaOutcome) -> None:
    sample = _forge_quota.parse_graphql_rate_limit(
        stdout, call_site=_QUOTA_CALL_SITE, outcome=outcome,
    )
    _forge_quota.record_quota_sample(sample)


def _handle_feedback_graphql_errors(stdout: str, typed_errors: list[object]) -> str:
    try:
        _pr_feedback_failure.raise_for_graphql_errors(
            typed_errors, context="gh api graphql feedback"
        )
    except _forge_errors.ForgeRateLimited:
        _record_feedback_quota_sample(stdout, outcome="rate_limited")
        raise
    _record_feedback_quota_sample(stdout, outcome="error")
    errors_repr = str(typed_errors)
    return f"graphql errors payload: {errors_repr[:200]}"


def _validate_feedback_body(stdout: str) -> str:
    parsed = safe_json(stdout)
    if not isinstance(parsed, dict):
        _record_feedback_quota_sample(stdout, outcome="error")
        return "feedback response body did not parse as a JSON object"
    obj = cast(_JsonObject, parsed)
    errors = obj.get("errors")
    if isinstance(errors, list) and errors:
        typed_errors = cast("list[object]", errors)
        return _handle_feedback_graphql_errors(stdout, typed_errors)
    if _nav_pull_request(stdout) is None:
        _record_feedback_quota_sample(stdout, outcome="error")
        return "graphql response missing data.repository.pullRequest"
    _record_feedback_quota_sample(stdout, outcome="ok")
    return ""


def _failed_snapshot(reason: str) -> FeedbackSnapshot:
    return FeedbackSnapshot(
        ok=False,
        thread_info=_EMPTY_THREAD_INFO,
        shadow_comment_info=_EMPTY_COMMENT_INFO,
        blocking_comment_info=_EMPTY_COMMENT_INFO,
        failure_reason=reason,
    )


def _filter_acked(
    pr_number: int, comments: tuple[CopilotComment, ...], forge_target: ForgeTarget,
) -> tuple[CopilotComment, ...]:
    if not comments:
        return comments
    acked = _pr_state_paths.load_acked_comment_ids(pr_number, forge_target)
    return tuple(c for c in comments if c.database_id not in acked)


def _ack_filtered(
    info: CommentInfo, pr_number: int, forge_target: ForgeTarget,
) -> CommentInfo:
    filtered = _filter_acked(pr_number, info.copilot_items, forge_target)
    return CommentInfo(
        copilot_items=filtered, human_items=info.human_items,
        other_bot_items=info.other_bot_items, total=info.total,
    )


def read_feedback(
    pr_number: int, *, forge_target: ForgeTarget,
) -> FeedbackSnapshot:
    stdout, reason = _fetch_feedback_raw(pr_number, forge_target=forge_target)
    if not reason:
        reason = _validate_feedback_body(stdout)
    if reason:
        return _failed_snapshot(reason)
    copilot_login = _COPILOT_GRAPHQL_LOGIN
    shadow = _ack_filtered(parse_comment_info(stdout, copilot_login), pr_number, forge_target)
    blocking = _ack_filtered(
        _blocking_comment_info(stdout, copilot_login), pr_number, forge_target
    )
    return FeedbackSnapshot(
        ok=True,
        thread_info=parse_thread_info(stdout, copilot_login),
        shadow_comment_info=shadow,
        blocking_comment_info=blocking,
        failure_reason="",
    )


_STRANGER_PERMISSIONS = frozenset({"read", "triage", "none"})
_KNOWN_PERMISSIONS = _STRANGER_PERMISSIONS | frozenset({"admin", "maintain", "write"})
_ERROR_CAP = 500
_T = TypeVar("_T")


@dataclass(frozen=True)
class _SettleCtx:
    pr_number: int
    forge_target: ForgeTarget
    permissions: dict[str, str | None] = field(default_factory=dict[str, str | None])

    def audit(self, action: str, **fields: object) -> None:
        append_audit({
            "action": action, "pr_number": self.pr_number,
            "repo": self.forge_target.repo.slug, **fields,
        })


@dataclass(frozen=True)
class _ReadBack:
    is_resolved: bool | None
    comment_count: int | None


_NO_READ_BACK = _ReadBack(is_resolved=None, comment_count=None)


def _skip_logins(forge_target: ForgeTarget) -> frozenset[str]:
    skip = {forge_target.repo.owner.casefold()}
    identity = _pr_branch.verifier_identity()
    if identity is not None:
        skip.add(identity.login.casefold())
    return frozenset(skip)


def _is_candidate(thread: HumanThread, skip: frozenset[str]) -> bool:
    return (
        bool(thread.login)
        and bool(thread.thread_id)
        and thread.comment_count == 1
        and thread.login.casefold() not in skip
    )


def _call_forge(call: Callable[[], _T]) -> _T | str:
    try:
        return call()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:_ERROR_CAP]


def _known_permission(read: PermissionRead | str) -> str | None:
    if isinstance(read, str) or read.outcome != "ok":
        return None
    return read.permission if read.permission in _KNOWN_PERMISSIONS else None


def _permission_failure(read: PermissionRead | str) -> str:
    if isinstance(read, str):
        return read
    if read.outcome != "ok":
        return read.stderr[:_ERROR_CAP]
    return f"unrecognised permission {read.permission!r}"[:_ERROR_CAP]


def _read_permission(ctx: _SettleCtx, login: str) -> str | None:
    read = _call_forge(
        lambda: active_forge().repo_permission(
            login, forge_target=ctx.forge_target, timeout=GH_TIMEOUT_S,
        )
    )
    permission = _known_permission(read)
    if permission is None:
        ctx.audit(
            "stranger_permission_lookup_failed", login=login, error=_permission_failure(read),
        )
    return permission


def _lookup_permission(ctx: _SettleCtx, login: str) -> str | None:
    key = login.casefold()
    if key not in ctx.permissions:
        ctx.permissions[key] = _read_permission(ctx, login)
    return ctx.permissions[key]


def _read_back(stdout: str, key: str) -> _ReadBack:
    thread = _nav_json_path(stdout, "data", key, "thread")
    if thread is None:
        return _NO_READ_BACK
    resolved = thread.get("isResolved")
    return _ReadBack(
        is_resolved=resolved if isinstance(resolved, bool) else None,
        comment_count=_comment_count(thread),
    )


def _mutate(
    call: Callable[[], ApiResult], key: str, *, resolved: bool,
) -> tuple[_ReadBack, str | None]:
    result = _call_forge(call)
    if isinstance(result, str):
        return (_NO_READ_BACK, result)
    if not result.ok:
        return (_NO_READ_BACK, result.stderr[:_ERROR_CAP])
    read_back = _read_back(result.stdout, key)
    if read_back.is_resolved is not resolved:
        return (read_back, "unexpected read-back")
    return (read_back, None)


def _audit_thread(
    ctx: _SettleCtx, action: str, thread: HumanThread, permission: str, *,
    comment_count: int | None, error: str | None = None,
) -> None:
    ctx.audit(
        action, thread_id=thread.thread_id, login=thread.login, permission=permission,
        comment_count=comment_count, error=error,
    )


def _undo(
    ctx: _SettleCtx, thread: HumanThread, permission: str, comment_count: int | None,
) -> None:
    _, error = _mutate(
        lambda: active_forge().unresolve_thread(thread.thread_id, forge_target=ctx.forge_target),
        "unresolveReviewThread", resolved=False,
    )
    if error is None:
        _audit_thread(
            ctx, "stranger_thread_unresolved", thread, permission, comment_count=comment_count,
        )
        return
    _audit_thread(
        ctx, "stranger_thread_unresolve_failed", thread, permission,
        comment_count=comment_count, error=error,
    )


def _resolve_and_verify(ctx: _SettleCtx, thread: HumanThread, permission: str) -> bool:
    read_back, error = _mutate(
        lambda: active_forge().resolve_thread_checked(
            thread.thread_id, forge_target=ctx.forge_target,
        ),
        "resolveReviewThread", resolved=True,
    )
    if error is not None:
        _audit_thread(
            ctx, "stranger_thread_resolve_failed", thread, permission,
            comment_count=thread.comment_count, error=error,
        )
        return False
    _audit_thread(
        ctx, "stranger_thread_resolved", thread, permission,
        comment_count=read_back.comment_count,
    )
    if read_back.comment_count == 1:
        return True
    _undo(ctx, thread, permission, read_back.comment_count)
    return False


def _settle_one(ctx: _SettleCtx, thread: HumanThread) -> bool:
    permission = _lookup_permission(ctx, thread.login)
    if permission is None or permission not in _STRANGER_PERMISSIONS:
        return False
    return _resolve_and_verify(ctx, thread, permission)


def _stranger_candidates(info: ThreadInfo, forge_target: ForgeTarget) -> list[HumanThread]:
    skip = _skip_logins(forge_target)
    return [t for t in info.human_threads if _is_candidate(t, skip)]


def _settled_threads(
    candidates: list[HumanThread], pr_number: int, forge_target: ForgeTarget,
) -> list[HumanThread]:
    ctx = _SettleCtx(pr_number=pr_number, forge_target=forge_target)
    return [t for t in candidates if _settle_one(ctx, t)]


def _without_settled(
    snapshot: FeedbackSnapshot, settled: list[HumanThread],
) -> FeedbackSnapshot:
    info = snapshot.thread_info
    settled_ids = {t.thread_id for t in settled}
    remaining = tuple(t for t in info.human_threads if t.thread_id not in settled_ids)
    return FeedbackSnapshot(
        ok=snapshot.ok,
        thread_info=ThreadInfo(
            copilot_threads=info.copilot_threads,
            human_unresolved=len(remaining),
            other_bot_unresolved=info.other_bot_unresolved,
            total=info.total,
            human_threads=remaining,
        ),
        shadow_comment_info=snapshot.shadow_comment_info,
        blocking_comment_info=snapshot.blocking_comment_info,
        failure_reason=snapshot.failure_reason,
    )


def _settle_stranger_threads(
    snapshot: FeedbackSnapshot, pr_number: int, forge_target: ForgeTarget,
) -> FeedbackSnapshot:
    candidates = _stranger_candidates(snapshot.thread_info, forge_target)
    if not candidates or not _pr_post.verifier_identity_available(forge_target):
        return snapshot
    settled = _settled_threads(candidates, pr_number, forge_target)
    return _without_settled(snapshot, settled) if settled else snapshot


def fetch_feedback(
    pr_number: int, *, forge_target: ForgeTarget,
) -> FeedbackSnapshot:
    snapshot = read_feedback(pr_number, forge_target=forge_target)
    if not snapshot.ok or not snapshot.thread_info.human_threads:
        return snapshot
    return _settle_stranger_threads(snapshot, pr_number, forge_target)


def has_unresolved_human(thread_info: ThreadInfo, blocking_comment_info: CommentInfo) -> bool:
    return thread_info.human_unresolved > 0 or blocking_comment_info.human_items > 0


def has_unresolved_copilot(
    thread_info: ThreadInfo, active_blocking_comments: tuple[CopilotComment, ...],
) -> bool:
    return bool(thread_info.copilot_threads) or bool(active_blocking_comments)
