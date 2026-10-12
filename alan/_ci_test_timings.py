from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

SLOWEST_LIMIT = 5
WEB_PREFIX = "alan/web/"

_TESTCASE = re.compile(r"<testcase\b(?P<attrs>[^>]*?)/?>")
_ATTRIBUTE = re.compile(r'\b(?P<key>[\w:-]+)="(?P<value>[^"]*)"')
_JsonObject = dict[str, object]


def _attributes(raw: str) -> dict[str, str]:
    return {
        match.group("key"): html.unescape(match.group("value"))
        for match in _ATTRIBUTE.finditer(raw)
    }


def testcase_file(classname: str) -> str:
    if not classname:
        return ""
    if "/" in classname or classname.endswith((".ts", ".tsx", ".py")):
        return classname
    parts: list[str] = []
    for part in classname.split("."):
        if part[:1].isupper():
            break
        parts.append(part)
    return "/".join(parts) + ".py"


def _seconds(raw: str | None) -> float:
    try:
        return float(raw) if raw is not None else 0.0
    except ValueError:
        return 0.0


def junit_file_durations(text: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for match in _TESTCASE.finditer(text):
        attrs = _attributes(match.group("attrs"))
        file = attrs.get("file") or testcase_file(attrs.get("classname", ""))
        if file:
            totals[file] = totals.get(file, 0.0) + _seconds(attrs.get("time"))
    return totals


def _spec_seconds(spec: _JsonObject) -> float:
    total_ms = 0.0
    for test in _objects(spec.get("tests")):
        for result in _objects(test.get("results")):
            duration = result.get("duration")
            if isinstance(duration, int | float) and not isinstance(duration, bool):
                total_ms += duration
    return total_ms / 1000.0


def _objects(value: object) -> list[_JsonObject]:
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast(_JsonObject, item) for item in items if isinstance(item, dict)]


def _collect_suite(suite: _JsonObject, inherited: str, totals: dict[str, float]) -> None:
    suite_file = suite.get("file")
    fallback = suite_file if isinstance(suite_file, str) else inherited
    for spec in _objects(suite.get("specs")):
        spec_file = spec.get("file")
        file = spec_file if isinstance(spec_file, str) else fallback
        if file:
            totals[file] = totals.get(file, 0.0) + _spec_seconds(spec)
    for child in _objects(suite.get("suites")):
        _collect_suite(child, fallback, totals)


def playwright_file_durations(text: str) -> dict[str, float]:
    try:
        data: object = json.loads(text)
    except ValueError:
        return {}
    totals: dict[str, float] = {}
    if isinstance(data, dict):
        for suite in _objects(cast(_JsonObject, data).get("suites")):
            _collect_suite(suite, "", totals)
    return totals


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_junit(path: Path) -> dict[str, float]:
    return junit_file_durations(_read(path))


def load_playwright(path: Path) -> dict[str, float]:
    return playwright_file_durations(_read(path))


def _normalized(path: str) -> str:
    return path.removeprefix(WEB_PREFIX)


def _same_file(changed: str, reported: str) -> bool:
    left, right = _normalized(changed), _normalized(reported)
    return left == right or left.endswith(f"/{right}") or right.endswith(f"/{left}")


def slowest_changed(
    durations: Mapping[str, float], changed: Sequence[str], limit: int = SLOWEST_LIMIT
) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for reported, seconds in durations.items():
        for path in changed:
            if _same_file(path, reported):
                totals[path] = totals.get(path, 0.0) + seconds
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]
