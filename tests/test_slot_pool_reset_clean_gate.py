from __future__ import annotations

from pathlib import Path

import pytest

from alan import slot_pool as sp
from alan.slot_pool import Reservation, SlotPool


def test_reset_all_clean_false_leaves_dirty_unleased_slot_untouched(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    slot_0 = pool.slot_path(0)
    slot_0.mkdir(parents=True)
    (slot_0 / ".git").mkdir()
    dirty = slot_0 / "dirty.txt"
    dirty.write_text("uncommitted\n")

    pool.reset_all(clean=False)

    assert dirty.exists()
    assert 0 in pool._free  # noqa: SLF001


def test_reset_all_clean_false_still_applies_reservation_and_free_bookkeeping(
    tmp_path: Path,
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))

    pool.reset_all({pool.slot_path(0): Reservation("sess-1", adoptable=True)}, clean=False)

    assert pool._reserved == {0: "sess-1"}  # noqa: SLF001
    assert 0 not in pool._free  # noqa: SLF001
    assert 1 in pool._free  # noqa: SLF001


def test_reset_all_empty_reserved_mapping_clears_reservations_with_clean_false(
    tmp_path: Path,
) -> None:
    pool = SlotPool(
        root=tmp_path / "slots", size=1, repo_root=str(tmp_path),
        reserved={tmp_path / "slots" / "slot_0": Reservation("sess-1", adoptable=True)},
    )
    assert pool._reserved == {0: "sess-1"}  # noqa: SLF001

    pool.reset_all({}, clean=False)

    assert pool._reserved == {}  # noqa: SLF001
    assert 0 in pool._free  # noqa: SLF001


def test_reset_all_no_arg_defaults_to_clean_true_and_cleans_every_unleased_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    for i in range(2):
        slot = pool.slot_path(i)
        slot.mkdir(parents=True)
        (slot / ".git").mkdir()

    cleaned: list[Path] = []
    monkeypatch.setattr(sp, "_clean_slot", lambda path: cleaned.append(path))

    pool.reset_all()

    assert set(cleaned) == {pool.slot_path(0), pool.slot_path(1)}


def test_reset_all_unresolved_reserved_path_warning_names_the_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    in_root_but_invalid = pool.slot_path(9)

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        pool.reset_all({in_root_but_invalid: Reservation("sess-x", adoptable=True)}, clean=False)

    assert any(str(in_root_but_invalid) in r.message for r in caplog.records)


def test_reset_all_reserved_path_outside_pool_root_is_not_warned(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    sibling_pool_path = tmp_path / "other-engine-slots" / "slot_0"

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        pool.reset_all({sibling_pool_path: Reservation("sess-x", adoptable=True)}, clean=False)

    assert not any(r.levelname == "WARNING" for r in caplog.records)
