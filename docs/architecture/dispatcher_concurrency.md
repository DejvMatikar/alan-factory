# Dispatcher concurrency model

**Status:** accepted  
**Source:** `alan/dispatcher.py`, `alan/_dispatcher_base.py`, `alan/_dispatcher_loop.py`,
`alan/_dispatcher_reconcile.py`, `alan/_dispatcher_selfheal.py`,
`alan/_dispatcher_usage_limit.py`, `alan/workspace_dag.py`, `alan/queue.py`, `alan/server.py`

## Module map (H5.2)

`Dispatcher` (`alan/dispatcher.py`) is assembled from mixins rather than being one class body:

```
class Dispatcher(
    _loop.LoopMixin,
    _reconcile.ReconcileMixin,
    _selfheal.SelfHealMixin,
    _usage_limit.UsageLimitMixin,
    _base.BaseMixin,
):
```

Each mixin subclasses `_dispatcher_base.DispatcherState`, an annotation-only class that
declares every instance attribute (`self._queue`, `self._proc_registry`,
`self._workspace_locks`, …) plus a handful of abstract method stubs for the few calls one
seam makes into another seam or into `Dispatcher` itself (e.g. `BaseMixin._fail_job` calls
`self._observe_task_failure(...)`, which is declared on `DispatcherState` but only
concretely implemented on `Dispatcher` in `dispatcher.py`). The stubs are never executed —
Python resolves `self.foo()` against `type(self).__mro__` at runtime, so a real
`Dispatcher` instance always finds the concrete implementation, wherever it lives, before
falling through to a stub.

**This is the load-bearing invariant for the [tool.importlinter] layers contract**:
`self.`-dispatched calls (`self._advance_workspace_dag(...)`, `self._maybe_self_heal(...)`)
create no import edge between mixins — they resolve purely through the MRO, so
`_dispatcher_reconcile.py` never needs to import `_dispatcher_selfheal.py` just because a
*method on Dispatcher* happens to call into it. Only **module-level** references (a
function calling another module's top-level function or class, e.g.
`_dispatcher_selfheal._probe_checkout_for(ws)` from `_dispatcher_reconcile.py`) are
layer-constrained, and those are always reached module-qualified (`import alan._x as _x;
_x.thing(...)`, never `from alan._x import thing`) so that `tests/test_dispatcher_*.py`'s
module-scope monkeypatches keep intercepting after the move (see
`tests/test_transport_canary.py`'s `_dispatcher_*` rows).

Seam ownership:

- **`alan/_dispatcher_base.py`** (lowest layer) — `DispatcherState`, `BaseMixin`
  (`_get_workspace_lock`, `_fail_job`, `_publish`, `_emitter`), `_NonReentrantLock`,
  `ProcRegistry`/`_terminate_proc`, `_schedule_manager_summary`,
  `_maybe_trigger_investigator`, `enqueue_with_pending_workflow`, the engine-config
  resolvers (`governing_engine_config`, `_governing_engine_config`, `_stamp_snapshot`,
  `forge_target_for_ws`), `_audit_dir`, `OutOfBandCompletionError`. Everything here is a
  leaf shared by two or more of the seams below, or by `dispatcher.py` itself.
- **`alan/_dispatcher_usage_limit.py`** — `UsageLimitMixin` (`_handle_server_error`,
  `_handle_usage_limit`) plus `_positive_float_env` and the server-error/usage-limit
  constants.
- **`alan/_dispatcher_selfheal.py`** — `SelfHealMixin` (`_self_heal_pr_number`,
  `_run_self_heal_scenarios`, `_maybe_self_heal`, `_record_self_heal_audit`),
  `_MissingParentHandoverError`, and the DoD/OOB scenario-probe cluster
  (`_probe_checkout_for`, `_run_scenarios`, `_needs_oob_scenario_probe`,
  `_validate_oob_completion`, …).
- **`alan/_dispatcher_reconcile.py`** — `ReconcileMixin` (`_advance_workspace_dag`,
  `_apply_injection`, `_maybe_evaluate_requirements`, `complete_out_of_band_task`,
  `_maybe_surface_stall`, `_maybe_recover_frozen_chain`, `_ready_tasks`, `_task_blocked`,
  `_dag_has_active_work`, `_enqueue_ready`), the sibling-manifest helpers
  (`_normalized_files`, `_is_sibling`, `_sibling_manifests`), `_propagate_and_cancel`, and
  its own `_propagate_task_failure`/`_stamp_evidence`/`_workspace_terminal_status` aliases
  of the `workspace_dag` functions (a second copy of the aliases `dispatcher.py` used to
  carry — reconcile is below dispatcher in the layer order, so it cannot reach dispatcher's
  copy and needs one of its own).
- **`alan/_dispatcher_loop.py`** — `LoopMixin` (`pubsub`/`live_worker_count`/
  `configured_worker_count` properties, `run_once`, `_loop`, `_publish_worker_state`,
  `_maybe_republish_worker_liveness`, `_on_worker_exit`, `_safe_fail_job`, `_execute`,
  `register_pool`, `_warm_pool`, `dispose_pool`, `pool_states`, `_load_prior`),
  `_stop_failure_message`, `_resolve_project_doc_path`, `_DEFAULT_POLL_INTERVAL`,
  `_WORKER_LIVENESS_REPUBLISH_INTERVAL_S`. `_publish_worker_state` writes a
  `worker_liveness.DispatchLoopReading` to `queue_meta` (key
  `dispatcher_worker_state_v2`) under `_alive_lock` — the same lock that guards
  `_alive_worker_count` — and, inside that same critical section, deletes the key
  instead of writing a `live_loops=0` reading whenever `self._stop.is_set()` is
  already true, so a concurrent periodic republish can never resurrect a stale
  reading after a clean stop's delete. `_maybe_republish_worker_liveness` (called
  from `_loop` right alongside `_maybe_sweep_deploy_gates`) refreshes the reading's
  `observed_at` every 60s so a healthy, otherwise-quiet dispatch loop never ages
  past `worker_liveness.HEARTBEAT_TTL_S`. See [queue_and_dispatcher.md](queue_and_dispatcher.md#worker-pool-heartbeat-alanworker_livenesspy)
  and [health.md](health.md)'s `worker_pool` section for the full read-side contract.
- **`alan/dispatcher.py`** (top layer, unchanged position) — the `Dispatcher` class shell
  (`__init__`, `start`, `stop`, `_join_workers`), the routing/merge-gate cluster
  (`_route_after_handover`, `_enforce_pr_merge_gate`, `_pr_merge_remediation`,
  `_apply_router_decision`, `_handle_decision`, …), the routing-entangled failure glue
  (`_fail_genuine`, `_fail_recoverable_subtask`, `_handle_team_exception`,
  `_observe_task_failure`, `replay_handover_routing`), `_fail_workspace`,
  `DispatcherError`/`NoPoolForEngineError`/`OutOfBandCompletionError`'s exception-hierarchy
  neighbors `_pool_for_workspace`/`_read_workspace_and_pool` (see below), and
  `pinned_engine_config` (kept here rather than moved to base because
  `tests/test_posture_routing.py` imports it directly from `alan.dispatcher` and is out of
  this item's file scope).

**Deliberate deviation from a literal reading of the seam line-ranges**: `start`, `stop`,
`_join_workers`, `_pool_for_workspace`, and `_read_workspace_and_pool` stay on `Dispatcher`
in `dispatcher.py` rather than moving into `LoopMixin`, because `NoPoolForEngineError`
subclasses `DispatcherError`, and `DispatcherError` has external consumers
(`alan/routes/infra.py`, `tests/test_dispatcher_runtime.py`, `tests/test_server.py`) that
require it to keep living in `alan.dispatcher` — a class one layer down cannot subclass an
exception defined one layer up without creating exactly the import cycle this item warns
against. `LoopMixin._execute` still calls `self._read_workspace_and_pool(...)`; that
self-dispatched call resolves via MRO with no import edge, so the constraint costs nothing
functionally, only slightly less code physically living in the new module.

## Context

The original `Dispatcher` ran one background thread that claimed one job at a time. With multiple distinct workspaces active simultaneously, throughput was bounded by the slowest in-flight `claude` subprocess — each workspace's job had to wait for every preceding job to finish before being claimed.

## Decision

**N independent worker threads each running the existing `_loop`, plus a per-workspace intra-workspace cap in `_CLAIM_SQL` and a per-workspace `_NonReentrantLock` protecting metadata mutations.**

No new abstractions are introduced: each worker calls the same `run_once()` → `_execute()` path the single-thread Dispatcher has always used.

### Intra-workspace fan-out cap

`_CLAIM_SQL` enforces a cap on simultaneously running jobs for the same workspace. The value is selected from actual slot-pool presence in `alan.routes.infra._intra_workspace_cap`: `INTRA_WORKSPACE_CAP = 1` when no pool is active (CI sets `ALAN_SLOT_POOL_ROOT=""`, as does any deployment without the pool), and `INTRA_WORKSPACE_CAP_POOLED = 2` when a `SlotPool` was created — so concurrent same-workspace jobs only ever run in isolated clones:

```sql
AND (
    workspace_id = ''
    OR (SELECT COUNT(*) FROM jobs j2
        WHERE j2.workspace_id = jobs.workspace_id
        AND j2.status = 'running'
        AND j2.workspace_id != '') < ?
)
```

The `< ?` placeholder is bound to the queue's `intra_workspace_cap`. `alan/routes/infra.py` selects that value via `_intra_workspace_cap(slot_pool)` at server startup — `INTRA_WORKSPACE_CAP` (1) when no pool is active, `INTRA_WORKSPACE_CAP_POOLED` (2) when one is. The `Queue` constructor default (`intra_workspace_cap=1`) preserves the old one-job-per-workspace behaviour for tests.

**Legacy single-task workspaces** (where `workspace_id == task_id`, no task list) remain serial regardless of the cap because the `task_id NOT IN (SELECT task_id FROM jobs WHERE status = 'running')` filter still prevents two jobs with the same `task_id` from running concurrently.

**No serialization guarantee:** the cap allows up to `INTRA_WORKSPACE_CAP` concurrent jobs. Residual file-overlap conflicts between tasks that were incorrectly declared disjoint are detected (not prevented) and resolved through the existing `implementation_fix` loop. Dependency edges set at enqueue time are the primary guard; the cap is a throughput control, not a correctness backstop.

### Per-workspace metadata lock

`Dispatcher._workspace_locks` is a `dict[str, _NonReentrantLock]` (both the attribute and the `_NonReentrantLock` type now declared on `_dispatcher_base.DispatcherState`), lazily populated by `_get_workspace_lock(workspace_id)` (`alan/_dispatcher_base.py::BaseMixin`) under `_workspace_locks_meta` (a separate meta-lock). `_NonReentrantLock` (`alan/_dispatcher_base.py`) wraps a `threading.Lock` with a `threading.local()` flag and raises `RuntimeError` instead of deadlocking when the same thread tries to acquire it twice (self-deadlock guard).

Every metadata mutation on the post-handover routing path holds this lock:

- `_route_after_handover` (`Dispatcher`, `alan/dispatcher.py`) — computes router decision and PR-merge readiness OUTSIDE the lock (`select_next_team` is pure; `_enforce_pr_merge_gate` may call `gh pr view` — slow network; `_maybe_self_heal` may run DoD scenarios — pytest subprocesses); then acquires lock → re-reads workspace from disk (`workspace_mod.read`, which never reads from the `_META_CACHE`) → validates any `scenario_rebind.yaml` fence on the handover (`_scenario_rebind.apply_rebinds`, which does no subprocess work of its own — see [acceptance_gate.md](acceptance_gate.md)'s "Fix-role scenario rebind" section) and folds an accepted rebind into the decision (`_scenario_rebind.route_after_rebind`) — both as the *first* statements inside the lock, deliberately ahead of the revisit-count block below, so an accepted rebind still decays the chain's revisit counter rather than incrementing it — → increments/decays revisit count using that possibly-rewritten decision → calls `_apply_router_decision` (loop guard + dispatch/enqueue) → releases lock → then calls `queue.complete(job.id)` in `finally`. The pre-lock computation is still inside the outer `try/except` so any unexpected exception is caught and surfaces via `_surface_routing_error`. Scenario-rebind validation moved inside the lock (it used to run outside, before the H-series probe was deleted) precisely because it stopped touching a checkout or spawning a subprocess — there is no longer an external-call reason to keep it out, and running it under the same re-read as the revisit-count decision keeps the ordering invariant (accept ⇒ decay, not increment) atomic with the read it depends on.
- `_observe_task_failure` (`Dispatcher`, `alan/dispatcher.py`) — acquires lock → re-reads workspace → propagates failure (via `alan/_dispatcher_reconcile.py::_propagate_and_cancel`, module-qualified) → releases lock.
- `_fail_workspace` (`Dispatcher`, `alan/dispatcher.py`) — acquires the lock, re-reads the workspace, parks it, releases the lock, then schedules the manager summary.

The read is **inside** the lock (not before) so the lock scope wraps the full read→mutate→write seam. A stale workspace snapshot read outside the lock is discarded.

### `meta.json` write semantics

Every `meta.json` write used to be last-writer-wins: a mutator built its update from the `Meta` object the caller happened to be holding (`ws.meta.model_copy(...)`), so a write from a stale copy silently discarded any change made to the on-disk file after that copy was taken. Three call sites held their copy across a long-running or externally-interleaved operation and were observed to erase a workspace note this way — brief refinement's final write, the end-of-workflow requirement check (which can run DoD scenarios for up to `_SELF_HEAL_SCENARIO_BUDGET_S` seconds before it writes), and the park that follows a failed job. The fix below is scoped to exactly those three writers; the general hazard — every other metadata write still copying the caller's stale view — is left to #1439 on purpose.

**Writes that apply to on-disk metadata.** The following `alan/workspace.py` mutators build their update from `_read_meta(ws.path)` (an uncached, fresh-from-disk read) rather than from the caller's `ws.meta`, so a concurrent write — most notably a note — made after the caller took its copy survives: `set_sticky_instructions`, `mark_delivered`, `record_intervention`, `update_requirements`, and `update_assumptions`. Each still returns the fresh `Workspace` its write produced, and callers must thread that returned value onward rather than continuing to use their stale copy — `workspace_dag.advance_workspace_dag` does this before its own terminal `transition` call.

**The failed-job park.** `Dispatcher._fail_workspace` acquires the per-workspace lock, re-reads the workspace with `workspace_mod.read` inside the lock, and parks it (`AWAITING_INPUT`/`ParkReason.CRASH`) from that fresh copy. A vanished workspace (`workspace_mod.WorkspaceError`, e.g. deleted concurrently) is logged and swallowed exactly like the pre-existing `OSError` write-failure case — both are caught by the same `except (OSError, workspace_mod.WorkspaceError):` clause, so a vanished workspace never escapes the failure path as an unhandled exception. The manager summary is scheduled after the lock is released.

**Everything else stays last-writer-wins until #1439**: `transition` called directly, `update_note`, `_surface_routing_error`, `restore_escalated`, `_restore_park_after_failed_dispatch`, `reconciler._fail_workspace` (a distinct function from `Dispatcher._fail_workspace` above, with its own much narrower read-then-write window — see its own module), and the field-level last-writer-wins on `requirements` itself: a concurrent human edit to `requirements` racing one of the fresh-apply writers above is not resolved by this fix.

**Residual non-atomicity.** The read-then-write in each fresh-apply mutator above is not atomic — a second write landing in the gap between the read and the write is still lost — and every writer to a given workspace's `meta.json` shares the same `meta.json.tmp` staging file.

**Critical section scope**: only fast disk I/O (workspace read/write, queue enqueue) runs inside the lock. Slow external calls (`gh pr view` in `_enforce_pr_merge_gate`) run before lock acquisition. This prevents the per-workspace mutex from becoming a global bottleneck when multiple workspaces route concurrently and any of them hits a slow PR readiness check. This invariant is unchanged by the H5.2 split — `_get_workspace_lock` moved to `alan/_dispatcher_base.py::BaseMixin`, `_apply_injection`/`_advance_workspace_dag` moved to `alan/_dispatcher_reconcile.py::ReconcileMixin`, `_route_after_handover`/`_observe_task_failure`/`_fail_workspace` stayed on `Dispatcher` in `alan/dispatcher.py` — but every one of those calls is still `self.`-dispatched, so the lock-acquire/release sequencing and the outside-lock/inside-lock boundary are byte-identical to before the move.

**Injection idempotency:** `workspace_dag.advance_workspace_dag` (called via `Dispatcher._advance_workspace_dag`, now `alan/_dispatcher_reconcile.py::ReconcileMixin`, still under the per-workspace lock — see [queue_and_dispatcher.md](queue_and_dispatcher.md#dag-mechanics-alanworkspace_dagpy)) invokes the `apply_injection` callback (bound to `Dispatcher._apply_injection`, also `ReconcileMixin`), which calls `plan_injection(ws, task_id, injected_tasks)` using a content-hash-based dedup (`_collect_existing_ids`). Because the workspace is re-read fresh inside the lock, the second concurrent caller sees any tasks already injected by the first caller and its `to_add` list is empty. The content hash includes the parent `task_id`, so two different parent tasks injecting logically identical subtasks produce distinct `inj-xxx` IDs — this is correct behaviour (each parent owns its child).

### Thread count

`Dispatcher.__init__` accepts `concurrency: int = 1` (single-thread default preserves unit-test determinism). `server.py` passes `concurrency=_resolve_dispatcher_concurrency()`, which reads `ALAN_DISPATCHER_CONCURRENCY` and defaults to **6**.

### Stop semantics

`stop()` uses a **single monotonic deadline** shared across all N worker threads. Every thread is joined before the `DispatcherError` is raised, preventing orphan threads from silently escaping the cleanup path.

## Concurrency safety analysis

| Area | Verdict |
|---|---|
| `Queue._lock` (RLock) | All `claim()` / `complete()` / `fail()` callers hold `_lock` → atomic under N workers |
| `_route_after_handover` (`Dispatcher`, `alan/dispatcher.py`) metadata mutations | Guarded by per-workspace `threading.Lock` (`_get_workspace_lock`, `alan/_dispatcher_base.py::BaseMixin`); workspace re-read inside lock |
| `_observe_task_failure` (`Dispatcher`, `alan/dispatcher.py`) failure propagation | Guarded by per-workspace `threading.Lock`; workspace re-read inside lock |
| `_fail_workspace` (`Dispatcher`, `alan/dispatcher.py`) | Guarded by per-workspace lock; workspace re-read inside lock; vanished workspace logged like `OSError` |
| `_fail_job` (`alan/_dispatcher_base.py::BaseMixin`) → `queue.fail()` | Runs inside `Queue._lock` → safe |
| Reconciler | Runs in `lifespan` **before** `dispatcher.start()`; no workers alive during the sweep → safe at any N |
| `pr_merge._MERGE_LOCK` (threading.Lock) | Module-level lock serializes concurrent `gh pr merge` calls across all workers; post-lock re-check detects BEHIND race before issuing the subprocess (see `pr_iteration_pipeline.md` §A8) |
| Proactive rebase daemon thread | `_maybe_trigger_proactive_rebase` spawns a `daemon=True` thread after every successful `pr_merge`; the thread calls `update_pr_branch_outcome` on other ACTIVE workspaces; non-fatal, does not hold any lock (see `pr_iteration_pipeline.md` §A9) |

## Per-workspace starvation

When no slot pool is active the cap is `1`, so only one task per workspace runs at a time. Once `ALAN_SLOT_POOL_ROOT` is set in production (now the default in `deploy/ai.alan.plist`) a pool is created at startup and the cap becomes `INTRA_WORKSPACE_CAP_POOLED = 2`: up to 2 tasks in a workspace run concurrently — each in its own isolated clone — while the third and beyond are queued. This is the desired semantic: throughput scales with the number of independent subtasks, but unbounded fan-out would exhaust API rate limits. The cap is tied to the actual `SlotPool` object (not merely the env var) so it can never rise to 2 without isolation in place — e.g. if the repo root is unresolvable, `_create_slot_pool` returns `None` and the cap stays 1.

**Correction (R1.3):** every keyed pool is **not** sized identically to the default pool. `alan.routes.infra._secondary_pool_size(cfg)` sizes a secondary engine's pool at `cfg.pool_size or _DEFAULT_SECONDARY_POOL_SIZE` (2), independent of `ALAN_DISPATCHER_CONCURRENCY` — both at boot (`_create_keyed_slot_pools`) and at runtime (`_build_pool_factory`, backing `Dispatcher.register_pool`). The **global** intra-workspace cap (`INTRA_WORKSPACE_CAP_POOLED = 2`, selected once from the *default* pool's presence — see `_intra_workspace_cap` above) is unaffected by any secondary engine's `pool_size`; nothing in this file's cap-selection logic reads per-engine sizing. `_DEFAULT_SECONDARY_POOL_SIZE` stays at 2 while the default engine's pool tracks `ALAN_DISPATCHER_CONCURRENCY` (6): a secondary engine's pool size bounds how many of *that repo's* jobs run at once across all of its workspaces, and holding a secondary engine to 2 is a deliberate throughput ceiling on that repo, not an oversight — raising it costs one warmed clone of that repo per added slot (~3 GB each for an iOS-preset engine) and would let a single secondary engine claim the whole fleet fan-out. Raise it per-engine via that engine's `pool_size` when a specific repo needs the headroom, rather than moving the shared default. The accepted consequence: a secondary engine whose `pool_size` happens to sit below what its own workspaces could otherwise fan out to (e.g. `pool_size=1` for an engine whose workspace wants `INTRA_WORKSPACE_CAP_POOLED = 2` concurrent tasks) simply **blocks** — `SlotPool._claim` waits on its `Condition` for a slot to free, exactly as it does for ordinary lease contention — it does **not deadlock**, because the queue's worker threads are shared across all engines and a blocked claim on one engine's undersized pool never holds a lock any other engine's dispatch needs. This is accepted as harmless for M scope: the default engine's pool sizing and the global intra-workspace cap are both unchanged by any of this, and a secondary engine choosing a small `pool_size` is opting into that throughput ceiling. Note the two are separate limits and were never equal — the default engine's pool is sized to `ALAN_DISPATCHER_CONCURRENCY` (6 in production), while `INTRA_WORKSPACE_CAP_POOLED` is 2; the pool bounds how many of that engine's jobs run at once, the cap bounds how many of them may belong to one workspace.

## Legacy serial path

A workspace with no task list (`ws.meta.tasks == []`) always runs at most one job at a time — both through the `task_id NOT IN (SELECT task_id FROM jobs WHERE status = running)` clause (since `workspace_id == task_id` for legacy workspaces) and through the workspace lock. No behaviour change from the previous design for this path.

## Recommended N for Mac deployment

Default `ALAN_DISPATCHER_CONCURRENCY=6` for the self-hosted M-series Mac:

- **Parallelism:** 6 simultaneous `claude` subprocesses fit comfortably on an 8-core M-series CPU with no contention.
- **RAM:** each `claude` process peaks at ~200–400 MB; 6 × 400 MB = 2.4 GB peak, well within typical 16–32 GB RAM.
- **API rate limits:** Anthropic's default tier allows ~5–10 concurrent requests; 6 workers leaves headroom for chat turns.
- **Resource exhaustion ceiling:** above N≈8–10, API rate-limit errors start dominating over latency gains. N=6 is a moderate default; operators can raise it via `ALAN_DISPATCHER_CONCURRENCY` once they observe the actual saturation point for their workload.

This host ran at an interim N=3 from 2026-08-17 after the fleet saturated it, and returned to N=6 on 2026-09-01 once the per-worker load caps (`alan/fleet_load.py`) were in place and alan's CI runners had moved off it. The return was made by deleting the `ALAN_DISPATCHER_CONCURRENCY` line from `~/.alan/secrets.env` and bouncing the service, not by changing code — the code default had been 6 throughout. The re-derivation of those caps against N=6 — including why they stay at 2 and what the accepted oversubscription is — lives in [deploy.md](deploy.md)'s "Fleet worker priority and parallelism caps" section; that is the file to read before changing N again.

## Migration from single-thread Dispatcher

No schema changes required. The existing `queue.db` is compatible: `_CLAIM_SQL` gains a subquery but the table layout is unchanged. The startup reconciler already handles any `status='running'` rows left by a restart, so upgrading from a single-thread deployment is a drop-in restart.

## Intra-team stage-group concurrency

Beyond the inter-workspace/inter-team concurrency above, `alan/teams/_static_pipeline.py` supports a second, narrower axis: **concurrent roles inside one team invocation**. This is opt-in per team via the `StaticPipelineTeam.stages: tuple[Stage, ...] | None` field (`Stage = tuple[str, ...]`, a tuple of role names). Unset (the default for every team except the two below) means one singleton stage per role — byte-identical to the old strictly-serial chain. A stage listing more than one role name runs those roles' `claude` subprocesses concurrently via a `ThreadPoolExecutor`; stages themselves still run strictly in order, and each role's `depends_on` is derived as every role name in the *previous* stage (`_depends_on_map`).

Two teams opt in as of R-series latency work:

- **`verification`**: `(("diff_reviewer", "scenario_runner"), ("judge",))` — the judge still receives both outputs unchanged.
- **`implementation_plan`**: `(("architect",), ("backend_analyst",), ("frontend_analyst", "qa_planner"), ("devil_advocate",), ("synthesizer",))` — the synthesizer still receives all four analyst outputs unchanged.

**Trigger and ordering guarantee.** `_run_stage_roles` returns `[(role.name, result), ...]` strictly in the stage's declared role order (`zip(stage_roles, futures, strict=True)`), regardless of which thread finishes first — this is load-bearing: `verification._collect_outputs` indexes `outputs[0]` (diff_reviewer) and `outputs[-1]` (judge) after the loop, and both would silently point at the wrong role under completion-order results. Each role is submitted via `pool.submit(contextvars.copy_context().run, run_role, role)` so per-thread `contextvars.ContextVar`s set by the calling team before the stage loop (verification's `_grounded_cwd_var`, `_scenario_results_var`, `_verification_task_id_var`) are captured and visible inside the worker thread. `ThreadPoolExecutor.__exit__` blocks until every submitted future is done *before* any `.result()` call can raise — a sibling's exception can never orphan the other thread mid-flight.

**Sibling-blindness is accepted, not a bug.** Roles inside the same stage never see each other's actual output — the `outputs` list handed to `build_role_user_text` is only ever the *previous* stage's results, extended after the whole stage completes. Each role's prompt still gets the existing `## COVERAGE` block naming its siblings, so it knows not to duplicate their scope; it just can't read what they wrote. This was verified against the two teams' prompt contracts before parallelizing: `scenario_runner`'s prompt never references `diff_reviewer`'s findings (its evidence is precomputed via `_compute_scenario_results` before either role runs), and `qa_planner` keeps its serial dependency on `backend_analyst` (unchanged) while losing only its dependency on `frontend_analyst`'s prose — an acceptable trade the audit judged non-load-bearing.

**Mutate-graph-and-save-under-lock, spawn-subprocess-outside-lock.** `WorkflowGraph.lock` (a `threading.Lock`, set in both `__init__` and `.from_doc`) wraps every place `_base.py` mutates `graph`'s task list/fields and then calls `save_workflow` — `start_node`+`save_workflow` (via `_start_node_locked`), `resume_node`+`save_workflow`, `complete_node`+`save_workflow`, and the `on_session_id` callback's `set_session_id`+`save_workflow`. It is **never** held around `run_agent_streaming` (the actual subprocess spawn+stream), so the lock is uncontended and near-zero-cost on every team that doesn't use stage groups, and on stage-group teams it only ever serializes the sub-millisecond graph-mutate-and-YAML-write, never the multi-minute subprocess itself.

**Slot/subprocess accounting.** A parallelized stage adds **+1 concurrent `claude` subprocess per parallelized team run** — bounded by the same worker-slot accounting every other concurrent job already uses (`SlotPool` leases, `ALAN_DISPATCHER_CONCURRENCY`), not by any new machine-wide CLI ceiling. `alan._dispatcher_base.ProcRegistry` (moved from `alan/dispatcher.py` by H5.2) maps `job_id -> set[Popen]` (was a single `Popen`) so `interrupt_task`'s SIGTERM/SIGKILL path terminates every attached proc for a stopped job, not just the first one registered.

**First case of two live `claude` processes read-sharing one leased checkout.** Verification's `diff_reviewer` and `scenario_runner` now run concurrently against the *same* grounded checkout path (`_grounded_cwd_var`, leased once per verification run via `leased_with_affinity` — see [slot_pool.md](slot_pool.md)) — previously only one `claude` process ever touched a leased checkout at a time. `scenario_runner`'s scenario evidence is precomputed into `_scenario_results_var` before the stage starts (no filesystem writes during its run), and `diff_reviewer`'s only filesystem-mutating action in that checkout is its best-effort, advisory `npm run capture:screens` (screen-captures directory, non-blocking, never affects the verdict). The accepted worst case is a flaky/missing capture, never a verdict change — narrowing `diff_reviewer`'s Bash tools for the concurrent stage was considered and rejected because it would drop the rendered-screen pass on UI PRs. This is a genuine (small) narrowing of the slot_pool "one worker per clone" intent, recorded here rather than in slot_pool.md because the exception is scoped to this one stage-group, not the pool's general contract.

`scenario_runner` is granted the identical `allowed_tools` set as `diff_reviewer` (`_role_overrides` derives it role-agnostically via `render_allowed_tools`, so both get Bash) but its only Bash usage in the checkout is the scenario subprocess runs, which complete before the concurrent stage starts (`_compute_scenario_results` populates `_scenario_results_var` ahead of time) — it performs no filesystem-mutating action of its own during the concurrent run, so the accepted risk above still reduces to `diff_reviewer`'s `capture:screens` alone.
