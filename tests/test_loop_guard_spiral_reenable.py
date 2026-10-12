from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, save_handover
from alan.loop_guard import _has_actionable_gate_block, detect_spiral  # noqa: SLF001
from alan.teams import _pr_flake_ledger
from alan.teams._verification_gates import _gate_actionability_lines
from alan.workspace import create, increment_revisit_count
from tests._pr_branch_support import _gate_readiness

_E2E_GATE = "Frontend E2E (Playwright, blocking)"

_NO_COMMITS_SYNTHESIS = (
    "implementation_fix produced no commits — escalating. Worker synthesis:\nnone"
)


def _evidenced_actionable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, specs_after: tuple[str, ...],
) -> tuple[str, ...]:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    survived = _pr_flake_ledger.spec_gated_survivors(
        (_E2E_GATE,), specs_before=("e2e/a.spec.ts",), specs_after=specs_after,
    )
    _pr_flake_ledger.record_flake_survival(
        501, survived, "sha-b", forge_target=DEFAULT_FORGE_TARGET,
    )
    readiness = _gate_readiness(failing_checks=(_E2E_GATE,))
    return _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, 501, "sha-b", forge_target=DEFAULT_FORGE_TARGET,
    )


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


def _verif_handover(*, synthesis: str, chain_id: str, handover_id: str, task_id: str) -> Handover:
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


def _impl_fix_handover(*, chain_id: str, handover_id: str, task_id: str) -> Handover:
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
        full_synthesis=_NO_COMMITS_SYNTHESIS,
    )


def test_disjoint_spec_rerun_stops_producing_false_actionable_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    actionable = _evidenced_actionable(monkeypatch, tmp_path, specs_after=("e2e/b.spec.ts",))
    assert actionable == ()
    synthesis = _gate_synthesis(actionable=actionable)
    verif_h = _verif_handover(
        synthesis=synthesis, chain_id="chain-1", handover_id="h1", task_id="t1",
    )
    assert _has_actionable_gate_block(verif_h) is False


def test_same_spec_rerun_still_produces_actionable_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    actionable = _evidenced_actionable(monkeypatch, tmp_path, specs_after=("e2e/a.spec.ts",))
    assert actionable == (_E2E_GATE,)
    synthesis = _gate_synthesis(actionable=actionable)
    verif_h = _verif_handover(
        synthesis=synthesis, chain_id="chain-1", handover_id="h1", task_id="t1",
    )
    assert _has_actionable_gate_block(verif_h) is True


def test_disjoint_spec_rerun_reenables_spiral_detector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ws_root: Path,
) -> None:
    ws = create("spiral-reenable-disjoint", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    actionable = _evidenced_actionable(monkeypatch, tmp_path, specs_after=("e2e/b.spec.ts",))
    verif_h = _verif_handover(
        synthesis=_gate_synthesis(actionable=actionable),
        chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1", task_id=ws.task_id,
    )
    impl_fix_h = _impl_fix_handover(
        chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", task_id=ws.task_id,
    )
    save_handover(ws, verif_h)
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"


def test_same_spec_rerun_still_bails_spiral_detector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ws_root: Path,
) -> None:
    ws = create("spiral-bail-same-spec", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    actionable = _evidenced_actionable(monkeypatch, tmp_path, specs_after=("e2e/a.spec.ts",))
    verif_h = _verif_handover(
        synthesis=_gate_synthesis(actionable=actionable),
        chain_id=chain_id, handover_id=f"handover_{ws.task_id}_v1", task_id=ws.task_id,
    )
    impl_fix_h = _impl_fix_handover(
        chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1", task_id=ws.task_id,
    )
    save_handover(ws, verif_h)
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None
