from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import process_runner
from alan.teams import _pr_closing
from alan.teams import implementation as impl
from alan.workspace import Workspace, create
from alan.worktree import WorktreeHandle

_GhCreateFake = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("salvage-pr-task", "Fix the thing.", root=tmp_path / "ws")


def _handle(tmp_path: Path) -> WorktreeHandle:
    return WorktreeHandle(path=tmp_path / "wt", base_ref="deadbeef", branch="teams/t-inv_1")


def _fake_gh_create(pr_number: int) -> _GhCreateFake:
    def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"https://github.com/o/r/pull/{pr_number}\n", stderr="",
        )

    return fake


def test_open_pr_uses_salvaged_body_when_every_commit_is_alan_salvage(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle(tmp_path)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_closing, "is_salvage_only", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_closing, "compose_salvaged_pr_body", lambda *_a, **_kw: "salvaged body")

    def boom_compose(*_a: object, **_kw: object) -> str:
        raise AssertionError("compose_pr_body should not be called for a salvage-only PR")

    monkeypatch.setattr(_pr_closing, "compose_pr_body", boom_compose)

    seen: dict[str, object] = {}
    fake = _fake_gh_create(9)

    def spy(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["body"] = cmd[cmd.index("--body") + 1]
        return fake(cmd, **kwargs)

    monkeypatch.setattr(process_runner, "run_subprocess", spy)

    pr_number = impl._open_pr(handle, workspace, "worker synthesis")  # noqa: SLF001

    assert pr_number == 9
    assert seen["body"] == "salvaged body"


def test_open_pr_uses_worker_synthesis_body_when_not_salvage_only(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle(tmp_path)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_closing, "is_salvage_only", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_closing, "compose_pr_body", lambda *_a, **_kw: "worker body")

    def boom_compose_salvaged(*_a: object, **_kw: object) -> str:
        raise AssertionError("compose_salvaged_pr_body should not be called")

    monkeypatch.setattr(_pr_closing, "compose_salvaged_pr_body", boom_compose_salvaged)

    seen: dict[str, object] = {}
    fake = _fake_gh_create(11)

    def spy(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["body"] = cmd[cmd.index("--body") + 1]
        return fake(cmd, **kwargs)

    monkeypatch.setattr(process_runner, "run_subprocess", spy)

    pr_number = impl._open_pr(handle, workspace, "worker synthesis")  # noqa: SLF001

    assert pr_number == 11
    assert seen["body"] == "worker body"


def test_open_pr_uses_handle_branch_and_path_for_gh_create(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle(tmp_path)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(_pr_closing, "is_salvage_only", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_closing, "compose_pr_body", lambda *_a, **_kw: "worker body")

    seen: dict[str, object] = {}
    fake = _fake_gh_create(13)

    def spy(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["cwd"] = kwargs.get("cwd")
        seen["head"] = cmd[cmd.index("--head") + 1]
        return fake(cmd, **kwargs)

    monkeypatch.setattr(process_runner, "run_subprocess", spy)

    impl._open_pr(handle, workspace, "worker synthesis")  # noqa: SLF001

    assert seen["cwd"] == str(handle.path)
    assert seen["head"] == handle.branch
