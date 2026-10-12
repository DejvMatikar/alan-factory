from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum

from alan import process_runner, secrets_store
from alan.engine_config import ForgeTarget
from alan.forge import active_forge
from alan.teams import _pr_audit_log, _pr_push
from alan.teams._pr_forge import GH_TIMEOUT_S

_BASE_REF = "refs/alan-reconcile/base"
_HEAD_REF = "refs/alan-reconcile/head"


class ReconcileOutcome(str, Enum):
    RECONCILED = "reconciled"  # noqa: S105  # nosec B105
    ALREADY_CURRENT = "already_current"
    CONFLICT = "conflict"
    RACED = "raced"
    TRANSPORT_FAILED = "transport_failed"  # noqa: S105  # nosec B105


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    base_sha: str | None = None
    pre_sha: str | None = None
    post_sha: str | None = None
    conflicted_paths: tuple[str, ...] = ()
    behind_commits: tuple[str, ...] = ()


def _run(
    args: list[str], cwd: str | None, *, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return process_runner.run_subprocess(args, cwd=cwd, timeout=GH_TIMEOUT_S, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _ok(result: subprocess.CompletedProcess[str] | None) -> bool:
    return result is not None and result.returncode == 0


def _rev_parse(d: str, ref: str) -> str | None:
    result = _run(["git", "rev-parse", ref], cwd=d)
    if not _ok(result):
        return None
    sha = ((result.stdout if result else "") or "").strip()
    return sha or None


def _valid_ref(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _resolve_head_base(
    pr_number: int, forge_target: ForgeTarget,
) -> tuple[str, str] | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("headRefName", "baseRefName"),
            forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not read.ok:
        return None
    head = _valid_ref(read.fields.get("headRefName"))
    base = _valid_ref(read.fields.get("baseRefName"))
    if head is None or base is None:
        return None
    return head, base


def _init_remote(d: str, forge_target: ForgeTarget) -> bool:
    if not _ok(_run(["git", "init", "-q"], cwd=d)):
        return False
    remote_url = f"https://github.com/{forge_target.repo.slug}.git"
    return _ok(_run(["git", "remote", "add", "origin", remote_url], cwd=d))


def _fetch_both(
    d: str, base: str, head: str, forge_target: ForgeTarget,
) -> tuple[str, str] | None:
    try:
        env = secrets_store.transport_env(forge_target)
    except secrets_store.MissingSecretError:
        return None
    result = _run(
        [
            "git", *secrets_store.GIT_CRED_RESET_ARGS, "fetch", "origin",
            f"{base}:{_BASE_REF}", f"{head}:{_HEAD_REF}",
        ],
        cwd=d, env=env,
    )
    if not _ok(result):
        return None
    base_sha = _rev_parse(d, _BASE_REF)
    pre_sha = _rev_parse(d, _HEAD_REF)
    if base_sha is None or pre_sha is None:
        return None
    return base_sha, pre_sha


def _already_ancestor(d: str, base_sha: str, pre_sha: str) -> bool:
    return _ok(_run(["git", "merge-base", "--is-ancestor", base_sha, pre_sha], cwd=d))


def _unmerged_paths(d: str) -> tuple[str, ...]:
    result = _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=d)
    if not _ok(result):
        return ()
    stdout = ((result.stdout if result else "") or "")
    return tuple(line.strip() for line in stdout.splitlines() if line.strip())


def _behind_commits(d: str, pre_sha: str, base_sha: str) -> tuple[str, ...]:
    result = _run(["git", "log", "--oneline", f"{pre_sha}..{base_sha}"], cwd=d)
    if not _ok(result):
        return ()
    stdout = ((result.stdout if result else "") or "")
    return tuple(line.strip() for line in stdout.splitlines() if line.strip())


def _checkout_and_rebase(
    d: str, head: str, base_sha: str, pre_sha: str,
) -> tuple[ReconcileOutcome | None, tuple[str, ...], tuple[str, ...]]:
    if not _ok(_run(["git", "checkout", "-B", head, pre_sha], cwd=d)):
        return ReconcileOutcome.TRANSPORT_FAILED, (), ()
    if _ok(_run(["git", "rebase", base_sha], cwd=d)):
        return None, (), ()
    paths = _unmerged_paths(d)
    behind = _behind_commits(d, pre_sha, base_sha)
    outcome = ReconcileOutcome.CONFLICT if paths else ReconcileOutcome.TRANSPORT_FAILED
    _run(["git", "rebase", "--abort"], cwd=d)
    return outcome, paths, behind


def _push_reconciled(
    d: str, head: str, pre_sha: str, forge_target: ForgeTarget,
) -> ReconcileOutcome:
    try:
        env = secrets_store.transport_env(forge_target)
    except secrets_store.MissingSecretError:
        return ReconcileOutcome.TRANSPORT_FAILED
    result = _run(
        [
            "git", *secrets_store.GIT_CRED_RESET_ARGS, "push",
            f"--force-with-lease={head}:{pre_sha}", "-u", "origin", head,
        ],
        cwd=d, env=env,
    )
    if _ok(result):
        return ReconcileOutcome.RECONCILED
    if result is not None and _pr_push._is_lease_rejection(result.stderr or ""):  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        return ReconcileOutcome.RACED
    return ReconcileOutcome.TRANSPORT_FAILED


def _rebase_and_push(
    d: str, head: str, base_sha: str, pre_sha: str, forge_target: ForgeTarget,
) -> ReconcileResult:
    failure, conflicted_paths, behind_commits = _checkout_and_rebase(d, head, base_sha, pre_sha)
    if failure is not None:
        return ReconcileResult(
            failure, base_sha, pre_sha,
            conflicted_paths=conflicted_paths, behind_commits=behind_commits,
        )
    post_sha = _rev_parse(d, "HEAD")
    if post_sha is None:
        return ReconcileResult(ReconcileOutcome.TRANSPORT_FAILED, base_sha, pre_sha)
    if post_sha == pre_sha:
        return ReconcileResult(ReconcileOutcome.ALREADY_CURRENT, base_sha, pre_sha, post_sha)
    outcome = _push_reconciled(d, head, pre_sha, forge_target)
    return ReconcileResult(outcome, base_sha, pre_sha, post_sha)


def _reconcile_in(
    d: str, head: str, base: str, forge_target: ForgeTarget,
) -> ReconcileResult:
    if not _init_remote(d, forge_target):
        return ReconcileResult(ReconcileOutcome.TRANSPORT_FAILED)
    fetched = _fetch_both(d, base, head, forge_target)
    if fetched is None:
        return ReconcileResult(ReconcileOutcome.TRANSPORT_FAILED)
    base_sha, pre_sha = fetched
    if _already_ancestor(d, base_sha, pre_sha):
        return ReconcileResult(ReconcileOutcome.ALREADY_CURRENT, base_sha, pre_sha, pre_sha)
    return _rebase_and_push(d, head, base_sha, pre_sha, forge_target)


def _finish(task_id: str, pr_number: int, result: ReconcileResult) -> ReconcileResult:
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "pr_reconcile_attempt",
        "pr_number": pr_number,
        "outcome": result.outcome.value,
        "base_sha": result.base_sha,
        "pre_sha": result.pre_sha,
        "post_sha": result.post_sha,
        "behind_commits": list(result.behind_commits),
    })
    return result


def reconcile_branch(
    pr_number: int, task_id: str, *,
    forge_target: ForgeTarget,
) -> ReconcileResult:
    resolved = _resolve_head_base(pr_number, forge_target)
    if resolved is None:
        return _finish(task_id, pr_number, ReconcileResult(ReconcileOutcome.TRANSPORT_FAILED))
    head, base = resolved
    d = tempfile.mkdtemp(prefix="alan-reconcile-")
    try:
        result = _reconcile_in(d, head, base, forge_target)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return _finish(task_id, pr_number, result)
