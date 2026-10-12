from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from alan import slot_pool as sp
from alan.slot_pool import SlotPool
from tests.fake_clock import FakeClock

_LEASE_RELEASE = ("lease", "release")
_HOLD_WAIT_TIMEOUT_S = 5.0
_CLEAN_WAIT_TIMEOUT_S = 5.0
_DRAIN_TIMEOUT_S = 5.0
_JOIN_TIMEOUT_S = 5.0
_RESET_ALL_PROGRESS_TIMEOUT_S = 5.0
_RESET_ALL_PROGRESS_GRACE_S = 0.02
_RESET_ALL_POLL_INTERVAL_S = 0.001
_RESET_ALL_COMPLETE_TIMEOUT_S = 5.0
_POST_RESET_ALL_SETTLE_S = 0.05

_1012_SCHEDULE = ["lease", "reset_all", "lease", "release", "release"]
_1012_SIZE = 2
_DEGENERATE_SCHEDULE = ["lease", "release"]
_DEGENERATE_SIZE = 1
_DOUBLE_CLAIM_SCHEDULE = ["lease", "reset_all", "lease", "lease"]
_DOUBLE_CLAIM_SIZE = 2


class _HarnessTimeoutError(RuntimeError):
    pass


class _OpenHold:
    __slots__ = ("idx", "release_event")

    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.release_event = threading.Event()


class _ScheduleHarness:
    def __init__(self, pool: SlotPool, clock: FakeClock) -> None:
        self.pool = pool
        self.clock = clock
        self.live_holders: dict[int, int] = {}
        self.clean_during_live_lease: list[int] = []
        self.double_assignment: list[int] = []
        self.max_open_sessions = 0
        self._open_sessions = 0
        self._lock = threading.Lock()
        self._errors: list[BaseException] = []
        self._threads: list[threading.Thread] = []
        self._open_holds: list[_OpenHold] = []
        self._clean_entered = threading.Event()
        self._clean_proceed = threading.Event()
        self._clean_gate_armed = True
        self._reset_all_progressed = threading.Event()
        self._reset_all_thread: threading.Thread | None = None
        self._counter = 0

    def stub_is_git_clone(self, _path: Path) -> bool:
        self._reset_all_progressed.set()
        return True

    def stub_clean_slot(self, path: Path) -> None:
        idx = _slot_idx_from_path(path)
        gate = False
        with self._lock:
            if self.live_holders.get(idx, 0) > 0:
                self.clean_during_live_lease.append(idx)
            if self._clean_gate_armed:
                self._clean_gate_armed = False
                gate = True
        if gate:
            self._clean_entered.set()
            if not self._clean_proceed.wait(timeout=_CLEAN_WAIT_TIMEOUT_S):
                raise _HarnessTimeoutError("clean_slot gate did not proceed in time")

    def run_schedule(self, schedule: list[str]) -> None:
        for step in schedule:
            if step == "lease":
                self._spawn(self._run_lease)
            elif step == "release":
                self._release_one()
            elif step == "reset_all":
                self._reset_all_thread = self._spawn(self._run_reset_all)
                self._wait_for_reset_all_progress(self._reset_all_thread)
            else:  # pragma: no cover - schedule alphabet is closed
                raise AssertionError(f"unknown schedule step {step!r}")
        self._drain_and_join()

    def _spawn(self, target: object) -> threading.Thread:
        thread = threading.Thread(target=target)  # type: ignore[arg-type]
        thread.start()
        self._threads.append(thread)
        return thread

    def _wait_for_reset_all_progress(self, thread: threading.Thread) -> None:
        deadline = time.monotonic() + _RESET_ALL_PROGRESS_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._reset_all_progressed.is_set() or not thread.is_alive():
                time.sleep(_RESET_ALL_PROGRESS_GRACE_S)
                return
            time.sleep(_RESET_ALL_POLL_INTERVAL_S)
        raise _HarnessTimeoutError("reset_all did not progress past its locked section in time")

    def _release_one(self) -> None:
        with self._lock:
            hold = self._open_holds.pop(0) if self._open_holds else None
        if hold is not None:
            hold.release_event.set()

    def _run_reset_all(self) -> None:
        try:
            self.clock.advance(1.0)
            self.pool.reset_all()
        except BaseException as exc:  # noqa: BLE001 - captured for main-thread re-raise
            with self._lock:
                self._errors.append(exc)

    def _run_lease(self) -> None:
        with self._lock:
            self._counter += 1
            n = self._counter
        try:
            with self.pool.lease(f"task-{n}", f"inv-{n}") as (_path, _base_ref, _branch, idx):
                hold = _OpenHold(idx)
                with self._lock:
                    if self.live_holders.get(idx, 0) > 0:
                        self.double_assignment.append(idx)
                    self.live_holders[idx] = self.live_holders.get(idx, 0) + 1
                    self._open_sessions += 1
                    self.max_open_sessions = max(self.max_open_sessions, self._open_sessions)
                    self._open_holds.append(hold)
                if not hold.release_event.wait(timeout=_HOLD_WAIT_TIMEOUT_S):
                    raise _HarnessTimeoutError(f"lease hold on slot {idx} was not released in time")
                with self._lock:
                    self.live_holders[idx] -= 1
                    self._open_sessions -= 1
        except BaseException as exc:  # noqa: BLE001 - captured for main-thread re-raise
            with self._lock:
                self._errors.append(exc)

    def _drain_and_join(self) -> None:
        self._clean_proceed.set()
        if self._reset_all_thread is not None:
            self._reset_all_thread.join(timeout=_RESET_ALL_COMPLETE_TIMEOUT_S)
            if self._reset_all_thread.is_alive():
                raise _HarnessTimeoutError("reset_all did not complete in time")
            time.sleep(_POST_RESET_ALL_SETTLE_S)
        deadline = time.monotonic() + _DRAIN_TIMEOUT_S
        while time.monotonic() < deadline:
            with self._lock:
                hold = self._open_holds.pop(0) if self._open_holds else None
            if hold is not None:
                hold.release_event.set()
                continue
            if all(not t.is_alive() for t in self._threads):
                break
            time.sleep(0.01)
        for thread in self._threads:
            thread.join(timeout=_JOIN_TIMEOUT_S)
            if thread.is_alive():
                raise _HarnessTimeoutError(f"thread {thread.name} did not finish in time")
        if self._errors:
            raise self._errors[0]


def _insert_reset_all(pair: tuple[list[str], int | None]) -> list[str]:
    base, pos = pair
    if pos is None:
        return base
    idx = min(pos, len(base))
    return [*base[:idx], "reset_all", *base[idx:]]


def _schedules() -> st.SearchStrategy[list[str]]:
    return st.tuples(
        st.lists(st.sampled_from(_LEASE_RELEASE), min_size=1, max_size=20),
        st.one_of(st.none(), st.integers(min_value=0, max_value=20)),
    ).map(_insert_reset_all)


def _slot_idx_from_path(path: Path) -> int:
    name = path.name
    assert name.startswith("slot_"), f"unexpected slot path {path}"
    return int(name.removeprefix("slot_"))


def _install_stubs(monkeypatch: pytest.MonkeyPatch, harness: _ScheduleHarness) -> None:
    monkeypatch.setattr(sp, "is_git_clone", harness.stub_is_git_clone)
    monkeypatch.setattr(sp, "_clean_slot", harness.stub_clean_slot)
    monkeypatch.setattr(sp, "_init_slot", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        sp,
        "_prepare_new_branch",
        lambda path, task_id, inv_id, forge_target: (  # noqa: ARG005
            "origin/main",
            f"teams/{task_id}-{inv_id}",
        ),
    )
    monkeypatch.setattr(
        sp,
        "_prepare_existing_branch",
        lambda path, branch, forge_target: (f"origin/{branch}", branch),  # noqa: ARG005
    )
    monkeypatch.setattr(
        sp,
        "_prepare_resume",
        lambda path, task_id, inv_id, existing_branch, base_sha, forge_target: (  # noqa: ARG005
            base_sha,
            existing_branch or f"teams/{task_id}-{inv_id}",
            0,
            None,
        ),
    )


def _run_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int, schedule: list[str]
) -> _ScheduleHarness:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path, size=size, repo_root=str(tmp_path), clock=clock)
    harness = _ScheduleHarness(pool, clock)
    _install_stubs(monkeypatch, harness)
    harness.run_schedule(schedule)
    return harness


def test_harness_replays_the_1012_interleaving_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _run_property(tmp_path, monkeypatch, _1012_SIZE, _1012_SCHEDULE)
    assert not harness.clean_during_live_lease
    assert not harness.double_assignment
    assert harness.max_open_sessions <= _1012_SIZE


@given(
    schedule=_schedules(),
    size=st.integers(min_value=1, max_value=4),
)
@example(schedule=_1012_SCHEDULE, size=_1012_SIZE)
@example(schedule=_DEGENERATE_SCHEDULE, size=_DEGENERATE_SIZE)
@example(schedule=_DOUBLE_CLAIM_SCHEDULE, size=_DOUBLE_CLAIM_SIZE)
def test_lease_exclusivity_under_reset_all_interleavings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schedule: list[str], size: int
) -> None:
    harness = _run_property(tmp_path, monkeypatch, size, schedule)
    assert not harness.clean_during_live_lease, harness.clean_during_live_lease


@given(
    schedule=_schedules(),
    size=st.integers(min_value=1, max_value=4),
)
@example(schedule=_1012_SCHEDULE, size=_1012_SIZE)
@example(schedule=_DEGENERATE_SCHEDULE, size=_DEGENERATE_SIZE)
@example(schedule=_DOUBLE_CLAIM_SCHEDULE, size=_DOUBLE_CLAIM_SIZE)
def test_no_double_slot_assignment_under_generated_schedules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schedule: list[str], size: int
) -> None:
    harness = _run_property(tmp_path, monkeypatch, size, schedule)
    assert not harness.double_assignment, harness.double_assignment


@given(
    schedule=_schedules(),
    size=st.integers(min_value=1, max_value=4),
)
@example(schedule=_1012_SCHEDULE, size=_1012_SIZE)
@example(schedule=_DEGENERATE_SCHEDULE, size=_DEGENERATE_SIZE)
@example(schedule=_DOUBLE_CLAIM_SCHEDULE, size=_DOUBLE_CLAIM_SIZE)
def test_capacity_bounds_hold_after_every_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schedule: list[str], size: int
) -> None:
    harness = _run_property(tmp_path, monkeypatch, size, schedule)
    assert harness.max_open_sessions <= size, (harness.max_open_sessions, size)


@given(
    advances=st.lists(
        st.floats(min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=8,
    ),
)
@example(advances=[1.0, 1.0])
def test_lease_tokens_are_unique_when_clock_advances(
    tmp_path: Path, advances: list[float]
) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=clock)

    tokens = []
    for seconds in advances:
        clock.advance(seconds)
        idx, token = pool._claim()  # noqa: SLF001
        tokens.append(token)
        pool._release(idx, token)  # noqa: SLF001
    assert len(tokens) == len(set(tokens)), "clock-advancing claims produced colliding tokens"

    clock.advance(advances[0])
    idx_a, token_a = pool._claim()  # noqa: SLF001
    clock.advance(advances[0])
    pool._release(idx_a, token_a)  # noqa: SLF001
    clock.advance(advances[0])
    idx_b, token_b = pool._claim()  # noqa: SLF001
    assert token_b != token_a

    pool._release(idx_a, token_a)  # noqa: SLF001 - stale release, must be a no-op
    with pool._cond:  # noqa: SLF001
        assert idx_b not in pool._free, "stale release evicted the new holder"  # noqa: SLF001
        assert pool._lease_times.get(idx_b) == token_b  # noqa: SLF001
    pool._release(idx_b, token_b)  # noqa: SLF001


@given(
    extra_seconds=st.floats(
        min_value=0.001, max_value=10_000.0, allow_nan=False, allow_infinity=False
    ),
)
@example(extra_seconds=1.0)
def test_stale_reclaim_permits_a_second_holder(tmp_path: Path, extra_seconds: float) -> None:
    clock = FakeClock()
    pool = SlotPool(root=tmp_path, size=1, repo_root=str(tmp_path), clock=clock)
    idx_a, token_a = pool._claim()  # noqa: SLF001

    clock.advance(sp._STALE_LEASE_TIMEOUT_S + extra_seconds)  # noqa: SLF001
    with pool._cond:  # noqa: SLF001
        pool._reclaim_stale_under_lock()  # noqa: SLF001
        assert idx_a in pool._free  # noqa: SLF001
        assert idx_a not in pool._lease_times  # noqa: SLF001

    idx_b, token_b = pool._claim()  # noqa: SLF001
    assert idx_b == idx_a
    assert token_b != token_a
    pool._release(idx_b, token_b)  # noqa: SLF001
