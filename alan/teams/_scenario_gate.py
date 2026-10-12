from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from alan import store
from alan import workflow as _workflow_mod
from alan.handover import ScenarioOutcome
from alan.teams import _verdict
from alan.teams._scenario_runner import NON_BLOCKING_STATUSES
from alan.teams._verification_artifacts import append_audit
from alan.workspace import Workspace

_OUTCOMES_FILE = "scenario_outcomes.json"
_OUTCOMES_FILE_MODE = 0o600

_GATE_BANNER_TEMPLATE = (
    "> SCENARIO GATE — FAILED. Blocking scenario status on: {ids}. "
    "Downgrading to REQUEST_CHANGES regardless of the judge's own "
    "verdict.\n\n"
)


def outcomes_path(workspace: Workspace, inv_id: str) -> Path:
    return _workflow_mod.report_path(workspace, inv_id).parent / _OUTCOMES_FILE


def save_outcomes(
    workspace: Workspace, inv_id: str, outcomes: list[ScenarioOutcome]
) -> Path:
    path = outcomes_path(workspace, inv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(
        path,
        json.dumps([o.model_dump(mode="json") for o in outcomes]),
        mode=_OUTCOMES_FILE_MODE,
    )
    return path


def load_outcomes(workspace: Workspace, inv_id: str) -> list[ScenarioOutcome] | None:
    path = outcomes_path(workspace, inv_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list):
        return None
    items = cast("list[object]", raw)
    try:
        return [ScenarioOutcome.model_validate(item) for item in items]
    except ValidationError:
        return None


def _is_blocking_status(status: str) -> bool:
    return status != "PASS" and status not in NON_BLOCKING_STATUSES


_OUTCOMES_HEADING = "## Scenario outcomes (recorded)"

_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


def _single_line(text: str) -> str:
    return _LINE_BREAK_RE.sub(" ", text)


def _render_outcome_line(outcome: ScenarioOutcome) -> str:
    line = f"- {_single_line(outcome.scenario_id)}: {outcome.status}"
    if outcome.diagnostic:
        line += f" — {_single_line(outcome.diagnostic)}"
    return line


def _render_outcomes(outcomes: list[ScenarioOutcome]) -> str:
    lines = [_OUTCOMES_HEADING, *(_render_outcome_line(o) for o in outcomes)]
    return "\n".join(lines)


def apply(judge_output: str, workspace: Workspace, inv_id: str) -> str:
    outcomes = load_outcomes(workspace, inv_id)
    if outcomes is None:
        append_audit({
            "task_id": workspace.task_id,
            "inv_id": inv_id,
            "action": "scenario_gate_no_outcomes",
        })
        return judge_output
    if not outcomes:
        return judge_output
    rendered = _render_outcomes(outcomes)
    blocking = [o for o in outcomes if _is_blocking_status(o.status)]
    if not blocking:
        return f"{judge_output}\n\n{rendered}"
    banner = _GATE_BANNER_TEMPLATE.format(
        ids=", ".join(_single_line(o.scenario_id) for o in blocking)
    )
    rewritten = _verdict.rewrite_verdict_to_request_changes(judge_output, banner)
    return f"{rewritten}\n\n{rendered}"


__all__ = [
    "apply",
    "load_outcomes",
    "outcomes_path",
    "save_outcomes",
]
