from __future__ import annotations

from pathlib import Path

from alan.park import Blocker, ParkReason
from alan.workspace import Status, create, transition


def test_merge_block_class_set_wins_over_clear(tmp_path: Path) -> None:
    ws = create("t", "d", root=tmp_path)
    updated = transition(
        ws,
        Status.AWAITING_INPUT,
        merge_block_class="genuine",
        clear_merge_block_class=True,
    )
    assert updated.meta.merge_block_class == "genuine"


def test_clear_nulls_a_populated_merge_block_class(tmp_path: Path) -> None:
    ws = create("t", "d", root=tmp_path)
    ws = transition(ws, Status.AWAITING_INPUT, merge_block_class="stale_review")
    cleared = transition(ws, Status.ESCALATED, clear_merge_block_class=True)
    assert cleared.meta.merge_block_class is None


def test_omitting_both_leaves_a_populated_merge_block_class_untouched(tmp_path: Path) -> None:
    ws = create("t", "d", root=tmp_path)
    ws = transition(ws, Status.AWAITING_INPUT, merge_block_class="dirty")
    unchanged = transition(ws, Status.ACTIVE)
    assert unchanged.meta.merge_block_class == "dirty"


def test_omitting_both_leaves_an_absent_merge_block_class_none(tmp_path: Path) -> None:
    ws = create("t", "d", root=tmp_path)
    unchanged = transition(ws, Status.ACTIVE)
    assert unchanged.meta.merge_block_class is None


def test_clear_merge_block_class_does_not_disturb_park_reason_or_blockers(
    tmp_path: Path,
) -> None:
    ws = create("t", "d", root=tmp_path)
    blockers = [Blocker(kind="reason", name="reason", detail="x")]
    updated = transition(
        ws,
        Status.ESCALATED,
        clear_merge_block_class=True,
        park_reason=ParkReason.ESCALATED,
        blockers=blockers,
    )
    assert updated.meta.park_reason is ParkReason.ESCALATED
    assert updated.meta.blockers == blockers


def test_empty_string_merge_block_class_is_stored_verbatim_with_no_special_casing(
    tmp_path: Path,
) -> None:
    ws = create("t", "d", root=tmp_path)
    updated = transition(ws, Status.AWAITING_INPUT, merge_block_class="")
    assert updated.meta.merge_block_class == ""
