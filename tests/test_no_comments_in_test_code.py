from __future__ import annotations

from pathlib import Path

import pytest

from tests.no_comments_policy import (
    MACHINE_READ_COMMENT,
    REMEDY,
    docstring_lines,
    iter_python_files,
    prose_comments,
    violations_in,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"


def test_no_test_module_carries_a_comment_or_docstring() -> None:
    offences: list[str] = []
    for rel_path in iter_python_files(_TESTS_ROOT, _REPO_ROOT):
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        offences.extend(violations_in(source, rel_path))
    assert not offences, REMEDY + "\n" + "\n".join(offences)


def test_the_gate_reaches_the_whole_tests_tree() -> None:
    reached = iter_python_files(_TESTS_ROOT, _REPO_ROOT)
    assert "tests/conftest.py" in reached
    assert "tests/eval/conftest.py" in reached
    assert len(reached) > 300


@pytest.mark.parametrize(
    "directive",
    [
        "# noqa: SLF001",
        "# type: ignore[import-not-found]",
        "# pragma: no cover",
        "# nosec B105",
        "#!/usr/bin/env python",
        "# -*- coding: utf-8 -*-",
        "# fmt: off",
        "# pyright: ignore[reportPrivateUsage]",
    ],
)
def test_machine_read_directives_are_not_prose(directive: str) -> None:
    assert MACHINE_READ_COMMENT.match(directive)
    assert prose_comments(f"x = 1  {directive}\n") == []


def test_a_directive_hiding_behind_prose_is_still_prose() -> None:
    found = prose_comments("x = 1  # no KeyError  # noqa: SLF001\n")
    assert [line for line, _ in found] == [1]


@pytest.mark.parametrize(
    "source",
    [
        "# explains the next line\nx = 1\n",
        "x = 1  # explains this line\n",
        "def f() -> None:\n    x = 1\n    # explains the assert\n    assert x\n",
    ],
)
def test_prose_comments_are_detected_wherever_they_sit(source: str) -> None:
    assert prose_comments(source)


@pytest.mark.parametrize(
    "source",
    [
        '"""Module docstring."""\nx = 1\n',
        'def f() -> None:\n    """Function docstring."""\n    return None\n',
        'class C:\n    """Class docstring."""\n\n    x = 1\n',
        'async def f() -> None:\n    """Async docstring."""\n    return None\n',
    ],
)
def test_docstrings_are_detected_on_every_owner(source: str) -> None:
    assert docstring_lines(source)


def test_a_string_that_is_not_the_first_statement_is_not_a_docstring() -> None:
    assert docstring_lines("x = 1\n'not a docstring'\n") == []


def test_a_hash_inside_a_string_literal_is_not_a_comment() -> None:
    assert prose_comments('url = "https://example.test/#anchor"\n') == []


def test_violations_report_path_and_line_in_source_order() -> None:
    source = '"""Doc."""\nx = 1\n# late comment\n'
    assert violations_in(source, "tests/test_x.py") == [
        "tests/test_x.py:1: docstring",
        "tests/test_x.py:3: comment '# late comment'",
    ]


def test_a_clean_module_reports_nothing() -> None:
    assert violations_in("from __future__ import annotations\n\nx = 1\n", "tests/test_x.py") == []


def test_iter_python_files_skips_vendored_trees(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("x = 1\n", encoding="utf-8")
    vendored = tmp_path / "tests" / "node_modules"
    vendored.mkdir()
    (vendored / "test_vendored.py").write_text("x = 1\n", encoding="utf-8")
    assert iter_python_files(tmp_path / "tests", tmp_path) == ["tests/test_real.py"]
