from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import build_chat as build_chat_mod
from alan import explore_chat as explore_chat_mod
from alan import workspace as workspace_mod
from alan._dispatcher_retire import archive_or_refuse, restore_from_archive
from alan.archive_guard import ArchiveRequest
from alan.engine_config import EngineConfig, RepoIdentity, register_and_persist
from alan.lane_limit import holds_seat
from alan.lifecycle import Blocker, ParkReason, Status
from alan.queue import Queue, SeatJobFacts
from alan.repo_quiesce import quiesce_repo
from alan.retention import archive_stale_completed
from alan.workspace import Task, TaskStatus, Workspace

_RESTORED_AS_SAVED = [
    Status.COMPLETED,
    Status.FAILED,
    Status.PARTIAL,
    Status.ESCALATED,
    Status.AWAITING_INPUT,
    Status.PAUSED,
]
_FINISHED = [Status.COMPLETED, Status.FAILED, Status.PARTIAL, Status.ESCALATED]
_NO_JOBS = SeatJobFacts(has_running=False, has_pending_pickable=False, last_activity=None)


@pytest.fixture
def retention_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_AUTO_ARCHIVE_DAYS", "14")
    monkeypatch.delenv("ALAN_AUTO_ARCHIVE_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture
def quiesce_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _reread(ws_root: Path, ws: Workspace) -> Workspace:
    return workspace_mod.read(ws.task_id, root=ws_root)


def _archive_from(ws_root: Path, status: Status, **fields: Any) -> Workspace:
    ws = workspace_mod.create("restore-me", "d", root=ws_root)
    ws = workspace_mod.transition(ws, status, **fields)
    workspace_mod.transition(ws, Status.ARCHIVED)
    return _reread(ws_root, ws)


def _forget_saved_status(ws_root: Path, ws: Workspace) -> Workspace:
    meta_path = ws.path / "meta.json"
    data = json.loads(meta_path.read_text())
    data.pop("pre_archive_status", None)
    meta_path.write_text(json.dumps(data))
    return _reread(ws_root, ws)


def _merge_blocked() -> dict[str, Any]:
    return {
        "park_reason": ParkReason.MERGE_BLOCK,
        "merge_block_class": "stale_review",
        "blockers": [Blocker(kind="failing_check", name="Tests", detail="check failing: Tests")],
    }


@pytest.mark.parametrize("saved", _RESTORED_AS_SAVED)
def test_restore_from_archive_returns_the_saved_status(
    ws_root: Path, queue: Queue, saved: Status
) -> None:
    archived = _archive_from(ws_root, saved)
    restored = restore_from_archive(archived, queue)
    assert restored.meta.status is saved
    assert restored.meta.park_reason is None
    assert _reread(ws_root, archived).meta.status is saved


def test_restore_from_archive_parks_a_saved_active_workspace_for_the_operator(
    ws_root: Path, queue: Queue
) -> None:
    archived = _archive_from(ws_root, Status.ACTIVE)
    restored = restore_from_archive(archived, queue)
    assert restored.meta.status is Status.AWAITING_INPUT
    assert restored.meta.park_reason is ParkReason.UNARCHIVED
    assert restored.meta.blockers == []


def test_restore_from_archive_parks_a_legacy_archive_for_the_operator(
    ws_root: Path, queue: Queue
) -> None:
    archived = _forget_saved_status(ws_root, _archive_from(ws_root, Status.COMPLETED))
    assert archived.meta.pre_archive_status is None
    restored = restore_from_archive(archived, queue)
    assert restored.meta.status is Status.AWAITING_INPUT
    assert restored.meta.park_reason is ParkReason.UNARCHIVED
    assert _reread(ws_root, archived).meta.status is Status.AWAITING_INPUT


@pytest.mark.parametrize("saved", [Status.ACTIVE, Status.COMPLETED, Status.PAUSED])
def test_restore_from_archive_lets_a_pause_marker_win_over_the_saved_status(
    ws_root: Path, queue: Queue, saved: Status
) -> None:
    archived = _archive_from(ws_root, saved)
    queue.pause_task(archived.task_id)
    restored = restore_from_archive(archived, queue)
    assert restored.meta.status is Status.PAUSED
    assert restored.meta.park_reason is None


def test_restore_from_archive_clears_blockers_and_merge_block_class_for_a_legacy_archive(
    ws_root: Path, queue: Queue
) -> None:
    archived = _forget_saved_status(
        ws_root, _archive_from(ws_root, Status.AWAITING_INPUT, **_merge_blocked())
    )
    assert archived.meta.blockers != []
    restored = restore_from_archive(archived, queue)
    assert restored.meta.park_reason is ParkReason.UNARCHIVED
    assert restored.meta.blockers == []
    assert restored.meta.merge_block_class is None


def test_restore_from_archive_keeps_the_park_state_of_a_saved_merge_block(
    ws_root: Path, queue: Queue
) -> None:
    archived = _archive_from(ws_root, Status.AWAITING_INPUT, **_merge_blocked())
    restored = restore_from_archive(archived, queue)
    assert restored.meta.status is Status.AWAITING_INPUT
    assert restored.meta.park_reason is ParkReason.MERGE_BLOCK
    assert restored.meta.merge_block_class == "stale_review"
    assert [b.name for b in restored.meta.blockers] == ["Tests"]


@pytest.mark.parametrize("saved", [Status.ACTIVE, *_RESTORED_AS_SAVED])
def test_restore_from_archive_clears_the_saved_status(
    ws_root: Path, queue: Queue, saved: Status
) -> None:
    archived = _archive_from(ws_root, saved)
    assert archived.meta.pre_archive_status is saved
    restored = restore_from_archive(archived, queue)
    assert restored.meta.pre_archive_status is None
    assert _reread(ws_root, archived).meta.pre_archive_status is None


@pytest.mark.parametrize("finished", _FINISHED)
def test_restore_from_archive_keeps_terminal_at_and_advances_updated_at(
    ws_root: Path, queue: Queue, finished: Status
) -> None:
    ws = workspace_mod.create("finish-me", "d", root=ws_root)
    finished_ws = workspace_mod.transition(ws, finished)
    assert finished_ws.meta.terminal_at is not None
    workspace_mod.transition(finished_ws, Status.ARCHIVED)
    archived = _reread(ws_root, ws)
    restored = restore_from_archive(archived, queue)
    assert restored.meta.terminal_at == finished_ws.meta.terminal_at
    assert restored.meta.updated_at > archived.meta.updated_at


def test_restore_from_archive_does_not_stamp_a_finish_for_the_operator_park(
    ws_root: Path, queue: Queue
) -> None:
    restored = restore_from_archive(_archive_from(ws_root, Status.ACTIVE), queue)
    assert restored.meta.terminal_at is None


@pytest.mark.parametrize("saved", [Status.ACTIVE, *_RESTORED_AS_SAVED])
@pytest.mark.parametrize("marker", [False, True])
def test_restore_from_archive_never_creates_a_job(
    ws_root: Path, queue: Queue, saved: Status, marker: bool
) -> None:
    archived = _archive_from(ws_root, saved)
    if marker:
        queue.pause_task(archived.task_id)
    restore_from_archive(archived, queue)
    assert queue.list_jobs(task_id=archived.task_id) == []
    assert queue.list_jobs(workspace_id=archived.task_id) == []


@pytest.mark.parametrize("status", [Status.ACTIVE, Status.COMPLETED, Status.FAILED])
def test_manual_archive_records_the_status_it_replaced(
    ws_root: Path, queue: Queue, status: Status
) -> None:
    ws = workspace_mod.create("manual", "d", root=ws_root)
    workspace_mod.transition(ws, status)
    outcome = archive_or_refuse(None, ws.task_id, queue, ws_root, None)
    assert outcome.archived is True
    reloaded = _reread(ws_root, ws)
    assert reloaded.meta.status is Status.ARCHIVED
    assert reloaded.meta.pre_archive_status is status


def test_forced_archive_records_the_status_it_replaced(ws_root: Path, queue: Queue) -> None:
    ws = workspace_mod.create(
        "forced", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    workspace_mod.transition(ws, Status.AWAITING_INPUT)
    body = ArchiveRequest(force=True, reason="abandon")
    outcome = archive_or_refuse(None, ws.task_id, queue, ws_root, body)
    assert outcome.archived is True
    assert _reread(ws_root, ws).meta.pre_archive_status is Status.AWAITING_INPUT


def test_refused_archive_records_no_status(ws_root: Path, queue: Queue) -> None:
    ws = workspace_mod.create(
        "refused", "d", root=ws_root, tasks=[Task(id="a", status=TaskStatus.PENDING)]
    )
    outcome = archive_or_refuse(None, ws.task_id, queue, ws_root, None)
    assert outcome.archived is False
    reloaded = _reread(ws_root, ws)
    assert reloaded.meta.status is Status.ACTIVE
    assert reloaded.meta.pre_archive_status is None


@pytest.mark.usefixtures("retention_env")
def test_retention_archive_records_completed_and_unarchive_restores_it(
    ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("retained", "d", root=ws_root)
    workspace_mod.transition(ws, Status.COMPLETED)
    swept = archive_stale_completed(
        queue=queue, workspaces_root=ws_root, now=datetime.now(UTC) + timedelta(days=15)
    )
    assert swept == [ws.task_id]
    archived = _reread(ws_root, ws)
    assert archived.meta.status is Status.ARCHIVED
    assert archived.meta.pre_archive_status is Status.COMPLETED
    assert restore_from_archive(archived, queue).meta.status is Status.COMPLETED


def test_archive_reads_the_status_on_disk_not_a_stale_snapshot(ws_root: Path) -> None:
    stale = workspace_mod.create("stale-snapshot", "d", root=ws_root)
    assert stale.meta.status is Status.ACTIVE
    workspace_mod.transition(stale, Status.COMPLETED)
    workspace_mod.transition(stale, Status.ARCHIVED)
    assert _reread(ws_root, stale).meta.pre_archive_status is Status.COMPLETED


def test_rearchiving_keeps_the_saved_status(ws_root: Path) -> None:
    archived = _archive_from(ws_root, Status.FAILED)
    workspace_mod.transition(archived, Status.ARCHIVED)
    assert _reread(ws_root, archived).meta.pre_archive_status is Status.FAILED


def test_a_legacy_meta_without_the_saved_status_loads_as_unset(ws_root: Path) -> None:
    archived = _archive_from(ws_root, Status.COMPLETED)
    legacy = _forget_saved_status(ws_root, archived)
    assert "pre_archive_status" not in json.loads((legacy.path / "meta.json").read_text())
    assert legacy.meta.status is Status.ARCHIVED
    assert legacy.meta.pre_archive_status is None


def test_a_restored_completed_workspace_holds_no_seat_under_the_lane_limit(
    ws_root: Path, queue: Queue
) -> None:
    restored = restore_from_archive(_archive_from(ws_root, Status.COMPLETED), queue)
    assert (
        holds_seat(
            restored.meta.status, _NO_JOBS, restored.meta.created_at, now=restored.meta.updated_at
        )
        is False
    )


@pytest.mark.usefixtures("retention_env")
def test_a_restored_completed_workspace_is_swept_again_only_after_the_quiet_period(
    ws_root: Path, queue: Queue
) -> None:
    restored = restore_from_archive(_archive_from(ws_root, Status.COMPLETED), queue)
    now = datetime.now(UTC)
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root, now=now) == []
    assert _reread(ws_root, restored).meta.status is Status.COMPLETED
    later = now + timedelta(days=15)
    assert archive_stale_completed(queue=queue, workspaces_root=ws_root, now=later) == [
        restored.task_id
    ]


@pytest.mark.usefixtures("quiesce_env")
def test_quiesce_repo_leaves_a_restored_completed_workspace_alone(
    ws_root: Path, queue: Queue, tmp_path: Path
) -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widgets"))
    register_and_persist(cfg, validate_env=False)
    slug = cfg.repo.slug
    completed_ws = workspace_mod.create("was-done", "d", root=ws_root, engine_key=slug)
    completed_ws = workspace_mod.transition(completed_ws, Status.COMPLETED)
    workspace_mod.transition(completed_ws, Status.ARCHIVED)
    parked_ws = workspace_mod.create("was-running", "d", root=ws_root, engine_key=slug)
    workspace_mod.transition(parked_ws, Status.ACTIVE)
    workspace_mod.transition(parked_ws, Status.ARCHIVED)
    restore_from_archive(_reread(ws_root, completed_ws), queue)
    restore_from_archive(_reread(ws_root, parked_ws), queue)
    report = quiesce_repo(
        cfg.repo,
        dispatcher=None,
        queue=queue,
        workspaces_root=ws_root,
        build_chat_root=tmp_path / "build-chat",
        build_chat_locks=build_chat_mod.BuildChatSessionLocks(),
        explore_chat_root=tmp_path / "explore-chat",
        explore_chat_locks=explore_chat_mod.ExploreChatSessionLocks(),
    )
    assert report.residue_workspace_ids == []
    assert queue.is_task_paused(completed_ws.task_id) is False
    assert queue.is_task_paused(parked_ws.task_id) is True
