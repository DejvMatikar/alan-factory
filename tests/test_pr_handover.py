from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.handover import Handover
from alan.teams._pr_handover import HandoverContext, push_failed_handover
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("ph-task", "push handover tests", root=tmp_path / "ws")


def _ctx(workspace: Workspace) -> HandoverContext:
    return HandoverContext.build(
        workspace,
        None,
        "inv_ph",
        team_name="implementation",
        default_forbidden=("no push",),
        next_team=("verification",),
    )


def _prior_with_pr(workspace: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id="ho_ph_001",
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


def test_push_failed_handover_existing_pr_uses_pr_number_message(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    ho = push_failed_handover(ctx, "worker synthesis", prior)
    assert ho.handover_type == "escalation"
    assert ho.pr_number == 42
    assert "push to PR #42 failed" in ho.full_synthesis
    assert "worker synthesis" in ho.full_synthesis


def test_push_failed_handover_fresh_pr_with_branch_names_branch(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(ctx, "worker synthesis", None, branch="teams/foo-bar")
    assert ho.handover_type == "escalation"
    assert ho.pr_number is None
    assert "no PR is available" in ho.full_synthesis
    assert "teams/foo-bar" in ho.full_synthesis
    assert "worker synthesis" in ho.full_synthesis


def test_push_failed_handover_fresh_pr_without_branch(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    ho = push_failed_handover(ctx, "worker synthesis", None, branch=None)
    assert ho.handover_type == "escalation"
    assert ho.pr_number is None
    assert "no PR is available" in ho.full_synthesis
    assert "worker synthesis" in ho.full_synthesis
    assert ho.credential_failure is None


def test_push_failed_handover_transient_leaves_credential_failure_none(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    ho = push_failed_handover(
        ctx, "worker synthesis", prior, credential_failure="transient"
    )
    assert ho.credential_failure is None
    assert "stable fallback name" not in ho.full_synthesis


def test_push_failed_handover_expired_token_sets_credential_failure(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    ho = push_failed_handover(
        ctx, "worker synthesis", prior, credential_failure="expired_token"
    )
    assert ho.credential_failure == "expired_token"
    assert "transport credential" in ho.full_synthesis
    assert "stable fallback name" in ho.full_synthesis


def test_push_failed_handover_expired_token_includes_forge_slug_when_available(
    workspace: Workspace,
) -> None:
    ctx = _ctx(workspace)
    prior = _prior_with_pr(workspace, 42)
    forge_target = ForgeTarget(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        credential_env_name="ACME_WIDGET_GH_TOKEN",
    )
    ho = push_failed_handover(
        ctx, "worker synthesis", prior,
        credential_failure="expired_token", forge_target=forge_target,
    )
    assert "acme-corp/widget-app" in ho.full_synthesis
    assert "ACME_WIDGET_GH_TOKEN" in ho.full_synthesis
