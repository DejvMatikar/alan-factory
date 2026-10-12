from __future__ import annotations

import ast
from pathlib import Path

from tests.size_baseline import (
    CLAUDE_MD_BYTE_CEILING,
    METRIC,
    PINNED_FRONTEND_TEST_COUNT,
    PINNED_FRONTEND_TEST_FILES,
    PINNED_FRONTEND_TEST_TOTAL,
    PINNED_SOURCE_COUNT,
    PINNED_SOURCE_MODULES,
    PINNED_SOURCE_TOTAL,
    PINNED_TEST_COUNT,
    PINNED_TEST_FILES,
    PINNED_TEST_TOTAL,
    SOURCE_MODULE_CAP,
    TEST_FILE_CAP,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"
_ALAN_ROOT = _REPO_ROOT / "alan"
_WEB_ROOT = _REPO_ROOT / "alan" / "web"

_EXCLUDED_SEGMENTS = frozenset({"web", ".venv", "node_modules"})
_FRONTEND_EXCLUDED_SEGMENTS = frozenset(
    {"node_modules", "dist", "coverage", "playwright-report", "test-results"}
)

_UNIT_NOUN = {"test": "file", "source": "module", "frontend": "file"}
_CONTENT_NOUN = {"test": "tests", "source": "code", "frontend": "tests"}
_ROOT_LABEL = {"test": "tests/", "source": "alan/", "frontend": "alan/web/"}
_PIN_DICT_NAME = {
    "test": "PINNED_TEST_FILES",
    "source": "PINNED_SOURCE_MODULES",
    "frontend": "PINNED_FRONTEND_TEST_FILES",
}
_CAP_BY_KIND = {"test": TEST_FILE_CAP, "source": SOURCE_MODULE_CAP, "frontend": TEST_FILE_CAP}


def count_non_blank_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def iter_source_files(
    root: Path, repo_root: Path, suffixes: tuple[str, ...], excluded: frozenset[str]
) -> list[str]:
    found: list[str] = []
    for suffix in suffixes:
        for path in root.rglob(f"*{suffix}"):
            rel_parts = path.relative_to(repo_root).parts
            if excluded.intersection(rel_parts):
                continue
            found.append(path.relative_to(repo_root).as_posix())
    return sorted(found)


def iter_python_files(root: Path, repo_root: Path) -> list[str]:
    return iter_source_files(root, repo_root, (".py",), _EXCLUDED_SEGMENTS)


def _measure_suffixed(
    root: Path, repo_root: Path, suffixes: tuple[str, ...], excluded: frozenset[str]
) -> dict[str, int]:
    return {
        rel_path: count_non_blank_lines((repo_root / rel_path).read_text(encoding="utf-8"))
        for rel_path in iter_source_files(root, repo_root, suffixes, excluded)
    }


def _measure(root: Path, repo_root: Path) -> dict[str, int]:
    return _measure_suffixed(root, repo_root, (".py",), _EXCLUDED_SEGMENTS)


def check_population(
    measured: dict[str, int], pins: dict[str, int], cap: int
) -> list[tuple[str, int, int, bool]]:
    violations: list[tuple[str, int, int, bool]] = []
    for rel_path in sorted(measured):
        size = measured[rel_path]
        pin = pins.get(rel_path)
        if pin is not None:
            if size > pin:
                violations.append((rel_path, size, pin, True))
        elif size > cap:
            violations.append((rel_path, size, cap, False))
    return violations


def build_failure_message(
    rel_path: str, measured: int, limit: int, *, is_pinned: bool, kind: str
) -> str:
    unit = _UNIT_NOUN[kind]
    content = _CONTENT_NOUN[kind]
    cap = _CAP_BY_KIND[kind]
    if is_pinned:
        return (
            f"SIZE RATCHET: {rel_path} is {measured} non-blank lines ({METRIC}); "
            f"it is pinned at {limit} and may only shrink. Two legal remedies: "
            f"(1) shrink this {unit}, or (2) put the new {content} in a NEW sibling "
            f"{unit} under {cap} non-blank lines. Raising the pin in "
            "tests/size_baseline.py is a human decision, not a fix."
        )
    root_label = _ROOT_LABEL[kind]
    pin_dict = _PIN_DICT_NAME[kind]
    return (
        f"SIZE RATCHET: {rel_path} is {measured} non-blank lines ({METRIC}); "
        f"the cap for a {unit} under {root_label} is {limit} non-blank lines. "
        f"Two legal remedies: (1) shrink this {unit} below the cap, or (2) put the "
        f"new {content} in a NEW sibling {unit} that is itself under {limit} "
        f"non-blank lines. Raising the cap, or adding this {unit} to {pin_dict} in "
        "tests/size_baseline.py, is a human decision, not a fix."
    )


def _messages_for(violations: list[tuple[str, int, int, bool]], *, kind: str) -> list[str]:
    return [
        build_failure_message(rel_path, size, limit, is_pinned=is_pinned, kind=kind)
        for rel_path, size, limit, is_pinned in violations
    ]


def test_test_corpus_within_cap_or_pin() -> None:
    measured = _measure(_TESTS_ROOT, _REPO_ROOT)
    violations = check_population(measured, PINNED_TEST_FILES, TEST_FILE_CAP)
    messages = _messages_for(violations, kind="test")
    assert not messages, "\n".join(messages)


def test_source_corpus_within_cap_or_pin() -> None:
    measured = _measure(_ALAN_ROOT, _REPO_ROOT)
    violations = check_population(measured, PINNED_SOURCE_MODULES, SOURCE_MODULE_CAP)
    messages = _messages_for(violations, kind="source")
    assert not messages, "\n".join(messages)


def test_frontend_test_corpus_within_cap_or_pin() -> None:
    measured = _measure_suffixed(
        _WEB_ROOT, _REPO_ROOT, (".test.ts", ".test.tsx"), _FRONTEND_EXCLUDED_SEGMENTS
    )
    violations = check_population(measured, PINNED_FRONTEND_TEST_FILES, TEST_FILE_CAP)
    messages = _messages_for(violations, kind="frontend")
    assert not messages, "\n".join(messages)


def test_pinned_test_baseline_does_not_grow() -> None:
    assert sum(PINNED_TEST_FILES.values()) <= 41699
    assert PINNED_TEST_TOTAL <= 41699
    assert len(PINNED_TEST_FILES) <= 25
    assert PINNED_TEST_COUNT <= 25
    assert PINNED_TEST_TOTAL == sum(PINNED_TEST_FILES.values())
    assert PINNED_TEST_COUNT == len(PINNED_TEST_FILES)


def test_pinned_source_baseline_does_not_grow() -> None:
    assert sum(PINNED_SOURCE_MODULES.values()) <= 6513
    assert PINNED_SOURCE_TOTAL <= 6513
    assert len(PINNED_SOURCE_MODULES) <= 6
    assert PINNED_SOURCE_COUNT <= 6
    assert PINNED_SOURCE_TOTAL == sum(PINNED_SOURCE_MODULES.values())
    assert PINNED_SOURCE_COUNT == len(PINNED_SOURCE_MODULES)


def test_pinned_frontend_baseline_does_not_grow() -> None:
    assert sum(PINNED_FRONTEND_TEST_FILES.values()) <= 4700
    assert PINNED_FRONTEND_TEST_TOTAL <= 4700
    assert len(PINNED_FRONTEND_TEST_FILES) <= 2
    assert PINNED_FRONTEND_TEST_COUNT <= 2
    assert PINNED_FRONTEND_TEST_TOTAL == sum(PINNED_FRONTEND_TEST_FILES.values())
    assert PINNED_FRONTEND_TEST_COUNT == len(PINNED_FRONTEND_TEST_FILES)


def test_claude_md_within_byte_ceiling() -> None:
    size = (_REPO_ROOT / "CLAUDE.md").stat().st_size
    assert size <= CLAUDE_MD_BYTE_CEILING, (
        f"SIZE RATCHET: CLAUDE.md is {size} bytes; the ceiling is "
        f"{CLAUDE_MD_BYTE_CEILING}. Two legal remedies: (1) shrink CLAUDE.md, "
        "or (2) put the new content under docs/architecture/ or "
        "docs/AGENTIC_CODING_GUIDE.md. Raising the ceiling in "
        "tests/size_baseline.py is a human decision, not a fix."
    )


def test_pinned_entries_reference_existing_files() -> None:
    for rel_path in PINNED_TEST_FILES:
        assert (_REPO_ROOT / rel_path).is_file(), rel_path
    for rel_path in PINNED_SOURCE_MODULES:
        assert (_REPO_ROOT / rel_path).is_file(), rel_path


def test_pinned_frontend_entries_reference_existing_files() -> None:
    for rel_path in PINNED_FRONTEND_TEST_FILES:
        assert (_REPO_ROOT / rel_path).is_file(), rel_path


def test_a_raised_test_pin_would_fail_the_aggregate_bound() -> None:
    raised_total = sum(PINNED_TEST_FILES.values()) + 1
    assert raised_total > PINNED_TEST_TOTAL


def test_a_raised_source_pin_would_fail_the_aggregate_bound() -> None:
    raised_total = sum(PINNED_SOURCE_MODULES.values()) + 1
    assert raised_total > PINNED_SOURCE_TOTAL


def test_a_raised_frontend_pin_would_fail_the_aggregate_bound() -> None:
    raised_total = sum(PINNED_FRONTEND_TEST_FILES.values()) + 1
    assert raised_total > PINNED_FRONTEND_TEST_TOTAL


def test_no_pinned_test_file_is_under_its_cap() -> None:
    assert all(size > TEST_FILE_CAP for size in PINNED_TEST_FILES.values())


def test_no_pinned_source_module_is_under_its_cap() -> None:
    assert all(size > SOURCE_MODULE_CAP for size in PINNED_SOURCE_MODULES.values())


def test_no_pinned_frontend_test_file_is_under_its_cap() -> None:
    assert all(size > TEST_FILE_CAP for size in PINNED_FRONTEND_TEST_FILES.values())


def test_frontend_population_excludes_e2e_spec_files() -> None:
    measured = _measure_suffixed(
        _WEB_ROOT, _REPO_ROOT, (".test.ts", ".test.tsx"), _FRONTEND_EXCLUDED_SEGMENTS
    )
    assert not any(rel_path.endswith(".spec.ts") for rel_path in measured)


_DISALLOWED_BASELINE_NODE_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Call,
    ast.Import,
    ast.If,
    ast.For,
    ast.While,
    ast.With,
    ast.Lambda,
)

_ALLOWED_BASELINE_TOP_LEVEL_STATEMENTS = (ast.Expr, ast.ImportFrom, ast.Assign, ast.AnnAssign)


def test_baseline_module_contains_only_literal_data() -> None:
    tree = ast.parse(
        (_REPO_ROOT / "tests" / "size_baseline.py").read_text(encoding="utf-8")
    )
    for statement in tree.body:
        assert isinstance(statement, _ALLOWED_BASELINE_TOP_LEVEL_STATEMENTS), (
            f"tests/size_baseline.py has no regeneration mechanism by design; a "
            f"top-level {type(statement).__name__} is not literal data and must "
            "not appear there"
        )
    for node in ast.walk(tree):
        assert not isinstance(node, _DISALLOWED_BASELINE_NODE_TYPES), (
            f"tests/size_baseline.py has no regeneration mechanism by design; "
            f"found a {type(node).__name__}, which means it does something "
            "instead of merely declaring literal data"
        )


def test_count_non_blank_lines_counts_only_non_blank() -> None:
    assert count_non_blank_lines("a\n\nb\n   \nc\n") == 3


def test_count_non_blank_lines_handles_all_blank_text() -> None:
    assert count_non_blank_lines("\n\n   \n\t\n") == 0


def test_count_non_blank_lines_handles_the_real_zero_line_file() -> None:
    content = (_REPO_ROOT / "tests" / "__init__.py").read_text(encoding="utf-8")
    assert count_non_blank_lines(content) == 0


def test_iter_python_files_excludes_frontend_and_vendor_segments(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    web_dir = tmp_path / "alan" / "web" / "node_modules" / "pkg"
    web_dir.mkdir(parents=True)
    (web_dir / "vendored.py").write_text("x = 1\n", encoding="utf-8")
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("x = 1\n", encoding="utf-8")
    assert iter_python_files(tmp_path, tmp_path) == ["tests/test_a.py"]


def test_iter_source_files_excludes_frontend_build_segments(tmp_path: Path) -> None:
    web_dir = tmp_path / "alan" / "web" / "src"
    web_dir.mkdir(parents=True)
    (web_dir / "a.test.ts").write_text("x = 1;\n", encoding="utf-8")
    dist_dir = tmp_path / "alan" / "web" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "b.test.ts").write_text("x = 1;\n", encoding="utf-8")
    result = iter_source_files(
        tmp_path / "alan" / "web",
        tmp_path,
        (".test.ts", ".test.tsx"),
        _FRONTEND_EXCLUDED_SEGMENTS,
    )
    assert result == ["alan/web/src/a.test.ts"]


def test_iter_source_files_captures_both_frontend_test_extensions(tmp_path: Path) -> None:
    web_dir = tmp_path / "alan" / "web" / "src"
    web_dir.mkdir(parents=True)
    (web_dir / "a.test.ts").write_text("x = 1;\n", encoding="utf-8")
    (web_dir / "b.test.tsx").write_text("x = 1;\n", encoding="utf-8")
    result = iter_source_files(
        tmp_path / "alan" / "web",
        tmp_path,
        (".test.ts", ".test.tsx"),
        _FRONTEND_EXCLUDED_SEGMENTS,
    )
    assert result == ["alan/web/src/a.test.ts", "alan/web/src/b.test.tsx"]


def test_check_population_flags_over_cap_unpinned_test_file() -> None:
    violations = check_population({"tests/test_new.py": 1001}, {}, 1000)
    assert violations == [("tests/test_new.py", 1001, 1000, False)]


def test_check_population_flags_over_cap_unpinned_source_module() -> None:
    violations = check_population({"alan/new_module.py": 801}, {}, 800)
    assert violations == [("alan/new_module.py", 801, 800, False)]


def test_check_population_flags_pinned_file_grown_by_one_line() -> None:
    violations = check_population(
        {"tests/test_dispatcher.py": 6110}, {"tests/test_dispatcher.py": 6109}, 1000
    )
    assert violations == [("tests/test_dispatcher.py", 6110, 6109, True)]


def test_check_population_passes_pinned_file_at_exact_pin() -> None:
    pins = {"tests/test_dispatcher.py": 6109}
    assert check_population(pins, pins, 1000) == []


def test_check_population_passes_pinned_file_that_shrank() -> None:
    violations = check_population(
        {"tests/test_dispatcher.py": 6000}, {"tests/test_dispatcher.py": 6109}, 1000
    )
    assert violations == []


def test_check_population_passes_under_cap_unpinned_file() -> None:
    assert check_population({"tests/test_small.py": 5}, {}, 1000) == []


def test_build_failure_message_unpinned_test_file_names_both_remedies() -> None:
    msg = build_failure_message("tests/test_new.py", 1001, 1000, is_pinned=False, kind="test")
    assert "tests/test_new.py is 1001 non-blank lines" in msg
    assert "shrink this file below the cap" in msg
    assert "NEW sibling file" in msg
    assert "PINNED_TEST_FILES" in msg
    assert "human decision, not a fix" in msg


def test_build_failure_message_pinned_test_file_names_both_remedies() -> None:
    msg = build_failure_message(
        "tests/test_dispatcher.py", 6110, 6109, is_pinned=True, kind="test"
    )
    assert "pinned at 6109 and may only shrink" in msg
    assert "NEW sibling file under 1000 non-blank lines" in msg
    assert "Raising the pin in tests/size_baseline.py" in msg


def test_build_failure_message_unpinned_source_module_names_both_remedies() -> None:
    msg = build_failure_message(
        "alan/new_module.py", 801, 800, is_pinned=False, kind="source"
    )
    assert "cap for a module under alan/" in msg
    assert "NEW sibling module" in msg
    assert "PINNED_SOURCE_MODULES" in msg
    assert "human decision, not a fix" in msg


def test_build_failure_message_pinned_source_module_names_both_remedies() -> None:
    msg = build_failure_message(
        "alan/dispatcher.py", 1777, 1776, is_pinned=True, kind="source"
    )
    assert "pinned at 1776 and may only shrink" in msg
    assert "NEW sibling module under 800 non-blank lines" in msg
    assert "Raising the pin in tests/size_baseline.py" in msg


def test_build_failure_message_frontend_names_alan_web_root_not_tests() -> None:
    msg = build_failure_message(
        "alan/web/src/pages/Big.test.tsx", 1001, 1000, is_pinned=False, kind="frontend"
    )
    assert "cap for a file under alan/web/ is 1000" in msg
    assert "cap for a file under tests/" not in msg
    assert "PINNED_FRONTEND_TEST_FILES" in msg
