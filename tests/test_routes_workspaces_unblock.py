from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import unblock_ledger as ledger
from alan.dispatcher import Dispatcher, PubSub
from alan.park import Blocker, ParkReason
from alan.queue import Queue
from alan.workspace import Status, Workspace, create, transition


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, pubsub: PubSub, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _parked(ws_root: Path, title: str = "parked") -> Workspace:
    ws = create(title, "d", root=ws_root)
    return transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=ParkReason.QUESTION,
        blockers=[Blocker(kind="reason", name="x", detail="d")],
    )


def _record_attempt(queue: Queue, task_id: str, *, shape: str = "stale_review") -> None:
    ledger.ensure_schema(queue)
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key=task_id,
            scope_ref=f"ws:{task_id}",
            shape=shape,
            fingerprint="fp-1",
            kind=ledger.KIND_ATTEMPT,
            action="dispatch:pr_merge",
            outcome=ledger.OUTCOME_ACTED,
        ),
    )


PARK_IDENTITY = "handover_seed@2026-09-19T17:00:00+00:00"
REPORT_AT = 1_000_000.0
NEXT_PROBE_AT = 2_000_000.0
WATCH_FIELDS = ("watch_attempts", "watch_cap", "watch_next_at", "watch_exhausted")


def _record_report(queue: Queue, task_id: str) -> None:
    ledger.ensure_schema(queue)
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key=task_id,
            scope_ref=f"ws:{task_id}",
            shape="needs_human",
            fingerprint="fp-report",
            kind=ledger.KIND_REPORT,
            outcome=ledger.OUTCOME_NEEDS_HUMAN,
            handover_id=PARK_IDENTITY,
            detail={"reason": "parked"},
            ts=REPORT_AT,
        ),
    )


def _seed_watch(queue: Queue, task_id: str, *, attempts: int) -> None:
    entry = ledger.WatchEntry(
        next_at=NEXT_PROBE_AT, attempts=attempts, identity=PARK_IDENTITY, terminal=True
    )
    queue.set_meta(ledger.UNBLOCK_WATCH_META_KEY, json.dumps({task_id: asdict(entry)}))


def _watch_of(unblock: dict[str, Any]) -> dict[str, Any]:
    return {name: unblock.get(name) for name in WATCH_FIELDS}


def test_get_workspace_serves_the_watch_beside_the_report_timestamp(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    stuck = _parked(ws_root)
    _record_report(queue, stuck.task_id)
    _seed_watch(queue, stuck.task_id, attempts=7)
    unblock = client.get(f"/api/workspaces/{stuck.task_id}").json()["unblock"]
    assert _watch_of(unblock) == {
        "watch_attempts": 7,
        "watch_cap": ledger.REPROBE_CAP,
        "watch_next_at": NEXT_PROBE_AT,
        "watch_exhausted": False,
    }
    assert unblock["at"] == REPORT_AT


def test_list_workspaces_serves_the_same_watch_values_as_the_detail(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    stuck = _parked(ws_root)
    _record_report(queue, stuck.task_id)
    _seed_watch(queue, stuck.task_id, attempts=72)
    detail = client.get(f"/api/workspaces/{stuck.task_id}").json()["unblock"]
    listing = {row["task_id"]: row for row in client.get("/api/workspaces").json()}
    assert _watch_of(listing[stuck.task_id]["unblock"]) == _watch_of(detail)
    assert listing[stuck.task_id]["unblock"]["watch_exhausted"] is True


def test_get_workspace_without_a_watch_serves_todays_record_with_the_fields_absent(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    stuck = _parked(ws_root)
    _record_report(queue, stuck.task_id)
    unblock = client.get(f"/api/workspaces/{stuck.task_id}").json()["unblock"]
    assert _watch_of(unblock) == dict.fromkeys(WATCH_FIELDS)
    assert {name: value for name, value in unblock.items() if name not in WATCH_FIELDS} == {
        "shape": "needs_human",
        "attempts": 0,
        "ceiling": ledger.ATTEMPT_CEILING_PER_WORKSPACE,
        "outcome": ledger.OUTCOME_NEEDS_HUMAN,
        "summary": f"needs human · needs_human · parked · {ledger.stamp(REPORT_AT)}",
        "issue_url": None,
        "at": REPORT_AT,
        "action": None,
        "reason": "parked",
        "authority_holder": None,
    }


def test_list_workspaces_attaches_unblock_only_for_stuck_rows(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    stuck = _parked(ws_root)
    active = create("active-ws", "d", root=ws_root)
    _record_attempt(queue, stuck.task_id)
    listing = {row["task_id"]: row for row in client.get("/api/workspaces").json()}
    assert listing[stuck.task_id]["unblock"] is not None
    assert listing[stuck.task_id]["unblock"]["shape"] == "stale_review"
    assert listing[active.task_id]["unblock"] is None


def test_list_workspaces_unblock_is_none_when_the_park_has_no_ledger_row(
    client: TestClient, ws_root: Path
) -> None:
    stuck = _parked(ws_root)
    listing = {row["task_id"]: row for row in client.get("/api/workspaces").json()}
    assert listing[stuck.task_id]["unblock"] is None


def test_list_workspaces_batches_one_records_for_call_for_the_whole_list(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _parked(ws_root, title="first")
    second = _parked(ws_root, title="second")
    _record_attempt(queue, first.task_id)
    _record_attempt(queue, second.task_id)
    calls: list[Iterable[str]] = []
    original = ledger.records_for

    def _counting_records_for(
        q: Queue, keys: Iterable[str]
    ) -> dict[str, ledger.UnblockRecord]:
        calls.append(keys)
        return original(q, keys)

    monkeypatch.setattr(
        "alan.routes.workspaces.unblock_ledger_mod.records_for", _counting_records_for
    )
    client.get("/api/workspaces")
    assert len(calls) == 1


def test_get_workspace_serves_unblock_while_stuck(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    stuck = _parked(ws_root)
    _record_attempt(queue, stuck.task_id, shape="crash_park")
    detail = client.get(f"/api/workspaces/{stuck.task_id}").json()
    assert detail["unblock"] is not None
    assert detail["unblock"]["shape"] == "crash_park"


def test_get_workspace_unblock_none_without_a_ledger_row(
    client: TestClient, ws_root: Path
) -> None:
    stuck = _parked(ws_root)
    detail = client.get(f"/api/workspaces/{stuck.task_id}").json()
    assert detail["unblock"] is None


def test_get_workspace_hides_unblock_once_the_park_clears(
    client: TestClient, ws_root: Path, queue: Queue
) -> None:
    ws = _parked(ws_root)
    _record_attempt(queue, ws.task_id, shape="crash_park")
    transition(ws, Status.COMPLETED)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["unblock"] is None


def test_get_workspace_unblock_none_for_an_active_workspace(
    client: TestClient, ws_root: Path
) -> None:
    ws = create("Active WS", "d", root=ws_root)
    detail = client.get(f"/api/workspaces/{ws.task_id}").json()
    assert detail["unblock"] is None
