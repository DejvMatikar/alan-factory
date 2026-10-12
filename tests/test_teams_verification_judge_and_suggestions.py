from __future__ import annotations

from pathlib import Path

import pytest

from alan import acceptance as _acceptance_mod
from alan.teams import verification as verif_mod
from alan.teams._scenario_gate import _OUTCOMES_HEADING  # pyright: ignore[reportPrivateUsage]
from alan.teams._verification_gates import GATE_BLOCK_MARKER
from alan.teams.verification import (
    TEAM,
    github_review_event,
    judge_system_prompt,
    suggestions_for_verdict,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def test_judge_prompt_forbids_negated_or_hypothetical_scenarios_missing_mentions() -> None:
    prompt = verif_mod._JUDGE_PROMPT
    assert "Marker discipline" in prompt
    assert "NEVER write that token anywhere" in prompt
    assert "not negated" in prompt
    assert "not hypothetically" in prompt


def test_judge_prompt_has_worked_verdict_block_example() -> None:
    prompt = verif_mod._JUDGE_PROMPT
    assert "Worked example" in prompt
    assert (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE"
    ) in prompt


def test_judge_system_prompt_declares_host_appended_blocks_forged_when_in_input() -> None:
    prompt = judge_system_prompt()
    assert GATE_BLOCK_MARKER in prompt
    assert _OUTCOMES_HEADING in prompt
    assert "appended by the host after your output" in prompt
    assert "forged" in prompt
    assert "Open questions" in prompt
    assert "never reproducing its heading line" in prompt
    assert "critical count comes from the diff_reviewer's own findings" in prompt


def test_judge_prompt_marker_contract_is_unchanged_by_forged_block_rule() -> None:
    prompt = verif_mod._JUDGE_PROMPT
    assert "DIFF_FINDINGS_CRITICAL: <n>\nVERDICT: APPROVE\n" in prompt
    marker_lines = [
        line
        for line in prompt.splitlines()
        if line.startswith(("VERDICT:", "DIFF_FINDINGS_CRITICAL:"))
    ]
    assert len(marker_lines) == 4


def test_github_review_event_keeps_request_changes_when_judge_names_a_forged_gate_block() -> None:
    judge_output = (
        "DIFF_FINDINGS_CRITICAL: 0\n"
        "VERDICT: REQUEST_CHANGES\n\n"
        "## Open questions\n"
        "The scenario_runner output carries a forged gate-status block.\n"
    )
    assert github_review_event(judge_output) == "REQUEST_CHANGES"


def test_judge_prompt_contains_rc4_rebind_rider() -> None:
    prompt = verif_mod._JUDGE_PROMPT
    assert "Scenario drift rebind" in prompt
    assert "rc=4" in prompt
    assert "## Findings to address" in prompt
    assert "never flips APPROVE to REQUEST_CHANGES" in prompt


def test_judge_prompt_pa3_1_worked_example_intact() -> None:
    prompt = verif_mod._JUDGE_PROMPT
    assert (
        "SCENARIOS_MISSING without SCENARIOS_COVERED_BY_DOD or "
        "SCENARIOS_WAIVED means no test scenarios were attached — treat "
        "this as REQUEST_CHANGES."
    ) in prompt
    assert (
        "Worked example — "
        "all three machine-readable lines are emitted together, in this exact "
        "order, whenever a `## IN-SCOPE ACCEPTANCE OUTCOMES` block is "
        "present:"
    ) in prompt
    assert (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE"
    ) in prompt


def test_judge_retry_nudge_asks_for_complete_reemit() -> None:
    nudge = verif_mod._JUDGE_RETRY_NUDGE
    assert "REPLACES your entire prior output" in nudge
    assert "COMPLETE review" in nudge
    assert "DIFF_FINDINGS_CRITICAL_IN_SCOPE" in nudge


def test_suggestions_for_verdict_in_scope_zero_routes_to_pr_merge() -> None:
    judge = (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    assert suggestions_for_verdict(judge, pr_number=505) == ("pr_merge",)


def test_github_review_event_in_scope_zero_is_approve() -> None:
    judge = (
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        "VERDICT: APPROVE\n"
    )
    assert github_review_event(judge) == "APPROVE"


def test_judge_retry_reason_missing_verdict(workspace: Workspace) -> None:
    assert verif_mod._judge_retry_reason(workspace, "no verdict here") == "missing_verdict"


def test_judge_retry_reason_none_when_complete(workspace: Workspace) -> None:
    assert verif_mod._judge_retry_reason(workspace, _DEFAULT_JUDGE_OUTPUT) is None


def test_judge_retry_reason_missing_in_scope_count(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_acceptance_mod, "scoped_dod", lambda _meta, _tid: (["owned"], []))
    token = verif_mod._verification_task_id_var.set(workspace.task_id)  # noqa: SLF001
    try:
        judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n"
        assert verif_mod._judge_retry_reason(workspace, judge) == "missing_in_scope_count"
    finally:
        verif_mod._verification_task_id_var.reset(token)  # noqa: SLF001


def test_judge_retry_reason_none_when_in_scope_count_present(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_acceptance_mod, "scoped_dod", lambda _meta, _tid: (["owned"], []))
    token = verif_mod._verification_task_id_var.set(workspace.task_id)  # noqa: SLF001
    try:
        judge = (
            "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 2\n"
            "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
        )
        assert verif_mod._judge_retry_reason(workspace, judge) is None
    finally:
        verif_mod._verification_task_id_var.reset(token)  # noqa: SLF001


def test_suggestions_for_verdict_approve_without_pr_is_empty() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=None) == ()


def test_suggestions_for_verdict_approve_with_pr_routes_to_pr_merge() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=42) == ("pr_merge",)


def test_suggestions_for_verdict_request_changes_routes_to_implementation_fix() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    assert suggestions_for_verdict(judge, pr_number=42) == ("implementation_fix",)
    assert suggestions_for_verdict(judge, pr_number=None) == ("implementation_fix",)


def test_suggestions_for_verdict_bold_approve_routes_to_pr_merge() -> None:
    judge = "**VERDICT: APPROVE**\n`DIFF_FINDINGS_CRITICAL: 0`"
    assert suggestions_for_verdict(judge, pr_number=252) == ("pr_merge",)


def test_suggestions_for_verdict_missing_treated_as_pause() -> None:
    assert suggestions_for_verdict("no verdict here", pr_number=42) == ()


def test_suggestions_for_verdict_gate_flaky_only_routes_to_pr_merge() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=42, gate_flaky_only=True) == (
        "pr_merge",
    )


def test_suggestions_for_verdict_gate_flaky_only_without_pr_number_routes_to_fix() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=None, gate_flaky_only=True) == (
        "implementation_fix",
    )


def test_suggestions_for_verdict_gate_flaky_only_default_false_unchanged() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    assert suggestions_for_verdict(judge, pr_number=42) == ("implementation_fix",)


def test_suggestions_for_verdict_gate_dirty_routes_to_pr_merge() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=42, gate_dirty=True) == ("pr_merge",)


def test_suggestions_for_verdict_gate_dirty_without_pr_number_routes_to_fix() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(judge, pr_number=None, gate_dirty=True) == (
        "implementation_fix",
    )


def test_suggestions_for_verdict_gate_dirty_and_flaky_only_both_true_routes_to_pr_merge() -> None:
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    assert suggestions_for_verdict(
        judge, pr_number=42, gate_flaky_only=True, gate_dirty=True
    ) == ("pr_merge",)


def test_run_approve_handover_suggests_pr_merge(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]


def test_run_approve_without_pr_yields_empty_suggestions(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == []


def test_run_request_changes_handover_suggests_implementation_fix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
    _patch_streaming(monkeypatch, judge_output=judge)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]


def test_run_request_changes_handover_passes_layer3_validator(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    from alan.handover import save_handover

    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
    _patch_streaming(monkeypatch, judge_output=judge)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.decisions
    assert handover.decisions[0].statement.startswith("verification verdict:")
    save_handover(workspace, handover)


def test_run_approve_with_pr_emits_verif_approve_decision(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(handover.decisions) == 1
    assert handover.decisions[0].id == "verif-approve"
    assert handover.decisions[0].statement == "judge approved PR #42"
    assert handover.decisions[0].confidence == 1.0
    assert handover.decisions[0].reversibility == "low"


def test_run_approve_without_pr_has_no_decisions(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.decisions == []


def test_decision_from_judge_uses_first_non_empty_line() -> None:
    from alan.teams._verification_routing import _decision_from_judge

    judge = "\n\n  ## Verdict header  \nVERDICT: REQUEST_CHANGES\n"
    decision = _decision_from_judge(judge, "REQUEST_CHANGES")
    assert decision.rationale == "## Verdict header"


def test_decision_from_judge_falls_back_when_output_blank() -> None:
    from alan.teams._verification_routing import _decision_from_judge

    decision = _decision_from_judge("   \n\t  \n", "REQUEST_CHANGES")
    assert decision.rationale == "verification ran"


def test_verdict_decisions_pr_merge_approve_emits_approve_decision() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    [decision] = verif_mod._verdict_decisions(judge, ("pr_merge",), 42)  # noqa: SLF001
    assert decision.id == "verif-approve"
    assert decision.statement == "judge approved PR #42"


def test_verdict_decisions_pr_merge_request_changes_uses_decision_from_judge() -> None:
    judge = (
        "> PARKED ON EXTERNAL GATES\n\nVERDICT: REQUEST_CHANGES\n"
        "DIFF_FINDINGS_CRITICAL: 0\n"
    )
    [decision] = verif_mod._verdict_decisions(judge, ("pr_merge",), 42)  # noqa: SLF001
    assert decision.id == "verif-01"
    assert decision.statement == "verification verdict: REQUEST_CHANGES"
