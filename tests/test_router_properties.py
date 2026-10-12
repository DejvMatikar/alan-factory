from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import get_args

import pytest

from alan.handover import Handover, OpenQuestion
from alan.router import TEAM_TRANSITION_POLICY, DecisionKind, RouterDecision, select_next_team
from alan.teams import TEAMS


def _make_handover(
    from_team: str,
    *,
    handover_type: str = "forward",
    to_team_suggestions: list[str] | None = None,
    next_hop: str | None = None,
    open_questions: list[OpenQuestion] | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_props_{from_team}",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="props",
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        next_hop=next_hop,
        open_questions=open_questions or [],
        full_synthesis="x",
    )


def _make_blocking_question(resolver_role: str) -> OpenQuestion:
    return OpenQuestion(
        id="q1",
        question="?",
        resolver_role=resolver_role,
        blocking=True,
        prior_attempts=[],
    )


def _all_teams() -> frozenset[str]:
    targets = {t for allowed in TEAM_TRANSITION_POLICY.values() for t in allowed}
    return frozenset(TEAM_TRANSITION_POLICY) | targets


def _iter_router_inputs() -> list[tuple[str, Handover]]:
    cases: list[tuple[str, Handover]] = []
    domain = _all_teams()
    for from_team, allowed in TEAM_TRANSITION_POLICY.items():
        allowed_list = sorted(allowed)
        disallowed = sorted(domain - allowed - {from_team})

        cases.append((f"{from_team}/no-hints", _make_handover(from_team)))

        for target in allowed_list:
            cases.append(
                (f"{from_team}/next_hop={target}", _make_handover(from_team, next_hop=target))
            )

        if allowed_list:
            cases.append(
                (
                    f"{from_team}/suggestions=all",
                    _make_handover(from_team, to_team_suggestions=allowed_list),
                )
            )
            cases.append(
                (
                    f"{from_team}/suggestions=reversed",
                    _make_handover(
                        from_team, to_team_suggestions=list(reversed(allowed_list))
                    ),
                )
            )

        if disallowed:
            bad = disallowed[0]
            cases.append(
                (
                    f"{from_team}/next_hop_disallowed",
                    _make_handover(from_team, next_hop=bad, to_team_suggestions=allowed_list),
                )
            )
            cases.append(
                (
                    f"{from_team}/suggestions_all_disallowed",
                    _make_handover(from_team, to_team_suggestions=disallowed[:2]),
                )
            )
            if allowed_list:
                cases.append(
                    (
                        f"{from_team}/suggestion_disallowed_then_allowed",
                        _make_handover(
                            from_team, to_team_suggestions=[bad, *allowed_list]
                        ),
                    )
                )

        cases.append(
            (
                f"{from_team}/blocking_human",
                _make_handover(from_team, open_questions=[_make_blocking_question("human")]),
            )
        )
        for target in allowed_list:
            cases.append(
                (
                    f"{from_team}/blocking_resolver={target}",
                    _make_handover(
                        from_team, open_questions=[_make_blocking_question(target)]
                    ),
                )
            )
        if disallowed:
            cases.append(
                (
                    f"{from_team}/blocking_resolver_disallowed",
                    _make_handover(
                        from_team, open_questions=[_make_blocking_question(disallowed[0])]
                    ),
                )
            )
        cases.append(
            (
                f"{from_team}/blocking_resolver_unknown",
                _make_handover(
                    from_team, open_questions=[_make_blocking_question("nonexistent_role")]
                ),
            )
        )
    return cases


_ROUTER_INPUTS = _iter_router_inputs()
_ROUTER_INPUT_IDS = [case_id for case_id, _ in _ROUTER_INPUTS]


def _assert_legal_target(decision: RouterDecision, from_team: str) -> None:
    if decision.kind != "enqueue":
        return
    team = decision.team
    assert team is not None
    allowed = TEAM_TRANSITION_POLICY.get(from_team, frozenset())
    assert team in allowed, (
        f"illegal transition: {from_team} -> {team} "
        f"(policy allows only {sorted(allowed)})"
    )


@pytest.mark.parametrize(
    ("case_id", "handover"), _ROUTER_INPUTS, ids=_ROUTER_INPUT_IDS
)
def test_no_illegal_transition(case_id: str, handover: Handover) -> None:
    del case_id
    decision = select_next_team(handover, TEAMS)
    _assert_legal_target(decision, handover.from_team)


@pytest.mark.parametrize(
    ("case_id", "handover"), _ROUTER_INPUTS, ids=_ROUTER_INPUT_IDS
)
def test_select_next_team_deterministic(case_id: str, handover: Handover) -> None:
    del case_id
    first = select_next_team(copy.deepcopy(handover), TEAMS)
    second = select_next_team(copy.deepcopy(handover), TEAMS)
    assert first == second


@pytest.mark.parametrize(
    ("case_id", "handover"), _ROUTER_INPUTS, ids=_ROUTER_INPUT_IDS
)
def test_select_next_team_totality(case_id: str, handover: Handover) -> None:
    del case_id
    decision = select_next_team(handover, TEAMS)
    assert decision is not None
    assert decision.kind in get_args(DecisionKind)


@pytest.mark.parametrize("from_team", sorted(TEAM_TRANSITION_POLICY))
def test_credential_park_preempts_normal_routing_for_every_team(from_team: str) -> None:
    h = _make_handover(from_team, handover_type="escalation").model_copy(
        update={"credential_failure": "expired_token"}
    )
    decision = select_next_team(h, TEAMS)
    assert decision.kind == "await_human"
    assert decision.block_class == "credential"
