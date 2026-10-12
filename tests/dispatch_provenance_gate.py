from __future__ import annotations

import ast
from dataclasses import dataclass

_PROVENANCE_FIELD = "dispatch_provenance"
_SUPPLY_KEYWORD = "provenance"
_CREATE_ATTR = "create"
_WORKSPACE_MODULE = "alan.workspace"
_WORKSPACE_PACKAGE = "alan"
_WORKSPACE_SUBMODULE = "workspace"

MUTATION_EXEMPTIONS: frozenset[tuple[str, str]] = frozenset({("alan/workspace.py", "Meta")})


@dataclass(frozen=True)
class Violation:
    lineno: int
    message: str


def _collect_workspace_bindings(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    aliases: set[str] = set()
    create_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _WORKSPACE_PACKAGE:
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == _WORKSPACE_SUBMODULE
            )
        elif isinstance(node, ast.ImportFrom) and node.module == _WORKSPACE_MODULE:
            create_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == _CREATE_ATTR
            )
        elif isinstance(node, ast.Import):
            aliases.update(
                alias.asname
                for alias in node.names
                if alias.name == _WORKSPACE_MODULE and alias.asname
            )
    return frozenset(aliases), frozenset(create_names)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _match_create_reference(
    node: ast.AST, aliases: frozenset[str], create_names: frozenset[str]
) -> ast.Attribute | ast.Name | None:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == _CREATE_ATTR
        and isinstance(node.ctx, ast.Load)
    ):
        if isinstance(node.value, ast.Name) and node.value.id in aliases:
            return node
        if _dotted_name(node.value) == _WORKSPACE_MODULE:
            return node
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in create_names:
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


def find_missing_provenance_supply(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    aliases, create_names = _collect_workspace_bindings(tree)
    parents = _build_parents(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        ref = _match_create_reference(node, aliases, create_names)
        if ref is None:
            continue
        call = _nearest_enclosing_call(ref, parents)
        if call is None or not _has_supply_keyword(call):
            violations.append(
                Violation(
                    ref.lineno,
                    f"{filename}:{ref.lineno} workspace.create reference has no "
                    f"enclosing call supplying {_SUPPLY_KEYWORD}=",
                )
            )
    return violations


def _call_func_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _dict_violations(node: ast.Dict, filename: str) -> list[Violation]:
    return [
        Violation(
            key.lineno,
            f"{filename}:{key.lineno} dict literal writes {_PROVENANCE_FIELD!r}",
        )
        for key in node.keys
        if isinstance(key, ast.Constant) and key.value == _PROVENANCE_FIELD
    ]


def _subscript_violations(node: ast.Subscript, filename: str) -> list[Violation]:
    if not isinstance(node.ctx, ast.Store):
        return []
    if isinstance(node.slice, ast.Constant) and node.slice.value == _PROVENANCE_FIELD:
        return [
            Violation(
                node.lineno,
                f"{filename}:{node.lineno} subscript assignment writes {_PROVENANCE_FIELD!r}",
            )
        ]
    return []


def _call_violations(node: ast.Call, filename: str) -> list[Violation]:
    if (filename, _call_func_name(node)) in MUTATION_EXEMPTIONS:
        return []
    return [
        Violation(
            kw.lineno,
            f"{filename}:{kw.lineno} call keyword writes {_PROVENANCE_FIELD!r}",
        )
        for kw in node.keywords
        if kw.arg == _PROVENANCE_FIELD
    ]


def find_provenance_mutation_writes(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            violations.extend(_dict_violations(node, filename))
        elif isinstance(node, ast.Subscript):
            violations.extend(_subscript_violations(node, filename))
        elif isinstance(node, ast.Call):
            violations.extend(_call_violations(node, filename))
    return violations
