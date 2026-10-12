from __future__ import annotations

import ast
from dataclasses import dataclass

from tests.dispatch_provenance_gate import _build_parents  # pyright: ignore[reportPrivateUsage]

_STATUS = "status"
_WRITE_META = "_write_meta"
_MODEL_COPY = "model_copy"
_META_CALLS = frozenset({"Meta", "_read_meta"})
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

STATUS_WRITE_EXEMPTIONS: tuple[tuple[str, str], ...] = (
    ("alan/workspace.py", "create"),
    ("alan/workspace.py", "transition"),
    ("alan/workspace.py", "update_task_status"),
)
STATUS_WRITE_EXEMPTIONS_COUNT = 3

_EXEMPT: frozenset[tuple[str, str]] = frozenset(STATUS_WRITE_EXEMPTIONS)


@dataclass(frozen=True)
class Violation:
    lineno: int
    function: str
    message: str


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _FUNCTION_NODES):
            return current
        current = parents.get(current)
    return None


def _names_status(scope: ast.AST) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id == _STATUS)
        or (isinstance(node, ast.Constant) and node.value == _STATUS)
        or (isinstance(node, ast.keyword) and node.arg == _STATUS)
        for node in ast.walk(scope)
    )


def _unplanned_writer_violations(
    tree: ast.Module,
    filename: str,
    parents: dict[ast.AST, ast.AST],
    exemptions: frozenset[tuple[str, str]],
) -> list[Violation]:
    seen: set[tuple[str, str]] = set()
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != _WRITE_META:
            continue
        function = _enclosing_function(node, parents)
        name = function.name if function is not None else "<module>"
        key = (filename, name)
        if key in exemptions or key in seen or not _names_status(function or tree):
            continue
        seen.add(key)
        violations.append(
            Violation(
                node.lineno,
                name,
                f"{filename}:{node.lineno} {name} persists meta and writes status "
                "outside the status-write exemption set",
            )
        )
    return violations


def _meta_shaped(node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "meta"
    if isinstance(node, ast.Name):
        return node.id == "meta" or node.id.endswith("_meta")
    return isinstance(node, ast.Call) and _call_name(node) in _META_CALLS


def _dict_has_status_key(node: ast.expr) -> bool:
    return isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == _STATUS for key in node.keys
    )


def _binds_status(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Assign) and _dict_has_status_key(node.value):
        return any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return (
            isinstance(node.target, ast.Name)
            and node.target.id == name
            and _dict_has_status_key(node.value)
        )
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == _STATUS
    )


def _update_carries_status(
    update: ast.expr, scope: ast.AST
) -> bool:
    if _dict_has_status_key(update):
        return True
    return isinstance(update, ast.Name) and any(
        _binds_status(node, update.id) for node in ast.walk(scope)
    )


def _meta_copy_violations(
    tree: ast.Module, filename: str, parents: dict[ast.AST, ast.AST]
) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != _MODEL_COPY
            or not _meta_shaped(node.func.value)
        ):
            continue
        function = _enclosing_function(node, parents)
        scope: ast.AST = function or tree
        if any(
            kw.arg == "update" and _update_carries_status(kw.value, scope)
            for kw in node.keywords
        ):
            name = function.name if function is not None else "<module>"
            violations.append(
                Violation(
                    node.lineno,
                    name,
                    f"{filename}:{node.lineno} model_copy on Meta writes a "
                    f"{_STATUS!r} key directly instead of deriving it from "
                    "lifecycle.transition",
                )
            )
    return violations


def find_status_write_violations(
    source: str,
    filename: str,
    exemptions: frozenset[tuple[str, str]] = _EXEMPT,
) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    parents = _build_parents(tree)
    return [
        *_unplanned_writer_violations(tree, filename, parents, exemptions),
        *_meta_copy_violations(tree, filename, parents),
    ]
