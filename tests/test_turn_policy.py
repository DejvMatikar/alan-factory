from __future__ import annotations

from alan.turn_policy import (
    FLOOR_TURNS,
    MIN_HISTORY_ROWS,
    RoleHistory,
    _percentile_95,
    role_history_from_turns,
    turn_ceiling,
)


def test_floor_turns_and_min_history_rows_are_positive() -> None:
    assert FLOOR_TURNS > 0
    assert MIN_HISTORY_ROWS > 0


def test_turn_ceiling_returns_role_cap_when_history_none() -> None:
    assert turn_ceiling("implementer", 500, None) == 500
    assert turn_ceiling("verifier", 150, None) == 150


def test_turn_ceiling_returns_role_cap_when_role_mismatches() -> None:
    history = RoleHistory(role="other_role", p95_turns=50.0)
    assert turn_ceiling("implementer", 500, history) == 500


def test_turn_ceiling_derives_below_cap_when_history_low() -> None:
    history = RoleHistory(role="implementer", p95_turns=50.0)
    result = turn_ceiling("implementer", 500, history)
    assert result == 65
    assert result < 500


def test_turn_ceiling_never_exceeds_role_cap() -> None:
    history = RoleHistory(role="implementer", p95_turns=1000.0)
    assert turn_ceiling("implementer", 150, history) == 150


def test_turn_ceiling_never_below_floor_turns() -> None:
    history = RoleHistory(role="implementer", p95_turns=1.0)
    assert turn_ceiling("implementer", 500, history) == FLOOR_TURNS


def test_turn_ceiling_role_cap_below_floor_wins_over_floor() -> None:
    history = RoleHistory(role="implementer", p95_turns=1.0)
    assert turn_ceiling("implementer", 10, history) == 10


def test_role_history_from_turns_returns_none_below_minimum() -> None:
    turns = [10, 20, 30, 40]
    assert len(turns) < MIN_HISTORY_ROWS
    assert role_history_from_turns("implementer", turns) is None


def test_role_history_from_turns_returns_history_at_minimum() -> None:
    turns = [10, 20, 30, 40, 50]
    history = role_history_from_turns("implementer", turns)
    assert history is not None
    assert history.role == "implementer"
    assert history.p95_turns == 50.0


def test_role_history_from_turns_computes_expected_p95() -> None:
    turns = list(range(1, 101))
    history = role_history_from_turns("implementer", turns)
    assert history is not None
    assert history.p95_turns == 95.0


def test_percentile_95_single_value() -> None:
    assert _percentile_95([42]) == 42.0


def test_percentile_95_sorts_unordered_input() -> None:
    assert _percentile_95([5, 1, 4, 2, 3]) == 5.0
