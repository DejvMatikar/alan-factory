from __future__ import annotations

from pathlib import Path

import pytest

from alan.park import Blocker, ParkReason
from alan.workspace import Status, Workspace, create, transition


def _parked(tmp_path: Path) -> Workspace:
    ws = create("t", "d", root=tmp_path)
    blockers = [Blocker(kind="reason", name="reason", detail="x")]
    return transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH, blockers=blockers)


def test_active_clears_park_reason_and_blockers(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, Status.ACTIVE)
    assert updated.meta.park_reason is None
    assert updated.meta.blockers == []


def test_completed_clears_park_reason_and_blockers(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, Status.COMPLETED)
    assert updated.meta.park_reason is None
    assert updated.meta.blockers == []


@pytest.mark.parametrize(
    "status",
    [
        Status.AWAITING_INPUT,
        Status.ESCALATED,
        Status.FAILED,
        Status.PARTIAL,
        Status.ARCHIVED,
        Status.PAUSED,
    ],
)
def test_retaining_statuses_preserve_park_reason_and_blockers(
    tmp_path: Path, status: Status
) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, status)
    assert updated.meta.park_reason is ParkReason.CRASH
    assert len(updated.meta.blockers) == 1
    assert updated.meta.blockers[0].detail == "x"


def test_clear_park_reason_flag_clears_on_a_retaining_status(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, Status.ESCALATED, clear_park_reason=True)
    assert updated.meta.park_reason is None
    assert len(updated.meta.blockers) == 1


def test_clear_blockers_flag_clears_on_a_retaining_status(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, Status.ESCALATED, clear_blockers=True)
    assert updated.meta.blockers == []
    assert updated.meta.park_reason is ParkReason.CRASH


def test_explicit_park_reason_wins_over_the_active_clear_rule(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    updated = transition(ws, Status.ACTIVE, park_reason=ParkReason.STALL)
    assert updated.meta.park_reason is ParkReason.STALL


def test_explicit_blockers_win_over_the_active_clear_rule(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    replacement = [Blocker(kind="reason", name="new", detail="y")]
    updated = transition(ws, Status.ACTIVE, blockers=replacement)
    assert updated.meta.blockers == replacement


def test_non_transition_to_active_still_heals_a_corrupted_record(tmp_path: Path) -> None:
    ws = _parked(tmp_path)
    active = transition(ws, Status.ACTIVE)
    corrupted_meta = active.meta.model_copy(
        update={
            "park_reason": ParkReason.CRASH,
            "blockers": [Blocker(kind="reason", name="stale", detail="stale")],
        }
    )
    corrupted = Workspace(task_id=active.task_id, path=active.path, meta=corrupted_meta)
    healed = transition(corrupted, Status.ACTIVE)
    assert healed.meta.park_reason is None
    assert healed.meta.blockers == []
