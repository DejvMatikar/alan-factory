from __future__ import annotations

from pathlib import Path

import pytest

from alan.handover import ArtifactRef
from alan.teams._base import build_forward_handover, build_terminal_handover
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("terminal-task", "d", root=tmp_path / "ws")


def test_build_terminal_handover_type(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.handover_type == "terminal"


def test_build_terminal_handover_no_suggestions(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.to_team_suggestions == []


def test_build_terminal_handover_first_iteration(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.iteration == 1
    assert h.revisit_of is None


def test_build_terminal_handover_increments_from_prior(workspace: Workspace) -> None:
    prior = build_forward_handover(
        workspace,
        from_team="verification",
        to_team_suggestions=["pr_merge"],
        summary="approved",
        prior=None,
    )
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=prior,
    )
    assert h.iteration == 2
    assert h.revisit_of == prior.handover_id


def test_build_terminal_handover_pr_number_propagation(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
        pr_number=42,
    )
    assert h.pr_number == 42


def test_build_terminal_handover_pr_number_defaults_none(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.pr_number is None


def test_build_terminal_handover_artifacts_propagation(workspace: Workspace) -> None:
    artifact = ArtifactRef(path="https://github.com/o/r/pull/1", description="PR #1 merged")
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
        artifacts_produced=[artifact],
    )
    assert len(h.artifacts_produced) == 1
    assert h.artifacts_produced[0].path == "https://github.com/o/r/pull/1"


def test_build_terminal_handover_artifacts_defaults_empty(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.artifacts_produced == []


def test_build_terminal_handover_from_team_and_summary(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="maintenance",
        summary="maintenance complete",
        prior=None,
    )
    assert h.from_team == "maintenance"
    assert h.full_synthesis == "maintenance complete"
    assert h.task_id == workspace.task_id


def test_build_terminal_handover_chain_id_from_prior(workspace: Workspace) -> None:
    prior = build_forward_handover(
        workspace,
        from_team="verification",
        to_team_suggestions=["pr_merge"],
        summary="approved",
        prior=None,
    )
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=prior,
    )
    assert h.handover_chain_id == prior.handover_chain_id


def test_build_terminal_handover_chain_id_self_when_no_prior(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
    )
    assert h.handover_chain_id == h.handover_id


def test_build_terminal_handover_invocation_id(workspace: Workspace) -> None:
    h = build_terminal_handover(
        workspace,
        from_team="pr_merge",
        summary="PR merged",
        prior=None,
        invocation_id="inv_042",
    )
    assert h.from_invocation_id == "inv_042"
