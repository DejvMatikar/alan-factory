"""Snapshot-run-resnapshot wrapper that fails a command which mutates the
repo's branch refs, origin/main, or the .worktrees/ registry as a side
effect, instead of merely asserting the command was invoked a certain way."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT_S = 30.0
_WORKTREES_DIRNAME = ".worktrees"
_REF_PATTERNS: tuple[str, ...] = (
    "refs/heads/teams/e2e-impl-*",
    "refs/heads/teams/e2e-wf-*",
    "refs/heads/e2e-stub-pr-branch*",
    "refs/remotes/origin/main",
)
_USAGE_ERROR_RC = 2
_COMMAND_NOT_FOUND_RC = 127


@dataclass(frozen=True)
class GitStateSnapshot:
    refs: dict[str, str]
    worktree_paths: frozenset[str]
    worktrees_dir_entries: frozenset[str]


def _repo_root() -> Path:
    result = subprocess.run(  # noqa: S603,S607
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=True,
    )
    return Path(result.stdout.strip())


def _scoped_refs(repo_root: Path) -> dict[str, str]:
    result = subprocess.run(  # noqa: S603,S607
        ["git", "for-each-ref", "--format=%(refname) %(objectname)", *_REF_PATTERNS],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if result.returncode != 0:
        return {}
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        name, _, sha = stripped.rpartition(" ")
        if name:
            refs[name] = sha
    return refs


def _worktree_paths(repo_root: Path) -> frozenset[str]:
    result = subprocess.run(  # noqa: S603,S607
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if result.returncode != 0:
        return frozenset()
    scoped_root = str(repo_root / _WORKTREES_DIRNAME)
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        path = line.removeprefix("worktree ").strip()
        if path == scoped_root or path.startswith(f"{scoped_root}/"):
            paths.add(path)
    return frozenset(paths)


def _worktrees_dir_entries(repo_root: Path) -> frozenset[str]:
    worktrees_dir = repo_root / _WORKTREES_DIRNAME
    if not worktrees_dir.is_dir():
        return frozenset()
    return frozenset(entry.name for entry in worktrees_dir.iterdir())


def snapshot(repo_root: Path) -> GitStateSnapshot:
    return GitStateSnapshot(
        refs=_scoped_refs(repo_root),
        worktree_paths=_worktree_paths(repo_root),
        worktrees_dir_entries=_worktrees_dir_entries(repo_root),
    )


def _ref_violations(before: GitStateSnapshot, after: GitStateSnapshot) -> list[str]:
    violations: list[str] = []
    for name in sorted(after.refs.keys() - before.refs.keys()):
        violations.append(f"ref created: {name} -> {after.refs[name]}")
    for name in sorted(before.refs.keys() - after.refs.keys()):
        violations.append(f"ref removed: {name} (was {before.refs[name]})")
    for name in sorted(before.refs.keys() & after.refs.keys()):
        if before.refs[name] != after.refs[name]:
            violations.append(
                f"ref moved: {name} {before.refs[name]} -> {after.refs[name]}"
            )
    return violations


def _worktree_violations(before: GitStateSnapshot, after: GitStateSnapshot) -> list[str]:
    violations: list[str] = []
    for path in sorted(after.worktree_paths - before.worktree_paths):
        violations.append(f"worktree registered: {path}")
    for path in sorted(before.worktree_paths - after.worktree_paths):
        violations.append(f"worktree deregistered: {path}")
    for entry in sorted(after.worktrees_dir_entries - before.worktrees_dir_entries):
        violations.append(f".worktrees/ entry created: {entry}")
    for entry in sorted(before.worktrees_dir_entries - after.worktrees_dir_entries):
        violations.append(f".worktrees/ entry removed: {entry}")
    return violations


def diff(before: GitStateSnapshot, after: GitStateSnapshot) -> list[str]:
    return _ref_violations(before, after) + _worktree_violations(before, after)


def _split_command(argv: list[str]) -> list[str] | None:
    if "--" not in argv:
        return None
    cmd = argv[argv.index("--") + 1 :]
    return cmd or None


def main(argv: list[str]) -> int:
    cmd = _split_command(argv)
    if cmd is None:
        print("usage: git_state_gate.py -- <command...>", file=sys.stderr)
        return _USAGE_ERROR_RC
    repo_root = _repo_root()
    before = snapshot(repo_root)
    try:
        wrapped = subprocess.run(cmd, check=False)  # noqa: S603
    except FileNotFoundError as exc:
        print(f"git_state_gate: command not found: {exc}", file=sys.stderr)
        return _COMMAND_NOT_FOUND_RC
    after = snapshot(repo_root)
    violations = diff(before, after)
    for violation in violations:
        print(f"git_state_gate: VIOLATION: {violation}", file=sys.stderr)
    if violations:
        return 1
    return wrapped.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
