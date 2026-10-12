from __future__ import annotations

from pathlib import Path

import pytest

from alan import slot_pool as sp
from alan.slot_pool import SlotPool
from tests.fake_clock import FakeClock


def test_claim_records_lease_time(tmp_path: Path) -> None:
    fake = FakeClock(monotonic=100.0)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)
    idx, token = pool._claim()  # noqa: SLF001
    with pool._cond:  # noqa: SLF001
        lease_t = pool._lease_times.get(idx)  # noqa: SLF001
    assert lease_t == fake.monotonic()
    assert token == fake.monotonic()
    pool._release(idx, token)  # noqa: SLF001


def test_release_clears_lease_time(tmp_path: Path) -> None:
    fake = FakeClock()
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)
    idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001
    with pool._cond:  # noqa: SLF001
        assert idx not in pool._lease_times  # noqa: SLF001


def test_reclaim_stale_under_lock_recovers_expired_slot(tmp_path: Path) -> None:
    fake = FakeClock(monotonic=0.0)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)
    with pool._cond:  # noqa: SLF001
        pool._free.discard(0)  # noqa: SLF001
        pool._lease_times[0] = fake.monotonic()  # noqa: SLF001
        fake.advance(sp._STALE_LEASE_TIMEOUT_S + 1.0)  # noqa: SLF001
        pool._reclaim_stale_under_lock()  # noqa: SLF001
        assert 0 in pool._free  # noqa: SLF001
        assert 0 not in pool._lease_times  # noqa: SLF001


def test_reclaim_stale_under_lock_ignores_fresh_slot(tmp_path: Path) -> None:
    fake = FakeClock(monotonic=0.0)
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path), clock=fake)
    with pool._cond:  # noqa: SLF001
        pool._free.discard(0)  # noqa: SLF001
        pool._lease_times[0] = fake.monotonic()  # noqa: SLF001  — just now
        pool._reclaim_stale_under_lock()  # noqa: SLF001
        assert 0 not in pool._free  # noqa: SLF001  — should still be leased
        assert 0 in pool._lease_times  # noqa: SLF001


def test_claim_reclaims_stale_slot_after_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp, "_STALE_LEASE_TIMEOUT_S", 0.0)
    fake = FakeClock(monotonic=100.0)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)
    with pool._cond:  # noqa: SLF001
        pool._free.discard(0)  # noqa: SLF001
        pool._lease_times[0] = fake.monotonic()  # noqa: SLF001
    fake.advance(1.0)
    idx, token = pool._claim()  # noqa: SLF001  — waits up to 1 s then reclaims
    assert idx == 0
    pool._release(idx, token)  # noqa: SLF001


def test_late_release_does_not_evict_re_leased_slot(tmp_path: Path) -> None:
    fake = FakeClock(monotonic=0.0)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)

    idx_a, token_a = pool._claim()  # noqa: SLF001  worker A claims
    assert idx_a == 0

    fake.advance(sp._STALE_LEASE_TIMEOUT_S + 1.0)
    with pool._cond:  # noqa: SLF001  force slot to look stale then reclaim
        pool._reclaim_stale_under_lock()  # noqa: SLF001

    fake.advance(1.0)
    idx_b, token_b = pool._claim()  # noqa: SLF001  worker B re-claims same slot
    assert idx_b == 0
    assert token_b != token_a

    pool._release(idx_a, token_a)  # noqa: SLF001  worker A un-hangs — must be no-op

    with pool._cond:  # noqa: SLF001
        assert 0 not in pool._free, "late release evicted a re-leased slot"  # noqa: SLF001
        assert pool._lease_times.get(0) == token_b, (  # noqa: SLF001
            "new holder's lease record was destroyed"
        )

    pool._release(idx_b, token_b)  # noqa: SLF001  worker B finishes normally
    assert 0 in pool._free  # noqa: SLF001


def test_non_advancing_fake_clock_yields_colliding_stamps(tmp_path: Path) -> None:
    fake = FakeClock(monotonic=42.0)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=fake)
    idx_a, token_a = pool._claim()  # noqa: SLF001
    pool._release(idx_a, token_a)  # noqa: SLF001
    idx_b, token_b = pool._claim()  # noqa: SLF001  same slot, same instant
    assert idx_b == idx_a
    assert token_a == token_b
    pool._release(idx_b, token_b)  # noqa: SLF001
