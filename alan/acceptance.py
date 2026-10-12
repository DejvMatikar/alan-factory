from __future__ import annotations

from alan.workspace import Meta, Requirement, RequirementStatus, Task, TaskDoDClause

TASK_COUNT_BUDGET = 10


def _resolved_status(
    req: Requirement, result_map: dict[str, str]
) -> RequirementStatus | None:
    if not req.test_ref:
        return None
    raw = result_map.get(req.test_ref)
    if raw is None or raw == "PENDING":
        return None
    return "PASS" if raw in ("PASS", "ADVISORY") else "FAIL"


def evaluate_requirements(
    scenario_results: list[tuple[str, str]],
    requirements: list[Requirement],
) -> list[Requirement]:
    result_map = dict(scenario_results)
    updated: list[Requirement] = []
    for req in requirements:
        new_status = _resolved_status(req, result_map)
        if new_status is None:
            updated.append(req)
        else:
            updated.append(req.model_copy(update={"status": new_status}))
    return updated


def is_withdrawn(req: Requirement) -> bool:
    return req.withdrawn_by_question_id is not None


def live_clauses(reqs: list[Requirement]) -> list[Requirement]:
    return [r for r in reqs if not is_withdrawn(r)]


def all_requirements_satisfied(meta: Meta) -> bool:
    if meta.requirements is None:
        return True
    if not meta.requirements:
        return False
    return all(r.status == "PASS" for r in live_clauses(meta.requirements) if r.test_ref)


def reconcile_clauses(
    existing: list[Requirement], new_texts: list[str]
) -> list[Requirement]:
    seen = {r.id for r in existing}
    appended: list[Requirement] = []
    for text in new_texts:
        if not text.strip():
            continue
        clause = Requirement(text=text)
        if clause.id in seen:
            continue
        seen.add(clause.id)
        appended.append(clause)
    return [*existing, *appended]


def _relevant_covers(covers: list[str], clause_ids: set[str]) -> set[str]:
    return {cid for cid in covers if cid in clause_ids}


def _owned_clause_ids(task: Task, clause_ids: set[str]) -> set[str]:
    result: set[str] = set()
    for clause in task.dod_clauses:
        result.update(_relevant_covers(clause.covers, clause_ids))
    return result


def resolve_owners(entries: list[tuple[str, set[str]]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for task_id, ids in entries:
        for rid in ids:
            owners[rid] = task_id
    return owners


def _group_owners(meta: Meta, parent_id: str, clause_ids: set[str]) -> dict[str, str]:
    entries = [
        (t.id, _owned_clause_ids(t, clause_ids))
        for t in meta.tasks
        if t.parent_task_id == parent_id
    ]
    return resolve_owners(entries)


def _sibling_parent_ids(meta: Meta) -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    for t in meta.tasks:
        if t.parent_task_id is not None and t.parent_task_id not in seen:
            seen.add(t.parent_task_id)
            order.append(t.parent_task_id)
    return order


def _all_group_owners(meta: Meta, clause_ids: set[str]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for parent_id in _sibling_parent_ids(meta):
        owners.update(_group_owners(meta, parent_id, clause_ids))
    return owners


def clause_owner_map(meta: Meta) -> dict[str, str]:
    clause_ids = _req_ids(meta)
    roots = resolve_owners([
        (t.id, _owned_clause_ids(t, clause_ids))
        for t in meta.tasks
        if t.parent_task_id is None
    ])
    return {**roots, **_all_group_owners(meta, clause_ids)}


def _is_owner(task: Task, rid: str, group_owners: dict[str, str]) -> bool:
    if task.parent_task_id is None:
        return True
    return group_owners.get(rid) == task.id


def owned_clause_texts(meta: Meta, task_id: str) -> dict[str, list[str]]:
    task = _find_task(meta, task_id)
    if task is None:
        return {}
    result: dict[str, list[str]] = {}
    for clause in task.dod_clauses:
        for rid in clause.covers:
            result.setdefault(rid, []).append(clause.text)
    return result


def _find_task(meta: Meta, task_id: str) -> Task | None:
    return next((t for t in meta.tasks if t.id == task_id), None)


def _req_ids(meta: Meta) -> set[str]:
    return {r.id for r in (meta.requirements or [])}


def _texts_for_ids(reqs: list[Requirement], ids: set[str]) -> list[str]:
    return [r.text for r in reqs if r.id in ids]


def _clause_texts(task: Task) -> list[str]:
    return [clause.text for clause in task.dod_clauses]


def _sibling_clause_texts(meta: Meta, task_id: str, parent_id: str | None) -> list[str]:
    if parent_id is None:
        return []
    return [
        text
        for t in meta.tasks
        if t.id != task_id and t.parent_task_id == parent_id
        for text in _clause_texts(t)
    ]


def _clause_text_scope(meta: Meta, current: Task) -> tuple[list[str], list[str]]:
    own = _clause_texts(current)
    siblings = _sibling_clause_texts(meta, current.id, current.parent_task_id)
    return own, [text for text in siblings if text not in own]


def _resolve_ownership(
    meta: Meta, current: Task, owned_all: set[str], clause_ids: set[str]
) -> tuple[set[str], set[str]]:
    if current.parent_task_id is None:
        return owned_all, set()
    owners = _group_owners(meta, current.parent_task_id, clause_ids)
    owned = {rid for rid in owned_all if owners.get(rid) == current.id}
    sibling_owned = {rid for rid, owner in owners.items() if owner != current.id}
    return owned, sibling_owned


def _dod_clause_is_live(clause: TaskDoDClause, req_by_id: dict[str, Requirement]) -> bool:
    resolved = [req_by_id[cid] for cid in clause.covers if cid in req_by_id]
    return not resolved or any(not is_withdrawn(r) for r in resolved)


def _live_dod_clauses(task: Task, req_by_id: dict[str, Requirement]) -> list[TaskDoDClause]:
    return [c for c in task.dod_clauses if _dod_clause_is_live(c, req_by_id)]


def _with_live_dod(task: Task, req_by_id: dict[str, Requirement]) -> Task:
    live = _live_dod_clauses(task, req_by_id)
    if live == task.dod_clauses:
        return task
    return task.model_copy(update={"dod_clauses": live})


def _live_scope_meta(meta: Meta, req_by_id: dict[str, Requirement]) -> Meta:
    new_tasks = [_with_live_dod(t, req_by_id) for t in meta.tasks]
    if new_tasks == meta.tasks:
        return meta
    return meta.model_copy(update={"tasks": new_tasks})


def _scoped_dod_from_live(
    meta: Meta, live_meta: Meta, live_current: Task
) -> tuple[list[str], list[str]]:
    clause_ids = _req_ids(meta)
    owned_all = _owned_clause_ids(live_current, clause_ids)
    if not owned_all:
        return _clause_text_scope(live_meta, live_current)
    owned, sibling_owned = _resolve_ownership(live_meta, live_current, owned_all, clause_ids)
    if not owned:
        return _clause_text_scope(live_meta, live_current)
    reqs = meta.requirements or []
    return _texts_for_ids(reqs, owned), _texts_for_ids(reqs, sibling_owned)


def scoped_dod(
    meta: Meta, task_id: str
) -> tuple[list[str], list[str]] | None:
    current = _find_task(meta, task_id)
    if current is None or not current.dod_clauses:
        return None
    req_by_id = {r.id: r for r in (meta.requirements or [])}
    live_meta = _live_scope_meta(meta, req_by_id)
    live_current = _find_task(live_meta, task_id) or current
    if not live_current.dod_clauses:
        return [], _sibling_clause_texts(live_meta, current.id, current.parent_task_id)
    return _scoped_dod_from_live(meta, live_meta, live_current)


def scoped_clause_requirements(meta: Meta, task_id: str) -> list[Requirement] | None:
    current = _find_task(meta, task_id)
    if current is None or not current.dod_clauses:
        return None
    clause_ids = _req_ids(meta)
    owned_all = _owned_clause_ids(current, clause_ids)
    owned, _sibling_owned = _resolve_ownership(meta, current, owned_all, clause_ids)
    reqs = meta.requirements or []
    return [r for r in reqs if r.id in owned]


def _clause_evidenced_ids(
    clause: TaskDoDClause,
    task: Task,
    relevant: set[str],
    group_owners: dict[str, str],
) -> set[str]:
    if clause.evidence is None:
        return set()
    return {rid for rid in relevant if _is_owner(task, rid, group_owners)}


def _covered_clause_ids(tasks: list[Task], clause_ids: set[str]) -> set[str]:
    covered: set[str] = set()
    for task in tasks:
        for clause in task.dod_clauses:
            covered.update(_relevant_covers(clause.covers, clause_ids))
    return covered


def _coverage_and_evidence(meta: Meta) -> tuple[set[str], set[str]]:
    clause_ids = {r.id for r in meta.requirements or []}
    group_owners = _all_group_owners(meta, clause_ids)
    covered = _covered_clause_ids(meta.tasks, clause_ids)
    evidenced: set[str] = set()
    for task in meta.tasks:
        for clause in task.dod_clauses:
            relevant = _relevant_covers(clause.covers, clause_ids)
            evidenced.update(_clause_evidenced_ids(clause, task, relevant, group_owners))
    return covered, evidenced


def _clause_ok(clause: Requirement, covered: set[str], evidenced: set[str]) -> bool:
    if clause.test_ref:
        return clause.status == "PASS"
    if clause.id not in covered:
        return True
    return clause.id in evidenced


def all_clauses_satisfied(meta: Meta) -> bool:
    if meta.requirements is None:
        return True
    if not meta.requirements:
        return False
    live = live_clauses(meta.requirements)
    if not live:
        return True
    covered, evidenced = _coverage_and_evidence(meta)
    if not covered:
        return all_requirements_satisfied(meta)
    return all(_clause_ok(c, covered, evidenced) for c in live)


def unproven_clause_ids(
    requirements: list[Requirement] | None, tasks: list[Task]
) -> set[str]:
    if not requirements:
        return set()
    live = live_clauses(requirements)
    clause_ids = {r.id for r in live}
    covered = _covered_clause_ids(tasks, clause_ids)
    return {r.id for r in live if not r.test_ref and r.id not in covered}


def requirements_unbound(meta: Meta) -> bool:
    reqs = meta.requirements or []
    unproven = unproven_clause_ids(meta.requirements, meta.tasks)
    return bool(reqs) and len(unproven) == len(reqs)
