from __future__ import annotations

import subprocess

import pytest

from alan import deploy_refresh as dr


def _run_git(args, cwd, *, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _init_bare(path):
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "--bare", "-b", "main"], path)
    return path


def _init_seed(path, origin):
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q", "-b", "main"], path)
    _run_git(["config", "user.email", "a@b.c"], path)
    _run_git(["config", "user.name", "test"], path)
    (path / "README.md").write_text("seed", encoding="utf-8")
    (path / "deploy").mkdir()
    (path / "deploy" / "ai.alan.plist").write_text("plist", encoding="utf-8")
    _run_git(["add", "-A"], path)
    _run_git(["commit", "-q", "-m", "seed"], path)
    _run_git(["remote", "add", "origin", str(origin)], path)
    _run_git(["push", "-q", "origin", "main"], path)
    return path


def _rev_parse(repo, rev):
    return _run_git(["rev-parse", rev], repo).stdout.strip()


def _clone(origin, dest, *, depth=None):
    args = ["clone", "-q"]
    if depth is not None:
        args += ["--depth", str(depth), "--no-local"]
    args += [str(origin), str(dest)]
    _run_git(args, dest.parent)
    _run_git(["config", "user.email", "a@b.c"], dest)
    _run_git(["config", "user.name", "test"], dest)
    return dest


def _advance(seed, filename):
    (seed / filename).write_text(filename, encoding="utf-8")
    _run_git(["add", "-A"], seed)
    _run_git(["commit", "-q", "-m", filename], seed)
    _run_git(["push", "-q", "origin", "main"], seed)
    return _rev_parse(seed, "HEAD")


def _skip_worktree_flag(repo):
    return _run_git(["ls-files", "-v", "deploy/ai.alan.plist"], repo).stdout[:1]


def _set_skip_worktree(repo):
    _run_git(["update-index", "--skip-worktree", "deploy/ai.alan.plist"], repo)


def _push_divergent_commit(origin, tmp_path, base_sha, label):
    work = tmp_path / f"divergent-work-{label}"
    _run_git(["clone", "-q", str(origin), str(work)], tmp_path)
    _run_git(["config", "user.email", "a@b.c"], work)
    _run_git(["config", "user.name", "test"], work)
    _run_git(["checkout", "-q", "-b", "scratch", base_sha], work)
    (work / "other.txt").write_text("other", encoding="utf-8")
    _run_git(["add", "-A"], work)
    _run_git(["commit", "-q", "-m", "divergent"], work)
    divergent_sha = _rev_parse(work, "HEAD")
    _run_git(["push", "-q", "origin", "scratch"], work)
    return divergent_sha


@pytest.fixture
def deploy_pair(tmp_path):
    origin = _init_bare(tmp_path / "origin.git")
    seed = _init_seed(tmp_path / "seed", origin)
    base_sha = _rev_parse(seed, "HEAD")
    local = _clone(origin, tmp_path / "local")
    return origin, seed, local, base_sha


def test_refresh_succeeds_under_held_remote_tracking_and_tag_locks(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")
    (local / ".git" / "refs" / "remotes" / "origin").mkdir(parents=True, exist_ok=True)
    (local / ".git" / "refs" / "remotes" / "origin" / "main.lock").write_bytes(b"")
    (local / ".git" / "refs" / "tags").mkdir(parents=True, exist_ok=True)
    (local / ".git" / "refs" / "tags" / "deploy-20260101_000000.lock").write_bytes(b"")

    result = dr.refresh(local, base_sha)

    assert result == tip_sha
    assert _rev_parse(local, "HEAD") == tip_sha


def test_plain_fetch_origin_main_fails_under_same_held_locks(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    _advance(seed, "second.txt")
    (local / ".git" / "refs" / "remotes" / "origin").mkdir(parents=True, exist_ok=True)
    (local / ".git" / "refs" / "remotes" / "origin" / "main.lock").write_bytes(b"")

    result = _run_git(["fetch", "origin", "main"], local, check=False)

    assert result.returncode != 0


def test_fetch_target_writes_no_fetch_head_file(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    _advance(seed, "second.txt")
    fetch_head = local / ".git" / "FETCH_HEAD"
    fetch_head.unlink(missing_ok=True)

    dr.fetch_target(local)

    assert not fetch_head.exists()


def test_fetch_target_resolves_private_ref_without_touching_remote_tracking_ref(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")

    resolved = dr.fetch_target(local)

    assert resolved == tip_sha
    assert _rev_parse(local, "refs/remotes/origin/main") == base_sha
    assert _rev_parse(local, dr.TARGET_REF) == tip_sha


def test_fetch_target_creates_no_tag_refs(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    _run_git(["tag", "sometag"], seed)
    _run_git(["push", "-q", "origin", "sometag"], seed)

    dr.fetch_target(local)

    assert _run_git(["tag", "--list"], local).stdout.strip() == ""


def test_fetch_target_force_overwrites_a_stale_target_ref_from_a_prior_failed_run(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")
    dr.fetch_target(local)
    assert _rev_parse(local, dr.TARGET_REF) == tip_sha

    _run_git(["reset", "-q", "--hard", base_sha], seed)
    (seed / "alt.txt").write_text("alt", encoding="utf-8")
    _run_git(["add", "-A"], seed)
    _run_git(["commit", "-q", "-m", "alt"], seed)
    alt_sha = _rev_parse(seed, "HEAD")
    _run_git(["push", "-q", "--force", "origin", "main"], seed)

    resolved = dr.fetch_target(local)

    assert resolved == alt_sha
    assert _rev_parse(local, dr.TARGET_REF) == alt_sha


def test_assert_not_shallow_passes_on_full_repo(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    dr.assert_not_shallow(local)


def test_assert_not_shallow_raises_on_shallow_clone(tmp_path):
    origin = _init_bare(tmp_path / "origin.git")
    seed = _init_seed(tmp_path / "seed", origin)
    _advance(seed, "second.txt")
    shallow = _clone(origin, tmp_path / "shallow", depth=1)

    with pytest.raises(dr.ShallowRepositoryError):
        dr.assert_not_shallow(shallow)


def test_assert_ancestry_passes_on_equal_shas(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    dr.assert_ancestry(local, base_sha, base_sha)


def test_assert_ancestry_passes_on_strict_ancestor(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")
    dr.fetch_target(local)
    dr.assert_ancestry(local, base_sha, tip_sha)


def test_assert_ancestry_raises_not_contained_on_divergent_branch(deploy_pair, tmp_path):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")
    divergent_sha = _push_divergent_commit(origin, tmp_path, base_sha, "notcontained")
    _run_git(["fetch", "-q", str(origin), divergent_sha], local)
    dr.fetch_target(local)

    with pytest.raises(dr.NotContainedError) as excinfo:
        dr.assert_ancestry(local, divergent_sha, tip_sha)

    assert excinfo.value.pushed_sha == divergent_sha
    assert excinfo.value.target_sha == tip_sha


def test_assert_ancestry_raises_pushed_sha_absent_on_unknown_sha(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    unknown_sha = "f" * 40

    with pytest.raises(dr.PushedShaAbsentError) as excinfo:
        dr.assert_ancestry(local, unknown_sha, base_sha)

    assert excinfo.value.pushed_sha == unknown_sha


def test_assert_ancestry_checks_presence_before_invoking_merge_base(deploy_pair, monkeypatch):
    origin, seed, local, base_sha = deploy_pair
    unknown_sha = "f" * 40
    calls = []
    original_git = dr._git

    def _spy(repo, args, *, check=True):
        calls.append(list(args))
        return original_git(repo, args, check=check)

    monkeypatch.setattr(dr, "_git", _spy)

    with pytest.raises(dr.PushedShaAbsentError):
        dr.assert_ancestry(local, unknown_sha, base_sha)

    assert not any(call[0] == "merge-base" for call in calls)


def test_refresh_targets_current_tip_when_pushed_sha_is_a_strict_ancestor(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    _advance(seed, "second.txt")
    tip3 = _advance(seed, "third.txt")

    result = dr.refresh(local, base_sha)

    assert result == tip3
    assert _rev_parse(local, "HEAD") == tip3


def _prepare_untouched_repo(tmp_path, name):
    origin = _init_bare(tmp_path / f"{name}-origin.git")
    seed = _init_seed(tmp_path / f"{name}-seed", origin)
    base_sha = _rev_parse(seed, "HEAD")
    local = _clone(origin, tmp_path / f"{name}-local")
    _set_skip_worktree(local)
    (local / "untracked.txt").write_text("untracked", encoding="utf-8")
    return origin, seed, local, base_sha


def test_refresh_leaves_working_copy_untouched_on_not_contained_failure(tmp_path):
    origin, seed, local, base_sha = _prepare_untouched_repo(tmp_path, "notcontained")
    divergent_sha = _push_divergent_commit(origin, tmp_path, base_sha, "leaveuntouched")
    _run_git(["fetch", "-q", str(origin), divergent_sha], local)

    with pytest.raises(dr.NotContainedError):
        dr.refresh(local, divergent_sha)

    assert _rev_parse(local, "HEAD") == base_sha
    assert (local / "untracked.txt").exists()
    assert _skip_worktree_flag(local) == "S"


def test_refresh_leaves_working_copy_untouched_on_pushed_sha_absent_failure(tmp_path):
    origin, seed, local, base_sha = _prepare_untouched_repo(tmp_path, "absent")

    with pytest.raises(dr.PushedShaAbsentError):
        dr.refresh(local, "f" * 40)

    assert _rev_parse(local, "HEAD") == base_sha
    assert (local / "untracked.txt").exists()
    assert _skip_worktree_flag(local) == "S"


def test_refresh_leaves_working_copy_untouched_on_shallow_failure(tmp_path):
    origin = _init_bare(tmp_path / "shallow-origin.git")
    seed = _init_seed(tmp_path / "shallow-seed", origin)
    _advance(seed, "second.txt")
    base_sha = _rev_parse(seed, "HEAD")
    shallow = _clone(origin, tmp_path / "shallow-local", depth=1)
    _set_skip_worktree(shallow)
    (shallow / "untracked.txt").write_text("untracked", encoding="utf-8")

    with pytest.raises(dr.ShallowRepositoryError):
        dr.refresh(shallow, base_sha)

    assert _rev_parse(shallow, "HEAD") == base_sha
    assert (shallow / "untracked.txt").exists()
    assert _skip_worktree_flag(shallow) == "S"


def test_refresh_tolerates_clean_index_for_the_plist_flag_clear(deploy_pair):
    origin, seed, local, base_sha = deploy_pair

    result = dr.refresh(local, base_sha)

    assert result == base_sha


def test_refresh_clean_excludes_all_three_named_paths(deploy_pair):
    origin, seed, local, base_sha = deploy_pair
    (local / "alan" / "web" / "node_modules").mkdir(parents=True)
    (local / "alan" / "web" / "node_modules" / "foo.txt").write_text("f", encoding="utf-8")
    (local / ".venv").mkdir()
    (local / ".venv" / "bar.txt").write_text("b", encoding="utf-8")
    (local / "alan" / "web" / "dist.a").mkdir(parents=True)
    (local / "alan" / "web" / "dist.a" / "baz.txt").write_text("z", encoding="utf-8")
    (local / "garbage").mkdir()
    (local / "garbage" / "qux.txt").write_text("q", encoding="utf-8")

    dr.refresh(local, base_sha)

    assert (local / "alan" / "web" / "node_modules" / "foo.txt").exists()
    assert (local / ".venv" / "bar.txt").exists()
    assert (local / "alan" / "web" / "dist.a" / "baz.txt").exists()
    assert not (local / "garbage").exists()


def test_deploy_refresh_resets_hard_to_the_verified_target_sha(deploy_pair, monkeypatch):
    origin, seed, local, base_sha = deploy_pair
    tip_sha = _advance(seed, "second.txt")
    calls = []
    original_git = dr._git

    def _spy(repo, args, *, check=True):
        calls.append(list(args))
        return original_git(repo, args, check=check)

    monkeypatch.setattr(dr, "_git", _spy)

    result = dr.refresh(local, base_sha)

    assert result == tip_sha
    assert ["reset", "--hard", tip_sha] in calls
    assert not any("origin/main" in arg for call in calls for arg in call)


def test_pushed_sha_rejects_non_forty_hex_input(deploy_pair):
    origin, seed, local, base_sha = deploy_pair

    with pytest.raises(dr.PushedShaAbsentError):
        dr.refresh(local, "not-a-sha")
