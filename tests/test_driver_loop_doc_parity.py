from __future__ import annotations

import re
from pathlib import Path

import pytest

from alan.driver_loop_cmd import render_for_doc
from alan.driver_loop_registry import DriverLoopEntry, load_registry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOC_PATH = _REPO_ROOT / "docs" / "architecture" / "driver_loops.md"

_ENTRY_BLOCK_RE = re.compile(
    r"^#### (?P<name>[a-z0-9-]+)\n\n```\n(?P<cmd>.*?)\n```", re.MULTILINE | re.DOTALL
)

_TEMPLATE_HEADING = "### Reference template for registering the live drivers"
_NEXT_HEADING_RE = re.compile(r"^#{2,3}(?!#) ", re.MULTILINE)


def _doc_command_lines(text: str) -> dict[str, str]:
    return {m.group("name"): m.group("cmd") for m in _ENTRY_BLOCK_RE.finditer(text)}


def _template_section(text: str) -> str:
    start = text.index(_TEMPLATE_HEADING)
    body_start = start + len(_TEMPLATE_HEADING)
    match = _NEXT_HEADING_RE.search(text, body_start)
    end = match.start() if match else len(text)
    return text[start:end]


def _assert_doc_matches_registry(entries: list[DriverLoopEntry], doc_text: str) -> None:
    doc_lines = _doc_command_lines(doc_text)
    for entry in entries:
        rendered = render_for_doc(entry).replace(str(Path.home()), "$HOME")
        assert doc_lines.get(entry.name) == rendered


def test_doc_embeds_a_command_line_for_every_shipped_entry() -> None:
    entries, quarantined = load_registry()
    assert quarantined == []
    doc_lines = _doc_command_lines(_DOC_PATH.read_text(encoding="utf-8"))
    assert set(doc_lines) == {e.name for e in entries}


def test_doc_matches_shipped_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DRIVER_LOOP_STATE_ROOT", raising=False)
    entries, quarantined = load_registry()
    assert quarantined == []
    _assert_doc_matches_registry(entries, _DOC_PATH.read_text(encoding="utf-8"))


def test_reference_template_is_not_shaped_like_a_registry_entry() -> None:
    text = _DOC_PATH.read_text(encoding="utf-8")
    assert _TEMPLATE_HEADING in text
    assert "#### Reference template" not in text
    assert _ENTRY_BLOCK_RE.search(_template_section(text)) is None


def _synthetic_entry() -> DriverLoopEntry:
    return DriverLoopEntry.model_validate(
        {
            "name": "synthetic-loop",
            "command": "/synthetic-loop",
            "cwd": "/opt/checkouts/synthetic-loop",
            "self_paced": True,
            "timeout_seconds": 3600,
        }
    )


def test_helper_accepts_a_matching_synthetic_entry_and_doc_block() -> None:
    entry = _synthetic_entry()
    rendered = render_for_doc(entry)
    doc_text = f"#### {entry.name}\n\n```\n{rendered}\n```\n"
    _assert_doc_matches_registry([entry], doc_text)


def test_helper_rejects_a_mismatched_synthetic_entry_and_doc_block() -> None:
    entry = _synthetic_entry()
    doc_text = f"#### {entry.name}\n\n```\nclaude -p /wrong-command\n```\n"
    with pytest.raises(AssertionError):
        _assert_doc_matches_registry([entry], doc_text)
