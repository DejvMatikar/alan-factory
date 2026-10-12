from __future__ import annotations

import pytest
from pydantic import ValidationError

from alan import dispatch_proposal as dispatch_proposal_mod
from alan.scope_audit import CounterCase

_COUNTER_CASE = CounterCase(
    against="This locks in scope before the shop has bandwidth to review it.",
    cheapest_80="Ship the smallest read-only slice first; defer the rest.",
    cost_estimate="insufficient data",
)


def test_dispatch_proposal_valid() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan", rationale="Ready to code.", counter_case=_COUNTER_CASE
    )
    assert p.team == "implementation_plan"
    assert p.instructions is None


def test_dispatch_proposal_empty_team_invalid() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal(team="", rationale="x", counter_case=_COUNTER_CASE)


def test_dispatch_proposal_empty_rationale_invalid() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal(
            team="implementation_plan", rationale="", counter_case=_COUNTER_CASE
        )


def test_dispatch_proposal_missing_counter_case_invalid() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            {"team": "implementation_plan", "rationale": "x"}
        )


def test_dispatch_proposal_accepts_legacy_action_field() -> None:
    legacy = dispatch_proposal_mod.DispatchProposal.model_validate(
        {
            "action": "implement",
            "rationale": "Carry the previous plan to code.",
            "counter_case": _COUNTER_CASE.model_dump(),
        }
    )
    assert legacy.team == "implementation_plan"
    assert legacy.instructions is None


def test_dispatch_proposal_legacy_action_passes_through_unknown_as_team() -> None:
    proposal = dispatch_proposal_mod.DispatchProposal.model_validate(
        {
            "action": "implementation_fix",
            "rationale": "x",
            "counter_case": _COUNTER_CASE.model_dump(),
        }
    )
    assert proposal.team == "implementation_fix"


def test_dispatch_proposal_legacy_action_non_string_falls_through() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate({"action": 42, "rationale": "x"})


def test_dispatch_proposal_team_wins_over_legacy_action() -> None:
    proposal = dispatch_proposal_mod.DispatchProposal.model_validate(
        {
            "action": "implement",
            "team": "pr_merge",
            "rationale": "x",
            "counter_case": _COUNTER_CASE.model_dump(),
        }
    )
    assert proposal.team == "pr_merge"


def test_dispatch_proposal_non_dict_falls_through() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate("not a dict")


def test_dispatch_proposal_instructions_round_trip() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_fix",
        rationale="Address Copilot comments.",
        instructions="Focus only on alan/foo.py:42.",
        counter_case=_COUNTER_CASE,
    )
    restored = dispatch_proposal_mod.DispatchProposal.model_validate(p.model_dump())
    assert restored.instructions == "Focus only on alan/foo.py:42."


def test_dispatch_proposal_scope_audit_round_trip() -> None:
    from alan.scope_audit import ScopeGapFinding

    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan",
        rationale="Address architecture decision #2.",
        scope_audit=[
            ScopeGapFinding(
                kind="handover_relative",
                summary="decisions 1 and 3 in the same area not addressed",
                references=["inv_002/handover.yaml"],
                suggested_action="needs_user_call",
            ),
        ],
        counter_case=_COUNTER_CASE,
    )
    restored = dispatch_proposal_mod.DispatchProposal.model_validate(p.model_dump())
    assert restored == p
    assert restored.scope_audit[0].kind == "handover_relative"


def test_dispatch_proposal_scope_audit_defaults_empty() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan", rationale="x", counter_case=_COUNTER_CASE
    )
    assert p.scope_audit == []


def test_dispatch_proposal_counter_case_round_trip() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan", rationale="x", counter_case=_COUNTER_CASE
    )
    restored = dispatch_proposal_mod.DispatchProposal.model_validate(p.model_dump())
    assert restored.counter_case == _COUNTER_CASE


def test_dispatch_proposal_title_and_acceptance_clauses_default_none_and_empty() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan", rationale="x", counter_case=_COUNTER_CASE
    )
    assert p.title is None
    assert p.acceptance_clauses == []


def test_dispatch_proposal_new_workspace_shape_accepted() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan",
        rationale="brief is ready",
        title="add-thing",
        acceptance_clauses=["clause one", "clause two"],
        counter_case=_COUNTER_CASE,
    )
    assert p.title == "add-thing"
    assert p.acceptance_clauses == ["clause one", "clause two"]


def test_dispatch_proposal_title_recovers_colon_clause_dict() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan",
        rationale="x",
        title="t",
        acceptance_clauses=[{"Clause text": "with detail"}],  # type: ignore[list-item]
        counter_case=_COUNTER_CASE,
    )
    assert p.acceptance_clauses == ["Clause text: with detail"]


def test_dispatch_proposal_title_present_with_wrong_team_rejected() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal(
            team="research",
            rationale="x",
            title="add-thing",
            counter_case=_COUNTER_CASE,
        )


def test_dispatch_proposal_title_blank_rejected() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal(
            team="implementation_plan",
            rationale="x",
            title="   ",
            counter_case=_COUNTER_CASE,
        )


def test_dispatch_proposal_acceptance_clauses_without_title_rejected() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal(
            team="implementation_plan",
            rationale="x",
            acceptance_clauses=["a clause with no title"],
            counter_case=_COUNTER_CASE,
        )


def test_dispatch_proposal_title_round_trip() -> None:
    p = dispatch_proposal_mod.DispatchProposal(
        team="implementation_plan",
        rationale="x",
        title="add-thing",
        acceptance_clauses=["a"],
        counter_case=_COUNTER_CASE,
    )
    restored = dispatch_proposal_mod.DispatchProposal.model_validate(p.model_dump())
    assert restored == p


def test_new_workspace_team_constant() -> None:
    assert dispatch_proposal_mod.NEW_WORKSPACE_TEAM == "implementation_plan"


def test_allowed_new_workspace_teams_context_key_constant() -> None:
    assert (
        dispatch_proposal_mod.ALLOWED_NEW_WORKSPACE_TEAMS_CONTEXT_KEY
        == "allowed_new_workspace_teams"
    )


def test_dispatch_proposal_context_allows_consultant_team_with_title() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        {
            "team": "brainstorming",
            "rationale": "x",
            "title": "explore-topic",
            "counter_case": _COUNTER_CASE.model_dump(),
        },
        context={"allowed_new_workspace_teams": frozenset({"brainstorming"})},
    )
    assert p.team == "brainstorming"
    assert p.title == "explore-topic"


def test_dispatch_proposal_no_context_still_requires_new_workspace_team() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            {
                "team": "brainstorming",
                "rationale": "x",
                "title": "explore-topic",
                "counter_case": _COUNTER_CASE.model_dump(),
            }
        )


def test_dispatch_proposal_empty_context_dict_defaults_to_new_workspace_team() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            {
                "team": "brainstorming",
                "rationale": "x",
                "title": "explore-topic",
                "counter_case": _COUNTER_CASE.model_dump(),
            },
            context={},
        )


def test_dispatch_proposal_context_rejects_team_outside_allowed_set() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            {
                "team": "research",
                "rationale": "x",
                "title": "explore-topic",
                "counter_case": _COUNTER_CASE.model_dump(),
            },
            context={"allowed_new_workspace_teams": frozenset({"brainstorming"})},
        )


def test_dispatch_proposal_build_door_unaffected_by_context_feature() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        {
            "team": "implementation_plan",
            "rationale": "x",
            "title": "add-thing",
            "counter_case": _COUNTER_CASE.model_dump(),
        }
    )
    assert p.team == "implementation_plan"


def test_map_legacy_dispatch_action_known_key_translates() -> None:
    result = dispatch_proposal_mod._map_legacy_dispatch_action(
        {"action": "implement", "rationale": "x"}
    )
    assert result["team"] == "implementation_plan"
    assert "action" not in result
    assert result["rationale"] == "x"


def test_map_legacy_dispatch_action_unknown_key_passes_through() -> None:
    result = dispatch_proposal_mod._map_legacy_dispatch_action(
        {"action": "future_team", "rationale": "x"}
    )
    assert result["team"] == "future_team"
    assert "action" not in result


def test_map_legacy_dispatch_action_non_string_returns_unchanged() -> None:
    typed: dict[str, object] = {"action": 42, "rationale": "x"}
    result = dispatch_proposal_mod._map_legacy_dispatch_action(typed)
    assert result is typed


def _recorded_proposal(**overrides: object) -> dict[str, object]:
    return {
        "team": "implementation_plan",
        "rationale": "x",
        "counter_case": _COUNTER_CASE.model_dump(),
        **overrides,
    }


def test_dispatch_proposal_recorded_clauses_default_empty() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(_recorded_proposal())
    assert p.recorded_clauses == []


def test_dispatch_proposal_recorded_clauses_without_title_rejected() -> None:
    with pytest.raises(
        ValidationError, match="acceptance_clauses and recorded_clauses require a title"
    ):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            _recorded_proposal(recorded_clauses=["the doc says X"])
        )


def test_dispatch_proposal_recorded_clauses_with_title_kept() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        _recorded_proposal(title="add-thing", recorded_clauses=["the doc says X"])
    )
    assert p.recorded_clauses == ["the doc says X"]


def test_dispatch_proposal_acceptance_clauses_without_title_rejected_beside_recorded() -> None:
    with pytest.raises(ValidationError):
        dispatch_proposal_mod.DispatchProposal.model_validate(
            _recorded_proposal(acceptance_clauses=["a clause"], recorded_clauses=[])
        )


def test_dispatch_proposal_neither_clause_list_and_no_title_is_valid() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        _recorded_proposal(acceptance_clauses=[], recorded_clauses=[])
    )
    assert p.title is None


def test_dispatch_proposal_legacy_shape_dumps_unchanged_plus_empty_recorded_clauses() -> None:
    legacy = _recorded_proposal(title="add-thing", acceptance_clauses=["a", "b"])
    dumped = dispatch_proposal_mod.DispatchProposal.model_validate(legacy).model_dump()
    assert dumped["acceptance_clauses"] == ["a", "b"]
    assert dumped["recorded_clauses"] == []


def test_dispatch_proposal_recorded_clauses_mapping_entry_heals_to_a_string() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        _recorded_proposal(title="t", recorded_clauses=[{"Docs say": "X"}])
    )
    assert p.recorded_clauses == ["Docs say: X"]


def test_dispatch_proposal_undeclared_sibling_keys_are_still_ignored() -> None:
    p = dispatch_proposal_mod.DispatchProposal.model_validate(
        _recorded_proposal(title="t", recorded_claus=["typo"], extra_key="x")
    )
    assert not hasattr(p, "recorded_claus")
    assert p.recorded_clauses == []
