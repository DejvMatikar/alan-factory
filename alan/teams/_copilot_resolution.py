from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Literal, cast

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.forge import active_forge
from alan.teams import (
    _block_correction_audit,
    _pr_audit_log,
    _pr_feedback,
    _pr_forge,
    _pr_state_paths,
    _pr_threads,
)
from alan.teams._copilot_resolution_block import (
    CopilotResolutionRow,
    parse_copilot_resolution_yaml,
    resolution_block_attempted,
)
from alan.workspace import Workspace
from alan.worktree import WorktreeHandle

ACK_CAP = 3

RefusalReason = Literal[
    "ack_cap_exceeded", "forge_call_failed", "already_handled", "thread_not_listed"
]

PostOutcome = Literal["posted", "failed", "ambiguous"]


@dataclass(frozen=True)
class CopilotRefusal:
    thread_id: str | None
    database_id: int | None
    reason: RefusalReason


@dataclass(frozen=True)
class CopilotResolutionOutcome:
    resolved: tuple[str, ...]
    acknowledged: tuple[str, ...]
    refused: tuple[CopilotRefusal, ...]


def _reply_outcome(thread_id: str, body: str) -> PostOutcome:
    try:
        result = active_forge().reply_to_thread(
            thread_id, body, forge_target=DEFAULT_FORGE_TARGET,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "ambiguous"
    return "posted" if result.ok else "failed"


def post_reply(thread_id: str, body: str) -> bool:
    return _reply_outcome(thread_id, body) == "posted"


def _comment_outcome(pr_number: int, body: str, *, forge_target: ForgeTarget) -> PostOutcome:
    try:
        result = active_forge().comment_on_pr(pr_number, body, forge_target=forge_target)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "ambiguous"
    return "posted" if result.ok else "failed"


def post_pr_comment(pr_number: int, body: str, *, forge_target: ForgeTarget) -> bool:
    return _comment_outcome(pr_number, body, forge_target=forge_target) == "posted"


def _audit(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    *, action: str, resolved: bool, acked: bool, refused: bool = False,
    reason: str | None = None,
) -> None:
    entry: dict[str, object] = {
        "action": action,
        "pr_number": pr_number,
        "thread_id": row.thread_id,
        "database_id": row.database_id,
        "mode": row.mode,
        "repo": forge_target.repo.slug,
        "resolved": resolved,
        "acked": acked,
        "refused": refused,
    }
    if reason is not None:
        entry["reason"] = reason
    _pr_audit_log.append_copilot_audit(entry)


def _refuse(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    reason: RefusalReason, refused: list[CopilotRefusal],
) -> None:
    _audit(
        row, pr_number, forge_target, action="copilot_resolution_refused",
        resolved=False, acked=False, refused=True, reason=reason,
    )
    refused.append(
        CopilotRefusal(thread_id=row.thread_id, database_id=row.database_id, reason=reason)
    )


def _ack_cap_ok(pr_number: int, forge_target: ForgeTarget, head_sha: str | None) -> bool:
    count = _pr_state_paths.load_ack_budget(pr_number, forge_target, head_sha=head_sha)
    if count >= ACK_CAP:
        return False
    _pr_state_paths.bump_ack_budget(pr_number, forge_target, head_sha=head_sha)
    return True


def _resolvable_thread_ids(
    listed: tuple[_pr_threads.ReviewThread, ...],
) -> frozenset[str]:
    copilot_login = (
        _pr_feedback._COPILOT_GRAPHQL_LOGIN.login  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    )
    return frozenset(t.thread_id for t in listed if t.author_login == copilot_login)


def _reentry_resolve_ok(
    pr_number: int, forge_target: ForgeTarget, thread_id: str, resolvable: frozenset[str],
) -> bool:
    return (
        _pr_state_paths.is_thread_replied(pr_number, thread_id, forge_target)
        and not _pr_state_paths.is_thread_resolved(pr_number, thread_id, forge_target)
        and thread_id in resolvable
    )


def _apply_fix_row(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    resolved: list[str], refused: list[CopilotRefusal], resolvable: frozenset[str],
) -> None:
    thread_id = cast(str, row.thread_id)
    if thread_id not in resolvable:
        _refuse(row, pr_number, forge_target, "thread_not_listed", refused)
        return
    if _pr_threads.resolve_review_thread(thread_id):
        _audit(row, pr_number, forge_target, action="copilot_resolution_fix",
               resolved=True, acked=False)
        resolved.append(thread_id)
    else:
        _refuse(row, pr_number, forge_target, "forge_call_failed", refused)


def _resolve_after_reply(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    thread_id: str, resolved: list[str], acknowledged: list[str], *, resolve: bool,
) -> None:
    ok = resolve and _pr_threads.resolve_review_thread(thread_id)
    if ok:
        _pr_state_paths.mark_thread_resolved(pr_number, thread_id, forge_target)
    _audit(row, pr_number, forge_target, action="copilot_resolution_acknowledge",
           resolved=ok, acked=True)
    acknowledged.append(thread_id)
    if ok:
        resolved.append(thread_id)


def _apply_acknowledge_row(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    head_sha: str | None, resolved: list[str], acknowledged: list[str],
    refused: list[CopilotRefusal], resolvable: frozenset[str],
) -> None:
    thread_id = cast(str, row.thread_id)
    body = cast(str, row.body)
    if not _pr_state_paths.claim_acked_thread(pr_number, thread_id, forge_target):
        if _reentry_resolve_ok(pr_number, forge_target, thread_id, resolvable):
            _resolve_after_reply(
                row, pr_number, forge_target, thread_id, resolved, acknowledged, resolve=True,
            )
            return
        _refuse(row, pr_number, forge_target, "already_handled", refused)
        return
    if not _ack_cap_ok(pr_number, forge_target, head_sha):
        _pr_state_paths.release_acked_thread(pr_number, thread_id, forge_target)
        _refuse(row, pr_number, forge_target, "ack_cap_exceeded", refused)
        return
    outcome = _reply_outcome(thread_id, body)
    if outcome == "failed":
        _pr_state_paths.release_acked_thread(pr_number, thread_id, forge_target)
        _pr_state_paths.release_ack_budget(pr_number, forge_target, head_sha=head_sha)
        _refuse(row, pr_number, forge_target, "forge_call_failed", refused)
        return
    if outcome == "ambiguous":
        _refuse(row, pr_number, forge_target, "forge_call_failed", refused)
        return
    _pr_state_paths.mark_thread_replied(pr_number, thread_id, forge_target)
    _resolve_after_reply(
        row, pr_number, forge_target, thread_id, resolved, acknowledged,
        resolve=thread_id in resolvable,
    )


def _apply_comment_ack_row(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    head_sha: str | None, acknowledged: list[str], refused: list[CopilotRefusal],
) -> None:
    database_id = cast(int, row.database_id)
    body = cast(str, row.body)
    if database_id in _pr_state_paths.load_acked_comment_ids(pr_number, forge_target):
        _refuse(row, pr_number, forge_target, "already_handled", refused)
        return
    if not _ack_cap_ok(pr_number, forge_target, head_sha):
        _refuse(row, pr_number, forge_target, "ack_cap_exceeded", refused)
        return
    _pr_state_paths.save_acked_comment_id(pr_number, database_id, forge_target)
    outcome = _comment_outcome(pr_number, body, forge_target=forge_target)
    if outcome == "failed":
        _pr_state_paths.release_acked_comment_id(pr_number, database_id, forge_target)
        _pr_state_paths.release_ack_budget(pr_number, forge_target, head_sha=head_sha)
        _refuse(row, pr_number, forge_target, "forge_call_failed", refused)
        return
    if outcome == "ambiguous":
        _refuse(row, pr_number, forge_target, "forge_call_failed", refused)
        return
    _audit(row, pr_number, forge_target, action="copilot_resolution_comment_ack",
           resolved=False, acked=True)
    acknowledged.append(str(database_id))


def _apply_row(
    row: CopilotResolutionRow, pr_number: int, forge_target: ForgeTarget,
    head_sha: str | None, resolved: list[str], acknowledged: list[str],
    refused: list[CopilotRefusal], resolvable: frozenset[str],
) -> None:
    if row.mode == "fix":
        _apply_fix_row(row, pr_number, forge_target, resolved, refused, resolvable)
    elif row.mode == "acknowledge":
        _apply_acknowledge_row(
            row, pr_number, forge_target, head_sha, resolved, acknowledged, refused, resolvable
        )
    else:
        _apply_comment_ack_row(row, pr_number, forge_target, head_sha, acknowledged, refused)


def apply(
    rows: tuple[CopilotResolutionRow, ...],
    *, pr_number: int, forge_target: ForgeTarget, head_sha: str | None,
    listed: tuple[_pr_threads.ReviewThread, ...],
) -> CopilotResolutionOutcome:
    resolved: list[str] = []
    acknowledged: list[str] = []
    refused: list[CopilotRefusal] = []
    resolvable = _resolvable_thread_ids(listed)
    for row in rows:
        _apply_row(
            row, pr_number, forge_target, head_sha, resolved, acknowledged, refused, resolvable
        )
    return CopilotResolutionOutcome(
        resolved=tuple(resolved), acknowledged=tuple(acknowledged), refused=tuple(refused),
    )


def _current_head_sha(handle: WorktreeHandle) -> str | None:
    try:
        result = process_runner.run_subprocess(
            ["git", "rev-parse", "HEAD"], cwd=str(handle.path), timeout=_pr_forge.GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = (result.stdout or "").strip()
    return sha or None


def maybe_resolve(
    workspace: Workspace,
    handle: WorktreeHandle,
    synthesis: str,
    pr_number: int,
    forge_target: ForgeTarget,
    *, role: str, allow_fix: bool = True,
    listed: tuple[_pr_threads.ReviewThread, ...],
) -> None:
    if not resolution_block_attempted(synthesis):
        return
    rows = _block_correction_audit.record_unreachable_attempt(
        site="copilot_resolution.yaml", role=role,
        validate=parse_copilot_resolution_yaml, text=synthesis, task_id=workspace.task_id,
    )
    if not rows:
        return
    if not allow_fix:
        rows = tuple(r for r in rows if r.mode != "fix")
        if not rows:
            return
    apply(
        rows, pr_number=pr_number, forge_target=forge_target,
        head_sha=_current_head_sha(handle), listed=listed,
    )


__all__ = [
    "ACK_CAP",
    "CopilotRefusal",
    "CopilotResolutionOutcome",
    "apply",
    "maybe_resolve",
    "post_pr_comment",
    "post_reply",
]
