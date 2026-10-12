from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_DIR = "alan"
_EXCLUDED_SEGMENTS = frozenset({"web", "__pycache__"})

ADAPTER_MODULES: frozenset[str] = frozenset({"alan/forge_gh.py", "alan/self_update_github.py"})

RECORDED_ARGV_MODULES: frozenset[str] = frozenset({
    "alan/branch_protection_compat.py",
})

MIGRATED_MODULES: frozenset[str] = frozenset({
    "alan/collision.py",
    "alan/delivery.py",
    "alan/deploy_runs.py",
    "alan/engine_preflight.py",
    "alan/recurring_forge.py",
    "alan/repo_status.py",
    "alan/teams/_clause_audit.py",
    "alan/teams/_pr_branch.py",
    "alan/teams/_pr_body.py",
    "alan/teams/_pr_feedback.py",
    "alan/teams/_pr_threads.py",
    "alan/teams/_pr_post.py",
    "alan/teams/_pr_freshness.py",
    "alan/teams/_pr_flake_ledger.py",
    "alan/teams/_pr_merge_conflict.py",
    "alan/teams/_verification_grounding.py",
    "alan/teams/_verification_merged_guard.py",
    "alan/teams/_verification_review_post.py",
    "alan/teams/_verifier_thread_resolution.py",
    "alan/teams/deploy_verify.py",
    "alan/teams/dependency_sweep.py",
    "alan/teams/implementation.py",
    "alan/teams/triage_pregate.py",
    "alan/teams/unblock_issue.py",
})

TRANSPORT_ENTRY_POINTS: frozenset[str] = frozenset({
    "run_subprocess", "run_gh_api_json_input", "gh_checked",
})


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def transport_call_nodes(source: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _call_name(node) in TRANSPORT_ENTRY_POINTS
    ]


def transport_calls(source: str) -> list[str]:
    return [_call_name(node) for node in transport_call_nodes(source)]


def literal_prefix(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    prefix: list[str] = []
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            break
        prefix.append(element.value)
    return tuple(prefix)


def is_gh_argv(node: ast.AST) -> bool:
    return literal_prefix(node)[:1] == ("gh",)


def builds_gh_argv(source: str) -> bool:
    return any(is_gh_argv(node) for node in ast.walk(ast.parse(source)))


def find_argv_modules(root: Path = _REPO_ROOT) -> frozenset[str]:
    found: set[str] = set()
    for path in (root / _SOURCE_DIR).rglob("*.py"):
        rel = path.relative_to(root)
        if _EXCLUDED_SEGMENTS.intersection(rel.parts):
            continue
        if builds_gh_argv(path.read_text(encoding="utf-8")):
            found.add(rel.as_posix())
    return frozenset(found)
