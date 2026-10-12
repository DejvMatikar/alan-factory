"""Extract the real boundary-smoke pytest failure text for the weekly issue body.

Reads `boundary-smoke-results.xml` (the `--junitxml` output of the `boundary-smoke`
job in `.github/workflows/boundary-smoke.yml`) and prints the actual failure detail so
the auto-filed issue reports what broke instead of asserting a permission-boundary
regression that may not have happened. Extraction uses a tolerant regex plus
`html.unescape` rather than `xml.etree.ElementTree`: ruff's `S` (bandit) ruleset
is selected repo-wide with no `scripts/**` per-file-ignore, so parsing with
`ElementTree` would trip S314, and neither downgrading that gate nor adding
`defusedxml` (which would drag `uv.lock` into the `pip-audit` gate) is warranted
for a one-shot log scrape.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

_DEFAULT_RESULTS_PATH = Path("boundary-smoke-results.xml")
_MAX_BODY_CHARS = 60_000
_TRUNCATION_MARKER = "\n\n[truncated — see boundary-smoke-results artifact]"
_FALLBACK_BODY = "could not extract — see artifact"

_ELEMENT_RE = re.compile(
    r"<(?P<tag>failure|error)\b(?P<attrs>[^>]*?)(?:/>|>(?P<text>.*?)</(?P=tag)>)",
    re.DOTALL,
)
_MESSAGE_ATTR_RE = re.compile(r'\bmessage="([^"]*)"')


def _element_detail(attrs: str, text: str | None) -> str:
    detail = text.strip() if text else ""
    if detail:
        return detail
    match = _MESSAGE_ATTR_RE.search(attrs)
    return match.group(1) if match else ""


def _extract_failure_text(xml_text: str) -> str | None:
    details = [
        html.unescape(detail)
        for match in _ELEMENT_RE.finditer(xml_text)
        if (detail := _element_detail(match.group("attrs"), match.group("text")))
    ]
    if not details:
        return None
    return "\n\n---\n\n".join(details)


def _truncate(body: str) -> str:
    if len(body) <= _MAX_BODY_CHARS:
        return body
    return body[: _MAX_BODY_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _fence_for(text: str) -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest_run + 1)


def _wrap_in_fence(text: str) -> str:
    fence = _fence_for(text)
    return f"{fence}\n{text}\n{fence}"


def render_body(results_path: Path = _DEFAULT_RESULTS_PATH) -> str:
    try:
        xml_text = results_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _FALLBACK_BODY
    extracted = _extract_failure_text(xml_text)
    if not extracted:
        return _FALLBACK_BODY
    return _wrap_in_fence(_truncate(extracted))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(_DEFAULT_RESULTS_PATH))
    try:
        return parser.parse_args()
    except SystemExit as exc:
        if exc.code == 0:
            raise
        raise SystemExit(1) from exc


def main() -> int:
    args = _parse_args()
    print(render_body(Path(args.results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
