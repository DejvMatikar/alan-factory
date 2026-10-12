from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from alan import manager_summary as ms
from tests.conftest import spawns_real_claude


class _UndecodableArgv0:
    def __fspath__(self) -> str:
        raise ValueError("cannot decode")


def test_forbid_real_claude_spawn_blocks_subprocess_run() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.run(["claude", "-p", "hi"], check=False)


def test_forbid_real_claude_spawn_blocks_subprocess_popen() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.Popen(["claude"])


def test_forbid_real_claude_spawn_blocks_absolute_path() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.run(["/opt/homebrew/bin/claude", "--version"], check=False)


def test_forbid_real_claude_spawn_allows_other_commands() -> None:
    completed = subprocess.run(["true"], check=False)
    assert completed.returncode == 0


def test_forbid_real_claude_spawn_blocks_bytes_argv() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.run([b"claude"], check=False)


def test_forbid_real_claude_spawn_blocks_bytes_command() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.Popen(b"claude")


def test_forbid_real_claude_spawn_blocks_pathlike_argv() -> None:
    with pytest.raises(RuntimeError, match="real 'claude'"):
        subprocess.run([Path("/opt/homebrew/bin/claude")], check=False)


def test_spawns_real_claude_false_for_path_containing_claude_but_not_basename() -> None:
    assert spawns_real_claude(".claude/worktrees/x/.venv/bin/python3") is False


def test_spawns_real_claude_true_for_bare_name() -> None:
    assert spawns_real_claude("claude") is True


def test_spawns_real_claude_true_for_pathlike_bare_name() -> None:
    assert spawns_real_claude(Path("claude")) is True


def test_spawns_real_claude_false_for_pathlike_path_not_basename() -> None:
    assert spawns_real_claude(Path(".claude/worktrees/x/bin/python3")) is False


def test_spawns_real_claude_true_for_bytes_bare_name() -> None:
    assert spawns_real_claude(b"claude") is True


def test_spawns_real_claude_true_for_bytes_absolute_path() -> None:
    assert spawns_real_claude(b"/opt/homebrew/bin/claude") is True


def test_spawns_real_claude_false_for_non_command_input() -> None:
    assert spawns_real_claude(None) is False
    assert spawns_real_claude(0) is False
    assert spawns_real_claude([]) is False


def test_spawns_real_claude_blocks_undecodable_argv0() -> None:
    assert spawns_real_claude(_UndecodableArgv0()) is True


def test_manager_summary_trigger_is_neutralized_by_default(tmp_path: Path) -> None:
    called: list[int] = []
    ms.trigger_manager_summary(
        tmp_path, "ws", "T", "completed", None, lambda: called.append(1)
    )
    assert ms.read_manager_summary(tmp_path) is None
    assert called == []


def test_ambient_gh_credentials_are_absent_at_import_time() -> None:
    assert "GH_TOKEN" not in os.environ
    assert "GITHUB_TOKEN" not in os.environ
