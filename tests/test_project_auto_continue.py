from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import merge_gate as merge_gate_mod
from alan import project as project_mod
from alan import project_auto_continue
from alan import project_roadmap as project_roadmap_mod
from alan import workspace as workspace_mod
from alan.events import PubSub
from alan.handover import Handover, save_handover
from alan.project_roadmap import ParsedRoadmapItem, RoadmapItem
from alan.queue import Job, JobStatus, Queue
from alan.workflow import WorkflowDoc, WorkflowTask, save_workflow
from alan.workspace import Status, Workspace
from alan.workspace_index import WorkspaceIndex


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(p))
    return p


@pytest.fixture
def logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(p))
    return p


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


class _RecordingEnqueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[Job, str]:
        self.calls.append(kwargs)
        job = Job(
            id=len(self.calls),
            task_id=kwargs["task_id"],
            team=kwargs["team"].name,
            status=JobStatus.PENDING,
            enqueued_at=datetime.now(UTC),
        )
        return job, f"inv_{len(self.calls):03d}"


def _project(
    projects_root: Path, *, autonomy: str = "propose", budget_usd: float | None = None
) -> project_mod.Project:
    project = project_mod.create("Sprocket", root=projects_root)
    updates: dict[str, Any] = {"autonomy": autonomy}
    if budget_usd is not None:
        updates["budget_usd"] = budget_usd
    return project_mod.update(project, updates, root=projects_root)


def _seed_roadmap(project_id: str, projects_root: Path, *titles: str) -> list[RoadmapItem]:
    parsed = [ParsedRoadmapItem(title=t, text=f"{t} body") for t in titles]
    return project_roadmap_mod.seed_items(project_id, parsed, root=projects_root)


def _handover(task_id: str, *, pr_number: int | None = None) -> Handover:
    return Handover(
        handover_id="h1",
        handover_chain_id="h1",
        handover_type="terminal",
        task_id=task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="done",
        pr_number=pr_number,
    )


def _deliver(ws: Workspace, *, pr_number: int = 1) -> Workspace:
    save_handover(ws, _handover(ws.task_id, pr_number=pr_number))
    return workspace_mod.transition(ws, Status.COMPLETED)


def _fail(ws: Workspace) -> Workspace:
    return workspace_mod.transition(ws, Status.FAILED)


def test_next_unattempted_item_returns_none_when_no_roadmap(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    assert project_auto_continue.next_unattempted_item(project.id, workspaces_root=root) is None


def test_next_unattempted_item_returns_none_when_nothing_stamped(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    _seed_roadmap(project.id, projects_root, "First", "Second")
    assert project_auto_continue.next_unattempted_item(project.id, workspaces_root=root) is None


def test_next_unattempted_item_skips_attempted_item_regardless_of_status(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "First WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _fail(ws)
    result = project_auto_continue.next_unattempted_item(project.id, workspaces_root=root)
    assert result is not None
    assert result.id == items[1].id


def test_next_unattempted_item_returns_none_when_all_attempted(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Only")
    workspace_mod.create(
        "Only WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    assert project_auto_continue.next_unattempted_item(project.id, workspaces_root=root) is None


def test_next_unattempted_item_ignores_same_project_workspace_with_no_roadmap_item_id(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    workspace_mod.create(
        "First WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    workspace_mod.create("Untied WS", "d", root=root, project_id=project.id)
    result = project_auto_continue.next_unattempted_item(project.id, workspaces_root=root)
    assert result is not None
    assert result.id == items[1].id


def test_next_unattempted_item_ignores_workspaces_from_other_projects(
    root: Path, projects_root: Path
) -> None:
    project_a = _project(projects_root)
    project_b = project_mod.create("Other", root=projects_root)
    items = _seed_roadmap(project_a.id, projects_root, "First", "Second")
    workspace_mod.create(
        "First WS", "d", root=root, project_id=project_a.id, roadmap_item_id=items[0].id
    )
    workspace_mod.create(
        "Other WS", "d", root=root, project_id=project_b.id, roadmap_item_id=items[1].id
    )
    result = project_auto_continue.next_unattempted_item(project_a.id, workspaces_root=root)
    assert result is not None
    assert result.id == items[1].id


def test_next_proposable_item_returns_none_when_roadmap_untouched(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    _seed_roadmap(project.id, projects_root, "First", "Second")
    assert project_auto_continue.next_proposable_item(project.id, workspaces_root=root) is None


def test_next_proposable_item_returns_next_when_prior_delivered(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "First WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(ws)
    result = project_auto_continue.next_proposable_item(project.id, workspaces_root=root)
    assert result is not None
    assert result.id == items[1].id


def test_next_proposable_item_none_when_prior_item_not_delivered(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "First WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _fail(ws)
    assert project_auto_continue.next_proposable_item(project.id, workspaces_root=root) is None


def test_next_proposable_item_none_when_prior_item_still_in_flight(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    workspace_mod.create(
        "First WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    assert project_auto_continue.next_proposable_item(project.id, workspaces_root=root) is None


def test_next_proposable_item_none_when_roadmap_fully_delivered(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Only")
    ws = workspace_mod.create(
        "Only WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(ws)
    assert project_auto_continue.next_proposable_item(project.id, workspaces_root=root) is None


def test_next_proposable_item_delivered_via_any_of_multiple_stamped_workspaces(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    failed_ws = workspace_mod.create(
        "First WS Fail", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _fail(failed_ws)
    delivered_ws = workspace_mod.create(
        "First WS Retry", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(delivered_ws)
    result = project_auto_continue.next_proposable_item(project.id, workspaces_root=root)
    assert result is not None
    assert result.id == items[1].id


def test_propose_next_item_proposal_item_none_for_auto_continue_project(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    _seed_roadmap(project.id, projects_root, "First")
    assert (
        project_auto_continue.propose_next_item_proposal_item(project, workspaces_root=root) is None
    )


def test_propose_next_item_proposal_item_none_when_no_candidate(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    assert (
        project_auto_continue.propose_next_item_proposal_item(project, workspaces_root=root) is None
    )


def test_propose_next_item_proposal_item_builds_direct_dispatch_card(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship the widget")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    card = project_auto_continue.propose_next_item_proposal_item(project, workspaces_root=root)
    assert card is not None
    assert card.type == "decision"
    assert card.project_id == project.id
    assert "Ship the widget" in card.title
    assert "Ship the widget body" in card.prepared_context
    assert len(card.actions) == 1
    action = card.actions[0]
    assert action.kind == "direct_dispatch"
    assert action.url == f"/api/projects/{project.id}/roadmap/advance"


def test_propose_next_item_proposal_item_none_when_budget_exhausted(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = _project(projects_root, budget_usd=1.0)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship the widget")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    _stamp_cost(prior, cost=5.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    card = project_auto_continue.propose_next_item_proposal_item(
        project, workspaces_root=root, workspace_index=index
    )

    assert card is None


def test_propose_next_item_proposal_item_builds_card_when_budget_has_headroom(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = _project(projects_root, budget_usd=100.0)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship the widget")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    _stamp_cost(prior, cost=1.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    card = project_auto_continue.propose_next_item_proposal_item(
        project, workspaces_root=root, workspace_index=index
    )

    assert card is not None


def test_propose_next_item_proposal_item_ignores_budget_when_index_not_supplied(
    root: Path, projects_root: Path
) -> None:
    project = _project(projects_root, budget_usd=1.0)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship the widget")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    _stamp_cost(prior, cost=5.0)

    card = project_auto_continue.propose_next_item_proposal_item(project, workspaces_root=root)

    assert card is not None


def test_propose_next_item_proposal_item_skips_budget_check_without_a_candidate(
    root: Path, projects_root: Path, queue: Queue
) -> None:
    project = _project(projects_root, budget_usd=1.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()

    card = project_auto_continue.propose_next_item_proposal_item(
        project, workspaces_root=root, workspace_index=index
    )

    assert card is None


def test_project_auto_continue_budget_exhausted_is_the_project_budget_function() -> None:
    from alan import project_budget

    assert project_auto_continue.budget_exhausted is project_budget.budget_exhausted


def test_advance_project_returns_none_for_unknown_project(
    root: Path, queue: Queue, pubsub: PubSub
) -> None:
    result = project_auto_continue.advance_project(
        "does-not-exist",
        workspaces_root=root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=_RecordingEnqueue(),
    )
    assert result is None


def test_advance_project_returns_none_when_no_next_item(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    result = project_auto_continue.advance_project(
        project.id,
        workspaces_root=root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=_RecordingEnqueue(),
    )
    assert result is None


def test_advance_project_seeds_workspace_from_item_text_with_no_planning(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_forge_calls(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a skipped merge-gate policy must make zero forge calls")

    monkeypatch.setattr(merge_gate_mod, "attempt_recovery", _no_forge_calls)
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Ship it")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    enqueue = _RecordingEnqueue()
    outcome = project_auto_continue.advance_project(
        project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=enqueue,
    )
    assert outcome is not None
    assert outcome.item.id == items[1].id
    ws = outcome.workspace
    assert ws.meta.project_id == project.id
    assert ws.meta.roadmap_item_id == items[1].id
    assert ws.meta.title == "Ship it"
    assert ws.meta.description == "Ship it body"
    assert len(enqueue.calls) == 1
    call = enqueue.calls[0]
    assert call["team"].name == "implementation"
    assert call["instructions"] is None
    assert outcome.dispatch.inv_id == "inv_001"
    assert outcome.dispatch.merge_gate.policy == "skipped"
    assert outcome.dispatch.merge_gate.skip_reason


def test_advance_project_stamps_engine_key_from_project(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    project = project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Only")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    outcome = project_auto_continue.advance_project(
        project.id,
        workspaces_root=root,
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=_RecordingEnqueue(),
    )
    assert outcome is not None
    assert outcome.workspace.meta.engine_key == "acme/sprocket"


def test_advance_project_only_dispatches_once_across_two_calls(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Only")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    enqueue = _RecordingEnqueue()
    first = project_auto_continue.advance_project(
        project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=enqueue,
    )
    second = project_auto_continue.advance_project(
        project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=enqueue,
    )
    assert first is not None
    assert second is None
    assert len(enqueue.calls) == 1


def test_advance_project_moves_to_next_item_on_repeat_call(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "First", "Second")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    enqueue = _RecordingEnqueue()
    first = project_auto_continue.advance_project(
        project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=enqueue,
    )
    second = project_auto_continue.advance_project(
        project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=enqueue,
    )
    assert first is not None
    assert second is not None
    assert first.item.id == items[1].id
    assert second.item.id == items[2].id
    assert len(enqueue.calls) == 2


def test_advance_project_rolls_back_seeded_workspace_when_dispatch_raises(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Only")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)

    def boom(**_kwargs: Any) -> tuple[Job, str]:
        raise RuntimeError("enqueue blew up")

    with pytest.raises(RuntimeError, match="enqueue blew up"):
        project_auto_continue.advance_project(
            project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=boom,
        )
    assert [ws.task_id for ws in workspace_mod.list_workspaces(root=root)] == [prior.task_id]


def test_advance_project_logs_when_rollback_delete_also_fails(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Only")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)

    def boom(**_kwargs: Any) -> tuple[Job, str]:
        raise RuntimeError("enqueue blew up")

    def delete_boom(*_a: object, **_kw: object) -> None:
        raise OSError("delete blew up too")

    monkeypatch.setattr(workspace_mod, "delete", delete_boom)

    with pytest.raises(RuntimeError, match="enqueue blew up"):
        project_auto_continue.advance_project(
            project.id, workspaces_root=root, queue=queue, pubsub=pubsub, enqueue_workflow=boom,
        )


def test_advance_project_concurrent_calls_start_item_exactly_once(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root)
    items = _seed_roadmap(project.id, projects_root, "Kickoff", "Only")
    prior = workspace_mod.create(
        "Kickoff WS", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    _deliver(prior)
    enqueue = _RecordingEnqueue()
    results: list[object] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(
            project_auto_continue.advance_project(
                project.id,
                workspaces_root=root,
                queue=queue,
                pubsub=pubsub,
                enqueue_workflow=enqueue,
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r is not None]
    assert len(successes) == 1
    assert len(enqueue.calls) == 1


def test_maybe_advance_project_false_when_not_tied_to_roadmap_item(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    ws = workspace_mod.create("no-item-ws", "d", root=root, project_id=project.id)
    ws = _deliver(ws)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )


def test_maybe_advance_project_false_when_not_delivered(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "undelivered-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _fail(ws)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )
    remaining = project_auto_continue.next_unattempted_item(project.id, workspaces_root=root)
    assert remaining is not None
    assert remaining.id == items[1].id


def test_maybe_advance_project_false_when_autonomy_is_propose(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="propose")
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "propose-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )


def test_maybe_advance_project_false_when_disabled(
    root: Path,
    projects_root: Path,
    queue: Queue,
    pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_AUTO_CONTINUE_DISABLE", "1")
    project = _project(projects_root, autonomy="auto_continue")
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "disabled-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )


def test_maybe_advance_project_false_when_project_archived(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "archived-project-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    project_mod.update(project, {"status": "archived"}, root=projects_root)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )


def test_maybe_advance_project_dispatches_next_item_and_records_audit(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "delivering-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    enqueue = _RecordingEnqueue()

    result = project_auto_continue.maybe_advance_project(
        ws, queue, pubsub, enqueue, workspaces_root=root
    )

    assert result is True
    assert len(enqueue.calls) == 1
    assert enqueue.calls[0]["team"].name == "implementation"

    audit_path = logs_root / project_auto_continue.PROJECT_ADVANCE_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["project_id"] == project.id
    assert row["from_task_id"] == ws.task_id
    assert row["roadmap_item_id"] == items[1].id
    assert row["team"] == "implementation"
    assert row["inv_id"] == "inv_001"


def test_maybe_advance_project_false_at_roadmap_end(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue")
    items = _seed_roadmap(project.id, projects_root, "Only")
    ws = workspace_mod.create(
        "last-item-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    assert (
        project_auto_continue.maybe_advance_project(
            ws, queue, pubsub, _RecordingEnqueue(), workspaces_root=root
        )
        is False
    )


def _stamp_cost(ws: Workspace, *, cost: float) -> None:
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


def test_maybe_advance_project_false_and_audited_when_budget_exhausted(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue", budget_usd=1.0)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "budget-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    _stamp_cost(ws, cost=5.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    enqueue = _RecordingEnqueue()

    result = project_auto_continue.maybe_advance_project(
        ws, queue, pubsub, enqueue, workspaces_root=root, workspace_index=index,
    )

    assert result is False
    assert enqueue.calls == []
    audit_path = logs_root / project_auto_continue.PROJECT_ADVANCE_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["skipped"] is True
    assert "budget" in rows[0]["reason"].lower()


def test_maybe_advance_project_dispatches_when_budget_has_headroom(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue", budget_usd=100.0)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "headroom-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    _stamp_cost(ws, cost=1.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    enqueue = _RecordingEnqueue()

    result = project_auto_continue.maybe_advance_project(
        ws, queue, pubsub, enqueue, workspaces_root=root, workspace_index=index,
    )

    assert result is True
    assert len(enqueue.calls) == 1


def test_maybe_advance_project_ignores_budget_when_index_not_supplied(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub
) -> None:
    project = _project(projects_root, autonomy="auto_continue", budget_usd=1.0)
    items = _seed_roadmap(project.id, projects_root, "First", "Second")
    ws = workspace_mod.create(
        "no-index-ws", "d", root=root, project_id=project.id, roadmap_item_id=items[0].id
    )
    ws = _deliver(ws)
    _stamp_cost(ws, cost=5.0)
    enqueue = _RecordingEnqueue()

    result = project_auto_continue.maybe_advance_project(
        ws, queue, pubsub, enqueue, workspaces_root=root,
    )

    assert result is True
    assert len(enqueue.calls) == 1
