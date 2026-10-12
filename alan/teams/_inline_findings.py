from __future__ import annotations

import re
from typing import cast

import yaml

import alan.teams._pr_post as _pr_post

_INLINE_FINDINGS_RE = re.compile(
    r"##\s+INLINE_FINDINGS_YAML[ \t]*\n+```(?:ya?ml)?[ \t]*\n(.*?)\n```",
    re.DOTALL,
)
_CONFIDENCE_FOOTER_FIELD_RE = r"[A-Z_]+:.*"
_CONFIDENCE_FOOTER_RE = re.compile(
    rf"\n---\n(?:{_CONFIDENCE_FOOTER_FIELD_RE}\n)+---\s*\Z"
)


def _valid_path(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _valid_line(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _valid_body(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _coerce_inline_finding(item: object) -> _pr_post.InlineFinding | None:
    if not isinstance(item, dict):
        return None
    raw = cast("dict[object, object]", item)
    path = _valid_path(raw.get("path"))
    line = _valid_line(raw.get("line"))
    body = _valid_body(raw.get("body"))
    if path is None or line is None or body is None:
        return None
    return _pr_post.InlineFinding(path=path, line=line, body=body.rstrip())


def strip_inline_findings_block(body: str) -> str:
    return _INLINE_FINDINGS_RE.sub("", body).rstrip() + "\n"


def strip_confidence_footer(body: str) -> str:
    match = _CONFIDENCE_FOOTER_RE.search(body)
    if match is None:
        return body
    block = match.group(0)
    if "AGENT_STATUS:" not in block or "CONFIDENCE:" not in block:
        return body
    return body[: match.start()].rstrip() + "\n"


def parse_inline_findings(judge_output: str) -> list[_pr_post.InlineFinding]:
    match = _INLINE_FINDINGS_RE.search(judge_output)
    if match is None:
        return []
    try:
        data: object = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return []
    if not isinstance(data, list):
        return []
    items = cast("list[object]", data)
    return [
        finding
        for finding in (_coerce_inline_finding(item) for item in items)
        if finding is not None
    ]


__all__ = [
    "parse_inline_findings",
    "strip_confidence_footer",
    "strip_inline_findings_block",
]
