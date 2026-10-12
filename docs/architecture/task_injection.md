# Mid-flight task injection

**Source:** `alan/injection.py`, `alan/dispatcher.py`, `alan/handover.py`, `alan/workspace.py`

## Purpose

Allows a completed DAG step to hand back a set of new, dependency-linked tasks via its `Handover.injected_tasks` field. The dispatcher weaves those tasks into the live workspace task set so they execute in declared dependency order alongside the original work.

Worker proposes, host disposes: the completed step *emits* `injected_tasks`; the dispatcher *applies* them. No team or worker writes tasks directly. The first real producer is the `decomposer` team (`docs/architecture/decomposer.md`), which emits a terminal handover whose `injected_tasks` carry `dod_clauses` (schema 1.9) and whose dependency edges it derives from per-subtask file overlap.

## Handover payload (schema 1.9)

```yaml
injected_tasks:
  - id: subtask-a          # local label for depends_on resolution within this batch
    team: research
    instructions: "analyse X"
    depends_on: []
  - id: subtask-b
    team: implementation
    instructions: "build Y"
    depends_on: [subtask-a]
```

`InjectedTask.id` is a local label used only to wire `depends_on` within the batch. The dispatcher replaces it with a stable content-hash ID before persisting.

## Content-hash task IDs

Each injected task receives a deterministic `id` of the form `inj-<16 hex chars>`, computed as:

```
sha256(parent_task_id | team | instructions | sorted(resolved_depends_on) | depth)[:16]
```

where `resolved_depends_on` uses the content-hash IDs of upstream tasks in the same batch. This makes applying the same batch a second time (workspace resume/replay) a true no-op — duplicate IDs are detected against `ws.meta.tasks` and skipped without creating duplicates.

## Depth tracking and cap

`Task.injection_depth` tracks how many injection levels below the original workspace task a given task sits:

- Original tasks (created at workspace creation time): `injection_depth = 0`
- Tasks injected by a depth-0 task completion: `injection_depth = 1`
- Tasks injected by a depth-1 task completion: `injection_depth = 2` (maximum)
- Tasks that would reach depth 3 are routed to `InjectionPlan.overflow`

## Overflow surfacing

When tasks cannot be injected (depth cap exceeded, cycle in batch, unknown `depends_on` reference), the entire batch is treated as overflow:

1. WARNING log with the workspace ID and dropped count
2. `TeamEvent("injection_overflow", {"dropped_count": str(n)})` published on the workspace's SSE channel
3. Local IDs of dropped tasks appended to `Meta.injection_overflow` for operator audit

No gate blocks execution and no UI renders overflow — it is operator-visible via logs and SSE.

## Single-writer and serial-execution invariants

Injection is applied inside `_advance_workspace_dag`, which runs exclusively on the dispatcher worker thread that holds the job's `status='running'` row. The queue's per-workspace serial execution guard (`_CLAIM_SQL`) ensures no second worker can claim a job for the same workspace while the first is still running — including during post-completion processing (injection, revisit count bump, router decision). This is what makes injection safe as a single-writer operation.

**Running a single workspace's tasks in parallel would void this safety argument.** The dispatcher concurrency model deliberately prevents intra-workspace parallelism. This must remain disallowed as long as mid-flight injection exists. See [dispatcher_concurrency.md](dispatcher_concurrency.md).

## Call site

```
Dispatcher._run_team
  → Dispatcher._apply_router_decision
    → Dispatcher._handle_decision (kind="done")
      → Dispatcher._advance_workspace_dag
          1. workspace_mod.update_task_status(ws, task_id, COMPLETED)
          2. if handover.injected_tasks:
               self._apply_injection(ws, task_id, handover)
                 → injection.plan_injection(ws, task_id, injected_tasks)
                 → workspace_mod.inject_tasks(ws, plan.to_add, overflow_ids)
          3. self._enqueue_ready(ws)
          4. self._maybe_evaluate_requirements(ws, handover)
          5. workspace_mod.transition(ws, terminal_status)
```

Injected tasks are persisted to `meta.tasks` before `_enqueue_ready` runs, so the strict-done gate (`_workspace_terminal_status`) counts them on every path to termination.
