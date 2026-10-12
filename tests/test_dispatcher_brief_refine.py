from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan._dispatcher_brief_refine import apply
from alan.handover import Handover
from alan.router import RouterDecision
from alan.workspace import create, set_sticky_instructions


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _handover(*, from_team: str, handover_type: str = "terminal") -> Handover:
    return Handover(
        handover_id="h1",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="t1",
        from_team=from_team,
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="done",
    )


def test_apply_ignores_handovers_from_other_teams(ws_root: Path) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="research")
    decision = RouterDecision(kind="done", reason="terminal handover")
    result = apply(ws, _handover(from_team="pr_merge"), decision)
    assert result is decision


def test_apply_falls_back_when_pending_first_hop_is_none(ws_root: Path) -> None:
    ws = create("T", "d", root=ws_root)
    decision = RouterDecision(kind="escalate", reason="unknown from_team: brief_refine")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result is decision


def test_apply_overrides_escalate_decision_with_enqueue(ws_root: Path) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="implementation_plan")
    decision = RouterDecision(kind="escalate", reason="unknown from_team: brief_refine")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result.kind == "enqueue"
    assert result.team == "implementation_plan"


def test_apply_overrides_done_decision_with_enqueue(ws_root: Path) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="research")
    decision = RouterDecision(kind="done", reason="terminal handover")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result.kind == "enqueue"
    assert result.team == "research"


def test_apply_result_carries_no_instructions(ws_root: Path) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="research")
    decision = RouterDecision(kind="done", reason="terminal handover")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result.instructions is None


def test_apply_forwards_sticky_instructions_to_non_consuming_team_target(
    ws_root: Path,
) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="triage")
    ws = set_sticky_instructions(ws, ws.task_id, "please check the flaky test", origin="dispatch")
    decision = RouterDecision(kind="done", reason="terminal handover")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result.team == "triage"
    assert result.instructions == "please check the flaky test"


def test_apply_carries_no_instructions_for_non_consuming_target_without_sticky(
    ws_root: Path,
) -> None:
    ws = create("T", "d", root=ws_root, pending_first_hop="pr_merge")
    decision = RouterDecision(kind="done", reason="terminal handover")
    result = apply(ws, _handover(from_team="brief_refine"), decision)
    assert result.team == "pr_merge"
    assert result.instructions is None
