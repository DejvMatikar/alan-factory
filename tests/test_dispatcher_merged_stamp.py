from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan._dispatcher_selfheal as selfheal
from alan.dispatcher import Dispatcher
from alan.engine_config import Posture
from alan.handover import Handover, save_handover
from alan.park import ParkReason
from alan.queue import Job, JobStatus, Queue
from alan.teams import TEAMS
from alan.workspace import EngineSnapshot, Workspace, create, read, stamp_engine_snapshot

_DEPLOY_VERIFY_TEAM = TEAMS["deploy_verify"]

_MERGE_ONLY_GRAPH: dict[str, list[str]] = {
    "verification": ["implementation_fix", "pr_merge"],
    "pr_merge": ["implementation_fix", "deploy_verify"],
    "deploy_verify": [],
}


def _job(*, team: str = "deploy_verify", job_id: int = 1) -> Job:
    return Job(
        id=job_id, task_id="t1", team=team, status=JobStatus.RUNNING,
        enqueued_at=datetime.now(UTC),
    )


def _pr_handover(ws: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_pr",
        handover_type="terminal",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_pr",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="merged",
        pr_number=pr_number,
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _ws_with_pr(ws_root: Path, *, graph: dict[str, list[str]] | None = None) -> Workspace:
    ws = create("merged-stamp-task", "d", root=ws_root)
    save_handover(ws, _pr_handover(ws, 42))
    if graph is not None:
        snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph=graph)
        ws = stamp_engine_snapshot(ws, snapshot)
    return read(ws.task_id, root=ws_root)


def test_merged_stamp_blocker_none_for_non_deploy_verify_team(ws_root: Path) -> None:
    ws = _ws_with_pr(ws_root, graph=_MERGE_ONLY_GRAPH)
    assert selfheal.merged_stamp_blocker(ws, _job(team="implementation_fix")) is None


def test_merged_stamp_blocker_none_without_a_pr_number(ws_root: Path) -> None:
    ws = create("merged-stamp-no-pr", "d", root=ws_root)
    snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph=_MERGE_ONLY_GRAPH)
    ws = stamp_engine_snapshot(ws, snapshot)
    ws = read(ws.task_id, root=ws_root)
    assert selfheal.merged_stamp_blocker(ws, _job()) is None


def test_merged_stamp_blocker_none_without_engine_snapshot(ws_root: Path) -> None:
    ws = create("merged-stamp-no-snapshot", "d", root=ws_root)
    save_handover(ws, _pr_handover(ws, 42))
    ws = read(ws.task_id, root=ws_root)
    assert selfheal.merged_stamp_blocker(ws, _job()) is None


def test_merged_stamp_blocker_none_when_another_team_also_feeds_deploy_verify(
    ws_root: Path,
) -> None:
    graph = dict(_MERGE_ONLY_GRAPH)
    graph["triage"] = ["deploy_verify"]
    ws = _ws_with_pr(ws_root, graph=graph)
    assert selfheal.merged_stamp_blocker(ws, _job()) is None


def test_merged_stamp_blocker_fires_when_only_pr_merge_feeds_deploy_verify(
    ws_root: Path,
) -> None:
    ws = _ws_with_pr(ws_root, graph=_MERGE_ONLY_GRAPH)
    blocker = selfheal.merged_stamp_blocker(ws, _job())
    assert blocker is not None
    assert blocker.name == "delivery_stamp_pending"
    assert "merged and live" in blocker.detail
    assert "delivery stamp is pending" in blocker.detail


def test_merged_stamp_blocker_none_when_transition_graph_empty(ws_root: Path) -> None:
    ws = _ws_with_pr(ws_root, graph={})
    assert selfheal.merged_stamp_blocker(ws, _job()) is None


def test_latest_pr_number_skips_a_more_recent_handover_carrying_no_pr(
    ws_root: Path,
) -> None:
    ws = create("latest-pr-skip-task", "d", root=ws_root)
    save_handover(ws, _pr_handover(ws, 42))
    save_handover(
        ws,
        Handover(
            handover_id=f"handover_{ws.task_id}_no_pr",
            handover_type="forward",
            task_id=ws.task_id,
            from_team="verification",
            from_invocation_id="inv_no_pr",
            iteration=2,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="still working",
        ),
    )
    ws = read(ws.task_id, root=ws_root)
    assert selfheal._latest_pr_number(ws) == 42  # noqa: SLF001


def test_latest_pr_number_returns_none_when_list_handovers_raises(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("latest-pr-raise-task", "d", root=ws_root)

    def _boom(_ws: Workspace) -> list[Handover]:
        raise RuntimeError("disk error")

    monkeypatch.setattr(selfheal, "list_handovers", _boom)
    assert selfheal._latest_pr_number(ws) is None  # noqa: SLF001


def test_merged_stamp_blocker_returns_none_when_an_inner_check_raises(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _ws_with_pr(ws_root, graph=_MERGE_ONLY_GRAPH)

    def _boom(_graph: dict[str, list[str]]) -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(selfheal, "_only_pr_merge_feeds_deploy_verify", _boom)
    assert selfheal.merged_stamp_blocker(ws, _job()) is None


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


def test_fail_genuine_parks_deploy_verify_crash_with_merged_and_live_blocker(
    ws_root: Path, queue: Queue,
) -> None:
    ws = _ws_with_pr(ws_root, graph=_MERGE_ONLY_GRAPH)
    d = Dispatcher(queue, teams={}, workspaces_root=ws_root)
    job = queue.enqueue(ws.task_id, "deploy_verify", workspace_id=ws.task_id)
    queue.claim()

    d._fail_genuine(job, ws, RuntimeError("boom"), "inv_1", _DEPLOY_VERIFY_TEAM)  # noqa: SLF001

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.park_reason is ParkReason.CRASH
    names = {b.name for b in reloaded.meta.blockers}
    assert "delivery_stamp_pending" in names


def test_fail_genuine_crash_without_pr_uses_generic_reason(
    ws_root: Path, queue: Queue,
) -> None:
    ws = create("no-pr-crash-task", "d", root=ws_root)
    d = Dispatcher(queue, teams={}, workspaces_root=ws_root)
    job = queue.enqueue(ws.task_id, "deploy_verify", workspace_id=ws.task_id)
    queue.claim()

    d._fail_genuine(job, ws, RuntimeError("boom"), "inv_1", _DEPLOY_VERIFY_TEAM)  # noqa: SLF001

    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.park_reason is ParkReason.CRASH
    names = {b.name for b in reloaded.meta.blockers}
    assert "delivery_stamp_pending" not in names
