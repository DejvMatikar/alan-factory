from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = "tests"
_EXCLUDED_SEGMENTS = frozenset({"__pycache__"})

FAMILY_MODULES: frozenset[str] = frozenset({
    "_pr_branch",
    "_pr_ready",
    "_pr_feedback",
    "_pr_threads",
    "_pr_post",
    "_pr_push",
    "_pr_flake_ledger",
    "_pr_merge_conflict",
    "_pr_reconcile",
    "_pr_freshness",
    "_pr_sonar",
})


class PatchSiteCount(NamedTuple):
    sites: int
    files: int


RECORDED_PATCH_SITES = PatchSiteCount(sites=794, files=74)


def _dotted_segments(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_dotted_segments(node.value), node.attr)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return tuple(node.value.split("."))
    return ()


def targets_family_module(target: ast.expr) -> bool:
    segments = _dotted_segments(target)
    module_segments = segments[:-1] if isinstance(target, ast.Constant) else segments
    return bool(module_segments) and module_segments[-1] in FAMILY_MODULES


def is_family_patch_site(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and bool(node.args)
        and targets_family_module(node.args[0])
    )


def count_patch_sites(source: str) -> int:
    return sum(is_family_patch_site(node) for node in ast.walk(ast.parse(source)))


def count_family_patch_sites(root: Path = _REPO_ROOT) -> PatchSiteCount:
    sites = 0
    files = 0
    for path in (root / _TESTS_DIR).rglob("*.py"):
        if _EXCLUDED_SEGMENTS.intersection(path.relative_to(root).parts):
            continue
        found = count_patch_sites(path.read_text(encoding="utf-8"))
        sites += found
        if found:
            files += 1
    return PatchSiteCount(sites=sites, files=files)
