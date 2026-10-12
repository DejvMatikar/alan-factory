from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import Handover
from alan.teams._pr_handover import (
    HandoverContext,
    PushFailureContext,
    TeamConfig,
    build_handover,
    push_failed_handover,
)
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("ph-push-result-task", "push result tests", root=tmp_path / "ws")


def _ctx(workspace: Workspace) -> HandoverContext:
    return HandoverContext.build(
        workspace,
        None,
        "inv_ph_pr",
        team_name="implementation",
        default_forbidden=("no push",),
        next_team=("verification",),
    )


def _prior_with_pr(workspace: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id="ho_ph_pr_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_000",
        to_team_suggestions=["implementation"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verdict",
        pr_number=pr_number,
    )


_TEST_TEAM_CONFIG = TeamConfig(
    team_name="implementation",
    default_forbidden=(),
    next_team=("verification",),
    decision_prefix="I",
)


def test_push_failed_handover_pushed_true_states_commits_safe_and_names_branch(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(
        ctx, "worker synthesis", None, pushed=True, branch="teams/foo-bar"
    )
    assert ho.handover_type == "escalation"
    assert ho.pr_number is None
    assert "teams/foo-bar" in ho.full_synthesis
    assert "SAFE" in ho.full_synthesis
    assert "Do NOT re-implement" in ho.full_synthesis
    assert "committed work was lost" not in ho.full_synthesis


def test_push_failed_handover_pushed_true_without_branch_still_escalates(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(ctx, "worker synthesis", None, pushed=True, branch=None)
    assert ho.pr_number is None
    assert "SAFE" in ho.full_synthesis
    assert "the pushed branch" in ho.full_synthesis
    assert "committed work was lost" not in ho.full_synthesis


def test_push_failed_handover_pushed_false_keeps_lost_wording(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(
        ctx, "worker synthesis", None, pushed=False, branch="teams/foo-bar"
    )
    assert "committed work was lost" in ho.full_synthesis
    assert "SAFE" not in ho.full_synthesis


def test_push_failed_handover_default_pushed_keeps_lost_wording(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(ctx, "worker synthesis", None, branch="teams/foo-bar")
    assert "committed work was lost" in ho.full_synthesis


def test_push_failed_handover_continuing_pr_failed_push_keeps_lost_wording(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    ho = push_failed_handover(
        ctx, "worker synthesis", prior, pushed=False, continuing_pr=True
    )
    assert ho.pr_number == 42
    assert "push to PR #42 failed" in ho.full_synthesis
    assert "were lost when the worktree was removed" in ho.full_synthesis
    assert "SAFE" not in ho.full_synthesis


def test_push_failed_handover_stale_prior_pr_with_fresh_branch_states_commits_safe(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    ho = push_failed_handover(
        ctx, "worker synthesis", prior,
        pushed=True, continuing_pr=False, branch="teams/fresh-branch",
    )
    assert "teams/fresh-branch" in ho.full_synthesis
    assert "SAFE" in ho.full_synthesis
    assert "Do NOT re-implement" in ho.full_synthesis
    assert "push to PR #42 failed" not in ho.full_synthesis
    assert "were lost when the worktree was removed" not in ho.full_synthesis


def test_push_failed_handover_pushed_true_with_expired_token_appends_recipe(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(
        ctx, "worker synthesis", None,
        pushed=True, branch="teams/foo-bar", credential_failure="expired_token",
    )
    assert ho.credential_failure == "expired_token"
    assert "transport credential" in ho.full_synthesis
    assert "SAFE" in ho.full_synthesis


def test_build_handover_defaults_pushed_true_for_no_prior_pr(
    workspace: Workspace,
) -> None:
    result = build_handover(
        workspace, None, "inv_002", "synthesis text", ["abc1234 add work"], None,
        _TEST_TEAM_CONFIG, iteration_push_failed=True,
        push_failure=PushFailureContext(branch="teams/foo-bar"),
    )
    assert "SAFE" in result.full_synthesis
    assert "committed work was lost" not in result.full_synthesis


def test_build_handover_pushed_false_explicit_keeps_lost_wording(
    workspace: Workspace,
) -> None:
    result = build_handover(
        workspace, None, "inv_002", "synthesis text", ["abc1234 add work"], None,
        _TEST_TEAM_CONFIG, iteration_push_failed=True,
        push_failure=PushFailureContext(branch="teams/foo-bar", pushed=False),
    )
    assert "committed work was lost" in result.full_synthesis
    assert "SAFE" not in result.full_synthesis


def test_finalize_handover_default_pushed_true_matches_implementation_fix_call_shape(
    workspace: Workspace,
) -> None:
    from alan.teams._pr_handover import finalize_handover

    prior = _prior_with_pr(workspace, 42)
    result = finalize_handover(
        _TEST_TEAM_CONFIG, workspace, prior, "inv_003", "synthesis text",
        ["abc1234 add work"], 42, iteration_push_failed=True,
    )
    assert "push to PR #42 failed" in result.full_synthesis
    assert "were lost when the worktree was removed" in result.full_synthesis


def test_finalize_handover_threads_pushed_through_to_push_failed_handover(
    workspace: Workspace,
) -> None:
    from alan.teams._pr_handover import finalize_handover

    result = finalize_handover(
        _TEST_TEAM_CONFIG, workspace, None, "inv_004", "synthesis text",
        ["abc1234 add work"], None, iteration_push_failed=True,
        push_failure=PushFailureContext(branch="teams/foo-bar", pushed=True),
    )
    assert "SAFE" in result.full_synthesis
    assert "teams/foo-bar" in result.full_synthesis
