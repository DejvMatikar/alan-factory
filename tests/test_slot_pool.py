from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from alan import secrets_store
from alan import slot_pool as sp
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity, resolve_cache_env
from alan.slot_pool import (
    SlotPool,
    get_active_pool,
    is_remote_url,
    resolve_repo_root,
    set_active_pool,
)

_FAKE_TOKEN = "test-transport-token"  # noqa: S105  # nosec B105


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", _FAKE_TOKEN)


def _git(path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", *args],
        cwd=str(path), check=True, capture_output=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


@pytest.fixture
def pool_root(tmp_path: Path) -> Path:
    return tmp_path / "slots"


def test_resolve_repo_root_uses_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", str(tmp_path))
    assert resolve_repo_root() == str(tmp_path)


def test_resolve_repo_root_strips_whitespace_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", f"  {tmp_path}  ")
    assert resolve_repo_root() == str(tmp_path)


def test_resolve_repo_root_returns_url_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/DejvMatikar/alan.git"
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", url)
    assert resolve_repo_root() == url


def test_resolve_repo_root_returns_none_when_git_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SLOT_REPO_ROOT", raising=False)

    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        return subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_repo_root() is None


def test_resolve_repo_root_returns_none_on_file_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SLOT_REPO_ROOT", raising=False)

    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        raise FileNotFoundError("no git")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_repo_root() is None


def test_resolve_repo_root_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_SLOT_REPO_ROOT", raising=False)

    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_repo_root() is None


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/DejvMatikar/alan.git",
        "http://example.com/repo.git",
        "git://example.com/repo.git",
        "ssh://git@github.com/DejvMatikar/alan.git",
        "git@github.com:DejvMatikar/alan.git",
    ],
)
def test_is_remote_url_true_for_remote(value: str) -> None:
    assert is_remote_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "/srv/checkouts/alan",
        "relative/path",
        "",
        "file:///srv/checkouts/alan",
    ],
)
def test_is_remote_url_false_for_local(value: str) -> None:
    assert is_remote_url(value) is False


def test_get_active_pool_returns_none_by_default() -> None:
    assert get_active_pool() is None


def test_set_and_get_active_pool(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    set_active_pool(pool)
    try:
        assert get_active_pool() is pool
    finally:
        set_active_pool(None)


def test_set_active_pool_to_none_clears_it(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    set_active_pool(pool)
    set_active_pool(None)
    assert get_active_pool() is None


def test_active_pool_is_thread_local(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    set_active_pool(pool)
    results: list[object] = []

    def worker() -> None:
        results.append(get_active_pool())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert results[0] is None
    set_active_pool(None)


def test_slot_pool_slot_path(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=3, repo_root=str(tmp_path))
    assert pool.slot_path(0) == tmp_path / "slots" / "slot_0"
    assert pool.slot_path(2) == tmp_path / "slots" / "slot_2"


def test_slot_pool_rejects_zero_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        SlotPool(root=tmp_path / "slots", size=0, repo_root=str(tmp_path))


def test_slot_pool_rejects_negative_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        SlotPool(root=tmp_path / "slots", size=-1, repo_root=str(tmp_path))


def test_slot_pool_cache_env_defaults_to_engine_default(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    assert pool._cache_env == dict(_ENGINE_DEFAULT.cache_env)  # noqa: SLF001


def test_slot_pool_cache_env_honors_explicit_override(tmp_path: Path) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path),
        cache_env={"NUGET_PACKAGES": "nuget"},
    )
    assert pool._cache_env == {"NUGET_PACKAGES": "nuget"}  # noqa: SLF001


def test_slot_pool_cache_env_explicit_empty_dict_disables_default(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), cache_env={})
    assert pool._cache_env == {}  # noqa: SLF001


def test_reset_all_skips_non_clone(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    (tmp_path / "slots").mkdir(parents=True)
    pool.reset_all()
    assert pool._free == {0, 1}


def test_reset_all_cleans_existing_clone(tmp_path: Path, repo: Path) -> None:
    pool_root = tmp_path / "slots"
    pool = SlotPool(root=pool_root, size=1, repo_root=str(repo))
    slot = pool.slot_path(0)
    slot.mkdir(parents=True)
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)],
        capture_output=True, check=True
    )
    dirty = slot / "dirty.txt"
    dirty.write_text("oops\n")
    pool.reset_all()
    assert not dirty.exists()
    assert pool._free == {0}


def test_is_git_clone_true(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert sp.is_git_clone(tmp_path) is True


def test_is_git_clone_false(tmp_path: Path) -> None:
    assert sp.is_git_clone(tmp_path) is False


def test_is_git_clone_rejects_worktree_git_file(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: ../.git/worktrees/foo\n")
    assert sp.is_git_clone(tmp_path) is False


def test_run_git_propagates_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, capture_output, text, timeout, check, env):  # noqa: ANN001
        del cwd, capture_output, text, timeout, check
        captured["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_git(["status"], tmp_path)
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_clean_slot_calls_reset_hard_and_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=["git"] + args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    sp._clean_slot(tmp_path)
    assert calls[0] == ["reset", "--hard"]
    assert calls[1] == ["clean", "-fd"]


def test_clean_slot_discards_staged_tracked_changes(tmp_path: Path, repo: Path) -> None:
    slot = tmp_path / "slot"
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)], capture_output=True, check=True
    )
    (slot / "README.md").write_text("staged-local\n")
    _git(slot, "add", "README.md")
    (slot / "untracked.txt").write_text("junk\n")

    sp._clean_slot(slot)

    status = subprocess.run(  # noqa: S603,S607
        ["git", "status", "--porcelain"], cwd=str(slot), capture_output=True, text=True, check=True
    )
    assert status.stdout == ""
    assert (slot / "README.md").read_text() == "hi\n"
    assert not (slot / "untracked.txt").exists()


def test_clean_slot_preserves_gitignored_dotnet_build_outputs(
    tmp_path: Path, repo: Path
) -> None:
    slot = tmp_path / "slot"
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)], capture_output=True, check=True
    )
    (slot / ".gitignore").write_text("bin/\nobj/\n")
    _git(slot, "add", ".gitignore")
    _git(slot, "commit", "-q", "-m", "add gitignore")
    (slot / "bin").mkdir()
    (slot / "bin" / "app.dll").write_text("compiled\n")
    (slot / "obj").mkdir()
    (slot / "obj" / "app.cache").write_text("build cache\n")
    (slot / "stray.txt").write_text("untracked, not gitignored\n")

    sp._clean_slot(slot)

    assert (slot / "bin" / "app.dll").exists()
    assert (slot / "obj" / "app.cache").exists()
    assert not (slot / "stray.txt").exists()


def test_clean_slot_preserves_dotnet_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    nuget_cache = Path(resolve_cache_env({"NUGET_PACKAGES": "nuget"})["NUGET_PACKAGES"])
    nuget_cache.mkdir(parents=True)
    cached_pkg = nuget_cache / "somepkg.1.0.0.nupkg"
    cached_pkg.write_text("cached package\n")

    slot = tmp_path / "slot"
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)], capture_output=True, check=True
    )

    sp._clean_slot(slot)

    assert cached_pkg.exists()


def test_prepare_existing_branch_survives_staged_leftovers(tmp_path: Path, repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "pr-branch")
    (repo / "README.md").write_text("pr-version\n")
    _git(repo, "commit", "-q", "-am", "pr change")
    _git(repo, "checkout", "-q", "main")

    slot = tmp_path / "slot"
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)], capture_output=True, check=True
    )
    (slot / "README.md").write_text("killed-worker-edit\n")
    _git(slot, "add", "README.md")

    _base, branch = sp._prepare_existing_branch(slot, "pr-branch", DEFAULT_FORGE_TARGET)

    assert branch == "pr-branch"
    assert (slot / "README.md").read_text() == "pr-version\n"


def test_fetch_base_returns_origin_main_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["git"] + args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._fetch_base(tmp_path, DEFAULT_FORGE_TARGET) == "origin/main"


def test_fetch_base_falls_back_to_head_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = "a" * 40

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["fetch", "origin", "main"]:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=sha, stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._fetch_base(tmp_path, DEFAULT_FORGE_TARGET) == sha


def test_fetch_base_falls_back_to_string_when_head_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    caplog.set_level("WARNING")
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    result = sp._fetch_base(tmp_path, DEFAULT_FORGE_TARGET)
    assert result == "origin/main"
    assert any("unresolvable" in r.message for r in caplog.records)


def test_fetch_existing_branch_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    result = sp._fetch_existing_branch(tmp_path, "my-branch", DEFAULT_FORGE_TARGET)
    assert result == "origin/my-branch"


def test_fetch_existing_branch_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    caplog.set_level("WARNING")
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._fetch_existing_branch(tmp_path, "my-branch", DEFAULT_FORGE_TARGET) == "my-branch"
    assert any("using local ref" in r.message for r in caplog.records)


def test_head_sha_returns_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = "b" * 40

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=sha + "\n", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._head_sha(tmp_path) == sha


def test_head_sha_returns_head_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._head_sha(tmp_path) == "HEAD"


def test_prepare_new_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    fake_sha = "a" * 40

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_fetch_base", lambda p, _ft: "origin/main")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    monkeypatch.setattr(sp, "_head_sha", lambda p: fake_sha)
    base, branch = sp._prepare_new_branch(tmp_path, "task1", "inv_001", DEFAULT_FORGE_TARGET)
    assert base == fake_sha
    assert branch == "teams/task1-inv_001"
    reset_call = next(c for c in calls if c[:2] == ["reset", "--hard"])
    assert reset_call[2] == "origin/main"


def test_prepare_new_branch_raises_on_reset_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["reset", "--hard"]:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="reset err")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_fetch_base", lambda p, _ft: "origin/main")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    with pytest.raises(RuntimeError, match="git reset"):
        sp._prepare_new_branch(tmp_path, "task1", "inv_001", DEFAULT_FORGE_TARGET)


def test_prepare_new_branch_raises_on_checkout_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["checkout", "-B"]:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="co err")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_fetch_base", lambda p, _ft: "origin/main")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    with pytest.raises(RuntimeError, match="git checkout"):
        sp._prepare_new_branch(tmp_path, "task1", "inv_001", DEFAULT_FORGE_TARGET)


def test_prepare_existing_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = "c" * 40
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: f"origin/{b}")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    def _ok(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", _ok)
    monkeypatch.setattr(sp, "_head_sha", lambda p: sha)
    base, branch = sp._prepare_existing_branch(tmp_path, "pr-branch", DEFAULT_FORGE_TARGET)
    assert base == sha
    assert branch == "pr-branch"


def test_prepare_existing_branch_raises_on_checkout_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="co err")

    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: f"origin/{b}")
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)
    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    with pytest.raises(RuntimeError, match="git checkout"):
        sp._prepare_existing_branch(tmp_path, "pr-branch", DEFAULT_FORGE_TARGET)


def test_warmup_command_env_uv_sets_uv_cache_dir(tmp_path: Path) -> None:
    env = sp._warmup_command_env({"UV_CACHE_DIR": "uv"}, DEFAULT_FORGE_TARGET)
    assert "UV_CACHE_DIR" in env


def test_warmup_command_env_npm_sets_npm_cache(tmp_path: Path) -> None:
    env = sp._warmup_command_env({"npm_config_cache": "npm"}, DEFAULT_FORGE_TARGET)
    assert "npm_config_cache" in env


def test_warmup_command_env_dotnet_sets_nuget_packages(tmp_path: Path) -> None:
    env = sp._warmup_command_env({"NUGET_PACKAGES": "nuget"}, DEFAULT_FORGE_TARGET)
    assert "NUGET_PACKAGES" in env


def test_warmup_command_env_empty_cache_env_returns_plain_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("npm_config_cache", raising=False)
    env = sp._warmup_command_env({}, DEFAULT_FORGE_TARGET)
    assert "UV_CACHE_DIR" not in env
    assert "npm_config_cache" not in env


def test_run_warmup_command_noop_on_empty_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, [], {}, DEFAULT_FORGE_TARGET)
    assert not called


def test_run_warmup_command_runs_argv_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, stdout, stderr, text, timeout, check, env):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["cargo", "check"], {}, DEFAULT_FORGE_TARGET)
    assert captured["cmd"] == ["cargo", "check"]
    assert captured["cwd"] == str(tmp_path)


def test_run_warmup_command_passes_resolved_cache_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, stdout, stderr, text, timeout, check, env):  # noqa: ANN001
        captured["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(
        tmp_path, ["dotnet", "restore"], {"NUGET_PACKAGES": "nuget"}, DEFAULT_FORGE_TARGET
    )
    assert "NUGET_PACKAGES" in captured["env"]


def test_run_warmup_command_appends_prefer_offline_to_npm_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, stdout, stderr, text, timeout, check, env):  # noqa: ANN001
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(
        tmp_path, ["npm", "--prefix", "alan/web", "ci"], {}, DEFAULT_FORGE_TARGET
    )
    assert captured["cmd"] == ["npm", "--prefix", "alan/web", "ci", "--prefer-offline"]


def test_run_warmup_command_does_not_duplicate_existing_prefer_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, stdout, stderr, text, timeout, check, env):  # noqa: ANN001
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["npm", "ci", "--prefer-offline"], {}, DEFAULT_FORGE_TARGET)
    assert captured["cmd"] == ["npm", "ci", "--prefer-offline"]


def test_run_warmup_command_leaves_non_ci_npm_commands_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, stdout, stderr, text, timeout, check, env):  # noqa: ANN001
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["npm", "run", "build"], {}, DEFAULT_FORGE_TARGET)
    assert captured["cmd"] == ["npm", "run", "build"]


def test_run_warmup_command_warns_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="oops")

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["cargo", "build"], {}, DEFAULT_FORGE_TARGET)
    assert any("warmup command failed" in r.message for r in caplog.records)


def test_run_warmup_command_warns_on_file_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        raise FileNotFoundError("no cargo")

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["cargo", "build"], {}, DEFAULT_FORGE_TARGET)
    assert any("warmup command raised" in r.message for r in caplog.records)


def test_run_warmup_command_warns_on_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        raise subprocess.TimeoutExpired(cmd="cargo", timeout=1)

    caplog.set_level("WARNING")
    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_warmup_command(tmp_path, ["cargo", "build"], {}, DEFAULT_FORGE_TARGET)
    assert any("warmup command raised" in r.message for r in caplog.records)


def test_run_warmup_runs_every_command_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sp, "_run_warmup_command",
        lambda p, argv, _cache_env, _ft: calls.append(argv),
    )
    sp._run_warmup(tmp_path, [["cargo", "fetch"], ["cargo", "build"]], {}, DEFAULT_FORGE_TARGET)
    assert calls == [["cargo", "fetch"], ["cargo", "build"]]


def test_run_warmup_empty_commands_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sp, "_run_warmup_command",
        lambda p, argv, _cache_env, _ft: calls.append(argv),
    )
    sp._run_warmup(tmp_path, [], {}, DEFAULT_FORGE_TARGET)
    assert calls == []


def test_run_warmup_single_command_does_not_positional_unpack_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sp, "_run_warmup_command",
        lambda p, argv, _cache_env, _ft: calls.append(argv),
    )
    sp._run_warmup(tmp_path, [["cargo", "check"]], {}, DEFAULT_FORGE_TARGET)
    assert calls == [["cargo", "check"]]


def test_run_warmup_threads_cache_env_to_each_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(
        sp, "_run_warmup_command",
        lambda p, argv, cache_env, _ft: seen.append(cache_env),
    )
    sp._run_warmup(
        tmp_path, [["dotnet", "restore"]], {"NUGET_PACKAGES": "nuget"}, DEFAULT_FORGE_TARGET
    )
    assert seen == [{"NUGET_PACKAGES": "nuget"}]


def test_init_slot_clones_and_runs_warmup_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    slot = tmp_path / "slot_0"
    warmup_calls: list[list[str]] = []

    def fake_run(cmd, *, capture_output, text, timeout, check, env):  # noqa: ANN001
        del capture_output, text, timeout, check, env
        slot.mkdir(parents=True, exist_ok=True)
        (slot / ".git").mkdir()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sp, "_run_warmup",
        lambda p, cmds, _cache_env, _ft: warmup_calls.extend(cmds),
    )
    sp._init_slot(slot, str(tmp_path), [["uv", "sync"], ["npm", "ci"]], {}, DEFAULT_FORGE_TARGET)
    assert warmup_calls == [["uv", "sync"], ["npm", "ci"]]


def test_init_slot_runs_nonpython_warmup_commands_without_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    slot = tmp_path / "slot_0"
    warmup_calls: list[list[str]] = []

    def fake_run(cmd, *, capture_output, text, timeout, check, env):  # noqa: ANN001
        del capture_output, text, timeout, check, env
        slot.mkdir(parents=True, exist_ok=True)
        (slot / ".git").mkdir()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sp, "_run_warmup",
        lambda p, cmds, _cache_env, _ft: warmup_calls.extend(cmds),
    )
    sp._init_slot(slot, str(tmp_path), [["cargo", "check"]], {}, DEFAULT_FORGE_TARGET)
    assert warmup_calls == [["cargo", "check"]]


def test_init_slot_threads_cache_env_to_run_warmup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    slot = tmp_path / "slot_0"
    seen: list[dict[str, str]] = []

    def fake_run(cmd, *, capture_output, text, timeout, check, env):  # noqa: ANN001
        del capture_output, text, timeout, check, env
        slot.mkdir(parents=True, exist_ok=True)
        (slot / ".git").mkdir()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        sp, "_run_warmup",
        lambda p, cmds, cache_env, _ft: seen.append(cache_env),
    )
    sp._init_slot(
        slot,
        str(tmp_path),
        [["dotnet", "restore"]],
        {"NUGET_PACKAGES": "nuget"},
        DEFAULT_FORGE_TARGET,
    )
    assert seen == [{"NUGET_PACKAGES": "nuget"}]


def test_init_slot_clones_url_verbatim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = "https://github.com/DejvMatikar/alan.git"
    slot = tmp_path / "slot_0"
    clone_args: list[str] = []

    def fake_run(cmd, *, capture_output, text, timeout, check, env):  # noqa: ANN001
        del capture_output, text, timeout, check, env
        clone_args.extend(cmd)
        slot.mkdir(parents=True, exist_ok=True)
        (slot / ".git").mkdir()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sp, "_run_warmup", lambda p, cmds, _cache_env, _ft: None)
    sp._init_slot(slot, url, [], {}, DEFAULT_FORGE_TARGET)
    assert clone_args[:5] == ["git", "-c", "credential.helper=", "clone", "--"]
    assert clone_args[5] == url


def test_init_slot_raises_on_clone_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*_a, **_k):  # noqa: ANN001,ANN002,ANN003
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sp, "_run_warmup", lambda p, cmds, _cache_env, _ft: None)
    with pytest.raises(RuntimeError, match="git clone failed"):
        sp._init_slot(tmp_path / "slot_0", str(tmp_path), [], {}, DEFAULT_FORGE_TARGET)


def test_claim_and_release(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    idx, token = pool._claim()
    assert idx in {0, 1}
    pool._release(idx, token)
    assert idx in pool._free
    assert len(pool._free) == 2


def test_claim_blocks_until_released(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    first, tok = pool._claim()
    got: list[int] = []
    claimed = threading.Event()

    def waiter() -> None:
        idx, _ = pool._claim()
        got.append(idx)
        claimed.set()

    t = threading.Thread(target=waiter)
    t.start()
    assert not claimed.wait(timeout=0.05)
    assert not got
    pool._release(first, tok)
    t.join(timeout=2.0)
    assert claimed.wait(timeout=2.0)
    assert got == [first]


def test_lease_inits_and_yields_handle(tmp_path: Path, repo: Path) -> None:
    pool_root = tmp_path / "slots"
    pool = SlotPool(root=pool_root, size=1, repo_root=str(repo))
    with pool.lease("task1", "inv_001") as (path, base_ref, branch, idx):
        assert path.is_dir()
        assert (path / ".git").exists()
        assert branch == "teams/task1-inv_001"
        assert idx == 0
        actual_branch = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path), capture_output=True, text=True, check=True
        ).stdout.strip()
        assert actual_branch == "teams/task1-inv_001"
    assert 0 in pool._free


def test_lease_releases_slot_on_exception(tmp_path: Path, repo: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    with pytest.raises(RuntimeError, match="boom"):
        with pool.lease("task2", "inv_002"):
            raise RuntimeError("boom")
    assert 0 in pool._free


def test_lease_measures_warmup_on_first_bootstrap(tmp_path: Path, repo: Path) -> None:
    measured: list[float] = []
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(repo),
        on_warmup_measured=measured.append,
    )
    with pool.lease("task1", "inv_001"):
        pass
    assert len(measured) == 1
    assert measured[0] >= 0.0


def test_lease_does_not_remeasure_warmup_on_reused_slot(tmp_path: Path, repo: Path) -> None:
    measured: list[float] = []
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(repo),
        on_warmup_measured=measured.append,
    )
    with pool.lease("task1", "inv_001"):
        pass
    with pool.lease("task1", "inv_002"):
        pass
    assert len(measured) == 1


def test_lease_with_no_on_warmup_measured_callback_is_fine(
    tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    with pool.lease("task1", "inv_001"):
        pass


def test_lease_swallows_on_warmup_measured_callback_exception(
    tmp_path: Path, repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(_elapsed: float) -> None:
        raise RuntimeError("sink down")

    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo), on_warmup_measured=_boom)
    with caplog.at_level("ERROR", logger="alan.slot_pool"):
        with pool.lease("task1", "inv_001"):
            pass
    assert any("on_warmup_measured callback raised" in r.message for r in caplog.records)


def test_lease_threads_pool_cache_env_into_init_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    captured: list[dict[str, str]] = []
    real_init_slot = sp._init_slot

    def spy_init_slot(path, repo_root, warmup_commands, cache_env, forge_target):  # noqa: ANN001
        captured.append(cache_env)
        return real_init_slot(path, repo_root, warmup_commands, cache_env, forge_target)

    monkeypatch.setattr(sp, "_init_slot", spy_init_slot)
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(repo),
        cache_env={"NUGET_PACKAGES": "nuget"},
    )
    with pool.lease("task1", "inv_001"):
        pass
    assert captured == [{"NUGET_PACKAGES": "nuget"}]


def test_lease_existing_branch(tmp_path: Path, repo: Path) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "feature-x"],
        cwd=str(repo), check=True, capture_output=True
    )
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    with pool.lease(
        "task3", "inv_003", existing_branch="feature-x"
    ) as (path, base_ref, branch, _idx):
        assert branch == "feature-x"
        actual_branch = subprocess.run(  # noqa: S603,S607
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path), capture_output=True, text=True, check=True
        ).stdout.strip()
        assert actual_branch == "feature-x"


def test_lease_reuses_existing_slot(tmp_path: Path, repo: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    with pool.lease("task4", "inv_004"):
        pass
    with pool.lease("task4", "inv_005") as (path, _base, _branch, _idx):
        assert path.is_dir()


def test_lease_existing_branch_discards_untracked_files_from_prior_run(
    tmp_path: Path, repo: Path
) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "branch", "dirty-resume-test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    with pool.lease(
        "task-dirty", "inv_001", existing_branch="dirty-resume-test"
    ) as (path, _, _, _):
        (path / "leftover.txt").write_text("uncommitted progress\n")
    with pool.lease(
        "task-dirty", "inv_002", existing_branch="dirty-resume-test"
    ) as (path, _, _, _):
        assert not (path / "leftover.txt").exists()


def test_lease_rejects_branch_starting_with_dash(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    with pytest.raises(ValueError, match="must not start with"):
        with pool.lease("task1", "inv_001", existing_branch="-bad-branch"):
            pass


def test_two_concurrent_leases_have_distinct_paths_and_no_file_crossover(
    tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(repo))
    captured: list[Path] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker(task_id: str, inv_id: str) -> None:
        try:
            with pool.lease(task_id, inv_id) as (path, _, _, _):
                captured.append(path)
                barrier.wait()
                (path / "sentinel.txt").write_text(str(path))
                barrier.wait()
                content = (path / "sentinel.txt").read_text()
                assert content == str(path), "sentinel overwritten — paths not isolated"
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("task-a", "inv_001"))
    t2 = threading.Thread(target=worker, args=("task-b", "inv_002"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive(), "thread t1 did not finish in time"
    assert not t2.is_alive(), "thread t2 did not finish in time"
    assert not errors, f"worker errors: {errors}"
    assert len(captured) == 2
    assert captured[0] != captured[1]


def test_claim_preferred_returns_that_slot(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=3, repo_root=str(tmp_path))
    idx, token = pool._claim(preferred_idx=2)  # noqa: SLF001
    assert idx == 2
    pool._release(idx, token)  # noqa: SLF001


def test_claim_preferred_blocks_until_that_slot_freed(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    held, held_tok = pool._claim(preferred_idx=0)  # noqa: SLF001
    got: list[int] = []
    started = threading.Event()
    claimed = threading.Event()

    def waiter() -> None:
        started.set()
        idx, _ = pool._claim(preferred_idx=0)  # noqa: SLF001
        got.append(idx)
        claimed.set()

    t = threading.Thread(target=waiter)
    t.start()
    assert started.wait(timeout=2.0)
    assert not claimed.wait(timeout=0.05)
    assert not got
    pool._release(held, held_tok)  # noqa: SLF001
    t.join(timeout=2.0)
    assert claimed.wait(timeout=2.0)
    assert got == [0]


def test_claim_preferred_out_of_range_warns_and_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="alan.slot_pool"):
        idx, token = pool._claim(preferred_idx=99)  # noqa: SLF001
    assert idx in {0, 1}
    assert "out of range" in caplog.text
    pool._release(idx, token)  # noqa: SLF001


def test_lease_with_preferred_idx_returns_requested_slot(
    tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(repo))
    with pool.lease("task1", "inv_001", preferred_idx=1) as (path, _base, _branch, idx):
        assert idx == 1
        assert path.name == "slot_1"


def test_prepare_existing_branch_discards_uncommitted_changes(
    tmp_path: Path, repo: Path
) -> None:
    slot = tmp_path / "slot_0"
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", str(repo), str(slot)],
        capture_output=True, check=True,
    )
    _git(slot, "config", "user.email", "t@t.com")
    _git(slot, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "checkout", "-b", "feature-resume")
    _git(repo, "checkout", "main")
    stale = slot / "stale_work.txt"
    stale.write_text("uncommitted work\n")
    assert stale.exists()
    sp._prepare_existing_branch(slot, "main", DEFAULT_FORGE_TARGET)
    assert not stale.exists(), (
        "uncommitted changes survived _prepare_existing_branch — isolation broken"
    )


def test_concurrent_leases_get_distinct_slot_paths(repo: Path, pool_root: Path) -> None:
    pool = SlotPool(root=pool_root, size=2, repo_root=str(repo))
    paths_seen: list[Path] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2, timeout=5.0)

    def do_lease(inv_id: str) -> None:
        try:
            with pool.lease("ws-shared", inv_id) as (path, _base, _branch, _idx):
                barrier.wait()
                paths_seen.append(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=lambda: do_lease("inv-a"))
    t2 = threading.Thread(target=lambda: do_lease("inv-b"))
    t1.start()
    t2.start()
    t1.join(timeout=15.0)
    t2.join(timeout=15.0)

    assert not t1.is_alive(), "thread t1 did not finish"
    assert not t2.is_alive(), "thread t2 did not finish"
    assert not errors, f"lease errors: {errors}"
    assert len(paths_seen) == 2
    assert paths_seen[0] != paths_seen[1], (
        "both workers got the same slot path — isolation not guaranteed"
    )


def test_rescue_branch_name_basic() -> None:
    assert sp._rescue_branch_name("task1", "inv_001") == "rescue/task1@inv_001"


def test_rescue_branch_name_sanitizes_slashes() -> None:
    assert sp._rescue_branch_name("my/task", "inv_002") == "rescue/my-task@inv_002"


def test_rescue_branch_name_sanitizes_inv_id() -> None:
    assert sp._rescue_branch_name("task1", "inv/003") == "rescue/task1@inv-003"


def test_count_commits_ahead_returns_zero_when_branch_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._count_commits_ahead(tmp_path, "abc123", "missing-branch") == 0


def test_count_commits_ahead_returns_zero_when_rev_list_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    call_count = [0]

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        call_count[0] += 1
        if args[:2] == ["rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="abc\n", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._count_commits_ahead(tmp_path, "abc123", "some-branch") == 0


def test_count_commits_ahead_returns_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="abc\n", stderr="")
        if args[:2] == ["rev-list", "--count"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="3\n", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._count_commits_ahead(tmp_path, "origin/main", "feature-x") == 3


def test_count_commits_ahead_returns_zero_on_non_numeric_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="abc\n", stderr="")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-a-number\n", stderr=""
        )

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    assert sp._count_commits_ahead(tmp_path, "abc", "branch") == 0


def test_anchor_rescue_ref_calls_branch_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    sp._anchor_rescue_ref(tmp_path, "teams/t1-inv_001", "rescue/t1@inv_001")
    assert calls == [["branch", "-f", "rescue/t1@inv_001", "teams/t1-inv_001"]]


def test_anchor_rescue_ref_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a valid branch"
        )

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    with pytest.raises(RuntimeError, match="git branch -f"):
        sp._anchor_rescue_ref(tmp_path, "teams/t1-inv_001", "rescue/t1@inv_001")


def test_fetch_for_resume_calls_fetch_existing_when_branch_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[str] = []
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: called.append(b))
    monkeypatch.setattr(sp, "_fetch_base", lambda p, _ft: called.append("base"))
    sp._fetch_for_resume(tmp_path, "teams/t1-inv_001", DEFAULT_FORGE_TARGET)
    assert called == ["teams/t1-inv_001"]


def test_fetch_for_resume_calls_fetch_base_when_no_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[str] = []
    monkeypatch.setattr(sp, "_fetch_existing_branch", lambda p, b, _ft: called.append(b))
    monkeypatch.setattr(sp, "_fetch_base", lambda p, _ft: called.append("base"))
    sp._fetch_for_resume(tmp_path, None, DEFAULT_FORGE_TARGET)
    assert called == ["base"]


def test_fallback_prepare_calls_prepare_new_branch_when_no_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[str] = []

    def _fake_new(p: Path, t: str, i: str, _ft: object) -> tuple[str, str]:
        called.append("new")
        return "origin/main", "teams/t-inv"

    def _fake_existing(p: Path, b: str, _ft: object) -> tuple[str, str]:
        called.append("existing")
        return b, b

    monkeypatch.setattr(sp, "_prepare_new_branch", _fake_new)
    monkeypatch.setattr(sp, "_prepare_existing_branch", _fake_existing)
    base, branch = sp._fallback_prepare(tmp_path, "t", "inv_001", None, DEFAULT_FORGE_TARGET)
    assert "new" in called
    assert "existing" not in called
    assert branch == "teams/t-inv"


def test_fallback_prepare_calls_prepare_existing_branch_when_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[str] = []

    def _fake_new(p: Path, t: str, i: str, _ft: object) -> tuple[str, str]:
        called.append("new")
        return "origin/main", "teams/t-inv"

    def _fake_existing(p: Path, b: str, _ft: object) -> tuple[str, str]:
        called.append("existing")
        return "sha123", b

    monkeypatch.setattr(sp, "_prepare_new_branch", _fake_new)
    monkeypatch.setattr(sp, "_prepare_existing_branch", _fake_existing)
    base, branch = sp._fallback_prepare(tmp_path, "t", "inv_001", "pr-branch", DEFAULT_FORGE_TARGET)
    assert "existing" in called
    assert "new" not in called
    assert branch == "pr-branch"


def test_prepare_resume_falls_back_when_zero_commits_ahead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sp, "_count_commits_ahead", lambda p, sha, _br: 0)
    monkeypatch.setattr(
        sp, "_fallback_prepare",
        lambda p, t, i, _eb, _ft: ("origin/main", f"teams/{t}-{i}"),
    )
    base, branch, n, rescue = sp._prepare_resume(
        tmp_path, "task1", "inv_001", None, "abc123", DEFAULT_FORGE_TARGET
    )
    assert n == 0
    assert rescue is None
    assert branch == "teams/task1-inv_001"


def test_prepare_resume_preserves_work_on_successful_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    anchored: list[str] = []
    monkeypatch.setattr(sp, "_count_commits_ahead", lambda p, sha, _br: 2)
    monkeypatch.setattr(sp, "_anchor_rescue_ref", lambda p, _br, r: anchored.append(r))
    monkeypatch.setattr(sp, "_fetch_for_resume", lambda p, _eb, _ft: None)
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["checkout"]:
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    base, branch, n, rescue = sp._prepare_resume(
        tmp_path, "task1", "inv_001", None, "base_sha", DEFAULT_FORGE_TARGET
    )
    assert n == 2
    assert rescue is None
    assert base == "base_sha"
    assert branch == "teams/task1-inv_001"
    assert len(anchored) == 1


def test_prepare_resume_rescues_when_checkout_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    anchored: list[str] = []
    fallback_called: list[str] = []
    monkeypatch.setattr(sp, "_count_commits_ahead", lambda p, sha, _br: 3)
    monkeypatch.setattr(sp, "_anchor_rescue_ref", lambda p, _br, r: anchored.append(r))
    monkeypatch.setattr(sp, "_fetch_for_resume", lambda p, _eb, _ft: None)
    monkeypatch.setattr(sp, "_clean_slot", lambda p: None)

    def fake_run_git(
        args: list[str], cwd: Path, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["checkout"]:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sp, "_run_git", fake_run_git)
    def _fb(p: Path, t: str, i: str, _eb: object, _ft: object) -> tuple[str, str]:
        fallback_called.append("ok")
        return ("fb_base", f"teams/{t}-{i}")

    monkeypatch.setattr(sp, "_fallback_prepare", _fb)
    base, branch, n, rescue = sp._prepare_resume(
        tmp_path, "task1", "inv_001", None, "base_sha", DEFAULT_FORGE_TARGET
    )
    assert n == 0
    assert rescue is not None
    assert "rescue/" in rescue
    assert len(fallback_called) == 1
    assert base == "fb_base"


def test_get_preserve_info_returns_zero_none_for_unknown_slot(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    assert pool.get_preserve_info(0) == (0, None)


def test_lease_without_preferred_idx_ignores_resume_base_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    resume_called: list[str] = []

    def patched_prepare_resume(
        p: Path, t: str, i: str, _eb: object, sha: str
    ) -> tuple[str, str, int, str | None]:
        resume_called.append(i)
        return ("base", "branch", 0, None)

    monkeypatch.setattr(sp, "_prepare_resume", patched_prepare_resume)
    with pool.lease("t", "inv_001", resume_base_ref="some_sha") as (_, _base, _br, _idx):
        pass

    assert resume_called == [], "resume path must not be taken without preferred_idx"


def test_lease_resume_stores_preserve_info_when_work_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    monkeypatch.setattr(sp, "_prepare_resume",
        lambda p, t, i, _eb, sha, _ft: ("sha", "branch", 5, None))
    with pool.lease(
        "t", "inv_001", preferred_idx=0, resume_base_ref="base"
    ) as (_path, _base, _branch, _idx):
        assert pool.get_preserve_info(0) == (5, None)


def test_lease_resume_stores_preserve_info_when_work_rescued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    monkeypatch.setattr(sp, "_prepare_resume",
        lambda p, t, i, _eb, sha, _ft: ("sha", "branch", 0, "rescue/t@inv_001"))
    with pool.lease(
        "t", "inv_001", preferred_idx=0, resume_base_ref="base"
    ) as (_path, _base, _branch, _idx):
        assert pool.get_preserve_info(0) == (0, "rescue/t@inv_001")


def test_lease_non_resume_clears_preserve_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    pool._preserve_info[0] = (3, "rescue/old@inv_001")  # noqa: SLF001
    monkeypatch.setattr(sp, "_prepare_new_branch",
        lambda p, t, i, _ft: ("origin/main", f"teams/{t}-{i}"))
    with pool.lease("t", "inv_002") as (_path, _base, _branch, idx):
        assert pool.get_preserve_info(idx) == (0, None)


def test_slot_pool_defaults_warmup_commands_to_engine_default(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    assert pool._warmup_commands == [  # noqa: SLF001
        list(cmd) for cmd in _ENGINE_DEFAULT.warmup_commands
    ]


def test_slot_pool_accepts_custom_warmup_commands(tmp_path: Path) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path),
        warmup_commands=[["cargo", "check"]],
    )
    assert pool._warmup_commands == [["cargo", "check"]]  # noqa: SLF001


def test_slot_pool_accepts_empty_warmup_commands(tmp_path: Path) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path), warmup_commands=[],
    )
    assert pool._warmup_commands == []  # noqa: SLF001


@pytest.fixture
def engine_keyed_slot_pool(tmp_path: Path, repo: Path) -> SlotPool:
    return SlotPool(
        root=tmp_path / "engine-slots",
        size=1,
        repo_root=str(repo),
        warmup_commands=[["cargo", "check"], ["cargo", "build"]],
    )


def test_engine_keyed_slot_pool_lease_runs_nonpython_warmup(
    engine_keyed_slot_pool: SlotPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sp, "_run_warmup_command",
        lambda p, argv, _cache_env, _ft: calls.append(argv),
    )
    with engine_keyed_slot_pool.lease("task1", "inv_001") as (path, _base, branch, idx):
        assert path.is_dir()
        assert (path / ".git").exists()
        assert branch == "teams/task1-inv_001"
        assert idx == 0
    assert calls == [["cargo", "check"], ["cargo", "build"]]


def test_engine_keyed_slot_pool_root_is_isolated_from_default_pool(
    engine_keyed_slot_pool: SlotPool, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    assert engine_keyed_slot_pool.slot_path(0) != default_pool.slot_path(0)


def test_claim_preferred_calls_on_blocked_once_when_slot_busy(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    held, held_tok = pool._claim()  # noqa: SLF001
    calls: list[int] = []
    blocked_seen = threading.Event()
    claimed = threading.Event()

    def on_blocked() -> None:
        calls.append(1)
        blocked_seen.set()

    def waiter() -> None:
        idx, tok = pool._claim(preferred_idx=0, on_blocked=on_blocked)  # noqa: SLF001
        pool._release(idx, tok)  # noqa: SLF001
        claimed.set()

    t = threading.Thread(target=waiter)
    t.start()
    assert blocked_seen.wait(timeout=2.0)
    pool._release(held, held_tok)  # noqa: SLF001
    assert claimed.wait(timeout=2.0)
    t.join(timeout=2.0)
    assert calls == [1]


def test_claim_preferred_second_wait_cycle_skips_repeat_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    with pool._cond:  # noqa: SLF001
        pool._free.discard(0)  # noqa: SLF001

    wait_calls: list[int] = []

    def fake_wait(timeout: float | None = None) -> None:
        del timeout
        wait_calls.append(1)
        if len(wait_calls) >= 2:
            pool._free.add(0)  # noqa: SLF001

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    calls: list[int] = []
    with pool._cond:  # noqa: SLF001
        idx, _tok = pool._claim_preferred_under_lock(  # noqa: SLF001
            0, on_blocked=lambda: calls.append(1)
        )
    assert idx == 0
    assert calls == [1]
    assert len(wait_calls) == 2


def test_claim_preferred_logs_warning_once_when_blocked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    held, held_tok = pool._claim()  # noqa: SLF001
    claimed = threading.Event()

    def waiter() -> None:
        idx, tok = pool._claim(preferred_idx=0)  # noqa: SLF001  no on_blocked passed
        pool._release(idx, tok)  # noqa: SLF001
        claimed.set()

    with caplog.at_level(logging.WARNING, logger="alan.slot_pool"):
        t = threading.Thread(target=waiter)
        t.start()
        assert not claimed.wait(timeout=0.1)
        pool._release(held, held_tok)  # noqa: SLF001
        assert claimed.wait(timeout=2.0)
        t.join(timeout=2.0)
    warnings = [r for r in caplog.records if "preferred slot 0 is held" in r.message]
    assert len(warnings) == 1


def test_claim_preferred_without_on_blocked_does_not_crash(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    held, held_tok = pool._claim()  # noqa: SLF001
    claimed = threading.Event()

    def waiter() -> None:
        idx, tok = pool._claim(preferred_idx=0, on_blocked=None)  # noqa: SLF001
        pool._release(idx, tok)  # noqa: SLF001
        claimed.set()

    t = threading.Thread(target=waiter)
    t.start()
    assert not claimed.wait(timeout=0.1)
    pool._release(held, held_tok)  # noqa: SLF001
    assert claimed.wait(timeout=2.0)
    t.join(timeout=2.0)


def test_claim_preferred_immediately_free_never_calls_on_blocked(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    calls: list[int] = []
    idx, tok = pool._claim(preferred_idx=0, on_blocked=lambda: calls.append(1))  # noqa: SLF001
    assert idx == 0
    assert calls == []
    pool._release(idx, tok)  # noqa: SLF001


def test_claim_preferred_releases_cond_for_other_threads_during_on_blocked(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    held, held_tok = pool._claim(preferred_idx=0)  # noqa: SLF001  slot 0 held, slot 1 free
    on_blocked_started = threading.Event()
    release_on_blocked = threading.Event()

    def on_blocked() -> None:
        on_blocked_started.set()
        release_on_blocked.wait(timeout=5.0)

    def waiter() -> None:
        idx, tok = pool._claim(preferred_idx=0, on_blocked=on_blocked)  # noqa: SLF001
        pool._release(idx, tok)  # noqa: SLF001

    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    assert on_blocked_started.wait(timeout=2.0)

    other_claimed = threading.Event()
    other_result: list[int] = []

    def other() -> None:
        idx2, tok2 = pool._claim()  # noqa: SLF001  any-slot claim — must not block
        other_result.append(idx2)
        other_claimed.set()
        pool._release(idx2, tok2)  # noqa: SLF001

    other_thread = threading.Thread(target=other)
    other_thread.start()
    assert other_claimed.wait(timeout=2.0), (
        "other thread blocked — _cond not released during on_blocked"
    )
    assert other_result == [1]

    release_on_blocked.set()
    pool._release(held, held_tok)  # noqa: SLF001
    waiter_thread.join(timeout=2.0)
    other_thread.join(timeout=2.0)
    assert not waiter_thread.is_alive()
    assert not other_thread.is_alive()


def test_claim_any_slot_path_ignores_on_blocked(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    calls: list[int] = []
    idx, tok = pool._claim(on_blocked=lambda: calls.append(1))  # noqa: SLF001
    assert calls == []
    pool._release(idx, tok)  # noqa: SLF001


def test_lease_threads_on_blocked_to_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    captured: list[object] = []
    real_claim = pool._claim  # noqa: SLF001

    def patched_claim(preferred_idx=None, on_blocked=None):
        captured.append(on_blocked)
        return real_claim(preferred_idx, on_blocked)

    monkeypatch.setattr(pool, "_claim", patched_claim)
    sentinel = lambda: None  # noqa: E731
    with pool.lease("t1", "inv_001", preferred_idx=0, on_blocked=sentinel):
        pass
    assert captured == [sentinel]


_REMOTE_REPO_ROOT = "https://github.com/DejvMatikar/alan.git"


def test_slot_pool_skips_transport_for_local_repo_root(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    assert pool._forge_target is None  # noqa: SLF001


def test_slot_pool_omitted_forge_target_injects_no_credential_for_remote_repo_root(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=_REMOTE_REPO_ROOT)
    slot = tmp_path / "slot_0"
    assert pool._forge_target is None  # noqa: SLF001
    env, argv = sp._clone_env_and_argv(_REMOTE_REPO_ROOT, slot, pool._forge_target)  # noqa: SLF001
    assert argv == ["git", "clone", "--", _REMOTE_REPO_ROOT, str(slot)]
    assert secrets_store.ASKPASS_TOKEN_ENV not in env


def test_slot_pool_explicit_none_forge_target_disables_injection_for_remote_repo_root(
    tmp_path: Path,
) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=_REMOTE_REPO_ROOT, forge_target=None,
    )
    assert pool._forge_target is None  # noqa: SLF001


def test_slot_pool_uses_explicit_forge_target_for_remote_repo_root(tmp_path: Path) -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        credential_env_name="SPROCKET_GH_TOKEN",
    )
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=_REMOTE_REPO_ROOT, forge_target=target,
    )
    assert pool._forge_target == target  # noqa: SLF001


def test_slot_pool_explicit_forge_target_ignored_for_local_repo_root(tmp_path: Path) -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        credential_env_name="SPROCKET_GH_TOKEN",
    )
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path), forge_target=target,
    )
    assert pool._forge_target is None  # noqa: SLF001


def test_run_git_without_extra_env_does_not_reset_credential_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, capture_output, text, timeout, check, env):  # noqa: ANN001
        del cwd, capture_output, text, timeout, check
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_git(["status"], tmp_path)
    assert captured["cmd"] == ["git", "status"]
    assert "credential.helper" not in " ".join(captured["cmd"])


def test_run_git_with_extra_env_adds_credential_reset_and_merges_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, capture_output, text, timeout, check, env):  # noqa: ANN001
        del cwd, capture_output, text, timeout, check
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sp._run_git(["fetch", "origin", "main"], tmp_path, extra_env={"FOO": "bar"})
    assert captured["cmd"] == ["git", "-c", "credential.helper=", "fetch", "origin", "main"]
    assert captured["env"]["FOO"] == "bar"
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_warmup_command_env_includes_transport_credentials_when_forge_target_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = sp._warmup_command_env({"UV_CACHE_DIR": "uv"}, DEFAULT_FORGE_TARGET)
    assert env[secrets_store.ASKPASS_TOKEN_ENV] == _FAKE_TOKEN
    assert env["GIT_ASKPASS"]


def test_warmup_command_env_skips_transport_when_forge_target_none() -> None:
    env = sp._warmup_command_env({"UV_CACHE_DIR": "uv"}, None)
    assert secrets_store.ASKPASS_TOKEN_ENV not in env
    assert "GIT_ASKPASS" not in env


def test_warmup_command_env_raises_missing_secret_when_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    with pytest.raises(secrets_store.MissingSecretError):
        sp._warmup_command_env({"UV_CACHE_DIR": "uv"}, DEFAULT_FORGE_TARGET)


def test_pool_starts_not_warm(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    assert pool.warm is False


def test_mark_warm_flips_warm_property(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    pool.mark_warm()
    assert pool.warm is True


def test_disk_usage_zero_when_root_absent(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "does-not-exist", size=1, repo_root=str(tmp_path))
    assert pool.disk_usage() == 0


def test_disk_usage_sums_file_sizes(tmp_path: Path) -> None:
    root = tmp_path / "slots"
    root.mkdir()
    (root / "a.txt").write_bytes(b"1234567890")
    nested = root / "slot_0"
    nested.mkdir()
    (nested / "b.txt").write_bytes(b"12345")
    pool = SlotPool(root=root, size=1, repo_root=str(tmp_path))
    assert pool.disk_usage() == 15


def test_snapshot_reflects_size_free_and_warm(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    idx, token = pool._claim()  # noqa: SLF001
    state = pool.snapshot("acme/widget")
    assert state.slug == "acme/widget"
    assert state.size == 2
    assert state.free_count == 1
    assert state.leased_count == 1
    assert state.warm is False
    assert state.quiescing is False
    pool._release(idx, token)  # noqa: SLF001


def test_snapshot_disk_usage_matches_disk_usage_method(tmp_path: Path) -> None:
    root = tmp_path / "slots"
    root.mkdir()
    (root / "f.bin").write_bytes(b"x" * 42)
    pool = SlotPool(root=root, size=1, repo_root=str(tmp_path))
    assert pool.snapshot("acme/widget").disk_usage_bytes == 42


def test_begin_dispose_succeeds_when_idle_and_sets_quiescing(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    pool.begin_dispose()
    with pool._cond:  # noqa: SLF001
        assert pool._quiescing is True  # noqa: SLF001


def test_begin_dispose_raises_pool_busy_when_lease_active(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    idx, token = pool._claim()  # noqa: SLF001
    with pytest.raises(sp.PoolBusyError, match="active leases"):
        pool.begin_dispose()
    with pool._cond:  # noqa: SLF001
        assert pool._quiescing is False  # noqa: SLF001 — refusal must not mutate state
    pool._release(idx, token)  # noqa: SLF001


def test_finish_dispose_removes_root_directory(tmp_path: Path) -> None:
    root = tmp_path / "slots"
    root.mkdir()
    (root / "slot_0").mkdir()
    pool = SlotPool(root=root, size=1, repo_root=str(tmp_path))
    pool.begin_dispose()
    pool.finish_dispose()
    assert not root.exists()


def test_finish_dispose_noop_when_root_never_existed(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "never-created", size=1, repo_root=str(tmp_path))
    pool.begin_dispose()
    pool.finish_dispose()


def test_finish_dispose_raises_when_rmtree_leaves_dir_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "slots"
    root.mkdir()
    pool = SlotPool(root=root, size=1, repo_root=str(tmp_path))
    pool.begin_dispose()
    monkeypatch.setattr(shutil, "rmtree", lambda *_a, **_kw: None)
    with pytest.raises(sp.PoolDisposeIncompleteError, match="failed to fully remove"):
        pool.finish_dispose()


def test_claim_raises_pool_busy_when_quiescing(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    pool.begin_dispose()
    with pytest.raises(sp.PoolBusyError, match="quiescing"):
        pool._claim()  # noqa: SLF001


def test_claim_preferred_raises_pool_busy_when_quiescing(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    pool.begin_dispose()
    with pytest.raises(sp.PoolBusyError, match="quiescing"):
        pool._claim(preferred_idx=0)  # noqa: SLF001


def test_lease_raises_pool_busy_when_pool_quiescing(tmp_path: Path, repo: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(repo))
    pool.begin_dispose()
    with pytest.raises(sp.PoolBusyError, match="quiescing"):
        with pool.lease("task1", "inv_001"):
            pass


def test_claim_raises_pool_busy_when_quiesced_while_parked_mid_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    idx, _token = pool._claim()  # noqa: SLF001  slot 0 held; free set now empty

    def fake_wait(timeout: float | None = None) -> None:
        del timeout
        pool._free.add(idx)  # noqa: SLF001
        pool._quiescing = True  # noqa: SLF001

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    with pytest.raises(sp.PoolBusyError, match="quiescing"):
        pool._claim()  # noqa: SLF001


def test_claim_preferred_raises_pool_busy_when_quiesced_while_parked_mid_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path))
    pool._claim(preferred_idx=0)  # noqa: SLF001  slot 0 held; not in free set

    def fake_wait(timeout: float | None = None) -> None:
        del timeout
        pool._free.add(0)  # noqa: SLF001
        pool._quiescing = True  # noqa: SLF001

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    with pytest.raises(sp.PoolBusyError, match="quiescing"):
        pool._claim(preferred_idx=0)  # noqa: SLF001
