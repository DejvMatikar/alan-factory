from __future__ import annotations

from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.queue import Queue
from alan.teams import _pr_state_paths
from alan.teams.triage_pregate import _classify_build_task, _fix_cycle_obs
from alan.workflow import PendingTaskSpec
from alan.workspace import create as create_workspace


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "fix-cycle-state"))


def test_fix_cycle_obs_no_pr_number_is_zero_and_not_escalated(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    count, escalate = _fix_cycle_obs(None, ws, True)
    assert count == 0
    assert escalate is False


def test_fix_cycle_obs_reads_the_persisted_count(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    for _ in range(4):
        _pr_state_paths.bump_fix_cycle_count(900, DEFAULT_FORGE_TARGET)
    count, escalate = _fix_cycle_obs(900, ws, True)
    assert count == 4
    assert escalate is True


def test_fix_cycle_obs_not_escalated_when_job_not_running(ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    for _ in range(4):
        _pr_state_paths.bump_fix_cycle_count(901, DEFAULT_FORGE_TARGET)
    count, escalate = _fix_cycle_obs(901, ws, False)
    assert count == 4
    assert escalate is False


def test_classify_build_task_carries_fix_cycle_obs(queue: Queue, ws_root: Path) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    for _ in range(3):
        _pr_state_paths.bump_fix_cycle_count(902, DEFAULT_FORGE_TARGET)
    pr_info = {
        "number": 902, "state": "OPEN", "reviewDecision": None,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.obs["fix_cycle_count"] == 3
    assert surface.obs["fix_cycle_escalate"] is False


def test_classify_build_task_no_pr_defaults_fix_cycle_obs_to_zero(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    surface = _classify_build_task(ws, queue, None)
    assert surface.obs["fix_cycle_count"] == 0
    assert surface.obs["fix_cycle_escalate"] is False


def test_classify_build_task_escalates_when_job_running_at_threshold(
    queue: Queue, ws_root: Path
) -> None:
    ws = create_workspace("t", "d", root=ws_root)
    for _ in range(3):
        _pr_state_paths.bump_fix_cycle_count(903, DEFAULT_FORGE_TARGET)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="implementation_fix", tasks=[PendingTaskSpec(id="n", role="r", model="haiku")]
    )
    queue.enqueue(ws.task_id, "implementation_fix", triggered_by_invocation_id=inv_id)
    queue.claim()
    pr_info = {
        "number": 903, "state": "OPEN", "reviewDecision": None,
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"conclusion": "FAILURE"}],
    }
    surface = _classify_build_task(ws, queue, pr_info)
    assert surface.obs["fix_cycle_count"] == 3
    assert surface.obs["fix_cycle_escalate"] is True
