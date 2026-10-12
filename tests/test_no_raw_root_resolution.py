from __future__ import annotations

import ast
from pathlib import Path

from alan.roots import ALL_KNOWN_ROOTS
from tests.raw_root_resolution import DYNAMIC_ENV_READS, RAW_ROOT_RESOLUTIONS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"
_ROOTS_MODULE = _ALAN_SRC_ROOT / "roots.py"


def _is_os_name(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Name) and expr.id == "os"


def _is_bare_name(expr: ast.expr, name: str) -> bool:
    return isinstance(expr, ast.Name) and expr.id == name


def _is_os_environ(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Attribute) and expr.attr == "environ" and _is_os_name(expr.value):
        return True
    return _is_bare_name(expr, "environ")


def _attribute_read_key(node: ast.Call, func: ast.Attribute) -> ast.expr | None:
    is_environ_get = func.attr == "get" and _is_os_environ(func.value)
    is_os_getenv = func.attr == "getenv" and _is_os_name(func.value)
    if not (is_environ_get or is_os_getenv):
        return None
    return node.args[0] if node.args else None


def _bare_getenv_read_key(node: ast.Call, func: ast.Name) -> ast.expr | None:
    if func.id != "getenv":
        return None
    return node.args[0] if node.args else None


def _call_read_key(node: ast.Call) -> ast.expr | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return _attribute_read_key(node, func)
    if isinstance(func, ast.Name):
        return _bare_getenv_read_key(node, func)
    return None


def _subscript_read_key(node: ast.Subscript) -> ast.expr | None:
    if isinstance(node.ctx, ast.Load) and _is_os_environ(node.value):
        return node.slice
    return None


def _single_name_target(node: ast.stmt) -> str | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return node.target.id
    return None


def _assign_value(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return node.value
    return None


def _string_constant_assign(node: ast.stmt) -> tuple[str | None, str | None]:
    name = _single_name_target(node)
    if name is None:
        return (None, None)
    value_node = _assign_value(node)
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
        return (name, value_node.value)
    return (name, None)


def _module_level_string_constants(tree: ast.Module) -> dict[str, str]:
    values: dict[str, str] = {}
    seen: set[str] = set()
    reassigned: set[str] = set()
    for node in tree.body:
        name, value = _string_constant_assign(node)
        if name is None:
            continue
        if name in seen:
            reassigned.add(name)
            continue
        seen.add(name)
        if value is not None:
            values[name] = value
    return {name: value for name, value in values.items() if name not in reassigned}


class _RootAccessVisitor(ast.NodeVisitor):
    def __init__(self, folded: dict[str, str]) -> None:
        self.hits: list[tuple[int, str | None]] = []
        self._folded = folded

    def _record(self, lineno: int, key_node: ast.expr | None) -> None:
        if key_node is None:
            return
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            self.hits.append((lineno, key_node.value))
        elif isinstance(key_node, ast.Name) and key_node.id in self._folded:
            self.hits.append((lineno, self._folded[key_node.id]))
        else:
            self.hits.append((lineno, None))

    def visit_Call(self, node: ast.Call) -> None:
        self._record(node.lineno, _call_read_key(node))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record(node.lineno, _subscript_read_key(node))
        self.generic_visit(node)


def _classify_source(source: str) -> tuple[list[tuple[int, str]], list[int]]:
    tree = ast.parse(source)
    folded = _module_level_string_constants(tree)
    visitor = _RootAccessVisitor(folded)
    visitor.visit(tree)
    literal_roots = [
        (lineno, token)
        for lineno, token in visitor.hits
        if token is not None and token in ALL_KNOWN_ROOTS
    ]
    dynamic = [lineno for lineno, token in visitor.hits if token is None]
    return literal_roots, dynamic


def _violation_messages(
    module_key: str,
    literal_roots: list[tuple[int, str]],
    dynamic: list[int],
    raw_allow: frozenset[str],
    dynamic_allow: frozenset[str],
) -> list[str]:
    violations: list[str] = []
    if literal_roots and module_key not in raw_allow:
        lineno, token = literal_roots[0]
        violations.append(
            f"{module_key}:{lineno} reads registered root {token!r} directly via os.environ; "
            "register in tests/raw_root_resolution.py or migrate via alan.roots"
        )
    if dynamic and module_key not in dynamic_allow:
        violations.append(
            f"{module_key}:{dynamic[0]} reads os.environ with a non-literal key; "
            "register in tests/raw_root_resolution.py or migrate via alan.roots"
        )
    return violations


def _violations_for_path(
    path: Path, module_key: str, raw_allow: frozenset[str], dynamic_allow: frozenset[str]
) -> list[str]:
    literal_roots, dynamic = _classify_source(path.read_text(encoding="utf-8"))
    return _violation_messages(module_key, literal_roots, dynamic, raw_allow, dynamic_allow)


def _alan_modules() -> list[Path]:
    return sorted(p for p in _ALAN_SRC_ROOT.rglob("*.py") if p != _ROOTS_MODULE)


def test_no_raw_root_resolution_outside_allowlists() -> None:
    violations: list[str] = []
    for path in _alan_modules():
        module_key = path.relative_to(_REPO_ROOT).as_posix()
        violations.extend(
            _violations_for_path(path, module_key, RAW_ROOT_RESOLUTIONS, DYNAMIC_ENV_READS)
        )
    assert not violations, "\n".join(violations)


def test_roots_module_is_excluded_from_the_walk() -> None:
    assert _ROOTS_MODULE not in _alan_modules()


def test_violations_for_path_fails_closed_on_unlisted_module(tmp_path: Path) -> None:
    surface = tmp_path / "surface.py"
    surface.write_text("import os\nos.environ.get('ALAN_STATE_ROOT', '')\n", encoding="utf-8")
    violations = _violations_for_path(surface, "fake/surface.py", frozenset(), frozenset())
    assert violations
    assert "ALAN_STATE_ROOT" in violations[0]


def test_violations_for_path_passes_when_module_is_allowlisted(tmp_path: Path) -> None:
    surface = tmp_path / "surface.py"
    surface.write_text("import os\nos.environ.get('ALAN_STATE_ROOT', '')\n", encoding="utf-8")
    violations = _violations_for_path(
        surface, "fake/surface.py", frozenset({"fake/surface.py"}), frozenset()
    )
    assert violations == []


def test_raw_root_resolutions_allowlist_does_not_grow() -> None:
    assert len(RAW_ROOT_RESOLUTIONS) == 0


def test_dynamic_env_reads_allowlist_does_not_grow() -> None:
    assert len(DYNAMIC_ENV_READS) <= 6


def test_allowlist_entries_reference_existing_files() -> None:
    for module_key in RAW_ROOT_RESOLUTIONS | DYNAMIC_ENV_READS:
        assert (_REPO_ROOT / module_key).is_file(), module_key


def test_module_level_literal_root_token_is_folded_to_raw() -> None:
    source = 'import os\n_TOKEN = "ALAN_STATE_ROOT"\nos.environ.get(_TOKEN, "")\n'
    literal_roots, dynamic = _classify_source(source)
    assert literal_roots == [(3, "ALAN_STATE_ROOT")]
    assert dynamic == []


def test_module_level_reassigned_root_token_name_stays_dynamic() -> None:
    source = (
        "import os\n"
        '_TOKEN = "ALAN_STATE_ROOT"\n'
        '_TOKEN = "ALAN_LOGS_ROOT"\n'
        'os.environ.get(_TOKEN, "")\n'
    )
    literal_roots, dynamic = _classify_source(source)
    assert literal_roots == []
    assert dynamic == [4]


def test_function_local_root_token_literal_stays_dynamic() -> None:
    source = (
        "import os\n"
        "def f():\n"
        '    _token = "ALAN_STATE_ROOT"\n'
        '    return os.environ.get(_token, "")\n'
    )
    literal_roots, dynamic = _classify_source(source)
    assert literal_roots == []
    assert dynamic == [4]


def test_rule1_dict_call_bulk_access_is_out_of_scope() -> None:
    literal_roots, dynamic = _classify_source("import os\nenv = dict(os.environ)\n")
    assert literal_roots == []
    assert dynamic == []


def test_rule1_dict_unpack_bulk_access_is_out_of_scope() -> None:
    literal_roots, dynamic = _classify_source("import os\nenv = {**os.environ, 'X': '1'}\n")
    assert literal_roots == []
    assert dynamic == []


def test_rule2_subscript_assignment_write_is_exempt() -> None:
    literal_roots, dynamic = _classify_source(
        "import os\nos.environ['ALAN_STATE_ROOT'] = '/tmp/x'\n"
    )
    assert literal_roots == []
    assert dynamic == []


def test_rule2_pop_call_write_is_exempt() -> None:
    literal_roots, dynamic = _classify_source(
        "import os\nos.environ.pop('ALAN_STATE_ROOT', None)\n"
    )
    assert literal_roots == []
    assert dynamic == []


def test_rule3_get_call_with_literal_root_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source("import os\nos.environ.get('ALAN_LOGS_ROOT', '')\n")
    assert literal_roots == [(2, "ALAN_LOGS_ROOT")]
    assert dynamic == []


def test_rule3_subscript_with_literal_root_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source("import os\nos.environ['ALAN_LOGS_ROOT']\n")
    assert literal_roots == [(2, "ALAN_LOGS_ROOT")]
    assert dynamic == []


def test_rule3_literal_non_root_key_needs_no_entry() -> None:
    literal_roots, dynamic = _classify_source("import os\nos.environ.get('SOME_OTHER_VAR', '')\n")
    assert literal_roots == []
    assert dynamic == []


def test_rule4_get_call_with_name_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source(
        "import os\nenv_name = str(1)\nos.environ.get(env_name)\n"
    )
    assert literal_roots == []
    assert dynamic == [3]


def test_rule4_getenv_call_with_name_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source(
        "import os\nenv_name = str(1)\nos.getenv(env_name)\n"
    )
    assert literal_roots == []
    assert dynamic == [3]


def test_rule3_bare_environ_get_call_with_literal_root_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source(
        "from os import environ\nenviron.get('ALAN_LOGS_ROOT', '')\n"
    )
    assert literal_roots == [(2, "ALAN_LOGS_ROOT")]
    assert dynamic == []


def test_rule3_bare_environ_subscript_with_literal_root_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source(
        "from os import environ\nenviron['ALAN_LOGS_ROOT']\n"
    )
    assert literal_roots == [(2, "ALAN_LOGS_ROOT")]
    assert dynamic == []


def test_rule4_bare_getenv_call_with_name_key_is_in_scope() -> None:
    literal_roots, dynamic = _classify_source(
        "from os import getenv\nenv_name = str(1)\ngetenv(env_name)\n"
    )
    assert literal_roots == []
    assert dynamic == [3]
