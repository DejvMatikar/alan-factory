from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alan.roots import worktree_repo_root
from alan.slot_pool import SlotPool, get_active_pool

logger = logging.getLogger(__name__)

WORKTREES_DIR = ".worktrees"
_GIT_TIMEOUT_S = 30.0
_BOOTSTRAP_TIMEOUT_S = 180.0
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_INV_ID_RE = re.compile(r"^inv_\d{3}$")


@dataclass(frozen=True)
class WorktreeHandle:
    """Path + branch + base-ref the worktree was cut from.

    The base_ref is the ref the worktree's branch was created at — either
    `origin/main` when `git fetch origin main` succeeded, or `HEAD` (local
    fallback) when the remote was unreachable. Carrying it forward keeps
    `captured_commits` honest: it won't compare against a stale local
    `origin/main` when the fetch never actually ran.

    The branch is the working branch name. For new PRs it is
    `teams/<task_id>-<inv_id>`; for iteration on an existing PR it is the
    PR's head branch (so the new commits land on the same PR).

    `preserved_commits` is non-zero on a pool resume where committed-but-unpushed
    work was found ahead of the stored base_ref_sha and kept in place.
    `rescued_ref` is set when prior commits existed but the subsequent checkout
    failed — the commits were anchored to a rescue ref and the slot was restarted
    cleanly so the work remains recoverable.
    """

    path: Path
    base_ref: str
    branch: str
    slot_index: int | None = None
    preserved_commits: int = 0
    rescued_ref: str | None = None


class WorktreeError(RuntimeError):
    pass


def is_repo_checkout(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


@contextmanager
def _pool_worktree(
    pool: SlotPool,
    task_id: str,
    inv_id: str,
    *,
    existing_branch: str | None,
    preferred_slot: int | None = None,
    resume_base_ref: str | None = None,
    on_blocked: Callable[[], None] | None = None,
) -> Generator[WorktreeHandle, None, None]:
    with pool.lease(
        task_id, inv_id, existing_branch=existing_branch,
        preferred_idx=preferred_slot, resume_base_ref=resume_base_ref,
        on_blocked=on_blocked,
    ) as (path, base_ref, branch, idx):
        n_pres, rescue = pool.get_preserve_info(idx)
        yield WorktreeHandle(
            path=path, base_ref=base_ref, branch=branch, slot_index=idx,
            preserved_commits=n_pres, rescued_ref=rescue,
        )


@contextmanager
def _bare_worktree(
    root: Path, task_id: str, inv_id: str, existing_branch: str | None
) -> Generator[WorktreeHandle, None, None]:
    worktree_path = root / WORKTREES_DIR / f"teams-{task_id}-{inv_id}"
    _prepare_path(root, worktree_path)
    base = _resolve_base_ref(root)
    if existing_branch is None:
        branch = f"teams/{task_id}-{inv_id}"
        start_point = base
    else:
        branch = existing_branch
        start_point = _resolve_existing_branch_ref(root, existing_branch)
    _create_worktree(root, worktree_path, branch, start_point)
    try:
        _assert_branch(worktree_path, branch)
        _bootstrap_python_deps(worktree_path)
        # For an existing branch, captured_commits must only see commits
        # added in THIS iteration — base off the worktree's HEAD sha at
        # creation time, not origin/main (which would surface every prior
        # iteration's commits as if they were new).
        capture_base = (
            _resolve_worktree_head(worktree_path) if existing_branch is not None else base
        )
        yield WorktreeHandle(path=worktree_path, base_ref=capture_base, branch=branch)
    finally:
        _remove_worktree(root, worktree_path)


@contextmanager
def implementation_worktree(
    task_id: str,
    inv_id: str,
    *,
    repo_root: Path | None = None,
    existing_branch: str | None = None,
    preferred_slot: int | None = None,
    resume_base_ref: str | None = None,
    on_blocked: Callable[[], None] | None = None,
) -> Generator[WorktreeHandle, None, None]:
    """Create a fresh git worktree; clean up on exit.

    Layout:  <repo_root>/.worktrees/teams-<task_id>-<inv_id>
    Branch:  `existing_branch` when given (iterating on an open PR), else a
             fresh `teams/<task_id>-<inv_id>` cut from origin/main when
             reachable, otherwise from local HEAD.

    When `existing_branch` is supplied, the worktree fetches that ref from
    origin and resets its local branch to `origin/<existing_branch>`. The
    handle's `base_ref` is set to the checkout SHA of that branch head at
    creation time so `captured_commits` reports only the commits added in
    this run, and the fix push can use it as an explicit lease anchor.

    `on_blocked` is forwarded to `SlotPool.lease()` when a pool is active; it
    is a no-op on the non-pool path (a plain `git worktree add` never blocks
    on another worker's slot).
    """
    _validate_task_id(task_id)
    _validate_inv_id(inv_id)
    pool = get_active_pool()
    if pool is not None:
        with _pool_worktree(
            pool, task_id, inv_id,
            existing_branch=existing_branch, preferred_slot=preferred_slot,
            resume_base_ref=resume_base_ref, on_blocked=on_blocked,
        ) as handle:
            yield handle
        return
    with _bare_worktree(
        repo_root or worktree_repo_root() or _repo_root(), task_id, inv_id, existing_branch
    ) as handle:
        yield handle


def _resolve_worktree_head(worktree_path: Path) -> str:
    result = _git(["rev-parse", "HEAD"], worktree_path, check=False)
    sha = (result.stdout or "").strip()
    if result.returncode != 0 or not sha:
        raise WorktreeError(
            f"could not resolve HEAD of worktree at {worktree_path}"
        )
    return sha


def _resolve_existing_branch_ref(repo_root: Path, branch: str) -> str:
    """Fetch `branch` from origin and return `origin/<branch>` as the
    start point for the worktree. Falls back to the bare branch name if
    the fetch fails (e.g. running against a local-only branch in tests)."""
    result = _git(["fetch", "origin", branch], repo_root, check=False)
    if result.returncode == 0:
        return f"origin/{branch}"
    logger.warning(
        "git fetch origin %s failed (rc=%s); using local branch ref",
        branch, result.returncode,
    )
    return branch


def _validate_task_id(task_id: str) -> None:
    if not _TASK_ID_RE.match(task_id):
        raise WorktreeError(f"unsafe task_id {task_id!r}")


def _validate_inv_id(inv_id: str) -> None:
    if not _INV_ID_RE.match(inv_id):
        raise WorktreeError(f"unsafe inv_id {inv_id!r}")


def _repo_root() -> Path:
    try:
        result = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise WorktreeError("not inside a git repository") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError("git rev-parse --show-toplevel timed out") from exc
    return Path(result.stdout.strip())


def _prepare_path(repo_root: Path, worktree_path: Path) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "prune"], repo_root, check=False)
    if worktree_path.exists():
        logger.info("reclaiming pre-existing worktree %s", worktree_path)
        _force_remove_worktree(repo_root, worktree_path)


def _resolve_base_ref(repo_root: Path) -> str:
    """Return a ref git can resolve to the commit the worktree should branch from.

    Returns `"origin/main"` when `git fetch origin main` succeeds; otherwise
    returns the current HEAD's sha so subsequent commit-range comparisons
    can't be polluted by a stale `origin/main` ref.
    """
    result = _git(
        ["fetch", "origin", "main"], repo_root, check=False
    )
    if result.returncode == 0:
        return "origin/main"
    logger.warning(
        "git fetch origin main failed (rc=%s); falling back to local HEAD sha",
        result.returncode,
    )
    head = _git(["rev-parse", "HEAD"], repo_root, check=False)
    sha = (head.stdout or "").strip()
    if head.returncode != 0 or not sha:
        raise WorktreeError(
            "fetch origin main failed and local HEAD is not resolvable"
        )
    return sha


def _create_worktree(
    repo_root: Path, worktree_path: Path, branch: str, base: str
) -> None:
    # `-B` (force) instead of `-b`: reset the branch ref if it already
    # exists from a prior crashed run whose path was reclaimed.
    result = _git(
        ["worktree", "add", str(worktree_path), "-B", branch, base],
        repo_root,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise WorktreeError(
            f"git worktree add failed (rc={result.returncode}): {stderr}"
        )


def _bootstrap_python_deps(worktree_path: Path) -> None:
    if not (worktree_path / "pyproject.toml").is_file():
        return
    try:
        result = subprocess.run(  # noqa: S603,S607
            ["uv", "sync", "--locked", "--extra", "dev"],
            cwd=str(worktree_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_BOOTSTRAP_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("uv CLI not found; worker will need to bootstrap manually")
        return
    except subprocess.TimeoutExpired:
        logger.warning(
            "uv sync timed out after %ss in %s; worker will need to retry",
            _BOOTSTRAP_TIMEOUT_S, worktree_path,
        )
        return
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        logger.warning(
            "uv sync exited %s in %s; worker will need to bootstrap manually. stderr=%s",
            result.returncode, worktree_path, stderr,
        )


def _assert_branch(worktree_path: Path, expected: str) -> None:
    result = _git(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        worktree_path,
        check=False,
    )
    actual = (result.stdout or "").strip()
    if result.returncode != 0 or actual != expected:
        raise WorktreeError(
            f"worktree {worktree_path} is on branch {actual!r}, "
            f"expected {expected!r}"
        )


def _remove_worktree(repo_root: Path, worktree_path: Path) -> None:
    try:
        _force_remove_worktree(repo_root, worktree_path)
    except Exception:  # noqa: BLE001
        logger.exception("worktree cleanup failed for %s", worktree_path)


def _force_remove_worktree(repo_root: Path, worktree_path: Path) -> None:
    _git(
        ["worktree", "remove", str(worktree_path), "--force"],
        repo_root,
        check=False,
    )
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def _git(
    args: list[str], cwd: Path, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=check,
            env=env,
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {' '.join(args)} timed out") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise WorktreeError(
            f"git {' '.join(args)} failed (rc={exc.returncode}): {stderr}"
        ) from exc


def captured_commits(handle: WorktreeHandle, limit: int = 20) -> list[str]:
    """Return short commit lines (`<sha> <subject>`) made on this worktree.

    Always compares against the handle's `base_ref` — either `origin/main`
    (when fetch succeeded) or the resolved HEAD sha at creation time. A
    stale local `origin/main` ref can't pollute the captured list because
    the fallback path records the actual sha, not the ref name.
    """
    rev_range = f"{handle.base_ref}..HEAD"
    result = _git(
        ["log", "--oneline", f"-{limit}", rev_range],
        handle.path,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def repo_root() -> Path:
    return _repo_root()


__all__ = [
    "WORKTREES_DIR",
    "WorktreeError",
    "WorktreeHandle",
    "captured_commits",
    "implementation_worktree",
    "is_repo_checkout",
    "repo_root",
]
