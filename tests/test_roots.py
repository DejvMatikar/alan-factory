from __future__ import annotations

from pathlib import Path

import pytest

from alan import roots


def test_worktree_repo_root_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(roots.WORKTREE_REPO_ROOT_ENV, raising=False)
    assert roots.worktree_repo_root() is None


def test_worktree_repo_root_empty_string_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(roots.WORKTREE_REPO_ROOT_ENV, "")
    assert roots.worktree_repo_root() is None


def test_worktree_repo_root_set_returns_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(roots.WORKTREE_REPO_ROOT_ENV, str(tmp_path))
    assert roots.worktree_repo_root() == tmp_path


def test_worktree_repo_root_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(roots.WORKTREE_REPO_ROOT_ENV, f"  {tmp_path}  ")
    assert roots.worktree_repo_root() == tmp_path


def test_ci_isolation_roots_includes_worktree_repo_root_env() -> None:
    assert roots.WORKTREE_REPO_ROOT_ENV in roots.CI_ISOLATION_ROOTS


def test_worktree_repo_root_env_ends_in_root_token() -> None:
    assert roots.WORKTREE_REPO_ROOT_ENV.endswith("_ROOT")
