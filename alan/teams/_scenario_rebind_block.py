from __future__ import annotations

import re
from typing import cast

import yaml

from alan import scenario_binding, store
from alan.engine_config import EngineConfig
from alan.handover import TestScenario
from alan.health_repo_metrics import read_audit_tail
from alan.roots import logs_root
from alan.teams import _scenario_runner

_MAX_RENDERED_REJECTIONS = 20

RULE_UNKNOWN_SCENARIO = "unknown_scenario"
RULE_PROSE_WHEN = "prose_when"
RULE_ALLOWLIST = "allowlist"
RULE_REBIND_CAP = "rebind_cap"
RULE_TEST_REF_CONFLICT = "test_ref_conflict"
RULE_WAIVED_CONFLICT = "waived_conflict"

LIVE_REBIND_REJECTION_RULES: frozenset[str] = frozenset(
    {
        RULE_UNKNOWN_SCENARIO,
        RULE_PROSE_WHEN,
        RULE_ALLOWLIST,
        RULE_REBIND_CAP,
        RULE_TEST_REF_CONFLICT,
        RULE_WAIVED_CONFLICT,
    }
)

REBIND_BLOCK_RE = re.compile(
    r"```yaml[ \t]+scenario_rebind\.yaml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

REBIND_BLOCK_GUIDANCE = (
    "SCENARIO REBIND — the block above (if present) lists each current "
    "scenario id and the exact command that proves it today. If one of "
    "those commands no longer resolves because the test it names was "
    "renamed, moved, or reorganized since the plan was written — and you "
    "can point to a different command in the current checkout that proves "
    "the SAME behavior — correct WHERE THE PROOF LIVES, never what it "
    "asserts, by appending a fenced scenario_rebind.yaml block to your "
    "final synthesis. Use this EXACT opening fence line, including the "
    "` scenario_rebind.yaml` info-string:\n"
    "```yaml scenario_rebind.yaml\n"
    "<scenario_id>: <the exact command that now proves this scenario>\n"
    "```\n"
    "One entry per corrected scenario id — every scenario id you do not "
    "name is left exactly as it was. Only a plain string command is "
    "accepted per id; do not nest `when`/`waived`/`given`/`then`/"
    "`test_ref` fields under an id — a non-string value is dropped "
    "entirely, not partially honored. This can never waive a scenario: the "
    "command is checked for validity (parses cleanly, no forbidden shell "
    "operators, prefix on the scenario-runner allowlist) but is NOT "
    "executed before acceptance. An accepted correction is then evaluated "
    "strictly — if it later fails to resolve (a collection error or an "
    "unfound target), the scenario FAILS the gate instead of skipping it, "
    "so a wrong correction cannot silently drop out of verification.\n\n"
)


def _valid_entry(key: object, value: object) -> tuple[str, str] | None:
    if not isinstance(key, str) or not key.strip():
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return key, value


def parse_rebinds(synthesis: str) -> dict[str, str]:
    match = REBIND_BLOCK_RE.search(synthesis)
    if match is None:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    mapping = cast(dict[object, object], parsed)
    result: dict[str, str] = {}
    for raw_key, raw_value in mapping.items():
        entry = _valid_entry(raw_key, raw_value)
        if entry is not None:
            result[entry[0]] = entry[1]
    return result


def _parse_diagnostics(block: str) -> dict[str, str]:
    try:
        yaml.safe_load(block)
    except yaml.MarkedYAMLError as exc:
        diagnostics: dict[str, str] = {"parse_error": str(exc)}
        mark = exc.problem_mark
        if mark is not None:
            lines = block.splitlines()
            if 0 <= mark.line < len(lines):
                diagnostics["source_line"] = lines[mark.line]
        return diagnostics
    except yaml.YAMLError as exc:
        return {"parse_error": str(exc)}
    return {}


def unparsed_rebind_audit_row(
    synthesis: str,
    rebinds: dict[str, str],
    *,
    task_id: str,
    team: str,
) -> dict[str, object] | None:
    if rebinds:
        return None
    match = REBIND_BLOCK_RE.search(synthesis)
    if match is None:
        return None
    row: dict[str, object] = {
        "task_id": task_id,
        "action": "scenario_rebind_unparsed",
        "team": team,
    }
    row.update(_parse_diagnostics(match.group(1)))
    return row


def current_scenarios_block(
    scenarios: list[TestScenario], cfg: EngineConfig | None
) -> str:
    if not scenarios:
        return ""
    lines = ["\n\n## Current scenarios and their proving commands"]
    for s in scenarios:
        cmd = _scenario_runner.resolved_command(s, cfg)
        rendered = cmd if cmd.strip() else "(no command — prose only, not rebindable)"
        lines.append(f"- `{s.id}`: `{rendered}`")
    return "\n".join(lines)


def _is_rejection_for(row: dict[str, object], workspace_id: str) -> bool:
    rule = row.get("rule")
    return (
        row.get("action") == "fix_rebind_rejected"
        and row.get("workspace_id") == workspace_id
        and isinstance(rule, str)
        and rule in LIVE_REBIND_REJECTION_RULES
    )


def _rejected_rows(workspace_id: str) -> list[dict[str, object]]:
    text = read_audit_tail(logs_root() / scenario_binding.AUDIT_FILE)
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is not None and _is_rejection_for(row, workspace_id):
            rows.append(row)
    return rows


def rejected_rebinds_block(workspace_id: str) -> str:
    rows = _rejected_rows(workspace_id)
    if not rows:
        return ""
    lines = ["\n\n## Recent scenario rebind rejections"]
    for row in rows[-_MAX_RENDERED_REJECTIONS:]:
        lines.append(f"- `{row.get('scenario_id')}`: `{row.get('when')}` — {row.get('reason')}")
    return "\n".join(lines)


__all__ = [
    "LIVE_REBIND_REJECTION_RULES",
    "REBIND_BLOCK_GUIDANCE",
    "REBIND_BLOCK_RE",
    "RULE_ALLOWLIST",
    "RULE_PROSE_WHEN",
    "RULE_REBIND_CAP",
    "RULE_TEST_REF_CONFLICT",
    "RULE_UNKNOWN_SCENARIO",
    "RULE_WAIVED_CONFLICT",
    "current_scenarios_block",
    "parse_rebinds",
    "rejected_rebinds_block",
    "unparsed_rebind_audit_row",
]
