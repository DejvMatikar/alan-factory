from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import base_ref
from alan.teams import _changed_e2e


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _init_bare(path: Path) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "init", "-q", "--bare", "-b", "main", str(path)],
        check=True, capture_output=True, text=True,
    )


def _clone(bare: Path, dest: Path) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", "-q", str(bare), str(dest)],
        check=True, capture_output=True, text=True,
    )


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


def test_local_diff_files_uses_the_provided_base(synthetic_repo: Path) -> None:
    assert _changed_e2e.local_diff_files(synthetic_repo, "HEAD") == frozenset()


def test_build_changed_e2e_scenario_reports_unresolved_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch, synthetic_repo: Path
) -> None:
    monkeypatch.setattr(base_ref, "resolve", lambda *_a, **_kw: None)
    selection = _changed_e2e.build_changed_e2e_scenario(synthetic_repo)
    assert selection == _changed_e2e.ChangedE2ESelection(
        scenario=None, base_unresolved=True
    )


def test_build_changed_e2e_scenario_refreshes_a_stale_local_base_before_diffing(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "origin.git"
    _init_bare(bare)

    seed = tmp_path / "seed"
    _clone(bare, seed)
    _git(seed, "config", "user.email", "test@example.com")
    _git(seed, "config", "user.name", "Test")
    _commit_file(seed, "README.md", "hello\n", "initial")
    _git(seed, "push", "-q", "origin", "main")
    old_sha = _git(seed, "rev-parse", "HEAD").stdout.strip()
    _commit_file(seed, "alan/web/e2e/unrelated.spec.ts", "// spec\n", "add unrelated spec")
    _git(seed, "push", "-q", "origin", "main")

    checkout = tmp_path / "checkout"
    _clone(bare, checkout)
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test")
    _git(checkout, "checkout", "-q", "-b", "feature")
    _commit_file(checkout, "alan/web/e2e/a.spec.ts", "// spec\n", "add a.spec.ts")

    _git(checkout, "update-ref", "refs/remotes/origin/main", old_sha)

    stale_diff = _changed_e2e.local_diff_files(checkout, "origin/main")
    assert stale_diff == frozenset(
        {"alan/web/e2e/unrelated.spec.ts", "alan/web/e2e/a.spec.ts"}
    )

    selection = _changed_e2e.build_changed_e2e_scenario(checkout)

    assert selection.base_unresolved is False
    assert selection.scenario is not None
    assert selection.scenario.when == "npm --prefix alan/web run e2e -- e2e/a.spec.ts"
