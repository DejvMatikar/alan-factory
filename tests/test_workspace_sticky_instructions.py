from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.workspace import Task, TaskStatus, Workspace, create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def ws(ws_root: Path) -> Workspace:
    return create("sticky-ws-task", "d", root=ws_root)


def test_set_sticky_instructions_persists_text_and_origin(
    ws: Workspace, ws_root: Path
) -> None:
    updated = workspace_mod.set_sticky_instructions(ws, ws.task_id, "do it", origin="dispatch")
    assert updated.meta.dispatch_instructions[ws.task_id].text == "do it"
    assert updated.meta.dispatch_instructions[ws.task_id].origin == "dispatch"
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions[ws.task_id].text == "do it"


def test_set_sticky_instructions_replaces_prior_value(ws: Workspace) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "first", origin="dispatch")
    ws3 = workspace_mod.set_sticky_instructions(ws2, ws.task_id, "second", origin="dispatch")
    assert ws3.meta.dispatch_instructions[ws.task_id].text == "second"
    assert len(ws3.meta.dispatch_instructions) == 1


def test_clear_sticky_instructions_removes_the_entry(ws: Workspace) -> None:
    ws2 = workspace_mod.set_sticky_instructions(ws, ws.task_id, "text", origin="dispatch")
    ws3 = workspace_mod.clear_sticky_instructions(ws2, ws.task_id)
    assert ws.task_id not in ws3.meta.dispatch_instructions


def test_clear_sticky_instructions_is_a_noop_when_absent(ws: Workspace) -> None:
    result = workspace_mod.clear_sticky_instructions(ws, ws.task_id)
    assert result is ws


def test_legacy_meta_json_without_dispatch_instructions_defaults_to_empty(
    ws: Workspace, ws_root: Path
) -> None:
    raw = (ws.path / "meta.json").read_text()
    data = json.loads(raw)
    del data["dispatch_instructions"]
    (ws.path / "meta.json").write_text(json.dumps(data))
    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.dispatch_instructions == {}


def test_set_sticky_instructions_raises_once_the_cap_is_reached(ws_root: Path) -> None:
    tasks = [Task(id=f"t{i}", status=TaskStatus.READY) for i in range(200)]
    ws = create("cap-task", "d", root=ws_root, tasks=tasks)
    for i in range(200):
        ws = workspace_mod.set_sticky_instructions(ws, f"t{i}", "x", origin="dispatch")
    with pytest.raises(workspace_mod.WorkspaceError):
        workspace_mod.set_sticky_instructions(ws, "overflow", "y", origin="dispatch")


def test_set_sticky_instructions_at_cap_can_still_replace_an_existing_key(
    ws_root: Path,
) -> None:
    tasks = [Task(id=f"t{i}", status=TaskStatus.READY) for i in range(200)]
    ws = create("cap-replace-task", "d", root=ws_root, tasks=tasks)
    for i in range(200):
        ws = workspace_mod.set_sticky_instructions(ws, f"t{i}", "x", origin="dispatch")
    ws = workspace_mod.set_sticky_instructions(ws, "t0", "updated", origin="dispatch")
    assert ws.meta.dispatch_instructions["t0"].text == "updated"
    assert len(ws.meta.dispatch_instructions) == 200


def test_sticky_instructions_do_not_inherit_to_child_task_ids(ws_root: Path) -> None:
    tasks = [Task(id="parent", status=TaskStatus.READY)]
    ws = create("decomp-task", "d", root=ws_root, tasks=tasks)
    ws = workspace_mod.set_sticky_instructions(
        ws, "parent", "parent instructions", origin="dispatch"
    )
    child = Task(id="child", status=TaskStatus.READY, parent_task_id="parent")
    ws = workspace_mod.inject_tasks(ws, [child], [])
    assert "child" not in ws.meta.dispatch_instructions
    assert ws.meta.dispatch_instructions["parent"].text == "parent instructions"
