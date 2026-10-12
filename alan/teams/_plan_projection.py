from __future__ import annotations

import re
from collections.abc import Sequence

from alan.handover import TestScenario

_FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,})([^`]*)$")
_FENCE_CLOSE_RE = re.compile(r"^[ \t]*`{3,}[ \t]*$")
_DECISIONS_YAML_INFO_RE = re.compile(r"decisions\.ya?ml", re.IGNORECASE)
_TEST_SCENARIOS_KEY_RE = re.compile(r"^[ \t]*test_scenarios\s*:")

REDACTION_PLACEHOLDER = "[REDACTED: acceptance scenario reference]"


def _scan_fence(
    lines: list[str], open_match: re.Match[str], start: int, n: int
) -> tuple[str, list[str], int]:
    info = open_match.group(2).strip()
    body: list[str] = []
    j = start + 1
    while j < n:
        stripped = lines[j].rstrip("\r\n")
        if _FENCE_CLOSE_RE.match(stripped):
            return info, body, j + 1
        body.append(stripped)
        j += 1
    return info, body, n


def _is_scenario_fence(info: str, body: list[str]) -> bool:
    if _DECISIONS_YAML_INFO_RE.search(info) is not None:
        return True
    return any(_TEST_SCENARIOS_KEY_RE.match(line) for line in body)


def strip_scenario_fences(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        open_match = _FENCE_OPEN_RE.match(lines[i].rstrip("\r\n"))
        if open_match is None:
            out.append(lines[i])
            i += 1
            continue
        info, body, end = _scan_fence(lines, open_match, i, n)
        if not _is_scenario_fence(info, body):
            out.extend(lines[i:end])
        i = end
    return "".join(out)


def _scenario_needles(scenarios: Sequence[TestScenario]) -> list[str]:
    needles = {
        value.strip()
        for scenario in scenarios
        for value in (scenario.id, scenario.when)
        if value.strip()
    }
    return sorted(needles, key=len, reverse=True)


def redact_scenarios(
    text: str, scenarios: Sequence[TestScenario]
) -> tuple[str, int]:
    if not scenarios or not text:
        return text, 0
    count = 0
    for needle in _scenario_needles(scenarios):
        occurrences = text.count(needle)
        if occurrences:
            text = text.replace(needle, REDACTION_PLACEHOLDER)
            count += occurrences
    return text, count


__all__ = ["REDACTION_PLACEHOLDER", "redact_scenarios", "strip_scenario_fences"]
