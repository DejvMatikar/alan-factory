# Workspace deletion: the two failure shapes

The delete-a-stopped-workspace E2E scenario (`alan/web/e2e/stop-archive-delete.spec.ts`) failed
repeatedly under load. Two independent defects were confirmed with the evidence below — the
"server-side teardown is slow" hypothesis recorded on the original issue is **not** one of them;
nothing in either trace involves waiting on work that is legitimately still finishing on schedule.

## Shape A: delete reports success, a later read still finds the workspace

**Mechanism: a resurrection race between `workspace.delete()`'s `rmtree` and file writers that
blindly `mkdir(parents=True, exist_ok=True)` before writing.**

`DELETE /api/workspaces/{id}` (pre-fix, `alan/routes/workspaces.py`) guarded on
`queue.has_active_work(task_id)` (`alan/queue.py:231`):

```
_HAS_ACTIVE_WORK_SQL = (
    "SELECT 1 FROM jobs WHERE workspace_id = ? AND status IN (?, ?) "
    "AND workspace_id NOT IN (SELECT task_id FROM paused_tasks) LIMIT 1"
)
```

This excludes **any** workspace present in `paused_tasks` — regardless of whether the job row
backing it has actually left `RUNNING`. `Dispatcher.interrupt_task` (`alan/dispatcher.py:360`)
inserts that `paused_tasks` row (`alan/dispatcher.py:367`, `queue.pause_task`) *before* it even
attempts to terminate the worker's subprocess. So the instant a user clicks Stop, `has_active_work`
can already read "no active work," independent of whether the worker thread executing that job has
actually stopped writing.

If `DELETE` proceeds during that window, `workspace_mod.delete()`'s `shutil.rmtree` (`alan/workspace.py:734-741`,
pre-fix) can run concurrently with the still-executing worker thread's post-interrupt cleanup. Two
concrete call chains land there:

- The normal user-pause path: `Dispatcher._pause_job` (`alan/dispatcher.py:486`) calls
  `workflow_interrupt.interrupt_workflow_on_disk`, which calls `save_workflow` on a mutation
  (`alan/workflow.py:310-337`).
- The stranded-job fallback, reached when `_requeue_for_pause`'s (`alan/_dispatcher_usage_limit.py:94`)
  underlying `requeue_for_resume`/`requeue_pending` UPDATE can't find the job row it expects (e.g. a
  concurrent `delete_jobs_by_workspace` already ran): `_recover_stranded` → `_fail_workspace`
  (`alan/_dispatcher_usage_limit.py:62-78`) → `workspace_mod.transition`.

Five `alan/workflow.py` writers call `.mkdir(parents=True, exist_ok=True)` unconditionally before
writing, with no check that the workspace directory they are writing into still exists:

| Writer | Line (pre-fix) |
|---|---|
| `create_invocation_dir` | `alan/workflow.py:494` |
| `save_node_result` | `alan/workflow.py:564` |
| `save_node_config` | `alan/workflow.py:689` |
| `save_invocation_report` | `alan/workflow.py:727` |
| `_write_yaml` (used by `save_workflow`) | `alan/workflow.py:922` |

Any one of these, called from the cleanup paths above *after* `rmtree` has already removed
`ws.path`, recreates the directory tree. `alan/workspace.py`'s own `_write_json` (`alan/workspace.py:809`,
used by `_write_meta`) has **no** `mkdir` of its own — pre-fix, it would fail cleanly with
`FileNotFoundError` once the directory is gone. But once a workflow.py writer has *just* recreated
that directory in the same race, a subsequent `_write_meta` call (e.g. from `_fail_workspace`'s
`workspace_mod.transition`) succeeds — writing `meta.json` back and resurrecting the workspace
a later `GET /api/workspaces/{id}` will find present again.

This is confirmed structurally (every mkdir call site above was read directly from the source and
each one runs with no existence check), not inferred from a stack trace we don't have — which is
also why the captured server log for the failing run needs to be attributable to the exact worker
that ran the failing scenario (see "Artifact gaps" below); today it isn't, so this chain could not
be confirmed from a live failure's own logs.

## Shape B: delete never succeeds

**Mechanism: `Dispatcher.interrupt_task` can legitimately find nothing to signal for a job the
queue already reports as RUNNING, and that failure is by design — not a bug to patch in
`interrupt_task` itself.**

The frontend's Stop button visibility gates purely on `has_running_job`
(`alan/web/src/features/workspace/pages/Workspace.tsx:878`, `stopResumeModeFor`), which the backend derives from
`queue.list_jobs_for_workspace(status=RUNNING)` (`alan/routes/workspaces.py`, `get_workspace`). A
job becomes RUNNING the instant `Queue.claim()`'s `UPDATE` matches it (`alan/queue.py:333-351`) —
before the claiming worker thread has done anything else. That same thread then runs synchronously
through several file writes (workflow-node bookkeeping, node-config persistence, the worker-record
`write_initial`) before it ever reaches the point where it spawns the team's subprocess and
registers the `Popen` with `ProcRegistry` via `ProcStartedHook` (`alan/_dispatcher_base.py:120`,
`145-151`). Under load, this window — RUNNING-and-visible vs. Popen-actually-registered — measurably
widens (more contention for the same dispatcher worker thread, more contention for disk I/O on the
several small writes ahead of the spawn).

If the user (or an E2E scenario reacting the instant the Stop button appears) clicks Stop inside
that window, `ProcRegistry.terminate(job_id)` (`alan/_dispatcher_base.py:135-142`) finds no attached
`Popen` and falls back to `_terminate_via_records(job_id)` (`alan/_dispatcher_base.py:154-169`). That
fallback is **structurally unable** to rescue a job the *current* service run itself just spawned:
`worker_records.probe()`'s same-service-run rule (`alan/worker_records.py:291-292`, `313-314`)
unconditionally classifies any record whose `(service_pid, service_started_at)` matches the running
service as `DEAD` — by design, documented in `docs/architecture/worker_records.md` ("the rule that
current-run records are never LIVE is load-bearing"), so the boot-time reaper never mistakes the
fleet's own live workers for a restart survivor. It is not, and cannot be, weakened to also rescue
this narrower same-run "not registered yet" window without reopening that hazard. So `terminate()`
returns `False`.

`Dispatcher.interrupt_task` (`alan/dispatcher.py:360-373`) then takes its revert branch
(`alan/dispatcher.py:370-372`): it discards the pause marker and calls `queue.unpause_task`, undoing
the one thing that had excluded the workspace from `has_active_work`. **This branch is deliberate,
existing, tested behaviour** — `tests/test_dispatcher_stop.py::test_interrupt_task_rolls_back_pause_when_proc_not_registered`
asserts exactly this outcome, with a docstring naming the same two triggers ("team finished between
list_jobs and terminate, or team type that doesn't spawn claude") independently confirming the shape
of the race. The job's row is still, genuinely, `RUNNING`. `DELETE /api/workspaces/{id}`'s
pre-fix `has_active_work` guard correctly, but *unconditionally and unboundedly*, returns 409 for as
long as that holds — with no retry, no settle window, and a shared, non-actionable
`_WORKSPACE_ACTIVE_JOBS_DETAIL` message indistinguishable from a workspace that is genuinely,
permanently busy. From the UI's perspective this is indistinguishable from "never succeeds": the
page never leaves the workspace route, matching the observed failure exactly.

## Why the other stated candidates don't explain the reachable failure

- **The naive "queued work has not cleared yet" story** (delete blocked because `has_active_work`
  is too strict) is not reachable: `_HAS_ACTIVE_WORK_SQL` already excludes `paused_tasks` rows, and
  `interrupt_task` inserts that row *before* attempting to terminate anything (`alan/dispatcher.py:367-368`).
  If anything, this makes the guard too *loose*, not too strict — which is Shape A's enabling
  condition, not Shape B's.
- **B2** (`_pause_job` → `_requeue_for_pause` racing a concurrent `delete_jobs_by_workspace`) is a
  real, reachable sub-race, but it produces Shape A (resurrection via the mkdir-then-write chain
  above), not "delete never succeeds" — the job row being gone is exactly what routes execution into
  the stranded-job/`_fail_workspace` cleanup path that recreates `meta.json`.
- **B3 as literally scoped** — `ProcRegistry` failing to terminate a *reattached detached* worker
  that `alan/teams/_node_attach.py:157`'s `attach()` owns via a `PidHandle` — **does not reach the
  E2E environment**. `ALAN_DETACHED_WORKERS` is pinned to `"off"` for every E2E server
  (`alan/web/e2e/fixtures/server-env.ts:121`, with the comment "so an e2e run can never pick up a
  detached/journal-mode worker by accident"). With the dial off, `resolve_detached_mode()`
  (`alan/detached_mode.py`) never resolves to journal-stream mode, no `pgid`-bearing worker record is
  ever written (`alan/teams/_worker_record_hook.py`'s `_stamp_detached_if_opened` only fires when a
  journal was opened), and `attach()` never has a survivor record to find. B3 remains a real,
  separately-tracked production gap (reachable only across an actual service restart with detached
  workers on — see "Known related gap" below), but it is not what reproduces in the E2E stress
  harness. The *code* it points at (`_terminate_via_records`'s same-run exclusion) is the same
  code implicated in Shape B above — just triggered by the ordinary claim-to-spawn window, not by a
  restart.

No production code in this change is justified by the naive "queued work hasn't cleared" story or
by the literal B3 (detached-reattach) story — both are ruled out above with file:line evidence, not
assumed away.

## The fix

Both shapes share one root enabler: `DELETE` decides "safe to remove" from a single, immediately-stale
snapshot (`has_active_work`), then performs an unguarded `rmtree` with no confirmation step and no
serialization against the in-flight worker thread that (by design) can still be writing for a short
window after that snapshot was taken. `alan/_dispatcher_retire.py` replaces that snapshot-and-go
sequence with:

1. Acquire the existing per-workspace lock (`Dispatcher._get_workspace_lock`, `alan/_dispatcher_base.py:369`)
   for the duration of the retire — serializing delete against the other dispatcher code paths that
   already take this same lock to mutate workspace state (`_route_after_handover`,
   `_fail_recoverable_subtask`, `_observe_task_failure`).
2. `_settle_for_delete`: a *bounded* poll of `queue.list_jobs_for_workspace(status=RUNNING)` —
   deliberately bypassing the `paused_tasks` exclusion baked into `has_active_work`, since that
   exclusion is exactly what lets delete race ahead of a genuinely-still-running job. If the RUNNING
   row hasn't cleared within the bound, return a blocked result immediately — never hang the request.
3. Only once settled, apply the *existing* `has_active_work` check for genuinely active/queued
   (non-paused) work, preserving today's behaviour of refusing to silently drop real queued work.
4. Delete the job rows and the workspace tree, then **confirm** via `workspace_mod.exists()` under
   the same lock before reporting success. `204` is returned only when that confirmation passes;
   otherwise the endpoint returns `409` with a new, dedicated `_WORKSPACE_DELETE_BLOCKED_DETAIL`
   (kept separate from `_WORKSPACE_ACTIVE_JOBS_DETAIL` so `archive_workspace`/`unarchive_workspace`
   keep their own accurate message).

As belt-and-suspenders for writers that run *outside* the lock's reach (a detached worker the lock
cannot signal, or any writer racing the narrow window described above), the five `alan/workflow.py`
writers are made non-creating: each now checks that `ws.path` still exists before writing, raising
`WorkflowError` instead of resurrecting a deleted workspace tree — following the same
check-before-write precedent `interrupt_workflow_on_disk` already applies via `load_workflow`.
`workspace.exists()` gives every caller (including the settle/confirm step above) a cheap,
non-raising existence check, and `workspace.delete()` now maps a racing `OSError` (e.g. a writer
recreating a file mid-`rmtree`) to `WorkspaceError` instead of surfacing a bare 500.

Each writer's `ws.path.is_dir()` check is a **TOCTOU** check, not a lock: a delete can still land
between the check and the write. `create_invocation_dir` is therefore hardened on both sides of that
window — it creates its `invocations/` base **without** `parents=True` (so a delete landing after
the check can never re-create `ws.path`), and it wraps the base and slot `mkdir`s so a racing
deletion surfaces as `WorkflowError` rather than escaping as a bare `FileNotFoundError`. Without
this, `test_workspace_delete_resurrection.py::test_racing_writer_thread_cannot_resurrect_a_deleted_workspace`
fails intermittently under `-n 6` (~1 run in 4 on a loaded box) with `FileNotFoundError` raised
inside the straggler thread — the guard narrowed the window but did not close it.

**Residual, not closed here:** the other four writers still call
`path.parent.mkdir(parents=True, exist_ok=True)` after their own `is_dir()` check, so a delete
landing in that same window re-creates the tree. Closing it the same way is a follow-up; the lock
plus the delete-confirm step remains the primary defence, and no failing scenario currently reaches
that window.

## Known related gap: B3 in production

B3 (a reattached detached worker `ProcRegistry` cannot terminate) is real but out of this change's
reach: it requires `ALAN_DETACHED_WORKERS=on` (production's default when a slot pool is configured)
*and* an actual service restart landing mid-invocation. Investigation here did not find a case where
fixing it would require reverting #1081/#1084 (durable worker records / detached-worker restart
attach) — `_terminate_via_records` does correctly identify and `terminate_group` a genuinely
reattached survivor's process group once `probe()` confirms it belongs to a *prior* service run (see
`docs/architecture/worker_records.md`). The gap is narrower: today's E2E suite cannot exercise this
path at all (the dial is pinned off everywhere it runs), so a regression in the reattach-then-Stop
sequence would ship undetected. That gap is not fixed here — no reachable evidence connects it to
the failing scenario, and Phase 0 forbids production code against an unconfirmed cause. Surfaced for
the PR body per the dispatch instructions.

## Artifact gaps for future failures

Today's captured server-side log for a failing run is truncated before the relevant request, and it
was not confirmed that the captured log belongs to the worker that actually ran the failing scenario
rather than a fixed one. Neither is fixed by this change (out of the named file scope for this task);
recorded here so a follow-up can close it before the next occurrence of either shape relies on
diagnosing from artifacts alone.
