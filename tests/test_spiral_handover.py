from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import ArtifactRef, Decision, Handover, OpenQuestion
from alan.teams import implementation_fix as fix_mod
from alan.teams._spiral_handover import spiral_escalation_handover
from alan.workspace import Workspace, create


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(root: Path) -> Workspace:
    return create("spiral-handover-test", "desc", root=root)


def _prior_handover(workspace: Workspace, **kwargs: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{workspace.task_id}_001",
        "handover_type": "forward",
        "task_id": workspace.task_id,
        "from_team": "implementation_fix",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["verification"],
        "iteration": 3,
        "timestamp_utc": datetime.now(UTC),
        "decisions": [
            Decision(
                id="d1", statement="did x", rationale="y",
                confidence=0.8, reversibility="medium",
            )
        ],
        "artifacts_produced": [
            ArtifactRef(path="judge_review.txt", description="full judge review: pass")
        ],
        "forbidden_actions": ["do not touch config"],
        "pr_number": 77,
        "full_synthesis": "prior worker synthesis text",
    }
    base.update(kwargs)
    return Handover.model_validate(base)


def test_spiral_escalation_handover_carries_decisions_and_artifacts(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert result.decisions == prior.decisions
    assert result.artifacts_produced == prior.artifacts_produced


def test_spiral_escalation_handover_excludes_open_questions(workspace: Workspace) -> None:
    prior = _prior_handover(
        workspace,
        open_questions=[
            OpenQuestion(
                id="q1", question="blocked on what?",
                resolver_role="human", blocking=True,
            )
        ],
    )
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert result.open_questions == []


def test_spiral_escalation_handover_full_synthesis_matches_reason_exactly(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    reason = "spiral:scenarios_missing: SCENARIOS_MISSING recurred twice"
    result = spiral_escalation_handover(workspace, prior, reason)
    assert result.full_synthesis == reason


def test_spiral_escalation_handover_is_escalation_type(workspace: Workspace) -> None:
    prior = _prior_handover(workspace)
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert result.handover_type == "escalation"
    assert result.from_team == prior.from_team
    assert result.from_invocation_id == prior.from_invocation_id
    assert result.to_team_suggestions == []
    assert result.pr_number == prior.pr_number


def test_spiral_escalation_handover_chains_from_prior(workspace: Workspace) -> None:
    prior = _prior_handover(workspace)
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert result.iteration == prior.iteration + 1
    assert result.revisit_of == prior.handover_id
    assert result.handover_chain_id == prior.handover_chain_id
    assert result.forbidden_actions == prior.forbidden_actions


def test_spiral_escalation_handover_no_pr_number_when_prior_has_none(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, pr_number=None)
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert result.pr_number is None


def test_spiral_escalation_handover_reconnects_judge_review_artifact(
    workspace: Workspace, tmp_path: Path
) -> None:
    review_path = tmp_path / "judge_review.txt"
    review_path.write_text("judge said pass")
    prior = _prior_handover(
        workspace,
        artifacts_produced=[
            ArtifactRef(path=str(review_path), description="full judge review: pass")
        ],
    )
    result = spiral_escalation_handover(workspace, prior, "spiral:unclassified: evidence")
    assert fix_mod._load_judge_review(result) == "judge said pass"
