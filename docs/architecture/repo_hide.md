# Reversible repository hide (backend: persistence + quiesce)

This doc covers the backend substrate for hiding a repository: the two persisted
`EngineConfig` fields, the forced quiesce sequence (`alan/repo_quiesce.py`), and the
HTTP surface (`alan/routes/engines_visibility.py`). It does not cover the frontend
surfaces (main-view removal, the "hidden repositories" affordance, chooser/palette/
inbox filtering) — those are separate, later slices of the same build.

## Persisted fields (`alan/engine_config.py`)

`EngineConfig` carries two additive fields, written through the existing
`engine_registry_store` (no new store, no new `ALAN_*` env var):

| Field | Type | Purpose |
|---|---|---|
| `hidden` | `bool` | Whether the repo is currently parked. Default `False`. |
| `hidden_restore_posture` | `Posture \| None` | The posture the repo had immediately before it was hidden, so unhide can restore it exactly. Default `None`. |

### `hide_and_persist(repo) -> EngineConfig`

One atomic `model_copy` under `_registry_lock`, setting `hidden=True`,
`hidden_restore_posture=<current posture>`, and `posture=Posture.observe` together in
the same write. A reader can never observe the posture floored without the restore
value remembered, or vice versa. Idempotent: if the config is already hidden, this is
a no-op that returns the current config unchanged — a second call must never re-stamp
`hidden_restore_posture` from the already-floored `observe`, which would permanently
lose the real prior level. Raises `EngineNotFoundError` for an unregistered repo.

### `unhide_and_persist(repo) -> EngineConfig`

The exact inverse, under the same lock: `hidden=False`, `posture=<hidden_restore_posture>`,
`hidden_restore_posture=None`, in one write. Falls back to `Posture.observe` when the
remembered posture is `None` (a config hidden before this field existed, or hand-edited
state) rather than raising or leaving posture unset. Idempotent when not hidden — returns
the current config unchanged. Raises `EngineNotFoundError` for an unregistered repo.

Both functions persist through `engine_registry_store.write_engine`, so the hidden flag
and the remembered posture survive a service restart exactly like every other
`EngineConfig` field — `load_persisted_engines()` reloads them unchanged.

Both functions also append a `posture_audit.jsonl` line (`engine_registry_store.
append_posture_audit`, source `"hide"`/`"unhide"`) whenever the write actually changes
`posture` — the same one-write-under-the-lock guarantee `set_posture_and_persist` gives
(see `engine_config.md`'s "Posture dial and live-floor semantics"), just with a
different `source` value than the `POST .../posture` route. No audit line is written
when the value doesn't change: hiding a repo already at `observe`, or unhiding a repo
whose remembered posture is `observe` — matching `set_posture_and_persist`'s own
same-target no-op. `engine_config.md` documents this as the one other posture-mutating
path besides the dedicated posture route.

Flooring posture to `observe` is what stops recurring background automation
(dependency sweep self-reschedule, triage, deploy-verify, the maintenance loop) from
admitting new work for the repo: `admitted_teams` (see `engine_config.md`) withholds
every team at `observe`, and `live_floored_posture` re-reads the registry fresh on
every enforcement point, so the floor takes effect for the very next admission check —
no re-dispatch needed. This module adds no new admission logic; it reuses the existing
posture-gate mechanism.

## Forced quiesce sequence (`alan/repo_quiesce.py`)

`quiesce_repo(repo, *, dispatcher, queue, workspaces_root, build_chat_root,
build_chat_locks, explore_chat_root, explore_chat_locks) -> QuiesceReport`
composes existing primitives only — it adds no lines to `alan/dispatcher.py` (pinned
shrink-only) and sits directly below `routes` in the import-linter layers list (it may
be imported by `routes`/`server`, and may import everything below it: `dispatcher`,
`queue`, `workspace`, `slot_pool`, `build_chat`/`explore_chat`/
`_chat_core`, `engine_config`).

The order is forced and does not run concurrently across its steps:

1. **Flip hidden + floor posture.** `hide_and_persist(repo)` — see above. This runs
   first so nothing new can be admitted for the repo from this instant on, before any
   attempt is made to stop what is already running.
2. **Interrupt running workers.** For every non-terminal workspace referencing the
   repo (via `Meta.engine_key` or a pinned `Meta.engine_snapshot.config.repo`),
   `Dispatcher.interrupt_task(ws.task_id)` is called. This reaches both an
   in-process-attached worker (`ProcRegistry`) and a re-attached detached
   journal-mode worker via `ProcRegistry.terminate`'s `_terminate_via_records`
   fallback (`worker_records.py`) — the same mechanism the user-facing Stop button
   uses, with no special-casing for detached mode.
3. **Pause queued and future-eligible jobs.** `queue.pause_task(ws.task_id)` is then
   called for every one of the same workspaces, regardless of whether step 2
   succeeded. A job's `eligible_at` (used for delayed retries/reschedules) does not
   exempt it from a paused workspace's claim exclusion (see `queue_and_dispatcher.md`),
   so a job scheduled to wake up days into the park never fires.
4. **Cancel bound chats.** For each of the two globally-listable chat families —
   `build_chat` and `explore_chat`, both backed by `_chat_core.ChatCoreConfig` — every
   session whose `engine_key` equals the repo's slug is looked up via
   `_chat_core.list_sessions` and cancelled via the family's `ChatSessionLocks.cancel`.
   Only a session with an active in-flight turn counts as cancelled
   (`cancel` returns `False`, not an error, for an idle session) — `chats_cancelled` on
   the report sums `True` returns across both families. (A third, per-workspace-rooted
   chat family was cancelled here too until the per-workspace discussion feature was
   removed — see `docs/architecture/chat_shared_mechanics.md`'s removal record;
   `chats_cancelled` stays a single summed `int` with no per-family breakdown, so this
   step's removal needed no report-shape change.)
5. **Dispose the warm pool, last.** `Dispatcher.dispose_pool(slug)` — which calls
   `SlotPool.begin_dispose()` internally — runs only after every lease-holding worker
   has had a chance to stop. `begin_dispose` raises `PoolBusyError` while any lease is
   still held, which is exactly why this step cannot run earlier: a worker still
   running against a leased slot would make disposal either block forever or race a
   live clone out from under it. A missing pool (no dispatcher, or the repo was never
   registered with one) is not an error — disposal is simply skipped.

### Residue rule

A workspace counts as **residue** — named in the report as still alive — only when,
after the interrupt attempt, it still has a `RUNNING` job:

```python
interrupted = dispatcher.interrupt_task(ws.task_id)  # or False if no dispatcher
if interrupted:
    # not residue, regardless of anything else
    ...
else:
    still_running = queue.list_jobs_for_workspace(ws.task_id, status=RUNNING)
    # residue iff still_running is non-empty
```

A bare `False` from `interrupt_task` on an **idle** workspace (nothing was running to
begin with — the common case) is never reported as a survivor. This is deliberate:
`interrupt_task` returns `False` both when there was nothing to interrupt and when
termination itself failed, and only the second case is a real survivor.

Pool disposal failure is reported separately (`pool_disposed` / `pool_residue`), not
folded into `residue_workspace_ids` — the two failure classes name different things
(a still-running worker vs. a pool that could not be torn down) and can occur
independently (e.g. a lease held by something other than one of this repo's own
non-terminal workspaces).

### `QuiesceReport`

```python
class QuiesceReport(BaseModel):
    slug: str
    residue_workspace_ids: list[str]
    workers_interrupted: int
    chats_cancelled: int
    pool_disposed: bool
    pool_residue: str | None
```

Never raises for a partial failure — every step that can fail (`PoolBusyError`,
`PoolDisposeIncompleteError`) is caught inside `quiesce_repo` and folded into the
report instead of propagating. This is what lets the HTTP route return 200 even when
something refused to stop: the hide still takes effect (posture is floored, no new
work can start), and the report names exactly what is still alive.

## HTTP surface (`alan/routes/engines_visibility.py`)

Mounted from `alan/server.py` alongside (not merged into) `alan/routes/engines.py`,
and listed in the "Routes siblings are independent" import-linter contract like every
other route module.

- **`POST /api/engines/{owner}/{repo}/hide`** — control-token gated. Returns
  `QuiesceReport` with **200** even when residue is non-empty or the pool refused to
  dispose (see above). Raises only:
  - **404** if the repo is not registered.
  - **409** if the repo is the current default engine (`get_registered()`) — checked
    **before** `quiesce_repo` is called, so a 409 mutates no state at all, not even the
    hidden flag.
- **`POST /api/engines/{owner}/{repo}/unhide`** — control-token gated. Returns the
  restored `EngineConfig`. Raises only **404** if the repo is not registered.
  Idempotent (calling it on a repo that isn't hidden is a no-op that returns the
  config unchanged). When the repo actually was hidden and a dispatcher is running,
  the route rebuilds the warm pool via `Dispatcher.register_pool` — the one exception
  to "nothing about hide is destructive" is that the pool is genuinely gone after step
  5 above, so restoring the repo to a usable state requires rebuilding it. The pool
  rebuild only fires when a real hidden → visible transition happened, never on the
  no-op path, so a stray `unhide` call can't blow away a perfectly good running pool.

Neither route resumes any paused work, deletes anything, or touches `delete_engine`
(unchanged, still unexposed). Restarting stopped work is exclusively the existing
per-workspace resume, triggered individually by a human once the repo is visible
again.
