from __future__ import annotations

_SNAPSHOT_BASE_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Bash(uv run:*)",
    "Bash(npm run:*)",
    "Bash(npm --prefix:*)",
    "Bash(npx:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git show:*)",
    "Bash(git blame:*)",
    "Bash(git ls-files:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(pwd:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(diff:*)",
    "Bash(nl:*)",
    "Bash(git rev-parse:*)",
    "Bash(git merge-base:*)",
    "Bash(git rev-list:*)",
    "Bash(git ls-tree:*)",
    "Bash(git cat-file:*)",
    "Bash(git describe:*)",
    "Bash(git diff-tree:*)",
)

_SNAPSHOT_IMPL_EXTRAS: tuple[str, ...] = (
    "Edit",
    "Write",
    "Bash(git:*)",
    "Bash(uv:*)",
    "Bash(npm:*)",
    "Bash(node:*)",
    "Bash(python:*)",
    "Bash(python3:*)",
    "Bash(pytest:*)",
    "Bash(mkdir:*)",
    "Bash(mv:*)",
    "Bash(cp:*)",
    "Bash(rm:*)",
)

_SNAPSHOT_TRANSITION_POLICY: dict[str, frozenset[str]] = {
    "research": frozenset({"brainstorming", "architecture", "implementation_plan"}),
    "brainstorming": frozenset({"research", "architecture", "implementation_plan"}),
    "architecture": frozenset({"implementation_plan", "research"}),
    "implementation_plan": frozenset(
        {"brainstorming", "research", "decomposer", "architecture"}
    ),
    "decomposer": frozenset({"implementation", "architecture", "research"}),
    "implementation": frozenset({"verification"}),
    "implementation_fix": frozenset({"verification"}),
    "verification": frozenset({"implementation_fix", "pr_merge"}),
    "pr_merge": frozenset({"implementation_fix", "deploy_verify"}),
    "deploy_verify": frozenset(),
    "maintenance": frozenset(),
    "triage": frozenset(),
    "opportunity_scout": frozenset({"research", "brainstorming"}),
    "ui_review": frozenset(),
}


def test_base_allowed_tools_matches_snapshot() -> None:
    from alan.teams._base import BASE_ALLOWED_TOOLS

    assert BASE_ALLOWED_TOOLS == _SNAPSHOT_BASE_TOOLS


def test_impl_extras_matches_snapshot() -> None:
    from alan.teams.implementation import _IMPL_EXTRAS

    assert _IMPL_EXTRAS == _SNAPSHOT_IMPL_EXTRAS


def test_allowed_tools_is_base_plus_impl_extras() -> None:
    from alan.teams._base import BASE_ALLOWED_TOOLS
    from alan.teams.implementation import _IMPL_EXTRAS, ALLOWED_TOOLS

    assert ALLOWED_TOOLS == BASE_ALLOWED_TOOLS + _IMPL_EXTRAS


def test_render_environment_block_with_explicit_config() -> None:
    from alan.engine_config import _DEFAULT
    from alan.teams.implementation import render_environment_block

    rendered = render_environment_block(_DEFAULT)
    assert "uv sync --locked --extra dev" in rendered
    assert "npm --prefix alan/web ci" in rendered
    assert "uv run --no-active pytest" in rendered
    assert "npm --prefix alan/web run typecheck" in rendered
    assert "npm --prefix alan/web run lint:dup" in rendered


def test_environment_block_contains_all_non_special_gate_commands() -> None:
    from alan.engine_config import _DEFAULT
    from alan.teams.implementation import ENVIRONMENT_BLOCK

    for cmd in _DEFAULT.gate_commands:
        joined = " ".join(cmd)
        if "lizard" not in joined and "pip-audit" not in joined:
            assert joined in ENVIRONMENT_BLOCK, f"{joined!r} not in ENVIRONMENT_BLOCK"

    assert "uv run --no-active lizard --CCN 7 --length 60 --languages python" in ENVIRONMENT_BLOCK
    assert "uv run --no-active pip-audit --strict --disable-pip" in ENVIRONMENT_BLOCK


def test_environment_block_embedded_in_system_prompt() -> None:
    from alan.teams.implementation import _SYSTEM_PROMPT, ENVIRONMENT_BLOCK

    assert ENVIRONMENT_BLOCK in _SYSTEM_PROMPT


def test_transition_policy_matches_snapshot() -> None:
    from alan.router import TEAM_TRANSITION_POLICY

    assert TEAM_TRANSITION_POLICY == _SNAPSHOT_TRANSITION_POLICY


def test_transition_policy_has_14_keys() -> None:
    from alan.router import TEAM_TRANSITION_POLICY

    assert len(TEAM_TRANSITION_POLICY) == 14
    assert set(TEAM_TRANSITION_POLICY) == set(_SNAPSHOT_TRANSITION_POLICY)


def test_implementer_model_effort_from_config() -> None:
    from alan.engine_config import _DEFAULT
    from alan.teams import implementation, implementation_fix

    assert implementation._MODEL == _DEFAULT.roles["implementer"].model
    assert implementation._EFFORT == _DEFAULT.roles["implementer"].effort
    assert implementation_fix._MODEL == _DEFAULT.roles["implementer"].model
    assert implementation_fix._EFFORT == _DEFAULT.roles["implementer"].effort


def test_default_flow_policy_internal_only_teams_matches_registry() -> None:
    from alan.engine_config import _DEFAULT
    from alan.teams import INTERNAL_ONLY_TEAMS

    assert _DEFAULT.flow_policy is not None
    assert _DEFAULT.flow_policy.internal_only_teams == INTERNAL_ONLY_TEAMS


def test_default_flow_policy_revisit_cap_matches_loop_guard() -> None:
    from alan.engine_config import _DEFAULT
    from alan.loop_guard import REVISIT_CAP

    assert _DEFAULT.flow_policy is not None
    assert _DEFAULT.flow_policy.revisit_cap == REVISIT_CAP


def test_default_flow_policy_restricted_predecessors_covers_internal_only() -> None:
    from alan.engine_config import _DEFAULT

    assert _DEFAULT.flow_policy is not None
    assert (
        set(_DEFAULT.flow_policy.restricted_predecessors.keys())
        == _DEFAULT.flow_policy.internal_only_teams
    )


def test_default_flow_policy_internal_only_disjoint_from_initial_dispatchable() -> None:
    from alan.engine_config import _DEFAULT
    from alan.teams import INITIAL_DISPATCHABLE_TEAMS

    assert _DEFAULT.flow_policy is not None
    assert _DEFAULT.flow_policy.internal_only_teams.isdisjoint(INITIAL_DISPATCHABLE_TEAMS)


def test_brief_refine_registered_internal_only_and_absent_from_transition_graph() -> None:
    from alan.engine_config import _DEFAULT
    from alan.router import TEAM_TRANSITION_POLICY
    from alan.teams import INTERNAL_ONLY_TEAMS, MANUALLY_DISPATCHABLE_TEAMS, TEAMS

    assert "brief_refine" in TEAMS
    assert "brief_refine" in INTERNAL_ONLY_TEAMS
    assert "brief_refine" not in MANUALLY_DISPATCHABLE_TEAMS
    assert _DEFAULT.flow_policy is not None
    assert _DEFAULT.flow_policy.restricted_predecessors["brief_refine"] == frozenset()
    assert "brief_refine" not in TEAM_TRANSITION_POLICY
    assert "brief_refine" not in _DEFAULT.transition_graph
