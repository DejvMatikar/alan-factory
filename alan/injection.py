from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from alan.handover import InjectedTask
from alan.workspace import Task, TaskStatus, TestScenario, Workspace

_DEPTH_CAP = 2
_HASH_HEX_LEN = 16
_HASH_PREFIX = "inj-"


@dataclass
class InjectionPlan:
    to_add: list[Task] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    overflow: list[InjectedTask] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


def _content_hash(
    parent_task_id: str,
    team: str,
    instructions: str | None,
    resolved_depends_on: list[str],
    depth: int,
) -> str:
    key = "|".join([
        parent_task_id,
        team,
        instructions or "",
        ",".join(sorted(resolved_depends_on)),
        str(depth),
    ])
    digest = hashlib.sha256(key.encode()).hexdigest()[:_HASH_HEX_LEN]
    return f"{_HASH_PREFIX}{digest}"


def _validate_deps(tasks: list[InjectedTask]) -> None:
    local_ids = {t.id for t in tasks}
    if len(local_ids) < len(tasks):
        raise ValueError("duplicate task id")
    for t in tasks:
        for dep in t.depends_on:
            if dep not in local_ids:
                raise ValueError(f"unknown dependency: {dep!r}")


def _build_topo_graph(
    tasks: list[InjectedTask],
) -> tuple[dict[str, int], dict[str, list[str]], dict[str, InjectedTask]]:
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}
    task_map: dict[str, InjectedTask] = {t.id: t for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            adj[dep].append(t.id)
            in_degree[t.id] += 1
    return in_degree, adj, task_map


def _kahn_sort(
    in_degree: dict[str, int],
    adj: dict[str, list[str]],
    task_map: dict[str, InjectedTask],
) -> list[InjectedTask]:
    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    result: list[InjectedTask] = []
    cursor = 0
    while cursor < len(queue):
        tid = queue[cursor]
        cursor += 1
        result.append(task_map[tid])
        for neighbor in adj[tid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(task_map):
        raise ValueError("cycle detected")
    return result


def _topological_sort(tasks: list[InjectedTask]) -> list[InjectedTask]:
    _validate_deps(tasks)
    in_degree, adj, task_map = _build_topo_graph(tasks)
    return _kahn_sort(in_degree, adj, task_map)


def _collect_existing_ids(ws: Workspace) -> set[str]:
    return {t.id for t in ws.meta.tasks}


def _get_parent_depth(ws: Workspace, completed_task_id: str) -> int:
    for t in ws.meta.tasks:
        if t.id == completed_task_id:
            return t.injection_depth
    return 0


def _resolve_scenarios(
    scenario_ids: list[str],
    parent_scenarios: list[TestScenario] | None,
) -> list[TestScenario] | None:
    if not scenario_ids:
        return []
    if parent_scenarios is None:
        return None
    by_id = {s.id: s for s in parent_scenarios}
    result: list[TestScenario] = []
    for sid in scenario_ids:
        if sid not in by_id:
            return None
        result.append(by_id[sid])
    return result


def _scenarios_for_task(
    scenario_ids: list[str],
    parent_scenarios: list[TestScenario] | None,
) -> list[TestScenario]:
    return _resolve_scenarios(scenario_ids, parent_scenarios) or []


def plan_injection(
    ws: Workspace,
    completed_task_id: str,
    injected: list[InjectedTask],
    parent_scenarios: list[TestScenario] | None = None,
) -> InjectionPlan:
    if not injected:
        return InjectionPlan()
    parent_depth = _get_parent_depth(ws, completed_task_id)
    new_depth = parent_depth + 1
    if new_depth > _DEPTH_CAP:
        return InjectionPlan(overflow=list(injected))
    try:
        sorted_tasks = _topological_sort(injected)
    except ValueError:
        return InjectionPlan(overflow=list(injected))
    existing_ids = _collect_existing_ids(ws)
    id_map: dict[str, str] = {}
    to_add: list[Task] = []
    for inj in sorted_tasks:
        resolved_deps = [id_map[dep] for dep in inj.depends_on]
        content_id = _content_hash(
            completed_task_id, inj.team, inj.instructions, resolved_deps, new_depth
        )
        id_map[inj.id] = content_id
        if content_id in existing_ids:
            continue
        to_add.append(Task(
            id=content_id,
            team=inj.team,
            title=inj.title,
            instructions=inj.instructions,
            depends_on=resolved_deps,
            status=TaskStatus.PENDING,
            injection_depth=new_depth,
            dod_clauses=inj.dod_clauses,
            parent_task_id=completed_task_id,
            scenarios=_scenarios_for_task(inj.scenarios, parent_scenarios),
            scenarios_waived=inj.scenarios_waived,
            files=inj.files,
        ))
        existing_ids.add(content_id)
    return InjectionPlan(to_add=to_add)
