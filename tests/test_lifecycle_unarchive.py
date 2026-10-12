from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

import pytest

from alan.lifecycle import (
    ParkReason,
    Status,
    held_by_unarchive,
    pre_archive_status,
    stamps_finish,
    unarchive_target,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_NOT_ARCHIVED = [status for status in Status if status is not Status.ARCHIVED]
_RESTORED_AS_SAVED = [
    Status.COMPLETED,
    Status.FAILED,
    Status.PARTIAL,
    Status.ESCALATED,
    Status.AWAITING_INPUT,
    Status.PAUSED,
]
_FINISHED = [Status.COMPLETED, Status.FAILED, Status.ESCALATED, Status.PARTIAL]


@pytest.mark.parametrize("prev", _NOT_ARCHIVED)
def test_pre_archive_status_saves_the_previous_status_on_entry_to_archived(prev: Status) -> None:
    assert pre_archive_status(prev, Status.ARCHIVED, None) is prev


@pytest.mark.parametrize("prev", _NOT_ARCHIVED)
def test_pre_archive_status_prefers_the_previous_status_over_a_stale_saved_value(
    prev: Status,
) -> None:
    assert pre_archive_status(prev, Status.ARCHIVED, Status.FAILED) is prev


@pytest.mark.parametrize("saved", _NOT_ARCHIVED)
def test_pre_archive_status_keeps_the_saved_status_on_rearchive(saved: Status) -> None:
    assert pre_archive_status(Status.ARCHIVED, Status.ARCHIVED, saved) is saved


def test_pre_archive_status_leaves_a_legacy_archive_unset_on_rearchive() -> None:
    assert pre_archive_status(Status.ARCHIVED, Status.ARCHIVED, None) is None


@pytest.mark.parametrize(("prev", "new"), list(product(Status, _NOT_ARCHIVED)))
def test_pre_archive_status_is_none_for_any_non_archived_target(prev: Status, new: Status) -> None:
    assert pre_archive_status(prev, new, Status.COMPLETED) is None


@pytest.mark.parametrize("saved", _RESTORED_AS_SAVED)
def test_unarchive_target_restores_the_saved_status_without_a_park_reason(saved: Status) -> None:
    assert unarchive_target(saved, has_pause_marker=False) == (saved, None)


def test_unarchive_target_maps_a_saved_active_to_a_parked_needs_you() -> None:
    assert unarchive_target(Status.ACTIVE, has_pause_marker=False) == (
        Status.AWAITING_INPUT,
        ParkReason.UNARCHIVED,
    )


def test_unarchive_target_maps_a_legacy_archive_to_a_parked_needs_you() -> None:
    assert unarchive_target(None, has_pause_marker=False) == (
        Status.AWAITING_INPUT,
        ParkReason.UNARCHIVED,
    )


def test_unarchive_target_never_returns_active() -> None:
    targets = {
        unarchive_target(pre, has_pause_marker=marker)[0]
        for pre, marker in product([None, *Status], [False, True])
    }
    assert Status.ACTIVE not in targets


@pytest.mark.parametrize("saved", [None, Status.ACTIVE, Status.COMPLETED, Status.FAILED])
def test_unarchive_target_lets_a_pause_marker_win(saved: Status | None) -> None:
    assert unarchive_target(saved, has_pause_marker=True) == (Status.PAUSED, None)


@pytest.mark.parametrize(
    ("status", "reason"), list(product(Status, [None, *ParkReason]))
)
def test_held_by_unarchive_is_true_only_for_awaiting_input_with_the_unarchived_reason(
    status: Status, reason: ParkReason | None
) -> None:
    expected = status is Status.AWAITING_INPUT and reason is ParkReason.UNARCHIVED
    assert held_by_unarchive(status, reason) is expected


@pytest.mark.parametrize("status", [Status.ESCALATED, Status.PAUSED])
def test_held_by_unarchive_is_false_for_other_statuses_with_the_same_reason(
    status: Status,
) -> None:
    assert held_by_unarchive(status, ParkReason.UNARCHIVED) is False


@pytest.mark.parametrize("new", _FINISHED)
@pytest.mark.parametrize("terminal_at", [None, _TS])
def test_stamps_finish_is_false_when_leaving_archived(
    new: Status, terminal_at: datetime | None
) -> None:
    assert stamps_finish(Status.ARCHIVED, new, terminal_at) is False


@pytest.mark.parametrize("prev", [Status.ACTIVE, Status.AWAITING_INPUT, Status.PAUSED])
def test_stamps_finish_still_stamps_a_first_finish(prev: Status) -> None:
    assert stamps_finish(prev, Status.COMPLETED, None) is True
