from __future__ import annotations

import re
from pathlib import Path

from tests.host_parsed_blocks import DECLARATION_MESSAGE, HOST_PARSED_BLOCKS

_ALAN_ROOT = Path(__file__).resolve().parent.parent / "alan"

_FENCE_TOKEN_RE = re.compile(r"```yaml[^\n]{0,20}?([A-Za-z0-9_]{2,40})\\?\.yaml")
_SENTINEL_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+_YAML)\b")


def _alan_source_files() -> list[Path]:
    return [
        path
        for path in _ALAN_ROOT.rglob("*.py")
        if "web" not in path.relative_to(_ALAN_ROOT).parts
    ]


def _combined_source_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in _alan_source_files()
    )


def _discovered_tokens(text: str) -> set[str]:
    return {m.group(1) for m in _FENCE_TOKEN_RE.finditer(text)} | {
        m.group(1) for m in _SENTINEL_TOKEN_RE.finditer(text)
    }


def _is_covered(token: str) -> bool:
    return any(
        block.discovery is not None and token in block.discovery for block in HOST_PARSED_BLOCKS
    )


def test_inventory_has_exactly_ten_rows() -> None:
    assert len(HOST_PARSED_BLOCKS) == 10


def test_every_declared_discovery_pattern_still_matches_real_source() -> None:
    text = _combined_source_text()
    for block in HOST_PARSED_BLOCKS:
        if block.discovery is None:
            continue
        assert re.search(block.discovery, text) is not None, (
            f"{block.name}: discovery pattern no longer matches any alan/ "
            "source — inventory entry is stale"
        )


def test_undeclared_host_parsed_block_fails_with_declaration_message() -> None:
    text = _combined_source_text()
    uncovered = {token for token in _discovered_tokens(text) if not _is_covered(token)}
    assert not uncovered, f"{sorted(uncovered)}: {DECLARATION_MESSAGE}"


def test_discovery_finds_at_least_one_token_per_declared_pattern() -> None:
    text = _combined_source_text()
    discovered = _discovered_tokens(text)
    for block in HOST_PARSED_BLOCKS:
        if block.discovery is None:
            continue
        assert any(token in block.discovery for token in discovered), (
            f"{block.name}: no discovered token maps back to its declared pattern"
        )
