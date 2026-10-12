from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from alan import operator_repos
from alan.engine_config import RepoIdentity
from alan.operator_checkout import legacy_checkout_path
from alan.operator_repos import RepoTarget
from tests.conftest import FakeGit, git_reply

_ORIGIN = "https://example.test/install-owner/install-repo.git"
_INSTALL = RepoIdentity(owner="install-owner", repo="install-repo")
_INSTALL_TARGET = RepoTarget(_INSTALL, _ORIGIN, True)


@pytest.fixture
def renames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    real_rename = os.rename

    def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls.append((os.fspath(src), os.fspath(dst)))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", spy)
    return calls


@pytest.fixture
def legacy() -> Path:
    path = legacy_checkout_path()
    (path / ".git").mkdir(parents=True)
    (path / "local-work.txt").write_text("operator left this here")
    return path


@pytest.fixture
def target() -> Path:
    return operator_repos.repo_path(_INSTALL)


def test_the_legacy_checkout_is_renamed_into_the_new_layout_with_its_local_work(
    fake_git: FakeGit, legacy: Path, target: Path, renames: list[tuple[str, str]]
) -> None:
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert renames == [(str(legacy), str(target))]
    assert not legacy.exists()
    assert (target / ".git").is_dir()
    assert (target / "local-work.txt").read_text() == "operator left this here"
    assert result.path == target
    assert result.warning is None
    assert fake_git.verbs() == ["fetch", "merge"]


def test_a_migrated_checkout_is_updated_in_place_rather_than_cloned(
    fake_git: FakeGit, legacy: Path
) -> None:
    del legacy
    operator_repos.ensure_repo(_INSTALL_TARGET)
    assert fake_git.calls_of("clone") == []


def test_the_new_parent_directory_is_created_for_the_rename(
    fake_git: FakeGit, legacy: Path, target: Path
) -> None:
    del fake_git, legacy
    assert not target.parent.exists()
    operator_repos.ensure_repo(_INSTALL_TARGET)
    assert target.parent.is_dir()


def test_a_second_ensure_does_not_rename_again(
    fake_git: FakeGit, legacy: Path, renames: list[tuple[str, str]]
) -> None:
    del fake_git, legacy
    operator_repos.ensure_repo(_INSTALL_TARGET)
    operator_repos.ensure_repo(_INSTALL_TARGET)
    assert len(renames) == 1


def test_when_the_target_already_exists_the_legacy_folder_is_left_untouched_with_a_warning(
    fake_git: FakeGit, legacy: Path, target: Path, renames: list[tuple[str, str]]
) -> None:
    (target / ".git").mkdir(parents=True)
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert renames == []
    assert (legacy / ".git").is_dir()
    assert (legacy / "local-work.txt").read_text() == "operator left this here"
    assert result.path == target
    assert result.warning is not None
    assert "left in place" in result.warning
    assert str(legacy) in result.warning
    assert fake_git.verbs() == ["fetch", "merge"]


def test_the_collision_warning_comes_before_an_update_warning(
    fake_git: FakeGit, legacy: Path, target: Path
) -> None:
    del legacy
    (target / ".git").mkdir(parents=True)
    fake_git.fail("fetch", "install-repo", git_reply(stderr="boom"))
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert result.warning is not None
    first, second = result.warning.split("; ", 1)
    assert "left in place" in first
    assert "checkout not updated" in second


def test_a_target_directory_without_a_checkout_still_leaves_the_legacy_folder_alone(
    fake_git: FakeGit, legacy: Path, target: Path, renames: list[tuple[str, str]]
) -> None:
    target.mkdir(parents=True)
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert renames == []
    assert (legacy / "local-work.txt").is_file()
    assert result.warning is not None
    assert "left in place" in result.warning
    assert fake_git.verbs() == ["clone"]


def test_a_legacy_folder_without_a_git_directory_is_not_moved(
    fake_git: FakeGit, target: Path, renames: list[tuple[str, str]]
) -> None:
    folder = legacy_checkout_path()
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("not a checkout")
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert renames == []
    assert (folder / "notes.txt").read_text() == "not a checkout"
    assert result.warning is None
    assert fake_git.verbs() == ["clone"]
    assert (target / ".git").is_dir()


def test_without_a_legacy_folder_nothing_is_renamed(
    fake_git: FakeGit, renames: list[tuple[str, str]]
) -> None:
    operator_repos.ensure_repo(_INSTALL_TARGET)
    assert renames == []
    assert fake_git.verbs() == ["clone"]


def test_only_the_installs_own_repository_migrates_the_legacy_folder(
    fake_git: FakeGit, legacy: Path, renames: list[tuple[str, str]]
) -> None:
    other = RepoTarget(
        RepoIdentity(owner="acme", repo="widget"), "https://github.com/acme/widget.git", False
    )
    operator_repos.ensure_repo(other)
    assert renames == []
    assert (legacy / ".git").is_dir()
    assert fake_git.verbs() == ["clone"]


def test_a_rename_that_fails_warns_leaves_the_legacy_folder_and_clones_fresh(
    fake_git: FakeGit, legacy: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", refuse)
    result = operator_repos.ensure_repo(_INSTALL_TARGET)
    assert (legacy / "local-work.txt").read_text() == "operator left this here"
    assert result.warning is not None
    assert "not moved" in result.warning
    assert "Invalid cross-device link" in result.warning
    assert (target / ".git").is_dir()
    assert fake_git.verbs() == ["clone"]


def test_nothing_is_ever_deleted_during_a_migration(
    fake_git: FakeGit, legacy: Path, target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_git
    removed: list[object] = []
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **kw: removed.append(a))
    operator_repos.ensure_repo(_INSTALL_TARGET)
    assert removed == []
    assert (target / "local-work.txt").is_file()
    assert not legacy.exists()
