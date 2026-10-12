from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from alan.slot_pool import resolve_repo_root

_CLEARED_GIT_VARS = (
    "ALAN_SLOT_REPO_ROOT",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


def _isolate_git(monkeypatch: pytest.MonkeyPatch, ceiling: Path) -> None:
    for name in _CLEARED_GIT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))


def _toplevel_returncode() -> int:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
    ).returncode


def test_resolve_repo_root_returns_the_top_level_of_the_enclosing_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = tmp_path.resolve()
    _isolate_git(monkeypatch, base)
    outside = base / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert _toplevel_returncode() != 0
    repo = base / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    monkeypatch.chdir(repo / "sub")
    result = resolve_repo_root()
    assert result is not None
    assert Path(result).resolve() == repo.resolve()
