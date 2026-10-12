from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

FLOOR_TURNS: int = 20
MIN_HISTORY_ROWS: int = 5
_P95_MULTIPLIER: float = 1.3


@dataclass(frozen=True)
class RoleHistory:
    role: str
    p95_turns: float


def _percentile_95(values: Sequence[int]) -> float:
    ordered = sorted(values)
    idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return float(ordered[idx])


def role_history_from_turns(role: str, turns: Sequence[int]) -> RoleHistory | None:
    if len(turns) < MIN_HISTORY_ROWS:
        return None
    return RoleHistory(role=role, p95_turns=_percentile_95(turns))


def turn_ceiling(role: str, role_cap: int, history: RoleHistory | None) -> int:
    if history is None or history.role != role:
        return role_cap
    derived = max(FLOOR_TURNS, math.ceil(_P95_MULTIPLIER * history.p95_turns))
    return min(role_cap, derived)
