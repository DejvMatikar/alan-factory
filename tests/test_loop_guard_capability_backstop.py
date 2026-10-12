from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from alan.handover import Decision, Handover, save_handover
from alan.loop_guard import (
    _STALL_THRESHOLD,  # noqa: SLF001
    LoopGuardAuditKind,
    SpiralKind,
    _classify_spiral,  # noqa: SLF001
    _is_stalled,  # noqa: SLF001
    _spiral_anchors_to_classify,  # noqa: SLF001
    _verifier_repeats_unresolved,  # noqa: SLF001
    detect_spiral,
)
from alan.workspace import Workspace, create, increment_revisit_count

_CHAIN = "handover_backstop_chain"
_FINDING_PROSE = (
    "The paginator drops the last page when total items are an exact "
    "multiple of the page size."
)


def _anchored(
    anchors: list[tuple[str, int]],
    *,
    critical: int | None = None,
    in_scope: int | None = None,
    prose: str = _FINDING_PROSE,
) -> str:
    lines = [
        "VERDICT: REQUEST_CHANGES",
        f"DIFF_FINDINGS_CRITICAL: {critical if critical is not None else len(anchors)}",
    ]
    if in_scope is not None:
        lines.append(f"DIFF_FINDINGS_CRITICAL_IN_SCOPE: {in_scope}")
    lines.append("")
    lines.append(prose)
    lines.append("")
    lines.append("## INLINE_FINDINGS_YAML")
    lines.append("```yaml")
    for path, line in anchors:
        lines.append(f"- path: {path}")
        lines.append(f"  line: {line}")
        lines.append("  body: finding detail")
    lines.append("```")
    return "\n".join(lines)


_FINDING_TEXT = _anchored([("pyproject.toml", 289)])
_MALFORMED_ANCHOR_BLOCK = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\nsome finding text.\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    '- path: "pyproject.toml\n  line: 289\n'
    "```\n"
)
_INVALID_ENTRIES_BLOCK = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\nfinding text.\n\n"
    "## INLINE_FINDINGS_YAML\n```yaml\n"
    "- path: pyproject.toml\n  body: missing line\n"
    "- path: pyproject.toml\n  line: not-a-number\n  body: bad line type\n"
    "```\n"
)
_NO_ANCHOR_BLOCK_TEXT = (
    "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\nno anchor block here."
)
_VERIF_DECISION = Decision(
    id="verif-decision",
    statement="requesting changes",
    rationale="finding present",
    confidence=1.0,
    reversibility="low",
)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _verif_handover(
    workspace: Workspace,
    *,
    handover_id: str,
    synthesis: str = _FINDING_TEXT,
    chain_id: str = _CHAIN,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_v",
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        decisions=[_VERIF_DECISION],
    )


def _impl_fix_handover(
    workspace: Workspace,
    *,
    handover_id: str,
    synthesis: str = "implementation_fix committed a tidy-up",
    chain_id: str = _CHAIN,
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_f",
        to_team_suggestions=["verification"],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        decisions=[_VERIF_DECISION],
    )


def test_spiral_kind_includes_verifier_repeating_unresolved() -> None:
    assert "verifier_repeating_unresolved" in get_args(SpiralKind)


def test_loop_guard_audit_kind_includes_verifier_repeating_unresolved() -> None:
    assert "verifier_repeating_unresolved" in get_args(LoopGuardAuditKind)


def test_stall_threshold_stays_pinned_at_0_85() -> None:
    assert _STALL_THRESHOLD == 0.85


def test_verifier_repeats_unresolved_none_when_workspace_is_none(ws_root: Path) -> None:
    ws = create("backstop-no-ws", "d", root=ws_root)
    verif = _verif_handover(ws, handover_id="h1")
    assert _verifier_repeats_unresolved(None, verif) is None


def test_verifier_repeats_unresolved_none_when_critical_count_zero(ws_root: Path) -> None:
    ws = create("backstop-zero-crit", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1", synthesis="VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2", synthesis="VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_no_prior_verification_handover(
    ws_root: Path,
) -> None:
    ws = create("backstop-no-prior", "d", root=ws_root)
    current = _verif_handover(ws, handover_id="h1")
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_fires_on_identical_anchor_pair(ws_root: Path) -> None:
    ws = create("backstop-fires", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    finding = _verifier_repeats_unresolved(ws, current)
    assert finding is not None
    assert finding.kind == "verifier_repeating_unresolved"
    assert "DIFF_FINDINGS_CRITICAL: 1" in finding.evidence


def test_verifier_repeats_unresolved_none_when_critical_moves_from_copilot_resolution_to_implementation_fix(  # noqa: E501
    ws_root: Path,
) -> None:
    ws = create("backstop-cross-file-a", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1",
        synthesis=_anchored([("alan/teams/_copilot_resolution.py", 147)]),
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2",
        synthesis=_anchored([("alan/teams/implementation_fix.py", 428)]),
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_critical_moves_from_scratch_diagnostic_to_snapshot_harness(  # noqa: E501
    ws_root: Path,
) -> None:
    ws = create("backstop-cross-file-b", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1",
        synthesis=_anchored([("ScratchDiagnosticTests.swift", 43)]),
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2",
        synthesis=_anchored([("SnapshotHarness.swift", 203)]),
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_critical_moves_fourteen_lines_in_same_file(
    ws_root: Path,
) -> None:
    ws = create("backstop-same-file-moved", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1", synthesis=_anchored([("scripts/gate.sh", 884)])
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2", synthesis=_anchored([("scripts/gate.sh", 898)])
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_fires_on_every_pair_of_three_passes_at_one_anchor(
    ws_root: Path,
) -> None:
    ws = create("backstop-three-passes", "d", root=ws_root)
    h1 = _verif_handover(ws, handover_id="h1")
    save_handover(ws, h1)
    h2 = _impl_fix_handover(ws, handover_id="h2")
    save_handover(ws, h2)
    h3 = _verif_handover(ws, handover_id="h3")
    finding = _verifier_repeats_unresolved(ws, h3)
    assert finding is not None
    assert finding.kind == "verifier_repeating_unresolved"
    save_handover(ws, h3)
    h4 = _impl_fix_handover(ws, handover_id="h4")
    save_handover(ws, h4)
    h5 = _verif_handover(ws, handover_id="h5")
    finding2 = _verifier_repeats_unresolved(ws, h5)
    assert finding2 is not None
    assert finding2.kind == "verifier_repeating_unresolved"


def test_verifier_repeats_unresolved_fires_on_identical_anchors_despite_changed_count_and_text(
    ws_root: Path,
) -> None:
    ws = create("backstop-count-text-irrelevant", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1",
        synthesis=_anchored(
            [("pyproject.toml", 289)], critical=3, prose="unrelated prose about scheduling"
        ),
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2",
        synthesis=_anchored(
            [("pyproject.toml", 289)],
            critical=1,
            prose="a completely different unrelated finding entirely",
        ),
    )
    finding = _verifier_repeats_unresolved(ws, current)
    assert finding is not None
    assert finding.kind == "verifier_repeating_unresolved"


def test_verifier_repeats_unresolved_none_when_current_pass_has_no_anchor_block(
    ws_root: Path,
) -> None:
    ws = create("backstop-current-no-anchor", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=_NO_ANCHOR_BLOCK_TEXT)
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_prior_pass_has_no_anchor_block(
    ws_root: Path,
) -> None:
    ws = create("backstop-prior-no-anchor", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1", synthesis=_NO_ANCHOR_BLOCK_TEXT)
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_anchor_list_is_empty(ws_root: Path) -> None:
    ws = create("backstop-anchor-list-empty", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=_anchored([], critical=1))
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_anchor_yaml_is_malformed(ws_root: Path) -> None:
    ws = create("backstop-anchor-yaml-malformed", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=_MALFORMED_ANCHOR_BLOCK)
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_every_anchor_entry_is_invalid(
    ws_root: Path,
) -> None:
    ws = create("backstop-anchor-entries-invalid", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=_INVALID_ENTRIES_BLOCK)
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_current_anchors_are_a_superset(
    ws_root: Path,
) -> None:
    ws = create("backstop-superset", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1", synthesis=_anchored([("pyproject.toml", 289)]))
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2",
        synthesis=_anchored(
            [("pyproject.toml", 289), ("alan/teams/implementation_fix.py", 428)]
        ),
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_current_anchors_are_a_subset(
    ws_root: Path,
) -> None:
    ws = create("backstop-subset", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1",
        synthesis=_anchored(
            [("pyproject.toml", 289), ("alan/teams/implementation_fix.py", 428)]
        ),
    )
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=_anchored([("pyproject.toml", 289)]))
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_anchors_partially_overlap(
    ws_root: Path,
) -> None:
    ws = create("backstop-partial-overlap", "d", root=ws_root)
    prior = _verif_handover(
        ws, handover_id="h1",
        synthesis=_anchored(
            [("pyproject.toml", 289), ("alan/teams/implementation_fix.py", 428)]
        ),
    )
    save_handover(ws, prior)
    current = _verif_handover(
        ws, handover_id="h2",
        synthesis=_anchored(
            [("alan/teams/implementation_fix.py", 428), ("scripts/gate.sh", 884)]
        ),
    )
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_none_when_only_sibling_owned_anchors_repeat(
    ws_root: Path,
) -> None:
    ws = create("backstop-sibling-only", "d", root=ws_root)
    text = _anchored([("pyproject.toml", 289)], critical=1, in_scope=0)
    prior = _verif_handover(ws, handover_id="h1", synthesis=text)
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=text)
    assert _verifier_repeats_unresolved(ws, current) is None


def test_verifier_repeats_unresolved_fires_when_in_scope_anchor_repeats_beside_sibling_anchor(
    ws_root: Path,
) -> None:
    ws = create("backstop-in-scope-plus-sibling", "d", root=ws_root)
    text = _anchored(
        [("pyproject.toml", 289), ("alan/teams/implementation_fix.py", 428)],
        critical=2,
        in_scope=1,
    )
    prior = _verif_handover(ws, handover_id="h1", synthesis=text)
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=text)
    finding = _verifier_repeats_unresolved(ws, current)
    assert finding is not None
    assert finding.kind == "verifier_repeating_unresolved"


def test_verifier_repeats_unresolved_names_in_scope_prefix_when_present(
    ws_root: Path,
) -> None:
    ws = create("backstop-in-scope-prefix", "d", root=ws_root)
    in_scope_text = _anchored([("pyproject.toml", 289)], critical=5, in_scope=2)
    prior = _verif_handover(ws, handover_id="h1", synthesis=in_scope_text)
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2", synthesis=in_scope_text)
    finding = _verifier_repeats_unresolved(ws, current)
    assert finding is not None
    assert "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 2" in finding.evidence


def test_classify_spiral_prioritizes_backstop_over_genuine_defect(ws_root: Path) -> None:
    ws = create("backstop-priority", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    impl_fix = _impl_fix_handover(ws, handover_id="h3")
    finding = _classify_spiral(current, impl_fix, workspace=ws)
    assert finding.kind == "verifier_repeating_unresolved"


def test_classify_spiral_legacy_two_arg_call_is_unaffected(ws_root: Path) -> None:
    ws = create("backstop-legacy", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    impl_fix = _impl_fix_handover(ws, handover_id="h3")
    finding = _classify_spiral(current, impl_fix)
    assert finding.kind == "genuine_defect"


def test_spiral_anchors_admit_pair_when_impl_fix_committed_normally(ws_root: Path) -> None:
    ws = create("backstop-anchors", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    impl_fix = _impl_fix_handover(ws, handover_id="h3")
    save_handover(ws, impl_fix)
    anchors = _spiral_anchors_to_classify(ws, current)
    assert anchors == (current, impl_fix)


def test_is_stalled_respects_custom_threshold(ws_root: Path) -> None:
    ws = create("backstop-threshold", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    current = _verif_handover(ws, handover_id="h2")
    assert _is_stalled(ws, current, threshold=1.0) is False
    assert _is_stalled(ws, current, threshold=0.85) is True


def test_detect_spiral_end_to_end_classifies_repeated_anchor_as_verifier_repeating_unresolved(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("backstop-e2e", "d", root=ws_root)
    prior = _verif_handover(ws, handover_id="h1")
    save_handover(ws, prior)
    impl_fix = _impl_fix_handover(ws, handover_id="h2")
    save_handover(ws, impl_fix)
    current = _verif_handover(ws, handover_id="h3")
    ws = increment_revisit_count(ws, "implementation_fix", _CHAIN)
    finding = detect_spiral(ws, current, "implementation_fix")
    assert finding is not None
    assert finding.kind == "verifier_repeating_unresolved"
