from __future__ import annotations

import json
import weakref
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, PubSub
from alan.handover import (
    ArtifactRef,
    Decision,
    Handover,
    OpenQuestion,
    handover_paths,
    load_handover,
    save_handover,
)
from alan.loop_guard import SpiralFinding
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create, increment_revisit_count


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


def _make_verif_handover(
    workspace: Workspace,
    *,
    synthesis: str,
    chain_id: str,
    handover_id: str,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
    )


_NO_COMMITS_SYNTHESIS = (
    "implementation_fix produced no commits — escalating. Worker synthesis:\nnone"
)


def _make_impl_fix_handover(
    workspace: Workspace,
    *,
    chain_id: str,
    handover_id: str,
    open_questions: list[OpenQuestion] | None = None,
    synthesis: str = _NO_COMMITS_SYNTHESIS,
    decisions: list[Decision] | None = None,
    artifacts_produced: list[ArtifactRef] | None = None,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_002",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        open_questions=open_questions or [],
        decisions=decisions or [],
        artifacts_produced=artifacts_produced or [],
    )


class _FakeVerificationTeam:
    name = "verification"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise AssertionError("should not be called in spiral tests")


class _FakeImplFixTeam:
    name = "implementation_fix"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        _ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str,  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        raise AssertionError("should not be called in spiral tests")


def _build_dispatcher(db_path: Path, ws_root: Path) -> Dispatcher:
    queue = Queue(db_path=db_path)
    teams: dict[str, Team] = {
        "verification": _FakeVerificationTeam(),
        "implementation_fix": _FakeImplFixTeam(),
    }
    dispatcher = Dispatcher(
        queue=queue,
        teams=teams,
        pubsub=PubSub(),
        workspaces_root=ws_root,
    )
    weakref.finalize(dispatcher, queue.close)
    return dispatcher


def _enforce(
    dispatcher: Dispatcher,
    ws: Workspace,
    handover: Handover,
    next_team: str,
) -> RouterDecision:
    enqueue_decision = RouterDecision(kind="enqueue", team=next_team)
    return dispatcher._enforce_loop_guard(ws, handover, enqueue_decision)  # noqa: SLF001


def test_enforce_loop_guard_escalates_on_spiral(
    ws_root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-spiral", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 0\nno known markers",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    decision = _enforce(dispatcher, ws, impl_fix_h, "verification")
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert decision.reason.startswith("spiral:")
    assert "unclassified" in decision.reason


def test_enforce_loop_guard_escalates_scenarios_missing(
    ws_root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-spiral-sm", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="SCENARIOS_MISSING\nDIFF_FINDINGS_CRITICAL: 0",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    decision = _enforce(dispatcher, ws, impl_fix_h, "verification")
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert "spiral:scenarios_missing:" in decision.reason


def test_enforce_loop_guard_passes_when_no_spiral(
    ws_root: Path, db_path: Path
) -> None:
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-no-spiral", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    decision = _enforce(dispatcher, ws, impl_fix_h, "verification")
    assert decision.kind == "enqueue"
    assert decision.team == "verification"


def test_enforce_loop_guard_non_enqueue_decision_passes_through(
    ws_root: Path, db_path: Path
) -> None:
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-passthrough", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    escalate_decision = RouterDecision(kind="escalate", reason="already escalated")
    result = dispatcher._enforce_loop_guard(ws, impl_fix_h, escalate_decision)  # noqa: SLF001
    assert result.kind == "escalate"
    assert result.reason == "already escalated"


def test_enforce_loop_guard_audit_record_written(
    ws_root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-audit", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 2\ncritical bug found",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    decision = _enforce(dispatcher, ws, impl_fix_h, "verification")
    assert decision.kind == "escalate"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "genuine_defect"
    assert record["handover_chain_id"] == chain_id
    assert "revisit_counts_snapshot" in record


def test_spiral_finding_is_frozen_dataclass() -> None:
    from dataclasses import FrozenInstanceError
    f = SpiralFinding(kind="unclassified", evidence="test")
    assert f.kind == "unclassified"
    assert f.evidence == "test"
    with pytest.raises(FrozenInstanceError):
        f.kind = "genuine_defect"  # type: ignore[misc]


def test_enforce_loop_guard_writes_escalation_handover(
    ws_root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    dispatcher = _build_dispatcher(db_path, ws_root)
    ws = create("disp-esc-handover", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="SCENARIOS_MISSING\nDIFF_FINDINGS_CRITICAL: 0",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        decisions=[
            Decision(
                id="d1", statement="did x", rationale="y",
                confidence=0.8, reversibility="medium",
            )
        ],
        artifacts_produced=[
            ArtifactRef(path="judge_review.txt", description="full judge review: pass")
        ],
        open_questions=[
            OpenQuestion(id="q1", question="blocked?", resolver_role="human", blocking=True)
        ],
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    decision = _enforce(dispatcher, ws, impl_fix_h, "verification")
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert decision.reason.startswith("spiral:scenarios_missing:")

    paths = handover_paths(ws)
    esc_handovers = [
        load_handover(p) for p in paths if load_handover(p).handover_type == "escalation"
    ]
    assert len(esc_handovers) == 1
    esc = esc_handovers[0]
    assert esc.handover_chain_id == chain_id
    assert esc.full_synthesis == decision.reason
    assert esc.decisions == impl_fix_h.decisions
    assert esc.artifacts_produced == impl_fix_h.artifacts_produced
    assert esc.open_questions == []
