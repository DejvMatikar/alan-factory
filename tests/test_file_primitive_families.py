from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.file_primitive_shapes import (
    REPO_ROOT,
    FunctionLike,
    is_row_parser_shape,
    is_signature_shape,
    is_tail_shape,
    is_writer_shape,
    module_functions,
    scanned_source_files,
)

EXCEPTION_ENTRIES: dict[str, str] = {
    "alan/routes/_deps.py::read_stream_events": (
        "logs a warning on decode failure but stays silent on a non-dict line; "
        "collapsing those two cases into a tolerant reader would add or drop a log line"
    ),
    "alan/teams/triage.py::_tail_jsonl_lines": (
        "a row-budgeted backward scan, not a byte-bounded seek — cannot be expressed "
        "once the rows sought exceed the byte budget"
    ),
    "alan/health_repo_metrics.py::read_audit_tail": (
        "its small-file path reads the file strictly, while its large-file path "
        "replaces invalid bytes — delegating would change behaviour on a small "
        "file containing invalid UTF-8"
    ),
    "alan/claude_cli.py::_parse_stream_line": (
        "raises on malformed input; folding it into a tolerant parser is a "
        "semantic change to a reader"
    ),
    "alan/teams/_node_attach.py::handle": "raises on malformed input, same reason",
    "alan/teams/_base.py::_parse_turns_row": (
        "parses then extracts a tuple[str, int] | None, not the raw record"
    ),
    "alan/repo_status.py::_parse_relevant_audit_line": (
        "parses then validates/extracts a specific action, not the raw record"
    ),
    "alan/teams/triage_pregate.py::_parse_tally_row": (
        "parses then extracts a tuple[str, datetime] | None, not the raw record"
    ),
    "alan/health_repo_metrics.py::_bc_row_from_line": (
        "parses then extracts a BlockCorrectionRow, not the raw record"
    ),
    "alan/retention.py::_compress_one": (
        "the single binary compress-and-swap: a gzip stream write to a sibling "
        "path, not an atomic overwrite of the same path"
    ),
}


def _hits(is_shape: Callable[[FunctionLike], bool]) -> set[str]:
    hits: set[str] = set()
    for path in scanned_source_files():
        for rel_path, fn in module_functions(path):
            if is_shape(fn):
                hits.add(f"{rel_path}::{fn.name}")
    return hits


def test_exception_entries_has_exactly_ten_agreed_paths() -> None:
    assert len(EXCEPTION_ENTRIES) == 10
    assert EXCEPTION_ENTRIES.keys() == {
        "alan/routes/_deps.py::read_stream_events",
        "alan/teams/triage.py::_tail_jsonl_lines",
        "alan/health_repo_metrics.py::read_audit_tail",
        "alan/claude_cli.py::_parse_stream_line",
        "alan/teams/_node_attach.py::handle",
        "alan/teams/_base.py::_parse_turns_row",
        "alan/repo_status.py::_parse_relevant_audit_line",
        "alan/teams/triage_pregate.py::_parse_tally_row",
        "alan/health_repo_metrics.py::_bc_row_from_line",
        "alan/retention.py::_compress_one",
    }


def test_writer_shape_hits_are_covered_by_exceptions() -> None:
    assert _hits(is_writer_shape) - EXCEPTION_ENTRIES.keys() == set()


def test_signature_shape_hits_are_covered_by_exceptions() -> None:
    assert _hits(is_signature_shape) - EXCEPTION_ENTRIES.keys() == set()


def test_tail_shape_hits_are_covered_by_exceptions() -> None:
    assert _hits(is_tail_shape) - EXCEPTION_ENTRIES.keys() == set()


def test_row_parser_shape_hits_are_covered_by_exceptions() -> None:
    assert _hits(is_row_parser_shape) - EXCEPTION_ENTRIES.keys() == set()


def _parse_one(source: str) -> FunctionLike:
    tree = ast.parse(source)
    return next(
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_writer_shape_detects_a_synthetic_copy() -> None:
    fn = _parse_one(
        "def write_it(path, text):\n"
        "    tmp = path.with_suffix(path.suffix + '.tmp')\n"
        "    tmp.write_text(text)\n"
        "    tmp.replace(path)\n"
    )
    assert is_writer_shape(fn) is True


def test_signature_shape_detects_a_synthetic_copy() -> None:
    fn = _parse_one(
        "def sig(path):\n"
        "    try:\n"
        "        st = path.stat()\n"
        "    except OSError:\n"
        "        return None\n"
        "    return (st.st_mtime_ns, st.st_size)\n"
    )
    assert is_signature_shape(fn) is True


def test_tail_shape_detects_a_synthetic_copy() -> None:
    fn = _parse_one(
        "def tail(path, max_bytes):\n"
        "    size = path.stat().st_size\n"
        "    offset = max(0, size - max_bytes)\n"
        "    with path.open('rb') as f:\n"
        "        f.seek(offset)\n"
        "        return f.read().decode('utf-8', errors='replace')\n"
    )
    assert is_tail_shape(fn) is True


def test_row_parser_shape_detects_a_synthetic_copy() -> None:
    fn = _parse_one(
        "def parse_row(line: str):\n"
        "    stripped = line.strip()\n"
        "    if not stripped:\n"
        "        return None\n"
        "    try:\n"
        "        raw = json.loads(stripped)\n"
        "    except json.JSONDecodeError:\n"
        "        return None\n"
        "    return cast('dict', raw) if isinstance(raw, dict) else None\n"
    )
    assert is_row_parser_shape(fn) is True


def test_row_parser_shape_does_not_flag_a_whole_blob_decoder() -> None:
    fn = _parse_one(
        "def safe_json(raw_text: str):\n"
        "    stripped = (raw_text or '').strip()\n"
        "    if not stripped:\n"
        "        return None\n"
        "    try:\n"
        "        return json.loads(stripped)\n"
        "    except json.JSONDecodeError:\n"
        "        return None\n"
    )
    assert is_row_parser_shape(fn) is False


_DELIBERATELY_UNFLAGGED: tuple[tuple[str, str, Callable[[FunctionLike], bool]], ...] = (
    ("alan/forge_gh.py", "run_gh_api_json_input", is_writer_shape),
    ("alan/teams/_forge_quota.py", "_rotate_if_oversized", is_writer_shape),
    ("alan/handover.py", "_build_chain_index", is_signature_shape),
    ("alan/workspace_index.py", "_dir_mtime", is_signature_shape),
    ("alan/claude_cli_journal.py", "read_err_file", is_tail_shape),
    ("alan/teams/_pr_json.py", "safe_json", is_row_parser_shape),
    ("alan/teams/_pr_ready.py", "_parse_readiness_payload", is_row_parser_shape),
)


def _real_function(rel_path: str, name: str) -> FunctionLike:
    for found_rel, fn in module_functions(REPO_ROOT / rel_path):
        if found_rel == rel_path and fn.name == name:
            return fn
    raise AssertionError(f"{rel_path}::{name} not found")


@pytest.mark.parametrize(("rel_path", "name", "detector"), _DELIBERATELY_UNFLAGGED)
def test_deliberately_unflagged_example_is_not_flagged(
    rel_path: str, name: str, detector: Callable[[FunctionLike], bool]
) -> None:
    assert detector(_real_function(rel_path, name)) is False


def test_module_functions_includes_async_def(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tests.file_primitive_shapes.REPO_ROOT", tmp_path)
    module_path = tmp_path / "async_copy.py"
    module_path.write_text(
        "async def write_it(path, text):\n"
        "    tmp = path.with_suffix(path.suffix + '.tmp')\n"
        "    tmp.write_text(text)\n"
        "    tmp.replace(path)\n"
    )
    found = module_functions(module_path)
    assert [fn.name for _, fn in found] == ["write_it"]
    assert isinstance(found[0][1], ast.AsyncFunctionDef)


@pytest.mark.parametrize(
    "detector", (is_writer_shape, is_signature_shape, is_tail_shape, is_row_parser_shape)
)
def test_detectors_see_async_writer_copy(detector: Callable[[FunctionLike], bool]) -> None:
    fn = _parse_one(
        "async def write_it(path, text):\n"
        "    tmp = path.with_suffix(path.suffix + '.tmp')\n"
        "    tmp.write_text(text)\n"
        "    tmp.replace(path)\n"
    )
    assert isinstance(fn, ast.AsyncFunctionDef)
    if detector is is_writer_shape:
        assert detector(fn) is True
    else:
        assert detector(fn) is False
