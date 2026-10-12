from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from alan import slot_pool as slot_pool_mod
from alan import worker_records
from alan.slot_pool import Reservation, SlotPool
from tests.fake_clock import FakeClock


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def _write_record(session_id: str, *, pid: int | None, slot_path: str) -> None:
    identity = worker_records.ps_identity(pid) if pid is not None else None
    lstart, cmdline = identity if identity is not None else (None, None)
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id="t1", workspace_id="ws-1", team="implementation",
            inv_id="inv_001", node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path=slot_path, session_id=session_id,
            output_path=f"{slot_path}/stream.jsonl",
            pid=pid, started_at=1.0, lstart=lstart, cmdline=cmdline,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(),
        )
    )


def test_adopted_lease_adopts_and_yields_idx(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    with pool.adopted_lease(pool.slot_path(0), "sess-owner") as idx:
        assert idx == 0
        assert 0 not in pool._reserved  # noqa: SLF001
        assert 0 in pool._lease_times  # noqa: SLF001


def test_adopted_lease_releases_slot_on_exit(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    with pool.adopted_lease(pool.slot_path(0), "sess-owner") as idx:
        del idx
    assert pool._free == {0}  # noqa: SLF001
    assert 0 not in pool._lease_times  # noqa: SLF001


def test_adopted_lease_does_not_clean_the_working_tree(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    (pool.slot_path(0) / ".git").mkdir(parents=True)
    dirty = pool.slot_path(0) / "in-flight.txt"
    dirty.write_text("uncommitted work\n")
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    with pool.adopted_lease(pool.slot_path(0), "sess-owner"):
        assert dirty.exists()
    assert dirty.exists()


def test_expire_reservations_re_arms_when_record_probes_live(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    _write_record("sess-live", pid=None, slot_path=str(pool.slot_path(0)))
    pool.reset_all({pool.slot_path(0): Reservation("sess-live", adoptable=True)})

    clock.advance(3601.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {0: "sess-live"}  # noqa: SLF001
    assert pool._reserved_at[0] == clock.monotonic()  # noqa: SLF001


def test_expire_reservations_releases_when_record_confirmed_dead(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    _write_record("sess-dead", pid=999999, slot_path=str(pool.slot_path(0)))
    pool.reset_all({pool.slot_path(0): Reservation("sess-dead", adoptable=True)})

    clock.advance(3601.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_session_still_live_false_when_no_matching_record(tmp_path: Path) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    assert pool._session_still_live("sess-unrecorded") is False  # noqa: SLF001


def test_expire_reservations_skips_mutation_when_reservation_changed_during_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    _write_record("sess-a", pid=None, slot_path=str(pool.slot_path(0)))
    pool.reset_all({pool.slot_path(0): Reservation("sess-a", adoptable=True)})
    clock.advance(3601.0)

    def _probe_and_mutate(session_id: str) -> bool:
        del session_id
        pool._reserved[0] = "sess-b"  # noqa: SLF001
        return True

    monkeypatch.setattr(pool, "_session_still_live", _probe_and_mutate)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {0: "sess-b"}  # noqa: SLF001


def test_expire_reservations_skips_entry_removed_by_earlier_iteration_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path), clock=clock)
    _write_record("sess-a", pid=None, slot_path=str(pool.slot_path(0)))
    _write_record("sess-b", pid=None, slot_path=str(pool.slot_path(1)))
    pool.reset_all({
        pool.slot_path(0): Reservation("sess-a", adoptable=True),
        pool.slot_path(1): Reservation("sess-b", adoptable=True),
    })
    clock.advance(3601.0)

    def _probe_and_remove_other(session_id: str) -> bool:
        if session_id == "sess-a":
            del pool._reserved[1]  # noqa: SLF001
            del pool._reserved_at[1]  # noqa: SLF001
        return False

    monkeypatch.setattr(pool, "_session_still_live", _probe_and_remove_other)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {}  # noqa: SLF001
    assert pool._reserved_at == {}  # noqa: SLF001


def test_expire_reservations_releases_when_no_matching_record(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-unrecorded", adoptable=True)})

    clock.advance(3601.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {}  # noqa: SLF001


def test_expire_reservations_never_reconsiders_a_never_stamped_reservation(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    pool.reset_all({pool.slot_path(0): Reservation("sess-never-stamped", adoptable=False)})
    assert pool._reserved == {}  # noqa: SLF001
    assert pool._reserved_at == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001

    clock.advance(10 * 3601.0)
    with pool._cond:  # noqa: SLF001
        pool._expire_reservations_under_lock()  # noqa: SLF001

    assert pool._reserved == {}  # noqa: SLF001
    assert pool._free == {0}  # noqa: SLF001


def test_adopted_lease_refreshes_base_with_pool_forge_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})
    calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        slot_pool_mod, "_refresh_base_best_effort",
        lambda path, forge_target: calls.append((path, forge_target)),
    )
    with pool.adopted_lease(pool.slot_path(0), "sess-owner"):
        pass
    assert calls == [(pool.slot_path(0), pool._forge_target)]  # noqa: SLF001


def test_adopted_lease_refresh_git_failure_still_yields_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    def _raise(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(slot_pool_mod, "_run_git", _raise)
    with pool.adopted_lease(pool.slot_path(0), "sess-owner") as idx:
        assert idx == 0
    assert pool._free == {0}  # noqa: SLF001
    assert 0 not in pool._lease_times  # noqa: SLF001


def test_adopted_lease_refresh_runs_outside_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    pool.reset_all({pool.slot_path(0): Reservation("sess-owner", adoptable=True)})

    refresh_entered = threading.Event()
    release_refresh = threading.Event()

    def _blocking_refresh(*_a: object, **_kw: object) -> None:
        refresh_entered.set()
        release_refresh.wait(timeout=5.0)

    monkeypatch.setattr(slot_pool_mod, "_refresh_base_best_effort", _blocking_refresh)
    entered_lease: list[int] = []

    def _adopt() -> None:
        with pool.adopted_lease(pool.slot_path(0), "sess-owner") as idx:
            entered_lease.append(idx)

    thread = threading.Thread(target=_adopt)
    thread.start()
    try:
        assert refresh_entered.wait(timeout=5.0)
        claimed_idx, token = pool._claim()  # noqa: SLF001
        assert claimed_idx == 1
        pool._release(claimed_idx, token)  # noqa: SLF001
    finally:
        release_refresh.set()
        thread.join(timeout=5.0)
    assert entered_lease == [0]


def test_expire_reservations_re_arms_on_real_live_subprocess(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path), clock=clock)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], text=True)
    try:
        _write_record("sess-realpid", pid=proc.pid, slot_path=str(pool.slot_path(0)))
        pool.reset_all({pool.slot_path(0): Reservation("sess-realpid", adoptable=True)})

        clock.advance(3601.0)
        with pool._cond:  # noqa: SLF001
            pool._expire_reservations_under_lock()  # noqa: SLF001

        assert pool._reserved == {0: "sess-realpid"}  # noqa: SLF001
    finally:
        proc.kill()
        proc.wait()
