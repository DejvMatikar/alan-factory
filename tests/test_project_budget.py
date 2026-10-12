from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import project as project_mod
from alan import project_budget
from alan import workspace as workspace_mod
from alan.queue import Queue
from alan.workflow import WorkflowDoc, WorkflowTask, save_workflow
from alan.workspace_index import ProjectBurn, WorkspaceIndex
from tests.fs_timestamp_support import bump_mtime


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(p))
    return p


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def _stamp_cost(ws: workspace_mod.Workspace, *, cost: float) -> None:
    doc = WorkflowDoc(
        id="inv_001",
        namespace=f"alan.workspace.{ws.task_id}",
        team="implementation",
        status="success",
        started_at=datetime.now(UTC),
        tasks=[
            WorkflowTask(
                id="worker", role="worker", model="sonnet", status="success",
                total_cost_usd=cost,
            )
        ],
    )
    save_workflow(ws, doc)


def test_budget_state_normal_when_burn_is_none() -> None:
    assert project_budget.budget_state(None) == "normal"


def test_budget_state_normal_when_budget_not_set() -> None:
    burn = ProjectBurn(burn_usd=1000.0, budget_usd=None, remaining_usd=None)
    assert project_budget.budget_state(burn) == "normal"


def test_budget_state_paused_at_exact_zero_remaining() -> None:
    burn = ProjectBurn(burn_usd=10.0, budget_usd=10.0, remaining_usd=0.0)
    assert project_budget.budget_state(burn) == "paused"


def test_budget_state_paused_when_over_budget() -> None:
    burn = ProjectBurn(burn_usd=15.0, budget_usd=10.0, remaining_usd=-5.0)
    assert project_budget.budget_state(burn) == "paused"


def test_budget_state_tolerates_missing_remaining_when_budget_set() -> None:
    burn = ProjectBurn(burn_usd=1.0, budget_usd=10.0, remaining_usd=None)
    assert project_budget.budget_state(burn) == "normal"


def test_budget_state_approaching_at_exact_warn_fraction() -> None:
    burn = ProjectBurn(burn_usd=8.0, budget_usd=10.0, remaining_usd=2.0)
    assert project_budget.budget_state(burn) == "approaching"


def test_budget_state_approaching_above_warn_fraction() -> None:
    burn = ProjectBurn(burn_usd=9.5, budget_usd=10.0, remaining_usd=0.5)
    assert project_budget.budget_state(burn) == "approaching"


def test_budget_state_normal_below_warn_fraction() -> None:
    burn = ProjectBurn(burn_usd=1.0, budget_usd=10.0, remaining_usd=9.0)
    assert project_budget.budget_state(burn) == "normal"


def test_warn_fraction_is_the_documented_constant() -> None:
    assert project_budget.WARN_FRACTION == pytest.approx(0.8)


def test_resolve_project_burn_none_when_index_not_supplied() -> None:
    assert project_budget.resolve_project_burn("does-not-matter", None) is None


def test_resolve_project_burn_none_for_unknown_project(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    _ = projects_root
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    assert project_budget.resolve_project_burn("does-not-exist", index) is None


def test_resolve_project_burn_matches_stamped_cost(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("Burn Project", root=projects_root)
    project = project_mod.update(project, {"budget_usd": 10.0}, root=projects_root)
    ws = workspace_mod.create("burn-ws", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=3.5)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    burn = project_budget.resolve_project_burn(project.id, index)

    assert burn is not None
    assert burn.burn_usd == pytest.approx(3.5)
    assert burn.budget_usd == pytest.approx(10.0)
    assert burn.remaining_usd == pytest.approx(6.5)


def test_resolve_burn_for_project_none_when_index_not_supplied(
    root: Path, projects_root: Path
) -> None:
    project = project_mod.create("Burn Project", root=projects_root)
    assert project_budget.resolve_burn_for_project(project, None) is None


def test_resolve_burn_for_project_matches_stamped_cost(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("Burn Project", root=projects_root)
    project = project_mod.update(project, {"budget_usd": 10.0}, root=projects_root)
    ws = workspace_mod.create("burn-ws-2", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=3.5)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    burn = project_budget.resolve_burn_for_project(project, index)

    assert burn is not None
    assert burn.burn_usd == pytest.approx(3.5)
    assert burn.budget_usd == pytest.approx(10.0)
    assert burn.remaining_usd == pytest.approx(6.5)


def test_resolve_budget_state_none_index_is_normal() -> None:
    assert project_budget.resolve_budget_state("anything", None) == "normal"


def test_resolve_budget_state_paused_when_over_budget(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("Paused Project", root=projects_root)
    project = project_mod.update(project, {"budget_usd": 1.0}, root=projects_root)
    ws = workspace_mod.create("paused-ws", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=5.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    assert project_budget.resolve_budget_state(project.id, index) == "paused"
    assert project_budget.budget_exhausted(project.id, index) is True


def test_resolve_budget_state_approaching_below_hard_limit(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("Approaching Project", root=projects_root)
    project = project_mod.update(project, {"budget_usd": 10.0}, root=projects_root)
    ws = workspace_mod.create("approaching-ws", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=8.5)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    assert project_budget.resolve_budget_state(project.id, index) == "approaching"
    assert project_budget.budget_exhausted(project.id, index) is False


def test_resolve_budget_state_normal_with_headroom(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("Normal Project", root=projects_root)
    project = project_mod.update(project, {"budget_usd": 10.0}, root=projects_root)
    ws = workspace_mod.create("normal-ws", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=1.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    assert project_budget.resolve_budget_state(project.id, index) == "normal"
    assert project_budget.budget_exhausted(project.id, index) is False


def test_budget_exhausted_false_when_no_budget_set(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = project_mod.create("No Budget Project", root=projects_root)
    ws = workspace_mod.create("no-budget-ws", "d", root=root, project_id=project.id)
    _stamp_cost(ws, cost=1000.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    assert project_budget.budget_exhausted(project.id, index) is False


def test_budget_exhausted_false_when_index_not_supplied() -> None:
    assert project_budget.budget_exhausted("anything", None) is False


def test_project_burn_includes_cleared_chat_cost(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    import json
    import shutil

    from alan import workspace_cost as workspace_cost_mod

    project = project_mod.create("Cleared Cost Project", root=projects_root)
    ws = workspace_mod.create("cleared-cost-ws", "d", root=root, project_id=project.id)
    session_id = "11111111-1111-1111-1111-111111111111"
    session_dir = ws.path / "chat" / session_id
    session_dir.mkdir(parents=True)
    event = {"type": "result", "total_cost_usd": 2.0, "usage": {}}
    (session_dir / "stream.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    cleared_dir = ws.path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True)
    tombstone = workspace_cost_mod.ClearedSessionCost(discussion_usd=2.0)
    (cleared_dir / f"{session_id}.json").write_text(
        tombstone.model_dump_json(), encoding="utf-8"
    )
    shutil.rmtree(session_dir)

    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    burn = project_budget.resolve_project_burn(project.id, index)
    assert burn is not None
    assert burn.burn_usd == pytest.approx(2.0)


def test_project_burn_does_not_drop_after_chat_clear(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    import json
    import shutil

    from alan import workspace_cost as workspace_cost_mod

    project = project_mod.create("Burn Preserve Project", root=projects_root)
    ws = workspace_mod.create("burn-preserve-ws", "d", root=root, project_id=project.id)
    session_id = "22222222-2222-2222-2222-222222222222"
    session_dir = ws.path / "chat" / session_id
    session_dir.mkdir(parents=True)
    event = {"type": "result", "total_cost_usd": 1.25, "usage": {}}
    (session_dir / "stream.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    before = project_budget.resolve_project_burn(project.id, index)
    assert before is not None
    assert before.burn_usd == pytest.approx(1.25)

    cleared_dir = ws.path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True)
    tombstone = workspace_cost_mod.ClearedSessionCost(discussion_usd=1.25)
    (cleared_dir / f"{session_id}.json").write_text(
        tombstone.model_dump_json(), encoding="utf-8"
    )
    shutil.rmtree(session_dir)
    bump_mtime(ws.path / "chat")
    index.refresh()

    after = project_budget.resolve_project_burn(project.id, index)
    assert after is not None
    assert after.burn_usd >= before.burn_usd
    assert after.burn_usd == pytest.approx(1.25)
