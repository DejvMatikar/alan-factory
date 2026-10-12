from __future__ import annotations

import ast
from pathlib import Path

from tests.reexport_facade import REEXPORT_FACADE_ALLOWLIST

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"


def _is_identity_alias(alias: ast.alias) -> bool:
    return alias.asname is not None and alias.asname == alias.name


def _identity_reexport_aliases(tree: ast.Module) -> list[tuple[int, str]]:
    aliases: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.extend(
                (node.lineno, alias.name) for alias in node.names if _is_identity_alias(alias)
            )
    return aliases


def _loaded_names(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )


def _module_all_names(tree: ast.Module) -> frozenset[str]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return frozenset(
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    return frozenset()


def _is_eligible(module_basename: str, name: str) -> bool:
    return module_basename.startswith("_") or name.startswith("_")


def _classify_source(source: str, module_basename: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    loaded = _loaded_names(tree)
    exported = _module_all_names(tree)
    return [
        (lineno, name)
        for lineno, name in _identity_reexport_aliases(tree)
        if name not in loaded and name not in exported and _is_eligible(module_basename, name)
    ]


def _violations_for_path(path: Path, module_key: str, allow: frozenset[str]) -> list[str]:
    if module_key in allow:
        return []
    hits = _classify_source(path.read_text(encoding="utf-8"), path.stem)
    return [
        f"{module_key}:{lineno} unused private identity re-export {name!r} outside the "
        "allowlist; delete the re-export (fix the source) or add the name to __all__ if it "
        "is a genuine deliberate facade — never add an allowlist entry"
        for lineno, name in hits
    ]


def _alan_modules() -> list[Path]:
    return sorted(p for p in _ALAN_SRC_ROOT.rglob("*.py") if p.name != "__init__.py")


def test_no_unused_private_reexport_facade_outside_allowlist() -> None:
    violations: list[str] = []
    for path in _alan_modules():
        module_key = path.relative_to(_REPO_ROOT).as_posix()
        violations.extend(_violations_for_path(path, module_key, REEXPORT_FACADE_ALLOWLIST))
    assert not violations, "\n".join(violations)


def test_init_files_excluded_from_the_walk() -> None:
    assert all(p.name != "__init__.py" for p in _alan_modules())


def test_private_module_unused_public_name_is_flagged() -> None:
    violations = _classify_source("from x import Foo as Foo\n", "_private_mod")
    assert violations == [(1, "Foo")]


def test_public_module_unused_private_name_is_flagged() -> None:
    violations = _classify_source("from x import _foo as _foo\n", "public_mod")
    assert violations == [(1, "_foo")]


def test_private_module_unused_private_name_is_flagged() -> None:
    violations = _classify_source("from x import _foo as _foo\n", "_private_mod")
    assert violations == [(1, "_foo")]


def test_used_via_name_load_is_not_flagged() -> None:
    source = "from x import Foo as Foo\n\nFoo()\n"
    assert _classify_source(source, "_private_mod") == []


def test_used_via_attribute_load_is_not_flagged() -> None:
    source = "from x import Foo as Foo\n\nFoo.bar()\n"
    assert _classify_source(source, "_private_mod") == []


def test_public_module_public_name_is_not_flagged() -> None:
    assert _classify_source("from x import Foo as Foo\n", "public_mod") == []


def test_non_identity_alias_is_not_flagged() -> None:
    assert _classify_source("from x import Foo as Bar\n", "_private_mod") == []


def test_name_listed_in_all_is_not_flagged() -> None:
    source = "from x import Foo as Foo\n\n__all__ = ['Foo']\n"
    assert _classify_source(source, "_private_mod") == []


def test_violations_for_path_fails_closed_on_unlisted_module(tmp_path: Path) -> None:
    surface = tmp_path / "_surface.py"
    surface.write_text("from x import Foo as Foo\n", encoding="utf-8")
    violations = _violations_for_path(surface, "fake/_surface.py", frozenset())
    assert violations
    assert "Foo" in violations[0]


def test_violations_for_path_passes_when_module_is_allowlisted(tmp_path: Path) -> None:
    surface = tmp_path / "_surface.py"
    surface.write_text("from x import Foo as Foo\n", encoding="utf-8")
    violations = _violations_for_path(
        surface, "fake/_surface.py", frozenset({"fake/_surface.py"})
    )
    assert violations == []


def test_reexport_facade_allowlist_does_not_grow() -> None:
    assert len(REEXPORT_FACADE_ALLOWLIST) == 0


def test_allowlist_entries_reference_existing_files() -> None:
    for module_key in REEXPORT_FACADE_ALLOWLIST:
        assert (_REPO_ROOT / module_key).is_file(), module_key
