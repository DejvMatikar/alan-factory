from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import workspace as ws_mod
from alan.engine_config import EngineConfig, RepoIdentity, canonical_scope_key, register
from alan.lane_limit import (
    LaneFullError,
    SeatHolder,
    create_within_limit,
    get_limit,
    holds_seat,
    lane_status,
    seat_holders,
    set_limit,
)
from alan.queue import Queue, SeatJobFacts
from alan.workspace import Status
from alan.workspace import create as ws_create

_NOW = datetime(2026, 1, 15, tzinfo=UTC)
_NO_JOBS = SeatJobFacts(has_running=False, has_pending_pickable=False, last_activity=None)
_RUNNING = SeatJobFacts(has_running=True, has_pending_pickable=False, last_activity=None)
_PENDING_PICKABLE = SeatJobFacts(has_running=False, has_pending_pickable=True, last_activity=None)
_ACME = RepoIdentity(owner="acme", repo="widget")


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def q(tmp_path: Path) -> Iterator[Queue]:
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        yield queue
    finally:
        queue.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _backdate(ws: ws_mod.Workspace, *, days: int) -> None:
    created_at = datetime.now(UTC) - timedelta(days=days)
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"created_at": created_at}))  # noqa: SLF001


def _audit_lines(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "engines" / "lane_limit_audit.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.mark.parametrize(
    "status", [s for s in Status if s not in {Status.COMPLETED, Status.ARCHIVED}]
)
def test_holds_seat_true_for_non_terminal_status_when_fresh(status: Status) -> None:
    assert holds_seat(status, _NO_JOBS, _NOW, now=_NOW) is True


@pytest.mark.parametrize("status", [Status.COMPLETED, Status.ARCHIVED])
def test_holds_seat_false_for_terminal_status_even_with_running_job(status: Status) -> None:
    assert holds_seat(status, _RUNNING, _NOW, now=_NOW) is False


def test_holds_seat_true_when_running_despite_long_idle() -> None:
    created_at = _NOW - timedelta(days=30)
    assert holds_seat(Status.FAILED, _RUNNING, created_at, now=_NOW) is True


def test_holds_seat_true_when_pending_pickable_despite_long_idle() -> None:
    created_at = _NOW - timedelta(days=30)
    assert holds_seat(Status.AWAITING_INPUT, _PENDING_PICKABLE, created_at, now=_NOW) is True


def test_holds_seat_false_once_idle_seven_days() -> None:
    created_at = _NOW - timedelta(days=7)
    assert holds_seat(Status.ACTIVE, _NO_JOBS, created_at, now=_NOW) is False


def test_holds_seat_true_just_under_seven_days_idle() -> None:
    created_at = _NOW - timedelta(days=6, hours=23)
    assert holds_seat(Status.ACTIVE, _NO_JOBS, created_at, now=_NOW) is True


def test_holds_seat_uses_last_activity_over_stale_created_at() -> None:
    created_at = _NOW - timedelta(days=30)
    facts = SeatJobFacts(
        has_running=False, has_pending_pickable=False, last_activity=_NOW - timedelta(days=1)
    )
    assert holds_seat(Status.PARTIAL, facts, created_at, now=_NOW) is True


def test_holds_seat_floors_at_created_at_when_last_activity_predates_it() -> None:
    created_at = _NOW - timedelta(days=1)
    facts = SeatJobFacts(
        has_running=False, has_pending_pickable=False, last_activity=_NOW - timedelta(days=30)
    )
    assert holds_seat(Status.PAUSED, facts, created_at, now=_NOW) is True


def test_get_limit_returns_none_when_unset(q: Queue) -> None:
    assert get_limit(q, _ACME) is None


def test_set_limit_then_get_limit_roundtrips(q: Queue) -> None:
    set_limit(q, _ACME, 3, source="api")
    assert get_limit(q, _ACME) == 3


def test_set_limit_none_clears(q: Queue) -> None:
    set_limit(q, _ACME, 3, source="api")
    set_limit(q, _ACME, None, source="api")
    assert get_limit(q, _ACME) is None


def test_set_limit_unset_to_unset_is_a_noop(q: Queue, tmp_path: Path) -> None:
    set_limit(q, _ACME, None, source="api")
    assert get_limit(q, _ACME) is None
    assert _audit_lines(tmp_path) == []


def test_get_limit_tolerant_of_non_integer_meta(q: Queue) -> None:
    q.set_meta("engine_lane_limit_v1:acme/widget", "not-a-number")
    assert get_limit(q, _ACME) is None


def test_get_limit_tolerant_of_out_of_range_meta(q: Queue) -> None:
    q.set_meta("engine_lane_limit_v1:acme/widget", "0")
    assert get_limit(q, _ACME) is None


def test_set_limit_appends_one_audit_line_per_change(q: Queue, tmp_path: Path) -> None:
    set_limit(q, _ACME, 2, source="api")
    set_limit(q, _ACME, 3, source="api")
    lines = _audit_lines(tmp_path)
    assert len(lines) == 2
    assert lines[0]["from"] is None
    assert lines[0]["to"] == 2
    assert lines[1]["from"] == 2
    assert lines[1]["to"] == 3
    assert lines[1]["source"] == "api"
    assert lines[1]["repo"] == "acme/widget"


def test_set_limit_same_value_appends_no_audit_line(q: Queue, tmp_path: Path) -> None:
    set_limit(q, _ACME, 2, source="api")
    set_limit(q, _ACME, 2, source="api")
    assert len(_audit_lines(tmp_path)) == 1


def test_seat_holders_excludes_completed_and_archived(root: Path, q: Queue) -> None:
    ws1 = ws_create("t1", "d", root=root)
    ws_mod.transition(ws1, Status.COMPLETED)
    ws2 = ws_create("t2", "d", root=root)
    ws_mod.transition(ws2, Status.ARCHIVED)
    ws3 = ws_create("t3", "d", root=root)
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert {h.task_id for h in holders} == {ws3.task_id}


def test_seat_holders_workspace_with_no_engine_key_counts_toward_default(
    root: Path, q: Queue
) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="DejvMatikar", repo="alan")),
        default=True,
        validate_env=False,
    )
    ws_implicit = ws_create("implicit", "d", root=root)
    ws_explicit = ws_create("explicit", "d", root=root, engine_key="DejvMatikar/alan")
    assert ws_implicit.meta.engine_key is None
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert {h.task_id for h in holders} == {ws_implicit.task_id, ws_explicit.task_id}


def test_seat_holders_excludes_workspace_scoped_to_other_engine(root: Path, q: Queue) -> None:
    register(EngineConfig(repo=_ACME), validate_env=False)
    ws_default = ws_create("default-ws", "d", root=root)
    ws_acme = ws_create("acme-ws", "d", root=root, engine_key="acme/widget")
    holders = seat_holders(q, root, _ACME)
    assert {h.task_id for h in holders} == {ws_acme.task_id}
    assert ws_default.task_id not in {h.task_id for h in holders}


def test_seat_holders_excludes_idle_workspace_past_seven_days(root: Path, q: Queue) -> None:
    ws = ws_create("t1", "d", root=root)
    _backdate(ws, days=8)
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert holders == []


def test_seat_holders_paused_workspace_with_pending_job_excluded(root: Path, q: Queue) -> None:
    ws = ws_create("t1", "d", root=root)
    _backdate(ws, days=8)
    q.enqueue(ws.task_id, "implementation", workspace_id=ws.task_id)
    old_iso = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    q._conn.execute(  # noqa: SLF001
        "UPDATE jobs SET enqueued_at = ? WHERE workspace_id = ?", (old_iso, ws.task_id)
    )
    q.pause_task(ws.task_id)
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert holders == []


def test_seat_holders_pending_job_holds_seat_when_not_paused(root: Path, q: Queue) -> None:
    ws = ws_create("t1", "d", root=root)
    _backdate(ws, days=8)
    q.enqueue(ws.task_id, "implementation", workspace_id=ws.task_id)
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert {h.task_id for h in holders} == {ws.task_id}


def test_seat_holders_running_job_holds_seat_when_idle(root: Path, q: Queue) -> None:
    ws = ws_create("t1", "d", root=root)
    _backdate(ws, days=8)
    q.enqueue(ws.task_id, "implementation", workspace_id=ws.task_id)
    q.claim()
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert {h.task_id for h in holders} == {ws.task_id}


def test_seat_holders_orders_by_created_at_then_task_id(root: Path, q: Queue) -> None:
    same_time = datetime.now(UTC)
    ws_b = ws_create("b-task", "d", root=root)
    ws_mod._write_meta(ws_b.path, ws_b.meta.model_copy(update={"created_at": same_time}))  # noqa: SLF001
    ws_a = ws_create("a-task", "d", root=root)
    ws_mod._write_meta(ws_a.path, ws_a.meta.model_copy(update={"created_at": same_time}))  # noqa: SLF001
    holders = seat_holders(q, root, canonical_scope_key(None))
    assert [h.task_id for h in holders] == sorted([ws_a.task_id, ws_b.task_id])


def test_lane_status_returns_limit_seats_and_holders(root: Path, q: Queue) -> None:
    set_limit(q, canonical_scope_key(None), 5, source="api")
    ws = ws_create("t1", "d", root=root)
    status = lane_status(q, root, canonical_scope_key(None))
    assert status.limit == 5
    assert status.seats == 1
    assert status.holders == [
        SeatHolder(task_id=ws.task_id, title=ws.meta.title, status=ws.meta.status)
    ]


def test_create_within_limit_creates_when_no_limit_set(root: Path, q: Queue) -> None:
    called: list[int] = []

    def _create() -> ws_mod.Workspace:
        called.append(1)
        return ws_create("t1", "d", root=root)

    ws = create_within_limit(q, root=root, engine_key=None, create=_create)
    assert ws.task_id == "t1"
    assert called == [1]


def test_create_within_limit_succeeds_below_limit(root: Path, q: Queue) -> None:
    set_limit(q, canonical_scope_key(None), 2, source="api")
    ws_create("existing", "d", root=root)
    ws = create_within_limit(
        q, root=root, engine_key=None, create=lambda: ws_create("new", "d", root=root)
    )
    assert ws.task_id == "new"


def test_create_within_limit_raises_lane_full_detail_at_limit(root: Path, q: Queue) -> None:
    set_limit(q, canonical_scope_key(None), 1, source="api")
    ws_create("existing", "d", root=root)
    with pytest.raises(LaneFullError) as exc_info:
        create_within_limit(
            q, root=root, engine_key=None, create=lambda: ws_create("new", "d", root=root)
        )
    assert str(exc_info.value).startswith("lane full:")


def test_create_within_limit_raises_before_calling_create(root: Path, q: Queue) -> None:
    set_limit(q, canonical_scope_key(None), 1, source="api")
    ws_create("existing", "d", root=root)
    called: list[int] = []

    def _create() -> ws_mod.Workspace:
        called.append(1)
        return ws_create("never", "d", root=root)

    with pytest.raises(LaneFullError):
        create_within_limit(q, root=root, engine_key=None, create=_create)
    assert called == []


def test_two_concurrent_creates_one_seat_short_yield_one_success_one_lane_full(
    root: Path, q: Queue
) -> None:
    set_limit(q, canonical_scope_key(None), 2, source="api")
    ws_create("existing", "d", root=root)
    barrier = threading.Barrier(2)
    results_lock = threading.Lock()
    results: list[str] = []

    def _slow_create(title: str) -> ws_mod.Workspace:
        time.sleep(0.05)
        return ws_create(title, "d", root=root)

    def _attempt(title: str) -> None:
        barrier.wait()
        try:
            create_within_limit(
                q, root=root, engine_key=None, create=lambda: _slow_create(title)
            )
        except LaneFullError:
            with results_lock:
                results.append("refused")
        else:
            with results_lock:
                results.append("ok")

    threads = [threading.Thread(target=_attempt, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("refused") == 1
