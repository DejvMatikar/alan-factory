from __future__ import annotations

import pytest

from alan.incident import STUCK_WS_STATUSES, stuck_workspace_keys
from alan.workspace import Status


def test_stuck_workspace_keys_returns_empty_for_no_pairs() -> None:
    assert stuck_workspace_keys([]) == frozenset()


@pytest.mark.parametrize("status", sorted(STUCK_WS_STATUSES, key=str))
def test_stuck_workspace_keys_includes_every_stuck_status(status: Status) -> None:
    assert stuck_workspace_keys([("ws-1", status)]) == frozenset({"ws-1"})


@pytest.mark.parametrize("status", sorted(set(Status) - STUCK_WS_STATUSES, key=str))
def test_stuck_workspace_keys_excludes_every_non_stuck_status(status: Status) -> None:
    assert stuck_workspace_keys([("ws-1", status)]) == frozenset()


def test_stuck_workspace_keys_filters_a_mixed_population() -> None:
    pairs = [
        ("stuck-1", Status.AWAITING_INPUT),
        ("active-1", Status.ACTIVE),
        ("stuck-2", Status.ESCALATED),
        ("completed-1", Status.COMPLETED),
    ]
    assert stuck_workspace_keys(pairs) == frozenset({"stuck-1", "stuck-2"})


def test_stuck_workspace_keys_deduplicates_repeated_keys() -> None:
    pairs = [("ws-1", Status.FAILED), ("ws-1", Status.PARTIAL)]
    assert stuck_workspace_keys(pairs) == frozenset({"ws-1"})


def test_stuck_workspace_keys_accepts_a_generator() -> None:
    pairs = (("ws-1", Status.ESCALATED) for _ in range(1))
    assert stuck_workspace_keys(pairs) == frozenset({"ws-1"})
