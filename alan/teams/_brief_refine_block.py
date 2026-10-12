from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

import yaml

BRIEF_REFINE_BLOCK_RE = re.compile(
    r"```yaml[ \t]+brief_refine\.yaml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

ORIGIN_LABELS: frozenset[str] = frozenset({"user_text", "cited_source", "house_rule"})

BRIEF_REFINE_BLOCK_GUIDANCE = (
    "Emit ONLY this block (no preamble, no other prose) with this EXACT "
    "opening fence line, including the ` brief_refine.yaml` info-string:\n"
    "```yaml brief_refine.yaml\n"
    "requirements:\n"
    "  - id: req-abc123def456   # present => this is a prior clause, kept or lightly reworded\n"
    '    text: "the exact (possibly reworded) clause text"\n'
    "  - text: \"a NEW clause you are promoting to a requirement\"\n"
    "    origin: user_text        # user_text | cited_source\n"
    '    locator: "a short verbatim quote from the brief that supports this"\n'
    "assumptions:\n"
    '  - "something you inferred but cannot trace to the brief"\n'
    "```\n"
    "Every prior requirement (listed below by id) must appear, by id, "
    "somewhere in your `requirements:` output — never drop one. A NEW "
    "requirement entry (no `id`) must carry `origin` (`user_text` or "
    "`cited_source`) and a `locator`; when you cannot support a new clause "
    "this way, put it under `assumptions:` instead."
)


@dataclass(frozen=True)
class RefinedRequirementItem:
    id: str | None
    text: str
    origin: str | None = None
    locator: str | None = None


@dataclass(frozen=True)
class BriefRefinePayload:
    requirements: tuple[RefinedRequirementItem, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def _clean_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_origin(data: dict[str, Any]) -> tuple[str | None, bool]:
    raw = data.get("origin")
    if raw is None:
        return None, True
    cleaned = _clean_str(raw)
    if cleaned is None or cleaned not in ORIGIN_LABELS:
        return None, False
    return cleaned, True


def _parse_requirement_item(raw: object) -> RefinedRequirementItem | None:
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    text = _clean_str(data.get("text"))
    if text is None:
        return None
    item_id = _clean_str(data.get("id"))
    origin, origin_ok = _parse_origin(data)
    if not origin_ok:
        return None
    locator = _clean_str(data.get("locator"))
    if item_id is None and (origin is None or locator is None):
        return None
    return RefinedRequirementItem(id=item_id, text=text, origin=origin, locator=locator)


def _parse_requirements(raw: object) -> tuple[RefinedRequirementItem, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    items: list[RefinedRequirementItem] = []
    for entry in cast(list[Any], raw):  # type: ignore[redundant-cast]
        parsed = _parse_requirement_item(entry)
        if parsed is None:
            return None
        items.append(parsed)
    return tuple(items)


def _parse_assumptions(raw: object) -> tuple[str, ...] | None:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for entry in cast(list[Any], raw):  # type: ignore[redundant-cast]
        cleaned = _clean_str(entry)
        if cleaned is None:
            return None
        out.append(cleaned)
    return tuple(out)


def _parse_shape(raw: object) -> BriefRefinePayload | None:
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    requirements = _parse_requirements(data.get("requirements"))
    if requirements is None:
        return None
    assumptions = _parse_assumptions(data.get("assumptions"))
    if assumptions is None:
        return None
    return BriefRefinePayload(requirements=requirements, assumptions=assumptions)


def _invariants_hold(payload: BriefRefinePayload, prior_ids: frozenset[str]) -> bool:
    id_list = [item.id for item in payload.requirements if item.id is not None]
    if len(id_list) != len(set(id_list)):
        return False
    output_ids = set(id_list)
    if not prior_ids <= output_ids:
        return False
    return not (prior_ids and not payload.requirements)


def parse_brief_refine(text: str, prior_ids: frozenset[str]) -> BriefRefinePayload | None:
    match = BRIEF_REFINE_BLOCK_RE.search(text)
    if match is None:
        return None
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    payload = _parse_shape(raw)
    if payload is None or not _invariants_hold(payload, prior_ids):
        return None
    return payload


__all__ = [
    "BRIEF_REFINE_BLOCK_GUIDANCE",
    "BRIEF_REFINE_BLOCK_RE",
    "ORIGIN_LABELS",
    "BriefRefinePayload",
    "RefinedRequirementItem",
    "parse_brief_refine",
]
