from __future__ import annotations

from typing import Literal

from alan.handover import Handover, TestScenario
from alan.workspace import Meta, ScenarioBinding, Workspace

ScenarioMode = Literal["RUN", "WAIVED", "MISSING"]
AUDIT_FILE = "scenario_override_audit.jsonl"

UNKNOWN_SCENARIO_DETAIL = "scenario_id not in the workspace's scenario set"
PROSE_WHEN_DETAIL = (
    "when field is prose (command prefix not in the scenario runner allowlist); "
    "a repointed scenario must stay executable"
)


def _apply_binding(scenario: TestScenario, binding: ScenarioBinding) -> TestScenario:
    update: dict[str, str] = {}
    if binding.when is not None:
        update["when"] = binding.when
    elif binding.test_ref is not None:
        # A test_ref-only repoint must blank `when` so _resolve_command's
        # fallback to test_ref actually engages — otherwise the scenario
        # keeps executing its stale `when` command untouched.
        update["when"] = ""
    if binding.test_ref is not None:
        update["test_ref"] = binding.test_ref
    return scenario.model_copy(update=update) if update else scenario


def bound(meta: Meta, scenarios: list[TestScenario]) -> list[TestScenario]:
    bindings = meta.scenario_bindings
    return [_apply_binding(s, bindings[s.id]) if s.id in bindings else s for s in scenarios]


def waived_ids(meta: Meta, scenarios: list[TestScenario]) -> frozenset[str]:
    present = {s.id for s in scenarios}
    return frozenset(
        sid for sid, b in meta.scenario_bindings.items() if b.waived and sid in present
    )


def runnable(meta: Meta, scenarios: list[TestScenario]) -> list[TestScenario]:
    waived = waived_ids(meta, scenarios)
    return [s for s in bound(meta, scenarios) if s.id not in waived]


def _resolved_task_id(ws: Workspace, task_id: str | None) -> str:
    return task_id if task_id is not None else ws.task_id


def base_scenarios(
    ws: Workspace, prior: Handover | None, *, task_id: str | None = None
) -> list[TestScenario]:
    tid = _resolved_task_id(ws, task_id)
    task = next((t for t in ws.meta.tasks if t.id == tid), None)
    if task is not None and task.scenarios:
        return task.scenarios
    if prior is not None:
        return prior.test_scenarios
    return []


def effective_scenarios(
    ws: Workspace, prior: Handover | None, *, task_id: str | None = None
) -> list[TestScenario]:
    return bound(ws.meta, base_scenarios(ws, prior, task_id=task_id))


def _legacy_waived(ws: Workspace, prior: Handover | None, *, task_id: str | None) -> bool:
    tid = _resolved_task_id(ws, task_id)
    task = next((t for t in ws.meta.tasks if t.id == tid), None)
    return (task is not None and task.scenarios_waived) or (
        prior is not None and (prior.scenarios_waived or prior.dependency_bot_authorized)
    )


def scenario_mode(
    ws: Workspace, prior: Handover | None, *, task_id: str | None = None
) -> ScenarioMode:
    base = base_scenarios(ws, prior, task_id=task_id)
    if _legacy_waived(ws, prior, task_id=task_id):
        return "WAIVED"
    if not base:
        return "MISSING"
    if waived_ids(ws.meta, base) == frozenset(s.id for s in base):
        return "WAIVED"
    return "RUN"


def fix_bound_ids(meta: Meta, scenarios: list[TestScenario]) -> frozenset[str]:
    present = {s.id for s in scenarios}
    return frozenset(
        sid
        for sid, b in meta.scenario_bindings.items()
        if b.written_by == "fix_rebind" and sid in present
    )


def all_base_scenarios(ws: Workspace, prior: Handover | None) -> list[TestScenario]:
    seen: set[str] = set()
    result: list[TestScenario] = []
    for task in ws.meta.tasks:
        for sc in task.scenarios:
            if sc.id not in seen:
                seen.add(sc.id)
                result.append(sc)
    if result:
        return result
    if prior is not None:
        return prior.test_scenarios
    return []


def blanket_waived_ids(ws: Workspace) -> frozenset[str]:
    return frozenset(
        sc.id for task in ws.meta.tasks if task.scenarios_waived for sc in task.scenarios
    )


def all_scenario_mode(ws: Workspace, prior: Handover | None) -> ScenarioMode:
    base = all_base_scenarios(ws, prior)
    if _legacy_waived(ws, prior, task_id=None):
        return "WAIVED"
    if not base:
        return "MISSING"
    waived = waived_ids(ws.meta, base) | blanket_waived_ids(ws)
    if frozenset(s.id for s in base) <= waived:
        return "WAIVED"
    return "RUN"


__all__ = [
    "AUDIT_FILE",
    "PROSE_WHEN_DETAIL",
    "UNKNOWN_SCENARIO_DETAIL",
    "ScenarioMode",
    "all_base_scenarios",
    "all_scenario_mode",
    "base_scenarios",
    "blanket_waived_ids",
    "bound",
    "effective_scenarios",
    "fix_bound_ids",
    "runnable",
    "scenario_mode",
    "waived_ids",
]
