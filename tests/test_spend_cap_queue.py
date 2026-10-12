from __future__ import annotations

import datetime as dt_mod
import json
import math
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import queue as queue_mod
from alan.queue import SPEND_CAP_STOP_META_KEY, JobStatus, Queue
from alan.usage_limit import SPEND_CAP_HOLD_EPOCH
from tests.fake_clock import FakeClock

_REGAIN = "2026-10-01T00:00:00+00:00"
_STOPPED_AT = 1_790_000_000.0


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=_STOPPED_AT)


@pytest.fixture
def q(tmp_path: Path, clock: FakeClock) -> Iterator[Queue]:
    instance = Queue(db_path=tmp_path / "queue.db", clock=clock)
    try:
        yield instance
    finally:
        instance.close()


def _marker(q: Queue) -> dict[str, object]:
    raw = q.get_meta(SPEND_CAP_STOP_META_KEY)
    assert raw is not None
    loaded = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def test_stop_until_resume_holds_the_queue_at_the_sentinel(q: Queue) -> None:
    q.stop_until_resume(_REGAIN)
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_stored_hold_is_finite_valid_json_and_converts_to_a_date(q: Queue) -> None:
    q.stop_until_resume(_REGAIN)
    stored = q.get_meta("paused_until")
    assert stored is not None
    hold = float(stored)
    assert math.isfinite(hold)
    assert json.loads(json.dumps(hold, allow_nan=False)) == hold
    assert dt_mod.datetime.fromtimestamp(hold, tz=dt_mod.UTC).year == 2100
    assert hold * 1000 < 8.64e15


def test_stop_until_resume_stores_only_alan_owned_fields(q: Queue) -> None:
    q.stop_until_resume(_REGAIN)
    assert _marker(q) == {"regain_at": _REGAIN, "stopped_at": _STOPPED_AT}


def test_stop_until_resume_without_a_regain_date_stores_null(q: Queue) -> None:
    q.stop_until_resume(None)
    assert _marker(q) == {"regain_at": None, "stopped_at": _STOPPED_AT}


def test_claim_returns_nothing_while_stopped(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.stop_until_resume(_REGAIN)
    assert q.claim() is None


def test_stop_never_ends_on_its_own_even_years_past_the_regain_date(
    q: Queue, clock: FakeClock
) -> None:
    q.enqueue("t1", "research")
    q.stop_until_resume(_REGAIN)
    clock.advance(3 * 365 * 86400.0)
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert q.claim() is None


def test_ordinary_pause_during_a_stop_neither_ends_nor_shortens_it(
    q: Queue, clock: FakeClock
) -> None:
    q.stop_until_resume(_REGAIN)
    q.pause_until(clock.now().value + 900.0)
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH
    clock.advance(1000.0)
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_ordinary_pause_before_a_stop_is_replaced_by_the_hold(
    q: Queue, clock: FakeClock
) -> None:
    q.pause_until(clock.now().value + 900.0)
    q.stop_until_resume(_REGAIN)
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_second_stop_replaces_the_marker(q: Queue, clock: FakeClock) -> None:
    q.stop_until_resume(_REGAIN)
    clock.advance(60.0)
    q.stop_until_resume(None)
    assert _marker(q) == {"regain_at": None, "stopped_at": _STOPPED_AT + 60.0}
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH


def test_hold_and_marker_survive_reopening_the_database(
    tmp_path: Path, clock: FakeClock
) -> None:
    db = tmp_path / "reopen.db"
    first = Queue(db_path=db, clock=clock)
    first.enqueue("t1", "research")
    first.stop_until_resume(_REGAIN)
    first.close()
    second = Queue(db_path=db, clock=clock)
    try:
        assert second.paused_until() == SPEND_CAP_HOLD_EPOCH
        assert _marker(second) == {"regain_at": _REGAIN, "stopped_at": _STOPPED_AT}
        assert second.claim() is None
    finally:
        second.close()


def test_clear_pause_removes_hold_and_marker_together(q: Queue) -> None:
    q.stop_until_resume(_REGAIN)
    prior = q.clear_pause()
    assert prior == SPEND_CAP_HOLD_EPOCH
    assert q.paused_until() is None
    assert q.get_meta("paused_until") is None
    assert q.get_meta(SPEND_CAP_STOP_META_KEY) is None


def test_clear_pause_removes_an_orphan_marker_without_a_hold(q: Queue) -> None:
    q.set_meta(SPEND_CAP_STOP_META_KEY, "not json")
    assert q.clear_pause() is None
    assert q.get_meta(SPEND_CAP_STOP_META_KEY) is None


def test_claim_returns_the_job_after_clear_pause_ends_a_stop(q: Queue) -> None:
    q.enqueue("t1", "research")
    q.stop_until_resume(_REGAIN)
    assert q.claim() is None
    q.clear_pause()
    job = q.claim()
    assert job is not None
    assert job.status is JobStatus.RUNNING


def test_clear_pause_of_an_ordinary_pause_leaves_no_stop_marker(q: Queue) -> None:
    q.pause_until(time.time() + 3600.0)
    q.clear_pause()
    assert q.get_meta(SPEND_CAP_STOP_META_KEY) is None
    assert q.paused_until() is None


def test_failed_marker_write_rolls_back_the_hold(
    q: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_mod, "_SET_META_SQL", "INSERT INTO no_such_table VALUES (?, ?)")
    with pytest.raises(sqlite3.OperationalError):
        q.stop_until_resume(_REGAIN)
    assert q.paused_until() is None
    assert q.get_meta("paused_until") is None
    assert q.get_meta(SPEND_CAP_STOP_META_KEY) is None


def test_failed_clear_keeps_the_hold_and_marker(
    q: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    q.stop_until_resume(_REGAIN)
    monkeypatch.setattr(queue_mod, "_DELETE_META_SQL", "DELETE FROM no_such_table WHERE k = ?")
    with pytest.raises(sqlite3.OperationalError):
        q.clear_pause()
    assert q.paused_until() == SPEND_CAP_HOLD_EPOCH
    assert _marker(q) == {"regain_at": _REGAIN, "stopped_at": _STOPPED_AT}
