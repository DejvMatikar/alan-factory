from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.self_update_child import (
    gh_green_shim,
    git_fetch_shim,
    refusing_shim,
    run_self_update,
    sealed_env,
    start_health_server,
    stateful_launchctl_shim,
    stop_health_server,
    succeeding_shim,
)
from tests.self_update_fixtures import git, redirect_env
from tests.self_update_run_fakes import (
    build_good_repo,
    good_rollback_source,
    push_sabotaged_commit,
    read_rejected,
    write_gh_envelopes,
    write_journal,
    write_marker,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


def journal_phase(state_root: Path) -> str:
    path = state_root.parent / "deploy" / "self_update_journal.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload["phase"])


def test_a_sabotaged_target_is_rolled_back_by_the_pre_apply_commit_in_the_same_run(
    tmp_path: Path,
) -> None:
    repo = build_good_repo(tmp_path)
    bad_sha = push_sabotaged_commit(repo, tmp_path, render_fails=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runs_file, checks_file = write_gh_envelopes(tmp_path, bad_sha)
    gh_green_shim(bin_dir, runs_file, checks_file)
    stateful_launchctl_shim(bin_dir, tmp_path / "launchctl_state.txt", loaded=True)
    succeeding_shim(bin_dir, "uv")
    succeeding_shim(bin_dir, "npm")
    git_fetch_shim(bin_dir, repo.bare)
    health_url, server = start_health_server(repo.good_sha)
    try:
        env = sealed_env(tmp_path, bin_dir)
        state_root = Path(env["ALAN_STATE_ROOT"])
        write_marker(state_root, repo.good_sha)
        result = run_self_update(
            repo.clone, env, "apply", "--plist-path", str(tmp_path / "ai.alan.plist"),
            "--health-url", health_url,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert git(repo.clone, "rev-parse", "HEAD") == repo.good_sha
        restored = (repo.clone / "alan" / "self_update" / "_rollback.py").read_text(
            encoding="utf-8"
        )
        assert restored == good_rollback_source()
        assert read_rejected(state_root) == frozenset({bad_sha})
        assert journal_phase(state_root) == "rolled_back"
    finally:
        stop_health_server(server)


def test_a_later_run_rolls_back_a_leftover_using_the_previous_commits_code(
    tmp_path: Path,
) -> None:
    repo = build_good_repo(tmp_path)
    bad_sha = push_sabotaged_commit(repo, tmp_path, render_fails=True)
    git(repo.clone, "fetch", "-q", str(repo.bare), "main")
    git(repo.clone, "checkout", "-fB", "main", bad_sha)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("launchctl", "uv", "npm", "gh"):
        refusing_shim(bin_dir, name)
    env = sealed_env(tmp_path, bin_dir)
    state_root = Path(env["ALAN_STATE_ROOT"])
    write_journal(
        state_root,
        {
            "phase": "rolling_back",
            "target_sha": bad_sha,
            "previous_sha": repo.good_sha,
            "previous_slot": "dist.a",
            "restarted": False,
        },
    )
    result = run_self_update(
        repo.clone, env, "apply", "--plist-path", str(tmp_path / "ai.alan.plist"),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert git(repo.clone, "rev-parse", "HEAD") == repo.good_sha
    restored = (repo.clone / "alan" / "self_update" / "_rollback.py").read_text(encoding="utf-8")
    assert restored == good_rollback_source()
    assert read_rejected(state_root) == frozenset({bad_sha})
    assert journal_phase(state_root) == "rolled_back"
