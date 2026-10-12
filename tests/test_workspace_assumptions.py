from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.workspace import Meta, create, read, update_assumptions


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def test_create_defaults_assumptions_to_empty_list(root: Path) -> None:
    ws = create("T", "d", root=root)
    assert ws.meta.assumptions == []


def test_create_defaults_pending_first_hop_to_none(root: Path) -> None:
    ws = create("T", "d", root=root)
    assert ws.meta.pending_first_hop is None


def test_create_persists_pending_first_hop(root: Path) -> None:
    ws = create("T", "d", root=root, pending_first_hop="implementation_plan")
    assert ws.meta.pending_first_hop == "implementation_plan"
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.pending_first_hop == "implementation_plan"


def test_update_assumptions_persists_and_returns_new_workspace(root: Path) -> None:
    ws = create("T", "d", root=root)
    updated = update_assumptions(ws, ["assumed X", "assumed Y"])
    assert updated.meta.assumptions == ["assumed X", "assumed Y"]
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.assumptions == ["assumed X", "assumed Y"]


def test_update_assumptions_does_not_touch_requirements(root: Path) -> None:
    ws = create("T", "d", root=root, requirements=None)
    updated = update_assumptions(ws, ["assumed X"])
    assert updated.meta.requirements is None


def test_pre_change_meta_json_without_new_fields_still_validates(root: Path) -> None:
    ws = create("T", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    del raw["assumptions"]
    del raw["pending_first_hop"]
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.assumptions == []
    assert reloaded.meta.pending_first_hop is None


def test_meta_model_validate_without_new_fields() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    meta = Meta.model_validate({
        "task_id": "t1",
        "title": "T",
        "description": "d",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    assert meta.assumptions == []
    assert meta.pending_first_hop is None
