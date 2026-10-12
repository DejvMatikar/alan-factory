from __future__ import annotations

from alan.claude_cli import (
    BUILD_SUMMARIZE_SENTINEL,
    DISPATCH_PROPOSAL_SENTINEL,
    format_build_system_prompt,
)
from alan.claude_cli_grants import OWN_REPO_LABEL


def test_default_prompt_names_alan_repo_label() -> None:
    assert OWN_REPO_LABEL in format_build_system_prompt()


def test_prompt_names_a_passed_non_alan_repo_label() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert "acme/widget" in prompt
    assert OWN_REPO_LABEL not in prompt


def test_prompt_with_shop_stats_and_repo_label_names_both() -> None:
    prompt = format_build_system_prompt("shop stats block", "acme/widget")
    assert "acme/widget" in prompt
    assert "## Measured shop stats" in prompt


def test_prompt_never_claims_alan_workspaces_readability() -> None:
    for prompt in (
        format_build_system_prompt(),
        format_build_system_prompt("shop stats block"),
        format_build_system_prompt(repo_label="acme/widget"),
        format_build_system_prompt("shop stats block", "acme/widget"),
    ):
        assert "~/.alan/workspaces" not in prompt


def test_prompt_with_repo_label_still_carries_summarize_and_dispatch_contracts() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert BUILD_SUMMARIZE_SENTINEL in prompt
    assert DISPATCH_PROPOSAL_SENTINEL in prompt
    assert "## UX lens" in prompt
    assert "Read-only investigation posture" in prompt


def _dispatch_contract() -> str:
    prompt = format_build_system_prompt()
    return prompt[prompt.index("## EXTRACT_DISPATCH_YAML contract") :]


def test_dispatch_contract_names_the_recorded_clauses_key() -> None:
    assert "recorded_clauses:" in _dispatch_contract()


def test_dispatch_contract_restricts_recorded_clauses_to_prose_only_evidence() -> None:
    contract = _dispatch_contract()
    assert "ONLY possible evidence is prose" in contract
    assert "never anything a test, CI, or the running system can prove" in contract


def test_dispatch_contract_offers_an_empty_recorded_list_when_none() -> None:
    assert "`recorded_clauses: []` when none" in _dispatch_contract()


def test_dispatch_contract_carries_the_budget_guidance_sentence() -> None:
    contract = _dispatch_contract()
    assert "about a dozen" in contract
    assert "splitting" in contract


def test_dispatch_contract_carries_the_budget_guidance_sentence_exactly_once() -> None:
    assert _dispatch_contract().count("about a dozen") == 1


def test_recorded_clauses_key_sits_between_acceptance_clauses_and_the_quoted_scalar_note() -> None:
    contract = _dispatch_contract()
    assert (
        contract.index("acceptance_clauses:")
        < contract.index("recorded_clauses:")
        < contract.index("Use quoted scalars")
    )
