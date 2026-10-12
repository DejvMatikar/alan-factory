from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from alan.clock import Monotonic
from alan.slot_pool import Reservation, SlotPool, SlotReservedError
from tests.fake_clock import FakeClock


def test_reset_all_reserved_excludes_slot_from_free(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    reserved = {pool.slot_path(0): Reservation("sess-a", adoptable=True)}
    pool.reset_all(reserved)
    assert pool._free == {1}  # noqa: SLF001
    assert pool._reserved == {0: "sess-a"}  # noqa: SLF001


def test_reset_all_reserved_does_not_clean_dirty_reserved_slot(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    dirty = pool.slot_path(0) / "dirty.txt"
    dirty.write_text("mid-implementation work\n")
    pool.reset_all({pool.slot_path(0): Reservation("sess-a", adoptable=True)})
    assert dirty.exists()
    assert pool._free == set()  # noqa: SLF001


def test_reset_all_unresolvable_path_is_dropped(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    foreign_path = tmp_path / "not-a-slot-of-this-pool"
    pool.reset_all({foreign_path: Reservation("sess-a", adoptable=True)})
    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_reset_all_none_preserves_construction_seeded_reservation(tmp_path: Path) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=2, repo_root=str(tmp_path),
        reserved={(tmp_path / "slots" / "slot_0"): Reservation("sess-boot", adoptable=True)},
    )
    assert pool._reserved == {0: "sess-boot"}  # noqa: SLF001
    assert pool._free == {1}  # noqa: SLF001

    pool.reset_all()

    assert pool._reserved == {0: "sess-boot"}  # noqa: SLF001
    assert pool._free == {1}  # noqa: SLF001


def test_reset_all_explicit_empty_dict_clears_reservation(tmp_path: Path) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path),
        reserved={(tmp_path / "slots" / "slot_0"): Reservation("sess-boot", adoptable=True)},
    )
    pool.reset_all({})
    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_constructor_reserved_is_zero_io(tmp_path: Path) -> None:
    root = tmp_path / "never-created"
    pool = SlotPool(
        root=root, size=1, repo_root=str(tmp_path),
        reserved={root / "slot_0": Reservation("sess-a", adoptable=True)},
    )
    assert not root.exists()
    assert pool._reserved == {0: "sess-a"}  # noqa: SLF001


def test_adopt_succeeds_for_matching_owner_token(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    idx = pool.adopt(pool.slot_path(0), "sess-owner")
    assert idx == 0
    assert 0 not in pool._reserved  # noqa: SLF001
    assert 0 in pool._lease_times  # noqa: SLF001
    assert 0 not in pool._free  # noqa: SLF001


def test_adopt_raises_for_foreign_claimant(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    with pytest.raises(SlotReservedError):
        pool.adopt(pool.slot_path(0), "sess-impostor")
    assert pool._reserved == {0: "sess-owner"}  # noqa: SLF001
    assert 0 not in pool._lease_times  # noqa: SLF001


def test_adopt_raises_for_unreserved_path(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    with pytest.raises(SlotReservedError):
        pool.adopt(pool.slot_path(0), "sess-anything")


def test_adopt_raises_for_path_outside_pool(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    with pytest.raises(SlotReservedError):
        pool.adopt(tmp_path / "somewhere-else", "sess-owner")


def test_release_reservation_returns_slot_to_free_dirty(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    dirty = pool.slot_path(0) / "dirty.txt"
    dirty.write_text("residue\n")
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    pool.release_reservation(pool.slot_path(0))

    assert pool._free == {0}  # noqa: SLF001
    assert 0 not in pool._reserved  # noqa: SLF001
    assert dirty.exists()


def test_release_reservation_noop_for_unreserved_path(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.release_reservation(pool.slot_path(0))
    assert pool._free == {0}  # noqa: SLF001


def test_release_reservation_noop_for_path_outside_pool(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    pool.release_reservation(tmp_path / "elsewhere")
    assert pool._reserved == {0: "sess-owner"}  # noqa: SLF001


def test_expire_reservations_under_lock_releases_after_ttl(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    clock.advance(3601.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_expire_reservations_under_lock_leaves_fresh_reservation_alone(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    clock.advance(10.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {0: "sess-owner"}  # noqa: SLF001


def test_claim_any_slot_skips_reserved_slot(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    idx, token = pool._claim()  # noqa: SLF001
    assert idx == 1
    pool._release(idx, token)  # noqa: SLF001


def test_claim_preferred_reserved_slot_blocks_until_released(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    result: list[tuple[int, Monotonic]] = []
    started = threading.Event()

    def _run() -> None:
        started.set()
        result.append(pool._claim(0))  # noqa: SLF001

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        assert started.wait(timeout=5.0)
        time.sleep(0.2)
        assert result == []
        pool.release_reservation(pool.slot_path(0))
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert result and result[0][0] == 0
    finally:
        if thread.is_alive():
            pool.release_reservation(pool.slot_path(0))
            thread.join(timeout=5.0)


def test_claim_preferred_reserved_slot_recovers_via_ttl_expiry(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    result: list[tuple[int, Monotonic]] = []

    def _run() -> None:
        result.append(pool._claim(0))  # noqa: SLF001

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        clock.advance(3601.0)
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert result and result[0][0] == 0
    finally:
        if thread.is_alive():
            pool.release_reservation(pool.slot_path(0))
            thread.join(timeout=5.0)


def test_reset_all_non_adoptable_reservation_does_not_exclude_slot_from_free(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("hold:ws-x", adoptable=False)})
    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_reset_all_non_adoptable_reservation_still_excludes_slot_from_clean(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    dirty = pool.slot_path(0) / "dirty.txt"
    dirty.write_text("mid-implementation work\n")
    pool.reset_all({pool.slot_path(0): Reservation("hold:ws-x", adoptable=False)})
    assert dirty.exists()


def test_clean_excluded_clears_after_reset_all_completes(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("hold:ws-x", adoptable=False)})
    assert pool._clean_excluded == {}  # noqa: SLF001


def test_clean_excluded_clears_when_boot_clean_refused(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("hold:ws-x", adoptable=False)}, clean=False)
    assert pool._clean_excluded == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_apply_reserved_under_lock_moves_reservation_between_adoptable_states(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-a", adoptable=True)}, clean=False)
    assert pool._reserved == {0: "sess-a"}  # noqa: SLF001
    assert 0 not in pool._free  # noqa: SLF001

    pool.reset_all({pool.slot_path(0): Reservation("sess-a", adoptable=False)}, clean=False)

    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_boot_clean_refused_with_a_hold_still_cleans_that_slot_on_its_next_claim(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    slot_0 = pool.slot_path(0)
    slot_0.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(slot_0), check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=str(slot_0), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(slot_0), check=True)
    (slot_0 / "committed.txt").write_text("base\n")
    subprocess.run(["git", "add", "committed.txt"], cwd=str(slot_0), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(slot_0), check=True)
    (slot_0 / "untracked.txt").write_text("stray\n")

    pool.reset_all({slot_0: Reservation("hold:ws-x", adoptable=False)}, clean=False)
    assert (slot_0 / "untracked.txt").exists()

    with pool.lease("t1", "inv_001") as (path, _base_ref, _branch, idx):
        assert idx == 0
        assert path == slot_0
        assert not (slot_0 / "untracked.txt").exists()
