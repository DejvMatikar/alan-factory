from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

TARGET_REF = "refs/deploy/target"
CLEAN_EXCLUDES: tuple[str, ...] = ("alan/web/node_modules", ".venv", "alan/web/dist*")

_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_GIT_TIMEOUT_S = 300.0


class ShallowRepositoryError(RuntimeError):
    pass


class PushedShaAbsentError(RuntimeError):
    def __init__(self, pushed_sha: str) -> None:
        super().__init__(f"pushed commit not present in repository: {pushed_sha}")
        self.pushed_sha = pushed_sha


class NotContainedError(RuntimeError):
    def __init__(self, pushed_sha: str, target_sha: str) -> None:
        super().__init__(
            f"target commit {target_sha} does not contain pushed commit {pushed_sha}"
        )
        self.pushed_sha = pushed_sha
        self.target_sha = target_sha


def _git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=check,
        env=env,
    )


def assert_not_shallow(repo: Path) -> None:
    result = _git(repo, ["rev-parse", "--is-shallow-repository"])
    if result.stdout.strip() == "true":
        raise ShallowRepositoryError(f"repository at {repo} is a shallow clone")


def fetch_target(repo: Path) -> str:
    # refs/deploy/target is private only because ci-deploy.yml's
    # concurrency: group: ci-deploy serialises deploy runs.
    # --no-write-fetch-head keeps this fetch from also appending to
    # $GIT_DIR/FETCH_HEAD, a plain file the live service's own
    # concurrent fetches append to as well; nothing here ever reads
    # FETCH_HEAD back, so there is no reason to write it at all.
    _git(
        repo,
        [
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "--refmap=",
            "origin",
            f"+refs/heads/main:{TARGET_REF}",
        ],
    )
    return _git(repo, ["rev-parse", TARGET_REF]).stdout.strip()


def _pushed_sha_present(repo: Path, pushed_sha: str) -> bool:
    if not _SHA_RE.fullmatch(pushed_sha):
        return False
    result = _git(repo, ["cat-file", "-e", f"{pushed_sha}^{{commit}}"], check=False)
    return result.returncode == 0


def assert_ancestry(repo: Path, pushed_sha: str, target_sha: str) -> None:
    if not _pushed_sha_present(repo, pushed_sha):
        raise PushedShaAbsentError(pushed_sha)
    result = _git(repo, ["merge-base", "--is-ancestor", pushed_sha, target_sha], check=False)
    if result.returncode != 0:
        raise NotContainedError(pushed_sha, target_sha)


def _mutate(repo: Path, target_sha: str) -> None:
    _git(repo, ["update-index", "--no-skip-worktree", "deploy/ai.alan.plist"], check=False)
    _git(repo, ["checkout", "-fB", "main", target_sha])
    _git(repo, ["reset", "--hard", target_sha])
    clean_args = ["clean", "-fdx"]
    for excluded in CLEAN_EXCLUDES:
        clean_args += ["-e", excluded]
    _git(repo, clean_args)


def refresh(repo_dir: Path, pushed_sha: str) -> str:
    assert_not_shallow(repo_dir)
    target_sha = fetch_target(repo_dir)
    assert_ancestry(repo_dir, pushed_sha, target_sha)
    _mutate(repo_dir, target_sha)
    return target_sha


__all__ = [
    "TARGET_REF",
    "CLEAN_EXCLUDES",
    "ShallowRepositoryError",
    "PushedShaAbsentError",
    "NotContainedError",
    "assert_not_shallow",
    "fetch_target",
    "assert_ancestry",
    "refresh",
]
