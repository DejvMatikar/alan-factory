from __future__ import annotations

import itertools

import pytest

from alan.lifecycle import Status, advance_target


def _oracle(current: Status, block: bool, awaiting: bool) -> Status | None:
    if current == Status.ESCALATED:
        if awaiting:
            return None
        return Status.ACTIVE
    if block:
        return current
    return None


@pytest.mark.parametrize(
    ("current", "block", "awaiting"),
    list(itertools.product(list(Status), [False, True], [False, True])),
)
def test_advance_target_matches_the_truth_table(
    current: Status, block: bool, awaiting: bool
) -> None:
    assert (
        advance_target(current, has_block_class=block, has_awaiting_subtask=awaiting)
        == _oracle(current, block, awaiting)
    )


def test_escalated_with_awaiting_subtask_skips_the_transition() -> None:
    assert (
        advance_target(Status.ESCALATED, has_block_class=True, has_awaiting_subtask=True)
        is None
    )


def test_escalated_without_awaiting_subtask_advances_to_active() -> None:
    assert (
        advance_target(Status.ESCALATED, has_block_class=False, has_awaiting_subtask=False)
        is Status.ACTIVE
    )


def test_non_escalated_with_block_class_restamps_the_current_status() -> None:
    assert (
        advance_target(Status.PAUSED, has_block_class=True, has_awaiting_subtask=False)
        is Status.PAUSED
    )


def test_non_escalated_without_block_class_writes_nothing() -> None:
    assert (
        advance_target(Status.ACTIVE, has_block_class=False, has_awaiting_subtask=True)
        is None
    )
