from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.workflow import WorkflowDoc


def _deleted_workspace(ws_root: Path) -> workspace_mod.Workspace:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    workspace_mod.delete(ws.task_id, root=ws_root)
    return ws


def test_create_invocation_dir_refuses_when_workspace_gone(ws_root: Path) -> None:
    ws = _deleted_workspace(ws_root)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.create_invocation_dir(ws)
    assert not ws.path.exists()


def test_create_invocation_dir_still_works_when_workspace_present(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    inv_id = workflow_mod.create_invocation_dir(ws)
    assert (ws.path / "invocations" / inv_id).is_dir()


def test_create_invocation_dir_maps_a_delete_landing_mid_call_to_workflow_error(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    real_mkdir = Path.mkdir

    def racing_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self.name.startswith("inv_"):
            shutil.rmtree(ws.path)
        real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.create_invocation_dir(ws)
    assert not ws.path.exists()


def _mkdir_failing_with(code: int, name_predicate: Callable[[str], bool]) -> Callable[..., None]:
    real_mkdir = Path.mkdir

    def failing_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if name_predicate(self.name):
            raise OSError(code, os.strerror(code), str(self))
        real_mkdir(self, mode, parents=parents, exist_ok=exist_ok)

    return failing_mkdir


def _is_slot_mkdir(name: str) -> bool:
    return name.startswith("inv_")


def _is_base_invocations_mkdir(name: str) -> bool:
    return name == "invocations"


def test_create_invocation_dir_maps_an_einval_delete_race_to_workflow_error(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    monkeypatch.setattr(Path, "mkdir", _mkdir_failing_with(errno.EINVAL, _is_slot_mkdir))
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.create_invocation_dir(ws)


def test_create_invocation_dir_maps_an_eexist_delete_race_on_the_base_dir_to_workflow_error(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    monkeypatch.setattr(
        Path, "mkdir", _mkdir_failing_with(errno.EEXIST, _is_base_invocations_mkdir)
    )
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.create_invocation_dir(ws)


def test_create_invocation_dir_reraises_a_storage_failure_rather_than_calling_it_missing(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    monkeypatch.setattr(Path, "mkdir", _mkdir_failing_with(errno.ENOSPC, _is_slot_mkdir))
    with pytest.raises(OSError) as excinfo:
        workflow_mod.create_invocation_dir(ws)
    assert excinfo.value.errno == errno.ENOSPC


def test_save_node_result_refuses_when_workspace_gone(ws_root: Path) -> None:
    ws = _deleted_workspace(ws_root)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.save_node_result(
            ws, "inv_001", "role", role="role", model="sonnet", result_text="x"
        )
    assert not ws.path.exists()


def test_save_node_result_still_works_when_workspace_present(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    path = workflow_mod.save_node_result(
        ws, "inv_001", "role", role="role", model="sonnet", result_text="x"
    )
    assert path.is_file()


def test_save_node_config_refuses_when_workspace_gone(ws_root: Path) -> None:
    ws = _deleted_workspace(ws_root)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.save_node_config(
            ws, "inv_001", "role", system_prompt="s", user_text="u",
            allowed_tools=[], max_turns=1, permission_mode="auto", model="sonnet",
            turns_source="static", derived_max_turns=1,
        )
    assert not ws.path.exists()


def test_save_node_config_still_works_when_workspace_present(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    path = workflow_mod.save_node_config(
        ws, "inv_001", "role", system_prompt="s", user_text="u",
        allowed_tools=[], max_turns=1, permission_mode="auto", model="sonnet",
        turns_source="static", derived_max_turns=1,
    )
    assert path.is_file()


def test_save_invocation_report_refuses_when_workspace_gone(ws_root: Path) -> None:
    ws = _deleted_workspace(ws_root)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.save_invocation_report(ws, "inv_001", "report text")
    assert not ws.path.exists()


def test_save_invocation_report_still_works_when_workspace_present(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    path = workflow_mod.save_invocation_report(ws, "inv_001", "report text")
    assert path.read_text(encoding="utf-8") == "report text"


def _doc(task_id: str) -> WorkflowDoc:
    return WorkflowDoc(
        id="inv_001",
        namespace=f"alan.workspace.{task_id}",
        team="research",
        status="running",
        started_at=datetime.now(UTC),
    )


def test_save_workflow_refuses_when_workspace_gone(ws_root: Path) -> None:
    ws = _deleted_workspace(ws_root)
    with pytest.raises(workflow_mod.WorkflowError, match="workspace directory missing"):
        workflow_mod.save_workflow(ws, _doc(ws.task_id))
    assert not ws.path.exists()


def test_save_workflow_still_works_when_workspace_present(ws_root: Path) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    path = workflow_mod.save_workflow(ws, _doc(ws.task_id))
    assert path.is_file()
