from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan.incident import STUCK_WS_STATUSES
from alan.lifecycle import Status
from alan.queue import Queue
from alan.routes.workspaces import WorkspaceSummary
from alan.workspace import create, transition


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def client(ws_root: Path, queue: Queue) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _summary(status: Status) -> WorkspaceSummary:
    now = datetime.now(UTC)
    return WorkspaceSummary(
        task_id="t",
        title="T",
        status=status,
        team_runs_total=0,
        invocations_total=0,
        updated_at=now,
        created_at=now,
    )


@pytest.mark.parametrize("status", list(Status), ids=lambda status: status.value)
def test_the_operator_link_is_true_exactly_for_the_stuck_statuses(status: Status) -> None:
    assert _summary(status).operator_link is (status in STUCK_WS_STATUSES)


def test_the_operator_link_is_part_of_the_serialised_summary() -> None:
    assert _summary(Status.FAILED).model_dump()["operator_link"] is True
    assert _summary(Status.ACTIVE).model_dump()["operator_link"] is False


def test_the_stuck_statuses_are_the_ones_that_show_the_link_on_the_workspace_page() -> None:
    assert STUCK_WS_STATUSES == {
        Status.AWAITING_INPUT,
        Status.ESCALATED,
        Status.FAILED,
        Status.PARTIAL,
    }


def test_the_workspace_listing_carries_the_operator_link_per_workspace(
    client: TestClient, ws_root: Path
) -> None:
    healthy = create("Healthy thing", "d", root=ws_root)
    stuck = create("Stuck thing", "d", root=ws_root)
    transition(stuck, Status.FAILED)
    listing = {row["task_id"]: row for row in client.get("/api/workspaces").json()}
    assert listing[healthy.task_id]["operator_link"] is False
    assert listing[stuck.task_id]["operator_link"] is True


def test_the_listing_and_the_detail_agree_on_the_operator_link(
    client: TestClient, ws_root: Path
) -> None:
    stuck = create("Stuck thing", "d", root=ws_root)
    transition(stuck, Status.ESCALATED)
    listed = next(
        row for row in client.get("/api/workspaces").json() if row["task_id"] == stuck.task_id
    )
    detail = client.get(f"/api/workspaces/{stuck.task_id}").json()
    assert listed["operator_link"] is detail["operator_link"] is True
