from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import scenario_binding
from alan import workflow as workflow_mod
from alan.handover import Handover, TestScenario, record_answer, save_handover
from alan.teams import _pr_handover as ph
from alan.teams import pr_merge as pr_merge_mod
from alan.teams.pr_merge import OutcomeKind, PrMergeOutcome
from alan.workspace import Workspace, create


@pytest.fixture(autouse=True)
def _silence_audit_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PR_MERGE_AUDIT_DISABLE", "1")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-carry", "carry scenarios through merge stage", root=tmp_path / "ws")


def _prior_with_scenarios(ws: Workspace, **kwargs: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "forward",
        "task_id": ws.task_id,
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["pr_merge"],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "ready",
        "pr_number": 42,
        "test_scenarios": [
            TestScenario(id="s1", description="d", given="g", when="w", then="t")
        ],
    }
    base.update(kwargs)
    return Handover.model_validate(base)


def test_escalation_handover_carries_scenarios(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="closed", pr_number=42, reason="r")
    handover = pr_merge_mod._escalation_handover(workspace, prior, "inv_002", outcome)
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_escalation_handover_none_prior_yields_empty_scenarios(workspace: Workspace) -> None:
    outcome = PrMergeOutcome(kind="closed", pr_number=None, reason="r")
    handover = pr_merge_mod._escalation_handover(workspace, None, "inv_002", outcome)
    assert handover.test_scenarios == []


def test_dep_bot_rebase_handover_carries_scenarios(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace, dependency_bot_authorized=True)
    outcome = PrMergeOutcome(kind="failed", pr_number=42, reason="r")
    handover = pr_merge_mod._dep_bot_rebase_handover(workspace, prior, "inv_002", outcome)
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_dep_bot_rebase_handover_empty_prior_scenarios_yields_empty(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(
        workspace, dependency_bot_authorized=True, test_scenarios=[]
    )
    outcome = PrMergeOutcome(kind="failed", pr_number=42, reason="r")
    handover = pr_merge_mod._dep_bot_rebase_handover(workspace, prior, "inv_002", outcome)
    assert handover.test_scenarios == []


def test_await_human_handover_carries_scenarios(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="timeout", pr_number=42, reason="r")
    handover = pr_merge_mod._await_human_handover(workspace, prior, "inv_002", outcome)
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_await_human_handover_none_prior_yields_empty_scenarios(workspace: Workspace) -> None:
    outcome = PrMergeOutcome(kind="timeout", pr_number=None, reason="r")
    handover = pr_merge_mod._await_human_handover(workspace, None, "inv_002", outcome)
    assert handover.test_scenarios == []


def test_copilot_handover_carries_scenarios(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    handover = pr_merge_mod._copilot_handover(workspace, prior, "inv_002", outcome)
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_copilot_handover_none_prior_yields_empty_scenarios(workspace: Workspace) -> None:
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=None, reason="r")
    handover = pr_merge_mod._copilot_handover(workspace, None, "inv_002", outcome)
    assert handover.test_scenarios == []


def test_escalation_handover_preserves_declared_fields(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace, dependency_bot_authorized=True)
    outcome = PrMergeOutcome(
        kind="closed", pr_number=42, reason="closed", credential_failure="expired_token",
    )
    handover = pr_merge_mod._escalation_handover(workspace, prior, "inv_002", outcome)
    assert handover.credential_failure == "expired_token"
    assert handover.forbidden_actions == []
    assert handover.to_team_suggestions == []
    assert handover.handover_type == "escalation"
    assert handover.dependency_bot_authorized is True


def test_escalation_handover_dependency_bot_authorized_resolves_false_without_prior(
    workspace: Workspace,
) -> None:
    outcome = PrMergeOutcome(kind="closed", pr_number=None, reason="r")
    handover = pr_merge_mod._escalation_handover(workspace, None, "inv_002", outcome)
    assert handover.dependency_bot_authorized is False


def test_copilot_handover_does_not_propagate_dependency_bot_authorized(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(workspace, dependency_bot_authorized=True)
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    handover = pr_merge_mod._copilot_handover(workspace, prior, "inv_002", outcome)
    assert handover.dependency_bot_authorized is False


def test_handover_context_extra_test_scenarios_overrides_carried(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(
        workspace,
        test_scenarios=[TestScenario(id="a", description="d", given="g", when="w", then="t")],
    )
    extra = [TestScenario(id="b", description="d2", given="g2", when="w2", then="t2")]
    ctx = ph.HandoverContext.build(
        workspace, prior, "inv_002",
        team_name=pr_merge_mod._TEAM_NAME, default_forbidden=(), next_team=(),
        extra_test_scenarios=extra,
    )
    handover = ph.escalation_skeleton(ctx, "summary", None)
    assert [s.id for s in handover.test_scenarios] == ["b"]


@pytest.mark.parametrize(
    ("kind", "expected_type"),
    [
        ("already_merged", "terminal"),
        ("ready", "terminal"),
        ("closed", "escalation"),
        ("error", "escalation"),
        ("unresolved_ceiling", "escalation"),
    ],
)
def test_build_handover_routes_by_outcome_kind(
    workspace: Workspace, kind: OutcomeKind, expected_type: str
) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind=kind, pr_number=42, reason="r", pr_url="u")
    handover = pr_merge_mod._build_handover(workspace, prior, "inv_002", outcome)
    assert handover.handover_type == expected_type


def test_build_handover_routes_copilot_unresolved_to_copilot_or_escalate(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    handover = pr_merge_mod._build_handover(workspace, prior, "inv_002", outcome)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_build_handover_routes_failed_with_dep_bot_auth_to_dep_rebase(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(workspace, dependency_bot_authorized=True)
    outcome = PrMergeOutcome(kind="failed", pr_number=42, reason="r")
    handover = pr_merge_mod._build_handover(workspace, prior, "inv_002", outcome)
    assert handover.dependency_bot_authorized is True
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert [s.id for s in handover.test_scenarios] == [s.id for s in prior.test_scenarios]


def test_build_handover_fallthrough_routes_to_await_human(workspace: Workspace) -> None:
    prior = _prior_with_scenarios(workspace, dependency_bot_authorized=False)
    outcome = PrMergeOutcome(kind="timeout", pr_number=42, reason="r")
    handover = pr_merge_mod._build_handover(workspace, prior, "inv_002", outcome)
    assert handover.open_questions
    assert handover.open_questions[0].resolver_role == "human"


def test_build_handover_fallthrough_with_no_prior_routes_to_await_human(
    workspace: Workspace,
) -> None:
    outcome = PrMergeOutcome(kind="failed", pr_number=None, reason="r")
    handover = pr_merge_mod._build_handover(workspace, None, "inv_002", outcome)
    assert handover.open_questions


def test_copilot_handover_or_escalate_below_cap_routes_to_copilot_handover(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    handover = pr_merge_mod._copilot_handover_or_escalate(workspace, prior, "inv_002", outcome)
    assert handover.to_team_suggestions == ["implementation_fix"]


def test_copilot_handover_or_escalate_at_cap_routes_to_await_human(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_COPILOT_ESCALATION_MAX", "1")
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r")
    pr_merge_mod._increment_copilot_escalation(workspace)
    handover = pr_merge_mod._copilot_handover_or_escalate(workspace, prior, "inv_002", outcome)
    assert handover.open_questions


def _fixture_no_pr_handover_with_workflow(workspace: Workspace) -> Handover:
    prior = _prior_with_scenarios(workspace)
    specs = pr_merge_mod.TEAM.plan_workflow(workspace, prior)
    inv_id = workflow_mod.seed_pending_workflow(
        workspace, team=pr_merge_mod.TEAM.name, tasks=specs
    )
    return pr_merge_mod._no_pr_handover_with_workflow(workspace, prior, lambda _e: None, inv_id)


def test_no_pr_handover_with_workflow_returns_a_handover(workspace: Workspace) -> None:
    handover = _fixture_no_pr_handover_with_workflow(workspace)
    assert isinstance(handover, Handover)
    assert handover.open_questions


def _carry_fixtures(workspace: Workspace) -> dict[str, Callable[[], Handover]]:
    return {
        "_escalation_handover": lambda: pr_merge_mod._escalation_handover(
            workspace, _prior_with_scenarios(workspace), "inv_002",
            PrMergeOutcome(kind="closed", pr_number=42, reason="r"),
        ),
        "_dep_bot_rebase_handover": lambda: pr_merge_mod._dep_bot_rebase_handover(
            workspace, _prior_with_scenarios(workspace), "inv_002",
            PrMergeOutcome(kind="failed", pr_number=42, reason="r"),
        ),
        "_await_human_handover": lambda: pr_merge_mod._await_human_handover(
            workspace, _prior_with_scenarios(workspace), "inv_002",
            PrMergeOutcome(kind="timeout", pr_number=42, reason="r"),
        ),
        "_copilot_handover": lambda: pr_merge_mod._copilot_handover(
            workspace, _prior_with_scenarios(workspace), "inv_003",
            PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r"),
        ),
        "_build_handover": lambda: pr_merge_mod._build_handover(
            workspace, _prior_with_scenarios(workspace), "inv_002",
            PrMergeOutcome(kind="closed", pr_number=42, reason="r"),
        ),
        "_copilot_handover_or_escalate": lambda: pr_merge_mod._copilot_handover_or_escalate(
            workspace, _prior_with_scenarios(workspace), "inv_004",
            PrMergeOutcome(kind="copilot_unresolved", pr_number=42, reason="r"),
        ),
        "_no_pr_handover_with_workflow": lambda: _fixture_no_pr_handover_with_workflow(
            workspace
        ),
    }


def _discover_handover_builders() -> set[str]:
    return {
        name
        for name, fn in inspect.getmembers(pr_merge_mod, inspect.isfunction)
        if fn.__module__ == pr_merge_mod.__name__
        and inspect.signature(fn).return_annotation == "Handover"
    }


def test_merge_stage_handover_builders_are_exhaustively_covered(
    workspace: Workspace,
) -> None:
    fixtures = _carry_fixtures(workspace)
    exempt = {"_terminal_handover"}
    discovered = _discover_handover_builders()
    assert discovered == set(fixtures) | exempt
    assert exempt == {"_terminal_handover"}, (
        "terminal handovers end the chain and cannot starve anything "
        "downstream, so they are the sole pre-agreed exemption from the "
        "carry-scenarios contract; a new non-terminal Handover-returning "
        "function must get a carry fixture, not join this set"
    )
    assert len(discovered) == 8
    assert "build_terminal_handover" not in discovered
    for name, fixture in fixtures.items():
        handover = fixture()
        assert isinstance(handover, Handover), name
        assert handover.test_scenarios, name


def test_integration_escalation_handover_scenarios_reach_base_scenarios(
    workspace: Workspace,
) -> None:
    assert workspace.meta.tasks == []
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="closed", pr_number=42, reason="closed")
    handover = pr_merge_mod._escalation_handover(workspace, prior, "inv_002", outcome)
    result = scenario_binding.base_scenarios(workspace, handover)
    assert result
    assert [s.id for s in result] == [s.id for s in prior.test_scenarios]


def test_integration_await_human_handover_scenarios_reach_base_scenarios(
    workspace: Workspace,
) -> None:
    assert workspace.meta.tasks == []
    prior = _prior_with_scenarios(workspace)
    outcome = PrMergeOutcome(kind="timeout", pr_number=42, reason="timeout")
    handover = pr_merge_mod._await_human_handover(workspace, prior, "inv_002", outcome)
    result = scenario_binding.base_scenarios(workspace, handover)
    assert result
    assert [s.id for s in result] == [s.id for s in prior.test_scenarios]


def test_record_answer_from_await_human_handover_carries_scenarios_end_to_end(
    workspace: Workspace,
) -> None:
    prior = _prior_with_scenarios(workspace)
    save_handover(workspace, prior)
    outcome = PrMergeOutcome(kind="timeout", pr_number=42, reason="timeout")
    blocking = pr_merge_mod._await_human_handover(workspace, prior, "inv_002", outcome)
    save_handover(workspace, blocking)
    question_id = blocking.open_questions[0].id
    amended = record_answer(
        workspace, blocking, question_id, "Re-dispatch pr_merge.", from_team="human",
    )
    assert [s.id for s in amended.test_scenarios] == [s.id for s in prior.test_scenarios]
