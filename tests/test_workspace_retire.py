from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from alan import workspace as workspace_mod


def test_exists_true_for_a_created_workspace(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    assert workspace_mod.exists(ws.task_id, root=ws_root) is True


def test_exists_false_for_unknown_workspace(ws_root: Path) -> None:
    assert workspace_mod.exists("ghost", root=ws_root) is False


def test_exists_false_when_root_directory_absent(tmp_path: Path) -> None:
    assert workspace_mod.exists("ghost", root=tmp_path / "no-such-root") is False


def test_exists_tracks_deletion(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    assert workspace_mod.exists(ws.task_id, root=ws_root) is True
    workspace_mod.delete(ws.task_id, root=ws_root)
    assert workspace_mod.exists(ws.task_id, root=ws_root) is False


def test_exists_false_when_directory_present_but_meta_missing(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    (ws.path / "meta.json").unlink()
    assert workspace_mod.exists(ws.task_id, root=ws_root) is False


def test_delete_removes_the_workspace_tree(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    workspace_mod.delete(ws.task_id, root=ws_root)
    assert not (ws_root / ws.task_id).exists()


def test_delete_raises_workspace_error_for_unknown_task(ws_root: Path) -> None:
    with pytest.raises(workspace_mod.WorkspaceError, match="not found"):
        workspace_mod.delete("ghost", root=ws_root)


def test_delete_maps_racing_os_error_to_workspace_error(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)

    def racing_rmtree(*_args: object, **_kwargs: object) -> None:
        raise NotADirectoryError(f"{ws.path}: raced by a concurrent writer")

    monkeypatch.setattr(shutil, "rmtree", racing_rmtree)
    with pytest.raises(workspace_mod.WorkspaceError, match="failed to delete workspace"):
        workspace_mod.delete(ws.task_id, root=ws_root)


def test_delete_maps_permission_error_to_workspace_error_not_a_bare_500(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(workspace_mod.WorkspaceError):
        workspace_mod.delete(ws.task_id, root=ws_root)
