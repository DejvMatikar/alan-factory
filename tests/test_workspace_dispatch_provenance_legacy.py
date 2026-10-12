from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatch_provenance import DispatchSurface

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _seed_legacy_workspace(root: Path, task_id: str, fixture_name: str) -> None:
    ws_dir = root / task_id
    ws_dir.mkdir(parents=True)
    shutil.copy(_FIXTURES / fixture_name, ws_dir / "meta.json")


def test_read_pre_provenance_record_defaults_to_unrecorded(tmp_path: Path) -> None:
    _seed_legacy_workspace(tmp_path, "legacy-workspace", "meta_pre_provenance.json")
    ws = workspace_mod.read("legacy-workspace", root=tmp_path)
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.UNRECORDED


def test_list_workspaces_includes_pre_provenance_record_as_unrecorded(tmp_path: Path) -> None:
    _seed_legacy_workspace(tmp_path, "legacy-workspace", "meta_pre_provenance.json")
    rows = workspace_mod.list_workspaces(root=tmp_path)
    assert len(rows) == 1
    assert rows[0].meta.dispatch_provenance.surface == DispatchSurface.UNRECORDED


def test_read_malformed_provenance_record_raises(tmp_path: Path) -> None:
    _seed_legacy_workspace(
        tmp_path, "legacy-workspace-malformed", "meta_malformed_provenance.json"
    )
    with pytest.raises(workspace_mod.WorkspaceError):
        workspace_mod.read("legacy-workspace-malformed", root=tmp_path)


def test_list_workspaces_silently_omits_malformed_provenance_record(tmp_path: Path) -> None:
    _seed_legacy_workspace(
        tmp_path, "legacy-workspace-malformed", "meta_malformed_provenance.json"
    )
    assert workspace_mod.list_workspaces(root=tmp_path) == []
