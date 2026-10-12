from __future__ import annotations

import ast
import io
import tokenize
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN = _REPO_ROOT / "alan"
_ROUTES = _ALAN / "routes"
_SHARED_RESPONSES_MODULE = _ROUTES / "_deps.py"
_SERVER = _ALAN / "server.py"


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "web" not in p.relative_to(_ALAN).parts[:1])


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _raises_http_exception(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "HTTPException":
            lines.append(node.lineno)
    return lines


def _imports_module(tree: ast.Module, module: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            return True
        if isinstance(node, ast.Import) and any(a.name == module for a in node.names):
            return True
    return False


def _layers() -> list[str]:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contract = next(
        c for c in data["tool"]["importlinter"]["contracts"] if c["name"] == "Layered architecture"
    )
    layers: list[str] = contract["layers"]
    return layers


def test_no_module_outside_the_route_layer_raises_http_exception() -> None:
    offenders = {
        f"{_relative(path)}:{line}"
        for path in _python_files(_ALAN)
        if _ROUTES not in path.parents
        for line in _raises_http_exception(_parse(path))
    }
    assert not offenders, sorted(offenders)


def test_the_application_layer_never_names_the_http_exception_type() -> None:
    offenders = [
        _relative(path)
        for path in _python_files(_ALAN / "app")
        if "HTTPException" in path.read_text(encoding="utf-8")
    ]
    assert not offenders


def test_control_plane_and_errors_do_not_import_fastapi() -> None:
    for module in (_ALAN / "control_plane.py", _ALAN / "errors.py"):
        assert not _imports_module(_parse(module), "fastapi"), _relative(module)


def test_errors_module_imports_nothing_from_alan() -> None:
    tree = _parse(_ALAN / "errors.py")
    imported = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert not [name for name in imported if name.split(".")[0] == "alan"]


def test_server_module_carries_no_unused_import_suppressions() -> None:
    source = _SERVER.read_text(encoding="utf-8")
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    assert not [comment for comment in comments if "F401" in comment], comments


def _int_keys_of_dict_literals(tree: ast.Module) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, int):
                hits.append((key.value, key.lineno))
    return hits


def test_no_route_module_writes_a_401_response_entry_inline() -> None:
    offenders = [
        f"{_relative(path)}:{line}"
        for path in _python_files(_ROUTES)
        if path != _SHARED_RESPONSES_MODULE
        for status, line in _int_keys_of_dict_literals(_parse(path))
        if status == 401
    ]
    assert not offenders, offenders


def test_the_shared_module_defines_both_named_401_descriptions() -> None:
    lines = [
        line for status, line in _int_keys_of_dict_literals(_parse(_SHARED_RESPONSES_MODULE))
        if status == 401
    ]
    assert len(lines) == 2


def test_route_dependency_module_stays_under_the_backend_module_cap() -> None:
    non_blank = [
        line for line in _SHARED_RESPONSES_MODULE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(non_blank) <= 800


def test_the_application_layer_sits_directly_beneath_the_routes() -> None:
    layers = _layers()
    assert layers.index("app") == layers.index("routes") + 1
    assert layers.index("app") < layers.index("dispatch")
    assert layers.index("app") < layers.index("dispatcher")


def test_the_error_type_sits_below_every_module_that_raises_it() -> None:
    layers = _layers()
    errors_index = layers.index("errors")
    for raiser in ("dispatch", "project_amendment", "unblock_sweep", "unblock_recipes", "app"):
        assert errors_index > layers.index(raiser), raiser
    assert errors_index > layers.index("routes")
    assert errors_index > layers.index("server")
