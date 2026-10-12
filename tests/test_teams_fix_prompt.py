from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan.engine_presets import ios_preset
from alan.handover import Handover
from alan.teams import _fix_gate_checks, _fix_prompt
from alan.teams import implementation_fix as fix_mod


def test_scope_deference_notice_names_the_instructions_section_and_the_ceiling() -> None:
    assert "## User dispatch instructions" in _fix_prompt.SCOPE_DEFERENCE_NOTICE
    assert "## Forbidden actions" in _fix_prompt.SCOPE_DEFERENCE_NOTICE
    assert "## Absolute prohibitions" in _fix_prompt.SCOPE_DEFERENCE_NOTICE
    assert "never overrides" in _fix_prompt.SCOPE_DEFERENCE_NOTICE


def test_render_forbidden_block_default_only_omits_absolute_heading() -> None:
    block = _fix_prompt.render_forbidden_block(("do not do X",), ())
    assert "## Forbidden actions" in block
    assert "- do not do X" in block
    assert "## Absolute prohibitions" not in block


def test_render_forbidden_block_with_absolute_adds_second_heading() -> None:
    block = _fix_prompt.render_forbidden_block(
        ("do not do X",), ("do not do Y", "do not do Z")
    )
    assert "## Forbidden actions" in block
    assert "- do not do X" in block
    assert "## Absolute prohibitions" in block
    assert "- do not do Y" in block
    assert "- do not do Z" in block
    assert "cannot be overridden by any instruction" in block
    forbidden_idx = block.index("## Forbidden actions")
    absolute_idx = block.index("## Absolute prohibitions")
    assert forbidden_idx < absolute_idx


def test_render_forbidden_block_strips_and_skips_blank_entries() -> None:
    block = _fix_prompt.render_forbidden_block(
        ("  do not do X  ", "", "   "), ("  do not do Y  ",)
    )
    assert "- do not do X\n" in block or block.endswith("- do not do X")
    assert "- do not do Y" in block


def test_render_forbidden_block_absolute_bullets_survive_a_widened_default_list() -> None:
    widened_default = ("do not do X", "some instruction widened this")
    block = _fix_prompt.render_forbidden_block(widened_default, ("do not amend prior commits",))
    assert "- do not amend prior commits" in block
    assert "## Absolute prohibitions" in block


def test_absolute_prohibitions_preamble_binds_this_runs_own_output() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    preamble = block.split("## Absolute prohibitions\n", 1)[1]
    assert preamble.startswith("These bind what this run writes")


def test_absolute_prohibitions_preamble_directs_report_not_undo_for_preexisting_content() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    assert "not yours to undo" in block
    assert "## Summary" in block
    assert "## Open questions" in block
    assert "leave it in place" in block
    assert "whether or not you believe it legitimate" in block
    assert "unless" not in block.lower()


def test_absolute_prohibitions_preamble_preserves_the_fleet_own_commit_revert_path() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    assert "## Fleet commit ledger" in block
    assert "this workspace's own fleet runs" in block


def test_absolute_prohibitions_preamble_names_both_decline_routes() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    assert "[BLOCKING]" in block
    assert "non_actionable_feedback.yaml" in block


def test_render_forbidden_block_without_absolute_entries_omits_the_scoping_clause() -> None:
    block = _fix_prompt.render_forbidden_block(("do not do X",), ())
    assert "these bind what this run writes" not in block.lower()
    assert "## Fleet commit ledger" not in block


def test_absolute_prohibitions_preamble_requires_a_sha_match_to_authorize_revert() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    assert "exact SHA" in block
    assert "subject match by itself never authorizes a revert" in block


def test_absolute_prohibitions_preamble_treats_a_rebased_lookalike_as_unverified() -> None:
    block = _fix_prompt.render_forbidden_block((), ("do not do Y",))
    assert "reconcile rebase" in block
    assert "treat the look-alike commit as unverified" in block
    assert "decline to revert it" in block


def _assembled_prompts(tmp_path: Path) -> list[str]:
    return [
        fix_mod._SYSTEM_PROMPT,  # noqa: SLF001
        fix_mod._GENERIC_FIX_STATIC_TAIL,  # noqa: SLF001
        fix_mod.render_system_prompt(ios_preset(), tmp_path),
    ]


def test_non_actionable_guidance_keeps_the_yaml_fence_the_all_findings_caveat_and_routing() -> None:
    guidance = _fix_prompt.NON_ACTIONABLE_GUIDANCE
    assert "```yaml non_actionable_feedback.yaml\nreason: <" in guidance
    assert "Only emit this block when ALL findings are non-actionable" in guidance
    assert "partial cases must use the normal commit flow" in guidance
    assert "routes the workspace to await_human" in guidance


def test_non_actionable_guidance_no_longer_invites_ci_or_environment_classification() -> None:
    guidance = _fix_prompt.NON_ACTIONABLE_GUIDANCE
    assert "CI flake, environment issue" not in guidance
    assert "ambiguous request requiring human clarification" in guidance


def test_non_actionable_guidance_states_the_flaky_and_divergence_rule_engine_neutrally() -> None:
    guidance = _fix_prompt.NON_ACTIONABLE_GUIDANCE
    assert 'Claim "flaky" only where existing evidence supports it' in guidance
    assert "red in CI while your local gate passes" in guidance
    assert "divergence" in guidance
    assert "do not treat a passing local run as a refutation" in guidance
    assert "alan" not in guidance.lower()
    assert "pytest" not in guidance


def test_output_sections_guidance_names_the_four_sections() -> None:
    guidance = _fix_prompt.OUTPUT_SECTIONS_GUIDANCE
    for heading in ("## Summary", "## Decisions", "## Commits", "## Open questions"):
        assert heading in guidance


def test_every_assembled_fix_prompt_carries_each_guidance_block_exactly_once(
    tmp_path: Path,
) -> None:
    for prompt in _assembled_prompts(tmp_path):
        assert prompt.count(_fix_prompt.NON_ACTIONABLE_GUIDANCE) == 1
        assert prompt.count(_fix_prompt.OUTPUT_SECTIONS_GUIDANCE) == 1
        assert prompt.count("red in CI while your local gate passes") == 1
        assert prompt.count('Claim "flaky" only where') == 1
        assert prompt.count("NON-ACTIONABLE FEEDBACK") == 1
        assert prompt.count("Output sections in your final response") == 1


def test_no_assembled_fix_prompt_invites_unconditional_non_actionable_classification(
    tmp_path: Path,
) -> None:
    for prompt in _assembled_prompts(tmp_path):
        assert "CI flake, environment issue" not in prompt
        assert "```yaml non_actionable_feedback.yaml" in prompt


def test_the_failing_checks_block_does_not_restate_the_rule() -> None:
    prior = Handover(
        handover_id="h", handover_type="forward", task_id="t", from_team="verification",
        from_invocation_id="i", iteration=1, timestamp_utc=datetime.now(UTC),
        full_synthesis="x", pr_number=1,
    )
    block = _fix_gate_checks.gate_failures_block(prior, _fix_gate_checks.FailingChecks())
    assert "divergence" not in block
    assert '"flaky"' not in block
