from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.handover as handover_mod
from alan.handover import Decision, Handover, handover_paths_under, handovers_dir, save_handover
from alan.queue import Queue
from alan.workspace import create
from alan.workspace_index import WorkspaceIndex, WorkspaceRow
from alan.workspace_size import SizeClass

_SEVEN_FILES = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"]


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


def _decisions_for(from_team: str) -> list[Decision]:
    if from_team != "architecture":
        return []
    return [
        Decision(id="d1", statement="s", rationale="r", confidence=0.9, reversibility="high")
    ]


def _minimal_handover(
    task_id: str,
    *,
    pr_number: int | None,
    from_team: str = "implementation",
    files_to_edit: list[str] | None = None,
) -> Handover:
    return Handover(
        handover_id=f"h-{task_id}-{pr_number}-{from_team}",
        handover_chain_id="c1",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv1",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="s",
        decisions=_decisions_for(from_team),
        pr_number=pr_number,
        files_to_edit=files_to_edit or [],
    )


def _plan_handover(task_id: str, *, pr_number: int | None) -> Handover:
    return _minimal_handover(
        task_id,
        pr_number=pr_number,
        from_team="implementation_plan",
        files_to_edit=_SEVEN_FILES,
    )


def _row_by_id(rows: list[WorkspaceRow], task_id: str) -> WorkspaceRow:
    return next(r for r in rows if r.task_id == task_id)


def _restore_mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _fresh_row(ws_root: Path, queue: Queue, task_id: str) -> WorkspaceRow:
    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    return _row_by_id(fresh.rows(), task_id)


def _count_loads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    real = handover_mod.load_handover_cached

    def spy(path: Path) -> Handover:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(handover_mod, "load_handover_cached", spy)
    return calls


def test_new_handover_seen_when_dir_mtime_unchanged(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Same Tick New", "d", root=ws_root)
    assert _row_by_id(index.rows(), ws.task_id).size_class is None
    dir_ns = handovers_dir(ws.path).stat().st_mtime_ns
    save_handover(ws, _plan_handover(ws.task_id, pr_number=5))
    _restore_mtime(handovers_dir(ws.path), dir_ns)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.M
    assert row.scope_ref == "pr:5"
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_filled_empty_slot_seen_when_dir_mtime_unchanged(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Same Tick Fill", "d", root=ws_root)
    slot = save_handover(ws, _plan_handover(ws.task_id, pr_number=8))
    text = slot.read_text(encoding="utf-8")
    slot.write_text("", encoding="utf-8")
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.scope_ref == f"ws:{ws.task_id}"
    assert first.size_class is None
    dir_ns = handovers_dir(ws.path).stat().st_mtime_ns
    slot_ns = slot.stat().st_mtime_ns
    staged = slot.with_suffix(".yaml.tmp")
    staged.write_text(text, encoding="utf-8")
    staged.replace(slot)
    _restore_mtime(handovers_dir(ws.path), dir_ns)
    _restore_mtime(slot, slot_ns)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == "pr:8"
    assert row.size_class == SizeClass.M
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_removed_handovers_seen_when_dir_mtime_unchanged(
    index: WorkspaceIndex, queue: Queue, ws_root: Path
) -> None:
    ws = create("Same Tick Remove", "d", root=ws_root)
    save_handover(ws, _plan_handover(ws.task_id, pr_number=None))
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=9))
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.size_class == SizeClass.M
    assert first.scope_ref == "pr:9"
    dir_ns = handovers_dir(ws.path).stat().st_mtime_ns
    for path in handover_paths_under(ws.path, ws.task_id):
        path.unlink()
    _restore_mtime(handovers_dir(ws.path), dir_ns)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == f"ws:{ws.task_id}"
    assert row.size_class is None
    assert row == _fresh_row(ws_root, queue, ws.task_id)


def test_unchanged_handovers_are_not_reloaded_on_refresh(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Steady State", "d", root=ws_root)
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=None))
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=11))
    assert _row_by_id(index.rows(), ws.task_id).scope_ref == "pr:11"
    calls = _count_loads(monkeypatch)
    assert _row_by_id(index.rows(), ws.task_id).scope_ref == "pr:11"
    assert calls == []
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=12))
    assert _row_by_id(index.rows(), ws.task_id).scope_ref == "pr:12"
    assert len(calls) >= 1


def test_handover_vanishing_between_listing_and_stat_is_skipped(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Vanishing Entry", "d", root=ws_root)
    real = save_handover(ws, _minimal_handover(ws.task_id, pr_number=13))
    missing = real.with_name(f"handover_{ws.task_id}_099.yaml")
    monkeypatch.setattr(
        handover_mod, "handover_paths_under", lambda ws_path, task_id: [real, missing]
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == "pr:13"
