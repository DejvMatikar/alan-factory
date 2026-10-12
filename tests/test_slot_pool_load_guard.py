from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest

from alan import fleet_load
from alan import slot_pool as sp
from alan.clock import Monotonic
from alan.engine_config import EngineConfig, RepoIdentity
from alan.routes import infra as infra_mod
from alan.slot_pool import PoolBusyError, SlotPool
from tests.fake_clock import FakeClock


def _engine_config() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        warmup_commands=[["cargo", "check"]],
    )


def test_claim_with_no_load_sampler_never_waits_on_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path))
    wait_calls: list[float | None] = []
    monkeypatch.setattr(pool._cond, "wait", lambda timeout=None: wait_calls.append(timeout))  # noqa: SLF001
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert wait_calls == []
    pool._release(idx, tok)  # noqa: SLF001


def test_load_at_threshold_admits_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), load_sampler=lambda: 8.0)
    wait_calls: list[float | None] = []
    monkeypatch.setattr(pool._cond, "wait", lambda timeout=None: wait_calls.append(timeout))  # noqa: SLF001
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert wait_calls == []
    pool._release(idx, tok)  # noqa: SLF001


def test_load_below_threshold_admits_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), load_sampler=lambda: 1.0)
    wait_calls: list[float | None] = []
    monkeypatch.setattr(pool._cond, "wait", lambda timeout=None: wait_calls.append(timeout))  # noqa: SLF001
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert wait_calls == []
    pool._release(idx, tok)  # noqa: SLF001


def test_load_above_then_below_threshold_defers_then_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    clock = FakeClock()
    readings = iter([20.0, 4.0])
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: next(readings),
    )
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None = None) -> None:
        wait_calls.append(timeout)
        clock.advance(timeout or 0.0)

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert wait_calls == [0.01]
    pool._release(idx, tok)  # noqa: SLF001


def test_load_wait_tick_reruns_reclaim_and_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    clock = FakeClock()
    readings = iter([20.0, 20.0, 4.0])
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: next(readings),
    )
    reclaim_calls: list[int] = []
    expire_calls: list[int] = []
    monkeypatch.setattr(pool, "_reclaim_stale_under_lock", lambda: reclaim_calls.append(1))  # noqa: SLF001
    monkeypatch.setattr(pool, "_expire_reservations_under_lock", lambda: expire_calls.append(1))  # noqa: SLF001
    monkeypatch.setattr(
        pool._cond, "wait",  # noqa: SLF001
        lambda timeout=None: clock.advance(timeout or 0.0),
    )
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert len(reclaim_calls) == 2
    assert len(expire_calls) == 2
    pool._release(idx, tok)  # noqa: SLF001


def test_load_defer_bound_reached_admits_claim_anyway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    monkeypatch.setattr(sp, "_LOAD_DEFER_MAX_S", 0.05)
    clock = FakeClock()
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: 100.0,
    )
    monkeypatch.setattr(
        pool._cond, "wait",  # noqa: SLF001
        lambda timeout=None: clock.advance(timeout or 0.0),
    )
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    pool._release(idx, tok)  # noqa: SLF001


def test_first_deferral_warning_logged_once_naming_load_threshold_cores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    clock = FakeClock()
    readings = iter([20.0, 4.0])
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: next(readings),
    )
    monkeypatch.setattr(
        pool._cond, "wait",  # noqa: SLF001
        lambda timeout=None: clock.advance(timeout or 0.0),
    )
    with caplog.at_level(logging.WARNING, logger="alan.slot_pool"):
        idx, tok = pool._claim()  # noqa: SLF001
    deferral = [r for r in caplog.records if "deferring claim" in r.getMessage()]
    assert len(deferral) == 1
    message = deferral[0].getMessage()
    assert "20.00" in message
    assert "8.00" in message
    assert "cores=4" in message
    pool._release(idx, tok)  # noqa: SLF001


def test_bound_reached_warning_logged_once_and_distinct_from_deferral_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    monkeypatch.setattr(sp, "_LOAD_DEFER_MAX_S", 0.02)
    clock = FakeClock()
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: 100.0,
    )
    monkeypatch.setattr(
        pool._cond, "wait",  # noqa: SLF001
        lambda timeout=None: clock.advance(timeout or 0.0),
    )
    with caplog.at_level(logging.WARNING, logger="alan.slot_pool"):
        idx, tok = pool._claim()  # noqa: SLF001
    deferral = [r for r in caplog.records if "deferring claim" in r.getMessage()]
    admitted = [r for r in caplog.records if "admitting claim anyway" in r.getMessage()]
    assert len(deferral) == 1
    assert len(admitted) == 1
    pool._release(idx, tok)  # noqa: SLF001


def test_load_wait_never_calls_time_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    clock = FakeClock()
    readings = iter([20.0, 4.0])
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: next(readings),
    )

    def boom(_seconds: float) -> None:
        raise AssertionError("must not call time.sleep while holding the claim lock")

    monkeypatch.setattr(time, "sleep", boom)
    monkeypatch.setattr(
        pool._cond, "wait",  # noqa: SLF001
        lambda timeout=None: clock.advance(timeout or 0.0),
    )
    idx, tok = pool._claim()  # noqa: SLF001
    pool._release(idx, tok)  # noqa: SLF001


def test_load_wait_releases_lock_during_cond_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.05)
    monkeypatch.setattr(sp, "_LOAD_DEFER_MAX_S", 0.2)
    pool = SlotPool(root=tmp_path, size=2, repo_root=str(tmp_path), load_sampler=lambda: 100.0)
    entered_wait = threading.Event()
    real_wait = pool._cond.wait  # noqa: SLF001

    def spy_wait(timeout: float | None = None) -> None:
        entered_wait.set()
        real_wait(timeout)

    monkeypatch.setattr(pool._cond, "wait", spy_wait)  # noqa: SLF001

    result: list[tuple[int, Monotonic]] = []

    def blocker() -> None:
        result.append(pool._claim())  # noqa: SLF001

    t = threading.Thread(target=blocker)
    t.start()
    assert entered_wait.wait(timeout=2.0)
    acquired = pool._cond.acquire(timeout=1.0)  # noqa: SLF001
    assert acquired, "lock not released during cond.wait — load guard is holding it"
    pool._cond.release()  # noqa: SLF001
    t.join(timeout=2.0)
    assert not t.is_alive()
    idx, tok = result[0]
    pool._release(idx, tok)  # noqa: SLF001


def test_quiescing_raised_during_load_wait_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    monkeypatch.setattr(sp, "_LOAD_TICK_S", 0.01)
    clock = FakeClock()
    pool = SlotPool(
        root=tmp_path, size=1, repo_root=str(tmp_path),
        clock=clock, load_sampler=lambda: 100.0,
    )

    def fake_wait(timeout: float | None = None) -> None:
        clock.advance(timeout or 0.0)
        pool._quiescing = True  # noqa: SLF001

    monkeypatch.setattr(pool._cond, "wait", fake_wait)  # noqa: SLF001
    with pytest.raises(PoolBusyError):
        pool._claim()  # noqa: SLF001


def test_load_guard_applies_to_preferred_slot_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    sample_calls: list[int] = []

    def sampler() -> float:
        sample_calls.append(1)
        return 1.0

    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), load_sampler=sampler)
    idx, tok = pool._claim(preferred_idx=0)  # noqa: SLF001
    assert idx == 0
    assert sample_calls
    pool._release(idx, tok)  # noqa: SLF001


def test_load_guard_applies_to_ordinary_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_load, "host_cores", lambda: 4)
    sample_calls: list[int] = []

    def sampler() -> float:
        sample_calls.append(1)
        return 1.0

    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), load_sampler=sampler)
    idx, tok = pool._claim()  # noqa: SLF001
    assert idx == 0
    assert sample_calls
    pool._release(idx, tok)  # noqa: SLF001


class _RecordingSlotPool:
    last_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        _RecordingSlotPool.last_kwargs = kwargs


def test_create_slot_pool_wires_read_load1_as_load_sampler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    monkeypatch.setattr(
        infra_mod, "resolve_repo_root", lambda: "https://github.com/DejvMatikar/alan.git"
    )
    monkeypatch.setattr(infra_mod, "SlotPool", _RecordingSlotPool)
    infra_mod._create_slot_pool(2)  # noqa: SLF001
    assert _RecordingSlotPool.last_kwargs["load_sampler"] is fleet_load.read_load1


def test_create_slot_pool_for_engine_wires_read_load1_as_load_sampler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(infra_mod, "SlotPool", _RecordingSlotPool)
    infra_mod._create_slot_pool_for_engine(tmp_path, 1, _engine_config())  # noqa: SLF001
    assert _RecordingSlotPool.last_kwargs["load_sampler"] is fleet_load.read_load1
