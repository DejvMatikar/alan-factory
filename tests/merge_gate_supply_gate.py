from __future__ import annotations

import ast
from dataclasses import dataclass

_SUPPLY_KEYWORD = "merge_gate"
_CALL_ATTR = "dispatch_team_core"
_DISPATCH_MODULE = "alan.dispatch"
_DISPATCH_PACKAGE = "alan"
_DISPATCH_SUBMODULE = "dispatch"


@dataclass(frozen=True)
class Violation:
    lineno: int
    message: str


def _collect_dispatch_bindings(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    aliases: set[str] = set()
    call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _DISPATCH_PACKAGE:
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == _DISPATCH_SUBMODULE
            )
        elif isinstance(node, ast.ImportFrom) and node.module == _DISPATCH_MODULE:
            call_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == _CALL_ATTR
            )
        elif isinstance(node, ast.Import):
            aliases.update(
                alias.asname
                for alias in node.names
                if alias.name == _DISPATCH_MODULE and alias.asname
            )
    return frozenset(aliases), frozenset(call_names)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _match_dispatch_reference(
    node: ast.AST, aliases: frozenset[str], call_names: frozenset[str]
) -> ast.Attribute | ast.Name | None:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == _CALL_ATTR
        and isinstance(node.ctx, ast.Load)
    ):
        if isinstance(node.value, ast.Name) and node.value.id in aliases:
            return node
        if _dotted_name(node.value) == _DISPATCH_MODULE:
            return node
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in call_names:
        return node
    return None


def _build_parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _nearest_enclosing_call(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.Call | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Call):
            return current
        current = parents.get(current)
    return None


def _has_supply_keyword(call: ast.Call) -> bool:
    return any(kw.arg == _SUPPLY_KEYWORD for kw in call.keywords)


def find_missing_merge_gate_supply(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    aliases, call_names = _collect_dispatch_bindings(tree)
    parents = _build_parents(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        ref = _match_dispatch_reference(node, aliases, call_names)
        if ref is None:
            continue
        call = _nearest_enclosing_call(ref, parents)
        if call is None or not _has_supply_keyword(call):
            violations.append(
                Violation(
                    ref.lineno,
                    f"{filename}:{ref.lineno} dispatch_team_core reference has no "
                    f"enclosing call supplying {_SUPPLY_KEYWORD}=",
                )
            )
    return violations
