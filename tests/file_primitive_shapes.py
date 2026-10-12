from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALAN_ROOT = REPO_ROOT / "alan"
WEB_ROOT = ALAN_ROOT / "web"
STORE_PATH = ALAN_ROOT / "store.py"

FunctionLike = ast.FunctionDef | ast.AsyncFunctionDef


def scanned_source_files() -> list[Path]:
    return sorted(
        p for p in ALAN_ROOT.rglob("*.py")
        if WEB_ROOT not in p.parents and p != STORE_PATH
    )


def module_functions(path: Path) -> list[tuple[str, FunctionLike]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        (path.relative_to(REPO_ROOT).as_posix(), node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _call_func_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_call_to(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and _call_func_name(node.func) == name


def _mentions_any(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _temp_path_targets(fn: FunctionLike) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if _is_call_to(value, "with_suffix") or _is_call_to(value, "with_name"):
            targets.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(value, ast.Call) and _call_func_name(value.func) == "mkstemp":
            for t in node.targets:
                if isinstance(t, ast.Tuple):
                    targets.update(e.id for e in t.elts if isinstance(e, ast.Name))
    return targets


def _replaces_target(fn: FunctionLike, targets: set[str]) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "replace":
            continue
        if isinstance(func.value, ast.Name) and func.value.id in targets:
            return True
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in targets
        ):
            return True
    return False


def is_writer_shape(fn: FunctionLike) -> bool:
    targets = _temp_path_targets(fn)
    if not targets:
        return False
    return _replaces_target(fn, targets)


def _stat_result_names(fn: FunctionLike) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _is_call_to(node.value, "stat"):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _is_mtime_size_tuple(value: ast.expr, stat_names: set[str]) -> bool:
    if not isinstance(value, ast.Tuple) or len(value.elts) != 2:
        return False
    a, b = value.elts
    return (
        isinstance(a, ast.Attribute)
        and a.attr == "st_mtime_ns"
        and isinstance(a.value, ast.Name)
        and a.value.id in stat_names
        and isinstance(b, ast.Attribute)
        and b.attr == "st_size"
        and isinstance(b.value, ast.Name)
        and b.value.id in stat_names
    )


def is_signature_shape(fn: FunctionLike) -> bool:
    stat_names = _stat_result_names(fn)
    if not stat_names:
        return False
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Return)
            and node.value is not None
            and _is_mtime_size_tuple(node.value, stat_names)
        ):
            return True
    return False


def _size_from_stat_names(fn: FunctionLike) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "st_size"
            and isinstance(value.value, ast.Call)
            and _call_func_name(value.value.func) == "stat"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _expand_derived_names(fn: FunctionLike, seed: set[str]) -> set[str]:
    names = set(seed)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign) or not _mentions_any(node.value, names):
                continue
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in names:
                    names.add(t.id)
                    changed = True
    return names


def is_tail_shape(fn: FunctionLike) -> bool:
    seed = _size_from_stat_names(fn)
    if not seed:
        return False
    derived = _expand_derived_names(fn, seed)
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and _call_func_name(node.func) == "seek"
            and node.args
            and _mentions_any(node.args[0], derived)
        ):
            return True
    return False


def _single_str_param_name(fn: FunctionLike) -> str | None:
    args = fn.args.args
    is_method = bool(args) and args[0].arg == "self"
    positional = args[1:] if is_method else args
    if len(positional) != 1:
        return None
    param = positional[0]
    if param.annotation is None:
        return None
    if not (isinstance(param.annotation, ast.Name) and param.annotation.id == "str"):
        return None
    return param.arg


def _chain_base_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _expand_via_chain_base(fn: FunctionLike, seed: set[str]) -> set[str]:
    names = set(seed)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(fn):
            pair = _assignment_target_and_value(node)
            if pair is None:
                continue
            target, value = pair
            if not isinstance(target, ast.Name) or target.id in names:
                continue
            if _chain_base_name(value) in names:
                names.add(target.id)
                changed = True
    return names


def _json_decode_error_try(
    fn: FunctionLike, param: str
) -> tuple[ast.Try, str] | None:
    derived = _expand_via_chain_base(fn, {param})
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            assigned = _json_loads_assignment(stmt, derived)
            if assigned is not None and _catches_decode_error(node) and not _handlers_raise(node):
                return node, assigned
    return None


def _assignment_target_and_value(stmt: ast.AST) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and stmt.value is not None:
        return stmt.targets[0], stmt.value
    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        return stmt.target, stmt.value
    return None


def _json_loads_assignment(stmt: ast.stmt, derived_names: set[str]) -> str | None:
    pair = _assignment_target_and_value(stmt)
    if pair is None:
        return None
    target, value = pair
    if not isinstance(target, ast.Name):
        return None
    if not (isinstance(value, ast.Call) and _call_func_name(value.func) == "loads"):
        return None
    if value.args and _mentions_any(value.args[0], derived_names):
        return target.id
    return None


def _catches_decode_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        for exc_name in _handler_exception_names(handler):
            if exc_name in {"JSONDecodeError", "ValueError"}:
                return True
    return False


def _handler_exception_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return []
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "") for t in types]


def _handlers_raise(node: ast.Try) -> bool:
    return any(
        isinstance(n, ast.Raise) for handler in node.handlers for n in ast.walk(handler)
    )


def _is_passthrough_return(value: ast.expr, parsed_name: str) -> bool:
    unwrapped = value
    is_cast = isinstance(unwrapped, ast.Call) and _call_func_name(unwrapped.func) == "cast"
    if is_cast and isinstance(unwrapped, ast.Call) and unwrapped.args:
        unwrapped = unwrapped.args[-1]
    if isinstance(unwrapped, ast.Name):
        return unwrapped.id == parsed_name
    if isinstance(unwrapped, ast.IfExp):
        return (
            _mentions_any(unwrapped.body, {parsed_name})
            and _is_call_to(unwrapped.test, "isinstance")
            and isinstance(unwrapped.test, ast.Call)
            and any(isinstance(a, ast.Name) and a.id == parsed_name for a in unwrapped.test.args)
        )
    return False


def is_row_parser_shape(fn: FunctionLike) -> bool:
    param = _single_str_param_name(fn)
    if param is None:
        return False
    found = _json_decode_error_try(fn, param)
    if found is None:
        return False
    _try_node, parsed_name = found
    final_returns = [
        n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None
    ]
    return any(_is_passthrough_return(r.value, parsed_name) for r in final_returns if r.value)
