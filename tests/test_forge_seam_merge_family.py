from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.forge_argv import literal_prefix

_REPO_ROOT = Path(__file__).resolve().parent.parent

BRIEF_MERGE_FAMILY = (
    "alan/teams/pr_merge.py",
    "alan/teams/_pr_ready.py",
    "alan/merge_recovery.py",
    "alan/merge_gate.py",
    "alan/teams/_pr_reconcile.py",
)
_FORBIDDEN_NAMES = frozenset({"update_pr_branch", "fetch_diff_files", "_fetch_diff_files"})
_FORBIDDEN_GH_VERBS = frozenset({"view", "checks", "merge"})


def _is_forbidden_gh_argv(node: ast.AST) -> bool:
    prefix = literal_prefix(node)
    return len(prefix) > 2 and prefix[:2] == ("gh", "pr") and prefix[2] in _FORBIDDEN_GH_VERBS


def _forbidden_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        name = node.attr
    elif isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.alias):
        name = node.name.rpartition(".")[2]
    else:
        return None
    return name if name in _FORBIDDEN_NAMES else None


def seam_violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        name = _forbidden_name(node)
        if name is not None:
            found.append(f"line {getattr(node, 'lineno', 0)}: references {name}")
        elif _is_forbidden_gh_argv(node):
            found.append(f"line {getattr(node, 'lineno', 0)}: builds a gh pr argv")
    return found


@pytest.mark.parametrize(
    "rel_path",
    [pytest.param(path, id=f"brief-five-{Path(path).name}") for path in BRIEF_MERGE_FAMILY],
)
def test_brief_merge_family_component_reaches_the_forge_only_through_the_port(
    rel_path: str,
) -> None:
    assert seam_violations((_REPO_ROOT / rel_path).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "cmd = ['gh', 'pr', 'merge', str(n), '--squash']",
        "cmd = ('gh', 'pr', 'view', str(n))",
        "cmd = ['gh', 'pr', 'checks', str(n), '--json', 'name,state']",
        "from alan.teams._pr_branch import fetch_diff_files as _f",
        "from alan.teams._pr_branch import _fetch_diff_files",
        "ok = _pr_branch.update_pr_branch(1, 't')",
        "files = _pr_branch._fetch_diff_files(1)",
    ],
)
def test_seam_gate_flags_a_residual_command_line_or_helper_reference(source: str) -> None:
    assert len(seam_violations(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        "outcome = _pr_branch.update_pr_branch_outcome(1, 't')",
        "cmd = ['gh', 'api', 'graphql']",
        "cmd = ['gh', 'pr', 'diff', '1']",
        "cmd = ['git', 'fetch']",
        "from alan.forge import active_forge",
    ],
)
def test_seam_gate_leaves_uncovered_forge_seams_alone(source: str) -> None:
    assert seam_violations(source) == []
