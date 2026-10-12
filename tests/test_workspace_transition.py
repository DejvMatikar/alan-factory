from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import lifecycle
from alan import workspace as workspace_mod
from alan.park import Blocker, ParkReason
from alan.workspace import Status, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def ws(root: Path) -> Workspace:
    return workspace_mod.create("transition-writer", "d", root=root)


_BLOCKERS = [Blocker(kind="reason", name="n", detail="d")]
_FINISHED = [Status.COMPLETED, Status.FAILED, Status.ESCALATED, Status.PARTIAL]


def _rewrite_meta(ws: Workspace, **fields: object) -> None:
    meta_path = ws.path / "meta.json"
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    raw.update(fields)
    meta_path.write_text(json.dumps(raw), encoding="utf-8")


def _completed_without_a_stamp(ws: Workspace, root: Path) -> Workspace:
    workspace_mod.transition(ws, Status.COMPLETED)
    _rewrite_meta(ws, terminal_at=None)
    return workspace_mod.read(ws.task_id, root=root)


def test_the_absorbed_status_writer_is_gone_from_the_workspace_module() -> None:
    assert not hasattr(workspace_mod, "update_status")


def test_transition_persists_status_and_bumps_updated_at(ws: Workspace, root: Path) -> None:
    moved = workspace_mod.transition(ws, Status.PAUSED)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert moved.meta.status is Status.PAUSED
    assert reloaded.meta.status is Status.PAUSED
    assert moved.meta.updated_at > ws.meta.updated_at


def test_transition_applies_the_planner_output(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Status] = []

    def planner(status: Status, **_kw: object) -> dict[str, object]:
        seen.append(status)
        return {"status": Status.FAILED}

    monkeypatch.setattr(lifecycle, "transition", planner)
    moved = workspace_mod.transition(ws, Status.PAUSED)
    assert seen == [Status.PAUSED]
    assert moved.meta.status is Status.FAILED


def test_transition_forwards_all_six_keywords_to_the_planner(
    ws: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def planner(status: Status, **kw: object) -> dict[str, object]:
        captured.update(kw)
        return {"status": status}

    monkeypatch.setattr(lifecycle, "transition", planner)
    workspace_mod.transition(
        ws,
        Status.PAUSED,
        merge_block_class="dirty",
        clear_merge_block_class=True,
        park_reason=ParkReason.CRASH,
        blockers=_BLOCKERS,
        clear_park_reason=True,
        clear_blockers=True,
    )
    assert captured == {
        "merge_block_class": "dirty",
        "clear_merge_block_class": True,
        "park_reason": ParkReason.CRASH,
        "blockers": _BLOCKERS,
        "clear_park_reason": True,
        "clear_blockers": True,
    }


@pytest.mark.parametrize("status", list(Status))
def test_transition_clears_park_state_only_for_the_clearing_rows(
    ws: Workspace, root: Path, status: Status
) -> None:
    parked = workspace_mod.transition(
        ws, Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION, blockers=_BLOCKERS
    )
    moved = workspace_mod.transition(parked, status)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    cleared = lifecycle.WORKSPACE_TABLE[status].clears_park_reason
    assert reloaded.meta == moved.meta
    assert (moved.meta.park_reason is None) is cleared
    assert (moved.meta.blockers == []) is cleared


def test_a_new_workspace_has_no_terminal_at(ws: Workspace) -> None:
    assert ws.meta.terminal_at is None


def test_a_meta_written_before_terminal_at_existed_reads_back_without_one(
    ws: Workspace, root: Path
) -> None:
    meta_path = ws.path / "meta.json"
    raw = json.loads(meta_path.read_text(encoding="utf-8"))
    del raw["terminal_at"]
    meta_path.write_text(json.dumps(raw), encoding="utf-8")
    assert workspace_mod.read(ws.task_id, root=root).meta.terminal_at is None


@pytest.mark.parametrize("status", _FINISHED)
def test_transition_stamps_terminal_at_on_entry_into_a_finished_status(
    ws: Workspace, root: Path, status: Status
) -> None:
    moved = workspace_mod.transition(ws, status)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert moved.meta.terminal_at == moved.meta.updated_at
    assert reloaded.meta.terminal_at == moved.meta.terminal_at


@pytest.mark.parametrize(
    "status", [Status.ACTIVE, Status.PAUSED, Status.AWAITING_INPUT, Status.ARCHIVED]
)
def test_transition_does_not_stamp_terminal_at_for_an_unfinished_status(
    ws: Workspace, status: Status
) -> None:
    assert workspace_mod.transition(ws, status).meta.terminal_at is None


def test_transition_restamps_terminal_at_on_a_later_finished_entry_from_a_different_status(
    ws: Workspace,
) -> None:
    first = workspace_mod.transition(ws, Status.COMPLETED)
    second = workspace_mod.transition(first, Status.FAILED)
    assert first.meta.terminal_at is not None
    assert second.meta.terminal_at is not None
    assert second.meta.terminal_at > first.meta.terminal_at
    assert second.meta.terminal_at == second.meta.updated_at


def test_transition_restamps_terminal_at_when_a_finished_workspace_is_reopened_and_finished(
    ws: Workspace,
) -> None:
    first = workspace_mod.transition(ws, Status.COMPLETED)
    reopened = workspace_mod.transition(first, Status.ACTIVE)
    second = workspace_mod.transition(reopened, Status.COMPLETED)
    assert reopened.meta.terminal_at == first.meta.terminal_at
    assert first.meta.terminal_at is not None
    assert second.meta.terminal_at is not None
    assert second.meta.terminal_at > first.meta.terminal_at


def test_transition_keeps_terminal_at_when_the_same_finished_status_is_rewritten(
    ws: Workspace,
) -> None:
    first = workspace_mod.transition(ws, Status.ESCALATED)
    again = workspace_mod.transition(first, Status.ESCALATED)
    assert again.meta.terminal_at == first.meta.terminal_at
    assert again.meta.updated_at > first.meta.updated_at


def test_transition_stamps_a_missing_terminal_at_on_a_same_status_rewrite(
    ws: Workspace, root: Path
) -> None:
    legacy = _completed_without_a_stamp(ws, root)
    moved = workspace_mod.transition(legacy, Status.COMPLETED)
    assert legacy.meta.terminal_at is None
    assert moved.meta.terminal_at == moved.meta.updated_at


def test_archiving_and_unarchiving_never_change_terminal_at(ws: Workspace, root: Path) -> None:
    completed = workspace_mod.transition(ws, Status.COMPLETED)
    archived = workspace_mod.transition(completed, Status.ARCHIVED)
    unarchived = workspace_mod.transition(archived, Status.ACTIVE)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert completed.meta.terminal_at is not None
    assert archived.meta.terminal_at == completed.meta.terminal_at
    assert unarchived.meta.terminal_at == completed.meta.terminal_at
    assert reloaded.meta.terminal_at == completed.meta.terminal_at


def test_archiving_a_finished_workspace_without_a_stamp_does_not_invent_one(
    ws: Workspace, root: Path
) -> None:
    archived = workspace_mod.transition(_completed_without_a_stamp(ws, root), Status.ARCHIVED)
    assert archived.meta.terminal_at is None


@pytest.mark.parametrize("status", [Status.ARCHIVED, Status.ACTIVE, Status.PAUSED])
def test_a_stale_snapshot_never_clears_a_terminal_at_written_since(
    ws: Workspace, root: Path, status: Status
) -> None:
    finished = workspace_mod.transition(ws, Status.COMPLETED)
    moved = workspace_mod.transition(ws, status)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert finished.meta.terminal_at is not None
    assert moved.meta.terminal_at == finished.meta.terminal_at
    assert reloaded.meta.terminal_at == finished.meta.terminal_at


def test_a_stale_snapshot_finishing_into_the_status_already_on_disk_does_not_restamp(
    ws: Workspace, root: Path
) -> None:
    finished = workspace_mod.transition(ws, Status.COMPLETED)
    again = workspace_mod.transition(ws, Status.COMPLETED)
    assert again.meta.terminal_at == finished.meta.terminal_at
    assert workspace_mod.read(ws.task_id, root=root).meta.terminal_at == finished.meta.terminal_at


def test_a_stale_snapshot_finishing_into_another_status_restamps_over_the_one_on_disk(
    ws: Workspace,
) -> None:
    finished = workspace_mod.transition(ws, Status.COMPLETED)
    refinished = workspace_mod.transition(ws, Status.FAILED)
    assert finished.meta.terminal_at is not None
    assert refinished.meta.terminal_at is not None
    assert refinished.meta.terminal_at > finished.meta.terminal_at


def test_transition_falls_back_to_the_snapshot_when_the_meta_on_disk_is_malformed(
    ws: Workspace, root: Path
) -> None:
    (ws.path / "meta.json").write_text("not json", encoding="utf-8")
    moved = workspace_mod.transition(ws, Status.COMPLETED)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert moved.meta.terminal_at == moved.meta.updated_at
    assert reloaded.meta == moved.meta


def test_transition_leaves_unrelated_meta_untouched(ws: Workspace) -> None:
    moved = workspace_mod.transition(ws, Status.PAUSED)
    assert moved.meta.model_copy(update={"status": ws.meta.status, "updated_at": None}) == (
        ws.meta.model_copy(update={"updated_at": None})
    )


def test_transition_into_a_finished_status_touches_only_status_updated_at_and_terminal_at(
    ws: Workspace,
) -> None:
    moved = workspace_mod.transition(ws, Status.FAILED)
    blanked = {"status": ws.meta.status, "updated_at": None, "terminal_at": None}
    assert moved.meta.model_copy(update=blanked) == ws.meta.model_copy(update=blanked)
