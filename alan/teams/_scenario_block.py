from __future__ import annotations

import re
import shlex
from typing import Any

import yaml
from pydantic import ValidationError

from alan.handover import TestScenario

SCENARIOS_BLOCK_RE = re.compile(
    r"```yaml[ \t]+test_scenarios\.yaml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

SCENARIOS_HEADING_RE = re.compile(
    r"^##\s+TEST\s+SCENARIOS\b[^\n]*\n+```ya?ml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

SCENARIOS_BLOCK_GUIDANCE = (
    "If the prior handover carried no test_scenarios (judge review "
    "mentions SCENARIOS_MISSING), append a fenced test_scenarios.yaml "
    "block covering the Definition of Done — one Given/When/Then entry "
    "per behavior, all five fields (id, description, given, when, then) "
    "required on every entry. Use this EXACT opening fence line, "
    "including the ` test_scenarios.yaml` info-string:\n"
    "```yaml test_scenarios.yaml\n"
    "- id: <snake_case_id>\n"
    "  description: <one-line summary>\n"
    "  given: <precondition>\n"
    "  when: <action>\n"
    "  then: <human-readable note; advisory only, never blocks>\n"
    "```\n"
    "The system parses this block and forwards the scenarios to the "
    "next verification run. Quote any free-text field value that "
    "contains a colon followed by a space (`: `) — left unquoted, that "
    "sequence starts a new YAML mapping key and breaks the block. Quote "
    "any value that opens with a single quote, a double quote, or a "
    "backtick. Before ending your turn, re-read the finished block and "
    "confirm it parses as YAML.\n\n"
)

_TOP_LEVEL_ITEM_RE = re.compile(r"^-(\s|$)")


def validate_scenario(item: Any) -> TestScenario | None:
    if not isinstance(item, dict):
        return None
    try:
        return TestScenario.model_validate(item)
    except ValidationError:
        return None


def _split_entries(block: str) -> list[str]:
    chunks: list[list[str]] = []
    for line in block.splitlines():
        if _TOP_LEVEL_ITEM_RE.match(line):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
    return ["\n".join(chunk) for chunk in chunks]


def _entry_scenario(chunk: str) -> TestScenario | None:
    try:
        parsed = yaml.safe_load(chunk)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    return validate_scenario(parsed[0])


def recover_entries(block: str) -> list[TestScenario]:
    result: list[TestScenario] = []
    for chunk in _split_entries(block):
        scenario = _entry_scenario(chunk)
        if scenario is not None:
            result.append(scenario)
    return result


def _source_line(block: str, mark: yaml.Mark | None) -> str | None:
    if mark is None:
        return None
    lines = block.splitlines()
    if not 0 <= mark.line < len(lines):
        return None
    return lines[mark.line]


def _parse_diagnostics(block: str) -> dict[str, str]:
    try:
        yaml.safe_load(block)
    except yaml.MarkedYAMLError as exc:
        diagnostics: dict[str, str] = {"parse_error": str(exc)}
        source_line = _source_line(block, exc.problem_mark)
        if source_line is not None:
            diagnostics["source_line"] = source_line
        return diagnostics
    except yaml.YAMLError as exc:
        return {"parse_error": str(exc)}
    return {}


def unparsed_audit_row(
    synthesis: str,
    scenarios: list[TestScenario],
    *,
    task_id: str,
    team: str,
) -> dict[str, object] | None:
    if scenarios:
        return None
    match = SCENARIOS_BLOCK_RE.search(synthesis) or SCENARIOS_HEADING_RE.search(synthesis)
    if match is None:
        return None
    row: dict[str, object] = {
        "task_id": task_id,
        "action": "scenario_block_unparsed",
        "team": team,
    }
    row.update(_parse_diagnostics(match.group(1)))
    return row


_NPM_WHOLE_SUITE_SCRIPTS: frozenset[str] = frozenset(
    {"test", "test:coverage", "e2e", "audit:mobile", "audit:desktop"}
)


_PYTEST_FILTER_FLAGS: frozenset[str] = frozenset({"-k", "-m"})


def _looks_like_pytest_target(tok: str) -> bool:
    return "/" in tok or "::" in tok or tok.endswith(".py")


def _pytest_unscoped(tokens: list[str]) -> bool:
    if "pytest" not in tokens:
        return False
    idx = tokens.index("pytest")
    for tok in tokens[idx + 1 :]:
        if tok in _PYTEST_FILTER_FLAGS:
            return False
        if not tok.startswith("-") and _looks_like_pytest_target(tok):
            return False
    return True


def _npm_has_scoping_arg(rest: list[str]) -> bool:
    if rest[0] == "--" and len(rest) > 1:
        return True
    return any("/" in tok for tok in rest)


def _npm_unscoped(tokens: list[str]) -> bool:
    if "run" not in tokens:
        return False
    script_idx = tokens.index("run") + 1
    if script_idx >= len(tokens):
        return False
    if tokens[script_idx] not in _NPM_WHOLE_SUITE_SCRIPTS:
        return False
    rest = tokens[script_idx + 1 :]
    if not rest:
        return True
    return not _npm_has_scoping_arg(rest)


def unscoped_suite_when(when: str) -> bool:
    try:
        tokens = shlex.split(when)
    except ValueError:
        return False
    if not tokens:
        return False
    if tokens[0] == "uv":
        return _pytest_unscoped(tokens)
    if tokens[0] == "npm":
        return _npm_unscoped(tokens)
    return False


__all__ = [
    "SCENARIOS_BLOCK_GUIDANCE",
    "SCENARIOS_BLOCK_RE",
    "SCENARIOS_HEADING_RE",
    "_npm_unscoped",
    "_pytest_unscoped",
    "recover_entries",
    "unparsed_audit_row",
    "unscoped_suite_when",
    "validate_scenario",
]
