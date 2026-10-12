from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import alan._dispatcher_base as dispatcher_mod
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, Posture, RepoIdentity, register
from alan.handover import Handover
from alan.queue import Job, JobStatus, Queue
from alan.teams import TEAMS
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_state import (
    _build_dispatcher_with_pr_merge,
    _make_collector,
    _pr_merge_handover,
    _ws_with_snapshot_and_engine_key,
)
from tests._dispatcher_support_teams import _NOOP_TEAMS, _make_handover, _NoopTeam


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return support_state.make_root(tmp_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return support_state.make_db_path(tmp_path)


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    yield from support_state.make_queue(db_path)


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_summary_threads(monkeypatch)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    support_state.suppress_proactive_rebase_threads(monkeypatch)


def test_route_after_handover_completes_job_on_normal_path(
    queue: Queue, root: Path
) -> None:
    workspace_mod.create("routes-normally", "d", root=root)
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    queue.enqueue("routes-normally", "research")
    d.run_once()
    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.COMPLETED


def test_route_after_handover_tolerates_job_already_finalized(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_mod.create("already-finalized", "d", root=root)
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    queue.enqueue("already-finalized", "research")

    original = Dispatcher._apply_router_decision  # noqa: SLF001

    def sneaky_finish(
        self: Dispatcher, ws: Workspace, handover: Handover, *args: Any, **kwargs: Any
    ) -> None:
        for running in queue.list_running():
            queue.complete(running.id)
        return original(self, ws, handover, *args, **kwargs)

    monkeypatch.setattr(Dispatcher, "_apply_router_decision", sneaky_finish)

    with caplog.at_level("DEBUG", logger="alan.dispatcher"):
        d.run_once()

    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.COMPLETED
    assert any(
        "already finalized" in r.message and r.levelname == "DEBUG"
        for r in caplog.records
    )
    assert not any(
        "unexpectedly not RUNNING" in r.message for r in caplog.records
    )


def test_route_after_handover_warns_on_genuine_non_running_anomaly(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_mod.create("anomalous", "d", root=root)
    d = Dispatcher(queue, {"research": _NoopTeam()}, workspaces_root=root)
    queue.enqueue("anomalous", "research")

    original = Dispatcher._apply_router_decision  # noqa: SLF001

    def requeue_mid_routing(
        self: Dispatcher, ws: Workspace, handover: Handover, *args: Any, **kwargs: Any
    ) -> None:
        for running in queue.list_running():
            queue.requeue_pending(running.id)
        return original(self, ws, handover, *args, **kwargs)

    monkeypatch.setattr(Dispatcher, "_apply_router_decision", requeue_mid_routing)

    with caplog.at_level("DEBUG", logger="alan.dispatcher"):
        d.run_once()

    finished = queue.list_jobs()[0]
    assert finished.status is JobStatus.PENDING
    assert any(
        "unexpectedly not RUNNING" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )
    assert not any("already finalized" in r.message for r in caplog.records)


def test_dispatcher_enqueues_next_team_via_router(queue: Queue, root: Path) -> None:
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("router-flow", "d", root=root)

    class ForwardingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            h = _make_handover(workspace.task_id, from_team=self.name)
            return h.model_copy(update={"to_team_suggestions": ["implementation_plan"]})

    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["research"] = ForwardingTeam()
    d = Dispatcher(queue, teams, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    jobs = queue.list_jobs()
    assert len(jobs) == 2
    assert jobs[0].status is JobStatus.COMPLETED
    assert jobs[1].team == "implementation_plan"
    assert jobs[1].status is JobStatus.PENDING
    assert jobs[1].parent_handover_id is not None


def test_dispatcher_marks_workspace_completed_on_done(queue: Queue, root: Path) -> None:
    ws = workspace_mod.create("done-flow", "d", root=root)

    class TerminalTeam:
        name = "maintenance"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team=self.name)

    d = Dispatcher(queue, {"maintenance": TerminalTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "maintenance")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_dispatcher_marks_workspace_awaiting_input_on_blocking_human(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("await-flow", "d", root=root)

    class BlockedTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            from alan.handover import OpenQuestion

            return _make_handover(
                workspace.task_id, from_team=self.name
            ).model_copy(
                update={
                    "open_questions": [
                        OpenQuestion(
                            id="q1",
                            question="?",
                            resolver_role="human",
                            blocking=True,
                            prior_attempts=[],
                        )
                    ]
                }
            )

    d = Dispatcher(queue, {"research": BlockedTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_dispatcher_marks_workspace_escalated_on_escalation_handover(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("escalate-flow", "d", root=root)

    class EscalatingTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(
                workspace.task_id, from_team=self.name
            ).model_copy(update={"handover_type": "escalation"})

    d = Dispatcher(queue, {"research": EscalatingTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_dispatcher_router_event_includes_next_team_and_reason(
    queue: Queue, root: Path
) -> None:
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("router-event", "d", root=root)

    class ForwardTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team=self.name).model_copy(
                update={"to_team_suggestions": ["implementation_plan"]}
            )

    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["research"] = ForwardTeam()
    events, collect = _make_collector()
    d = Dispatcher(queue, teams, workspaces_root=root)
    d.pubsub.subscribe(ws.task_id, collect)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    router_events = [e for e in events if e.kind.startswith("router_")]
    assert len(router_events) == 1
    assert router_events[0].kind == "router_enqueue"
    assert router_events[0].payload["next_team"] == "implementation_plan"
    assert router_events[0].payload["from_team"] == "research"
    assert "reason" in router_events[0].payload


def test_dispatcher_increments_revisit_count_after_team_run(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("rev-count", "d", root=root)
    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    expected_key = "rev-count|rev-count|research|handover_rev-count_test"
    assert reloaded.meta.revisit_counts == {expected_key: 1}


def test_dispatcher_decays_revisit_count_on_forward_progress(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json as _json

    from alan.teams import TEAMS as REAL_TEAMS

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("decay-count", "d", root=root)
    chain_id = f"handover_{ws.task_id}_test"
    ws = workspace_mod.increment_revisit_count(ws, "verification", chain_id)
    ws = workspace_mod.increment_revisit_count(ws, "verification", chain_id)

    class _VerifTeam:
        name = "verification"
        supports_resume = False

        def plan_workflow(
            self, _workspace: Workspace, _prior: Handover | None
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team="verification").model_copy(
                update={"to_team_suggestions": ["pr_merge"]}
            )

    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["verification"] = _VerifTeam()
    d = Dispatcher(queue, teams, workspaces_root=root)
    queue.enqueue(ws.task_id, "verification")
    d.run_once()

    reloaded = workspace_mod.read(ws.task_id, root=root)
    key = f"decay-count|decay-count|verification|{chain_id}"
    assert reloaded.meta.revisit_counts.get(key) == 1

    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    records = [_json.loads(line) for line in audit_file.read_text().splitlines()]
    assert any(r.get("kind") == "chain_decay" for r in records)
    decay_record = next(r for r in records if r.get("kind") == "chain_decay")
    assert decay_record["from_team"] == "verification"
    assert decay_record["next_team"] == "pr_merge"


def test_dispatcher_loop_guard_escalates_when_revisit_cap_exceeded(
    queue: Queue, root: Path
) -> None:
    from alan.loop_guard import REVISIT_CAP
    from alan.teams import TEAMS as REAL_TEAMS
    ws = workspace_mod.create("over-cap", "d", root=root)
    chain_id = f"handover_{ws.task_id}_test"
    for _ in range(REVISIT_CAP + 1):
        ws = workspace_mod.increment_revisit_count(ws, "implementation_plan", chain_id)

    class ForwardTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self,
            _workspace: Workspace,
            _prior: Handover | None,
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *, inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team=self.name).model_copy(
                update={"to_team_suggestions": ["implementation_plan"]}
            )

    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["research"] = ForwardTeam()
    d = Dispatcher(queue, teams, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED
    assert all(j.team != "implementation_plan" for j in queue.list_jobs())


def test_handle_decision_enqueue_from_escalated_resets_to_active(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("esc-to-active", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE
    assert any(j.team == "implementation_plan" for j in queue.list_jobs())


def test_handle_decision_done_from_escalated_reaches_completed(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("esc-to-completed", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_handle_decision_from_escalated_restores_on_downstream_error(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    from alan.router import RouterDecision
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("esc-restore", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(dispatcher_mod, "enqueue_with_pending_workflow", boom)
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    with pytest.raises(RuntimeError, match="enqueue failed"):
        d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_handle_decision_from_active_does_not_escalate_on_downstream_error(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan._dispatcher_base as dispatcher_mod
    from alan.router import RouterDecision
    from alan.teams import TEAMS as REAL_TEAMS

    ws = workspace_mod.create("active-restore", "d", root=root)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(dispatcher_mod, "enqueue_with_pending_workflow", boom)
    teams: dict[str, Team] = dict(REAL_TEAMS)
    d = Dispatcher(queue, teams, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="enqueue", team="implementation_plan", reason="")
    with pytest.raises(RuntimeError, match="enqueue failed"):
        d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ACTIVE


def test_run_once_terminal_team_completes_escalated_workspace(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("esc-retro", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)

    class MaintenanceTeam:
        name = "maintenance"
        supports_resume = False

        def plan_workflow(
            self, _workspace: Workspace, _prior: Handover | None
        ) -> list[PendingTaskSpec]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return _make_handover(workspace.task_id, from_team="maintenance")

    d = Dispatcher(queue, {"maintenance": MaintenanceTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "maintenance")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_handle_decision_done_from_escalated_restores_on_advance_dag_error(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("esc-done-err", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("dag error")

    monkeypatch.setattr(d, "_advance_workspace_dag", boom)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="done", reason="done")
    with pytest.raises(RuntimeError, match="dag error"):
        d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_handle_decision_await_human_from_escalated_sets_awaiting_input(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("esc-await", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="await_human", reason="needs input")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT


def test_handle_decision_escalate_from_escalated_stays_escalated(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision

    ws = workspace_mod.create("esc-re-esc", "d", root=root)
    ws = workspace_mod.transition(ws, workspace_mod.Status.ESCALATED)
    d = Dispatcher(queue, {}, workspaces_root=root)
    handover = _make_handover(ws.task_id, from_team="research")
    decision = RouterDecision(kind="escalate", reason="still stuck")
    d._handle_decision(ws, handover, decision, ws.task_id)  # noqa: SLF001
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.ESCALATED


def test_surface_routing_error_swallows_oserror_on_status_update(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("surf-os-err", "d", root=root)
    events, collect = _make_collector()

    def raising_transition(*_a: object, **_k: object) -> Workspace:
        raise OSError("disk full")

    monkeypatch.setattr("alan.dispatcher.workspace_mod.transition", raising_transition)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, collect)
    queue.enqueue(ws.task_id, "research")
    job_obj = queue.list_jobs()[0]
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._surface_routing_error(ws, job_obj, "test error")  # noqa: SLF001
    assert any("status update failed" in r.message for r in caplog.records)
    assert any(e.kind == "job_failed" for e in events)


def test_surface_routing_error_swallows_non_oserror_on_status_update(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = workspace_mod.create("surf-non-os-err", "d", root=root)
    events, collect = _make_collector()

    def raising_transition(*_a: object, **_k: object) -> Workspace:
        raise ValueError("corrupt meta")

    monkeypatch.setattr("alan.dispatcher.workspace_mod.transition", raising_transition)
    d = Dispatcher(queue, {}, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, collect)
    queue.enqueue(ws.task_id, "research")
    job_obj = queue.list_jobs()[0]
    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._surface_routing_error(ws, job_obj, "test error")  # noqa: SLF001
    assert any("status update failed" in r.message for r in caplog.records)
    assert any(e.kind == "job_failed" for e in events)


def test_routing_error_surfaces_awaiting_input_instead_of_crashing_thread(
    queue: Queue,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("route-err", "d", root=root)
    events, collect = _make_collector()

    class _TerminalResearchTeam:
        name = "research"
        supports_resume = False

        def plan_workflow(
            self, _w: Workspace, _p: Handover | None
        ) -> list[Any]:
            return []

        def run(
            self,
            workspace: Workspace,
            _prior: Handover | None,
            _emit: Callable[[TeamEvent], None],
            *,
            inv_id: str = "inv_001",
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: Any = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id=f"term-rte-{workspace.task_id}",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="research",
                from_invocation_id=inv_id,
                to_team_suggestions=[],
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="done",
            )

    d = Dispatcher(queue, {"research": _TerminalResearchTeam()}, workspaces_root=root)
    d.pubsub.subscribe(ws.workspace_id, collect)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("dag kaboom")

    monkeypatch.setattr(d, "_advance_workspace_dag", boom)

    queue.enqueue(ws.task_id, "research")
    d.run_once()

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert any(e.kind == "job_failed" for e in events)
    assert queue.list_jobs()[0].status is JobStatus.COMPLETED
    from alan.park import ParkReason

    assert reloaded.meta.park_reason is ParkReason.ROUTING_ERROR
    assert len(reloaded.meta.blockers) == 1
    assert reloaded.meta.blockers[0].detail == "dag kaboom"


def test_replay_handover_routing_logs_warning_when_workspace_not_found(
    queue: Queue, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import shutil

    from alan.workflow import PendingTaskSpec, seed_pending_workflow

    ws = workspace_mod.create("ghost-ws", "d", root=root)
    inv_id = seed_pending_workflow(
        ws, team="research", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None

    handover = _make_handover(ws.task_id)
    shutil.rmtree(ws.path)

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    with caplog.at_level("WARNING", logger="alan.dispatcher"):
        d.replay_handover_routing(claimed, handover)

    assert any("workspace not found" in r.message for r in caplog.records)
    still_running = queue.list_jobs(task_id=ws.task_id, status=JobStatus.RUNNING)
    assert any(r.id == claimed.id for r in still_running)


def test_replay_handover_routing_calls_route_after_handover(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.workflow import PendingTaskSpec, seed_pending_workflow

    ws = workspace_mod.create("replay-route", "d", root=root)
    inv_id = seed_pending_workflow(
        ws, team="research", tasks=[PendingTaskSpec(id="w", role="w", model="sonnet")]
    )
    queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    claimed = queue.claim()
    assert claimed is not None

    handover = _make_handover(ws.task_id)
    route_calls: list[tuple[Any, Any, Any]] = []

    def fake_route(
        self: Dispatcher, job: Job, ws_arg: Any, h: Any
    ) -> None:
        route_calls.append((job, ws_arg, h))
        self._queue.complete(job.id)  # noqa: SLF001

    d = Dispatcher(queue, _NOOP_TEAMS, workspaces_root=root)
    monkeypatch.setattr(Dispatcher, "_route_after_handover", fake_route)
    d.replay_handover_routing(claimed, handover)

    assert len(route_calls) == 1
    assert route_calls[0][0].id == claimed.id
    completed = queue.list_jobs(task_id=ws.task_id, status=JobStatus.COMPLETED)
    assert any(r.id == claimed.id for r in completed)


def test_governing_engine_config_none_and_explicit_default_slug_agree() -> None:
    from_none = dispatcher_mod._governing_engine_config(None)  # noqa: SLF001
    from_slug = dispatcher_mod._governing_engine_config("DejvMatikar/alan")  # noqa: SLF001
    assert from_none.repo == from_slug.repo


def test_governing_engine_config_unregistered_slug_folds_to_default() -> None:
    default_cfg = dispatcher_mod._governing_engine_config(None)  # noqa: SLF001
    unknown_cfg = dispatcher_mod._governing_engine_config("ghost-co/ghost")  # noqa: SLF001
    assert unknown_cfg.repo == default_cfg.repo


def test_governing_engine_config_registered_engine_returns_its_own_config() -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.propose)
    )
    cfg = dispatcher_mod._governing_engine_config("acme/widget")  # noqa: SLF001
    assert cfg.repo == RepoIdentity(owner="acme", repo="widget")
    assert cfg.posture == Posture.propose


def test_effective_teams_downgrade_floors_pr_merge_out_of_admitted_set(
    queue: Queue, root: Path
) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.observe)
    )
    ws = _ws_with_snapshot_and_engine_key(
        root, snapshot_posture=Posture.self_merge, engine_key="acme/widget"
    )
    d = Dispatcher(queue=queue, teams=dict(TEAMS), workspaces_root=root)
    effective = d._effective_teams(ws)  # noqa: SLF001
    assert "pr_merge" not in effective
    assert "verification" not in effective


def test_effective_teams_upgrade_holds_pr_merge_absent_until_restamp(
    queue: Queue, root: Path
) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.autonomous)
    )
    ws = _ws_with_snapshot_and_engine_key(
        root, snapshot_posture=Posture.observe, engine_key="acme/widget"
    )
    d = Dispatcher(queue=queue, teams=dict(TEAMS), workspaces_root=root)
    effective = d._effective_teams(ws)  # noqa: SLF001
    assert not effective


def test_effective_teams_no_snapshot_regression_uses_live_config_directly(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("no-snapshot", "d", root=root)
    assert ws.meta.engine_snapshot is None
    d = Dispatcher(queue=queue, teams=dict(TEAMS), workspaces_root=root)
    effective = d._effective_teams(ws)  # noqa: SLF001
    assert set(effective) == set(TEAMS)


class _NamedNoopTeam(_NoopTeam):
    def __init__(self, name: str) -> None:
        self.name = name


def test_advance_or_enqueue_auto_routed_run_carries_the_sticky_instruction(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.workspace import set_sticky_instructions

    ws = workspace_mod.create("sticky-carry", "d", root=root)
    ws = set_sticky_instructions(ws, ws.task_id, "sticky text", origin="dispatch")
    d = Dispatcher(
        queue, {"implementation_fix": _NamedNoopTeam("implementation_fix")}, workspaces_root=root
    )
    handover = _make_handover(ws.task_id, from_team="verification")
    decision = RouterDecision(kind="enqueue", team="implementation_fix", reason="")
    d._advance_or_enqueue(ws, handover, decision, ws.task_id)  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "implementation_fix"]
    assert len(jobs) == 1
    assert jobs[0].instructions == "sticky text"
    assert jobs[0].instructions_origin == "inherited"


def test_advance_or_enqueue_deploy_verify_gate_enqueue_does_not_carry_the_sticky(
    queue: Queue, root: Path
) -> None:
    from alan.router import RouterDecision
    from alan.workspace import set_sticky_instructions

    ws = workspace_mod.create("sticky-non-consuming", "d", root=root)
    ws = set_sticky_instructions(ws, ws.task_id, "sticky text", origin="dispatch")
    d = Dispatcher(
        queue, {"deploy_verify": _NamedNoopTeam("deploy_verify")}, workspaces_root=root
    )
    handover = _make_handover(ws.task_id, from_team="pr_merge")
    decision = RouterDecision(
        kind="enqueue",
        team="deploy_verify",
        reason="",
        instructions='{"path": "awaiting_restart"}',
    )
    d._advance_or_enqueue(ws, handover, decision, ws.task_id)  # noqa: SLF001
    jobs = [j for j in queue.list_jobs() if j.team == "deploy_verify"]
    assert len(jobs) == 1
    assert jobs[0].instructions == '{"path": "awaiting_restart"}'
    assert jobs[0].instructions_origin is None


def test_downgrade_floors_pr_merge_out_of_next_route_admitted_set(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"), posture=Posture.observe)
    )
    ws = _ws_with_snapshot_and_engine_key(
        root, snapshot_posture=Posture.self_merge, engine_key="acme/widget"
    )
    d, recording = _build_dispatcher_with_pr_merge(queue, root)
    d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001
    assert recording.called == 0
    enqueued_teams = [j.team for j in queue.list_jobs()]
    assert "pr_merge" not in enqueued_teams
