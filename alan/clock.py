from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final, Protocol


@dataclass(frozen=True, slots=True, order=True)
class Monotonic:
    value: float

    def __sub__(self, other: Monotonic) -> float:
        if type(other) is not Monotonic:
            raise TypeError(f"cannot subtract {type(other).__name__} from Monotonic")
        return self.value - other.value


@dataclass(frozen=True, slots=True, order=True)
class Epoch:
    value: float

    def __sub__(self, other: Epoch) -> float:
        if type(other) is not Epoch:
            raise TypeError(f"cannot subtract {type(other).__name__} from Epoch")
        return self.value - other.value


class Clock(Protocol):
    def monotonic(self) -> Monotonic: ...  # pragma: no cover

    def now(self) -> Epoch: ...  # pragma: no cover


class SystemClock:
    def monotonic(self) -> Monotonic:
        return Monotonic(time.monotonic())

    def now(self) -> Epoch:
        return Epoch(time.time())


SYSTEM_CLOCK: Final[SystemClock] = SystemClock()

__all__ = ["SYSTEM_CLOCK", "Clock", "Epoch", "Monotonic", "SystemClock"]
