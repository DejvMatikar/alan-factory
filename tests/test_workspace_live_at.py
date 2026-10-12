from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan.workspace import create, mark_delivered


def test_live_at_defaults_none(tmp_path: Path) -> None:
    ws = create("live-at-default", "d", root=tmp_path / "ws")
    assert ws.meta.live_at is None


def test_mark_delivered_stamps_live_at(tmp_path: Path) -> None:
    ws = create("live-at-stamped", "d", root=tmp_path / "ws")
    live_at = datetime.now(UTC)
    updated = mark_delivered(ws, settled_active_seconds=10.0, live_at=live_at)
    assert updated.meta.live_at == live_at


def test_mark_delivered_without_live_at_leaves_it_none(tmp_path: Path) -> None:
    ws = create("live-at-omitted", "d", root=tmp_path / "ws")
    updated = mark_delivered(ws, settled_active_seconds=10.0)
    assert updated.meta.live_at is None


def test_mark_delivered_is_idempotent_and_never_overwrites_live_at(tmp_path: Path) -> None:
    ws = create("live-at-idempotent", "d", root=tmp_path / "ws")
    first_live_at = datetime.now(UTC)
    first = mark_delivered(ws, settled_active_seconds=10.0, live_at=first_live_at)
    second_live_at = datetime.now(UTC)
    second = mark_delivered(first, settled_active_seconds=20.0, live_at=second_live_at)
    assert second.meta.live_at == first_live_at
    assert second.meta.settled_active_seconds == first.meta.settled_active_seconds
