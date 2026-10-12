from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import workspace_index as wsi
from alan.workspace import create
from alan.workspace_index import WorkspaceRow


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def test_workspace_row_model_validate_naive_updated_at_becomes_utc_aware() -> None:
    row = WorkspaceRow.model_validate(
        {
            "task_id": "t1",
            "title": "T",
            "status": "active",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )
    assert row.updated_at.tzinfo is not None
    assert row.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_workspace_row_aware_non_utc_offset_updated_at_preserved() -> None:
    row = WorkspaceRow.model_validate(
        {
            "task_id": "t1",
            "title": "T",
            "status": "active",
            "created_at": "2026-01-01T00:00:00+05:00",
            "updated_at": "2026-01-01T00:00:00+05:00",
        }
    )
    assert row.updated_at.utcoffset() == timedelta(hours=5)


def test_parse_blob_rows_naive_updated_at_becomes_utc_aware() -> None:
    payload = json.dumps(
        {
            "version": wsi._BLOB_VERSION,  # noqa: SLF001
            "complete": True,
            "rows": {
                "archived-1": {
                    "task_id": "archived-1",
                    "title": "Archived",
                    "status": "archived",
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                }
            },
        }
    )
    rows, complete = wsi._parse_blob_rows(payload)  # noqa: SLF001
    assert complete is True
    assert rows["archived-1"].updated_at.tzinfo is not None
    assert rows["archived-1"].updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_blob_rows_still_drops_malformed_row() -> None:
    payload = json.dumps(
        {
            "version": wsi._BLOB_VERSION,  # noqa: SLF001
            "complete": True,
            "rows": {"broken": {"task_id": "broken"}},
        }
    )
    rows, complete = wsi._parse_blob_rows(payload)  # noqa: SLF001
    assert rows == {}
    assert complete is True


def test_row_from_meta_updated_at_is_aware(root: Path) -> None:
    ws = create("Row From Meta", "d", root=root)
    guards = wsi._Guards(ws_path=ws.path, meta_sig=(0, 0))  # noqa: SLF001
    row = wsi._row_from_meta(ws.meta, guards)  # noqa: SLF001
    assert row.updated_at.tzinfo is not None


def test_workspace_row_model_copy_preserves_aware_updated_at() -> None:
    row = WorkspaceRow.model_validate(
        {
            "task_id": "t1",
            "title": "T",
            "status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    copied = row.model_copy(update={"title": "Copied"})
    assert copied.updated_at == row.updated_at
    assert copied.updated_at.tzinfo is not None
