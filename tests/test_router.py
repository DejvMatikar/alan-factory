from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alan.handover import Handover, OpenQuestion
from alan.park import ParkReason, park_reason_for_decision
from alan.router import TEAM_TRANSITION_POLICY, RouterDecision, select_next_team
from alan.teams import TEAMS
from alan.teams._base import SingleAgentTeam, Team
from alan.terminal_outcome import (
    _FALLBACK_ESCALATED,  # pyright: ignore[reportPrivateUsage]
    _REASON_MAX,  # pyright: ignore[reportPrivateUsage]
    _first_line,  # pyright: ignore[reportPrivateUsage]
)


def _make_handover(
    from_team: str,
    *,
    handover_type: str = "forward",
    to_team_suggestions: list[str] | None = None,
    next_hop: str | None = None,
    open_questions: list[OpenQuestion] | None = None,
    full_synthesis: str = "x",
) -> Handover:
    return Handover(
        handover_id=f"handover_t1_{from_team}",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="t1",
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        next_hop=next_hop,
        open_questions=open_questions or [],
        full_synthesis=full_synthesis,
    )


def _make_blocking_question(resolver_role: str, qid: str = "q1") -> OpenQuestion:
    return OpenQuestion(
        id=qid,
        question="?",
        resolver_role=resolver_role,
        blocking=True,
        prior_attempts=[],
    )


def _make_non_blocking_question(resolver_role: str = "research") -> OpenQuestion:
    return OpenQuestion(
        id="q-soft",
        question="non-blocking",
        resolver_role=resolver_role,
        blocking=False,
        prior_attempts=[],
    )


def test_policy_matches_research_synthesis() -> None:
    expected: dict[str, frozenset[str]] = {
        "research": frozenset({"brainstorming", "architecture", "implementation_plan"}),
        "brainstorming": frozenset(
            {"research", "architecture", "implementation_plan"}
        ),
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
    assert TEAM_TRANSITION_POLICY == expected


def test_escalation_handover_returns_escalate() -> None:
    h = _make_handover("implementation_plan", handover_type="escalation")
    d = select_next_team(h, TEAMS)
    assert d.kind == "escalate"


def test_escalation_blocker_detail_is_first_line_of_synthesis() -> None:
    h = _make_handover(
        "implementation_plan",
        handover_type="escalation",
        full_synthesis="PR #42 is clean, mergeable and green.\nmore detail below",
    )
    d = select_next_team(h, TEAMS)
    assert d.blockers[0].kind == "reason"
    assert d.blockers[0].name == "escalation"
    assert d.blockers[0].detail == "PR #42 is clean, mergeable and green."


def test_escalation_blocker_detail_falls_back_when_synthesis_is_empty() -> None:
    h = _make_handover(
        "implementation_plan", handover_type="escalation", full_synthesis=""
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "escalate"
    assert d.blockers[0].detail == _FALLBACK_ESCALATED


def test_escalation_blocker_detail_falls_back_when_synthesis_is_whitespace_only() -> None:
    h = _make_handover(
        "implementation_plan", handover_type="escalation", full_synthesis="   \n\t "
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "escalate"
    assert d.blockers[0].detail == _FALLBACK_ESCALATED


def test_escalation_blocker_detail_capped_at_200_chars() -> None:
    long_line = "a" * 300
    h = _make_handover(
        "implementation_plan", handover_type="escalation", full_synthesis=long_line
    )
    d = select_next_team(h, TEAMS)
    assert len(d.blockers[0].detail) == _REASON_MAX
    assert d.blockers[0].detail == _first_line(long_line)


def test_escalation_reason_names_stage_and_cause() -> None:
    h = _make_handover(
        "implementation_plan",
        handover_type="escalation",
        full_synthesis="PR is clean, mergeable and green.",
    )
    d = select_next_team(h, TEAMS)
    assert d.reason is not None
    assert "implementation_plan" in d.reason
    assert "PR is clean, mergeable and green." in d.reason


def test_escalation_blocker_carries_no_instruction_and_parks_as_escalated() -> None:
    h = _make_handover("implementation_plan", handover_type="escalation")
    d = select_next_team(h, TEAMS)
    assert d.blockers[0].instruction is None
    assert d.block_class is None
    assert park_reason_for_decision(d.kind, d.block_class) is ParkReason.ESCALATED


def test_blocking_question_routes_to_team_resolver_when_allowed() -> None:
    h = _make_handover(
        "implementation_plan",
        open_questions=[_make_blocking_question("brainstorming")],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "brainstorming"
    assert d.blockers == ()


def test_blocking_question_with_human_resolver_returns_await_human() -> None:
    h = _make_handover(
        "implementation_plan",
        open_questions=[_make_blocking_question("human")],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert len(d.blockers) == 1
    assert d.blockers[0].kind == "reason"
    assert d.blockers[0].name == "q1"
    assert d.blockers[0].detail == "?"


def test_blocking_question_with_unknown_resolver_returns_await_human() -> None:
    h = _make_handover(
        "implementation_plan",
        open_questions=[_make_blocking_question("nonexistent_role")],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert len(d.blockers) == 1
    assert d.blockers[0].kind == "reason"
    assert d.blockers[0].name == "q1"
    assert d.blockers[0].detail == "?"


def test_blocking_question_disallowed_by_policy_returns_await_human() -> None:
    h = _make_handover(
        "verification",
        open_questions=[_make_blocking_question("research")],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert len(d.blockers) == 1
    assert d.blockers[0].kind == "reason"
    assert d.blockers[0].name == "q1"
    assert d.blockers[0].detail == "?"


def test_blocking_question_detail_truncated_to_500_chars() -> None:
    long_question = "x" * 600
    h = _make_handover(
        "implementation_plan",
        open_questions=[
            OpenQuestion(
                id="q-long",
                question=long_question,
                resolver_role="human",
                blocking=True,
            )
        ],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert len(d.blockers) == 1
    assert d.blockers[0].detail == long_question[:500]
    assert len(d.blockers[0].detail) == 500


def test_non_blocking_question_does_not_block_forward_progress() -> None:
    h = _make_handover(
        "research",
        to_team_suggestions=["implementation_plan"],
        open_questions=[_make_non_blocking_question()],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "implementation_plan"


def test_next_hop_when_allowed_takes_priority() -> None:
    h = _make_handover(
        "implementation_plan",
        to_team_suggestions=["implementation"],
        next_hop="research",
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "research"


def test_next_hop_when_disallowed_falls_through_to_suggestions() -> None:
    h = _make_handover(
        "research",
        to_team_suggestions=["implementation_plan"],
        next_hop="implementation",
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "implementation_plan"


def test_first_allowed_suggestion_wins() -> None:
    h = _make_handover(
        "implementation_plan",
        to_team_suggestions=["ghost", "decomposer", "architecture"],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "decomposer"


def test_disallowed_suggestion_skipped() -> None:
    h = _make_handover(
        "research",
        to_team_suggestions=["implementation", "implementation_plan"],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "implementation_plan"


def test_maintenance_terminal_team_returns_done() -> None:
    h = _make_handover("maintenance")
    d = select_next_team(h, TEAMS)
    assert d.kind == "done"
    assert d.reason == "terminal team"


def test_no_valid_suggestion_returns_await_human() -> None:
    h = _make_handover("implementation", to_team_suggestions=["ghost"])
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert d.reason == "no allowed suggestion"


def test_empty_suggestions_with_non_terminal_returns_await_human() -> None:
    h = _make_handover("research", to_team_suggestions=[])
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"


def test_suggestion_not_in_registered_teams_skipped() -> None:
    minimal: dict[str, Team] = {
        "decomposer": TEAMS["decomposer"],
    }
    h = _make_handover(
        "implementation_plan",
        to_team_suggestions=["architecture", "decomposer"],
    )
    d = select_next_team(h, minimal)
    assert d.kind == "enqueue"
    assert d.team == "decomposer"


def test_next_hop_not_in_registered_teams_falls_through() -> None:
    minimal: dict[str, Team] = {
        "implementation_plan": TEAMS["implementation_plan"],
    }
    h = _make_handover(
        "architecture",
        to_team_suggestions=["implementation_plan"],
        next_hop="research",
    )
    d = select_next_team(h, minimal)
    assert d.kind == "enqueue"
    assert d.team == "implementation_plan"


def test_unknown_from_team_escalates_rather_than_completes() -> None:
    h = _make_handover("ghost", to_team_suggestions=["research"])
    d = select_next_team(h, TEAMS)
    assert d.kind == "escalate"
    assert d.reason is not None
    assert "unknown from_team" in d.reason


@pytest.mark.parametrize(
    ("from_team", "expected_team"),
    [
        ("research", "implementation_plan"),
        ("brainstorming", "implementation_plan"),
        ("architecture", "implementation_plan"),
        ("implementation_plan", "decomposer"),
        ("decomposer", "implementation"),
        ("implementation", "verification"),
        ("implementation_fix", "verification"),
        ("verification", "implementation_fix"),
        ("opportunity_scout", "research"),
    ],
)
def test_default_charter_handover_routes_to_first_suggestion(
    from_team: str, expected_team: str
) -> None:
    suggestions: dict[str, list[str]] = {
        "research": ["implementation_plan"],
        "brainstorming": ["implementation_plan"],
        "architecture": ["implementation_plan"],
        "implementation_plan": ["decomposer"],
        "decomposer": ["implementation"],
        "implementation": ["verification"],
        "implementation_fix": ["verification"],
        "verification": ["implementation_fix"],
        "opportunity_scout": ["research"],
    }
    h = _make_handover(from_team, to_team_suggestions=suggestions[from_team])
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == expected_team


def test_router_decision_default_fields() -> None:
    d = RouterDecision(kind="done")
    assert d.team is None
    assert d.reason is None
    assert d.blockers == ()


def test_router_decision_carries_blockers() -> None:
    from alan.park import Blocker

    blockers = (Blocker(kind="failing_check", name="Tests", detail="check failing"),)
    d = RouterDecision(kind="await_human", reason="blocked", blockers=blockers)
    assert d.blockers == blockers


def test_router_decision_with_all_fields() -> None:
    d = RouterDecision(kind="enqueue", team="research", reason="forward")
    assert d.team == "research"
    assert d.reason == "forward"


def test_select_next_team_works_with_minimal_team_subset() -> None:
    team: Team = SingleAgentTeam(name="t", system_prompt="p", to_team_suggestions=())
    assert team.name == "t"


def test_pr_merge_can_route_to_implementation_fix() -> None:
    h = _make_handover("pr_merge", to_team_suggestions=["implementation_fix"])
    d = select_next_team(h, TEAMS)
    assert d.kind == "enqueue"
    assert d.team == "implementation_fix"


def test_principal_proxy_resolver_role_falls_through_to_human() -> None:
    h = _make_handover(
        "research",
        open_questions=[_make_blocking_question("principal_proxy")],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"


def test_terminal_handover_type_returns_done() -> None:
    h = _make_handover("pr_merge", handover_type="terminal", to_team_suggestions=[])
    d = select_next_team(h, TEAMS)
    assert d.kind == "done"
    assert d.reason == "terminal handover"


def test_terminal_handover_ignores_policy() -> None:
    h = _make_handover(
        "pr_merge",
        handover_type="terminal",
        to_team_suggestions=["retrospective"],
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "done"
    assert d.reason == "terminal handover"


def test_credential_park_preempts_escalation_short_circuit() -> None:
    h = _make_handover("implementation", handover_type="escalation").model_copy(
        update={"credential_failure": "expired_token"}
    )
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert d.block_class == "credential"
    assert len(d.blockers) == 1
    assert d.blockers[0].name == "credential_blocker"


def test_credential_park_preempts_forward_routing() -> None:
    h = _make_handover(
        "verification", to_team_suggestions=["pr_merge"]
    ).model_copy(update={"credential_failure": "expired_token"})
    d = select_next_team(h, TEAMS)
    assert d.kind == "await_human"
    assert d.block_class == "credential"


def test_credential_park_blocker_carries_full_synthesis_as_recipe() -> None:
    h = _make_handover("pr_merge", handover_type="escalation")
    h = h.model_copy(update={
        "credential_failure": "expired_token",
        "full_synthesis": "gh pr merge failed. Rotate under stable fallback name.",
    })
    d = select_next_team(h, TEAMS)
    assert d.blockers[0].detail == "gh pr merge failed. Rotate under stable fallback name."
    assert d.blockers[0].instruction == d.blockers[0].detail


def test_missing_token_and_transient_do_not_trigger_credential_park() -> None:
    for cls in ("missing_token", "transient", None):
        h = _make_handover("implementation", handover_type="escalation").model_copy(
            update={"credential_failure": cls}
        )
        d = select_next_team(h, TEAMS)
        assert d.kind == "escalate"
        assert d.block_class is None
