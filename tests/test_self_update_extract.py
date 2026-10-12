from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

from alan.self_update import _extract
from tests.self_update_fixtures import commit, git


def init_repo(path: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True
    )


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


@pytest.fixture(autouse=True)
def _redirect_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def test_build_kit_extracts_only_the_alan_subtree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    commit(repo, "alan/marker.py", "VALUE = 1\n")
    commit(repo, "outside.txt", "not archived\n")
    head = git(repo, "rev-parse", "HEAD")
    kit = _extract.build_kit(repo, head)
    assert (kit / "alan" / "marker.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (kit / "outside.txt").exists()


def test_build_kit_extracts_the_content_of_an_older_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    first = commit(repo, "alan/marker.py", "VALUE = 1\n")
    commit(repo, "alan/marker.py", "VALUE = 2\n")
    kit = _extract.build_kit(repo, first)
    assert (kit / "alan" / "marker.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_build_kit_refuses_a_ref_that_is_not_a_commit_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    commit(repo, "alan/marker.py", "VALUE = 1\n")
    with pytest.raises(ValueError, match="non-commit-sha"):
        _extract.build_kit(repo, "--upload-pack=evil")


def test_member_is_safe_rejects_a_symlink_member(tmp_path: Path) -> None:
    member = tarfile.TarInfo(name="alan/evil-link")
    member.type = tarfile.SYMTYPE
    assert not _extract._member_is_safe(member, tmp_path)


def test_member_is_safe_rejects_a_path_that_escapes_the_destination(tmp_path: Path) -> None:
    member = tarfile.TarInfo(name="../escape.txt")
    assert not _extract._member_is_safe(member, tmp_path)


def test_member_is_safe_accepts_a_plain_file_inside_the_destination(tmp_path: Path) -> None:
    member = tarfile.TarInfo(name="alan/marker.py")
    assert _extract._member_is_safe(member, tmp_path)


def test__exec_refuses_a_non_absolute_interpreter_path() -> None:
    with pytest.raises(ValueError, match="non-absolute"):
        _extract._exec("python3", ["-S", "-m", "alan.self_update", "apply"])


def test_extract_and_exec_removes_a_stale_kit_directory_before_building_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    commit(repo, "alan/marker.py", "VALUE = 1\n")
    head = git(repo, "rev-parse", "HEAD")
    stale = Path(tempfile.mkdtemp(prefix=_extract._KIT_PREFIX, dir=_extract._kit_root()))
    (stale / "leftover.txt").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(_extract, "_exec", lambda python, argv: None)
    original_cwd = Path.cwd()
    try:
        _extract.extract_and_exec(repo, head, ["apply"])
    finally:
        os.chdir(original_cwd)
    assert not stale.exists()


def test__exec_invokes_os_execv_with_the_python_and_full_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    _extract._exec("/usr/bin/python3", ["-S", "-m", "alan.self_update", "apply"])
    assert calls == [
        ("/usr/bin/python3", ["/usr/bin/python3", "-S", "-m", "alan.self_update", "apply"])
    ]


def test__exec_refuses_an_argv_element_that_looks_like_an_unrecognized_option() -> None:
    with pytest.raises(ValueError, match="unrecognized option"):
        _extract._exec(
            "/usr/bin/python3", ["-S", "-m", "alan.self_update", "apply", "--evil"]
        )


def test__exec_accepts_the_sha_force_and_queue_db_path_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    argv = [
        "-S", "-m", "alan.self_update", "apply",
        "--sha", "a" * 40, "--force", "--queue-db-path", "/repo/state/queue.db",
    ]
    _extract._exec("/usr/bin/python3", argv)
    assert calls == [("/usr/bin/python3", ["/usr/bin/python3", *argv])]


def test_extract_and_exec_changes_into_the_kit_and_execs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    commit(repo, "alan/marker.py", "VALUE = 1\n")
    head = git(repo, "rev-parse", "HEAD")
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(_extract, "_exec", lambda python, argv: calls.append((python, argv)))
    original_cwd = Path.cwd()
    try:
        _extract.extract_and_exec(repo, head, ["apply", "--scheduled"])
        assert (Path.cwd() / "alan" / "marker.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    finally:
        os.chdir(original_cwd)
    assert len(calls) == 1
    python, argv = calls[0]
    assert python == os.path.realpath(sys.executable)
    assert argv == ["-S", "-m", "alan.self_update", "apply", "--scheduled"]
