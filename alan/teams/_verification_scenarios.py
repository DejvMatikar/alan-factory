from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import alan.teams._scenario_runner as _scenario_runner
from alan import workflow as _workflow_mod
from alan.engine_config import EngineConfig
from alan.handover import Handover, ScenarioOutcome, TestScenario
from alan.teams import _changed_e2e
from alan.teams._changed_e2e import CHANGED_E2E_SCENARIO_ID
from alan.teams._scenario_runner import ScenarioResult
from alan.workflow import WorkflowError as _WorkflowError
from alan.workspace import Task as _Task
from alan.workspace import Workspace

_BASE_UNRESOLVED_DIAGNOSTIC = (
    "changed-e2e base unresolved: git fetch origin main failed, timed out, or "
    "git was unavailable — selection skipped, NOT confirmed clean"
)


def _format_dod_scope_block(  # pyright: ignore[reportUnusedFunction]
    in_scope: list[str], out_of_scope: list[str]
) -> str:
    lines = ["## IN-SCOPE ACCEPTANCE OUTCOMES"]
    lines.extend(f"- {t}" for t in in_scope)
    if out_of_scope:
        lines.append("\n## OUT-OF-SCOPE ACCEPTANCE OUTCOMES")
        lines.extend(f"- {t}" for t in out_of_scope)
    return "\n".join(lines)


def _format_scenarios_block(  # pyright: ignore[reportUnusedFunction]
    scenarios: list[TestScenario], waived: frozenset[str]
) -> str:
    lines = ["## TEST SCENARIOS"]
    for idx, s in enumerate(scenarios, start=1):
        marker = " — waived (operator override, non-blocking)" if s.id in waived else ""
        lines.append(
            f"\n### {idx}. {s.id} — {s.description}{marker}\n"
            f"- Given: {s.given}\n- When: {s.when}\n- Then: {s.then}"
        )
    return "\n".join(lines)


def _scenarios_waived(  # pyright: ignore[reportUnusedFunction]
    task: _Task | None, prior: Handover | None
) -> bool:
    return (task is not None and task.scenarios_waived) or (
        prior is not None and (prior.scenarios_waived or prior.dependency_bot_authorized)
    )


def _changed_e2e_results(  # pyright: ignore[reportUnusedFunction]
    checkout: Path, cfg: EngineConfig | None, *, env: Mapping[str, str] | None = None
) -> list[ScenarioResult]:
    selection = _changed_e2e.build_changed_e2e_scenario(checkout, env=env)
    if selection.base_unresolved:
        return [
            ScenarioResult(
                scenario_id=CHANGED_E2E_SCENARIO_ID,
                status="ADVISORY",
                diagnostic=_BASE_UNRESOLVED_DIAGNOSTIC,
                stdout_tail="",
                stderr_tail="",
                returncode=None,
                elapsed_s=0.0,
            )
        ]
    if selection.scenario is None:
        return []
    return [
        _scenario_runner.run_scenario(
            selection.scenario,
            cwd=str(checkout),
            timeout=_changed_e2e.changed_e2e_timeout_s(),
            cfg=cfg,
        )
    ]


def _scenario_runner_already_succeeded(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, inv_id: str
) -> bool:
    try:
        graph = _workflow_mod.load_graph(workspace, inv_id)
    except _WorkflowError:
        return False
    return any(
        t.id == "scenario_runner" and t.status == "success"
        for t in graph.doc.tasks
    )


_DIAGNOSTIC_MAX = 2000


def _to_scenario_outcomes(  # pyright: ignore[reportUnusedFunction]
    results: list[ScenarioResult]
) -> list[ScenarioOutcome]:
    return [
        ScenarioOutcome(
            scenario_id=r.scenario_id,
            status=r.status,
            diagnostic=r.diagnostic[:_DIAGNOSTIC_MAX],
        )
        for r in results
    ]
