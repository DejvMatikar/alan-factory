from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.gh_read_baseline import GH_READ_STUBBED_TESTS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_PATH = _REPO_ROOT / "tests" / "gh_read_baseline.py"
_FUNCTION_LEVEL_KEY = re.compile(r"^tests/[\w/.-]+\.py::[A-Za-z_][\w:]*$")
_GUARD_SUITES = (
    "tests/test_gh_guard.py::",
    "tests/test_gh_read_ratchet.py::",
    "tests/test_gh_read_baseline.py::",
    "tests/test_conftest_",
)


def _test_function_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_every_baseline_entry_resolves_to_an_existing_test_function() -> None:
    stale: list[str] = []
    names_by_module: dict[str, set[str]] = {}
    for key in sorted(GH_READ_STUBBED_TESTS):
        module, _, rest = key.partition("::")
        module_path = _REPO_ROOT / module
        if not module_path.is_file():
            stale.append(key)
            continue
        names = names_by_module.setdefault(module, _test_function_names(module_path))
        if rest.rsplit("::", 1)[-1] not in names:
            stale.append(key)
    assert not stale, (
        "tests/gh_read_baseline.py names tests that no longer exist; delete these "
        "entries (the baseline only shrinks): " + ", ".join(stale)
    )


def test_baseline_entries_are_function_level_keys() -> None:
    malformed = [key for key in GH_READ_STUBBED_TESTS if not _FUNCTION_LEVEL_KEY.match(key)]
    assert malformed == []


def test_baseline_contains_only_literal_data() -> None:
    tree = ast.parse(_BASELINE_PATH.read_text(encoding="utf-8"))
    assert len(tree.body) == 2
    future_import, assignment = tree.body
    assert isinstance(future_import, ast.ImportFrom)
    assert future_import.module == "__future__"
    assert isinstance(assignment, ast.AnnAssign)
    value = assignment.value
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name)
    assert value.func.id == "frozenset"
    assert value.keywords == []
    for argument in value.args:
        assert isinstance(argument, ast.Set)
        assert all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in argument.elts
        )


def test_baseline_never_lists_the_guard_suites() -> None:
    listed_guards = [
        key for key in GH_READ_STUBBED_TESTS if key.startswith(_GUARD_SUITES)
    ]
    assert listed_guards == []
