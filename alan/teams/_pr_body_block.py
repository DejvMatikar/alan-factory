from __future__ import annotations

import re
from dataclasses import dataclass

_OPENER_RE = re.compile(r"(?m)^(`{3,})pr_body[ \t]*\r?\n")
_FENCE_LINE_RE = re.compile(r"(?m)^(`{3,})([^`\n]*)$")


@dataclass(frozen=True)
class FenceLine:
    pos: int
    end: int
    fence_len: int
    is_bare: bool


@dataclass(frozen=True)
class MarkdownState:
    fences: tuple[FenceLine, ...]


@dataclass(frozen=True)
class BodyExtraction:
    body: str
    alternative_lengths: tuple[int, ...]


def _classify_fence(match: re.Match[str]) -> FenceLine:
    ticks, trailing = match.group(1), match.group(2).rstrip("\r")
    is_bare = not trailing.strip()
    return FenceLine(pos=match.start(), end=match.end(), fence_len=len(ticks), is_bare=is_bare)


def scan_markdown(text: str) -> MarkdownState:
    fences = tuple(_classify_fence(m) for m in _FENCE_LINE_RE.finditer(text))
    return MarkdownState(fences=fences)


def _openers(text: str) -> list[tuple[int, int]]:
    return [(len(m.group(1)), m.end()) for m in _OPENER_RE.finditer(text)]


def _find_closer(fences: tuple[FenceLine, ...], start: int, min_len: int) -> int | None:
    for idx in range(start, len(fences)):
        candidate = fences[idx]
        if candidate.is_bare and candidate.fence_len >= min_len:
            return idx
    return None


def _pair_and_collect(fences: tuple[FenceLine, ...]) -> list[FenceLine]:
    outer: list[FenceLine] = []
    i = 0
    n = len(fences)
    while i < n:
        fence = fences[i]
        if fence.is_bare:
            outer.append(fence)
            i += 1
            continue
        closer = _find_closer(fences, i + 1, fence.fence_len)
        i = closer + 1 if closer is not None else n
    return outer


def _extract_body(text: str, start: int, fence_pos: int) -> str:
    raw = text[start:fence_pos]
    if raw.endswith("\r\n"):
        return raw[:-2]
    if raw.endswith("\n"):
        return raw[:-1]
    return raw


def _choose(
    outer: list[FenceLine], fence_len: int, content_start: int, text: str
) -> BodyExtraction | None:
    candidates = [f for f in outer if f.fence_len == fence_len]
    qualifying = [c for idx, c in enumerate(candidates) if idx % 2 == 0]
    if not qualifying:
        return None
    chosen = qualifying[-1]
    alternatives = tuple(
        len(_extract_body(text, content_start, c.pos)) for c in qualifying[:-1]
    )
    return BodyExtraction(
        body=_extract_body(text, content_start, chosen.pos),
        alternative_lengths=alternatives,
    )


def extract_pr_body_block(synthesis: str) -> BodyExtraction | None:
    state = scan_markdown(synthesis)
    for fence_len, content_start in _openers(synthesis):
        relevant = tuple(f for f in state.fences if f.pos >= content_start)
        outer = _pair_and_collect(relevant)
        result = _choose(outer, fence_len, content_start, synthesis)
        if result is not None and result.body.strip():
            return result
    return None


__all__ = [
    "BodyExtraction",
    "FenceLine",
    "MarkdownState",
    "extract_pr_body_block",
    "scan_markdown",
]
