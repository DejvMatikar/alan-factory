from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import proactive_rebase as pr_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, save_handover
from alan.queue import Queue
from alan.teams import _pr_branch, _pr_state_paths
from alan.teams._pr_branch import BranchUpdateOutcome
from alan.workspace import Workspace

_KNOWN = "sha-known"


def _pr_handover(ws: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"h_{ws.task_id}_{pr_number}",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="test",
        pr_number=pr_number,
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(_pr_branch, "pr_is_open", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)


def _fan_out(tmp_path: Path, queue: Queue, pr_number: int) -> None:
    ws_root = tmp_path / "workspaces"
    ws = workspace_mod.create(f"fan-{pr_number}", "desc", root=ws_root)
    save_handover(ws, _pr_handover(ws, pr_number))
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(exist_ok=True)
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=1)  # noqa: SLF001


def test_a_successful_update_whose_head_could_not_be_read_leaves_an_unknown_head_marker(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.UPDATED
    )
    _fan_out(tmp_path, queue, 43)
    marker = _pr_state_paths.load_system_head(43, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (None, "proactive_rebase", False)
    assert marker.written_at is not None


@pytest.mark.parametrize(
    "outcome", [BranchUpdateOutcome.CONFLICT, BranchUpdateOutcome.FAILED]
)
def test_an_update_that_did_not_happen_leaves_the_marker_as_it_found_it(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, outcome: BranchUpdateOutcome
) -> None:
    _pr_state_paths.save_system_head(44, _KNOWN, "dirty", DEFAULT_FORGE_TARGET)
    before = _pr_state_paths.load_system_head(44, DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: outcome)
    _fan_out(tmp_path, queue, 44)
    after = _pr_state_paths.load_system_head(44, DEFAULT_FORGE_TARGET)
    assert after is not None and before is not None
    assert (after.head_sha, after.rung) == (_KNOWN, "dirty")
    assert after.written_at == before.written_at


def test_an_update_that_raised_leaves_no_marker(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> BranchUpdateOutcome:
        raise RuntimeError("gh exploded")

    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", boom)
    _fan_out(tmp_path, queue, 45)
    assert _pr_state_paths.load_system_head(45, DEFAULT_FORGE_TARGET) is None


def test_a_read_head_is_still_recorded_as_before(
    tmp_path: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.UPDATED
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    _fan_out(tmp_path, queue, 46)
    marker = _pr_state_paths.load_system_head(46, DEFAULT_FORGE_TARGET)
    assert marker is not None and marker.head_sha == "sha-new"
