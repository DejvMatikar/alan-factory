from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import deploy_signal


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "a@b.c"],
        ["git", "config", "user.name", "test"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)


def _commit(repo: Path, rel_path: str, message: str) -> str:
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_merge_is_served_false_when_served_sha_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: None)
    assert deploy_signal.merge_is_served("a" * 40, repo=tmp_path) is False


def test_merge_is_served_true_when_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = _commit(repo, "a.txt", "base")
    head_sha = _commit(repo, "b.txt", "head")
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: head_sha)
    assert deploy_signal.merge_is_served(base_sha, repo=repo) is True


def test_merge_is_served_false_when_not_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = _commit(repo, "a.txt", "base")
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: base_sha)
    unrelated_sha = "f" * 40
    assert deploy_signal.merge_is_served(unrelated_sha, repo=repo) is False


def test_merge_is_served_true_when_served_equals_merge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    head_sha = _commit(repo, "a.txt", "base")
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: head_sha)
    assert deploy_signal.merge_is_served(head_sha, repo=repo) is True


def test_merge_is_served_false_on_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: "b" * 40)

    def _raise_timeout(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    assert deploy_signal.merge_is_served("a" * 40, repo=tmp_path) is False


def test_merge_is_served_false_on_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(deploy_signal, "served_sha", lambda: "b" * 40)

    def _raise_oserror(*_a: object, **_k: object) -> object:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise_oserror)
    assert deploy_signal.merge_is_served("a" * 40, repo=tmp_path) is False


def test_is_ancestor_true_for_self(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    sha = _commit(repo, "a.txt", "base")
    assert deploy_signal._is_ancestor(repo, sha, sha) is True  # noqa: SLF001


def test_is_ancestor_false_returncode_does_not_raise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    sha = _commit(repo, "a.txt", "base")
    assert deploy_signal._is_ancestor(repo, "f" * 40, sha) is False  # noqa: SLF001
