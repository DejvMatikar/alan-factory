from __future__ import annotations

import pytest

from alan.clock import Epoch, Monotonic
from tests.fake_clock import FakeClock

_STALE_THRESHOLD_S = 8.0 * 3600


def test_uptime_comparison_across_domains_raises_instead_of_never_firing() -> None:
    uptime = Monotonic(100.0)
    threshold = Epoch(_STALE_THRESHOLD_S)
    with pytest.raises(TypeError):
        _ = uptime > threshold  # type: ignore[operator]


def test_same_domain_uptime_comparison_becomes_true_after_clock_advances() -> None:
    clock = FakeClock(monotonic=0.0)
    started = clock.monotonic()
    assert not (clock.monotonic() - started) > _STALE_THRESHOLD_S
    clock.advance(_STALE_THRESHOLD_S + 1.0)
    assert (clock.monotonic() - started) > _STALE_THRESHOLD_S
