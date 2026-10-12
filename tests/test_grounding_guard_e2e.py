from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.claude_cli import AgentStreamingResult, OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Decision, Handover
from alan.park import ParkReason
from alan.queue import Queue
from alan.teams import _base as base_mod
from alan.teams._base import SingleAgentTeam, TeamEvent, UngroundedRunError, WorkerOverrides
from alan.teams.implementation_fix import TEAM as IMPL_FIX_TEAM
from alan.teams.verification import TEAM as VERIFICATION_TEAM
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace
from tests._helpers import seed_and_run


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _prior_handover(workspace: Workspace, pr_number: int | None) -> Handover:
    return Handover(
        handover_id=f"handover_{workspace.task_id}_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation",
        from_invocation_id="inv_001",
        to_team_suggestions=["verification"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="impl-01", statement="abc1234 feat: a", rationale="see synthesis",
                confidence=0.8, reversibility="medium",
            )
        ],
        full_synthesis="implementation done",
        pr_number=pr_number,
    )


def test_verification_pr_number_none_spawns_no_worker_and_escalates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("verification must not spawn a claude worker without a resolvable PR")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)

    ws = workspace_mod.create("guard-e2e-verif", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws, pr_number=None)
    handover = seed_and_run(VERIFICATION_TEAM, ws, prior, lambda _e: None)

    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "verification_audit.jsonl").read_text().splitlines()
    ]
    precondition_entries = [e for e in entries if e["action"] == "pr_precondition_failed"]
    assert len(precondition_entries) == 1
    assert precondition_entries[0]["pr_number"] is None


def test_implementation_fix_no_prior_pr_yields_exactly_one_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        "alan.teams.implementation_fix._resolve_existing_branch", lambda *_a, **_k: None
    )

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("implementation_fix must not spawn a claude worker without a resolvable PR")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)

    ws = workspace_mod.create("guard-e2e-fix", "iterate on the open PR", root=tmp_path / "ws")
    prior = _prior_handover(ws, pr_number=None)
    handover = seed_and_run(IMPL_FIX_TEAM, ws, prior, lambda _e: None)

    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []

    grounding_audit = tmp_path / "logs" / "grounding_audit.jsonl"
    assert not grounding_audit.exists()


def _make_unreachable_team(*, supports_resume: bool) -> type:
    class _UnreachableTeam:
        name = "research"

        def __init__(self) -> None:
            self.supports_resume = supports_resume

        def plan_workflow(
            self, _workspace: Workspace, _prior: Handover | None
        ) -> list[PendingTaskSpec]:
            return [PendingTaskSpec(id="research", role="research", model="sonnet")]

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
            raise AssertionError("run() must not be invoked — the guard trips before dispatch")

    return _UnreachableTeam


def _trip_seam_guard(ws: Workspace, inv_id: str, tmp_path: Path) -> UngroundedRunError:
    ungrounded = tmp_path / "not-a-repo-checkout"
    ungrounded.mkdir(exist_ok=True)
    with pytest.raises(UngroundedRunError) as excinfo:
        base_mod._require_grounded_cwd(  # noqa: SLF001
            WorkerOverrides(cwd=ungrounded),
            task_id=ws.task_id, inv_id=inv_id,
            team="research", role="research", node_id="research",
        )
    return excinfo.value


def _grounding_audit_entries(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "logs" / "grounding_audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_seam_guard_trip_on_empty_tasks_workspace_fails_workspace(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("guard-empty-tasks", "d", root=root)
    assert ws.meta.tasks == []
    UnreachableTeam = _make_unreachable_team(supports_resume=True)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(ws.task_id, "research", triggered_by_invocation_id=inv_id)
    queue.claim()
    d = Dispatcher(queue, {"research": UnreachableTeam()}, workspaces_root=root)

    exc = _trip_seam_guard(ws, inv_id, tmp_path)
    d._fail_genuine(job, ws, exc, inv_id, UnreachableTeam())  # noqa: SLF001

    finished = queue.list_jobs()[0]
    assert finished.resume_inv_id == inv_id
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.CRASH

    entries = _grounding_audit_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "ungrounded_cwd_blocked"


def _write_multi_task_meta(ws: Workspace, tasks: list[dict[str, Any]]) -> Workspace:
    meta_path = ws.path / "meta.json"
    data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = tasks
    meta_path.write_text(json.dumps(data), encoding="utf-8")
    return workspace_mod.read(ws.task_id, root=ws.path.parent.parent.parent)


def test_seam_guard_trip_on_dag_subtask_fails_recoverable_subtask(
    queue: Queue, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("guard-dag-subtask", "d", root=root)
    ws = _write_multi_task_meta(
        ws, [{"id": "step-a", "effort": "medium", "status": "running", "depends_on": []}]
    )
    assert ws.meta.tasks
    UnreachableTeam = _make_unreachable_team(supports_resume=True)
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="research",
        tasks=[PendingTaskSpec(id="research", role="research", model="sonnet")],
    )
    job = queue.enqueue(
        "step-a", "research", workspace_id=ws.workspace_id, triggered_by_invocation_id=inv_id
    )
    queue.claim()
    d = Dispatcher(queue, {"research": UnreachableTeam()}, workspaces_root=root)

    exc = _trip_seam_guard(ws, inv_id, tmp_path)
    d._fail_genuine(job, ws, exc, inv_id, UnreachableTeam())  # noqa: SLF001

    finished = queue.list_jobs()[0]
    assert finished.resume_inv_id == inv_id
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.CRASH
    assert len(reloaded.meta.blockers) == 1

    entries = _grounding_audit_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "ungrounded_cwd_blocked"


def test_cwd_none_team_leaves_grounding_audit_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        lambda *_a, **_kw: AgentStreamingResult(result_text="ok"),
    )
    ws = workspace_mod.create("guard-unaffected", "d", root=tmp_path / "ws")
    team = SingleAgentTeam(
        name="research", system_prompt="you are research", to_team_suggestions=()
    )
    seed_and_run(team, ws, None, lambda _e: None)

    assert not (tmp_path / "logs" / "grounding_audit.jsonl").exists()
