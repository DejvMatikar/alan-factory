from __future__ import annotations

import pytest

from alan import claude_cli
from alan.claude_cli import (
    CONSULTANTS,
    DISPATCH_PROPOSAL_SENTINEL,
    EXPLORE_SUMMARIZE_SENTINEL,
    ConsultantInfo,
    format_explore_system_prompt,
    get_consultant,
)


def test_consultants_has_five_entries():
    assert len(CONSULTANTS) == 5


def test_consultant_keys_are_unique():
    keys = [c.key for c in CONSULTANTS]
    assert len(keys) == len(set(keys))


def test_all_consultant_fields_non_empty():
    for c in CONSULTANTS:
        assert c.key
        assert c.label
        assert c.description
        assert c.team
        assert c.steering_text


def test_consultant_keys_match_expected():
    keys = {c.key for c in CONSULTANTS}
    expected = {
        "brainstorming",
        "research",
        "architecture",
        "opportunity_scout",
        "ui_review",
    }
    assert keys == expected


def test_consultant_teams_are_known_teams():
    from alan.teams import TEAMS
    for c in CONSULTANTS:
        assert c.team in TEAMS, f"consultant {c.key} team {c.team!r} not in TEAMS"


def test_get_consultant_known():
    c = get_consultant("brainstorming")
    assert isinstance(c, ConsultantInfo)
    assert c.key == "brainstorming"


def test_get_consultant_unknown_raises():
    with pytest.raises(ValueError, match="unknown consultant key"):
        get_consultant("nonexistent")


def test_format_explore_system_prompt_contains_label():
    prompt = format_explore_system_prompt("brainstorming")
    assert "Brainstorming" in prompt


def test_format_explore_system_prompt_contains_sentinel():
    prompt = format_explore_system_prompt("architecture")
    assert EXPLORE_SUMMARIZE_SENTINEL in prompt


def test_format_explore_system_prompt_contains_scope_section():
    prompt = format_explore_system_prompt("research")
    assert "## Scope" in prompt


def test_format_explore_system_prompt_contains_expected_results_section():
    prompt = format_explore_system_prompt("opportunity_scout")
    assert "## Expected results" in prompt


def test_format_explore_system_prompt_contains_conditions_section():
    prompt = format_explore_system_prompt("opportunity_scout")
    assert "## Conditions and constraints" in prompt


def test_format_explore_system_prompt_contains_steering():
    prompt = format_explore_system_prompt("brainstorming")
    assert "## Conversational posture" in prompt


def test_format_explore_system_prompt_contains_no_impl():
    prompt = format_explore_system_prompt("architecture")
    assert "Read-only investigation posture" in prompt


def test_format_explore_system_prompt_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown consultant key"):
        format_explore_system_prompt("bogus")


def test_format_explore_system_prompt_label_substituted_correctly():
    prompt = format_explore_system_prompt("opportunity_scout")
    assert "Opportunity scout" in prompt
    assert "{label}" not in prompt
    assert "{{label}}" not in prompt


def test_brainstorming_consultant_carries_ux_lens_flag():
    c = get_consultant("brainstorming")
    assert c.carries_ux_lens is True
    assert "UX lens" not in c.steering_text


def test_architecture_consultant_carries_ux_lens_flag():
    c = get_consultant("architecture")
    assert c.carries_ux_lens is True
    assert "UX lens" not in c.steering_text


def test_ui_review_consultant_carries_ux_lens_flag():
    c = get_consultant("ui_review")
    assert c.carries_ux_lens is True
    assert "UX lens" not in c.steering_text


def test_research_and_opportunity_scout_do_not_carry_ux_lens():
    assert get_consultant("research").carries_ux_lens is False
    assert get_consultant("opportunity_scout").carries_ux_lens is False


def test_brainstorming_prompt_includes_shared_ux_lens():
    prompt = format_explore_system_prompt("brainstorming")
    assert claude_cli._CHAT_UX_LENS in prompt


def test_architecture_prompt_includes_shared_ux_lens():
    prompt = format_explore_system_prompt("architecture")
    assert claude_cli._CHAT_UX_LENS in prompt


def test_research_prompt_excludes_ux_lens():
    prompt = format_explore_system_prompt("research")
    assert "## UX lens" not in prompt


def test_opportunity_scout_prompt_excludes_ux_lens():
    prompt = format_explore_system_prompt("opportunity_scout")
    assert "## UX lens" not in prompt


def test_brainstorming_prompt_ux_lens_after_steering_before_summarize():
    prompt = format_explore_system_prompt("brainstorming")
    steering_idx = prompt.index("## Conversational posture")
    lens_idx = prompt.index("## UX lens")
    summarize_idx = prompt.index(f'"{EXPLORE_SUMMARIZE_SENTINEL}"')
    assert steering_idx < lens_idx < summarize_idx


def test_format_explore_system_prompt_ui_review_scopes_and_carries_lens():
    prompt = format_explore_system_prompt("ui_review")
    assert "whole app" in prompt
    assert "flow" in prompt
    assert "## UX lens" in prompt


def test_format_explore_system_prompt_contains_dispatch_proposal_sentinel():
    prompt = format_explore_system_prompt("brainstorming")
    assert DISPATCH_PROPOSAL_SENTINEL in prompt


def test_format_explore_system_prompt_dispatch_contract_hardcodes_consultant_team():
    prompt = format_explore_system_prompt("architecture")
    assert "team: architecture" in prompt


def test_format_explore_system_prompt_dispatch_contract_requires_counter_case():
    prompt = format_explore_system_prompt("brainstorming")
    assert "counter_case" in prompt
    assert claude_cli._COUNTER_CASE_REQUIRED_NOTE in prompt


def test_format_explore_system_prompt_dispatch_contract_status_ready_and_need_more_info():
    prompt = format_explore_system_prompt("research")
    assert "status: ready" in prompt
    assert "status: need_more_info" in prompt


def test_format_explore_system_prompt_dispatch_contract_after_summarize_contract():
    prompt = format_explore_system_prompt("opportunity_scout")
    summarize_idx = prompt.index(f'"{EXPLORE_SUMMARIZE_SENTINEL}"')
    dispatch_idx = prompt.index("## EXTRACT_DISPATCH_YAML contract")
    assert summarize_idx < dispatch_idx


def test_format_explore_system_prompt_dispatch_contract_per_consultant_team():
    for consultant_key in (
        "brainstorming",
        "research",
        "architecture",
        "opportunity_scout",
        "ui_review",
    ):
        team = get_consultant(consultant_key).team
        prompt = format_explore_system_prompt(consultant_key)
        assert f"team: {team}" in prompt
