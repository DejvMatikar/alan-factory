from __future__ import annotations

from pathlib import Path

from alan.headless_prompt import HEADLESS_EXECUTION_BLOCK, project_doc_block
from alan.project_doc_context import set_active_project_doc_path


def test_headless_execution_block_covers_both_backgrounding_and_refusal_advice() -> None:
    assert "moves a command to the background" in HEADLESS_EXECUTION_BLOCK
    assert "refuses a command" in HEADLESS_EXECUTION_BLOCK
    assert "no notification and no wakeup can ever reach" in HEADLESS_EXECUTION_BLOCK
    assert "do not comply" in HEADLESS_EXECUTION_BLOCK


def test_project_doc_block_empty_when_no_active_doc() -> None:
    set_active_project_doc_path(None)
    assert project_doc_block() == ""


def test_project_doc_block_empty_when_active_doc_path_missing_on_disk(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "gone.md"
    set_active_project_doc_path(str(missing))
    try:
        assert project_doc_block() == ""
    finally:
        set_active_project_doc_path(None)


def test_project_doc_block_references_active_doc_path(tmp_path: Path) -> None:
    doc = tmp_path / "project.md"
    doc.write_text("## Vision\n", encoding="utf-8")
    set_active_project_doc_path(str(doc))
    try:
        block = project_doc_block()
        assert f"@{doc}" in block
        assert "READ-ONLY" in block
    finally:
        set_active_project_doc_path(None)
