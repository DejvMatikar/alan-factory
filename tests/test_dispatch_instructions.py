from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dispatch_instructions import (
    DispatchInstructionsReadback,
    _jobs_by_invocation,
    _readback_for_job,
    resolve_dispatch_instructions,
)
from alan.queue import Job, JobStatus, Queue
from alan.sticky_instructions import NON_CONSUMING_TEAMS
from alan.teams._base import format_dispatch_instructions
from alan.workflow import PendingTaskSpec


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _make_job(
    *, team: str, instructions: str | None, instructions_origin: str | None = None
) -> Job:
    return Job(
        id=1,
        task_id="t1",
        team=team,
        status=JobStatus.PENDING,
        enqueued_at=datetime.now(UTC),
        instructions=instructions,
        instructions_origin=instructions_origin,
    )


def _seed_invocation(ws: workspace_mod.Workspace, *, team: str = "implementation") -> str:
    return workflow_mod.seed_pending_workflow(
        ws,
        team=team,
        tasks=[PendingTaskSpec(id="worker", role="worker", model="haiku")],
        source_task_id=ws.task_id,
    )


def test_non_consuming_teams_is_pinned() -> None:
    assert NON_CONSUMING_TEAMS == frozenset(
        {"pr_merge", "triage", "dependency_sweep", "deploy_verify"}
    )


def test_readback_for_job_none_when_no_instructions() -> None:
    job = _make_job(team="implementation", instructions=None)
    assert _readback_for_job(job) == DispatchInstructionsReadback(status="none")


def test_readback_for_job_none_when_blank_instructions() -> None:
    job = _make_job(team="implementation", instructions="")
    assert _readback_for_job(job) == DispatchInstructionsReadback(status="none")


def test_readback_for_job_attached_for_consuming_team() -> None:
    job = _make_job(team="implementation", instructions="do the thing")
    assert _readback_for_job(job) == DispatchInstructionsReadback(
        status="attached", text="do the thing", origin=None
    )


@pytest.mark.parametrize("team", sorted(NON_CONSUMING_TEAMS))
def test_readback_for_job_not_delivered_for_non_consuming_team(team: str) -> None:
    job = _make_job(team=team, instructions="do the thing")
    assert _readback_for_job(job) == DispatchInstructionsReadback(
        status="not_delivered", text="do the thing", origin=None
    )


@pytest.mark.parametrize("origin", ["dispatch", "inherited", "generated"])
def test_readback_for_job_attached_carries_origin(origin: str) -> None:
    job = _make_job(team="implementation_fix", instructions="do it", instructions_origin=origin)
    readback = _readback_for_job(job)
    assert readback.status == "attached"
    assert readback.origin == origin


def test_readback_for_job_not_delivered_carries_origin() -> None:
    job = _make_job(team="pr_merge", instructions="gate json", instructions_origin="generated")
    readback = _readback_for_job(job)
    assert readback.status == "not_delivered"
    assert readback.origin == "generated"


def test_jobs_by_invocation_skips_rows_without_triggered_by_invocation_id(
    queue: Queue,
) -> None:
    queue.enqueue("t1", "implementation", instructions="orphan")
    queue.enqueue(
        "t1", "implementation", triggered_by_invocation_id="inv_001", instructions="linked"
    )
    by_inv = _jobs_by_invocation(queue, "t1")
    assert set(by_inv) == {"inv_001"}
    assert by_inv["inv_001"].instructions == "linked"


def test_jobs_by_invocation_newest_wins_on_id_collision(queue: Queue) -> None:
    queue.enqueue(
        "t1", "implementation", triggered_by_invocation_id="inv_001", instructions="first"
    )
    queue.enqueue(
        "t1", "pr_merge", triggered_by_invocation_id="inv_001", instructions="second"
    )
    by_inv = _jobs_by_invocation(queue, "t1")
    assert by_inv["inv_001"].instructions == "second"
    assert by_inv["inv_001"].team == "pr_merge"


def test_resolve_dispatch_instructions_empty_workspace_returns_empty_map(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("empty", "d", root=ws_root)
    assert resolve_dispatch_instructions(queue, ws) == {}


def test_resolve_dispatch_instructions_attached(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create("attached", "d", root=ws_root)
    inv_id = _seed_invocation(ws, team="implementation")
    queue.enqueue(
        ws.task_id,
        "implementation",
        triggered_by_invocation_id=inv_id,
        instructions="build the widget",
    )
    result = resolve_dispatch_instructions(queue, ws)
    assert result == {
        inv_id: DispatchInstructionsReadback(status="attached", text="build the widget")
    }


def test_resolve_dispatch_instructions_none(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create("none-case", "d", root=ws_root)
    inv_id = _seed_invocation(ws, team="implementation")
    queue.enqueue(ws.task_id, "implementation", triggered_by_invocation_id=inv_id)
    result = resolve_dispatch_instructions(queue, ws)
    assert result == {inv_id: DispatchInstructionsReadback(status="none")}


def test_resolve_dispatch_instructions_not_delivered(queue: Queue, ws_root: Path) -> None:
    ws = workspace_mod.create("gated", "d", root=ws_root)
    inv_id = _seed_invocation(ws, team="pr_merge")
    queue.enqueue(
        ws.task_id,
        "pr_merge",
        triggered_by_invocation_id=inv_id,
        instructions="merge it",
    )
    result = resolve_dispatch_instructions(queue, ws)
    assert result == {
        inv_id: DispatchInstructionsReadback(status="not_delivered", text="merge it")
    }


def test_resolve_dispatch_instructions_unknown_when_job_row_missing(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("orphan-inv", "d", root=ws_root)
    inv_id = _seed_invocation(ws, team="implementation")
    result = resolve_dispatch_instructions(queue, ws)
    assert result == {inv_id: DispatchInstructionsReadback(status="unknown")}


def test_resolve_dispatch_instructions_multiple_invocations(
    queue: Queue, ws_root: Path
) -> None:
    ws = workspace_mod.create("multi", "d", root=ws_root)
    inv_attached = _seed_invocation(ws, team="implementation")
    queue.enqueue(
        ws.task_id,
        "implementation",
        triggered_by_invocation_id=inv_attached,
        instructions="do it",
    )
    inv_unknown = _seed_invocation(ws, team="verification")
    result = resolve_dispatch_instructions(queue, ws)
    assert result[inv_attached] == DispatchInstructionsReadback(
        status="attached", text="do it"
    )
    assert result[inv_unknown] == DispatchInstructionsReadback(status="unknown")


def test_write_path_prompt_splicing_that_this_readback_mirrors_is_pinned() -> None:
    assert format_dispatch_instructions(None) == ""
    assert format_dispatch_instructions("") == ""
    assert format_dispatch_instructions("   ") == ""
    assert (
        format_dispatch_instructions("do the thing")
        == "\n\n## User dispatch instructions\n\ndo the thing\n"
    )
    assert (
        format_dispatch_instructions("  do the thing  ")
        == "\n\n## User dispatch instructions\n\ndo the thing\n"
    )
