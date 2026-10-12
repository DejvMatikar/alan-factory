from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import git_state_gate


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


def test_scoped_refs_includes_matching_branches_and_origin_main(repo: Path) -> None:
    _git(repo, "branch", "teams/e2e-impl-1")
    _git(repo, "branch", "teams/e2e-wf-2")
    _git(repo, "branch", "e2e-stub-pr-branch-w0")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    refs = git_state_gate._scoped_refs(repo)  # noqa: SLF001
    assert "refs/heads/teams/e2e-impl-1" in refs
    assert "refs/heads/teams/e2e-wf-2" in refs
    assert "refs/heads/e2e-stub-pr-branch-w0" in refs
    assert "refs/remotes/origin/main" in refs


def test_scoped_refs_excludes_unrelated_branch(repo: Path) -> None:
    _git(repo, "branch", "some-other-branch")
    refs = git_state_gate._scoped_refs(repo)  # noqa: SLF001
    assert "refs/heads/some-other-branch" not in refs


def test_worktree_paths_includes_entries_under_worktrees_dir(repo: Path) -> None:
    wt = repo / ".worktrees" / "teams-x-inv_001"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", str(wt), "-b", "teams/x-inv_001")
    paths = git_state_gate._worktree_paths(repo)  # noqa: SLF001
    assert str(wt) in paths


def test_worktree_paths_excludes_worktree_outside_worktrees_dir(
    repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-worktree"
    _git(repo, "worktree", "add", str(outside), "-b", "outside-branch")
    paths = git_state_gate._worktree_paths(repo)  # noqa: SLF001
    assert str(outside) not in paths


def test_worktrees_dir_entries_lists_directory_contents(repo: Path) -> None:
    worktrees = repo / ".worktrees"
    worktrees.mkdir()
    (worktrees / "some-dir").mkdir()
    entries = git_state_gate._worktrees_dir_entries(repo)  # noqa: SLF001
    assert entries == frozenset({"some-dir"})


def test_worktrees_dir_entries_empty_when_dir_absent(repo: Path) -> None:
    assert git_state_gate._worktrees_dir_entries(repo) == frozenset()  # noqa: SLF001


def test_diff_empty_when_nothing_changed(repo: Path) -> None:
    before = git_state_gate.snapshot(repo)
    after = git_state_gate.snapshot(repo)
    assert git_state_gate.diff(before, after) == []


def test_diff_flags_new_branch(repo: Path) -> None:
    before = git_state_gate.snapshot(repo)
    _git(repo, "branch", "teams/e2e-impl-new")
    after = git_state_gate.snapshot(repo)
    violations = git_state_gate.diff(before, after)
    assert any("teams/e2e-impl-new" in v for v in violations)


def test_diff_flags_new_worktree(repo: Path) -> None:
    before = git_state_gate.snapshot(repo)
    wt = repo / ".worktrees" / "teams-y-inv_002"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", str(wt), "-b", "teams/y-inv_002")
    after = git_state_gate.snapshot(repo)
    violations = git_state_gate.diff(before, after)
    assert any(str(wt) in v for v in violations)


def test_diff_ignores_unrelated_branch(repo: Path) -> None:
    before = git_state_gate.snapshot(repo)
    _git(repo, "branch", "some-other-branch")
    after = git_state_gate.snapshot(repo)
    assert git_state_gate.diff(before, after) == []


def test_diff_flags_moved_ref() -> None:
    before = git_state_gate.GitStateSnapshot(
        refs={"refs/remotes/origin/main": "a" * 40},
        worktree_paths=frozenset(),
        worktrees_dir_entries=frozenset(),
    )
    after = git_state_gate.GitStateSnapshot(
        refs={"refs/remotes/origin/main": "b" * 40},
        worktree_paths=frozenset(),
        worktrees_dir_entries=frozenset(),
    )
    violations = git_state_gate.diff(before, after)
    assert any("ref moved" in v for v in violations)


def test_diff_flags_removed_ref_and_removed_worktree_entry() -> None:
    before = git_state_gate.GitStateSnapshot(
        refs={"refs/heads/teams/e2e-impl-gone": "a" * 40},
        worktree_paths=frozenset({"/repo/.worktrees/gone"}),
        worktrees_dir_entries=frozenset({"gone"}),
    )
    after = git_state_gate.GitStateSnapshot(
        refs={}, worktree_paths=frozenset(), worktrees_dir_entries=frozenset(),
    )
    violations = git_state_gate.diff(before, after)
    assert any("ref removed" in v for v in violations)
    assert any("worktree deregistered" in v for v in violations)
    assert any(".worktrees/ entry removed" in v for v in violations)


def test_main_requires_double_dash() -> None:
    assert git_state_gate.main(["python3"]) == 2


def test_main_requires_command_after_double_dash() -> None:
    assert git_state_gate.main(["--"]) == 2


def test_main_clean_pass_through(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(git_state_gate, "_repo_root", lambda: repo)
    rc = git_state_gate.main(["--", "python3", "-c", "import sys; sys.exit(0)"])
    assert rc == 0


def test_main_wrapped_failure_propagates(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(git_state_gate, "_repo_root", lambda: repo)
    rc = git_state_gate.main(["--", "python3", "-c", "import sys; sys.exit(7)"])
    assert rc == 7


def test_main_violation_flips_green_to_red(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(git_state_gate, "_repo_root", lambda: repo)
    script = (
        "import subprocess, sys\n"
        "subprocess.run(['git', 'branch', 'teams/e2e-impl-flip'], "
        "cwd=sys.argv[1], check=True)\n"
        "sys.exit(0)\n"
    )
    rc = git_state_gate.main(["--", "python3", "-c", script, str(repo)])
    assert rc == 1


def test_main_command_not_found(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(git_state_gate, "_repo_root", lambda: repo)
    rc = git_state_gate.main(["--", "definitely-not-a-real-command-xyz"])
    assert rc == 127
