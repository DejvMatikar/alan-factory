from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from alan.queue import ForgeDegradedMarker, JobStatus, Queue, parse_forge_degraded


@pytest.fixture
def q(tmp_path: Path) -> Iterator[Queue]:
    queue = Queue(db_path=tmp_path / "queue.db")
    try:
        yield queue
    finally:
        queue.close()


def test_job_from_row_matches_the_row_it_was_written_from(q: Queue) -> None:
    job = q.enqueue(
        "t1",
        "research",
        workspace_id="ws-1",
        parent_handover_id="h-1",
        triggered_by_invocation_id="inv_001",
        instructions="do it",
        instructions_origin="human",
        eligible_at=123.5,
    )
    assert job.id == 1
    assert job.task_id == "t1"
    assert job.workspace_id == "ws-1"
    assert job.team == "research"
    assert job.status is JobStatus.PENDING
    assert job.parent_handover_id == "h-1"
    assert job.triggered_by_invocation_id == "inv_001"
    assert job.instructions == "do it"
    assert job.instructions_origin == "human"
    assert job.eligible_at == 123.5
    assert q.get_job(job.id) == job


def test_job_from_row_maps_null_columns_to_none_and_counters_to_zero(q: Queue) -> None:
    job = q.enqueue("t1", "research")
    assert job.parent_handover_id is None
    assert job.triggered_by_invocation_id is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.error is None
    assert job.resume_inv_id is None
    assert job.instructions is None
    assert job.instructions_origin is None
    assert job.eligible_at is None
    assert job.interruption_count == 0
    assert job.requeue_count == 0
    assert job.server_error_retry_count == 0


def test_job_from_row_carries_lifecycle_columns(q: Queue) -> None:
    q.enqueue("t1", "research")
    claimed = q.claim()
    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert isinstance(claimed.started_at, datetime)
    failed = q.mark_interrupted_and_fail(claimed.id, "boom", resume_inv_id="inv_009")
    assert failed.status is JobStatus.FAILED
    assert failed.error == "boom"
    assert failed.resume_inv_id == "inv_009"
    assert failed.interruption_count == 1
    assert isinstance(failed.finished_at, datetime)


def test_job_timestamps_serialise_as_the_stdlib_parse_would(q: Queue) -> None:
    job = q.enqueue("t1", "research")
    claimed = q.claim()
    assert claimed is not None
    [row] = q.run("SELECT enqueued_at, started_at FROM jobs")
    legacy = claimed.model_copy(
        update={
            "enqueued_at": datetime.fromisoformat(row["enqueued_at"]),
            "started_at": datetime.fromisoformat(row["started_at"]),
        }
    )
    assert claimed.model_dump_json() == legacy.model_dump_json()
    assert job.enqueued_at.isoformat() == row["enqueued_at"]
    assert claimed.started_at is not None
    assert claimed.started_at.isoformat() == row["started_at"]


def test_job_from_row_with_unknown_status_raises_value_error(q: Queue) -> None:
    job = q.enqueue("t1", "research")
    q.run("UPDATE jobs SET status = 'bogus' WHERE id = ?", (job.id,))
    with pytest.raises(ValueError, match="status"):
        q.get_job(job.id)


def test_parse_forge_degraded_none_returns_none() -> None:
    assert parse_forge_degraded(None) is None


def test_parse_forge_degraded_reads_a_full_marker() -> None:
    raw = json.dumps({"reset_at": 1234.5, "detail": "slow down", "remaining": 3, "limit": 5000})
    assert parse_forge_degraded(raw) == ForgeDegradedMarker(
        reset_at=1234.5, detail="slow down", remaining=3, limit=5000
    )


def test_parse_forge_degraded_leaves_unknown_headroom_and_detail_at_defaults() -> None:
    marker = parse_forge_degraded(json.dumps({"reset_at": 99}))
    assert marker is not None
    assert marker.reset_at == 99.0
    assert marker.detail == ""
    assert marker.remaining is None
    assert marker.limit is None


@pytest.mark.parametrize(
    ("detail", "expected"),
    [(None, "None"), (123, "123"), (["x"], "['x']"), ({"a": 1}, "{'a': 1}")],
)
def test_parse_forge_degraded_keeps_marker_when_detail_is_not_text(
    detail: object, expected: str
) -> None:
    marker = parse_forge_degraded(json.dumps({"reset_at": 500.0, "detail": detail}))
    assert marker is not None
    assert marker.reset_at == 500.0
    assert marker.detail == expected


@pytest.mark.parametrize(
    "raw",
    [
        "not-json{{{",
        "",
        "[1, 2]",
        "null",
        "42",
        '"reset_at"',
        "{}",
        '{"detail": "no reset"}',
        '{"reset_at": null}',
        '{"reset_at": "soon"}',
        '{"reset_at": [1]}',
    ],
)
def test_parse_forge_degraded_returns_none_for_an_unusable_marker(raw: str) -> None:
    assert parse_forge_degraded(raw) is None


def test_forge_degraded_marker_dump_without_none_omits_unknown_headroom() -> None:
    marker = ForgeDegradedMarker(reset_at=10.0, detail="d")
    assert marker.model_dump(exclude_none=True) == {"reset_at": 10.0, "detail": "d"}
