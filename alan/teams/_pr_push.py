from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget
from alan.handover import CredentialFailureClass
from alan.teams import _pr_branch
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_forge import GH_TIMEOUT_S

logger = logging.getLogger(__name__)


def push_branch(
    worktree: Path,
    branch: str,
    task_id: str,
    *, forge_target: ForgeTarget,
) -> PushResult:
    argv = ["git", *secrets_store.GIT_CRED_RESET_ARGS, "push", "-u", "origin", branch]
    try:
        result = process_runner.run_subprocess(
            argv, cwd=str(worktree), timeout=GH_TIMEOUT_S,
            env=secrets_store.transport_env(forge_target),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git push raised %s", exc)
        append_audit({
            "task_id": task_id,
            "action": "push_failed",
            "branch": branch,
            "error": str(exc),
        })
        return PushResult(outcome=PushOutcome.PUSH_FAILED)
    if result.returncode != 0:
        logger.warning("git push failed (rc=%s): %s", result.returncode, result.stderr)
        append_audit({
            "task_id": task_id,
            "action": "push_failed",
            "branch": branch,
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        classified = _pr_branch.classify_credential_failure(
            token_present=True, returncode=result.returncode, stderr=result.stderr,
        )
        return PushResult(
            outcome=PushOutcome.PUSH_FAILED,
            credential_failure=classified if classified == "expired_token" else None,
            detail=_push_failure_detail(result.stderr),
        )
    append_audit({"task_id": task_id, "action": "push_ok", "branch": branch})
    return PushResult(outcome=PushOutcome.PUSHED)


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PushOutcome(Enum):
    PUSHED = "pushed"
    RECONCILED = "reconciled"
    DIVERGED_ESCALATE = "diverged_escalate"
    PUSH_FAILED = "push_failed"


@dataclass(frozen=True)
class PushResult:
    outcome: PushOutcome
    credential_failure: CredentialFailureClass | None = None
    detail: str = ""


def _is_lease_rejection(stderr: str) -> bool:
    return "stale info" in stderr.lower()


def _remote_lines(stderr: str) -> list[str]:
    lines = (
        line.removeprefix("remote:").strip()
        for line in stderr.splitlines()
        if line.startswith("remote:")
    )
    return [line for line in lines if line]


def _truncated(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut] + "…"


def _push_failure_detail(stderr: str | None) -> str:
    return _truncated(" ".join(_remote_lines(stderr or "")))


def _try_push_with_lease(
    worktree: Path, branch: str, task_id: str, base_sha: str,
    *, forge_target: ForgeTarget,
) -> PushResult | None:
    try:
        result = process_runner.run_subprocess(
            [
                "git", *secrets_store.GIT_CRED_RESET_ARGS, "push",
                f"--force-with-lease={branch}:{base_sha}", "-u", "origin", branch,
            ],
            cwd=str(worktree),
            timeout=GH_TIMEOUT_S,
            env=secrets_store.transport_env(forge_target),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git push (fix) raised %s", exc)
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "error": str(exc),
        })
        return PushResult(outcome=PushOutcome.PUSH_FAILED)
    if result.returncode == 0:
        append_audit({
            "task_id": task_id, "action": "push_ok", "branch": branch, "phase": "fix",
        })
        return PushResult(outcome=PushOutcome.PUSHED)
    if _is_lease_rejection(result.stderr or ""):
        return None
    logger.warning("git push (fix) failed (rc=%s): %s", result.returncode, result.stderr)
    append_audit({
        "task_id": task_id, "action": "push_failed", "branch": branch,
        "phase": "fix", "returncode": result.returncode,
        "stderr": (result.stderr or "")[:500],
    })
    classified = _pr_branch.classify_credential_failure(
        token_present=True, returncode=result.returncode, stderr=result.stderr,
    )
    return PushResult(
        outcome=PushOutcome.PUSH_FAILED,
        credential_failure=classified if classified == "expired_token" else None,
        detail=_push_failure_detail(result.stderr),
    )


def _fetch_for_reconcile(
    worktree: Path, branch: str, task_id: str,
    *, forge_target: ForgeTarget,
) -> bool:
    try:
        result = process_runner.run_subprocess(
            ["git", *secrets_store.GIT_CRED_RESET_ARGS, "fetch", "origin", "--", branch],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
            env=secrets_store.transport_env(forge_target),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git fetch (fix reconcile) raised %s", exc)
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "error": f"fetch for reconcile raised: {exc}",
        })
        return False
    if result.returncode != 0:
        logger.warning(
            "git fetch (fix reconcile) failed (rc=%s): %s", result.returncode, result.stderr,
        )
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        return False
    return True


def try_resolve_remote_sha(worktree: Path, branch: str) -> str:
    try:
        result = process_runner.run_subprocess(
            ["git", "rev-parse", f"origin/{branch}"],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
        )
        return (result.stdout or "").strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _abort_rebase(worktree: Path) -> None:
    try:
        process_runner.run_subprocess(
            ["git", "rebase", "--abort"],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git rebase --abort raised %s", exc)


def _rebase_onto(worktree: Path, base_sha: str, task_id: str, branch: str) -> bool:
    try:
        result = process_runner.run_subprocess(
            ["git", "rebase", "--onto", f"origin/{branch}", base_sha],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git rebase (fix reconcile) raised %s", exc)
        _abort_rebase(worktree)
        append_audit({
            "task_id": task_id, "action": "push_diverged_escalate", "branch": branch,
            "phase": "fix", "error": f"rebase raised: {exc}",
        })
        return False
    if result.returncode != 0:
        logger.warning(
            "git rebase (fix reconcile) failed (rc=%s): %s", result.returncode, result.stderr,
        )
        _abort_rebase(worktree)
        append_audit({
            "task_id": task_id, "action": "push_diverged_escalate", "branch": branch,
            "phase": "fix", "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        return False
    return True


def _repush_reconciled(
    worktree: Path, branch: str, task_id: str, base_sha: str, remote_sha: str,
    *, forge_target: ForgeTarget,
) -> PushResult:
    if not remote_sha:
        logger.warning("git push (fix repush) refused: empty remote_sha resolve")
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "reason": "empty_remote_sha_resolve",
        })
        return PushResult(outcome=PushOutcome.PUSH_FAILED)
    try:
        result = process_runner.run_subprocess(
            ["git", *secrets_store.GIT_CRED_RESET_ARGS, "push",
             f"--force-with-lease={branch}:{remote_sha}", "-u", "origin", branch],
            cwd=str(worktree), timeout=GH_TIMEOUT_S,
            env=secrets_store.transport_env(forge_target),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("git push (fix repush) raised %s", exc)
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "error": f"repush raised: {exc}",
        })
        return PushResult(outcome=PushOutcome.PUSH_FAILED)
    if result.returncode != 0:
        logger.warning(
            "git push (fix repush) failed (rc=%s): %s", result.returncode, result.stderr,
        )
        append_audit({
            "task_id": task_id, "action": "push_failed", "branch": branch,
            "phase": "fix", "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        classified = _pr_branch.classify_credential_failure(
            token_present=True, returncode=result.returncode, stderr=result.stderr,
        )
        return PushResult(
            outcome=PushOutcome.PUSH_FAILED,
            credential_failure=classified if classified == "expired_token" else None,
        )
    append_audit({
        "task_id": task_id, "action": "push_reconciled", "branch": branch,
        "phase": "fix", "base_sha": base_sha, "remote_sha": remote_sha,
    })
    return PushResult(outcome=PushOutcome.RECONCILED)


def push_fix_branch_with_reconcile(
    worktree: Path, branch: str, task_id: str, base_sha: str,
    *, forge_target: ForgeTarget,
) -> PushResult:
    if not _SHA_RE.match(base_sha):
        logger.warning("push_fix_branch_with_reconcile: non-concrete base_sha %r", base_sha)
        append_audit({
            "task_id": task_id, "action": "push_diverged_escalate", "branch": branch,
            "phase": "fix", "reason": "base_sha_not_concrete", "base_sha": base_sha,
        })
        return PushResult(outcome=PushOutcome.DIVERGED_ESCALATE)
    result = _try_push_with_lease(worktree, branch, task_id, base_sha, forge_target=forge_target)
    if result is not None:
        return result
    append_audit({
        "task_id": task_id, "action": "push_lease_rejected",
        "branch": branch, "phase": "fix", "base_sha": base_sha,
    })
    if not _fetch_for_reconcile(worktree, branch, task_id, forge_target=forge_target):
        return PushResult(outcome=PushOutcome.PUSH_FAILED)
    remote_sha = try_resolve_remote_sha(worktree, branch)
    if not _rebase_onto(worktree, base_sha, task_id, branch):
        return PushResult(outcome=PushOutcome.DIVERGED_ESCALATE)
    return _repush_reconciled(
        worktree, branch, task_id, base_sha, remote_sha, forge_target=forge_target
    )
