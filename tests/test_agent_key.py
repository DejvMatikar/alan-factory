from __future__ import annotations

import pytest

from alan import agent_key
from alan.agent_key import AgentKey
from alan.teams import TEAMS
from alan.teams import _orchestrated as orchestrated


def test_the_id_combines_kind_and_name() -> None:
    assert AgentKey("chat", "build").id == "chat:build"
    assert AgentKey("team_role", "verification/judge").id == "team_role:verification/judge"


def test_keys_are_hashable_value_objects_with_a_stable_order() -> None:
    assert AgentKey("chat", "a") == AgentKey("chat", "a")
    assert len({AgentKey("chat", "a"), AgentKey("chat", "a")}) == 1
    assert sorted([AgentKey("chat", "b"), AgentKey("chat", "a")])[0].name == "a"


@pytest.mark.parametrize(
    ("team", "role", "expected"),
    [
        ("research", "orchestrator", "orchestrator"),
        ("research", "synthesizer", "synthesizer"),
        ("brainstorming", "verifier", "verifier"),
        ("opportunity_scout", "market_sizer", "worker"),
        ("ui_review", "accessibility_reviewer", "worker"),
        ("verification", "judge", "judge"),
        ("implementation", "implementer", "implementer"),
        ("architecture", "devil_advocate", "devil_advocate"),
    ],
)
def test_canonical_team_role_collapses_only_orchestrated_workers(
    team: str, role: str, expected: str
) -> None:
    assert agent_key.canonical_team_role(team, role) == expected


def test_team_role_builds_a_team_scoped_key() -> None:
    assert agent_key.team_role("research", "any_dynamic_role") == AgentKey(
        "team_role", "research/worker"
    )
    assert agent_key.team_role("verification", "judge") == AgentKey(
        "team_role", "verification/judge"
    )


def test_named_constructors() -> None:
    assert agent_key.explore_chat("research") == AgentKey("chat", "explore/research")
    assert agent_key.legacy_planning("architect") == AgentKey(
        "team_role", "legacy_planning/architect"
    )
    assert agent_key.driver_loop("alan-issue-driver") == AgentKey(
        "operator_launched", "driver_loop/alan-issue-driver"
    )
    assert agent_key.operator_session("fleet") == AgentKey("operator_launched", "operator/fleet")


def test_static_keys_are_unique_and_use_known_kinds() -> None:
    assert len(set(agent_key.STATIC_KEYS)) == len(agent_key.STATIC_KEYS)
    assert {key.kind for key in agent_key.STATIC_KEYS} <= set(agent_key.AGENT_KINDS)
    assert agent_key.BUILD_CHAT in agent_key.STATIC_KEYS
    assert agent_key.IMPLEMENTATION_DRIFT_BACKSTOP in agent_key.STATIC_KEYS


def test_orchestrated_teams_match_the_registered_orchestrated_team_instances() -> None:
    registered = {
        name
        for name, team in TEAMS.items()
        if isinstance(team, orchestrated.OrchestratedTeam)
    }
    assert registered == set(agent_key.ORCHESTRATED_TEAMS)


def test_orchestrated_fixed_roles_match_the_reserved_role_names() -> None:
    assert agent_key.ORCHESTRATED_FIXED_ROLES == orchestrated._RESERVED_ROLES  # noqa: SLF001
