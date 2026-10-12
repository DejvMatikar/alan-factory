from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import ArtifactRef, Handover, OpenQuestion, save_handover
from alan.loop_guard import (
    REVISIT_CAP,
    _find_latest_on_chain,
    _find_latest_prior_for_team,
    _strip_boilerplate,
    check_loop_guard,
    detect_spiral,
    has_forward_progress,
    is_no_commit_escalation,
    jaccard_trigrams,
)
from alan.teams.verification import GATE_ACTIONABLE_NO, GATE_ACTIONABLE_YES
from alan.workspace import Workspace, create, increment_revisit_count, revisit_key


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_handover(
    workspace: Workspace,
    *,
    from_team: str = "research",
    summary: str = "ok",
    handover_id: str | None = None,
    blocking_questions: int = 0,
    handover_chain_id: str | None = None,
) -> Handover:
    return Handover(
        handover_id=handover_id or f"handover_{workspace.task_id}_{from_team}_{summary[:6]}",
        handover_chain_id=handover_chain_id or "",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        open_questions=[
            OpenQuestion(
                id=f"q{i}",
                question="?",
                resolver_role="human",
                blocking=True,
                prior_attempts=[],
            )
            for i in range(blocking_questions)
        ],
        full_synthesis=summary,
    )


def test_revisit_cap_is_three() -> None:
    assert REVISIT_CAP == 3


def test_revisit_key_round_trip_check_loop_guard_and_detect_spiral(ws_root: Path) -> None:
    ws = create("key-roundtrip", "d", root=ws_root)
    chain_id = "test-chain"
    next_team = "implementation_fix"
    key = revisit_key(ws.workspace_id, ws.task_id, next_team, chain_id)
    ws = increment_revisit_count(ws, next_team, chain_id)
    assert ws.meta.revisit_counts.get(key) == 1
    handover = _make_handover(ws, from_team="verification", handover_chain_id=chain_id)
    assert check_loop_guard(ws, handover, next_team) is None
    assert detect_spiral(ws, handover, next_team) is None


def test_check_loop_guard_passes_below_cap(ws_root: Path) -> None:
    ws = create("under-cap", "d", root=ws_root)
    handover = _make_handover(ws)
    assert check_loop_guard(ws, handover, "implementation_plan") is None


def test_check_loop_guard_escalates_above_cap(ws_root: Path) -> None:
    ws = create("over-cap", "d", root=ws_root)
    handover = _make_handover(ws)
    for _ in range(REVISIT_CAP + 1):
        ws = increment_revisit_count(ws, "implementation_plan", handover.handover_chain_id)
    decision = check_loop_guard(ws, handover, "implementation_plan")
    assert decision is not None
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert "revisit cap exceeded" in decision.reason


def test_check_loop_guard_passes_at_cap_boundary(ws_root: Path) -> None:
    ws = create("at-cap", "d", root=ws_root)
    handover = _make_handover(ws)
    chain = handover.handover_chain_id
    for _ in range(REVISIT_CAP):
        ws = increment_revisit_count(ws, "implementation_plan", chain)
    assert check_loop_guard(ws, handover, "implementation_plan") is None


def test_check_loop_guard_escalates_just_above_cap(ws_root: Path) -> None:
    ws = create("just-over-cap", "d", root=ws_root)
    handover = _make_handover(ws)
    for _ in range(REVISIT_CAP + 1):
        ws = increment_revisit_count(ws, "implementation_plan", handover.handover_chain_id)
    decision = check_loop_guard(ws, handover, "implementation_plan")
    assert decision is not None
    assert decision.kind == "escalate"


def test_check_loop_guard_no_prior_no_stall(ws_root: Path) -> None:
    ws = create("no-prior", "d", root=ws_root)
    handover = _make_handover(ws)
    assert check_loop_guard(ws, handover, "implementation_plan") is None


def test_check_loop_guard_detects_stall_when_summary_repeats(ws_root: Path) -> None:
    ws = create("stall", "d", root=ws_root)
    summary = (
        "We recommend the standard approach with the usual trade-offs and "
        "the same set of next steps as before."
    )
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary=summary, handover_id=f"handover_{ws.task_id}_first",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary=summary, handover_id=f"handover_{ws.task_id}_second",
        handover_chain_id=chain_id,
    )
    decision = check_loop_guard(ws, current, "implementation_plan")
    assert decision is not None
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert "stall detected" in decision.reason


def test_check_loop_guard_no_stall_when_summary_changes(ws_root: Path) -> None:
    ws = create("evolving", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="A" * 200, handover_id=f"handover_{ws.task_id}_one",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws,
        summary="completely different content with no shared trigrams here",
        handover_id=f"handover_{ws.task_id}_two",
        handover_chain_id=chain_id,
    )
    assert check_loop_guard(ws, current, "implementation_plan") is None


def test_check_loop_guard_no_stall_when_blocking_questions_changed(
    ws_root: Path,
) -> None:
    ws = create("progressing", "d", root=ws_root)
    same = "x" * 200
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws,
        summary=same,
        handover_id=f"handover_{ws.task_id}_a",
        blocking_questions=2,
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws,
        summary=same,
        handover_id=f"handover_{ws.task_id}_b",
        blocking_questions=1,
        handover_chain_id=chain_id,
    )
    assert check_loop_guard(ws, current, "implementation_plan") is None


def test_check_loop_guard_catches_implementation_fix_verification_cycle(
    ws_root: Path,
) -> None:
    ws = create("fix-loop", "d", root=ws_root)
    repeated_summary = (
        "REQUEST_CHANGES: the fix did not address the regression in "
        "feature.py L42; same finding as before, no new diagnostic."
    )
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws,
        from_team="verification",
        summary=repeated_summary,
        handover_id=f"handover_{ws.task_id}_v1",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws,
        from_team="verification",
        summary=repeated_summary,
        handover_id=f"handover_{ws.task_id}_v2",
        handover_chain_id=chain_id,
    )
    decision = check_loop_guard(ws, current, "implementation_fix")
    assert decision is not None
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert "stall detected" in decision.reason


def test_check_loop_guard_catches_pr_merge_comment_cycle(ws_root: Path) -> None:
    ws = create("comment-loop", "d", root=ws_root)
    repeated_summary = (
        "PR #99 has 0 unresolved Copilot review thread(s) and 1 unresolved "
        "Copilot comment(s)\n\n"
        "Comment 1 (issue_comment, database_id=4242):\n"
        "rename foo to bar in alan/teams/_pr_branch.py"
    )
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws,
        from_team="pr_merge",
        summary=repeated_summary,
        handover_id=f"handover_{ws.task_id}_p1",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws,
        from_team="pr_merge",
        summary=repeated_summary,
        handover_id=f"handover_{ws.task_id}_p2",
        handover_chain_id=chain_id,
    )
    decision = check_loop_guard(ws, current, "implementation_fix")
    assert decision is not None
    assert decision.kind == "escalate"
    assert decision.reason is not None
    assert "stall detected" in decision.reason


def test_check_loop_guard_ignores_priors_from_other_teams(ws_root: Path) -> None:
    ws = create("cross-team", "d", root=ws_root)
    other = _make_handover(
        ws,
        from_team="brainstorming",
        summary="x" * 200,
        handover_id=f"handover_{ws.task_id}_b",
    )
    save_handover(ws, other)
    current = _make_handover(
        ws,
        from_team="research",
        summary="x" * 200,
        handover_id=f"handover_{ws.task_id}_r",
    )
    assert check_loop_guard(ws, current, "implementation_plan") is None


def test_jaccard_trigrams_identical_strings() -> None:
    assert jaccard_trigrams("abcdef", "abcdef") == 1.0


def test_jaccard_trigrams_no_overlap() -> None:
    assert jaccard_trigrams("abcdef", "ghijkl") == 0.0


def test_jaccard_trigrams_partial_overlap() -> None:
    s = jaccard_trigrams("abcdef", "abcxyz")
    assert 0 < s < 1


def test_jaccard_trigrams_both_too_short_treated_as_identical() -> None:
    assert jaccard_trigrams("ab", "xy") == 1.0


def test_jaccard_trigrams_one_too_short() -> None:
    assert jaccard_trigrams("ab", "abcdef") == 0.0


def test_check_loop_guard_two_chains_tracked_independently(ws_root: Path) -> None:
    ws = create("two-chains", "d", root=ws_root)
    h_a = _make_handover(ws, handover_id="handover_two-chains_a")
    h_b = _make_handover(ws, handover_id="handover_two-chains_b")
    for _ in range(REVISIT_CAP + 1):
        ws = increment_revisit_count(ws, "implementation_plan", h_a.handover_chain_id)
    assert check_loop_guard(ws, h_a, "implementation_plan") is not None
    assert check_loop_guard(ws, h_b, "implementation_plan") is None


def test_check_loop_guard_no_stall_when_prior_in_different_chain(ws_root: Path) -> None:
    ws = create("diff-chain-stall", "d", root=ws_root)
    same = "x" * 200
    prior = _make_handover(
        ws, summary=same, handover_id=f"handover_{ws.task_id}_prior"
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary=same, handover_id=f"handover_{ws.task_id}_current"
    )
    assert check_loop_guard(ws, current, "implementation_plan") is None


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
    synthesis: str = "applied fixes",
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
    )


def test_detect_spiral_scenarios_missing(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-sm", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_synthesis = "SCENARIOS_MISSING\nDIFF_FINDINGS_CRITICAL: 0\nno test scenarios provided"
    verif_h = _make_verif_handover(
        ws,
        synthesis=verif_synthesis,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "scenarios_missing"
    assert "SCENARIOS_MISSING" in finding.evidence
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    lines = audit_file.read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["kind"] == "scenarios_missing"
    assert record["task_id"] == ws.task_id
    assert record["timestamp_utc"]
    assert record["evidence"]
    assert record["handover_chain_id"] == impl_fix_h.handover_chain_id
    spiral_key = f"{ws.task_id}|{ws.task_id}|verification|{chain_id}"
    assert spiral_key in record["revisit_counts_snapshot"]


def test_detect_spiral_external_gate_blocked(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-egb", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\nCI checks failed."
    )
    verif_h = _make_verif_handover(
        ws,
        synthesis=verif_synthesis,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"
    assert "External Gate Status" in finding.evidence
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "external_gate_blocked"


def test_detect_spiral_non_actionable_finding(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-naf", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\nsome finding",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    other_q = OpenQuestion(
        id="some_unrelated_question",
        question="unrelated",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
    )
    non_actionable_q = OpenQuestion(
        id="non_actionable_feedback_abc12345",
        question="CI environment issue cannot be fixed in code",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
    )
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        open_questions=[other_q, non_actionable_q],
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "non_actionable_finding"
    assert "non-actionable" in finding.evidence
    assert "non_actionable_feedback_abc12345" in finding.evidence
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "non_actionable_finding"


def test_detect_spiral_genuine_defect(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-gd", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_synthesis = (
        "SCENARIOS_MISSING\n"
        "## External Gate Status\nsome gate info\n"
        "DIFF_FINDINGS_CRITICAL: 3\nfoo.py:42 null deref"
    )
    verif_h = _make_verif_handover(
        ws,
        synthesis=verif_synthesis,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "genuine_defect"
    assert "DIFF_FINDINGS_CRITICAL:" in finding.evidence
    assert "3" in finding.evidence
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "genuine_defect"


def test_detect_spiral_unclassified(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-unc", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 0\nno known markers present here",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "unclassified"
    assert "no known pattern matched" in finding.evidence
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "unclassified"


def test_detect_spiral_no_spiral_on_first_verif_to_impl_fix(ws_root: Path) -> None:
    ws = create("spiral-neg-first", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 0\nrequest changes",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    assert detect_spiral(ws, verif_h, "implementation_fix") is None


def test_detect_spiral_no_spiral_on_architecture_to_implementation_chain(
    ws_root: Path,
) -> None:
    ws = create("spiral-neg-arch", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    arch_h = Handover(
        handover_id=f"handover_{ws.task_id}_arch",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="architecture",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="architecture done",
    )
    ws = increment_revisit_count(ws, "implementation_plan", chain_id)
    assert detect_spiral(ws, arch_h, "implementation_plan") is None


def test_detect_spiral_no_spiral_when_anchors_missing(ws_root: Path) -> None:
    ws = create("spiral-no-anchor", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None


def test_detect_spiral_no_spiral_verif_from_verif_no_impl_fix_anchor(
    ws_root: Path,
) -> None:
    ws = create("spiral-no-impl-anchor", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 0\nrequest changes",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    ws = increment_revisit_count(ws, "implementation_fix", chain_id)
    assert detect_spiral(ws, verif_h, "implementation_fix") is None


def test_detect_spiral_not_stuck_returns_none(ws_root: Path) -> None:
    ws = create("spiral-not-stuck", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    verif_h = _make_verif_handover(
        ws,
        synthesis="DIFF_FINDINGS_CRITICAL: 2\nreal bug in foo.py",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    other_q = OpenQuestion(
        id="some_other_question",
        question="unrelated question",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
    )
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 2 fixes to foo.py",
        open_questions=[other_q],
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None
    assert check_loop_guard(ws, impl_fix_h, "verification") is None


def test_detect_spiral_external_gate_blocked_committing_impl_fix(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-egb-commits", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\nCI check X failed."
    )
    verif_h1 = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h1)
    verif_h2 = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v2",
    )
    save_handover(ws, verif_h2)
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 2 commits to ci config",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "external_gate_blocked"


def test_detect_spiral_scenarios_missing_committing_impl_fix(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-sm-commits", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    sm_synthesis = "SCENARIOS_MISSING\nDIFF_FINDINGS_CRITICAL: 0\nno test scenarios provided"
    verif_h1 = _make_verif_handover(
        ws, synthesis=sm_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h1)
    verif_h2 = _make_verif_handover(
        ws, synthesis=sm_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v2",
    )
    save_handover(ws, verif_h2)
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="added missing test scenarios",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "scenarios_missing"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "scenarios_missing"


def test_detect_spiral_single_clean_block_no_short_circuit(ws_root: Path) -> None:
    ws = create("spiral-single-block", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = "DIFF_FINDINGS_CRITICAL: 0\n## External Gate Status\nCI failed."
    verif_h = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied commits, gate still blocked",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None


def test_detect_spiral_prior_verif_not_clean_block_no_short_circuit(ws_root: Path) -> None:
    ws = create("spiral-prior-defect", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior_synthesis = "DIFF_FINDINGS_CRITICAL: 2\n## External Gate Status\nCI failed."
    verif_h1 = _make_verif_handover(
        ws, synthesis=prior_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h1)
    gate_synthesis = "DIFF_FINDINGS_CRITICAL: 0\n## External Gate Status\nCI still failing."
    verif_h2 = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v2",
    )
    save_handover(ws, verif_h2)
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="fixed some but prior had real criticals",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None


@pytest.mark.parametrize("legacy_keys", [
    {"verification": 2},
    {"implementation_fix": 1, "verification": 3},
])
def test_detect_spiral_legacy_revisit_counts_fall_through(
    ws_root: Path, legacy_keys: dict[str, int]
) -> None:
    ws = create("spiral-legacy", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    ws.meta.revisit_counts.update(legacy_keys)
    impl_fix_h = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f1",
    )
    assert detect_spiral(ws, impl_fix_h, "verification") is None


def test_check_loop_guard_emits_cap_exhausted_audit(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("cap-audit", "d", root=ws_root)
    handover = _make_handover(ws)
    for _ in range(REVISIT_CAP + 1):
        ws = increment_revisit_count(ws, "implementation_plan", handover.handover_chain_id)
    decision = check_loop_guard(ws, handover, "implementation_plan")
    assert decision is not None
    assert decision.kind == "escalate"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "cap_exhausted_no_spiral"
    assert record["task_id"] == ws.task_id


def test_check_loop_guard_emits_stall_audit(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("stall-audit", "d", root=ws_root)
    summary = "We recommend the standard approach with the usual trade-offs same set next steps."
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary=summary, handover_id=f"handover_{ws.task_id}_first",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary=summary, handover_id=f"handover_{ws.task_id}_second",
        handover_chain_id=chain_id,
    )
    decision = check_loop_guard(ws, current, "implementation_plan")
    assert decision is not None
    assert decision.kind == "escalate"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["kind"] == "cap_exhausted_no_spiral"


def test_detect_spiral_from_impl_fix_to_verif(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-sym", "d", root=ws_root)
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
    save_handover(ws, impl_fix_h)
    ws = increment_revisit_count(ws, "verification", chain_id)
    impl_fix_h2 = _make_impl_fix_handover(
        ws,
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_f2",
        synthesis=_NO_COMMITS_SYNTHESIS,
    )
    finding = detect_spiral(ws, impl_fix_h2, "verification")
    assert finding is not None
    assert finding.kind == "unclassified"
    audit_file = tmp_path / "logs" / "spiral_audit.jsonl"
    record = json.loads(audit_file.read_text().splitlines()[-1])
    assert record["handover_chain_id"] == chain_id


def test_check_loop_guard_old_2segment_key_not_counted(ws_root: Path) -> None:
    ws = create("legacy-2seg", "d", root=ws_root)
    handover = _make_handover(ws)
    ws.meta.revisit_counts[f"implementation_plan|{handover.handover_chain_id}"] = 10
    assert check_loop_guard(ws, handover, "implementation_plan") is None


def test_check_loop_guard_explicit_task_id_creates_distinct_key(ws_root: Path) -> None:
    ws = create("explicit-tid", "d", root=ws_root)
    handover = _make_handover(ws)
    for _ in range(REVISIT_CAP + 1):
        ws = increment_revisit_count(
            ws, "implementation_plan", handover.handover_chain_id, task_id="other-task"
        )
    assert check_loop_guard(ws, handover, "implementation_plan") is None
    decision = check_loop_guard(ws, handover, "implementation_plan", task_id="other-task")
    assert decision is not None
    assert decision.kind == "escalate"


def test_find_latest_on_chain_skips_corrupt_file(ws_root: Path) -> None:
    ws = create("chain-corrupt", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    valid_h = _make_verif_handover(
        ws,
        synthesis="ok",
        chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, valid_h)
    corrupt_path = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [", encoding="utf-8")
    anchor = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1"
    )
    result = _find_latest_on_chain(ws, anchor, "verification")
    assert result is not None
    assert result.handover_id == valid_h.handover_id


def test_find_latest_on_chain_corrupt_is_logged(
    ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = create("chain-log", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )
    anchor = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1"
    )
    with caplog.at_level("WARNING", logger="alan.loop_guard"):
        result = _find_latest_on_chain(ws, anchor, "verification")
    assert result is None
    assert any("skipping unreadable" in r.message for r in caplog.records)


def test_find_latest_prior_for_team_skips_corrupt_file(ws_root: Path) -> None:
    ws = create("prior-corrupt", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior_h = _make_handover(
        ws,
        from_team="research",
        summary="prior ok",
        handover_id=f"handover_{ws.task_id}_first",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior_h)
    corrupt_path = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [", encoding="utf-8")
    current_h = _make_handover(
        ws,
        from_team="research",
        summary="current",
        handover_id=f"handover_{ws.task_id}_second",
        handover_chain_id=chain_id,
    )
    result = _find_latest_prior_for_team(ws, current_h)
    assert result is not None
    assert result.handover_id == prior_h.handover_id


def test_find_latest_prior_for_team_corrupt_is_logged(
    ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = create("prior-log", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )
    current_h = _make_handover(
        ws,
        from_team="research",
        summary="current",
        handover_id=f"handover_{ws.task_id}_second",
        handover_chain_id=chain_id,
    )
    with caplog.at_level("WARNING", logger="alan.loop_guard"):
        result = _find_latest_prior_for_team(ws, current_h)
    assert result is None
    assert any("skipping unreadable" in r.message for r in caplog.records)


def test_strip_boilerplate_removes_verdict_line() -> None:
    text = "VERDICT: REQUEST_CHANGES\nsome finding\n"
    result = _strip_boilerplate(text)
    assert "VERDICT:" not in result
    assert "some finding" in result


def test_strip_boilerplate_removes_diff_findings_critical_line() -> None:
    text = "DIFF_FINDINGS_CRITICAL: 2\nsome finding\n"
    result = _strip_boilerplate(text)
    assert "DIFF_FINDINGS_CRITICAL:" not in result
    assert "some finding" in result


def test_strip_boilerplate_removes_scenarios_missing_line() -> None:
    text = "SCENARIOS_MISSING\nsome finding\n"
    result = _strip_boilerplate(text)
    assert "SCENARIOS_MISSING" not in result
    assert "some finding" in result


def test_strip_boilerplate_removes_gate_block_section() -> None:
    text = "finding A\n## External Gate Status\nCI failed.\nsome other line"
    result = _strip_boilerplate(text)
    assert "External Gate Status" not in result
    assert "CI failed" not in result
    assert "finding A" in result


def test_strip_boilerplate_preserves_genuine_findings() -> None:
    text = "Finding: null check missing in foo.py line 42"
    assert _strip_boilerplate(text) == text


def test_has_forward_progress_pr_merge(ws_root: Path) -> None:
    ws = create("fp-prm", "d", root=ws_root)
    h = _make_handover(ws)
    assert has_forward_progress(ws, h, "pr_merge") is True


def test_has_forward_progress_no_prior(ws_root: Path) -> None:
    ws = create("fp-nop", "d", root=ws_root)
    h = _make_handover(ws)
    assert has_forward_progress(ws, h, "implementation_fix") is False


def test_has_forward_progress_prior_verdict_none(ws_root: Path) -> None:
    ws = create("fp-pvn", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="no verdict here", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary="no verdict here", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is False


def test_has_forward_progress_cur_verdict_none(ws_root: Path) -> None:
    ws = create("fp-cvn", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="VERDICT: REQUEST_CHANGES\nfinding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary="no verdict, same finding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is False


def test_has_forward_progress_same_verdict(ws_root: Path) -> None:
    ws = create("fp-svd", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="VERDICT: REQUEST_CHANGES\nfinding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary="VERDICT: REQUEST_CHANGES\nfinding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is False


def test_has_forward_progress_verdict_changed(ws_root: Path) -> None:
    ws = create("fp-vch", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="VERDICT: REQUEST_CHANGES\nfinding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary="VERDICT: APPROVE\nall fixed", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is True


def test_has_forward_progress_critical_count_decreased(ws_root: Path) -> None:
    ws = create("fp-ccd", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary="DIFF_FINDINGS_CRITICAL: 2\nfinding", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary="DIFF_FINDINGS_CRITICAL: 0\nall clear", from_team="verification",
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert has_forward_progress(ws, current, "implementation_fix") is True


def _make_handover_with_artifacts(
    ws: Workspace, artifacts: list[str], *, handover_id: str, chain_id: str
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime(2024, 1, 1, tzinfo=UTC),
        full_synthesis="DIFF_FINDINGS_CRITICAL: 0\nok",
        artifacts_produced=[ArtifactRef(path=p, description="review") for p in artifacts],
    )


def test_has_forward_progress_artifacts_changed(ws_root: Path) -> None:
    ws = create("fp-art", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover_with_artifacts(
        ws, ["pr/1"], handover_id=f"handover_{ws.task_id}_v1", chain_id=chain_id
    )
    save_handover(ws, prior)
    current = _make_handover_with_artifacts(
        ws, ["pr/1", "pr/2"], handover_id=f"handover_{ws.task_id}_v2", chain_id=chain_id
    )
    assert has_forward_progress(ws, current, "implementation_fix") is True


def test_has_forward_progress_no_change(ws_root: Path) -> None:
    ws = create("fp-noc", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover_with_artifacts(
        ws, ["pr/1"], handover_id=f"handover_{ws.task_id}_v1", chain_id=chain_id
    )
    save_handover(ws, prior)
    current = _make_handover_with_artifacts(
        ws, ["pr/1"], handover_id=f"handover_{ws.task_id}_v2", chain_id=chain_id
    )
    assert has_forward_progress(ws, current, "implementation_fix") is False


def test_new_invocation_report_counts_as_artifact_progress(ws_root: Path) -> None:
    ws = create("report-progress", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws,
        from_team="research",
        summary="x" * 200,
        handover_id=f"handover_{ws.task_id}_v1",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = Handover(
        handover_id=f"handover_{ws.task_id}_v2",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="research",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_plan"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="x" * 200,
        artifacts_produced=[
            ArtifactRef(
                path="invocations/inv_002/report.md",
                description="Full research report",
            )
        ],
    )
    assert has_forward_progress(ws, current, "implementation_plan") is True


def test_check_loop_guard_no_stall_when_forward_progress_pr_merge(ws_root: Path) -> None:
    ws = create("fp-skip-stall", "d", root=ws_root)
    same = "x" * 200
    chain_id = f"handover_{ws.task_id}_chain"
    prior = _make_handover(
        ws, summary=same, handover_id=f"handover_{ws.task_id}_v1",
        handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, summary=same, handover_id=f"handover_{ws.task_id}_v2",
        handover_chain_id=chain_id,
    )
    assert check_loop_guard(ws, current, "pr_merge") is None


def test_check_loop_guard_no_stall_when_verification_boilerplate_same(ws_root: Path) -> None:
    ws = create("verif-boilerplate", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    boilerplate = "VERDICT: REQUEST_CHANGES\n## External Gate Status\nCI checks failed.\n"
    prior = _make_handover(
        ws, from_team="verification",
        summary="Finding A: foo.py line 42 missing null check.\n" + boilerplate,
        handover_id=f"handover_{ws.task_id}_v1", handover_chain_id=chain_id,
    )
    save_handover(ws, prior)
    current = _make_handover(
        ws, from_team="verification",
        summary="Finding B: bar.py line 99 race condition with threading lock.\n" + boilerplate,
        handover_id=f"handover_{ws.task_id}_v2", handover_chain_id=chain_id,
    )
    assert check_loop_guard(ws, current, "implementation_fix") is None


def _escalation(full_synthesis: str, *, handover_type: str = "escalation") -> Handover:
    return Handover(
        handover_id="h",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="t1",
        from_team="implementation",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=full_synthesis,
    )


class TestIsNoCommitEscalation:
    def test_true_for_no_commit_escalation(self) -> None:
        h = _escalation("implementation produced no commits — escalating. Worker synthesis:\nx")
        assert is_no_commit_escalation(h) is True

    def test_false_for_escalation_without_sentinel(self) -> None:
        assert is_no_commit_escalation(_escalation("some other escalation reason")) is False

    def test_false_for_non_escalation_type(self) -> None:
        h = _escalation(
            "implementation produced no commits — escalating.", handover_type="forward"
        )
        assert is_no_commit_escalation(h) is False


def test_detect_spiral_actionable_gate_block_does_not_short_circuit(
    ws_root: Path,
) -> None:
    ws = create("spiral-egb-actionable", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\n"
        "- failing checks: SonarCloud (blocking quality gate)\n"
        f"{GATE_ACTIONABLE_YES}\n"
        "These failing checks are this diff's own defects."
    )
    verif_h1 = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h1)
    verif_h2 = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v2",
    )
    save_handover(ws, verif_h2)
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 3 commits to the failing spec",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None
    assert check_loop_guard(ws, impl_fix_h, "verification") is None


def test_detect_spiral_non_actionable_gate_block_still_escalates(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-egb-nonaction", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\n"
        f"{GATE_ACTIONABLE_NO}\n"
        "No failing check is attributable to this diff."
    )
    for suffix in ("v1", "v2"):
        save_handover(ws, _make_verif_handover(
            ws, synthesis=gate_synthesis, chain_id=chain_id,
            handover_id=f"handover_{ws.task_id}_{suffix}",
        ))
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 1 commit",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"


def test_detect_spiral_legacy_gate_block_without_marker_still_escalates(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-egb-legacy", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\nCI checks failed."
    )
    for suffix in ("v1", "v2"):
        save_handover(ws, _make_verif_handover(
            ws, synthesis=gate_synthesis, chain_id=chain_id,
            handover_id=f"handover_{ws.task_id}_{suffix}",
        ))
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 1 commit",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"


def test_detect_spiral_actionable_marker_outside_gate_block_still_escalates(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-egb-echoed", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## Rationale\n"
        f"The previous block said {GATE_ACTIONABLE_YES}, which I disagree with.\n"
        "## External Gate Status\n"
        f"{GATE_ACTIONABLE_NO}\nnothing this diff can fix\n"
    )
    for suffix in ("v1", "v2"):
        save_handover(ws, _make_verif_handover(
            ws, synthesis=gate_synthesis, chain_id=chain_id,
            handover_id=f"handover_{ws.task_id}_{suffix}",
        ))
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="applied 1 commit",
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "external_gate_blocked"


def test_detect_spiral_actionable_gate_beats_stuck_impl_fix(
    ws_root: Path,
) -> None:
    ws = create("spiral-actionable-vs-stuck", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        "## External Gate Status\n"
        f"{GATE_ACTIONABLE_YES}\n"
        "GATE_ACTIONABLE_CHECKS:SonarCloud (blocking quality gate)\n"
    )
    verif_h = _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    )
    save_handover(ws, verif_h)
    stuck_q = OpenQuestion(
        id="non_actionable_feedback_inv_018",
        question="no code defect to fix; only external CI gate status",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
    )
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="nothing to fix", open_questions=[stuck_q],
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    assert detect_spiral(ws, impl_fix_h, "verification") is None
    assert check_loop_guard(ws, impl_fix_h, "verification") is None


def test_detect_spiral_non_actionable_gate_still_honours_stuck_impl_fix(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("spiral-nonaction-vs-stuck", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"
    gate_synthesis = (
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
        f"## External Gate Status\n{GATE_ACTIONABLE_NO}\nnothing this diff can fix\n"
    )
    save_handover(ws, _make_verif_handover(
        ws, synthesis=gate_synthesis, chain_id=chain_id,
        handover_id=f"handover_{ws.task_id}_v1",
    ))
    stuck_q = OpenQuestion(
        id="non_actionable_feedback_inv_018",
        question="no code defect to fix",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
    )
    impl_fix_h = _make_impl_fix_handover(
        ws, chain_id=chain_id, handover_id=f"handover_{ws.task_id}_f1",
        synthesis="nothing to fix", open_questions=[stuck_q],
    )
    ws = increment_revisit_count(ws, "verification", chain_id)
    finding = detect_spiral(ws, impl_fix_h, "verification")
    assert finding is not None
    assert finding.kind == "non_actionable_finding"
