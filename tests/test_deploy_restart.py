from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from alan import deploy_restart as rd

_A_SHA = "a" * 40


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, paths: list[str], message: str) -> str:
    for rel in paths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _write_marker(tmp_path: Path, sha: str) -> Path:
    marker = tmp_path / "restarted_sha"
    marker.write_text(sha, encoding="utf-8")
    return marker


def test_is_safe_path_docs_anywhere() -> None:
    assert rd.is_safe_path("docs/architecture/deploy.md") is True


def test_is_safe_path_tests_dir() -> None:
    assert rd.is_safe_path("tests/test_x.py") is True


def test_is_safe_path_frontend_dir() -> None:
    assert rd.is_safe_path("alan/web/src/App.tsx") is True


def test_is_safe_path_markdown_anywhere() -> None:
    assert rd.is_safe_path("README.md") is True
    assert rd.is_safe_path(".github/workflows/README.md") is True


def test_is_safe_path_backend_code_is_hot() -> None:
    assert rd.is_safe_path("alan/server.py") is False


def test_is_safe_path_webhooks_does_not_false_match_alan_web() -> None:
    assert rd.is_safe_path("alan/webhooks.py") is False


def test_is_safe_path_docs_prefix_guard() -> None:
    assert rd.is_safe_path("docsite/x.py") is False


def test_is_safe_path_unrecognized_top_level() -> None:
    assert rd.is_safe_path("Makefile") is False


def test_decide_safe_paths_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["alan/server.py"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["docs/x.md", "tests/test_y.py"], "safe change")
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is False
    assert reason == "safe_paths_only"
    assert set(paths) == {"docs/x.md", "tests/test_y.py"}


def test_decide_backend_code_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["alan/server.py"], "backend change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_deps_only_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["pyproject.toml", "uv.lock"], "deps change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_deploy_config_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["deploy/render_plist.sh"], "deploy config change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_repo_scripts_restart(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["scripts/x.py"], "scripts change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_ci_defs_restart(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, [".github/workflows/ci-deploy.yml"], "ci change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_mixed_safe_plus_hot_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["docs/x.md", "alan/server.py"], "mixed change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_unrecognized_path_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["Makefile"], "unrecognized change")
    restart_required, reason, _paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"


def test_decide_empty_diff_skips(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is False
    assert reason == "no_changes"
    assert paths == []


def test_decide_rename_out_of_hot_into_safe_still_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["alan/server_util.py"], "base")
    marker = _write_marker(tmp_path, base_sha)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "mv", "alan/server_util.py", "docs/server_util.py"], cwd=repo, check=True
    )
    subprocess.run(["git", "commit", "-q", "-m", "rename into docs"], cwd=repo, check=True)
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "hot_paths"
    assert set(paths) == {"alan/server_util.py", "docs/server_util.py"}


def test_changed_paths_invokes_git_diff_with_rename_detection_disabled(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    _commit(repo, ["docs/x.md"], "safe change")
    with patch("alan.deploy_restart.subprocess.run", wraps=subprocess.run) as mock_run:
        rd.changed_paths(repo, base_sha)
    argv = mock_run.call_args[0][0]
    assert "--no-renames" in argv
    assert "-z" in argv
    assert "--name-only" in argv
    assert argv[-1] == "HEAD"


def test_changed_paths_diffs_against_explicit_rev_not_local_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    mid_sha = _commit(repo, ["docs/x.md"], "safe change")
    _commit(repo, ["alan/server.py"], "hot change, later than mid_sha")
    paths = rd.changed_paths(repo, base_sha, mid_sha)
    assert paths == ["docs/x.md"]


def test_decide_honors_rev_pointing_at_non_head_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    mid_sha = _commit(repo, ["docs/x.md"], "safe change")
    _commit(repo, ["alan/server.py"], "hot change landed locally after mid_sha")
    restart_required, reason, paths = rd.decide(repo, marker, True, rev=mid_sha)
    assert restart_required is False
    assert reason == "safe_paths_only"
    assert paths == ["docs/x.md"]


def test_decide_missing_marker_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["README.md"], "base")
    marker = tmp_path / "does-not-exist"
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == rd.REASON_MISSING_MARKER
    assert paths == []


def test_decide_malformed_marker_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, "not-a-sha")
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "unknown_marker_sha"
    assert paths == []


def test_decide_unreachable_marker_sha_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, _A_SHA)
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "unknown_marker_sha"
    assert paths == []


def test_decide_service_not_loaded_restarts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    restart_required, reason, paths = rd.decide(repo, marker, False)
    assert restart_required is True
    assert reason == "service_not_loaded"
    assert paths == []


def test_classify_range_hot(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    _commit(repo, ["alan/server.py"], "hot change")
    restart_required, reason, paths = rd.classify_range(repo, base_sha)
    assert restart_required is True
    assert reason == "hot_paths"
    assert paths == ["alan/server.py"]


def test_classify_range_safe_paths_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["alan/server.py"], "base")
    _commit(repo, ["docs/x.md"], "safe change")
    restart_required, reason, paths = rd.classify_range(repo, base_sha)
    assert restart_required is False
    assert reason == "safe_paths_only"
    assert paths == ["docs/x.md"]


def test_classify_range_no_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    restart_required, reason, paths = rd.classify_range(repo, base_sha)
    assert restart_required is False
    assert reason == "no_changes"
    assert paths == []


def test_classify_range_honors_explicit_rev(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    mid_sha = _commit(repo, ["docs/x.md"], "safe change")
    _commit(repo, ["alan/server.py"], "hot change landed locally after mid_sha")
    restart_required, reason, paths = rd.classify_range(repo, base_sha, mid_sha)
    assert restart_required is False
    assert reason == "safe_paths_only"
    assert paths == ["docs/x.md"]


def test_decide_delegates_to_classify_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)
    _commit(repo, ["alan/server.py"], "hot change")
    sentinel = (False, "sentinel_reason", ["sentinel_path"])
    captured: dict[str, object] = {}

    def _fake_classify_range(
        repo_arg: Path, base_arg: str, rev_arg: str = "HEAD"
    ) -> tuple[bool, str, list[str]]:
        captured["args"] = (repo_arg, base_arg, rev_arg)
        return sentinel

    monkeypatch.setattr(rd, "classify_range", _fake_classify_range)
    result = rd.decide(repo, marker, True)
    assert result == sentinel
    assert captured["args"] == (repo, base_sha, "HEAD")


def test_decide_internal_exception_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["README.md"], "base")
    marker = _write_marker(tmp_path, base_sha)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(rd, "marker_reachable", _boom)
    restart_required, reason, paths = rd.decide(repo, marker, True)
    assert restart_required is True
    assert reason == "classifier_error"
    assert paths == []
