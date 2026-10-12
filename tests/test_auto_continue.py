from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import auto_continue
from alan import merge_gate as merge_gate_mod
from alan import project as project_mod
from alan import workspace as workspace_mod
from alan.auto_continue import (
    _has_blocking_question,  # pyright: ignore[reportPrivateUsage]
    _resolve_auto_continue_target,  # pyright: ignore[reportPrivateUsage]
)
from alan.dispatch import (
    _WAKEABLE_STATUSES,  # pyright: ignore[reportPrivateUsage]
    DispatchOutcome,
)
from alan.events import PubSub
from alan.handover import (
    Decision,
    Handover,
    OpenQuestion,
    list_handovers,
    record_answer,
    save_handover,
)
from alan.incident import STUCK_WS_STATUSES
from alan.park import ParkReason
from alan.queue import Job, JobStatus, Queue
from alan.teams import AUTO_CONTINUE_TEAMS, MANUALLY_DISPATCHABLE_TEAMS
from alan.workflow import WorkflowDoc, WorkflowTask, save_workflow
from alan.workspace import Status
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


def _handover(
    task_id: str,
    *,
    to_team_suggestions: list[str] | None = None,
    open_questions: list[OpenQuestion] | None = None,
) -> Handover:
    return Handover(
        handover_id="h1",
        handover_chain_id="h1",
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="synthesis",
        open_questions=open_questions or [],
        decisions=[
            Decision(
                id="d1", statement="proceed", rationale="stub", confidence=0.9,
                reversibility="medium",
            )
        ],
    )


class _RecordingEnqueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> tuple[Job, str]:
        self.calls.append(kwargs)
        job = Job(
            id=1,
            task_id=kwargs["task_id"],
            team=kwargs["team"].name,
            status=JobStatus.PENDING,
            enqueued_at=datetime.now(UTC),
        )
        return job, "inv_001"


def _auto_continue_project(root: Path | None = None) -> project_mod.Project:
    project = project_mod.create("Auto Continue Project", root=root)
    return project_mod.update(project, {"autonomy": "auto_continue"}, root=root)


def test_auto_continue_teams_is_subset_of_manually_dispatchable() -> None:
    assert AUTO_CONTINUE_TEAMS <= MANUALLY_DISPATCHABLE_TEAMS


def test_wakeable_statuses_covers_every_stuck_status() -> None:
    assert STUCK_WS_STATUSES <= _WAKEABLE_STATUSES


def test_auto_continue_disabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_AUTO_CONTINUE_DISABLE", raising=False)
    assert auto_continue.auto_continue_disabled() is False
    monkeypatch.setenv("ALAN_AUTO_CONTINUE_DISABLE", "1")
    assert auto_continue.auto_continue_disabled() is True
    monkeypatch.setenv("ALAN_AUTO_CONTINUE_DISABLE", "false")
    assert auto_continue.auto_continue_disabled() is False


def test_maybe_auto_continue_false_when_disabled(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_AUTO_CONTINUE_DISABLE", "1")
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("disabled-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_without_project(
    root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    ws = workspace_mod.create("no-project-ws", "d", root=root)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_project_autonomy_is_propose(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = project_mod.create("Propose Project", root=projects_root)
    ws = workspace_mod.create("propose-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_for_unknown_project(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    _ = projects_root
    ws = workspace_mod.create("bad-project-ws", "d", root=root, project_id="does-not-exist")
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_no_incident(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("active-ws", "d", root=root, project_id=project.id)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_escalated_decision_has_blocking_question(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("decision-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    save_handover(
        ws,
        _handover(
            ws.task_id,
            to_team_suggestions=["implementation"],
            open_questions=[
                OpenQuestion(
                    id="q1", question="Proceed?", resolver_role="human", blocking=True
                )
            ],
        ),
    )
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_escalated_without_any_handover(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("escalated-no-handover-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_team_not_in_allowlist(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("non-allowlisted-team-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["architecture"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_false_when_no_resolvable_team(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("no-team-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=[]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_maybe_auto_continue_dispatches_and_records_audit(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("auto-continued-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    result = auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue)

    assert result is True
    assert len(enqueue.calls) == 1
    call = enqueue.calls[0]
    assert call["team"].name == "implementation"
    assert call["task_id"] == ws.task_id
    assert call["workspace"].meta.status == Status.ACTIVE
    assert call["instructions"] is not None
    assert "implementation" in call["instructions"]
    assert call["instructions_origin"] == "generated"

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.dispatch_instructions[ws.task_id].origin == "generated"

    audit_path = logs_root / auto_continue.AUTO_CONTINUE_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == ws.task_id
    assert row["workspace_id"] == ws.task_id
    assert row["project_id"] == project.id
    assert row["team"] == "implementation"
    assert row["incident_class"] == "park_crash"
    assert row["inv_id"] == "inv_001"


def test_no_double_dispatch_on_repeat_transition(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    _ = logs_root
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("repeat-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is True
    assert len(enqueue.calls) == 1

    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status == Status.ACTIVE
    assert auto_continue.maybe_auto_continue(ws_after, queue, pubsub, enqueue) is False
    assert len(enqueue.calls) == 1


def test_no_double_dispatch_on_repeat_transition_partial_status(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    _ = logs_root
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("repeat-partial-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.PARTIAL)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is True
    assert len(enqueue.calls) == 1

    ws_after = workspace_mod.read(ws.task_id, root=root)
    assert ws_after.meta.status == Status.ACTIVE
    assert auto_continue.maybe_auto_continue(ws_after, queue, pubsub, enqueue) is False
    assert len(enqueue.calls) == 1


def test_maybe_auto_continue_prefers_next_hop_over_suggestions(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("next-hop-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    handover = _handover(ws.task_id, to_team_suggestions=["verification"]).model_copy(
        update={"next_hop": "pr_merge"}
    )
    save_handover(ws, handover)
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is True
    assert enqueue.calls[0]["team"].name == "pr_merge"


def test_maybe_auto_continue_resolves_root_task_id_for_dag_workspace(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create(
        "dag-ws", "d", root=root, project_id=project.id,
        tasks=[
            workspace_mod.Task(
                id="alpha",
                team="implementation",
                status=workspace_mod.TaskStatus.READY,
                depends_on=[],
            ),
        ],
    )
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is True
    assert enqueue.calls[0]["task_id"] == "alpha"


def test_dispatch_outcome_returned_by_dispatch_team_core_feeds_audit_inv_id(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = logs_root
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("outcome-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))

    def _fixed_outcome(*, merge_gate: Any, **_kwargs: Any) -> DispatchOutcome:
        return DispatchOutcome(
            job=Job(
                id=42, task_id=ws.task_id, team="implementation",
                status=JobStatus.PENDING, enqueued_at=datetime.now(UTC),
            ),
            inv_id="inv_042",
            merge_gate=merge_gate,
        )

    monkeypatch.setattr(auto_continue, "dispatch_team_core", _fixed_outcome)
    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, _RecordingEnqueue()) is True

    audit_path = logs_root / auto_continue.AUTO_CONTINUE_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["inv_id"] == "inv_042"


def test_maybe_auto_continue_declares_a_skipped_merge_gate_policy(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("skip-policy-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    captured: dict[str, Any] = {}

    def _no_forge_calls(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a skipped merge-gate policy must make zero forge calls")

    monkeypatch.setattr(merge_gate_mod, "attempt_recovery", _no_forge_calls)

    def _capture(**kwargs: Any) -> DispatchOutcome:
        captured.update(kwargs)
        job = Job(
            id=1, task_id=kwargs["task_id"], team=kwargs["team"].name,
            status=JobStatus.PENDING, enqueued_at=datetime.now(UTC),
        )
        return DispatchOutcome(job=job, inv_id="inv_skip", merge_gate=kwargs["merge_gate"])

    monkeypatch.setattr(auto_continue, "dispatch_team_core", _capture)

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, _RecordingEnqueue()) is True

    decision = captured["merge_gate"]
    assert decision.policy == "skipped"
    assert decision.attempt is None
    assert decision.skip_reason


def test_budget_exhausted_delegates_to_project_budget_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str | None, object]] = []

    def fake_budget_exhausted(project_id: str | None, workspace_index: object) -> bool:
        calls.append((project_id, workspace_index))
        return True

    monkeypatch.setattr(auto_continue, "project_budget_exhausted", fake_budget_exhausted)

    result = auto_continue._budget_exhausted("proj-1", "sentinel-index")  # type: ignore[arg-type]

    assert result is True
    assert calls == [("proj-1", "sentinel-index")]


def _stamp_cost(ws: workspace_mod.Workspace, *, cost: float, inv_id: str = "inv_001") -> None:
    doc = WorkflowDoc(
        id=inv_id,
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


def test_maybe_auto_continue_false_when_budget_exhausted(
    root: Path, projects_root: Path, logs_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    project = project_mod.update(project, {"budget_usd": 1.0}, root=projects_root)
    ws = workspace_mod.create("budget-exhausted-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    _stamp_cost(ws, cost=5.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    enqueue = _RecordingEnqueue()

    result = auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue, workspace_index=index)

    assert result is False
    assert enqueue.calls == []
    audit_path = logs_root / auto_continue.AUTO_CONTINUE_AUDIT_FILE
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == ws.task_id
    assert row["project_id"] == project.id
    assert row["skipped"] is True
    assert "budget" in row["reason"].lower()


def test_maybe_auto_continue_dispatches_when_budget_has_headroom(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    project = project_mod.update(project, {"budget_usd": 10.0}, root=projects_root)
    ws = workspace_mod.create("budget-headroom-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    _stamp_cost(ws, cost=1.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    enqueue = _RecordingEnqueue()

    result = auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue, workspace_index=index)

    assert result is True
    assert len(enqueue.calls) == 1


def test_maybe_auto_continue_dispatches_when_project_has_no_budget_set(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("no-budget-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    _stamp_cost(ws, cost=1000.0)
    index = WorkspaceIndex(root=root, queue=queue)
    index.rebuild()
    enqueue = _RecordingEnqueue()

    result = auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue, workspace_index=index)

    assert result is True
    assert len(enqueue.calls) == 1


def test_maybe_auto_continue_ignores_budget_when_workspace_index_not_supplied(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    project = _auto_continue_project(projects_root)
    project = project_mod.update(project, {"budget_usd": 1.0}, root=projects_root)
    ws = workspace_mod.create("no-index-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    _stamp_cost(ws, cost=5.0)
    enqueue = _RecordingEnqueue()

    result = auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue)

    assert result is True
    assert len(enqueue.calls) == 1


def test_has_blocking_question_flips_after_record_answer_clears_last_blocking_question(
    root: Path, projects_root: Path,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("interlock-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    question = OpenQuestion(
        id="q1", question="Proceed?", resolver_role="human", blocking=True,
        proposed_answer_space=["yes", "no"], question_class="fix_escalation",
    )
    handover = _handover(
        ws.task_id, to_team_suggestions=["implementation"], open_questions=[question]
    )
    save_handover(ws, handover)

    handovers_before = list_handovers(ws)
    latest_before = handovers_before[-1]
    assert _has_blocking_question(latest_before) is True
    assert _resolve_auto_continue_target(ws, handovers_before) is None

    record_answer(ws, handover, "q1", "yes", from_team=handover.from_team)

    handovers_after = list_handovers(ws)
    latest_after = handovers_after[-1]
    assert _has_blocking_question(latest_after) is False


def test_has_blocking_question_stays_true_when_other_blocking_question_unanswered(
    root: Path, projects_root: Path,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("interlock-multi-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.ESCALATED)
    q1 = OpenQuestion(
        id="q1", question="a?", resolver_role="human", blocking=True,
        proposed_answer_space=["x", "y"], question_class="fix_escalation",
    )
    q2 = OpenQuestion(
        id="q2", question="b?", resolver_role="human", blocking=True,
        proposed_answer_space=["x", "y"], question_class="fix_escalation",
    )
    handover = _handover(
        ws.task_id, to_team_suggestions=["implementation"], open_questions=[q1, q2]
    )
    save_handover(ws, handover)

    record_answer(ws, handover, "q1", "x", from_team=handover.from_team)

    handovers = list_handovers(ws)
    latest = handovers[-1]
    assert _has_blocking_question(latest) is True
    assert _resolve_auto_continue_target(ws, handovers) is None


def test_resolve_auto_continue_target_admits_workspace_once_blocking_question_answered(
    root: Path, projects_root: Path,
) -> None:
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create("interlock-admits-ws", "d", root=root, project_id=project.id)
    ws = workspace_mod.transition(ws, Status.ESCALATED)

    unanswered = OpenQuestion(
        id="q1", question="Proceed?", resolver_role="human", blocking=True,
        proposed_answer_space=["yes", "no"], question_class="fix_escalation",
    )
    handover1 = _handover(
        ws.task_id, to_team_suggestions=["implementation"], open_questions=[unanswered]
    )
    save_handover(ws, handover1)
    handovers = list_handovers(ws)
    assert _resolve_auto_continue_target(ws, handovers) is None

    answered = unanswered.model_copy(update={"answer": "yes"})
    handover2 = handover1.model_copy(
        update={"handover_id": "h2", "iteration": 2, "open_questions": [answered]}
    )
    save_handover(ws, handover2)
    handovers = list_handovers(ws)
    target = _resolve_auto_continue_target(ws, handovers)

    assert target is not None
    _incident, team_name = target
    assert team_name == "implementation"


def test_resolve_auto_continue_target_off_roster_team_returns_none(
    root: Path, projects_root: Path,
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity, register

    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"verification": []},
        )
    )
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create(
        "off-roster-ws", "d", root=root, project_id=project.id, engine_key="acme/sprocket",
    )
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    handovers = list_handovers(ws)

    assert _resolve_auto_continue_target(ws, handovers) is None


def test_maybe_auto_continue_false_when_team_off_roster(
    root: Path, projects_root: Path, queue: Queue, pubsub: PubSub,
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity, register

    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"verification": []},
        )
    )
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create(
        "off-roster-e2e-ws", "d", root=root, project_id=project.id, engine_key="acme/sprocket",
    )
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    enqueue = _RecordingEnqueue()

    assert auto_continue.maybe_auto_continue(ws, queue, pubsub, enqueue) is False
    assert enqueue.calls == []


def test_resolve_auto_continue_target_pinned_snapshot_admits_team_excluded_from_live_graph(
    root: Path, projects_root: Path,
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity, register
    from alan.workspace import EngineSnapshot, stamp_engine_snapshot

    register(
        EngineConfig(
            repo=RepoIdentity(owner="acme", repo="sprocket"),
            posture=Posture.autonomous,
            transition_graph={"verification": []},
        )
    )
    project = _auto_continue_project(projects_root)
    ws = workspace_mod.create(
        "pinned-admit-ws", "d", root=root, project_id=project.id, engine_key="acme/sprocket",
    )
    snapshot = EngineSnapshot(
        posture=Posture.autonomous,
        transition_graph={"implementation": ["verification"], "verification": []},
    )
    ws = stamp_engine_snapshot(ws, snapshot)
    ws = workspace_mod.transition(ws, Status.AWAITING_INPUT, park_reason=ParkReason.CRASH)
    save_handover(ws, _handover(ws.task_id, to_team_suggestions=["implementation"]))
    handovers = list_handovers(ws)

    target = _resolve_auto_continue_target(ws, handovers)

    assert target is not None
    _incident, team_name = target
    assert team_name == "implementation"
