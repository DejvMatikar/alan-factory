from __future__ import annotations

import ast
import sys
import tokenize
from collections.abc import Iterator, Mapping
from io import StringIO
from pathlib import Path

from tests.duplication_baseline import (
    BLOCK_MIN_LINES,
    BLOCK_MIN_TOKENS,
    DUPLICATED_LINE_BUDGET,
    METRIC,
)
from tests.test_size_ratchets import iter_python_files

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_ROOT = _REPO_ROOT / "alan"

_SKIP_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)

_SYNTHETIC_CLONE_SOURCE = (
    "def _load_record(path: Path) -> dict[str, object] | None:\n"
    "    try:\n"
    '        raw = path.read_text(encoding="utf-8")\n'
    "    except OSError:\n"
    "        return None\n"
    "    try:\n"
    "        data: object = json.loads(raw)\n"
    "    except json.JSONDecodeError:\n"
    "        return None\n"
    "    if not isinstance(data, dict):\n"
    "        return None\n"
    '    return cast("dict[str, object]", data)\n'
    "\n"
    "\n"
    "def _optional_str(value: object) -> str | None:\n"
    "    return value if isinstance(value, str) else None\n"
    "\n"
    "\n"
    "def _meta_from_record(data: dict[str, object]) -> SecretRecordMeta | None:\n"
    '    engine = data.get("engine")\n'
    '    purpose = data.get("purpose")\n'
    '    fp = data.get("fingerprint")\n'
)


def read_sources(root: Path, repo_root: Path) -> dict[str, str]:
    return {
        rel_path: (repo_root / rel_path).read_text(encoding="utf-8")
        for rel_path in iter_python_files(root, repo_root)
    }


def significant_tokens(source: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type in _SKIP_TOKEN_TYPES:
            continue
        tokens.append((tok.string, tok.start[0], tok.end[0]))
    return tokens


def _line_start_indices(tokens: list[tuple[str, int, int]]) -> list[int]:
    indices: list[int] = []
    last_line = 0
    for idx, (_, start_line, _) in enumerate(tokens):
        if start_line != last_line:
            indices.append(idx)
            last_line = start_line
    return indices


def _line_windows(
    tokens: list[tuple[str, int, int]], min_lines: int, min_tokens: int
) -> Iterator[tuple[tuple[str, ...], int, int]]:
    for start_idx in _line_start_indices(tokens):
        end_idx = start_idx + min_tokens
        if end_idx > len(tokens):
            continue
        start_line = tokens[start_idx][1]
        end_line = tokens[end_idx - 1][2]
        if end_line - start_line + 1 < min_lines:
            continue
        window = tuple(text for text, _, _ in tokens[start_idx:end_idx])
        yield window, start_line, end_line


def find_duplicate_lines(
    sources: Mapping[str, str],
    *,
    min_lines: int = BLOCK_MIN_LINES,
    min_tokens: int = BLOCK_MIN_TOKENS,
) -> set[tuple[str, int]]:
    occurrences: dict[tuple[str, ...], list[tuple[str, int, int]]] = {}
    for name, text in sources.items():
        tokens = significant_tokens(text)
        for window, start_line, end_line in _line_windows(tokens, min_lines, min_tokens):
            occurrences.setdefault(window, []).append((name, start_line, end_line))
    marked: set[tuple[str, int]] = set()
    for locations in occurrences.values():
        if len(locations) < 2:
            continue
        for name, start_line, end_line in locations:
            marked.update((name, line) for line in range(start_line, end_line + 1))
    return marked


def count_duplicated_lines(sources: Mapping[str, str]) -> int:
    return len(find_duplicate_lines(sources))


def top_offending_regions(marked: set[tuple[str, int]], limit: int = 10) -> list[str]:
    by_file: dict[str, list[int]] = {}
    for name, line in marked:
        by_file.setdefault(name, []).append(line)
    regions: list[tuple[int, str]] = []
    for name, lines in by_file.items():
        ordered = sorted(lines)
        start = ordered[0]
        previous = ordered[0]
        for line in ordered[1:]:
            if line == previous + 1:
                previous = line
                continue
            regions.append((previous - start + 1, f"{name}:{start}-{previous}"))
            start = line
            previous = line
        regions.append((previous - start + 1, f"{name}:{start}-{previous}"))
    regions.sort(key=lambda item: (-item[0], item[1]))
    return [region for _, region in regions[:limit]]


def test_alan_source_duplication_within_budget() -> None:
    sources = read_sources(_ALAN_ROOT, _REPO_ROOT)
    marked = find_duplicate_lines(sources)
    measured = len(marked)
    assert measured <= DUPLICATED_LINE_BUDGET, (
        f"DUPLICATION RATCHET: alan/ source has {measured} duplicated lines "
        f"({METRIC}); the pinned budget is {DUPLICATED_LINE_BUDGET}. The only "
        "legal remedy is extracting the duplicated block into a shared "
        "sibling helper module — raising DUPLICATED_LINE_BUDGET in "
        "tests/duplication_baseline.py is a human decision, not a fix. Top "
        f"offending regions: {top_offending_regions(marked)}"
    )


def test_budget_is_pinned() -> None:
    assert DUPLICATED_LINE_BUDGET <= 595


def test_a_raised_budget_would_fail_the_pin() -> None:
    assert DUPLICATED_LINE_BUDGET + 1 > 595


def test_a_synthetic_clone_of_real_code_exceeds_the_budget() -> None:
    sources = dict(read_sources(_ALAN_ROOT, _REPO_ROOT))
    assert _SYNTHETIC_CLONE_SOURCE in sources["alan/secrets_store.py"]
    baseline = count_duplicated_lines(sources)
    sources["alan/_synthetic_duplicate_probe.py"] = _SYNTHETIC_CLONE_SOURCE
    with_clone = count_duplicated_lines(sources)
    assert with_clone > baseline
    if sys.version_info < (3, 12):
        assert with_clone > DUPLICATED_LINE_BUDGET


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
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.JoinedStr,
)

_ALLOWED_BASELINE_TOP_LEVEL_STATEMENTS = (ast.Expr, ast.ImportFrom, ast.Assign, ast.AnnAssign)


def test_duplication_baseline_contains_only_literal_data() -> None:
    tree = ast.parse(
        (_REPO_ROOT / "tests" / "duplication_baseline.py").read_text(encoding="utf-8")
    )
    for statement in tree.body:
        assert isinstance(statement, _ALLOWED_BASELINE_TOP_LEVEL_STATEMENTS), (
            "tests/duplication_baseline.py has no regeneration mechanism by "
            f"design; a top-level {type(statement).__name__} is not literal "
            "data and must not appear there"
        )
    for node in ast.walk(tree):
        assert not isinstance(node, _DISALLOWED_BASELINE_NODE_TYPES), (
            "tests/duplication_baseline.py has no regeneration mechanism by "
            f"design; found a {type(node).__name__}, which means it does "
            "something instead of merely declaring literal data"
        )


def test_significant_tokens_strips_comments_blanks_and_indentation() -> None:
    source = (
        '"""doc"""\n'
        "x = 1  # comment\n"
        "\n"
        "def f():\n"
        '    y = """\n'
        "    multi\n"
        "    line\n"
        '    """\n'
        "    return y\n"
    )
    tokens = significant_tokens(source)
    texts = [text for text, _, _ in tokens]
    assert "# comment" not in texts
    assert all(text.strip() != "" for text in texts)
    multiline = next(text for text in texts if text.startswith('"""\n'))
    start_line = next(start for text, start, _ in tokens if text == multiline)
    end_line = next(end for text, _, end in tokens if text == multiline)
    assert (start_line, end_line) == (5, 8)


def test_significant_tokens_propagates_token_error_on_unterminated_string() -> None:
    try:
        significant_tokens("x = '''unterminated\n")
    except tokenize.TokenError:
        return
    raise AssertionError("expected tokenize.TokenError to propagate")


def test_significant_tokens_propagates_syntax_error_on_bad_indentation() -> None:
    source = "def f():\n    x = 1\n  y = 2\n"
    try:
        significant_tokens(source)
    except SyntaxError:
        return
    raise AssertionError("expected SyntaxError to propagate")


def test_line_start_indices_returns_one_per_code_bearing_line() -> None:
    tokens = [
        ("a", 1, 1),
        ("=", 1, 1),
        ("1", 1, 1),
        ("b", 2, 1),
        ("=", 2, 1),
        ("2", 2, 1),
    ]
    assert _line_start_indices(tokens) == [0, 3]


def test_line_start_indices_handles_a_token_spanning_multiple_lines() -> None:
    tokens = [
        ("a", 1, 1),
        ('"""x\ny\n"""', 1, 3),
        ("b", 4, 4),
    ]
    assert _line_start_indices(tokens) == [0, 2]


def test_both_floors_bind_independently_dense_block_too_few_lines() -> None:
    dense_block = "a = 1; b = 2; c = 3\n" * 3
    sources = {"one.py": dense_block, "two.py": dense_block}
    assert find_duplicate_lines(sources, min_lines=10, min_tokens=5) == set()


def test_both_floors_bind_independently_long_block_too_few_tokens() -> None:
    sparse_block = "x = 1\n" * 5
    sources = {"one.py": sparse_block, "two.py": sparse_block}
    assert find_duplicate_lines(sources, min_lines=2, min_tokens=100) == set()


def test_exact_boundary_case_is_included() -> None:
    block = "a = 1\nb = 2\nc = 3\n"
    sources = {"one.py": block, "two.py": block}
    marked = find_duplicate_lines(sources, min_lines=3, min_tokens=9)
    assert marked == {
        ("one.py", 1),
        ("one.py", 2),
        ("one.py", 3),
        ("two.py", 1),
        ("two.py", 2),
        ("two.py", 3),
    }


def test_single_occurrence_is_not_marked() -> None:
    block = "a = 1\nb = 2\nc = 3\n"
    sources = {"one.py": block, "two.py": "d = 4\ne = 5\nf = 6\n"}
    assert find_duplicate_lines(sources, min_lines=3, min_tokens=9) == set()


def test_three_or_more_occurrences_are_counted_once() -> None:
    block = "a = 1\nb = 2\nc = 3\n"
    sources = {"one.py": block, "two.py": block, "three.py": block}
    marked = find_duplicate_lines(sources, min_lines=3, min_tokens=9)
    assert marked == {
        ("one.py", 1),
        ("one.py", 2),
        ("one.py", 3),
        ("two.py", 1),
        ("two.py", 2),
        ("two.py", 3),
        ("three.py", 1),
        ("three.py", 2),
        ("three.py", 3),
    }


def test_same_file_duplication_is_detected() -> None:
    source = "a = 1\nb = 2\nc = 3\nd = 9\na = 1\nb = 2\nc = 3\n"
    sources = {"one.py": source}
    marked = find_duplicate_lines(sources, min_lines=3, min_tokens=9)
    assert marked == {
        ("one.py", 1),
        ("one.py", 2),
        ("one.py", 3),
        ("one.py", 5),
        ("one.py", 6),
        ("one.py", 7),
    }


def test_renamed_identifier_is_not_flagged() -> None:
    original = "a = 1\nb = 2\nc = 3\n"
    renamed = "a = 1\nb = 2\nzzz = 3\n"
    sources = {"one.py": original, "two.py": renamed}
    assert find_duplicate_lines(sources, min_lines=3, min_tokens=9) == set()


def test_overlapping_windows_across_a_longer_block_dedup_to_full_coverage() -> None:
    block = "".join(f"v{i} = {i}\n" for i in range(8))
    sources = {"one.py": block, "two.py": block}
    marked = find_duplicate_lines(sources, min_lines=3, min_tokens=10)
    assert marked == {(name, line) for name in ("one.py", "two.py") for line in range(1, 9)}


def test_count_duplicated_lines_matches_find_duplicate_lines_length() -> None:
    block = "a = 1\nb = 2\nc = 3\n"
    sources = {"one.py": block, "two.py": block}
    assert count_duplicated_lines(sources) == len(
        find_duplicate_lines(sources, min_lines=BLOCK_MIN_LINES, min_tokens=BLOCK_MIN_TOKENS)
    )


def test_count_duplicated_lines_is_zero_on_a_clean_fixture() -> None:
    sources = {"one.py": "a = 1\n", "two.py": "b = 2\n"}
    assert count_duplicated_lines(sources) == 0


def test_top_offending_regions_on_empty_input() -> None:
    assert top_offending_regions(set()) == []


def test_top_offending_regions_groups_contiguous_lines() -> None:
    marked = {("one.py", 1), ("one.py", 2), ("one.py", 3)}
    assert top_offending_regions(marked) == ["one.py:1-3"]


def test_top_offending_regions_splits_two_disjoint_ranges_sorted_desc() -> None:
    marked = {("one.py", 1), ("one.py", 2), ("one.py", 5), ("one.py", 6), ("one.py", 7)}
    assert top_offending_regions(marked) == ["one.py:5-7", "one.py:1-2"]


def test_top_offending_regions_respects_limit() -> None:
    marked = {("one.py", 1), ("one.py", 2), ("one.py", 5), ("one.py", 6), ("one.py", 7)}
    assert top_offending_regions(marked, limit=1) == ["one.py:5-7"]


def test_top_offending_regions_formats_file_colon_start_dash_end() -> None:
    marked = {("alan/example.py", 5), ("alan/example.py", 6), ("alan/example.py", 7)}
    regions = top_offending_regions(marked)
    assert regions == ["alan/example.py:5-7"]


def test_read_sources_composes_over_a_tmp_path_tree(tmp_path: Path) -> None:
    alan_dir = tmp_path / "alan"
    alan_dir.mkdir()
    (alan_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    web_dir = alan_dir / "web"
    web_dir.mkdir()
    (web_dir / "ignored.py").write_text("y = 2\n", encoding="utf-8")
    assert read_sources(alan_dir, tmp_path) == {"alan/mod.py": "x = 1\n"}
