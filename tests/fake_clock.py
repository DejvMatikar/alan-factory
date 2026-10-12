from __future__ import annotations

from alan.clock import Epoch, Monotonic


class FakeClock:
    def __init__(self, *, monotonic: float = 0.0, epoch: float = 0.0) -> None:
        self._monotonic = monotonic
        self._epoch = epoch

    def monotonic(self) -> Monotonic:
        return Monotonic(self._monotonic)

    def now(self) -> Epoch:
        return Epoch(self._epoch)

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self._epoch += seconds
