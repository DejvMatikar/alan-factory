from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import deploy_refresh as script  # type: ignore[import-not-found]  # noqa: E402

from alan import deploy_refresh as deploy_refresh_mod


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
    _run_git(["add", "-A"], path)
    _run_git(["commit", "-q", "-m", "seed"], path)
    _run_git(["remote", "add", "origin", str(origin)], path)
    _run_git(["push", "-q", "origin", "main"], path)
    return path


def _rev_parse(repo, rev):
    return _run_git(["rev-parse", rev], repo).stdout.strip()


def _clone(origin, dest):
    _run_git(["clone", "-q", str(origin), str(dest)], dest.parent)
    _run_git(["config", "user.email", "a@b.c"], dest)
    _run_git(["config", "user.name", "test"], dest)
    return dest


def _deploy_pair(tmp_path):
    origin = _init_bare(tmp_path / "origin.git")
    seed = _init_seed(tmp_path / "seed", origin)
    base_sha = _rev_parse(seed, "HEAD")
    local = _clone(origin, tmp_path / "local")
    return origin, seed, local, base_sha


def _push_divergent_commit(origin, tmp_path, base_sha):
    work = tmp_path / "divergent-work"
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


def test_script_reuses_alan_deploy_refresh_objects():
    assert script.refresh is deploy_refresh_mod.refresh
    assert script.ShallowRepositoryError is deploy_refresh_mod.ShallowRepositoryError
    assert script.PushedShaAbsentError is deploy_refresh_mod.PushedShaAbsentError
    assert script.NotContainedError is deploy_refresh_mod.NotContainedError


def test_cli_success_writes_deploy_sha_to_github_env(tmp_path, monkeypatch):
    _origin, _seed, local, base_sha = _deploy_pair(tmp_path)
    github_env = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    rc = script._main(["--repo", str(local), "--pushed-sha", base_sha])

    assert rc == 0
    assert github_env.read_text(encoding="utf-8") == f"DEPLOY_SHA={base_sha}\n"


def test_cli_writes_no_github_env_line_on_failure(tmp_path, monkeypatch):
    _origin, _seed, local, _base_sha = _deploy_pair(tmp_path)
    github_env = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    rc = script._main(["--repo", str(local), "--pushed-sha", "a" * 40])

    assert rc == 1
    assert not github_env.exists()


def test_cli_reads_pushed_sha_from_github_sha_env_when_flag_omitted(tmp_path, monkeypatch):
    _origin, _seed, local, base_sha = _deploy_pair(tmp_path)
    github_env = tmp_path / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("GITHUB_SHA", base_sha)

    rc = script._main(["--repo", str(local)])

    assert rc == 0
    assert github_env.read_text(encoding="utf-8") == f"DEPLOY_SHA={base_sha}\n"


def test_cli_shallow_repository_exits_1(tmp_path, monkeypatch, capsys):
    origin, seed, _local, base_sha = _deploy_pair(tmp_path)
    (seed / "b.txt").write_text("b", encoding="utf-8")
    _run_git(["add", "-A"], seed)
    _run_git(["commit", "-q", "-m", "second"], seed)
    _run_git(["push", "-q", "origin", "main"], seed)
    shallow = tmp_path / "shallow"
    _run_git(["clone", "-q", "--depth", "1", "--no-local", str(origin), str(shallow)], tmp_path)
    monkeypatch.delenv("GITHUB_ENV", raising=False)

    rc = script._main(["--repo", str(shallow), "--pushed-sha", base_sha])

    assert rc == 1
    assert "shallow" in capsys.readouterr().err.lower()


def test_cli_pushed_sha_absent_exits_1(tmp_path, monkeypatch, capsys):
    _origin, _seed, local, _base_sha = _deploy_pair(tmp_path)
    monkeypatch.delenv("GITHUB_ENV", raising=False)

    rc = script._main(["--repo", str(local), "--pushed-sha", "a" * 40])

    assert rc == 1
    assert "not present" in capsys.readouterr().err.lower()


def test_cli_not_contained_exits_1(tmp_path, monkeypatch, capsys):
    origin, _seed, local, base_sha = _deploy_pair(tmp_path)
    divergent_sha = _push_divergent_commit(origin, tmp_path, base_sha)
    _run_git(["fetch", "-q", str(origin), divergent_sha], local)
    monkeypatch.delenv("GITHUB_ENV", raising=False)

    rc = script._main(["--repo", str(local), "--pushed-sha", divergent_sha])

    assert rc == 1
    assert "does not contain" in capsys.readouterr().err.lower()


def test_cli_distinguishes_absent_and_not_contained_messages(tmp_path, monkeypatch, capsys):
    origin, _seed, local, base_sha = _deploy_pair(tmp_path)
    divergent_sha = _push_divergent_commit(origin, tmp_path, base_sha)
    _run_git(["fetch", "-q", str(origin), divergent_sha], local)
    monkeypatch.delenv("GITHUB_ENV", raising=False)

    script._main(["--repo", str(local), "--pushed-sha", "a" * 40])
    absent_message = capsys.readouterr().err

    rc = script._main(["--repo", str(local), "--pushed-sha", divergent_sha])
    not_contained_message = capsys.readouterr().err

    assert rc == 1
    assert absent_message != not_contained_message
