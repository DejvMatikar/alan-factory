from __future__ import annotations

from pathlib import Path

import pytest

from alan.teams import implementation as impl_mod
from alan.teams._pr_push import PushOutcome, PushResult
from alan.workspace import Workspace, create
from alan.worktree import WorktreeHandle


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("impl-push-result-task", "do the thing", root=tmp_path / "ws")


@pytest.fixture(autouse=True)
def _logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_IMPL_DRY_RUN", raising=False)


def _handle(tmp_path: Path) -> WorktreeHandle:
    return WorktreeHandle(
        path=tmp_path / "wt", base_ref="deadbeef", branch="teams/impl-push-result-task-inv_001"
    )


def test_no_commits_returns_pushed_true_with_existing_pr(
    workspace: Workspace, tmp_path: Path
) -> None:
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", [], existing_pr=7
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=7, credential_failure=None, pushed=True
    )


def test_no_commits_returns_pushed_true_without_existing_pr(
    workspace: Workspace, tmp_path: Path
) -> None:
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", [], existing_pr=None
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=None, credential_failure=None, pushed=True
    )


def test_dry_run_returns_pushed_true(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_IMPL_DRY_RUN", "1")
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", ["abc1234 add work"], existing_pr=None
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=None, credential_failure=None, pushed=True
    )


def test_push_failure_returns_pushed_false(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        impl_mod, "_push_branch",
        lambda *_a, **_k: PushResult(
            outcome=PushOutcome.PUSH_FAILED, credential_failure="expired_token"
        ),
    )

    def _open_pr_should_not_run(*_a: object, **_k: object) -> int | None:
        raise AssertionError("gh pr create should not run after a failed push")

    monkeypatch.setattr(impl_mod, "_open_pr", _open_pr_should_not_run)
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", ["abc1234 add work"], existing_pr=None
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=None, credential_failure="expired_token", pushed=False
    )


def test_existing_pr_reconcile_returns_pushed_true(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        impl_mod, "_push_branch",
        lambda *_a, **_k: PushResult(outcome=PushOutcome.PUSHED),
    )

    def _open_pr_should_not_run(*_a: object, **_k: object) -> int | None:
        raise AssertionError("gh pr create should not run when reusing an existing PR")

    monkeypatch.setattr(impl_mod, "_open_pr", _open_pr_should_not_run)
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", ["abc1234 add work"], existing_pr=55
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=55, credential_failure=None, pushed=True
    )


def test_new_pr_opened_returns_pushed_true(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        impl_mod, "_push_branch",
        lambda *_a, **_k: PushResult(outcome=PushOutcome.PUSHED),
    )
    monkeypatch.setattr(impl_mod, "_open_pr", lambda *_a, **_k: 88)
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", ["abc1234 add work"], existing_pr=None
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=88, credential_failure=None, pushed=True
    )


def test_new_pr_number_unparseable_still_returns_pushed_true(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        impl_mod, "_push_branch",
        lambda *_a, **_k: PushResult(outcome=PushOutcome.PUSHED),
    )
    monkeypatch.setattr(impl_mod, "_open_pr", lambda *_a, **_k: None)
    result = impl_mod._push_changes(
        workspace, _handle(tmp_path), "synthesis", ["abc1234 add work"], existing_pr=None
    )
    assert result == impl_mod.PushChangesResult(
        pr_number=None, credential_failure=None, pushed=True
    )
