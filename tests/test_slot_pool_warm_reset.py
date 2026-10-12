from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from alan import slot_pool as sp
from alan.slot_pool import PoolBusyError, SlotPool


def _git(path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603,S607
        ["git", *args], cwd=str(path), check=True, capture_output=True
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "T")
    (path / "README.md").write_text("hi\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    return root


def _clone_into_slot(repo: Path, slot: Path) -> None:
    slot.mkdir(parents=True)
    subprocess.run(  # noqa: S603,S607
        ["git", "clone", "-q", str(repo), str(slot)], check=True, capture_output=True
    )


def test_reset_all_preserves_live_lease_bookkeeping(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    idx, token = pool._claim(0)  # noqa: SLF001
    assert idx == 0

    pool.reset_all()

    assert pool._free == {1}  # noqa: SLF001
    assert 0 in pool._lease_times  # noqa: SLF001
    second_idx, second_token = pool._claim()  # noqa: SLF001
    assert second_idx == 1
    pool._release(second_idx, second_token)  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001


def test_reset_all_never_cleans_leased_clone(tmp_path: Path, repo: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(repo))
    _clone_into_slot(repo, pool.slot_path(0))
    _clone_into_slot(repo, pool.slot_path(1))
    leased_dirty = pool.slot_path(0) / "dirty.txt"
    leased_dirty.write_text("mid-implementation work\n")
    free_dirty = pool.slot_path(1) / "dirty.txt"
    free_dirty.write_text("stale residue\n")
    idx, token = pool._claim(0)  # noqa: SLF001

    pool.reset_all()

    assert leased_dirty.exists()
    assert not free_dirty.exists()
    assert pool._free == {1}  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001


def test_begin_dispose_raises_after_reset_all_with_live_lease(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    idx, token = pool._claim(0)  # noqa: SLF001

    pool.reset_all()

    with pytest.raises(PoolBusyError, match="has active leases"):
        pool.begin_dispose()
    assert pool._quiescing is False  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001


def test_begin_dispose_raises_while_reset_all_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    cleaning_started = threading.Event()
    finish_cleaning = threading.Event()

    def _blocking_clean(path: Path) -> None:
        cleaning_started.set()
        assert finish_cleaning.wait(timeout=10)

    monkeypatch.setattr(sp, "_clean_slot", _blocking_clean)
    reset_thread = threading.Thread(target=pool.reset_all)
    reset_thread.start()
    try:
        assert cleaning_started.wait(timeout=10)
        with pytest.raises(PoolBusyError, match="has slot cleanup in flight"):
            pool.begin_dispose()
    finally:
        finish_cleaning.set()
        reset_thread.join(timeout=10)

    assert pool._free == {0}  # noqa: SLF001
    pool.begin_dispose()
    assert pool._quiescing is True  # noqa: SLF001


def test_reset_all_refrees_slots_when_clean_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)

    def _boom(path: Path) -> None:
        raise RuntimeError("git hung under I/O starvation")

    monkeypatch.setattr(sp, "_clean_slot", _boom)
    with pytest.raises(RuntimeError, match="git hung"):
        pool.reset_all()

    assert pool._free == {0}  # noqa: SLF001
    idx, token = pool._claim()  # noqa: SLF001
    assert idx == 0
    pool._release(idx, token)  # noqa: SLF001


def test_reset_all_bails_out_on_quiescing_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    cleaned: list[Path] = []
    monkeypatch.setattr(sp, "_clean_slot", cleaned.append)
    pool.begin_dispose()

    pool.reset_all()

    assert cleaned == []
    assert pool._free == {0}  # noqa: SLF001
    assert pool._quiescing is True  # noqa: SLF001


def test_release_after_reset_all_returns_leased_slot_to_free(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    idx, token = pool._claim(0)  # noqa: SLF001

    pool.reset_all()
    assert pool._free == set()  # noqa: SLF001

    pool._release(idx, token)  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001
    reclaimed_idx, reclaimed_token = pool._claim()  # noqa: SLF001
    assert reclaimed_idx == 0
    pool._release(reclaimed_idx, reclaimed_token)  # noqa: SLF001
