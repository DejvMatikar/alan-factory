from __future__ import annotations

import ast

ALLOWLIST: frozenset[str] = frozenset()

_TARGET_MODULE = "alan.__main__"
_TARGET_FUNCS = frozenset({"main", "_configure_logging"})
_MODULE_BINDING = "module"
_FUNC_BINDING_PREFIX = "func:"

REMEDY = (
    "alan.__main__.main()/_configure_logging() permanently mutate the process-wide "
    '"alan" logger\'s level via a raw logging.getLogger("alan").setLevel(...) call — '
    "correct for the real server process, which configures logging exactly once at "
    "startup, but a test that calls either function without isolating that side "
    "effect leaks the lowered level to every test that runs after it in the same "
    "process. That is exactly the class of bug this gate exists to catch: an "
    "unrelated, unmarked INFO-level alan.slot_pool log line became visible to a "
    "later test's caplog capture only once the level leaked, and that later test's "
    "own name happened to contain the substring its assertion was checking for — "
    "order-dependent contamination that only a serial full-suite run ever "
    "exercises. Wrap the call in try/finally: save "
    'logging.getLogger("alan").level before the call and restore it with '
    ".setLevel(...) in the finally block (see tests/test_main.py::_run_main and "
    "tests/test_main_utc_logging.py). This gate catches only this one call pair left "
    "unguarded, not global-state leaks reached through any other transitive call, "
    "and not order-dependence in general — a narrower net chosen deliberately for "
    "near-zero runtime cost over the 426s-vs-107s cost of running the whole suite "
    "serially on every pull request. A leak reached through a different call needs "
    "a new target added here, or a new gate; this is not general interprocedural "
    "analysis, by design."
)


def _record_import(node: ast.Import, bindings: dict[str, str]) -> None:
    for alias in node.names:
        if alias.name == _TARGET_MODULE:
            bindings[alias.asname or alias.name] = _MODULE_BINDING


def _record_import_from(node: ast.ImportFrom, bindings: dict[str, str]) -> None:
    if node.module == "alan":
        for alias in node.names:
            if alias.name == "__main__":
                bindings[alias.asname or alias.name] = _MODULE_BINDING
    elif node.module == _TARGET_MODULE:
        for alias in node.names:
            if alias.name in _TARGET_FUNCS:
                bindings[alias.asname or alias.name] = _FUNC_BINDING_PREFIX + alias.name


def _bound_names(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _record_import(node, bindings)
        elif isinstance(node, ast.ImportFrom):
            _record_import_from(node, bindings)
    return bindings


def _dotted_path(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_path(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _call_reason(node: ast.Call, bindings: dict[str, str]) -> str | None:
    func = node.func
    target: str | None = None
    if isinstance(func, ast.Attribute) and func.attr in _TARGET_FUNCS:
        receiver = _dotted_path(func.value)
        if receiver is not None and bindings.get(receiver) == _MODULE_BINDING:
            target = func.attr
    elif isinstance(func, ast.Name):
        binding = bindings.get(func.id)
        if binding is not None and binding.startswith(_FUNC_BINDING_PREFIX):
            target = binding[len(_FUNC_BINDING_PREFIX):]
    if target is None:
        return None
    return (
        f'calls alan.__main__.{target}, which mutates the shared "alan" logger\'s '
        "level with no enclosing try/finally to restore it"
    )


def _scan(
    node: ast.AST, protected: bool, bindings: dict[str, str], hits: list[tuple[int, str]]
) -> None:
    if isinstance(node, ast.Try):
        body_protected = protected or bool(node.finalbody)
        for child in (*node.body, *node.orelse):
            _scan(child, body_protected, bindings, hits)
        for handler in node.handlers:
            _scan(handler, body_protected, bindings, hits)
        for child in node.finalbody:
            _scan(child, protected, bindings, hits)
        return
    if isinstance(node, ast.Call):
        reason = _call_reason(node, bindings)
        if reason is not None and not protected:
            hits.append((node.lineno, reason))
    for kid in ast.iter_child_nodes(node):
        _scan(kid, protected, bindings, hits)


def unprotected_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    bindings = _bound_names(tree)
    hits: list[tuple[int, str]] = []
    _scan(tree, False, bindings, hits)
    return sorted(hits)


def violations_in(source: str, rel_path: str, allowed: frozenset[str]) -> list[str]:
    if rel_path in allowed:
        return []
    return [f"{rel_path}:{line}: {reason}" for line, reason in unprotected_calls(source)]
