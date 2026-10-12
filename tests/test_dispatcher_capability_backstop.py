from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher
from alan.handover import Decision, Handover, OpenQuestion
from alan.queue import JobStatus, Queue
from alan.teams import TEAMS as REAL_TEAMS
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, revisit_key

_CHAIN = "handover_repeat-verifier_chain"
_FINDING_TEXT = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
    "The paginator drops the last page when total items are an exact "
    "multiple of the page size.\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    "- path: pyproject.toml\n  line: 289\n  body: finding detail\n"
    "```\n"
)
_MOVING_ANCHOR_TEXT_1 = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
    "The copilot resolution helper silently drops the second commit hash when squashing a "
    "review-thread reply, producing an incomplete PR description on merge.\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    "- path: alan/teams/_copilot_resolution.py\n  line: 147\n  body: finding detail\n"
    "```\n"
)
_MOVING_ANCHOR_TEXT_2 = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
    "The implementation_fix worker reuses a stale judge review cache key across distinct "
    "handover chains, causing a fix meant for one workspace to be applied to another.\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    "- path: alan/teams/implementation_fix.py\n  line: 428\n  body: finding detail\n"
    "```\n"
)
_VERIF_DECISION = Decision(
    id="verif-decision",
    statement="requesting changes",
    rationale="finding present",
    confidence=1.0,
    reversibility="low",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase", lambda **_kw: None
    )


def _verif_handover(
    task_id: str, *, handover_id: str, inv_id: str, synthesis: str = _FINDING_TEXT
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=_CHAIN,
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id=inv_id,
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        decisions=[_VERIF_DECISION],
    )


def _capability_boundary_handover(task_id: str, *, handover_id: str, inv_id: str) -> Handover:
    oq = OpenQuestion(
        id=f"verif-capability-boundary-{inv_id}",
        question=(
            "Verifier declared this finding unresolvable by any fleet role: "
            "needs a human GitHub org-admin action."
        ),
        resolver_role="human",
        blocking=True,
        question_class="verification_escalation",
    )
    return Handover(
        handover_id=handover_id,
        handover_chain_id=_CHAIN,
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id=inv_id,
        to_team_suggestions=[],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_FINDING_TEXT,
        open_questions=[oq],
    )


def _impl_fix_handover(task_id: str, *, handover_id: str, inv_id: str) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=_CHAIN,
        handover_type="forward",
        task_id=task_id,
        from_team="implementation_fix",
        from_invocation_id=inv_id,
        to_team_suggestions=["verification"],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="implementation_fix committed a review-thread reply",
        decisions=[_VERIF_DECISION],
    )


class _RepeatingVerificationTeam:
    name = "verification"
    supports_resume = False

    def __init__(self) -> None:
        self.calls = 0

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
        inv_id: str = "inv_verif",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.calls += 1
        return _verif_handover(
            workspace.task_id, handover_id=f"h_v{self.calls}", inv_id=f"inv_v{self.calls}"
        )


class _MovingAnchorVerificationTeam:
    name = "verification"
    supports_resume = False

    def __init__(self) -> None:
        self.calls = 0

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
        inv_id: str = "inv_verif",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.calls += 1
        synthesis = _MOVING_ANCHOR_TEXT_1 if self.calls == 1 else _MOVING_ANCHOR_TEXT_2
        return _verif_handover(
            workspace.task_id,
            handover_id=f"h_v{self.calls}",
            inv_id=f"inv_v{self.calls}",
            synthesis=synthesis,
        )


class _CapabilityBoundaryThenRepeatVerificationTeam:
    name = "verification"
    supports_resume = False

    def __init__(self) -> None:
        self.calls = 0

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
        inv_id: str = "inv_verif",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.calls += 1
        if self.calls == 1:
            return _verif_handover(workspace.task_id, handover_id="h_v1", inv_id="inv_v1")
        return _capability_boundary_handover(
            workspace.task_id, handover_id="h_v2", inv_id="inv_v2"
        )


class _CommittingImplFixTeam:
    name = "implementation_fix"
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
        inv_id: str = "inv_fix",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _impl_fix_handover(workspace.task_id, handover_id="h_f1", inv_id="inv_f1")


def _build_dispatcher(
    queue: Queue, root: Path, verif_team: Team, fix_team: Team
) -> Dispatcher:
    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["verification"] = verif_team
    teams["implementation_fix"] = fix_team
    return Dispatcher(queue, teams, workspaces_root=root)


def _spiral_audit_records(tmp_path: Path) -> list[dict[str, object]]:
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    if not audit_file.exists():
        return []
    return [json.loads(line) for line in audit_file.read_text().splitlines()]


def test_verifier_repeating_one_anchor_escalates_after_two_real_legs(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("repeat-verifier", "d", root=root)
    verif_team = _RepeatingVerificationTeam()
    fix_team = _CommittingImplFixTeam()
    d = _build_dispatcher(queue, root, verif_team, fix_team)
    queue.enqueue(ws.task_id, "verification")

    assert d.run_once() is True
    assert d.run_once() is True

    key = revisit_key(ws.workspace_id, ws.task_id, "implementation_fix", _CHAIN)
    mid = workspace_mod.read(ws.task_id, root=root)
    assert mid.meta.revisit_counts.get(key) == 1
    assert mid.meta.status is workspace_mod.Status.ACTIVE

    assert d.run_once() is True

    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.status is workspace_mod.Status.ESCALATED
    assert verif_team.calls == 2

    pending = [j for j in queue.list_jobs() if j.status == JobStatus.PENDING]
    assert pending == []
    fix_jobs = [j for j in queue.list_jobs() if j.team == "implementation_fix"]
    assert len(fix_jobs) == 1

    records = _spiral_audit_records(tmp_path)
    spiral_records = [r for r in records if r.get("kind") == "verifier_repeating_unresolved"]
    assert len(spiral_records) == 1
    assert spiral_records[0]["task_id"] == ws.task_id


def test_marker_short_circuits_to_human_not_the_backstop(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("repeat-verifier-declared", "d", root=root)
    verif_team = _CapabilityBoundaryThenRepeatVerificationTeam()
    fix_team = _CommittingImplFixTeam()
    d = _build_dispatcher(queue, root, verif_team, fix_team)
    queue.enqueue(ws.task_id, "verification")

    assert d.run_once() is True
    assert d.run_once() is True

    key = revisit_key(ws.workspace_id, ws.task_id, "implementation_fix", _CHAIN)
    mid = workspace_mod.read(ws.task_id, root=root)
    assert mid.meta.revisit_counts.get(key) == 1

    assert d.run_once() is True

    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.status is workspace_mod.Status.AWAITING_INPUT
    assert verif_team.calls == 2

    pending = [j for j in queue.list_jobs() if j.status == JobStatus.PENDING]
    assert pending == []
    fix_jobs = [j for j in queue.list_jobs() if j.team == "implementation_fix"]
    assert len(fix_jobs) == 1

    records = _spiral_audit_records(tmp_path)
    assert not any(r.get("kind") == "verifier_repeating_unresolved" for r in records)


def test_verifier_moving_its_critical_to_another_file_keeps_the_fix_loop_running(
    queue: Queue, root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = workspace_mod.create("repeat-verifier-moving-anchor", "d", root=root)
    verif_team = _MovingAnchorVerificationTeam()
    fix_team = _CommittingImplFixTeam()
    d = _build_dispatcher(queue, root, verif_team, fix_team)
    queue.enqueue(ws.task_id, "verification")

    assert d.run_once() is True
    assert d.run_once() is True
    assert d.run_once() is True

    final = workspace_mod.read(ws.task_id, root=root)
    assert final.meta.status is workspace_mod.Status.ACTIVE
    assert verif_team.calls == 2

    fix_jobs = [j for j in queue.list_jobs() if j.team == "implementation_fix"]
    assert len(fix_jobs) == 2
    pending_fix_jobs = [j for j in fix_jobs if j.status == JobStatus.PENDING]
    assert len(pending_fix_jobs) == 1

    records = _spiral_audit_records(tmp_path)
    assert not any(r.get("kind") == "verifier_repeating_unresolved" for r in records)
    assert not any(r.get("kind") == "cap_exhausted_no_spiral" for r in records)
