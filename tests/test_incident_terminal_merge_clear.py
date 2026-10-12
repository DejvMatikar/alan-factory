from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan.incident import derive_incidents
from alan.workspace import Meta, Status, Workspace


def _terminal_failed_ws(*, merge_block_class: str | None) -> Workspace:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    meta = Meta(
        task_id="ws1",
        title="t",
        description="d",
        status=Status.FAILED,
        merge_block_class=merge_block_class,
        created_at=ts,
        updated_at=ts,
    )
    return Workspace(task_id="ws1", path=Path("ws1"), meta=meta)


def test_fingerprint_differs_between_stale_and_cleared_merge_block_class() -> None:
    stale = derive_incidents(workspaces=[_terminal_failed_ws(merge_block_class="stale_review")])
    cleared = derive_incidents(workspaces=[_terminal_failed_ws(merge_block_class=None)])
    assert len(stale) == 1
    assert len(cleared) == 1
    assert stale[0].fingerprint != cleared[0].fingerprint
