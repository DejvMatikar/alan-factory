from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import Handover, OpenQuestion, save_handover
from alan.loop_guard import _prior_verification_passes, detect_spiral
from alan.teams._verification_gates import _gate_actionability_lines
from alan.workspace import create


def _gate_synthesis(*, actionable: tuple[str, ...]) -> str:
    lines = [
        "VERDICT: REQUEST_CHANGES",
        "DIFF_FINDINGS_CRITICAL: 0",
        "## External Gate Status",
        "Downgraded APPROVE -> REQUEST_CHANGES because GitHub reports the PR is "
        "not currently mergeable.",
        "(mergeStateStatus=BLOCKED, reviewDecision=APPROVED)",
    ]
    lines.extend(_gate_actionability_lines(actionable))
    return "\n".join(lines)


def _verif_handover(
    task_id: str, *, chain_id: str, handover_id: str, iteration: int,
    actionable: tuple[str, ...],
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_gate_synthesis(actionable=actionable),
    )


def _impl_fix_handover(
    task_id: str, *, chain_id: str, handover_id: str, iteration: int,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_002",
        to_team_suggestions=[],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="committed a review-thread reply, nothing else actionable found",
    )


def _amend_handover(
    task_id: str, *, chain_id: str, handover_id: str, iteration: int, answered: bool,
    blocking_full_synthesis: str,
) -> Handover:
    question = OpenQuestion(
        id="clause-stall-x",
        question="does this still block?",
        resolver_role="human",
        blocking=True,
        answer="yes, proceed" if answered else None,
    )
    prefix = (
        "Recorded answer for question 'clause-stall-x': yes, proceed"
        if answered
        else "operator amended the handover without recording an answer"
    )
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="revisit_amend",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_003",
        to_team_suggestions=[],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"{prefix}\n\n{blocking_full_synthesis}",
        open_questions=[question],
    )


def test_three_passes_with_overlapping_checks_fire(ws_root: Path) -> None:
    ws = create("unchanged-overlap-fires", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a", "check-b"),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a", "check-c"),
    )
    save_handover(ws, v2)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=5,
    )
    save_handover(ws, f2)
    v3 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v3",
        iteration=6, actionable=("check-a", "check-d"),
    )
    save_handover(ws, v3)
    finding = detect_spiral(ws, v3, "implementation_fix")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"
    assert "check-a" in finding.evidence


def test_shrinking_set_fires(ws_root: Path) -> None:
    ws = create("unchanged-shrinking-fires", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a", "check-b"),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a", "check-b"),
    )
    save_handover(ws, v2)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=5,
    )
    save_handover(ws, f2)
    v3 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v3",
        iteration=6, actionable=("check-a",),
    )
    save_handover(ws, v3)
    finding = detect_spiral(ws, v3, "implementation_fix")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"
    assert "check-a" in finding.evidence


def test_disjoint_third_pass_does_not_fire(ws_root: Path) -> None:
    ws = create("unchanged-disjoint-no-fire", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-b",),
    )
    save_handover(ws, v2)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=5,
    )
    save_handover(ws, f2)
    v3 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v3",
        iteration=6, actionable=("check-c",),
    )
    save_handover(ws, v3)
    assert detect_spiral(ws, v3, "implementation_fix") is None


def test_only_two_passes_does_not_fire(ws_root: Path) -> None:
    ws = create("unchanged-two-passes-no-fire", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a",),
    )
    save_handover(ws, v2)
    assert detect_spiral(ws, v2, "implementation_fix") is None


def test_first_actionable_red_does_not_fire(ws_root: Path) -> None:
    ws = create("unchanged-first-red-no-fire", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    f0 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f0", iteration=1,
    )
    save_handover(ws, f0)
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    assert detect_spiral(ws, v1, "implementation_fix") is None


def test_answer_recording_amend_is_transparent_and_still_fires(
    ws_root: Path,
) -> None:
    ws = create("unchanged-transparent-amend-fires", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a",),
    )
    save_handover(ws, v2)
    amend = _amend_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_amend",
        iteration=5, answered=True, blocking_full_synthesis=v2.full_synthesis,
    )
    save_handover(ws, amend)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=6,
    )
    save_handover(ws, f2)
    v3 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v3",
        iteration=7, actionable=("check-a",),
    )
    save_handover(ws, v3)
    finding = detect_spiral(ws, v3, "implementation_fix")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"


def test_non_answer_amend_resets_the_count(ws_root: Path) -> None:
    ws = create("unchanged-non-answer-amend-resets", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a",),
    )
    save_handover(ws, v2)
    amend = _amend_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_amend",
        iteration=5, answered=False, blocking_full_synthesis=v2.full_synthesis,
    )
    save_handover(ws, amend)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=6,
    )
    save_handover(ws, f2)
    v3 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v3",
        iteration=7, actionable=("check-a",),
    )
    save_handover(ws, v3)
    assert detect_spiral(ws, v3, "implementation_fix") is None


def test_prior_verification_passes_skips_unreadable_handover(
    ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = create("unchanged-corrupt-skip", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    corrupt_path = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [", encoding="utf-8")
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a",),
    )
    save_handover(ws, v2)
    with caplog.at_level("WARNING", logger="alan.loop_guard"):
        result = _prior_verification_passes(ws, v2)
    assert [h.handover_id for h in result] == [v1.handover_id]
    assert any("skipping unreadable" in r.message for r in caplog.records)


def test_a_pass_with_critical_findings_is_not_admitted(ws_root: Path) -> None:
    ws = create("unchanged-critical-not-admitted", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    v1 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1",
        iteration=2, actionable=("check-a",),
    )
    save_handover(ws, v1)
    f1 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", iteration=3,
    )
    save_handover(ws, f1)
    v2 = _verif_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v2",
        iteration=4, actionable=("check-a",),
    )
    save_handover(ws, v2)
    f2 = _impl_fix_handover(
        ws.task_id, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f2", iteration=5,
    )
    save_handover(ws, f2)
    v3_synthesis = _gate_synthesis(actionable=("check-a",)).replace(
        "DIFF_FINDINGS_CRITICAL: 0", "DIFF_FINDINGS_CRITICAL: 1"
    )
    v3 = Handover(
        handover_id=f"handover_{ws.task_id}_v3",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=6,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=v3_synthesis,
    )
    save_handover(ws, v3)
    assert detect_spiral(ws, v3, "implementation_fix") is None
