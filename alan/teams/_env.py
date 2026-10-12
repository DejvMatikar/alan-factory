from __future__ import annotations

import math
import os


def int_or_none(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    return int(v) if isinstance(v, (int, float)) else None


def float_or_none(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 and math.isfinite(value) else default


def positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default
