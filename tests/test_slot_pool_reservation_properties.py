from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from alan.clock import Monotonic
from alan.slot_pool import Reservation, SlotPool, SlotReservedError
from tests.fake_clock import FakeClock

_SIZE = 3
_TOKENS = ("tok-a", "tok-b")

_op_strategy = st.tuples(
    st.sampled_from(
        ["reserve", "claim", "release_claim", "adopt", "release_reservation", "flip"]
    ),
    st.integers(min_value=0, max_value=_SIZE - 1),
    st.sampled_from(_TOKENS),
    st.booleans(),
)

_ModelReservations = dict[int, tuple[str, bool]]


def _redeemable(model_all: _ModelReservations) -> dict[int, str]:
    return {idx: token for idx, (token, adoptable) in model_all.items() if adoptable}


def _apply_reserved_map_directly(pool: SlotPool, model_all: _ModelReservations) -> None:
    reserved_map = {
        pool.slot_path(i): Reservation(token, adoptable)
        for i, (token, adoptable) in model_all.items()
    }
    with pool._cond:  # noqa: SLF001
        pool._apply_reserved_under_lock(reserved_map)  # noqa: SLF001
        pool._free -= set(pool._reserved)  # noqa: SLF001


def _apply_reserve(
    pool: SlotPool, model_all: _ModelReservations, model_leased: set[int],
    idx: int, token: str, adoptable: bool,
) -> None:
    if idx in model_leased:
        return
    model_all[idx] = (token, adoptable)
    _apply_reserved_map_directly(pool, model_all)


def _apply_flip(
    pool: SlotPool, model_all: _ModelReservations, model_leased: set[int],
    idx: int, adoptable: bool,
) -> None:
    if idx in model_leased or idx not in model_all:
        return
    token, _old_adoptable = model_all[idx]
    model_all[idx] = (token, adoptable)
    _apply_reserved_map_directly(pool, model_all)


def _apply_claim(
    pool: SlotPool, model_all: _ModelReservations, model_leased: set[int],
    lease_tokens: dict[int, Monotonic | None], idx: int,
) -> None:
    if idx in _redeemable(model_all) or idx in model_leased:
        return
    claimed_idx, lease_token = pool._claim(idx)  # noqa: SLF001
    assert claimed_idx == idx
    model_leased.add(idx)
    lease_tokens[idx] = lease_token


def _apply_release_claim(
    pool: SlotPool, model_leased: set[int], lease_tokens: dict[int, Monotonic | None], idx: int,
) -> None:
    if idx not in model_leased:
        return
    token = lease_tokens.get(idx)
    if token is None:
        return
    pool._release(idx, token)  # noqa: SLF001
    del lease_tokens[idx]
    model_leased.discard(idx)


def _apply_adopt(
    pool: SlotPool, model_all: _ModelReservations, model_leased: set[int],
    lease_tokens: dict[int, Monotonic | None], idx: int, token: str,
) -> None:
    expected = _redeemable(model_all).get(idx)
    if expected == token:
        adopted_idx = pool.adopt(pool.slot_path(idx), token)
        assert adopted_idx == idx
        model_all.pop(idx, None)
        model_leased.add(idx)
        lease_tokens[idx] = None
    else:
        with pytest.raises(SlotReservedError):
            pool.adopt(pool.slot_path(idx), token)


def _apply_release_reservation(
    pool: SlotPool, model_all: _ModelReservations, idx: int,
) -> None:
    if idx not in _redeemable(model_all):
        return
    pool.release_reservation(pool.slot_path(idx))
    model_all.pop(idx, None)


def _assert_invariants(
    pool: SlotPool, model_all: _ModelReservations, model_leased: set[int],
) -> None:
    redeemable = _redeemable(model_all)
    assert set(pool._reserved) == set(redeemable)  # noqa: SLF001
    assert pool._lease_times.keys() & pool._reserved.keys() == set()  # noqa: SLF001
    assert model_leased & set(redeemable) == set()
    assert pool._free & pool._reserved.keys() == set()  # noqa: SLF001
    assert pool._free & pool._lease_times.keys() == set()  # noqa: SLF001
    assert pool._clean_excluded.keys() >= pool._reserved.keys()  # noqa: SLF001


@given(st.lists(_op_strategy, max_size=25))
def test_reservation_ordering_never_hands_reserved_slot_to_wrong_claimant(
    tmp_path: Path, ops: list[tuple[str, int, str, bool]]
) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=_SIZE, repo_root=str(tmp_path), clock=FakeClock()
    )
    model_all: _ModelReservations = {}
    model_leased: set[int] = set()
    lease_tokens: dict[int, Monotonic | None] = {}

    for kind, idx, token, adoptable in ops:
        if kind == "reserve":
            _apply_reserve(pool, model_all, model_leased, idx, token, adoptable)
        elif kind == "flip":
            _apply_flip(pool, model_all, model_leased, idx, adoptable)
        elif kind == "claim":
            _apply_claim(pool, model_all, model_leased, lease_tokens, idx)
        elif kind == "release_claim":
            _apply_release_claim(pool, model_leased, lease_tokens, idx)
        elif kind == "adopt":
            _apply_adopt(pool, model_all, model_leased, lease_tokens, idx, token)
        elif kind == "release_reservation":
            _apply_release_reservation(pool, model_all, idx)
        _assert_invariants(pool, model_all, model_leased)
