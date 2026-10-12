from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from alan.handover import Handover, OpenQuestion, save_handover
from alan.loop_guard import (
    REVISIT_CAP,
    SpiralFinding,
    SpiralKind,
    _classify_spiral,
    _is_stalled,
    check_loop_guard,
    has_forward_progress,
)
from alan.workspace import Workspace, create, increment_revisit_count

_CHAIN = "chain-props"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_handover(
    workspace: Workspace,
    *,
    from_team: str,
    summary: str,
    handover_id: str,
    open_questions: list[OpenQuestion] | None = None,
    chain_id: str = _CHAIN,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        open_questions=open_questions or [],
    )


_NON_ACTIONABLE_Q = OpenQuestion(
    id="non_actionable_feedback_deadbeef",
    question="cannot be fixed in code",
    resolver_role="human",
    blocking=True,
    prior_attempts=[],
)


def _verif_synthesis(*, scenarios_missing: bool, gate_blocked: bool, critical: bool) -> str:
    lines = []
    if scenarios_missing:
        lines.append("SCENARIOS_MISSING")
    if gate_blocked:
        lines.append("## External Gate Status\nCI check failed.")
    lines.append(f"DIFF_FINDINGS_CRITICAL: {2 if critical else 0}")
    lines.append("some review narrative goes here")
    return "\n".join(lines)


def _grid_pair(
    workspace: Workspace,
    *,
    scenarios_missing: bool,
    gate_blocked: bool,
    critical: bool,
    non_actionable: bool,
) -> tuple[Handover, Handover]:
    verif = _make_handover(
        workspace,
        from_team="verification",
        summary=_verif_synthesis(
            scenarios_missing=scenarios_missing, gate_blocked=gate_blocked, critical=critical
        ),
        handover_id="handover_grid_verif",
    )
    impl_fix = _make_handover(
        workspace,
        from_team="implementation_fix",
        summary="applied fixes" if not non_actionable else "could not fix, see question",
        handover_id="handover_grid_implfix",
        open_questions=[_NON_ACTIONABLE_Q] if non_actionable else [],
    )
    return verif, impl_fix


_GRID_AXES = [False, True]


def _iter_grid_cells() -> list[tuple[bool, bool, bool, bool]]:
    return [
        (scenarios_missing, gate_blocked, critical, non_actionable)
        for scenarios_missing in _GRID_AXES
        for gate_blocked in _GRID_AXES
        for critical in _GRID_AXES
        for non_actionable in _GRID_AXES
    ]


_GRID_CELLS = _iter_grid_cells()
_GRID_IDS = [
    f"sm={sm}-gate={gate}-crit={crit}-na={na}" for sm, gate, crit, na in _GRID_CELLS
]


@pytest.mark.parametrize(
    ("scenarios_missing", "gate_blocked", "critical", "non_actionable"),
    _GRID_CELLS,
    ids=_GRID_IDS,
)
def test_classify_spiral_totality(
    ws_root: Path,
    scenarios_missing: bool,
    gate_blocked: bool,
    critical: bool,
    non_actionable: bool,
) -> None:
    ws = create("grid-totality", "d", root=ws_root)
    verif, impl_fix = _grid_pair(
        ws,
        scenarios_missing=scenarios_missing,
        gate_blocked=gate_blocked,
        critical=critical,
        non_actionable=non_actionable,
    )
    finding = _classify_spiral(verif, impl_fix)
    assert isinstance(finding, SpiralFinding)
    assert finding.kind in get_args(SpiralKind)
    assert finding.evidence


@pytest.mark.parametrize(
    ("scenarios_missing", "gate_blocked", "critical", "non_actionable"),
    _GRID_CELLS,
    ids=_GRID_IDS,
)
def test_classify_spiral_deterministic(
    ws_root: Path,
    scenarios_missing: bool,
    gate_blocked: bool,
    critical: bool,
    non_actionable: bool,
) -> None:
    ws = create("grid-determinism", "d", root=ws_root)
    verif, impl_fix = _grid_pair(
        ws,
        scenarios_missing=scenarios_missing,
        gate_blocked=gate_blocked,
        critical=critical,
        non_actionable=non_actionable,
    )
    first = _classify_spiral(verif, impl_fix)
    second = _classify_spiral(verif, impl_fix)
    assert first == second


_NEXT_TEAM = "implementation_fix"


def _progress_stall_texts(*, progress: bool, stalled: bool) -> tuple[str, str]:
    finding = "VERDICT: REQUEST_CHANGES\nfinding: null check missing in foo.py"
    if progress and stalled:
        return finding, "VERDICT: APPROVE\nfinding: null check missing in foo.py"
    if progress and not stalled:
        return finding, "VERDICT: APPROVE\ncompletely unrelated content about a different subsystem"
    if not progress and stalled:
        return finding, finding
    return (
        finding,
        "VERDICT: REQUEST_CHANGES\nunrelated different finding about bar.py race condition",
    )


@pytest.mark.parametrize("runs", [0, REVISIT_CAP, REVISIT_CAP + 1], ids=["below", "at", "above"])
@pytest.mark.parametrize(
    ("progress", "stalled"),
    [(True, True), (True, False), (False, True), (False, False)],
    ids=["progress_and_stall", "progress_only", "stall_only", "neither"],
)
def test_check_loop_guard_escalation_biconditional(
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    progress: bool,
    stalled: bool,
    runs: int,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create(f"bicond-{progress}-{stalled}-{runs}", "d", root=ws_root)
    prior_text, current_text = _progress_stall_texts(progress=progress, stalled=stalled)
    prior = _make_handover(
        ws, from_team="verification", summary=prior_text, handover_id="handover_bicond_prior"
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, from_team="verification", summary=current_text, handover_id="handover_bicond_current"
    )
    for _ in range(runs):
        ws = increment_revisit_count(ws, _NEXT_TEAM, _CHAIN)

    actual_progress = has_forward_progress(ws, current, _NEXT_TEAM)
    actual_stalled = _is_stalled(ws, current)
    assert actual_progress is progress
    assert actual_stalled is stalled

    decision = check_loop_guard(ws, current, _NEXT_TEAM)
    expected_escalate = runs > REVISIT_CAP or (not progress and stalled)
    if not expected_escalate:
        assert decision is None
        return
    assert decision is not None
    assert decision.kind == "escalate"
    assert decision.reason is not None
    if runs > REVISIT_CAP:
        assert "revisit cap exceeded" in decision.reason
    else:
        assert "stall detected" in decision.reason
