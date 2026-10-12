from __future__ import annotations

import ast

ALLOWLIST: frozenset[str] = frozenset()

_TARGET_MODULES = frozenset({"threading", "queue"})

_STDLIB_TARGETS: frozenset[str] = frozenset({
    "threading.Lock",
    "threading.RLock",
    "threading.Condition",
    "threading.Event",
    "threading.Semaphore",
    "threading.BoundedSemaphore",
    "threading.Barrier",
    "queue.Queue",
    "queue.LifoQueue",
    "queue.PriorityQueue",
    "queue.SimpleQueue",
})

_MODULE_BINDING_PREFIX = "module:"

REMEDY = (
    "This test class-scoped-patches a stdlib concurrency primitive "
    "(threading.Lock/RLock/Condition/Event/Semaphore/BoundedSemaphore/Barrier or "
    "queue.Queue/LifoQueue/PriorityQueue/SimpleQueue) via setattr on the class itself "
    "rather than on the one instance under test — every thread in the process observes "
    "the patched behaviour for as long as it is applied, which is order-dependent "
    "contamination that only a serial full-suite run ever exercises. "
    'Patch the instance instead: monkeypatch.setattr(pool._cond, "wait", fake_wait) rather '
    'than monkeypatch.setattr(threading.Condition, "wait", fake_wait), and drop the fake\'s '
    "receiver parameter since an instance-attribute override is called without an implicit "
    "self. This gate catches only this one pollution shape — process-global patching of a "
    "stdlib concurrency primitive class — and not order-dependence in general: a leak "
    "reached through any other kind of shared mutable state is invisible to it. That is a "
    "deliberate, narrower net, not an oversight — the alternative, running the whole suite "
    "serially on every pull request, was measured at 426s serially vs. 107s in normal "
    "parallel mode and declined as a permanent per-PR job to protect an advisory nightly "
    "report. threading.Thread lifecycle patches (join, start, run) and alan.queue.Queue "
    "(this repo's own class, not the stdlib one) are deliberately out of scope."
)


def _record_import(node: ast.Import, bindings: dict[str, str]) -> None:
    for alias in node.names:
        if alias.name in _TARGET_MODULES:
            bindings[alias.asname or alias.name] = _MODULE_BINDING_PREFIX + alias.name


def _record_import_from(node: ast.ImportFrom, bindings: dict[str, str]) -> None:
    if node.module not in _TARGET_MODULES:
        return
    for alias in node.names:
        canonical = f"{node.module}.{alias.name}"
        if canonical in _STDLIB_TARGETS:
            bindings[alias.asname or alias.name] = canonical


def _bound_names(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _record_import(node, bindings)
        elif isinstance(node, ast.ImportFrom):
            _record_import_from(node, bindings)
    return bindings


def _dotted_path(node: ast.expr) -> tuple[str, str | None] | None:
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _resolve_target(node: ast.expr, bindings: dict[str, str]) -> str | None:
    parsed = _dotted_path(node)
    if parsed is None:
        return None
    root, rest = parsed
    binding = bindings.get(root)
    if binding is None:
        return None
    if not binding.startswith(_MODULE_BINDING_PREFIX):
        return None if rest is not None else binding
    module = binding[len(_MODULE_BINDING_PREFIX):]
    canonical = f"{module}.{rest}" if rest is not None else module
    return canonical if canonical in _STDLIB_TARGETS else None


def _is_setattr_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "setattr"
    return isinstance(func, ast.Name) and func.id == "setattr"


def _call_reason(node: ast.Call, bindings: dict[str, str]) -> str | None:
    if not _is_setattr_call(node) or not node.args:
        return None
    target = _resolve_target(node.args[0], bindings)
    if target is None:
        return None
    return (
        f"class-scoped setattr patches {target}, a stdlib concurrency primitive, "
        "process-wide for as long as the patch is applied"
    )


def _scan(node: ast.AST, bindings: dict[str, str], hits: list[tuple[int, str]]) -> None:
    if isinstance(node, ast.Call):
        reason = _call_reason(node, bindings)
        if reason is not None:
            hits.append((node.lineno, reason))
    for kid in ast.iter_child_nodes(node):
        _scan(kid, bindings, hits)


def violating_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    bindings = _bound_names(tree)
    hits: list[tuple[int, str]] = []
    _scan(tree, bindings, hits)
    return sorted(hits)


def violations_in(source: str, rel_path: str, allowed: frozenset[str]) -> list[str]:
    if rel_path in allowed:
        return []
    return [f"{rel_path}:{line}: {reason}" for line, reason in violating_calls(source)]
