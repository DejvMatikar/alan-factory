from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Handover
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests import _dispatcher_support_state as support_state
from tests._dispatcher_support_teams import (
    _DeployVerifyNoopTeam,
    _PrMergeEscalationTeam,
    _PrMergeTerminalTeam,
)


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


def test_proactive_rebase_triggered_after_pr_merge_terminal(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triggered: list[dict[str, object]] = []
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase",
        lambda **kw: triggered.append(kw),
    )
    ws = workspace_mod.create("trigger-test", "d", root=root)
    d = Dispatcher(queue, {"pr_merge": _PrMergeTerminalTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    assert len(triggered) == 1
    assert triggered[0]["skip_workspace_id"] == ws.workspace_id
    assert triggered[0]["triggered_by_pr"] == 99


def test_proactive_rebase_not_triggered_for_escalation(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triggered: list[dict[str, object]] = []
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase",
        lambda **kw: triggered.append(kw),
    )
    ws = workspace_mod.create("no-trigger-esc", "d", root=root)
    d = Dispatcher(queue, {"pr_merge": _PrMergeEscalationTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    assert triggered == []
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status != workspace_mod.Status.COMPLETED


def test_proactive_rebase_not_triggered_for_non_pr_merge_team(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    triggered: list[dict[str, object]] = []
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase",
        lambda **kw: triggered.append(kw),
    )
    ws = workspace_mod.create("non-pr-merge", "d", root=root)

    class _TerminalResearch:
        name = "research"
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
            inv_id: str,
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id="h_research_terminal",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="research",
                from_invocation_id=inv_id,
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="done",
            )

    d = Dispatcher(queue, {"research": _TerminalResearch()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert triggered == []


def test_proactive_rebase_spawn_exception_is_non_fatal(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_on_trigger(**_kw: object) -> None:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase",
        raise_on_trigger,
    )
    ws = workspace_mod.create("spawn-exc", "d", root=root)
    d = Dispatcher(queue, {"pr_merge": _PrMergeTerminalTeam()}, workspaces_root=root)
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status == workspace_mod.Status.COMPLETED


def test_enforce_deploy_gate_enqueues_deploy_verify_after_pr_merge_terminal(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("dg-happy", "d", root=root)
    d = Dispatcher(
        queue,
        {"pr_merge": _PrMergeTerminalTeam(), "deploy_verify": _DeployVerifyNoopTeam()},
        workspaces_root=root,
    )
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    jobs = queue.list_jobs(task_id=ws.task_id)
    dv_jobs = [j for j in jobs if j.team == "deploy_verify"]
    assert len(dv_jobs) == 1
    assert dv_jobs[0].eligible_at is not None
    assert dv_jobs[0].eligible_at > time.time()
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is not workspace_mod.Status.COMPLETED


def test_enforce_deploy_gate_passthrough_when_deploy_verify_not_in_teams(
    queue: Queue, root: Path
) -> None:
    ws = workspace_mod.create("dg-no-dv-team", "d", root=root)
    d = Dispatcher(
        queue,
        {"pr_merge": _PrMergeTerminalTeam()},
        workspaces_root=root,
    )
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    jobs = queue.list_jobs(task_id=ws.task_id)
    assert not any(j.team == "deploy_verify" for j in jobs)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED


def test_enforce_deploy_gate_passthrough_when_no_pr_number(
    queue: Queue, root: Path
) -> None:
    class _PrMergeTerminalNoPr:
        name = "pr_merge"
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
            inv_id: str,
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            return Handover(
                handover_id="h_pr_merge_no_pr",
                handover_type="terminal",
                task_id=workspace.task_id,
                from_team="pr_merge",
                from_invocation_id=inv_id,
                iteration=1,
                timestamp_utc=datetime.now(UTC),
                full_synthesis="merged (no pr_number)",
                pr_number=None,
            )

    ws = workspace_mod.create("dg-no-pr-number", "d", root=root)
    d = Dispatcher(
        queue,
        {"pr_merge": _PrMergeTerminalNoPr(), "deploy_verify": _DeployVerifyNoopTeam()},
        workspaces_root=root,
    )
    queue.enqueue(ws.task_id, "pr_merge")
    d.run_once()
    jobs = queue.list_jobs(task_id=ws.task_id)
    assert not any(j.team == "deploy_verify" for j in jobs)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.COMPLETED
