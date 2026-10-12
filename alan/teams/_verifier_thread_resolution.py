from __future__ import annotations

from dataclasses import dataclass

from alan.engine_config import ForgeTarget
from alan.forge import LoginRead, active_forge
from alan.teams import (
    _pr_branch,
    _pr_forge,
    _pr_post,
    _pr_threads,
    _verdict,
    _verification_artifacts,
)

HELD_ACTION = "verifier_thread_resolution_held"
RESOLVED_ACTION = "verifier_thread_resolved"
REFUSED_ACTION = "verifier_thread_refused"
ERROR_TEXT_CAP = 500


@dataclass(frozen=True)
class _PassContext:
    task_id: str
    pr_number: int
    inv_id: str | None
    repo: str

    def audit(self, action: str, **fields: object) -> None:
        _verification_artifacts.append_audit({
            "action": action,
            "task_id": self.task_id,
            "pr_number": self.pr_number,
            "inv_id": self.inv_id,
            "repo": self.repo,
            **fields,
        })


@dataclass(frozen=True)
class _IdentityCheck:
    reason: str | None
    observed_login: str | None = None
    error: str | None = None


def _failure_text(read: LoginRead) -> str:
    if read.outcome == "failed" and read.returncode == 0:
        return "empty login"
    return read.stderr[:ERROR_TEXT_CAP]


def _confirm_identity(forge_target: ForgeTarget) -> _IdentityCheck:
    identity = _pr_branch.verifier_identity()
    if identity is None:
        return _IdentityCheck(reason="verifier_login_unconfigured")
    read = active_forge().viewer_login(
        forge_target=forge_target, as_verifier=True, timeout=_pr_forge.GH_TIMEOUT_S,
    )
    if read.outcome != "ok":
        return _IdentityCheck(reason="identity_check_failed", error=_failure_text(read))
    if not identity.matches(read.login):
        return _IdentityCheck(reason="login_mismatch", observed_login=read.login)
    return _IdentityCheck(reason=None, observed_login=read.login)


def _refusal_reason(thread: _pr_threads.ReviewThread) -> str | None:
    if thread.reply_count is None:
        return "reply_count_unreadable"
    if thread.reply_count > 0:
        return "has_replies"
    return None


def _resolve_candidates(
    ctx: _PassContext,
    candidates: tuple[_pr_threads.ReviewThread, ...],
    forge_target: ForgeTarget,
) -> None:
    stopped = False
    for thread in candidates:
        if stopped:
            ctx.audit(REFUSED_ACTION, thread_id=thread.thread_id, reason="stopped_after_failure")
            continue
        reason = _refusal_reason(thread)
        if reason is not None:
            ctx.audit(REFUSED_ACTION, thread_id=thread.thread_id, reason=reason)
            continue
        outcome = _pr_threads.resolve_thread_as(thread.thread_id, forge_target=forge_target)
        if outcome.ok:
            ctx.audit(RESOLVED_ACTION, thread_id=thread.thread_id)
        else:
            ctx.audit(
                REFUSED_ACTION, thread_id=thread.thread_id,
                reason="forge_refused", error=outcome.error,
            )
            stopped = True


@dataclass(frozen=True)
class _ReadyToResolve:
    candidates: tuple[_pr_threads.ReviewThread, ...]


def _verifier_threads(
    threads: tuple[_pr_threads.ReviewThread, ...],
) -> tuple[_pr_threads.ReviewThread, ...]:
    identity = _pr_branch.verifier_identity()
    if identity is None:
        return ()
    return tuple(t for t in threads if identity.matches(t.author_login))


def _prepare(
    ctx: _PassContext, pr_number: int, forge_target: ForgeTarget,
) -> _ReadyToResolve | None:
    if not _pr_post.verifier_identity_available(forge_target):
        ctx.audit(HELD_ACTION, reason="no_reviewer_credential")
        return None
    identity = _confirm_identity(forge_target)
    if identity.reason is not None:
        ctx.audit(
            HELD_ACTION, reason=identity.reason,
            observed_login=identity.observed_login, error=identity.error,
        )
        return None
    threads = _pr_threads.fetch_unresolved_threads(pr_number, forge_target=forge_target)
    if threads is None:
        ctx.audit(HELD_ACTION, reason="thread_read_failed")
        return None
    return _ReadyToResolve(candidates=_verifier_threads(threads))


def resolve_after_approval(
    event: str,
    critical: int,
    pr_number: int,
    *,
    task_id: str,
    inv_id: str | None,
    forge_target: ForgeTarget,
    counts: _verdict.CriticalCountReport | None,
) -> None:
    ctx = _PassContext(
        task_id=task_id, pr_number=pr_number, inv_id=inv_id, repo=forge_target.repo.slug,
    )
    if event != "APPROVE":
        return
    floor_reason = _verdict.count_floor_reason(critical, counts)
    if floor_reason is not None:
        ctx.audit(HELD_ACTION, reason=floor_reason)
        return
    ready = _prepare(ctx, pr_number, forge_target)
    if ready is None:
        return
    _resolve_candidates(ctx, ready.candidates, forge_target)
