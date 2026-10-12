from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from alan.queue import Queue


@pytest.fixture
def q(tmp_path: Path) -> Iterator[Queue]:
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        yield queue
    finally:
        queue.close()


def test_seat_job_facts_empty_ids_returns_empty_dict(q: Queue) -> None:
    assert q.seat_job_facts([]) == {}


def test_seat_job_facts_workspace_with_no_jobs_is_absent(q: Queue) -> None:
    assert q.seat_job_facts(["ws-1"]) == {}


def test_seat_job_facts_pending_job_not_running_not_pickable_excluded_workspace(
    q: Queue,
) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.has_running is False
    assert facts.has_pending_pickable is True


def test_seat_job_facts_pending_job_excluded_when_workspace_paused(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    q.pause_task("ws-1")
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.has_pending_pickable is False


def test_seat_job_facts_running_job_sets_has_running(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    q.claim()
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.has_running is True
    assert facts.has_pending_pickable is False


def test_seat_job_facts_last_activity_falls_back_to_enqueued_at(q: Queue) -> None:
    job = q.enqueue("t1", "research", workspace_id="ws-1")
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.last_activity == job.enqueued_at


def test_seat_job_facts_last_activity_prefers_started_at_over_enqueued_at(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    claimed = q.claim()
    assert claimed is not None
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.last_activity == claimed.started_at


def test_seat_job_facts_last_activity_prefers_finished_at_over_started_at(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    claimed = q.claim()
    assert claimed is not None
    done = q.complete(claimed.id)
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.last_activity == done.finished_at


def test_seat_job_facts_last_activity_is_a_datetime(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    assert isinstance(q.seat_job_facts(["ws-1"])["ws-1"].last_activity, datetime)


def test_seat_job_facts_last_activity_is_the_latest_across_the_workspace_jobs(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    q.enqueue("t2", "research", workspace_id="ws-1")
    claimed = q.claim()
    assert claimed is not None
    later = q.enqueue("t3", "research", workspace_id="ws-1")
    facts = q.seat_job_facts(["ws-1"])["ws-1"]
    assert facts.last_activity == later.enqueued_at
    assert facts.has_running is True
    assert facts.has_pending_pickable is True


def test_seat_job_facts_keys_by_requested_workspace_ids_only(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    q.enqueue("t2", "research", workspace_id="ws-2")
    facts = q.seat_job_facts(["ws-1"])
    assert set(facts) == {"ws-1"}


def test_seat_job_facts_covers_multiple_workspaces_independently(q: Queue) -> None:
    q.enqueue("t1", "research", workspace_id="ws-1")
    q.enqueue("t2", "research", workspace_id="ws-2")
    q.claim()
    facts = q.seat_job_facts(["ws-1", "ws-2"])
    assert facts["ws-1"].has_running is True
    assert facts["ws-2"].has_running is False
    assert facts["ws-2"].has_pending_pickable is True
