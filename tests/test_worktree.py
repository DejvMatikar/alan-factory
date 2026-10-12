from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from alan import worktree as worktree_mod
from alan.slot_pool import SlotPool, set_active_pool
from alan.worktree import (
    WORKTREES_DIR,
    WorktreeError,
    WorktreeHandle,
    captured_commits,
    implementation_worktree,
    is_repo_checkout,
    repo_root,
)


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


def test_implementation_worktree_creates_and_removes(repo: Path) -> None:
    with implementation_worktree("task-x", "inv_001", repo_root=repo) as handle:
        assert isinstance(handle, WorktreeHandle)
        assert handle.base_ref == "origin/main" or len(handle.base_ref) == 40
        assert handle.branch == "teams/task-x-inv_001"
        assert handle.path.is_dir()
        assert handle.slot_index is None
        assert handle.path.parent.name == WORKTREES_DIR
        assert handle.path.name == "teams-task-x-inv_001"
        head = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(handle.path),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == "teams/task-x-inv_001"
    assert not handle.path.exists()


def test_implementation_worktree_cleans_up_on_exception(repo: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with implementation_worktree("t1", "inv_002", repo_root=repo) as handle:
            assert handle.path.exists()
            raise RuntimeError("boom")
    assert not (repo / WORKTREES_DIR / "teams-t1-inv_002").exists()


def test_implementation_worktree_reclaims_stale_path(repo: Path) -> None:
    stale = repo / WORKTREES_DIR / "teams-t1-inv_003"
    stale.mkdir(parents=True)
    (stale / "stale-marker").write_text("stale")
    with implementation_worktree("t1", "inv_003", repo_root=repo) as handle:
        assert not (handle.path / "stale-marker").exists()
        assert (handle.path / "README.md").exists()


def test_implementation_worktree_reuses_branch_after_retry(repo: Path) -> None:
    with implementation_worktree("t1", "inv_011", repo_root=repo):
        pass
    assert (
        subprocess.run(  # noqa: S603,S607
            ["git", "show-ref", "--verify", "refs/heads/teams/t1-inv_011"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    with implementation_worktree("t1", "inv_011", repo_root=repo) as handle:
        assert handle.path.is_dir()


def test_implementation_worktree_rejects_unsafe_task_id(repo: Path) -> None:
    with pytest.raises(WorktreeError, match="unsafe task_id"):
        with implementation_worktree("../etc/passwd", "inv_001", repo_root=repo):
            pass


@pytest.mark.parametrize("bad", ["inv_1", "INV_001", "inv-001", "../inv_001"])
def test_implementation_worktree_rejects_unsafe_inv_id(repo: Path, bad: str) -> None:
    with pytest.raises(WorktreeError, match="unsafe inv_id"):
        with implementation_worktree("t1", bad, repo_root=repo):
            pass


def test_captured_commits_lists_only_new_commits(repo: Path) -> None:
    with implementation_worktree("t1", "inv_004", repo_root=repo) as handle:
        (handle.path / "feature.py").write_text("print('hi')\n")
        _git(handle.path, "add", "feature.py")
        _git(handle.path, "commit", "-q", "-m", "feat: add feature")
        commits = captured_commits(handle)
    assert len(commits) == 1
    assert "feat: add feature" in commits[0]


def test_captured_commits_returns_empty_when_no_new_commits(repo: Path) -> None:
    with implementation_worktree("t1", "inv_005", repo_root=repo) as handle:
        commits = captured_commits(handle)
    assert commits == []


def test_captured_commits_with_local_head_base_ignores_stale_origin_main(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: no remote",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    subprocess.run(  # noqa: S603,S607
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    with implementation_worktree("t1", "inv_013", repo_root=repo) as handle:
        assert handle.base_ref != "origin/main"
        assert len(handle.base_ref) == 40
        (handle.path / "marker.txt").write_text("x\n")
        _git(handle.path, "add", "marker.txt")
        _git(handle.path, "commit", "-q", "-m", "feat: marker")
        commits = captured_commits(handle)
    assert len(commits) == 1
    assert "feat: marker" in commits[0]


def test_resolve_base_ref_raises_when_head_unresolvable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: no remote",
            )
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: bad HEAD",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with pytest.raises(WorktreeError, match="HEAD is not resolvable"):
        with implementation_worktree("t1", "inv_014", repo_root=repo):
            pass


def test_repo_root_raises_outside_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_WORKTREE_REPO_ROOT", raising=False)
    cwd_orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(WorktreeError, match="not inside a git repository"):
            with implementation_worktree("t1", "inv_001"):
                pass
    finally:
        os.chdir(cwd_orig)


def test_implementation_worktree_repo_root_param_beats_env(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_repo = tmp_path / "env-repo"
    _init_repo(env_repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(env_repo))
    with implementation_worktree("t1", "inv_030", repo_root=repo) as handle:
        assert handle.path.parent.parent == repo
    assert not (env_repo / WORKTREES_DIR).exists()


def test_implementation_worktree_env_wins_over_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_repo = tmp_path / "env-repo"
    _init_repo(env_repo)
    monkeypatch.setenv("ALAN_WORKTREE_REPO_ROOT", str(env_repo))
    cwd_orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        with implementation_worktree("t1", "inv_031") as handle:
            assert handle.path.parent.parent == env_repo
    finally:
        os.chdir(cwd_orig)


def test_implementation_worktree_ambient_used_when_param_and_env_absent(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_WORKTREE_REPO_ROOT", raising=False)
    cwd_orig = os.getcwd()
    os.chdir(repo)
    try:
        with implementation_worktree("t1", "inv_032") as handle:
            assert handle.path.parent.parent == repo
    finally:
        os.chdir(cwd_orig)


def test_create_worktree_failure_surfaces_stderr(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    calls: list[list[str]] = []

    def fake_git(args, cwd, *, check=True):
        calls.append(args)
        if args[:2] == ["worktree", "add"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: simulated failure",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with pytest.raises(WorktreeError, match="simulated failure"):
        with implementation_worktree("t1", "inv_006", repo_root=repo):
            pass


def test_resolve_base_falls_back_to_local_head_when_fetch_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    seen_base: list[str] = []

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: no remote",
            )
        if args[:2] == ["worktree", "add"]:
            seen_base.append(args[-1])
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree("t1", "inv_007", repo_root=repo) as handle:
        assert handle.base_ref != "origin/main"
    assert len(seen_base) == 1
    assert len(seen_base[0]) == 40


def test_assert_branch_failure_raises(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git

    def fake_git(args, cwd, *, check=True):
        if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=0,
                stdout="some-other-branch\n",
                stderr="",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with pytest.raises(WorktreeError, match="expected 'teams/"):
        with implementation_worktree("t1", "inv_008", repo_root=repo):
            pass


def test_git_missing_binary_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("no git here")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(WorktreeError, match="git CLI not found"):
        worktree_mod._git(["status"], tmp_path)


def test_git_timeout_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(WorktreeError, match="timed out"):
        worktree_mod._git(["status"], tmp_path)


def test_cleanup_swallows_errors(
    repo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_git = worktree_mod._git

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["worktree", "remove"]:
            raise OSError("disk gone")
        return real_git(args, cwd, check=check)

    caplog.set_level("ERROR")
    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree("t1", "inv_009", repo_root=repo):
        pass
    assert any(
        "worktree cleanup failed" in record.message for record in caplog.records
    )


def test_captured_commits_with_log_failure_returns_empty(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["log", "--oneline"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: no log",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree("t1", "inv_010", repo_root=repo) as handle:
        assert captured_commits(handle) == []


def test_repo_root_default_uses_real_repo() -> None:
    root = worktree_mod._repo_root()
    assert (root / ".git").exists() or root.is_dir()


def test_resolve_base_uses_origin_main_when_fetch_succeeds(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    seen_base: list[str] = []

    def fake_git(args, cwd, *, check=True):
        if args[:2] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args), returncode=0, stdout="", stderr=""
            )
        if args[:2] == ["worktree", "add"]:
            seen_base.append(args[-1])
            args = args[:-1] + ["HEAD"]
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree("t1", "inv_011", repo_root=repo):
        pass
    assert seen_base == ["origin/main"]


def test_repo_root_timeout_raises_worktree_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git rev-parse", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(WorktreeError, match="git rev-parse.*timed out"):
        worktree_mod._repo_root()


def test_git_calledprocesserror_wrapped_as_worktree_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=2, cmd="git status", stderr="fatal: bad state"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(WorktreeError, match="failed.*bad state"):
        worktree_mod._git(["status"], tmp_path, check=True)


def test_bootstrap_python_deps_skips_when_no_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    worktree_mod._bootstrap_python_deps(tmp_path)
    assert called is False


def test_bootstrap_python_deps_invokes_uv_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    captured: dict[str, object] = {}

    def fake(cmd, *, cwd, stdout, stderr, text, timeout, check):  # noqa: ANN001
        del text, check
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake)
    worktree_mod._bootstrap_python_deps(tmp_path)
    assert captured["cmd"] == ["uv", "sync", "--locked", "--extra", "dev"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["timeout"] == worktree_mod._BOOTSTRAP_TIMEOUT_S
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.PIPE


def test_bootstrap_python_deps_warns_when_uv_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')

    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("uv not on PATH")

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", boom)
    worktree_mod._bootstrap_python_deps(tmp_path)
    assert any("uv CLI not found" in r.message for r in caplog.records)


def test_bootstrap_python_deps_warns_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')

    def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="uv sync", timeout=180)

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", boom)
    worktree_mod._bootstrap_python_deps(tmp_path)
    assert any("uv sync timed out" in r.message for r in caplog.records)


def test_existing_branch_uses_provided_branch_name(repo: Path) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "existing-pr"],
        cwd=str(repo), check=True, capture_output=True,
    )
    with implementation_worktree(
        "t1", "inv_020", repo_root=repo, existing_branch="existing-pr"
    ) as handle:
        assert handle.branch == "existing-pr"
        head = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(handle.path),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert head == "existing-pr"


def test_existing_branch_falls_back_to_local_when_fetch_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    fetched: list[list[str]] = []

    def fake_git(args, cwd, *, check=True):
        if args[:3] == ["fetch", "origin", "existing-pr"]:
            fetched.append(list(args))
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: no remote",
            )
        return real_git(args, cwd, check=check)

    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "existing-pr"],
        cwd=str(repo), check=True, capture_output=True,
    )
    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree(
        "t1", "inv_021", repo_root=repo, existing_branch="existing-pr"
    ) as handle:
        assert handle.branch == "existing-pr"
    assert fetched, "branch-specific fetch should have been attempted"


def test_existing_branch_raises_when_head_unresolvable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "existing-pr"],
        cwd=str(repo), check=True, capture_output=True,
    )

    def fake_git(args, cwd, *, check=True):
        if (
            args[:2] == ["rev-parse", "HEAD"]
            and Path(cwd).parent.name == WORKTREES_DIR
        ):
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=128,
                stdout="",
                stderr="fatal: ambiguous",
            )
        return real_git(args, cwd, check=check)

    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with pytest.raises(WorktreeError, match="could not resolve HEAD"):
        with implementation_worktree(
            "t1", "inv_023", repo_root=repo, existing_branch="existing-pr"
        ):
            pass


def test_existing_branch_uses_origin_ref_when_fetch_succeeds(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = worktree_mod._git
    seen_base: list[str] = []

    def fake_git(args, cwd, *, check=True):
        if args[:3] == ["fetch", "origin", "existing-pr"]:
            return subprocess.CompletedProcess(
                args=["git"] + list(args), returncode=0, stdout="", stderr=""
            )
        if args[:2] == ["worktree", "add"]:
            seen_base.append(args[-1])
            args = args[:-1] + ["HEAD"]
        return real_git(args, cwd, check=check)

    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "existing-pr"],
        cwd=str(repo), check=True, capture_output=True,
    )
    monkeypatch.setattr(worktree_mod, "_git", fake_git)
    with implementation_worktree(
        "t1", "inv_022", repo_root=repo, existing_branch="existing-pr"
    ):
        pass
    assert seen_base == ["origin/existing-pr"]


def test_bootstrap_python_deps_warns_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')

    def fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["uv"], returncode=2, stdout="", stderr="resolver failed"
        )

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", fake)
    worktree_mod._bootstrap_python_deps(tmp_path)
    assert any(
        "uv sync exited 2" in r.message and "resolver failed" in r.message
        for r in caplog.records
    )


def test_repo_root_returns_path() -> None:
    root = repo_root()
    assert isinstance(root, Path)
    assert root.is_dir()


def test_implementation_worktree_uses_pool_when_active(
    repo: Path, tmp_path: Path
) -> None:
    pool_root = tmp_path / "slots"
    pool = SlotPool(root=pool_root, size=1, repo_root=str(repo))
    set_active_pool(pool)
    try:
        with implementation_worktree("pool-task", "inv_001", repo_root=repo) as handle:
            assert isinstance(handle, WorktreeHandle)
            assert handle.path.is_dir()
            assert handle.slot_index == 0
            assert handle.branch == "teams/pool-task-inv_001"
            actual_branch = subprocess.run(  # noqa: S603,S607
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(handle.path), capture_output=True, text=True, check=True
            ).stdout.strip()
            assert actual_branch == "teams/pool-task-inv_001"
        assert 0 in pool._free
    finally:
        set_active_pool(None)


def test_implementation_worktree_pool_validates_ids_before_pool_check(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    set_active_pool(pool)
    try:
        with pytest.raises(WorktreeError, match="unsafe task_id"):
            with implementation_worktree("../etc", "inv_001"):
                pass
        with pytest.raises(WorktreeError, match="unsafe inv_id"):
            with implementation_worktree("good-task", "bad"):
                pass
    finally:
        set_active_pool(None)


def test_implementation_worktree_pool_releases_on_exception(
    repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    try:
        with pytest.raises(RuntimeError, match="kaboom"):
            with implementation_worktree("t1", "inv_099", repo_root=repo):
                raise RuntimeError("kaboom")
        assert 0 in pool._free
    finally:
        set_active_pool(None)


def test_implementation_worktree_pool_honors_preferred_slot(
    repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(repo))
    set_active_pool(pool)
    try:
        with implementation_worktree(
            "pool-task", "inv_001", repo_root=repo, preferred_slot=1
        ) as handle:
            assert handle.slot_index == 1
            assert handle.path.name == "slot_1"
    finally:
        set_active_pool(None)


def test_implementation_worktree_pool_existing_branch(
    repo: Path, tmp_path: Path
) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "pool-pr-branch"],
        cwd=str(repo), check=True, capture_output=True
    )
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    try:
        with implementation_worktree(
            "t1", "inv_098", repo_root=repo, existing_branch="pool-pr-branch"
        ) as handle:
            assert handle.branch == "pool-pr-branch"
    finally:
        set_active_pool(None)


def test_worktree_handle_has_preserved_commits_field() -> None:
    h = WorktreeHandle(path=Path("."), base_ref="abc", branch="main")
    assert h.preserved_commits == 0
    assert h.rescued_ref is None


def test_worktree_handle_carries_preserve_info() -> None:
    h = WorktreeHandle(
        path=Path("."), base_ref="abc", branch="main",
        preserved_commits=3, rescued_ref="rescue/t@inv_001",
    )
    assert h.preserved_commits == 3
    assert h.rescued_ref == "rescue/t@inv_001"


def test_implementation_worktree_pool_threads_resume_base_ref(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    captured_resume: list[object] = []
    orig_lease = pool.lease

    def patched_lease(  # type: ignore[no-untyped-def]
        t_id: str, i_id: str, *,
        existing_branch: str | None = None,
        preferred_idx: int | None = None,
        resume_base_ref: str | None = None,
        on_blocked=None,
    ):
        captured_resume.append(resume_base_ref)
        return orig_lease(
            t_id, i_id, existing_branch=existing_branch, preferred_idx=preferred_idx,
            resume_base_ref=resume_base_ref, on_blocked=on_blocked,
        )

    monkeypatch.setattr(pool, "lease", patched_lease)
    try:
        with implementation_worktree(
            "t1", "inv_100", repo_root=repo,
            preferred_slot=0, resume_base_ref="stored_sha",
        ) as _handle:
            pass
    finally:
        set_active_pool(None)

    assert captured_resume == ["stored_sha"]


def test_pool_worktree_populates_preserved_commits_from_pool(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    monkeypatch.setattr(pool, "get_preserve_info", lambda idx: (4, None))
    try:
        with implementation_worktree("t1", "inv_101", repo_root=repo) as handle:
            assert handle.preserved_commits == 4
            assert handle.rescued_ref is None
    finally:
        set_active_pool(None)


def test_pool_worktree_populates_rescued_ref_from_pool(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    monkeypatch.setattr(pool, "get_preserve_info", lambda idx: (0, "rescue/t@inv_001"))
    try:
        with implementation_worktree("t1", "inv_102", repo_root=repo) as handle:
            assert handle.preserved_commits == 0
            assert handle.rescued_ref == "rescue/t@inv_001"
    finally:
        set_active_pool(None)


def test_implementation_worktree_pool_threads_on_blocked(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    set_active_pool(pool)
    captured: list[object] = []
    orig_lease = pool.lease

    def patched_lease(  # type: ignore[no-untyped-def]
        t_id: str, i_id: str, *,
        existing_branch: str | None = None,
        preferred_idx: int | None = None,
        resume_base_ref: str | None = None,
        on_blocked=None,
    ):
        captured.append(on_blocked)
        return orig_lease(
            t_id, i_id, existing_branch=existing_branch,
            preferred_idx=preferred_idx, resume_base_ref=resume_base_ref,
            on_blocked=on_blocked,
        )

    monkeypatch.setattr(pool, "lease", patched_lease)
    sentinel = lambda: None  # noqa: E731
    try:
        with implementation_worktree(
            "t1", "inv_103", repo_root=repo, on_blocked=sentinel
        ):
            pass
    finally:
        set_active_pool(None)

    assert captured == [sentinel]


def test_implementation_worktree_non_pool_ignores_on_blocked(repo: Path) -> None:
    called: list[int] = []
    with implementation_worktree(
        "t1", "inv_104", repo_root=repo, on_blocked=lambda: called.append(1)
    ):
        pass
    assert called == []


def test_is_repo_checkout_true_when_git_is_a_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert is_repo_checkout(repo) is True


def test_is_repo_checkout_true_when_git_is_a_worktree_file(tmp_path: Path) -> None:
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /some/other/path/.git/worktrees/x\n")
    assert is_repo_checkout(repo) is True


def test_is_repo_checkout_false_when_no_git_entry(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_repo_checkout(plain) is False


def test_is_repo_checkout_false_when_path_does_not_exist(tmp_path: Path) -> None:
    assert is_repo_checkout(tmp_path / "nonexistent") is False


def test_is_repo_checkout_false_when_path_is_a_file(tmp_path: Path) -> None:
    f = tmp_path / "just-a-file"
    f.write_text("x")
    assert is_repo_checkout(f) is False
