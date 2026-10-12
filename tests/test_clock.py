from __future__ import annotations

import time

import pytest

from alan import clock as clock_mod
from alan.clock import SYSTEM_CLOCK, Epoch, Monotonic, SystemClock


def test_monotonic_subtraction_returns_float() -> None:
    delta = Monotonic(5.0) - Monotonic(2.0)
    assert delta == 3.0
    assert isinstance(delta, float)


def test_epoch_subtraction_returns_float() -> None:
    delta = Epoch(5.0) - Epoch(2.0)
    assert delta == 3.0
    assert isinstance(delta, float)


def test_monotonic_minus_epoch_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _ = Monotonic(5.0) - Epoch(2.0)  # type: ignore[operator]


def test_epoch_minus_monotonic_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _ = Epoch(5.0) - Monotonic(2.0)  # type: ignore[operator]


def test_monotonic_lt_epoch_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _ = Monotonic(1.0) < Epoch(1.0)  # type: ignore[operator]


def test_monotonic_le_epoch_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _ = Monotonic(1.0) <= Epoch(1.0)  # type: ignore[operator]


def test_monotonic_equals_epoch_is_false() -> None:
    assert (Monotonic(1.0) == Epoch(1.0)) is False  # type: ignore[comparison-overlap]


def test_same_domain_ordering_works() -> None:
    assert Monotonic(1.0) < Monotonic(2.0)
    assert Epoch(1.0) <= Epoch(1.0)


def test_system_clock_monotonic_returns_monotonic_around_stdlib_reading() -> None:
    before = time.monotonic()
    reading = SystemClock().monotonic()
    after = time.monotonic()
    assert isinstance(reading, Monotonic)
    assert before <= reading.value <= after


def test_system_clock_now_returns_epoch_around_stdlib_reading() -> None:
    before = time.time()
    reading = SystemClock().now()
    after = time.time()
    assert isinstance(reading, Epoch)
    assert before <= reading.value <= after


def test_system_clock_singleton_identity() -> None:
    assert clock_mod.SYSTEM_CLOCK is SYSTEM_CLOCK
    assert isinstance(SYSTEM_CLOCK, SystemClock)
