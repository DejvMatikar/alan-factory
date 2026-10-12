from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.workflow as workflow_mod
from alan.queue import Queue
from alan.workflow import (
    WorkflowDoc,
    WorkflowTask,
    invocations_dir,
    save_workflow,
    workflow_file,
)
from alan.workspace import Workspace, create
from alan.workspace_index import WorkspaceIndex, WorkspaceRow


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
def index(ws_root: Path, queue: Queue) -> WorkspaceIndex:
    return WorkspaceIndex(root=ws_root, queue=queue)


def _row_by_id(rows: list[WorkspaceRow], task_id: str) -> WorkspaceRow:
    return next(r for r in rows if r.task_id == task_id)


@contextlib.contextmanager
def _held_mtime(directory: Path) -> Iterator[None]:
    ns = directory.stat().st_mtime_ns
    yield
    os.utime(directory, ns=(ns, ns))
    assert directory.stat().st_mtime_ns == ns


def _fresh_row(ws_root: Path, queue: Queue, task_id: str) -> WorkspaceRow:
    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    return _row_by_id(fresh.rows(), task_id)


def _write_inv(ws: Workspace, inv_id: str, *, team: str, cost: float | None = None) -> Path:
    tasks = (
        []
        if cost is None
        else [
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="success",
                total_cost_usd=cost,
            )
        ]
    )
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team=team,
        status="success",
        started_at=datetime.now(UTC),
        tasks=tasks,
    )
    return save_workflow(ws, doc)


def test_new_invocation_seen_when_dir_mtime_unchanged(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Same Tick Inv", "d", root=ws_root)
    inv_dir = invocations_dir(ws.path)
    inv_dir.mkdir()
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.latest_team is None
    with _held_mtime(inv_dir):
        _write_inv(ws, "inv_001", team="research", cost=0.5)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "research"
    assert row.cost_totals.team_runs_usd == pytest.approx(0.5)
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_workflow_document_written_after_listing_seen_on_next_read(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Late Document", "d", root=ws_root)
    inv_dir = invocations_dir(ws.path)
    (inv_dir / "inv_001").mkdir(parents=True)
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.latest_team is None
    with _held_mtime(inv_dir):
        _write_inv(ws, "inv_001", team="implementation", cost=0.25)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "implementation"
    assert row.cost_totals.team_runs_usd == pytest.approx(0.25)
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_corrupt_workflow_document_not_reparsed_on_unchanged_read(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Corrupt Memo", "d", root=ws_root)
    _write_inv(ws, "inv_001", team="research", cost=0.5)
    corrupt = workflow_file(ws.path, "inv_002")
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not: [valid", encoding="utf-8")
    assert _row_by_id(index.rows(), ws.task_id).latest_team == "research"
    calls: list[Path] = []
    real = workflow_mod.load_workflow

    def spy(path: Path) -> WorkflowDoc:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    assert _row_by_id(index.rows(), ws.task_id).latest_team == "research"
    assert calls == []


def _write_corrupt_inv(ws: Workspace, inv_id: str) -> Path:
    corrupt = workflow_file(ws.path, inv_id)
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not: [valid", encoding="utf-8")
    return corrupt


def test_corrupt_workflow_document_row_restored_after_repair(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Repaired Document", "d", root=ws_root)
    _write_inv(ws, "inv_001", team="research", cost=0.5)
    _write_corrupt_inv(ws, "inv_002")
    assert _row_by_id(index.rows(), ws.task_id).latest_team == "research"
    _write_inv(ws, "inv_002", team="implementation", cost=0.25)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "implementation"
    assert row.cost_totals.team_runs_usd == pytest.approx(0.75)
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_removed_corrupt_invocation_clears_failure_memo(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Removed Corrupt", "d", root=ws_root)
    _write_inv(ws, "inv_001", team="research", cost=0.5)
    corrupt = _write_corrupt_inv(ws, "inv_002")
    index.rows()
    assert set(index._guards[ws.task_id].inv_failures) == {"inv_002"}  # noqa: SLF001
    shutil.rmtree(corrupt.parent)
    index.rows()
    assert index._guards[ws.task_id].inv_failures == {}  # noqa: SLF001


def test_unchanged_invocations_are_not_parsed_on_refresh(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Steady Invocations", "d", root=ws_root)
    _write_inv(ws, "inv_001", team="research", cost=0.5)
    _write_inv(ws, "inv_002", team="implementation", cost=0.25)
    index.rows()
    calls: list[Path] = []
    real = workflow_mod.load_workflow

    def spy(path: Path) -> WorkflowDoc:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(workflow_mod, "load_workflow", spy)
    index.rows()
    assert calls == []


def test_warm_refresh_lists_invocations_once_per_live_workspace(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = create("Two Invocations", "d", root=ws_root)
    second = create("Three Invocations", "d", root=ws_root)
    for inv_id in ("inv_001", "inv_002"):
        _write_inv(first, inv_id, team="research", cost=0.5)
    for inv_id in ("inv_001", "inv_002", "inv_003"):
        _write_inv(second, inv_id, team="research", cost=0.5)
    index.rows()
    listed: list[Path] = []
    loaded: list[Path] = []
    real_list = workflow_mod.list_invocation_ids
    real_load = workflow_mod.load_workflow_summary_cached

    def spy_list(ws_path: Path) -> list[str]:
        listed.append(ws_path)
        return real_list(ws_path)

    def spy_load(path: Path) -> workflow_mod.WorkflowRowSummary:
        loaded.append(path)
        return real_load(path)

    monkeypatch.setattr(workflow_mod, "list_invocation_ids", spy_list)
    monkeypatch.setattr(workflow_mod, "load_workflow_summary_cached", spy_load)
    index.rows()
    assert len(listed) == 2
    assert len(loaded) == 5


def test_in_place_workflow_rewrite_still_visible_with_dir_mtime_held(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Rewritten Document", "d", root=ws_root)
    _write_inv(ws, "inv_001", team="research", cost=0.5)
    assert _row_by_id(index.rows(), ws.task_id).latest_team == "research"
    with _held_mtime(invocations_dir(ws.path)):
        _write_inv(ws, "inv_001", team="implementation", cost=0.25)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "implementation"
    assert row.cost_totals.team_runs_usd == pytest.approx(0.25)
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_list_invocation_ids_missing_invocations_dir_returns_empty(tmp_path: Path) -> None:
    assert workflow_mod.list_invocation_ids(tmp_path / "nope") == []
