from __future__ import annotations

import ast
from pathlib import Path

from tests.raw_clock_reads import CLOCK_MIGRATED, RAW_CLOCK_READS

_REPO_ROOT = Path(__file__).resolve().parent.parent

_TIME_ATTRS = frozenset({
    "time", "time_ns",
    "monotonic", "monotonic_ns",
    "perf_counter", "perf_counter_ns",
})


def _is_datetime_now_call(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if isinstance(func, ast.Attribute) and func.attr == "now":
        value = func.value
        return isinstance(value, ast.Name) and value.id == "datetime"
    return False


class _RawClockReadVisitor(ast.NodeVisitor):
    def __init__(self, imported_time_names: set[str], time_module_names: set[str]) -> None:
        self.hits: list[int] = []
        self._imported_time_names = imported_time_names
        self._time_module_names = time_module_names

    def _is_time_module_name(self, expr: ast.expr) -> bool:
        return isinstance(expr, ast.Name) and expr.id in self._time_module_names

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _TIME_ATTRS and self._is_time_module_name(func.value):
                self.hits.append(node.lineno)
            elif func.attr == "timestamp" and _is_datetime_now_call(func.value):
                self.hits.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id in self._imported_time_names:
            self.hits.append(node.lineno)
        self.generic_visit(node)


def _time_import_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            for alias in node.names:
                if alias.name in _TIME_ATTRS:
                    names.add(alias.asname or alias.name)
    return names


def _time_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "time":
                    names.add(alias.asname or alias.name)
    return names


def _raw_clock_read_lines(source: str) -> list[int]:
    tree = ast.parse(source)
    imported = _time_import_names(tree)
    modules = _time_module_names(tree)
    visitor = _RawClockReadVisitor(imported, modules)
    visitor.visit(tree)
    return visitor.hits


def _violations_for_module(path: Path, module_key: str, exempt: frozenset[str]) -> list[str]:
    if module_key in exempt:
        return []
    lines = _raw_clock_read_lines(path.read_text(encoding="utf-8"))
    return [
        f"{module_key}:{lineno} reads raw stdlib time; migrate onto alan.clock"
        for lineno in lines
    ]


def test_no_raw_clock_reads_in_migrated_modules() -> None:
    violations: list[str] = []
    for module_key in sorted(CLOCK_MIGRATED):
        violations.extend(
            _violations_for_module(_REPO_ROOT / module_key, module_key, RAW_CLOCK_READS)
        )
    assert not violations, "\n".join(violations)


def test_raw_clock_reads_allowlist_is_empty() -> None:
    assert len(RAW_CLOCK_READS) == 0


def test_allowlist_entries_reference_existing_files() -> None:
    for module_key in CLOCK_MIGRATED | RAW_CLOCK_READS:
        assert (_REPO_ROOT / module_key).is_file(), module_key


def test_violations_for_module_fails_closed_on_raw_read(tmp_path: Path) -> None:
    surface = tmp_path / "surface.py"
    surface.write_text("import time\ntime.monotonic()\n", encoding="utf-8")
    violations = _violations_for_module(surface, "fake/surface.py", frozenset())
    assert violations
    assert "fake/surface.py:2" in violations[0]


def test_violations_for_module_passes_when_exempted(tmp_path: Path) -> None:
    surface = tmp_path / "surface.py"
    surface.write_text("import time\ntime.monotonic()\n", encoding="utf-8")
    violations = _violations_for_module(
        surface, "fake/surface.py", frozenset({"fake/surface.py"})
    )
    assert violations == []


def test_rule_attribute_call_after_import_time_is_detected() -> None:
    assert _raw_clock_read_lines("import time\ntime.time()\n") == [2]


def test_rule_bare_name_call_after_from_time_import_is_detected() -> None:
    assert _raw_clock_read_lines("from time import monotonic\nmonotonic()\n") == [2]


def test_rule_ns_variant_is_detected() -> None:
    assert _raw_clock_read_lines("import time\ntime.monotonic_ns()\n") == [2]


def test_rule_attribute_call_after_aliased_import_time_is_detected() -> None:
    assert _raw_clock_read_lines("import time as t\nt.monotonic()\n") == [2]


def test_rule_datetime_now_timestamp_is_detected() -> None:
    assert _raw_clock_read_lines(
        "from datetime import datetime\ndatetime.now().timestamp()\n"
    ) == [2]


def test_rule_datetime_now_isoformat_is_not_detected() -> None:
    assert _raw_clock_read_lines(
        "from datetime import UTC, datetime\ndatetime.now(UTC).isoformat()\n"
    ) == []
