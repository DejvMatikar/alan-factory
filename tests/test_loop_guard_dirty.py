from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import Handover, save_handover
from alan.loop_guard import (
    _classify_external_gate_blocked,  # noqa: SLF001
    _classify_scenarios_missing,  # noqa: SLF001
    _classify_spiral,  # noqa: SLF001
    _has_actionable_gate_block,  # noqa: SLF001
    check_loop_guard,
    detect_spiral,
    has_forward_progress,
)
from alan.teams import _verdict as _verdict_mod
from alan.teams import _verification_gates
from alan.teams._pr_ready import PrReadiness
from alan.teams.verification import SCENARIOS_MISSING_MARKER
from alan.workspace import create, increment_revisit_count


def _dirty_gate_synthesis() -> str:
    readiness = PrReadiness(
        ok=False,
        merge_state_status="DIRTY",
        review_decision="APPROVED",
        failed_reasons=("mergeStateStatus=DIRTY",),
    )
    block = _verification_gates._format_gate_block(readiness, ())  # noqa: SLF001
    return (
        "> PARKED ON EXTERNAL GATES\n\n"
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        + block
    )


def _verif_handover(task_id: str, *, chain_id: str, handover_id: str) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_dirty_gate_synthesis(),
    )


def _impl_fix_handover(task_id: str, *, chain_id: str, handover_id: str) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_002",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="nothing to fix — merge conflict, not a code defect",
    )


def test_has_actionable_gate_block_true_for_dirty_only_block() -> None:
    assert _has_actionable_gate_block(
        Handover(
            handover_id="h",
            handover_type="forward",
            task_id="t1",
            from_team="verification",
            from_invocation_id="inv_001",
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis=_dirty_gate_synthesis(),
        )
    ) is True


def test_detect_spiral_dirty_only_gate_block_does_not_short_circuit(
    tmp_path: Path,
) -> None:
    ws = create("spiral-dirty-actionable", "d", root=tmp_path / "workspaces")
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1"
    )
    save_handover(ws, verif_h1)
    verif_h2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2"
    )
    save_handover(ws, verif_h2)
    impl_fix_h = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1"
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None
    assert check_loop_guard(ws, impl_fix_h, "verification") is None


def test_detect_spiral_dirty_block_actionable_regardless_of_impl_fix_stuck_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-dirty-stuck-impl-fix", "d", root=tmp_path / "workspaces")
    chain_id = f"handover_{ws.task_id}_chain"
    for suffix in ("v1", "v2"):
        handover_id = f"handover_{ws.task_id}_{suffix}"
        save_handover(ws, _verif_handover(ws.task_id, chain_id=chain_id, handover_id=handover_id))
    stuck_impl_fix = Handover(
        handover_id=f"handover_{ws.task_id}_f1",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_002",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="implementation_fix produced no commits — escalating",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, stuck_impl_fix, "verification") is None


def _verif_h(task_id: str, *, chain_id: str, handover_id: str, synthesis: str) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
    )


def _gate_synthesis_without_critical_line() -> str:
    readiness = PrReadiness(
        ok=False,
        merge_state_status="DIRTY",
        review_decision="APPROVED",
        failed_reasons=("mergeStateStatus=DIRTY",),
    )
    block = _verification_gates._format_gate_block(readiness, ())  # noqa: SLF001
    return "> PARKED ON EXTERNAL GATES\n\nVERDICT: REQUEST_CHANGES\n" + block


def test_has_forward_progress_reflects_a_restored_reviewer_count(tmp_path: Path) -> None:
    ws = create("loop-guard-restored-fp", "d", root=tmp_path / "workspaces")
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _verif_h(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        synthesis="VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 3\n",
    )
    save_handover(ws, prior)
    restored = _verdict_mod._apply_reviewer_count_fallback(  # noqa: SLF001
        "## Verdict\n\nVERDICT: REQUEST_CHANGES\n", "DIFF_FINDINGS_CRITICAL: 1\n",
    )
    current = _verif_h(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        synthesis=restored,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is True


def test_classify_external_gate_blocked_stops_firing_once_the_count_is_restored() -> None:
    unrestored = _gate_synthesis_without_critical_line()
    assert _classify_external_gate_blocked(
        _verif_h("t1", chain_id="c1", handover_id="h1", synthesis=unrestored)
    ) is not None
    restored = _verdict_mod._apply_reviewer_count_fallback(  # noqa: SLF001
        unrestored, "DIFF_FINDINGS_CRITICAL: 2\n",
    )
    assert _classify_external_gate_blocked(
        _verif_h("t1", chain_id="c1", handover_id="h2", synthesis=restored)
    ) is None


def test_classify_scenarios_missing_stops_firing_once_the_count_is_restored() -> None:
    unrestored = f"VERDICT: REQUEST_CHANGES\n{SCENARIOS_MISSING_MARKER}\n"
    assert _classify_scenarios_missing(
        _verif_h("t1", chain_id="c1", handover_id="h1", synthesis=unrestored)
    ) is not None
    restored = _verdict_mod._apply_reviewer_count_fallback(  # noqa: SLF001
        unrestored, "DIFF_FINDINGS_CRITICAL: 1\n",
    )
    assert _classify_scenarios_missing(
        _verif_h("t1", chain_id="c1", handover_id="h2", synthesis=restored)
    ) is None


def test_a_scenario_gate_flipped_verdict_falls_through_to_unclassified() -> None:
    verif = _verif_h(
        "t1", chain_id="c1", handover_id="h1",
        synthesis=(
            "> SCENARIO GATE — FAILED. Blocking scenario status on: s_fail. "
            "Downgrading to REQUEST_CHANGES regardless of the judge's own verdict.\n\n"
            "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        ),
    )
    impl_fix = Handover(
        handover_id="h2",
        handover_chain_id="c1",
        handover_type="forward",
        task_id="t1",
        from_team="implementation_fix",
        from_invocation_id="inv_002",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="fixed nothing actionable",
    )
    finding = _classify_spiral(verif, impl_fix)
    assert finding.kind == "unclassified"
