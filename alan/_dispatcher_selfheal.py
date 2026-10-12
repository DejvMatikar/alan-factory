from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import alan._dispatcher_base as _base
from alan import acceptance as _acceptance_mod
from alan import scenario_binding
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, TestScenario, list_handovers, save_handover
from alan.loop_guard import fix_cycle_park_reason, is_no_commit_escalation
from alan.park import Blocker
from alan.queue import Job
from alan.roots import logs_root
from alan.router import RouterDecision
from alan.teams import _pr_forge, _pr_ready, _pr_state_paths
from alan.teams._audit import append_jsonl_audit
from alan.teams._scenario_runner import ScenarioResult, run_scenarios
from alan.teams._spiral_handover import fix_cycle_ceiling_handover
from alan.terminal_outcome import has_merged_sibling, merged_sibling_pr_number
from alan.workspace import Requirement, Task, TaskStatus, Workspace

logger = logging.getLogger("alan.dispatcher")

_SELF_HEAL_AUDIT_FILE = "self_heal_audit.jsonl"
_SELF_HEAL_SCENARIO_BUDGET_S = 300.0


@dataclass(frozen=True)
class ProbeCheckout:
    path: Path | None
    skip_reason: str | None


class _MissingParentHandoverError(RuntimeError):  # pyright: ignore[reportUnusedClass]
    pass


_TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED}
)


def _probe_checkout_for(ws: Workspace) -> ProbeCheckout:
    forge_target = _base.forge_target_for_ws(ws)
    if forge_target.repo.slug != DEFAULT_FORGE_TARGET.repo.slug:
        return ProbeCheckout(
            path=None,
            skip_reason=f"no checkout for forge {forge_target.repo.slug}",
        )
    return ProbeCheckout(path=Path.cwd(), skip_reason=None)


_UNEVALUATED_STATUSES: frozenset[str] = frozenset(
    {"SKIPPED", "DOCUMENTED", "TIMEOUT", "BUDGET_EXHAUSTED"}
)


def _scenarios_confirm_done(results: list[ScenarioResult]) -> bool:
    statuses = {r.status for r in results}
    if statuses & _UNEVALUATED_STATUSES:
        return False
    return bool({"PASS", "ADVISORY"} & statuses) and "FAIL" not in statuses


def _unevaluated_scenario_ids(results: list[ScenarioResult]) -> list[str]:
    return [r.scenario_id for r in results if r.status in _UNEVALUATED_STATUSES]


def _skip_unevaluated_ids(ws: Workspace, task_id: str) -> list[str]:
    task = next((t for t in ws.meta.tasks if t.id == task_id), None)
    return [sc.id for sc in task.scenarios] if task is not None else []


def _all_task_scenarios(ws: Workspace) -> list[TestScenario]:  # pyright: ignore[reportUnusedFunction]
    seen: set[str] = set()
    result: list[TestScenario] = []
    for task in ws.meta.tasks:
        for sc in task.scenarios:
            if sc.id not in seen:
                seen.add(sc.id)
                result.append(sc)
    return result


def _has_remaining_active_tasks(ws: Workspace, completing_task_id: str) -> bool:
    return any(
        t.id != completing_task_id and t.status not in _TERMINAL_TASK_STATUSES
        for t in ws.meta.tasks
    )


def _pending_test_refs(requirements: list[Requirement]) -> set[str]:
    return {
        r.test_ref
        for r in _acceptance_mod.live_clauses(requirements)
        if r.test_ref and r.status != "PASS"
    }


def _available_scenario_ids(ws: Workspace) -> set[str]:
    return {sc.id for t in ws.meta.tasks for sc in t.scenarios}


def _drains_with_unresolvable_test_refs(ws: Workspace, completing_task_id: str) -> bool:
    if ws.meta.requirements is None:
        return False
    if _has_remaining_active_tasks(ws, completing_task_id):
        return False
    pending_refs = _pending_test_refs(ws.meta.requirements)
    if not pending_refs:
        return False
    return not pending_refs.issubset(_available_scenario_ids(ws))


def _needs_oob_scenario_probe(ws: Workspace, task_id: str) -> bool:  # pyright: ignore[reportUnusedFunction]
    return (
        ws.meta.requirements is not None
        and bool(ws.meta.tasks)
        and all(
            t.status == TaskStatus.COMPLETED for t in ws.meta.tasks if t.id != task_id
        )
    )


def _validate_oob_completion(ws: Workspace, task_id: str) -> None:  # pyright: ignore[reportUnusedFunction]
    task = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if task is None:
        raise _base.OutOfBandCompletionError(
            f"task {task_id!r} not found in workspace {ws.workspace_id!r}"
        )
    if task.status in _TERMINAL_TASK_STATUSES:
        raise _base.OutOfBandCompletionError(
            f"task {task_id!r} is already terminal ({task.status.value})"
        )
    if _drains_with_unresolvable_test_refs(ws, task_id):
        raise _base.OutOfBandCompletionError(
            f"workspace {ws.workspace_id!r} has pending test_ref requirements "
            "with no matching scenario to resolve them"
        )


def _latest_pr_number(ws: Workspace) -> int | None:
    try:
        handovers = list_handovers(ws)
    except Exception:  # noqa: BLE001 — best-effort lookup, never blocks the park path
        return None
    for h in reversed(handovers):
        if h.pr_number is not None:
            return h.pr_number
    return None


def _only_pr_merge_feeds_deploy_verify(graph: dict[str, list[str]]) -> bool:
    feeders = {team for team, targets in graph.items() if "deploy_verify" in targets}
    return feeders == {"pr_merge"}


def _merged_stamp_blocker(ws: Workspace, failed_job: Job) -> Blocker | None:
    if failed_job.team != "deploy_verify" or _latest_pr_number(ws) is None:
        return None
    snapshot = ws.meta.engine_snapshot
    if snapshot is None or not _only_pr_merge_feeds_deploy_verify(snapshot.transition_graph):
        return None
    return Blocker(
        kind="reason", name="delivery_stamp_pending",
        detail="merged and live; only the delivery stamp is pending",
    )


def merged_stamp_blocker(ws: Workspace, failed_job: Job) -> Blocker | None:
    try:
        return _merged_stamp_blocker(ws, failed_job)
    except Exception:  # noqa: BLE001 — this predicate must never itself crash a park path
        return None


def _last_fix_ceiling_finding(handover: Handover, decision: RouterDecision) -> str:
    if decision.reason:
        return decision.reason[:300]
    return handover.full_synthesis.strip()[:300]


def _resolve_self_heal_task(ws: Workspace, task_id: str) -> Task | None:
    task = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if task is None or not task.scenarios:
        return None
    return task


def _no_commit_route_pr_number(
    ws: Workspace, handover: Handover, decision: RouterDecision
) -> int | None:
    if decision.kind != "escalate" or not is_no_commit_escalation(handover):
        return None
    if not ws.meta.tasks or has_merged_sibling(ws):
        return None
    return handover.pr_number


class SelfHealMixin(_base.DispatcherState):
    def _self_heal_pr_number(
        self, ws: Workspace, handover: Handover, decision: RouterDecision
    ) -> int | None:
        if decision.kind != "escalate" or not is_no_commit_escalation(handover):
            return None
        if not ws.meta.tasks or not has_merged_sibling(ws):
            return None
        return merged_sibling_pr_number(ws)

    def _route_noop_no_commit(
        self, ws: Workspace, handover: Handover, decision: RouterDecision
    ) -> RouterDecision:
        pr_number = _no_commit_route_pr_number(ws, handover, decision)
        if pr_number is None:
            return decision
        readiness = _pr_ready.readiness_or_unavailable(
            pr_number, ws.task_id, forge_target=_pr_forge.forge_target_for_ws(ws),
        )
        if isinstance(readiness, _pr_ready.ReadinessUnavailable) or not readiness.ok:
            return decision
        return RouterDecision(
            kind="enqueue",
            team="verification",
            reason=(
                f"no-op fix produced no commits on green PR #{pr_number} "
                "— routing to verification"
            ),
        )

    def _enforce_fix_ceiling(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str,
    ) -> tuple[RouterDecision, Handover]:
        del task_id
        if decision.kind != "enqueue" or decision.team != "implementation_fix":
            return decision, handover
        if handover.pr_number is None:
            return decision, handover
        pr_number = handover.pr_number
        forge_target = _pr_forge.forge_target_for_ws(ws)
        bumped = _pr_state_paths.bump_fix_cycle_count_if_under_ceiling(pr_number, forge_target)
        if bumped is not None:
            return decision, handover
        count = _pr_state_paths.load_fix_cycle_state(pr_number, forge_target).count
        last_finding = _last_fix_ceiling_finding(handover, decision)
        reason = fix_cycle_park_reason(count, last_finding)
        park_decision = RouterDecision(
            kind="await_human",
            reason=reason,
            blockers=(Blocker(kind="reason", name="fix_cycle_ceiling", detail=reason),),
        )
        park_handover = fix_cycle_ceiling_handover(ws, handover, count, last_finding)
        save_handover(ws, park_handover)
        return park_decision, park_handover

    def _run_self_heal_scenarios(self, ws: Workspace, task: Task) -> list[ScenarioResult]:
        checkout = _probe_checkout_for(ws)
        if checkout.path is None:
            return []
        try:
            return run_scenarios(
                scenario_binding.runnable(ws.meta, task.scenarios),
                cwd=str(checkout.path),
                aggregate_budget=_SELF_HEAL_SCENARIO_BUDGET_S,
            )
        except Exception:  # noqa: BLE001 — best-effort probe; any failure → no heal
            logger.warning(
                "self-heal scenario run failed for task %s", task.id, exc_info=True
            )
            return []

    def _self_heal_skip(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str,
        pr_number: int, skip_reason: str | None,
    ) -> tuple[RouterDecision, Handover]:
        self._maybe_record_self_heal_audit(
            ws, task_id, pr_number, [], skip_reason=skip_reason
        )
        return decision, handover

    def _maybe_self_heal(
        self, ws: Workspace, handover: Handover, decision: RouterDecision, task_id: str
    ) -> tuple[RouterDecision, Handover]:
        pr_number = self._self_heal_pr_number(ws, handover, decision)
        if pr_number is None:
            return self._route_noop_no_commit(ws, handover, decision), handover
        task = _resolve_self_heal_task(ws, task_id)
        if task is None:
            return decision, handover
        checkout = _probe_checkout_for(ws)
        if checkout.path is None:
            return self._self_heal_skip(
                ws, handover, decision, task_id, pr_number, checkout.skip_reason
            )
        results = self._run_self_heal_scenarios(ws, task)
        self._maybe_record_self_heal_audit(ws, task_id, pr_number, results)
        if not _scenarios_confirm_done(results):
            return decision, handover
        return (
            RouterDecision(
                kind="done",
                reason=f"self-heal: DoD scenarios pass on merged main (PR #{pr_number})",
            ),
            handover.model_copy(update={"pr_number": pr_number}),
        )

    def _maybe_record_self_heal_audit(
        self,
        ws: Workspace,
        task_id: str,
        pr_number: int,
        results: list[ScenarioResult],
        *,
        skip_reason: str | None = None,
    ) -> None:
        if not results and skip_reason is None:
            return
        unevaluated = (
            _skip_unevaluated_ids(ws, task_id)
            if skip_reason is not None
            else _unevaluated_scenario_ids(results)
        )
        self._record_self_heal_audit(
            ws, task_id, pr_number, results, skip_reason=skip_reason, unevaluated=unevaluated
        )

    def _record_self_heal_audit(
        self,
        ws: Workspace,
        task_id: str,
        pr_number: int,
        results: list[ScenarioResult],
        *,
        skip_reason: str | None = None,
        unevaluated: list[str] | None = None,
    ) -> None:
        append_jsonl_audit(
            logs_root() / _SELF_HEAL_AUDIT_FILE,
            {
                "task_id": task_id,
                "workspace_id": ws.workspace_id,
                "pr_number": pr_number,
                "scenarios": [(r.scenario_id, r.status) for r in results],
                "unevaluated_scenario_ids": (
                    unevaluated if unevaluated is not None
                    else _unevaluated_scenario_ids(results)
                ),
                "skipped_reason": skip_reason,
            },
            log_label="self_heal",
        )
