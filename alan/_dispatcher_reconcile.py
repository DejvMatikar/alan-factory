from __future__ import annotations

import functools
import logging
from collections.abc import Callable

import alan._dispatcher_base as _base
import alan._dispatcher_selfheal as _selfheal
import alan.scenario_binding as scenario_binding
from alan import acceptance, workspace_dag
from alan import workspace as workspace_mod
from alan.handover import Handover
from alan.injection import plan_injection
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.teams._scenario_runner import run_scenarios
from alan.workspace import Task, TaskStatus, TestScenario, Workspace

logger = logging.getLogger("alan.dispatcher")

# Own copies — a module below every seam re-exports the workspace_dag
# mechanics for its own _propagate_and_cancel (below) and for backward
# compatibility with tests that patch this module's globals directly.
_propagate_task_failure = workspace_dag.propagate_task_failure
_stamp_evidence = workspace_dag.stamp_evidence
_workspace_terminal_status = workspace_dag.workspace_terminal_status


def _normalized_files(files: list[str]) -> list[str]:  # pyright: ignore[reportUnusedFunction]
    return list(dict.fromkeys(f.strip() for f in files))


def _is_sibling(task: Task, task_id: str, parent_task_id: str) -> bool:
    return (
        task.id != task_id
        and task.parent_task_id == parent_task_id
        and bool(task.files)
    )


def _sibling_manifests(ws: Workspace, task_id: str) -> dict[str, list[str]]:  # pyright: ignore[reportUnusedFunction]
    current = next((t for t in ws.meta.tasks if t.id == task_id), None)
    if current is None or current.parent_task_id is None:
        return {}
    return {
        t.id: t.files
        for t in ws.meta.tasks
        if _is_sibling(t, task_id, current.parent_task_id)
    }


def _propagate_and_cancel(  # pyright: ignore[reportUnusedFunction]
    queue: Queue, ws: Workspace, task_id: str
) -> tuple[Workspace, Workspace | None]:
    pre_failed = {t.id for t in ws.meta.tasks if t.status == TaskStatus.FAILED}
    ws = _propagate_task_failure(ws, task_id)
    propagated = (
        {t.id for t in ws.meta.tasks if t.status == TaskStatus.FAILED}
        - pre_failed
        - {task_id}
    )
    for tid in propagated:
        queue.delete_jobs(tid)
    terminal = _workspace_terminal_status(ws)
    if terminal is not None:
        ws = workspace_mod.transition(ws, terminal, clear_merge_block_class=True)
        return ws, ws
    return ws, None


class ReconcileMixin(_base.DispatcherState):
    def _maybe_evaluate_requirements(
        self,
        ws: Workspace,
        handover: Handover | None,
        *,
        precomputed_scenario_results: list[tuple[str, str]] | None = None,
    ) -> Workspace:
        if ws.meta.tasks and not workspace_dag.all_tasks_completed(ws):
            return ws
        if ws.meta.requirements is None:
            return ws
        if precomputed_scenario_results is not None:
            scenario_results = precomputed_scenario_results
        elif handover is not None:
            scenarios = scenario_binding.runnable(ws.meta, handover.test_scenarios)
            scenario_results = self._run_bound_scenarios(ws, scenarios)
        else:
            scenario_results = self._run_oob_requirement_scenarios(ws)
        updated = acceptance.evaluate_requirements(
            scenario_results, ws.meta.requirements
        )
        return workspace_mod.update_requirements(ws, updated)

    def _run_bound_scenarios(
        self, ws: Workspace, scenarios: list[TestScenario]
    ) -> list[tuple[str, str]]:
        if not scenarios:
            return []
        checkout = _selfheal._probe_checkout_for(ws)  # pyright: ignore[reportPrivateUsage]
        if checkout.path is None:
            logger.warning(
                "_run_bound_scenarios: skipping scenario probe for %s: %s",
                ws.workspace_id, checkout.skip_reason,
            )
            return []
        try:
            results = run_scenarios(
                scenarios,
                cwd=str(checkout.path),
                aggregate_budget=_selfheal._SELF_HEAL_SCENARIO_BUDGET_S,  # pyright: ignore[reportPrivateUsage]
            )
        except Exception:  # noqa: BLE001 — best-effort; leaves clauses PENDING on failure
            logger.warning("_run_bound_scenarios: scenario run failed", exc_info=True)
            return []
        keep = ("PASS", "FAIL", "ADVISORY", "TIMEOUT")
        return [(r.scenario_id, r.status) for r in results if r.status in keep]

    def _run_oob_requirement_scenarios(self, ws: Workspace) -> list[tuple[str, str]]:
        scenarios = scenario_binding.runnable(
            ws.meta,
            _selfheal._all_task_scenarios(ws),  # pyright: ignore[reportPrivateUsage]
        )
        return self._run_bound_scenarios(ws, scenarios)

    def complete_out_of_band_task(
        self, workspace_id: str, task_id: str, pr_number: int
    ) -> None:
        # Validate and, if this completion would drain the DAG, run the DoD
        # scenario probe (a subprocess call that can take up to
        # _SELF_HEAL_SCENARIO_BUDGET_S) *before* taking the per-workspace
        # lock — mirroring _route_after_handover/_maybe_self_heal's
        # probe-outside/mutate-inside split so this call can't stall the live
        # dispatcher or a Stop/Resume click for the workspace's duration.
        ws = workspace_mod.read(workspace_id, root=self._workspaces_root)
        _selfheal._validate_oob_completion(ws, task_id)  # pyright: ignore[reportPrivateUsage]
        scenario_results = (
            self._run_oob_requirement_scenarios(ws)
            if _selfheal._needs_oob_scenario_probe(ws, task_id)  # pyright: ignore[reportPrivateUsage]
            else None
        )
        lock = self._get_workspace_lock(workspace_id)
        with lock:
            ws = workspace_mod.read(workspace_id, root=self._workspaces_root)
            _selfheal._validate_oob_completion(ws, task_id)  # pyright: ignore[reportPrivateUsage]
            self._advance_workspace_dag(
                ws,
                task_id,
                evidence=f"PR #{pr_number}",
                precomputed_scenario_results=scenario_results,
            )

    def _apply_injection(
        self, ws: Workspace, task_id: str, handover: Handover
    ) -> Workspace:
        plan = plan_injection(
            ws, task_id, handover.injected_tasks,
            parent_scenarios=handover.test_scenarios,
        )
        overflow_ids = [t.id for t in plan.overflow]
        if plan.overflow:
            n = len(plan.overflow)
            logger.warning(
                "injection overflow: %d tasks dropped for workspace %s", n, ws.workspace_id
            )
            self._pubsub.publish(
                ws.workspace_id,
                TeamEvent("injection_overflow", {"dropped_count": str(n), "task_id": task_id}),
            )
        return workspace_mod.inject_tasks(ws, plan.to_add, overflow_ids)

    def _enqueue_ready(self, ws: Workspace) -> None:
        workspace_dag.enqueue_ready(
            ws,
            queue=self._queue,
            pubsub=self._pubsub,
            teams=self._teams,
            enqueue_workflow=_base.enqueue_with_pending_workflow,
        )

    def _bound_schedule_manager_summary(self) -> Callable[..., None]:
        # workspace_dag's own callback signature has no workspaces_root slot
        # (it is not in this task's file scope to edit) — binding it here
        # lets every schedule_manager_summary=... passed into workspace_dag
        # still resolve project_auto_continue's cross-item seam against this
        # Dispatcher's own configured root, not the ALAN_WORKSPACES_ROOT
        # env-var default.
        return functools.partial(
            _base._schedule_manager_summary,  # pyright: ignore[reportPrivateUsage]
            workspaces_root=self._workspaces_root,
        )

    def _advance_workspace_dag(
        self,
        ws: Workspace,
        task_id: str,
        handover: Handover | None = None,
        *,
        evidence: str | None = None,
        precomputed_scenario_results: list[tuple[str, str]] | None = None,
    ) -> None:
        workspace_dag.advance_workspace_dag(
            ws,
            task_id,
            queue=self._queue,
            pubsub=self._pubsub,
            teams=self._teams,
            handover=handover,
            evidence=evidence,
            precomputed_scenario_results=precomputed_scenario_results,
            apply_injection=self._apply_injection,
            evaluate_requirements=self._maybe_evaluate_requirements,
            schedule_manager_summary=self._bound_schedule_manager_summary(),
            enqueue_workflow=_base.enqueue_with_pending_workflow,
            workspace_index=self._workspace_index,
        )

    def _dag_has_active_work(self, ws: Workspace) -> bool:
        return workspace_dag.dag_has_active_work(ws, queue=self._queue)

    def _maybe_surface_stall(self, ws: Workspace, completed_task_id: str) -> None:
        workspace_dag.maybe_surface_stall(
            ws,
            completed_task_id,
            queue=self._queue,
            pubsub=self._pubsub,
            schedule_manager_summary=self._bound_schedule_manager_summary(),
            workspace_index=self._workspace_index,
        )

    def _maybe_recover_frozen_chain(self, ws: Workspace) -> None:
        workspace_dag.maybe_recover_frozen_chain(
            ws,
            queue=self._queue,
            pubsub=self._pubsub,
            teams=self._teams,
            enqueue_workflow=_base.enqueue_with_pending_workflow,
            schedule_manager_summary=self._bound_schedule_manager_summary(),
            workspace_index=self._workspace_index,
        )

    def _ready_tasks(self, ws: Workspace) -> list[Task]:
        return workspace_dag.ready_tasks(ws, queue=self._queue)

    def _task_blocked(
        self,
        task: Task,
        completed_ids: frozenset[str],
        failed_ids: frozenset[str],
        workspace_id: str,
    ) -> bool:
        return workspace_dag.task_blocked(
            task, completed_ids, failed_ids, workspace_id, queue=self._queue
        )
