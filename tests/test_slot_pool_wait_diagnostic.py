from __future__ import annotations

import time
from pathlib import Path

import pytest

from alan.slot_pool import Reservation, SlotPool
from tests.fake_clock import FakeClock


def test_claim_blocked_past_threshold_logs_warning_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls = {"n": 0}

    def fake_wait(timeout: float | None = None) -> bool:
        calls["n"] += 1
        clock.advance(65.0)
        pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    assert idx == 0
    assert any("blocked" in r.message for r in caplog.records)


def test_claim_blocked_under_threshold_logs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls = {"n": 0}

    def fake_wait(timeout: float | None = None) -> bool:
        calls["n"] += 1
        clock.advance(10.0)
        if calls["n"] >= 2:
            pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    assert idx == 0
    assert not any("blocked" in r.message for r in caplog.records)


def test_claim_warning_fires_only_once_per_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls = {"n": 0}

    def fake_wait(timeout: float | None = None) -> bool:
        calls["n"] += 1
        clock.advance(70.0)
        if calls["n"] >= 3:
            pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    warnings = [r for r in caplog.records if "blocked" in r.message]
    assert len(warnings) == 1


def test_claim_warning_uses_injected_clock_not_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    def fake_wait(timeout: float | None = None) -> bool:
        clock.advance(3000.0)
        pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    wall_start = time.monotonic()
    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim()  # noqa: SLF001
    wall_elapsed = time.monotonic() - wall_start
    pool._release(idx, token)  # noqa: SLF001

    assert wall_elapsed < 5.0
    assert any("blocked" in r.message for r in caplog.records)


def test_claim_blocks_without_deadline_until_slot_frees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls = {"n": 0}

    def fake_wait(timeout: float | None = None) -> bool:
        calls["n"] += 1
        clock.advance(50.0)
        if calls["n"] >= 50:
            pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    assert idx == 0
    assert calls["n"] == 50


def test_claim_warning_includes_pool_root_and_free_size_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls = {"n": 0}

    def fake_wait(timeout: float | None = None) -> bool:
        calls["n"] += 1
        clock.advance(65.0)
        if calls["n"] >= 2:
            pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim()  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    message = next(r.message for r in caplog.records if "blocked" in r.message)
    assert str(pool._root) in message  # noqa: SLF001
    assert "0/1 free" in message


def test_claim_warning_does_not_invoke_on_blocked_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    def fake_wait(timeout: float | None = None) -> bool:
        clock.advance(65.0)
        pool.release_reservation(pool.slot_path(0))
        return False

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    on_blocked_call_count = {"n": 0}

    def on_blocked() -> None:
        on_blocked_call_count["n"] += 1

    with caplog.at_level("WARNING", logger="alan.slot_pool"):
        idx, token = pool._claim(None, on_blocked)  # noqa: SLF001
    pool._release(idx, token)  # noqa: SLF001

    assert idx == 0
    assert on_blocked_call_count["n"] == 0
    assert any("blocked" in r.message for r in caplog.records)
