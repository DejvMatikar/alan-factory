from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alan import deploy_observe

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


def _healthy_mock() -> MagicMock:
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = 200
    m.read.return_value = json.dumps({"ok": True}).encode()
    return m


def test_checkout_head_returns_current_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, ["a.txt"], "base")
    assert deploy_observe.checkout_head(repo) == sha


def test_checkout_head_returns_none_when_not_a_repo(tmp_path: Path) -> None:
    assert deploy_observe.checkout_head(tmp_path) is None


def test_checkout_head_returns_none_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert deploy_observe.checkout_head(tmp_path) is None


def test_checkout_head_returns_none_on_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: object, **_k: object) -> object:
        raise OSError("git missing")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert deploy_observe.checkout_head(tmp_path) is None


def test_active_slot_sha_reads_symlinked_slot(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    slot = web_dir / "dist.a"
    slot.mkdir(parents=True)
    (slot / ".deploy_sha").write_text(_A_SHA, encoding="utf-8")
    (web_dir / "dist").symlink_to("dist.a")
    assert deploy_observe.active_slot_sha(web_dir) == _A_SHA


def test_active_slot_sha_reads_real_directory(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    dist = web_dir / "dist"
    dist.mkdir(parents=True)
    (dist / ".deploy_sha").write_text(_A_SHA, encoding="utf-8")
    assert deploy_observe.active_slot_sha(web_dir) == _A_SHA


def test_active_slot_sha_none_when_missing(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    assert deploy_observe.active_slot_sha(web_dir) is None


def test_active_slot_sha_none_when_file_empty(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    dist = web_dir / "dist"
    dist.mkdir(parents=True)
    (dist / ".deploy_sha").write_text("", encoding="utf-8")
    assert deploy_observe.active_slot_sha(web_dir) is None


def test_live_served_sha_delegates_to_served_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_observe, "served_sha", lambda: _A_SHA)
    assert deploy_observe.live_served_sha() == _A_SHA


def test_probe_health_ok() -> None:
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        assert deploy_observe.probe_health("http://x/health") is True


def test_probe_health_not_ok_body() -> None:
    m = _healthy_mock()
    m.read.return_value = json.dumps({"ok": False}).encode()
    with patch("urllib.request.urlopen", return_value=m):
        assert deploy_observe.probe_health("http://x/health") is False


def test_probe_health_non_200_status() -> None:
    m = _healthy_mock()
    m.status = 204
    m.read.return_value = b""
    with patch("urllib.request.urlopen", return_value=m):
        assert deploy_observe.probe_health("http://x/health") is False


def test_probe_health_non_dict_response() -> None:
    m = _healthy_mock()
    m.read.return_value = json.dumps(["not", "a", "dict"]).encode()
    with patch("urllib.request.urlopen", return_value=m):
        assert deploy_observe.probe_health("http://x/health") is False


def test_probe_health_invalid_json() -> None:
    m = _healthy_mock()
    m.read.return_value = b"not-json"
    with patch("urllib.request.urlopen", return_value=m):
        assert deploy_observe.probe_health("http://x/health") is False


def test_probe_health_url_error() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert deploy_observe.probe_health("http://x/health") is False


def test_probe_health_os_error() -> None:
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        assert deploy_observe.probe_health("http://x/health") is False


def test_is_ancestor_true_for_self(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, ["a.txt"], "base")
    assert deploy_observe.is_ancestor(repo, sha, sha) is True


def test_is_ancestor_true_for_real_ancestor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["a.txt"], "base")
    head_sha = _commit(repo, ["b.txt"], "head")
    assert deploy_observe.is_ancestor(repo, base_sha, head_sha) is True


def test_is_ancestor_false_for_unrelated_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, ["a.txt"], "base")
    assert deploy_observe.is_ancestor(repo, "f" * 40, sha) is False


def test_is_ancestor_false_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert deploy_observe.is_ancestor(tmp_path, _A_SHA, _A_SHA) is False


def test_classify_arm_restart_free_when_only_safe_paths_changed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["alan/server.py"], "base")
    safe_sha = _commit(repo, ["docs/x.md", "tests/test_y.py"], "safe change")
    assert deploy_observe.classify_arm(repo, safe_sha) == "restart_free"


def test_classify_arm_restart_required_when_hot_path_changed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["alan/server.py"], "base")
    hot_sha = _commit(repo, ["alan/dispatcher.py"], "hot change")
    assert deploy_observe.classify_arm(repo, hot_sha) == "restart_required"


def test_classify_arm_restart_required_when_mixed_paths_changed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["alan/server.py"], "base")
    mixed_sha = _commit(repo, ["docs/x.md", "alan/dispatcher.py"], "mixed change")
    assert deploy_observe.classify_arm(repo, mixed_sha) == "restart_required"


def test_classify_arm_restart_required_on_diff_tree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_a: object, **_k: object) -> object:
        raise OSError("git missing")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert deploy_observe.classify_arm(tmp_path, _A_SHA) == "restart_required"


def test_observe_restart_free_arm_checks_checkout_and_slot(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["alan/server.py"], "base")
    safe_sha = _commit(repo, ["docs/x.md"], "safe change")
    web_dir = tmp_path / "web"
    dist = web_dir / "dist"
    dist.mkdir(parents=True)
    (dist / ".deploy_sha").write_text(safe_sha, encoding="utf-8")

    def _fail_if_called(url: str) -> bool:
        raise AssertionError("probe_health must not be called for the restart-free arm")

    with patch.object(deploy_observe, "probe_health", _fail_if_called):
        observation = deploy_observe.observe(
            safe_sha, health_url="http://x/health", repo=repo, web_dir=web_dir
        )
    assert observation.arm == "restart_free"
    assert observation.checkout_contains_merge is True
    assert observation.slot_contains_merge is True
    assert observation.served_contains_merge is False
    assert observation.health_ok is True


def test_observe_restart_free_arm_false_when_slot_stale(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["alan/server.py"], "base")
    safe_sha = _commit(repo, ["docs/x.md"], "safe change")
    web_dir = tmp_path / "web"
    dist = web_dir / "dist"
    dist.mkdir(parents=True)
    (dist / ".deploy_sha").write_text(base_sha, encoding="utf-8")

    observation = deploy_observe.observe(
        safe_sha, health_url=None, repo=repo, web_dir=web_dir
    )
    assert observation.checkout_contains_merge is True
    assert observation.slot_contains_merge is False


def test_observe_restart_required_arm_checks_served_and_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, ["alan/server.py"], "base")
    hot_sha = _commit(repo, ["alan/dispatcher.py"], "hot change")
    monkeypatch.setattr(deploy_observe, "served_sha", lambda: hot_sha)
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        observation = deploy_observe.observe(
            hot_sha, health_url="http://x/health", repo=repo, web_dir=tmp_path / "web"
        )
    assert observation.arm == "restart_required"
    assert observation.served_contains_merge is True
    assert observation.health_ok is True
    assert observation.checkout_contains_merge is False
    assert observation.slot_contains_merge is False
    del base_sha


def test_observe_restart_required_arm_health_ok_true_when_url_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    _commit(repo, ["alan/server.py"], "base")
    hot_sha = _commit(repo, ["alan/dispatcher.py"], "hot change")
    monkeypatch.setattr(deploy_observe, "served_sha", lambda: None)

    def _fail_if_called(url: str) -> bool:
        raise AssertionError("probe_health must not be called when health_url is None")

    with patch.object(deploy_observe, "probe_health", _fail_if_called):
        observation = deploy_observe.observe(
            hot_sha, health_url=None, repo=repo, web_dir=tmp_path / "web"
        )
    assert observation.health_ok is True
    assert observation.served_contains_merge is False
