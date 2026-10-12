from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from alan.workspace import Meta, Status, as_utc, create, list_workspaces, read


class _OffsetlessTzinfo(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def test_as_utc_stamps_naive_datetime_with_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = as_utc(naive)
    assert result.tzinfo is UTC
    assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_as_utc_returns_aware_datetime_unchanged() -> None:
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert as_utc(aware) is aware


def test_as_utc_stamps_datetime_with_tzinfo_that_reports_no_offset() -> None:
    naive_like = datetime(2026, 1, 1, 12, 0, 0, tzinfo=_OffsetlessTzinfo())
    result = as_utc(naive_like)
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_meta_model_validate_naive_updated_at_becomes_utc_aware() -> None:
    meta = Meta.model_validate(
        {
            "task_id": "t1",
            "title": "T",
            "description": "d",
            "status": "active",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )
    assert meta.updated_at.tzinfo is not None
    assert meta.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_meta_direct_construction_naive_updated_at_becomes_utc_aware() -> None:
    meta = Meta(
        task_id="t1",
        title="T",
        description="d",
        status=Status.ACTIVE,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    assert meta.updated_at.tzinfo is not None
    assert meta.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_meta_naive_iso_string_updated_at_becomes_utc_aware() -> None:
    payload = json.dumps(
        {
            "task_id": "t1",
            "title": "T",
            "description": "d",
            "status": "active",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )
    meta = Meta.model_validate_json(payload)
    assert meta.updated_at.tzinfo is not None
    assert meta.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_meta_aware_non_utc_offset_updated_at_preserved() -> None:
    meta = Meta.model_validate(
        {
            "task_id": "t1",
            "title": "T",
            "description": "d",
            "status": "active",
            "created_at": "2026-01-01T00:00:00+05:00",
            "updated_at": "2026-01-01T00:00:00+05:00",
        }
    )
    assert meta.updated_at.utcoffset() == timedelta(hours=5)


def _strip_tzinfo(iso_value: str) -> str:
    return datetime.fromisoformat(iso_value).replace(tzinfo=None).isoformat()


def test_read_meta_naive_updated_at_returns_aware_workspace(root: Path) -> None:
    ws = create("Naive TS", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = _strip_tzinfo(raw["updated_at"])
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")

    reread = read(ws.task_id, root=root)

    assert reread.meta.updated_at.tzinfo is not None


def test_list_workspaces_includes_workspace_with_naive_meta_timestamp(root: Path) -> None:
    ws = create("Naive Listing", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = _strip_tzinfo(raw["updated_at"])
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")

    workspaces = list_workspaces(root=root)

    assert any(w.task_id == ws.task_id for w in workspaces)
