from __future__ import annotations

import secrets
import threading
from typing import Final

from alan.clock import SYSTEM_CLOCK, Clock, Monotonic

TICKET_TTL_S: Final[float] = 30.0

_lock = threading.Lock()
_expiries: dict[str, Monotonic] = {}


def _purge_expired(now: Monotonic) -> None:
    for ticket in [t for t, expiry in _expiries.items() if expiry <= now]:
        del _expiries[ticket]


def issue(clock: Clock = SYSTEM_CLOCK) -> str:
    now = clock.monotonic()
    ticket = secrets.token_urlsafe(32)
    with _lock:
        _purge_expired(now)
        _expiries[ticket] = Monotonic(now.value + TICKET_TTL_S)
    return ticket


def consume(ticket: str, clock: Clock = SYSTEM_CLOCK) -> bool:
    now = clock.monotonic()
    with _lock:
        expiry = _expiries.pop(ticket, None)
    return expiry is not None and now < expiry


def clear() -> None:
    with _lock:
        _expiries.clear()


__all__ = ["TICKET_TTL_S", "clear", "consume", "issue"]
