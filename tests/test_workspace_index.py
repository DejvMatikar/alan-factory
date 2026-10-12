from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan import _chat_core, store
from alan import investigator as investigator_mod
from alan import workflow as workflow_mod
from alan import workspace as ws_mod
from alan import workspace_cost as workspace_cost_mod
from alan import workspace_index as wsi
from alan.handover import Decision, Handover, handovers_dir, save_handover
from alan.queue import Queue
from alan.workflow import WorkflowDoc, WorkflowTask, save_workflow
from alan.workspace import Status, Task, TaskDoDClause, TaskStatus, Workspace, create
from alan.workspace_index import (
    ARCHIVED_ROWS_META_KEY,
    WorkspaceIndex,
    WorkspaceRow,
    project_burn_usd,
)
from alan.workspace_size import SizeClass
from tests.fs_timestamp_support import bump_mtime


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


def _write_workflow(
    ws: Workspace,
    *,
    team: str,
    status: str,
    inv_id: str,
    cost: float | None = None,
) -> None:
    tasks = []
    if cost is not None:
        tasks = [
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="success",
                total_cost_usd=cost,
            )
        ]
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team=team,
        status=status,  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
        tasks=tasks,
    )
    save_workflow(ws, doc)


def _write_manager_stream(ws: Workspace, *, cost: float) -> None:
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    (ws.path / "manager_summary_stream.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )


def _row_by_id(rows: list[WorkspaceRow], task_id: str) -> WorkspaceRow:
    return next(r for r in rows if r.task_id == task_id)


def test_rows_empty_when_root_missing(index: WorkspaceIndex) -> None:
    assert index.rows() == []
    assert index.archived_rows() == []


def test_rows_reflect_created_workspace(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("First WS", "d", root=ws_root, team="implementation")
    rows = index.rows()
    row = _row_by_id(rows, ws.task_id)
    assert row.title == "First WS"
    assert row.status == Status.ACTIVE
    assert row.team == "implementation"
    assert row.latest_team is None
    assert row.latest_invocation_status is None
    assert row.total_cost_usd is None


def test_rows_carry_project_id_from_meta(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Has Project", "d", root=ws_root, project_id="proj-1")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.project_id == "proj-1"


def test_rows_project_id_none_when_unset(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("No Project", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.project_id is None


def test_archived_row_preserves_project_id(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Archive With Project", "d", root=ws_root, project_id="proj-2")
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.project_id == "proj-2"


def test_rows_skip_non_dir_and_meta_less_entries(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    create("Real WS", "d", root=ws_root)
    (ws_root / "stray.txt").write_text("x", encoding="utf-8")
    (ws_root / "empty-dir").mkdir()
    assert len(index.rows()) == 1


def test_row_picks_up_meta_change(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Mutable WS", "d", root=ws_root)
    assert _row_by_id(index.rows(), ws.task_id).status == Status.ACTIVE
    ws_mod.transition(ws, Status.FAILED)
    assert _row_by_id(index.rows(), ws.task_id).status == Status.FAILED


def test_row_latest_invocation_and_costs(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Cost WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    _write_workflow(ws, team="implementation", status="running", inv_id="inv_002", cost=0.02)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "implementation"
    assert row.latest_invocation_status == "running"
    assert row.total_cost_usd == pytest.approx(0.03)


def test_row_folds_manager_summary_cost(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Manager Cost WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    _write_manager_stream(ws, cost=0.005)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.total_cost_usd == pytest.approx(0.015)


def test_row_manager_summary_cost_only(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Manager Only WS", "d", root=ws_root)
    _write_manager_stream(ws, cost=0.005)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.total_cost_usd == pytest.approx(0.005)


def test_row_cost_survives_manager_usage_exception(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Boom WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)

    def _boom(_ws_path: Path) -> None:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_cost_mod, "read_manager_summary_usage", _boom)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.total_cost_usd == pytest.approx(0.01)


def test_row_in_place_workflow_rewrite_visible(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Rewrite WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001")
    assert _row_by_id(index.rows(), ws.task_id).latest_invocation_status == "success"
    _write_workflow(ws, team="research", status="failed", inv_id="inv_001")
    assert _row_by_id(index.rows(), ws.task_id).latest_invocation_status == "failed"


def test_row_corrupt_workflow_dropped(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Corrupt WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001")
    assert _row_by_id(index.rows(), ws.task_id).latest_team == "research"
    workflow_mod.workflow_file(ws.path, "inv_001").write_text("not: [yaml", encoding="utf-8")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team is None
    assert row.latest_invocation_status is None


def test_vanished_workspace_dropped(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Vanishing WS", "d", root=ws_root)
    assert len(index.rows()) == 1
    ws_mod.delete(ws.task_id, root=ws_root)
    assert index.rows() == []


def test_meta_deleted_mid_scan_keeps_stale_row(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Meta Gone WS", "d", root=ws_root)
    assert len(index.rows()) == 1
    (ws.path / "meta.json").unlink()
    assert len(index.rows()) == 1


def test_refresh_one_classifies_new_workspace(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Fresh Detail WS", "d", root=ws_root)
    row = index.refresh_one(ws.task_id)
    assert row is not None
    assert row.task_id == ws.task_id
    assert row.status == Status.ACTIVE


def test_refresh_one_reflects_meta_change(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Detail Mutable WS", "d", root=ws_root)
    assert index.refresh_one(ws.task_id) is not None
    ws_mod.transition(ws, Status.FAILED)
    row = index.refresh_one(ws.task_id)
    assert row is not None
    assert row.status == Status.FAILED


def test_refresh_one_does_not_scan_other_workspace_directories(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create("Other WS 1", "d", root=ws_root)
    create("Other WS 2", "d", root=ws_root)
    target = create("Target WS", "d", root=ws_root)
    index.rows()

    root_iterdir_calls = 0
    original_iterdir = Path.iterdir

    def _tracking_iterdir(self: Path) -> Iterator[Path]:
        nonlocal root_iterdir_calls
        if self == ws_root:
            root_iterdir_calls += 1
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _tracking_iterdir)
    row = index.refresh_one(target.task_id)
    assert row is not None
    assert row.task_id == target.task_id
    assert root_iterdir_calls == 0


def test_refresh_one_returns_none_and_drops_stale_row_for_vanished_workspace(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Vanishing Detail WS", "d", root=ws_root)
    assert index.refresh_one(ws.task_id) is not None
    ws_mod.delete(ws.task_id, root=ws_root)
    assert index.refresh_one(ws.task_id) is None
    assert index.rows() == []


def test_refresh_one_archives_row_and_persists_blob(
    index: WorkspaceIndex, ws_root: Path, queue: Queue
) -> None:
    ws = create("Refresh One Archive WS", "d", root=ws_root)
    assert index.refresh_one(ws.task_id) is not None
    ws_mod.transition(ws, Status.ARCHIVED)
    row = index.refresh_one(ws.task_id)
    assert row is not None
    assert row.status == Status.ARCHIVED
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    assert ws.task_id in json.loads(raw)["rows"]


def test_refresh_one_returns_archived_row_without_rescanning(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Archived Detail WS", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()

    def _boom(_task_id: str) -> bool:
        raise AssertionError("archived rows must not be re-derived by refresh_one")

    monkeypatch.setattr(index, "_refresh_one", _boom)
    row = index.refresh_one(ws.task_id)
    assert row is not None
    assert row.status == Status.ARCHIVED


def test_archive_moves_row_to_archived_and_persists_blob(
    index: WorkspaceIndex, ws_root: Path, queue: Queue
) -> None:
    ws = create("Archive Me", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    assert len(index.rows()) == 1
    ws_mod.transition(ws, Status.ARCHIVED)
    assert index.rows() == []
    archived = index.archived_rows()
    row = _row_by_id(archived, ws.task_id)
    assert row.status == Status.ARCHIVED
    assert row.total_cost_usd == pytest.approx(0.01)
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    assert ws.task_id in json.loads(raw)["rows"]


def test_archived_dir_never_rescanned(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Cold Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    reads: list[str] = []
    original = ws_mod.read

    def spy(task_id: str, *, root: Path | None = None) -> Workspace:
        reads.append(task_id)
        return original(task_id, root=root)

    monkeypatch.setattr(ws_mod, "read", spy)
    index.rows()
    assert reads == []


def test_new_dir_already_archived_gets_meta_only_row(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Seeded Archive", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.total_cost_usd is None
    assert row.latest_team is None


def test_backfill_completes_meta_only_archived_rows(
    index: WorkspaceIndex, ws_root: Path, queue: Queue
) -> None:
    ws = create("Backfill Me", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.total_cost_usd == pytest.approx(0.01)
    assert row.latest_team == "research"
    assert json.loads(queue.get_meta(ARCHIVED_ROWS_META_KEY) or "{}")["complete"] is True


def test_backfill_noop_when_complete(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Done Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    before = index.archived_rows()
    index.backfill_archived()
    assert index.archived_rows() == before


def test_backfill_skips_vanished_workspace(
    index: WorkspaceIndex, ws_root: Path, queue: Queue
) -> None:
    ws = create("Ghost Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    ws_mod.delete(ws.task_id, root=ws_root)
    index.backfill_archived()
    assert index.archived_rows() == []


def test_backfill_skips_row_invalidated_mid_run(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Raced Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    original = index._derive_archived_row  # noqa: SLF001

    def race(task_id: str) -> WorkspaceRow | None:
        index.invalidate(task_id)
        return original(task_id)

    monkeypatch.setattr(index, "_derive_archived_row", race)
    index.backfill_archived()
    with index._lock:  # noqa: SLF001
        assert ws.task_id not in index._archived  # noqa: SLF001


def test_rebuild_loads_blob_and_marks_complete(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create("Persisted Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    assert _row_by_id(fresh.archived_rows(), ws.task_id).status == Status.ARCHIVED
    fresh.backfill_archived()
    assert len(fresh.archived_rows()) == 1


def test_unarchive_invalidate_restores_row(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Round Trip", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    assert len(index.archived_rows()) == 1
    ws = ws_mod.read(ws.task_id, root=ws_root)
    ws_mod.transition(ws, Status.ACTIVE)
    index.invalidate(ws.task_id)
    assert index.archived_rows() == []
    assert _row_by_id(index.rows(), ws.task_id).status == Status.ACTIVE


def test_invalidate_unknown_task_noop(index: WorkspaceIndex, ws_root: Path) -> None:
    create("Unrelated", "d", root=ws_root)
    index.invalidate("never-seen")
    assert len(index.rows()) == 1


def test_reindex_recomputes_archived_costs(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Reindex Me", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.02)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    _write_workflow(ws, team="research", status="success", inv_id="inv_002", cost=0.03)
    index.reindex()
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.total_cost_usd == pytest.approx(0.05)


def test_parse_blob_rows_tolerates_garbage() -> None:
    assert wsi._parse_blob_rows("not json{{{") == ({}, False)  # noqa: SLF001
    assert wsi._parse_blob_rows(json.dumps(["list"])) == ({}, False)  # noqa: SLF001
    assert wsi._parse_blob_rows(json.dumps({"version": 99, "rows": {}})) == (  # noqa: SLF001
        {},
        False,
    )
    assert wsi._parse_blob_rows(  # noqa: SLF001
        json.dumps({"version": wsi._BLOB_VERSION, "rows": "nope"})  # noqa: SLF001
    ) == ({}, False)


def test_parse_blob_rows_drops_malformed_row() -> None:
    good = WorkspaceRow(
        task_id="ok",
        title="t",
        status=Status.ARCHIVED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    raw = json.dumps(
        {
            "version": wsi._BLOB_VERSION,  # noqa: SLF001
            "complete": True,
            "rows": {"ok": good.model_dump(mode="json"), "bad": {"nope": 1}},
        }
    )
    rows, complete = wsi._parse_blob_rows(raw)  # noqa: SLF001
    assert set(rows) == {"ok"}
    assert complete is True


def test_rebuild_tolerates_corrupt_blob(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    queue.set_meta(ARCHIVED_ROWS_META_KEY, "corrupt{{{")
    ws = create("Fresh Despite Blob", "d", root=ws_root)
    index.rebuild()
    assert _row_by_id(index.rows(), ws.task_id).title == "Fresh Despite Blob"


def test_classify_new_skips_meta_less_dir_race(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Sig Race", "d", root=ws_root)
    monkeypatch.setattr(store, "stat_signature", lambda _p: None)
    assert index.rows() == []
    monkeypatch.undo()
    assert _row_by_id(index.rows(), ws.task_id).task_id == ws.task_id


def test_reread_meta_tolerates_corruption(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Corrupt Meta", "d", root=ws_root)
    assert len(index.rows()) == 1
    (ws.path / "meta.json").write_text("not json{{{", encoding="utf-8")
    assert len(index.rows()) == 1


def test_dir_mtime_oserror(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert wsi._dir_mtime(missing) is None  # noqa: SLF001


def test_pruned_invocation_dir_dropped_from_row(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    import shutil

    ws = create("Pruned WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    _write_workflow(ws, team="implementation", status="success", inv_id="inv_002", cost=0.02)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "implementation"
    assert row.total_cost_usd == pytest.approx(0.03)
    shutil.rmtree(workflow_mod.invocations_dir(ws.path) / "inv_002")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.latest_team == "research"
    assert row.total_cost_usd == pytest.approx(0.01)


def test_archive_flip_captures_final_invocation_state(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Final State", "d", root=ws_root)
    _write_workflow(ws, team="research", status="running", inv_id="inv_001", cost=0.01)
    assert _row_by_id(index.rows(), ws.task_id).latest_invocation_status == "running"
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.02)
    bump_mtime(workflow_mod.workflow_file(ws.path, "inv_001"))
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.latest_invocation_status == "success"
    assert row.total_cost_usd == pytest.approx(0.02)


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
) -> "Handover":
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


def test_scope_ref_uses_latest_pr_number(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Scope PR", "d", root=ws_root)
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=None))
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=7))
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == "pr:7"
    assert row.resolved_scope_ref() == "pr:7"


def test_scope_ref_falls_back_to_workspace_id(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Scope WS", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == f"ws:{ws.task_id}"
    legacy = WorkspaceRow(
        task_id="old-blob-row",
        title="t",
        status=Status.ARCHIVED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert legacy.resolved_scope_ref() == "ws:old-blob-row"


def test_scope_ref_updates_on_new_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Scope Update", "d", root=ws_root)
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.scope_ref == f"ws:{ws.task_id}"
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=42))
    bump_mtime(handovers_dir(ws.path))
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == "pr:42"
    assert row.handovers_mtime is not None
    assert row.handovers_mtime != first.handovers_mtime


def test_scope_ref_skips_unreadable_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Scope Corrupt", "d", root=ws_root)
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=3))
    corrupt = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt.write_text("not: valid: [yaml", encoding="utf-8")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.scope_ref == "pr:3"


def test_archived_row_snapshots_scope_ref(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Scope Archived", "d", root=ws_root)
    save_handover(ws, _minimal_handover(ws.task_id, pr_number=11))
    index.rows()
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.scope_ref == "pr:11"


def test_old_archived_blob_without_project_id_key_defaults_to_none() -> None:
    ts = datetime.now(UTC).isoformat()
    legacy_row = {
        "task_id": "legacy-archived",
        "title": "Legacy",
        "status": "archived",
        "created_at": ts,
        "updated_at": ts,
    }
    raw = json.dumps(
        {"version": wsi._BLOB_VERSION, "complete": True, "rows": {"legacy-archived": legacy_row}}  # noqa: SLF001
    )
    rows, complete = wsi._parse_blob_rows(raw)  # noqa: SLF001
    assert complete is True
    assert set(rows) == {"legacy-archived"}
    assert rows["legacy-archived"].project_id is None


def test_rebuild_loads_pre_p4_4_archived_blob_without_blackout(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create("Pre P4.4 Archive", "d", root=ws_root)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    del blob["rows"][ws.task_id]["project_id"]
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    row = _row_by_id(fresh.archived_rows(), ws.task_id)
    assert row.project_id is None


def _cost_row(task_id: str, *, project_id: str | None, cost: float | None) -> WorkspaceRow:
    ts = datetime.now(UTC)
    return WorkspaceRow(
        task_id=task_id,
        title="t",
        status=Status.ACTIVE,
        created_at=ts,
        updated_at=ts,
        total_cost_usd=cost,
        project_id=project_id,
    )


def test_project_burn_usd_sums_active_and_archived_matching_rows() -> None:
    active = [
        _cost_row("a", project_id="proj-1", cost=1.5),
        _cost_row("b", project_id="proj-2", cost=9.0),
    ]
    archived = [_cost_row("c", project_id="proj-1", cost=2.5)]
    assert project_burn_usd(active, archived, "proj-1") == pytest.approx(4.0)


def test_project_burn_usd_coerces_none_cost_to_zero() -> None:
    row = _cost_row("a", project_id="proj-1", cost=None)
    assert project_burn_usd([row], [], "proj-1") == pytest.approx(0.0)


def test_project_burn_usd_no_matching_rows_is_zero() -> None:
    row = _cost_row("a", project_id="other", cost=5.0)
    assert project_burn_usd([row], [], "proj-1") == pytest.approx(0.0)


def test_project_aggregates_folds_active_and_archived_by_project() -> None:
    active = [
        _cost_row("a", project_id="proj-1", cost=1.5),
        _cost_row("b", project_id="proj-2", cost=9.0),
    ]
    archived = [_cost_row("c", project_id="proj-1", cost=2.5)]
    aggregates = wsi.project_aggregates(active, archived)
    assert aggregates["proj-1"].workspace_count == 2
    assert aggregates["proj-1"].total_spend_usd == pytest.approx(4.0)
    assert aggregates["proj-2"].workspace_count == 1
    assert aggregates["proj-2"].total_spend_usd == pytest.approx(9.0)


def test_project_aggregates_coerces_none_cost_to_zero() -> None:
    row = _cost_row("a", project_id="proj-1", cost=None)
    aggregates = wsi.project_aggregates([row], [])
    assert aggregates["proj-1"].workspace_count == 1
    assert aggregates["proj-1"].total_spend_usd == pytest.approx(0.0)


def test_project_aggregates_skips_unaffiliated_rows() -> None:
    row = _cost_row("a", project_id=None, cost=5.0)
    assert wsi.project_aggregates([row], []) == {}


def test_project_aggregates_empty_when_no_rows() -> None:
    assert wsi.project_aggregates([], []) == {}


def _roadmap_row(
    task_id: str,
    *,
    project_id: str | None,
    roadmap_item_id: str | None,
    status: Status = Status.COMPLETED,
    pr_number: int | None = None,
) -> WorkspaceRow:
    ts = datetime.now(UTC)
    return WorkspaceRow(
        task_id=task_id,
        title="t",
        status=status,
        created_at=ts,
        updated_at=ts,
        project_id=project_id,
        roadmap_item_id=roadmap_item_id,
        pr_number=pr_number,
    )


def test_delivered_roadmap_item_ids_includes_terminal_row_with_pr_number() -> None:
    row = _roadmap_row("a", project_id="proj-1", roadmap_item_id="itm-1", pr_number=7)
    assert wsi.delivered_roadmap_item_ids([row], []) == {"proj-1": {"itm-1"}}


def test_delivered_roadmap_item_ids_excludes_non_terminal_row() -> None:
    row = _roadmap_row(
        "a", project_id="proj-1", roadmap_item_id="itm-1", status=Status.ACTIVE, pr_number=7
    )
    assert wsi.delivered_roadmap_item_ids([row], []) == {}


def test_delivered_roadmap_item_ids_excludes_row_without_pr_number() -> None:
    row = _roadmap_row("a", project_id="proj-1", roadmap_item_id="itm-1", pr_number=None)
    assert wsi.delivered_roadmap_item_ids([row], []) == {}


def test_delivered_roadmap_item_ids_skips_row_without_roadmap_item_id() -> None:
    row = _roadmap_row("a", project_id="proj-1", roadmap_item_id=None, pr_number=1)
    assert wsi.delivered_roadmap_item_ids([row], []) == {}


def test_delivered_roadmap_item_ids_skips_unaffiliated_row() -> None:
    row = _roadmap_row("a", project_id=None, roadmap_item_id="itm-1", pr_number=1)
    assert wsi.delivered_roadmap_item_ids([row], []) == {}


def test_delivered_roadmap_item_ids_includes_archived_rows() -> None:
    row = _roadmap_row(
        "a", project_id="proj-1", roadmap_item_id="itm-1", status=Status.ARCHIVED, pr_number=3
    )
    assert wsi.delivered_roadmap_item_ids([], [row]) == {"proj-1": {"itm-1"}}


def test_delivered_roadmap_item_ids_folds_multiple_projects_and_items() -> None:
    rows = [
        _roadmap_row("a", project_id="proj-1", roadmap_item_id="itm-1", pr_number=1),
        _roadmap_row("b", project_id="proj-1", roadmap_item_id="itm-2", pr_number=2),
        _roadmap_row("c", project_id="proj-2", roadmap_item_id="itm-3", pr_number=3),
    ]
    result = wsi.delivered_roadmap_item_ids(rows, [])
    assert result == {"proj-1": {"itm-1", "itm-2"}, "proj-2": {"itm-3"}}


def test_delivered_roadmap_item_ids_empty_when_no_rows() -> None:
    assert wsi.delivered_roadmap_item_ids([], []) == {}


def test_engine_key_carried_from_meta_active_row(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Engine WS", "d", root=ws_root, engine_key="acme/widget")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.engine_key == "acme/widget"


def test_engine_key_none_when_unset(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("No Engine WS", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.engine_key is None


def test_archived_row_preserves_engine_key(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Archive Engine WS", "d", root=ws_root, engine_key="acme/archived-engine")
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.engine_key == "acme/archived-engine"


def test_old_version_archived_blob_discarded_and_rederives_engine_key(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create("V1 Blob WS", "d", root=ws_root, engine_key="acme/v1-blob")
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    blob["version"] = 1
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    row = _row_by_id(fresh.archived_rows(), ws.task_id)
    assert row.engine_key == "acme/v1-blob"
    assert fresh._archived_complete is False  # noqa: SLF001


def test_row_task_counts_zero_when_no_tasks(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("No Tasks", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.tasks_total == 0
    assert row.tasks_done == 0
    assert row.pr_number is None


def test_row_tasks_done_counts_only_completed(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create(
        "Mixed Tasks",
        "d",
        root=ws_root,
        tasks=[
            Task(id="a", status=TaskStatus.COMPLETED),
            Task(id="b", status=TaskStatus.FAILED),
            Task(id="c", status=TaskStatus.READY),
        ],
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.tasks_total == 3
    assert row.tasks_done == 1


def test_row_pr_number_is_latest_dod_evidence(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create(
        "PR Stamped",
        "d",
        root=ws_root,
        tasks=[
            Task(
                id="a",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="x", evidence="PR #7")],
            ),
            Task(
                id="b",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="y", evidence="PR #9")],
            ),
        ],
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.pr_number == 9


def test_row_pr_zero_is_not_none(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create(
        "PR Zero",
        "d",
        root=ws_root,
        tasks=[
            Task(
                id="a",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="x", evidence="PR #0")],
            )
        ],
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.pr_number == 0


def test_row_roadmap_item_id_carried_from_meta(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create(
        "Roadmap Stamped", "d", root=ws_root, project_id="proj-1", roadmap_item_id="itm-1"
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.roadmap_item_id == "itm-1"


def test_row_roadmap_item_id_none_when_unset(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("No Roadmap Item", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.roadmap_item_id is None


def test_archived_row_preserves_task_counts_pr_number_and_roadmap_item_id(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create(
        "Archive Full Fields",
        "d",
        root=ws_root,
        project_id="proj-1",
        roadmap_item_id="itm-1",
        tasks=[
            Task(
                id="a",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="x", evidence="PR #3")],
            )
        ],
    )
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.tasks_total == 1
    assert row.tasks_done == 1
    assert row.pr_number == 3
    assert row.roadmap_item_id == "itm-1"


def test_old_blob_version_3_discarded_and_rederives_task_fields(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create(
        "V3 Blob WS",
        "d",
        root=ws_root,
        project_id="proj-1",
        roadmap_item_id="itm-1",
        tasks=[
            Task(
                id="a",
                status=TaskStatus.COMPLETED,
                dod_clauses=[TaskDoDClause(text="x", evidence="PR #4")],
            )
        ],
    )
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    blob["version"] = 3
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    row = _row_by_id(fresh.archived_rows(), ws.task_id)
    assert row.tasks_total == 1
    assert row.tasks_done == 1
    assert row.pr_number == 4
    assert row.roadmap_item_id == "itm-1"
    assert fresh._archived_complete is False  # noqa: SLF001


_SESSION_A = "11111111-1111-1111-1111-111111111111"
_SESSION_B = "22222222-2222-2222-2222-222222222222"


def _write_session_stream(ws: Workspace, session_id: str, filename: str, *, cost: float) -> None:
    session_dir = ws.path / "chat" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    (session_dir / filename).write_text(json.dumps(event) + "\n", encoding="utf-8")


def _write_investigator_stream(ws: Workspace, *, cost: float) -> None:
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    (ws.path / "investigator_stream.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_row_folds_discussion_chat_cost(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Chat Cost WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.02)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.02)
    assert row.total_cost_usd == pytest.approx(0.02)


def test_row_folds_multiple_chat_sessions(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Multi Session WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.01)
    _write_session_stream(ws, _SESSION_B, "stream.jsonl", cost=0.03)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.04)


def test_row_folds_collision_check_cost_per_session(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Collision Cost WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "collision_stream.jsonl", cost=0.005)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.collision_check_usd == pytest.approx(0.005)
    assert row.total_cost_usd == pytest.approx(0.005)


def test_row_ignores_stray_system_prompt_file_in_chat_dir(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Stray File WS", "d", root=ws_root)
    (ws.path / "chat").mkdir()
    (ws.path / "chat" / "system_prompt.txt").write_text("x", encoding="utf-8")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.discussion_usd is None
    assert row.total_cost_usd is None


def test_row_folds_investigator_cost(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Investigator Cost WS", "d", root=ws_root)
    _write_investigator_stream(ws, cost=0.04)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.investigator_usd == pytest.approx(0.04)
    assert row.total_cost_usd == pytest.approx(0.04)


def test_row_folds_all_five_cost_categories(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("All Categories WS", "d", root=ws_root)
    _write_workflow(ws, team="research", status="success", inv_id="inv_001", cost=0.01)
    _write_manager_stream(ws, cost=0.02)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.03)
    _write_session_stream(ws, _SESSION_A, "collision_stream.jsonl", cost=0.04)
    _write_investigator_stream(ws, cost=0.05)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.team_runs_usd == pytest.approx(0.01)
    assert row.cost_totals.status_summary_usd == pytest.approx(0.02)
    assert row.cost_totals.discussion_usd == pytest.approx(0.03)
    assert row.cost_totals.collision_check_usd == pytest.approx(0.04)
    assert row.cost_totals.investigator_usd == pytest.approx(0.05)
    assert row.total_cost_usd == pytest.approx(0.15)


def _write_prep_chat_stream(
    ws: Workspace, session_id: str, filename: str, *, cost: float
) -> None:
    session_dir = ws.path / "prep-chat" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    (session_dir / filename).write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_row_folds_preparation_chat_cost(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Prep Chat Cost WS", "d", root=ws_root)
    _write_prep_chat_stream(ws, _SESSION_A, "stream.jsonl", cost=0.03)
    _write_prep_chat_stream(ws, _SESSION_A, "collision_stream.jsonl", cost=0.01)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.preparation_usd == pytest.approx(0.04)
    assert row.total_cost_usd == pytest.approx(0.04)


def test_row_preparation_cost_none_without_archive(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("No Prep Chat WS", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.preparation_usd is None


def test_row_chat_cost_not_recomputed_on_warm_refresh(
    index: WorkspaceIndex, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Warm Chat WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.02)
    index.rows()

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("derive_run_summary should not be called on a warm refresh")

    monkeypatch.setattr(workspace_cost_mod, "derive_run_summary", _boom)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.02)


def test_row_chat_cost_updates_on_stream_append_without_dir_mtime_change(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Append WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.01)
    assert _row_by_id(index.rows(), ws.task_id).cost_totals.discussion_usd == pytest.approx(0.01)
    stream = ws.path / "chat" / _SESSION_A / "stream.jsonl"
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "result", "total_cost_usd": 0.02, "usage": {}}) + "\n")
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.03)


def test_new_dir_already_archived_row_skips_new_categories_only(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Seeded Archive Full", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.02)
    _write_investigator_stream(ws, cost=0.03)
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.total_cost_usd is None
    assert row.cost_totals.discussion_usd is None
    assert row.cost_totals.investigator_usd is None


def test_new_dir_already_archived_row_still_reads_existing_status_summary_cost(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Seeded Archive Manager Cost", "d", root=ws_root)
    _write_manager_stream(ws, cost=0.02)
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.cost_totals.status_summary_usd == pytest.approx(0.02)
    assert row.total_cost_usd == pytest.approx(0.02)


def test_backfill_computes_new_cost_categories_for_meta_only_archived_row(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Backfill Full", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.02)
    _write_investigator_stream(ws, cost=0.03)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.02)
    assert row.cost_totals.investigator_usd == pytest.approx(0.03)
    assert row.total_cost_usd == pytest.approx(0.05)


def test_old_blob_version_2_discarded_and_rederives_new_cost_categories(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create("V2 Blob WS", "d", root=ws_root)
    _write_session_stream(ws, _SESSION_A, "stream.jsonl", cost=0.07)
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    blob["version"] = 2
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    fresh.backfill_archived()
    row = _row_by_id(fresh.archived_rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.07)


def _write_cleared_cost(ws: Workspace, session_id: str, *, discussion: float) -> None:
    cleared_dir = ws.path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True, exist_ok=True)
    payload = {"discussion_usd": discussion, "collision_check_usd": None}
    (cleared_dir / f"{session_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_archived_row_includes_cleared_chat_cost(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Cleared Cost Archive WS", "d", root=ws_root)
    _write_cleared_cost(ws, _SESSION_A, discussion=0.07)
    index.rows()
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.cost_totals.discussion_usd == pytest.approx(0.07)
    assert row.total_cost_usd == pytest.approx(0.07)


def test_stream_filenames_and_session_regex_locked_to_chat_core() -> None:
    assert workspace_cost_mod._STREAM_FILE == _chat_core._STREAM_FILE  # noqa: SLF001
    assert (  # noqa: SLF001
        workspace_cost_mod._COLLISION_STREAM_FILE == _chat_core._COLLISION_STREAM_FILE
    )
    assert (  # noqa: SLF001
        workspace_cost_mod._SESSION_ID_RE.pattern == _chat_core._SESSION_ID_RE.pattern
    )


def test_investigator_stream_filename_locked_to_investigator_module() -> None:
    assert workspace_cost_mod._INVESTIGATOR_STREAM_FILE == investigator_mod._STREAM_FILE  # noqa: SLF001


def _write_workflow_with_node(
    ws: Workspace,
    *,
    team: str,
    status: str,
    inv_id: str,
    node_started: datetime,
    node_completed: datetime | None,
    doc_completed: datetime | None,
    model: str = "sonnet",
) -> None:
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{ws.task_id}",
        team=team,
        status=status,  # type: ignore[arg-type]
        started_at=node_started,
        completed_at=doc_completed,
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model=model,
                status="success" if node_completed is not None else "running",
                started_at=node_started,
                completed_at=node_completed,
            )
        ],
    )
    save_workflow(ws, doc)


def test_row_active_seconds_none_when_no_non_sentinel_node(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("No Active WS", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.active_closed_seconds is None
    assert row.active_open_since is None
    assert row.live_active_seconds(datetime.now(UTC)) is None


def test_row_active_seconds_excludes_sentinel_node(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sentinel Only WS", "d", root=ws_root)
    started = datetime.now(UTC)
    _write_workflow_with_node(
        ws,
        team="deploy_verify",
        status="success",
        inv_id="inv_001",
        node_started=started,
        node_completed=started,
        doc_completed=started,
        model="none",
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.active_closed_seconds is None
    assert row.active_open_since is None


def test_row_active_seconds_closed_node(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Active Closed WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=60)
    completed = started + timedelta(seconds=40)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="success",
        inv_id="inv_001",
        node_started=started,
        node_completed=completed,
        doc_completed=completed,
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.active_open_since is None
    assert row.active_closed_seconds == pytest.approx(40.0)
    assert row.live_active_seconds(datetime.now(UTC)) == pytest.approx(40.0)


def test_row_active_seconds_open_node_advances_without_row_rebuild(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Active Open WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=10)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="running",
        inv_id="inv_001",
        node_started=started,
        node_completed=None,
        doc_completed=None,
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.active_open_since == started
    now = datetime.now(UTC)
    earlier = row.live_active_seconds(now)
    later = row.live_active_seconds(now + timedelta(seconds=30))
    assert earlier is not None
    assert later == pytest.approx(earlier + 30.0)


def test_row_active_seconds_clamps_crashed_node_on_terminal_doc(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Crashed WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=100)
    doc_completed = started + timedelta(seconds=20)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="failed",
        inv_id="inv_001",
        node_started=started,
        node_completed=None,
        doc_completed=doc_completed,
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.active_open_since is None
    assert row.active_closed_seconds == pytest.approx(20.0)


def test_row_active_seconds_updates_in_place_when_node_completes(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Complete In Place WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=30)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="running",
        inv_id="inv_001",
        node_started=started,
        node_completed=None,
        doc_completed=None,
    )
    first = _row_by_id(index.rows(), ws.task_id)
    assert first.active_open_since == started
    completed = started + timedelta(seconds=30)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="success",
        inv_id="inv_001",
        node_started=started,
        node_completed=completed,
        doc_completed=completed,
    )
    second = _row_by_id(index.rows(), ws.task_id)
    assert second.active_open_since is None
    assert second.active_closed_seconds == pytest.approx(30.0)


def test_row_uses_settled_active_seconds_once_delivered_and_never_stretches(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Delivered WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=60)
    completed = started + timedelta(seconds=40)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="success",
        inv_id="inv_001",
        node_started=started,
        node_completed=completed,
        doc_completed=completed,
    )
    assert _row_by_id(index.rows(), ws.task_id).active_closed_seconds == pytest.approx(40.0)

    ws_mod.mark_delivered(ws, settled_active_seconds=999.0)
    later_start = completed
    later_end = completed + timedelta(seconds=500)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="success",
        inv_id="inv_002",
        node_started=later_start,
        node_completed=later_end,
        doc_completed=later_end,
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.delivered_at is not None
    assert row.active_closed_seconds == pytest.approx(999.0)
    assert row.active_open_since is None


def test_archived_row_uses_settled_active_seconds(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Delivered Archived WS", "d", root=ws_root)
    started = datetime.now(UTC) - timedelta(seconds=60)
    completed = started + timedelta(seconds=15)
    _write_workflow_with_node(
        ws,
        team="implementation",
        status="success",
        inv_id="inv_001",
        node_started=started,
        node_completed=completed,
        doc_completed=completed,
    )
    ws = ws_mod.mark_delivered(ws, settled_active_seconds=15.0)
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.active_closed_seconds == pytest.approx(15.0)
    assert row.active_open_since is None
    assert row.delivered_at is not None


def test_workspace_row_live_active_seconds_no_data() -> None:
    ts = datetime.now(UTC)
    row = WorkspaceRow(task_id="a", title="t", status=Status.ACTIVE, created_at=ts, updated_at=ts)
    assert row.live_active_seconds(ts) is None


def test_workspace_row_live_active_seconds_combines_closed_and_open() -> None:
    ts = datetime.now(UTC)
    row = WorkspaceRow(
        task_id="a",
        title="t",
        status=Status.ACTIVE,
        created_at=ts,
        updated_at=ts,
        active_closed_seconds=10.0,
        active_open_since=ts,
    )
    assert row.live_active_seconds(ts + timedelta(seconds=5)) == pytest.approx(15.0)


def test_old_v4_archived_blob_discarded_on_version_mismatch() -> None:
    ts = datetime.now(UTC).isoformat()
    legacy_row = {
        "task_id": "legacy-v4",
        "title": "Legacy",
        "status": "archived",
        "created_at": ts,
        "updated_at": ts,
    }
    raw = json.dumps({"version": 4, "complete": True, "rows": {"legacy-v4": legacy_row}})
    rows, complete = wsi._parse_blob_rows(raw)  # noqa: SLF001
    assert rows == {}
    assert complete is False


def test_row_size_class_from_implementation_plan_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sized Plan WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id,
            pr_number=None,
            from_team="implementation_plan",
            files_to_edit=["a.py", "b.py", "c.py", "d.py"],
        ),
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.S


def test_row_size_class_falls_back_to_architecture_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sized Architecture WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id, pr_number=None, from_team="architecture", files_to_edit=["a.py"]
        ),
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.XS


def test_row_size_class_falls_back_to_decomposer_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sized Decomposer WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id,
            pr_number=None,
            from_team="decomposer",
            files_to_edit=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
        ),
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.M


def test_row_size_class_prefers_plan_over_architecture_handover(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sized Both WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id,
            pr_number=None,
            from_team="architecture",
            files_to_edit=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
        ),
    )
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id, pr_number=None, from_team="implementation_plan", files_to_edit=["a.py"]
        ),
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.XS


def test_row_size_class_falls_back_to_task_count_when_no_handover_files(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create(
        "Sized Tasks WS",
        "d",
        root=ws_root,
        tasks=[Task(id=f"t{i}", status=TaskStatus.PENDING) for i in range(5)],
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.S


def test_row_size_class_none_when_no_signal_available(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Unsized WS", "d", root=ws_root)
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class is None


def test_row_size_class_updates_on_new_handover_without_meta_change(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    ws = create("Sized Update WS", "d", root=ws_root)
    assert _row_by_id(index.rows(), ws.task_id).size_class is None
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id,
            pr_number=None,
            from_team="implementation_plan",
            files_to_edit=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
        ),
    )
    row = _row_by_id(index.rows(), ws.task_id)
    assert row.size_class == SizeClass.M


def test_archived_row_preserves_size_class(index: WorkspaceIndex, ws_root: Path) -> None:
    ws = create("Archived Sized WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id, pr_number=None, from_team="implementation_plan", files_to_edit=["a.py"]
        ),
    )
    index.rows()
    ws_mod.transition(ws, Status.ARCHIVED)
    row = _row_by_id(index.archived_rows(), ws.task_id)
    assert row.size_class == SizeClass.XS


def test_old_blob_version_5_discarded_and_rederives_size_class(
    ws_root: Path, queue: Queue, index: WorkspaceIndex
) -> None:
    ws = create("V5 Blob WS", "d", root=ws_root)
    save_handover(
        ws,
        _minimal_handover(
            ws.task_id,
            pr_number=None,
            from_team="implementation_plan",
            files_to_edit=["a.py", "b.py", "c.py", "d.py"],
        ),
    )
    ws_mod.transition(ws, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    raw = queue.get_meta(ARCHIVED_ROWS_META_KEY)
    assert raw is not None
    blob = json.loads(raw)
    blob["version"] = 5
    queue.set_meta(ARCHIVED_ROWS_META_KEY, json.dumps(blob))

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    fresh.rebuild()
    assert fresh._archived_complete is False  # noqa: SLF001
    fresh.backfill_archived()
    row = _row_by_id(fresh.archived_rows(), ws.task_id)
    assert row.size_class == SizeClass.S


def test_validated_rows_returns_both_buckets(index: WorkspaceIndex, ws_root: Path) -> None:
    live = create("Validated Live WS", "d", root=ws_root)
    archived = create("Validated Archived WS", "d", root=ws_root)
    ws_mod.transition(archived, Status.ARCHIVED)
    rows, archived_rows = index.validated_rows()
    assert {r.task_id for r in rows} == {live.task_id}
    assert {r.task_id for r in archived_rows} == {archived.task_id}


def test_validated_rows_self_heals_cold_discovered_row_after_earlier_backfill_completed(
    index: WorkspaceIndex, ws_root: Path
) -> None:
    first = create("First Archive", "d", root=ws_root)
    ws_mod.transition(first, Status.ARCHIVED)
    index.rows()
    index.backfill_archived()
    assert index._archived_complete is True  # noqa: SLF001

    second = create("Second Archive", "d", root=ws_root)
    _write_workflow(second, team="research", status="success", inv_id="inv_001", cost=0.02)
    ws_mod.transition(second, Status.ARCHIVED)
    index.rows()
    provisional = _row_by_id(index.archived_snapshot(), second.task_id)
    assert provisional.total_cost_usd is None

    _, archived_rows = index.validated_rows()
    healed = _row_by_id(archived_rows, second.task_id)
    assert healed.total_cost_usd == pytest.approx(0.02)
    assert healed.latest_team == "research"
