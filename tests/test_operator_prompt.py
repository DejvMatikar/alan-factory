from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alan.operator_prompt import InvalidScopeTarget, Scope, validate_target, workspace_prompt

_WORKSPACE_DIR = Path("/fleet/workspaces/fix-the-thing")
_LOGS_DIR = Path("/fleet/logs")
_DOCS_DIR = Path("/checkouts/alan/docs")


def _prompt(**overrides: Any) -> str:
    arguments: dict[str, Any] = {
        "task_id": "fix-the-thing",
        "repo_slug": "some-owner/some-repo",
        "workspace_dir": _WORKSPACE_DIR,
        "logs_dir": _LOGS_DIR,
        "docs_dir": _DOCS_DIR,
        **overrides,
    }
    return workspace_prompt(**arguments)


def test_scope_values_are_the_wire_names() -> None:
    assert [scope.value for scope in Scope] == ["fleet", "repo", "workspace"]


def test_workspace_prompt_names_the_workspace_and_its_repository() -> None:
    prompt = _prompt()
    assert "Scope: workspace fix-the-thing, in repository some-owner/some-repo." in prompt


def test_workspace_prompt_points_at_absolute_paths_only() -> None:
    prompt = _prompt()
    assert f"Look at {_WORKSPACE_DIR} first" in prompt
    assert f"- workspace: {_WORKSPACE_DIR}" in prompt
    assert f"- logs: {_LOGS_DIR}" in prompt
    for name in ("README", "unblocker", "deploy", "operator_terminal"):
        assert f"- {_DOCS_DIR}/architecture/{name}.md" in prompt
    assert "- docs/" not in prompt


def test_workspace_prompt_points_at_no_directory_the_session_cannot_reach() -> None:
    prompt = _prompt()
    assert "state:" not in prompt
    assert "workspaces:" not in prompt


def test_workspace_prompt_carries_the_safety_rules() -> None:
    prompt = _prompt()
    assert "Rules for this session:" in prompt
    assert "credential files of this install are blocked" in prompt
    assert "Never print, copy or transmit secrets or the control token." in prompt
    assert "Do not merge pull requests, delete branches or rewrite history" in prompt


def test_workspace_prompt_no_longer_claims_the_scope_grants_no_permission() -> None:
    assert "grants and removes no permission" not in _prompt()


def test_prompt_holds_pointers_only_and_none_of_the_workspace_contents(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "ws-1"
    workspace_dir.mkdir()
    (workspace_dir / "meta.json").write_text('{"note": "GATHERED-SENTINEL"}')
    assert "GATHERED-SENTINEL" not in _prompt(workspace_dir=workspace_dir)


@pytest.mark.parametrize(
    ("scope", "target"),
    [
        (Scope.FLEET, "anything"),
        (Scope.REPO, None),
        (Scope.REPO, "no-slash"),
        (Scope.REPO, "a/b/c"),
        (Scope.REPO, "../x/y"),
        (Scope.REPO, "a/../b"),
        (Scope.REPO, "a/b; rm -rf"),
        (Scope.WORKSPACE, None),
        (Scope.WORKSPACE, "../etc"),
        (Scope.WORKSPACE, "a/b"),
        (Scope.WORKSPACE, "UPPER"),
        (Scope.WORKSPACE, "-leading"),
        (Scope.WORKSPACE, "x" * 65),
        (Scope.WORKSPACE, ""),
    ],
)
def test_invalid_scope_targets_are_refused(scope: Scope, target: str | None) -> None:
    with pytest.raises(InvalidScopeTarget):
        validate_target(scope, target)


@pytest.mark.parametrize(
    ("scope", "target"),
    [
        (Scope.FLEET, None),
        (Scope.REPO, "Owner-1/repo.name_2"),
        (Scope.WORKSPACE, "a"),
        (Scope.WORKSPACE, "x" * 64),
    ],
)
def test_valid_scope_targets_are_returned_unchanged(scope: Scope, target: str | None) -> None:
    assert validate_target(scope, target) == target
