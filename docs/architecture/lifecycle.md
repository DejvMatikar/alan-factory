# Lifecycles as transition tables: `alan/lifecycle.py`

**Source:** `alan/lifecycle.py`, `alan/workspace.py`, `alan/queue.py`, `alan/workspace_dag.py`, `alan/park.py`, `tests/lifecycle_gate.py`.

Before this design a status change was spread across eight near-identical `UPDATE jobs SET status` SQL constants in `alan/queue.py`, eight writer methods differing only in which constant they passed, a workspace status writer called from twenty-one places across seventeen files, and `alan/_dispatcher_park_rollback.py` — a module whose only job was letting one transition span a file boundary. Nothing structural stopped a new transition being added somewhere no other part of the system knew about. `alan.lifecycle` is the one readable place a lifecycle now lives: one transition table per lifecycle, one `transition` function, and the vocabulary the tables are written in.

This is a **shape change, not a behaviour change**. Every table row encodes exactly what the code did before it; a behaviour change to any transition is a separate item. The design below is written first because four later roadmap items (X2.14–X2.17) inherit the table shape.

## Two lifecycles, one module

`alan/lifecycle.py` imports nothing from `alan` (`StrEnum`, `Literal`, `dataclass`, `overload`, pydantic only) and sits directly below `queue` in the `[tool.importlinter]` layers list. It owns:

- **Vocabulary**, declared in the module's `__all__`: `Status` (workspace), `JobStatus`, `ParkReason`, `BlockerKind`, `Blocker`. `alan/workspace.py`, `alan/queue.py` and `alan/park.py` import the names they already used from here as explicit identity re-exports (`from alan.lifecycle import Status as Status`), because `mypy --strict` disables implicit re-export and roughly seventy existing importers keep reading `alan.workspace.Status`, `alan.queue.JobStatus`, `alan.park.ParkReason`. Those importers are deliberately not migrated in this change.
- **Tables**: `WORKSPACE_TABLE` and `JOB_TABLE`.
- **The planner**: `transition`, plus `advance_target` (below).

### Table shape

Tables are dicts of frozen dataclass rows, **not** `match` statements. Eight `match` arms exceed lizard CCN 7 and xenon rank A on their own, and data — a row added to a mapping — is what X2.14–X2.17 extend.

- `WORKSPACE_TABLE: Mapping[Status, WorkspaceRow]` — one row per `Status` member. `WorkspaceRow(clears_park_reason, clears_blockers)`; both flags are `True` only for `ACTIVE` and `COMPLETED`, encoding what `_PARK_CLEARING_STATUSES` did before it was deleted. Entering any other status leaves `park_reason`/`blockers` alone unless the caller supplies a value or an explicit `clear_*` flag.
- `JOB_TABLE: Mapping[JobTransition, JobRow]` — eight rows, one per deleted SQL constant. `JobRow` carries `from_status`, `to_status`, the `miss_message` for a guard miss, and a per-column `Effect` (`KEEP`, `NULL`, `SET`, `INCREMENT`) for each of `started_at`, `finished_at`, `error`, `resume_inv_id`, `interruption_count`, `requeue_count`, `eligible_at`, `server_error_retry_count`.

| transition | from → to | started_at | finished_at | error | resume_inv_id | interruption_count | requeue_count | eligible_at | server_error_retry_count |
|---|---|---|---|---|---|---|---|---|---|
| `INTERRUPT` | RUNNING → FAILED | KEEP | SET | SET | KEEP | INCREMENT | KEEP | KEEP | KEEP |
| `INTERRUPT_WITH_RESUME` | RUNNING → FAILED | KEEP | SET | SET | SET | INCREMENT | KEEP | KEEP | KEEP |
| `INTERRUPT_CLEAR_RESUME` | RUNNING → FAILED | KEEP | SET | SET | NULL | INCREMENT | KEEP | KEEP | KEEP |
| `REQUEUE_RESUME` | RUNNING → PENDING | NULL | NULL | NULL | SET | INCREMENT | KEEP | NULL | SET (0) |
| `REQUEUE_PENDING` | RUNNING → PENDING | NULL | NULL | NULL | NULL | INCREMENT | KEEP | NULL | SET (0) |
| `REQUEUE_FAILED_FOR_RESUME` | FAILED → PENDING | NULL | NULL | NULL | KEEP | INCREMENT | INCREMENT | NULL | SET (0) |
| `REQUEUE_FAILED_WITH_RESUME` | FAILED → PENDING | NULL | NULL | NULL | SET | INCREMENT | INCREMENT | SET (may be `None`) | SET (caller value) |
| `REQUEUE_SERVER_ERROR` | RUNNING → PENDING | NULL | NULL | NULL | NULL | KEEP | KEEP | SET | INCREMENT |

The six `RUNNING`-sourced rows (three interrupt variants, three requeue variants) miss with `job {job_id} is not running`; the two `FAILED`-sourced rows with `job {job_id} is not a failed job`.

## Decision 1 — `transition` is a pure planner

`lifecycle` sits below `store`, `roots` and `queue`, so it cannot import `Meta`, `_write_meta` or the queue's SQLite connection: it physically cannot perform a write. `transition` therefore **returns the field updates** and the persistence modules apply them:

- `transition(Status, …) -> dict[str, Any]` — the `model_copy(update=…)` payload for a workspace, carrying `"status"` and whichever of `merge_block_class`/`park_reason`/`blockers` the row and the caller's keywords select. Precedence per field is unchanged: an explicit value wins, then a `clear_*` flag or a clearing row, else the key is omitted. `alan.workspace.transition(ws, status, …)` adds `updated_at`, calls `model_copy`, `_write_meta`, and returns the new `Workspace`.
- `transition(JobTransition, …) -> JobPlan` — the row plus the four caller-supplied values (`error`, `resume_inv_id`, `eligible_at`, `server_error_retry_count`). `Queue._execute_transition` binds the plan into the one statement and stamps `finished_at`.

The acceptance clause is therefore **not** "one `transition()` through which every `Status` write passes". It is: every `Status` write is *derived from* `lifecycle.transition`, persistence stays in `alan/workspace.py` and `alan/queue.py`, and the structural gate below governs the writer sites, not the planner.

Timestamps are stamped by the writer, never the planner: a pure function cannot call `datetime.now()`. `updated_at` (workspace) and `finished_at` (job) are added at the write. The `jobs` table has **no** `updated_at` column.

The public workspace writer was renamed `update_status` → `transition` in the same change, across every caller (including the three `run_in_threadpool(workspace_mod.update_status, …)` attribute references in `alan/routes/workspaces.py`, which a `update_status(` call regex misses). Three characters shorter, so every shrink-only pinned file can only get smaller.

## Decision 2 — guards that skip a transition are named inputs to the planner

Some callers skip or reshape a transition on a predicate that lives above the layer boundary. The case that exists today: a dispatcher hop may advance an `ESCALATED` workspace back to `ACTIVE`, **unless** a subtask is `AWAITING_INPUT`. `has_awaiting_subtask` reads task state (`TaskStatus`, deliberately excluded below) and lives in `alan/workspace_dag.py`; `lifecycle` cannot import it. That layer inversion is exactly why `_dispatcher_park_rollback.py` existed as its own module: the stamp-and-restore pair had to sit above `workspace_dag` to reach the predicate while also writing workspace status.

The pattern every later caller copies: **the predicate becomes a keyword argument of the planner, evaluated by the caller**. `advance_target(current, *, has_block_class, has_awaiting_subtask) -> Status | None` is a pure truth table over its inputs:

| current | has_awaiting_subtask | has_block_class | result |
|---|---|---|---|
| `ESCALATED` | true | — | `None` (skip: no write) |
| `ESCALATED` | false | — | `ACTIVE` |
| any other | — | true | `current` (re-stamp to record the block class) |
| any other | — | false | `None` |

`alan.workspace_dag.stamp_for_advance(ws, block_class)` passes `has_awaiting_subtask=was_escalated and has_awaiting_subtask(ws)`. The short-circuit keeps today's evaluation order: the task-state predicate is still evaluated **only** for `ESCALATED` workspaces. `ParkRollback` (the snapshot of `park_reason`/`blockers`) and `restore_escalated` moved beside it as writer plumbing, and the module `alan/_dispatcher_park_rollback.py` and its layers/allowlist entries are deleted. `stamp_for_advance` now takes `block_class: str | None` rather than a `RouterDecision`, which drops the `alan.router` import from that path entirely.

## Decision 3 — static superset SQL

One statement, defined once in `alan/queue.py`, replaces the eight constants:

```
UPDATE jobs SET status = ?,
  started_at = CASE WHEN ? THEN ? ELSE started_at END,
  finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
  error = CASE WHEN ? THEN ? ELSE error END,
  resume_inv_id = CASE WHEN ? THEN ? ELSE resume_inv_id END,
  interruption_count = interruption_count + ?,
  requeue_count = requeue_count + ?,
  eligible_at = CASE WHEN ? THEN ? ELSE eligible_at END,
  server_error_retry_count = CASE WHEN ? THEN ? ELSE server_error_retry_count + ? END
WHERE id = ? AND status = ? RETURNING *
```

Every `CASE` is fed a `(write?, value)` pair derived from that column's `Effect` (`KEEP` → `(False, None)`, `NULL` → `(True, None)`, `SET` → `(True, value)`); the two counters bind `1` for `INCREMENT` and `0` otherwise. **Parameter cost: 18 bound parameters per transition against 4–7 today**, one prepared-statement cache entry instead of eight, and zero runtime string assembly. All eight predecessors were `WHERE id = ? AND status = ? … RETURNING *`, so the collapse loses nothing.

Rejected: runtime-assembled `SET` clauses. They put string-built SQL on a write path (a Bandit/taint smell), and yield one statement shape per call rather than one.

**The omitted-column trap.** Today's constants *omit* the columns a transition does not touch. Under a superset every column is *written* unless a `CASE … ELSE <col>` restores it, so a single wrong `KEEP`/`NULL` flag silently corrupts a column the old SQL never reached — `interruption_count`, `requeue_count`, `eligible_at` and `server_error_retry_count` are the ones most at risk — and it is invisible to a test that only asserts `status`. `tests/test_queue_transition_columns.py` therefore asserts every column of the row a transition returns, for all eight transitions, against a pre-populated row where every column holds a distinctive non-default value.

## Decision 4 — deliberate exclusions

Considered and left out, named so the next item does not have to rediscover them:

- **`TaskStatus` and its writer `update_task_status`** (per-task lifecycle). A vocabulary with no transition table would make `lifecycle.py` misrepresent itself, so `TaskStatus` stays in `alan/workspace.py`. The writer is recorded *mechanically* as the third gate exemption, not only here in prose.
- **`ProjectStatus`** (project lifecycle). Out of scope; no table.
- **`_CLAIM_SQL` and `_FINISH_SQL`** — transition-shaped but **deliberately retained**. They sit on the hottest dispatch path, and `_CLAIM_SQL` carries an intra-workspace-cap subquery (`SELECT id … ORDER BY id LIMIT 1` with a concurrent-running count) that no superset absorbs. They are not among the eight enumerated constants.
- **`_REQUEUE_COUNT_FOR_TASK_SQL`** (a `SELECT MAX(requeue_count)` count query) and **`_ADVANCE_ELIGIBLE_AT_SQL`** (touches `eligible_at`/`instructions`, not `status`) are not status-shaped.
- Route-layer modules and `alan/park.py` as targets in their own right: neither holds a status-writer call site. `park.py` only *loses* its vocabulary to `lifecycle`.

## The structural gate

`tests/lifecycle_gate.py`, following `tests/dispatch_provenance_gate.py`, is a pure AST checker exercised by `tests/test_lifecycle_gate.py`, including two assertions that run it over the real `alan/**/*.py` tree and require zero violations. It evaluates **per enclosing function**, not per `(file, callee)` — the provenance gate's key is too coarse here, because `model_copy` would exempt every copy in a file.

- **Clause 1 (load-bearing).** A function that calls `_write_meta(` **and** names `status` (the identifier `status`, a `"status"` string, or a `status=` keyword) violates unless `(rel_path, function)` is in the exemption set. The conjunction is required: `_write_meta` alone has about twenty call sites in `alan/workspace.py`, and `alan/control_token_store.py` has an unrelated private of the same name.
- **Clause 2 (tripwire).** Tree-wide over `alan/**`, a `"status"` key in `model_copy(update=…)` whose receiver is `Meta`-shaped (an `.meta` attribute, a name ending `_meta`, or a `Meta(`/`_read_meta(` call). A `Name` passed as `update=` is resolved back to a same-function dict-literal assignment, since the old writer built a local dict first.
- **Exemption set** (`STATUS_WRITE_EXEMPTIONS`, an ordered tuple so "first entry" is expressible, asserted by the count literal `STATUS_WRITE_EXEMPTIONS_COUNT = 3`):
  1. `("alan/workspace.py", "create")` — the birth write. `create()` sets `status=Status.ACTIVE` on a brand-new `Meta`; a birth is not a transition.
  2. `("alan/workspace.py", "transition")` — the writer itself, which derives its update from `lifecycle.transition`.
  3. `("alan/workspace.py", "update_task_status")` — the per-task lifecycle, excluded above.

A test also asserts that `alan.workspace.transition` really calls `lifecycle.transition`, so the "derived from" clause is checked, not assumed. **Adding an exemption later to make a failing change pass is a gate downgrade, not a fix**: the count literal makes any addition visible in review.

### Limits, stated honestly

The gate is a syntactic tripwire, not a proof.

- A `**kwargs`/`setattr` write, or an `update=` built from a helper call, evades it.
- `meta.model_copy(update=lifecycle.transition(...))` from a third party is invisible to clause 2's literal-key match.
- `("alan/workspace.py", "create")` exempts the **whole function**, so a status write added inside `create()` later would pass silently.
- A seeded direct write placed inside `create()` correctly passes; the gate's own test seeds the write at a site *outside* the exemption set.

## Predicted line delta

Prediction, stated up front so verification reads a prediction rather than a miss: the backend delta lands **between roughly −75 non-blank lines and +9** — break-even to a modest reduction, **not** a large one. The lever between the two is the collapse of the six public queue writers into wrappers over `Queue._execute_transition` alongside the eight constants (their public names and callers are unchanged; they differ only in boilerplate): about −84 on its own, against about +9 without it. This item's value to the roadmap is that X2.14–X2.17 are gated behind it, not its size.

**Measured: +91 non-blank lines** in `alan/` (66,177 → 66,268), a miss against the predicted band. The writer collapse was performed (`alan/queue.py` net −130 lines); the overshoot is `alan/lifecycle.py` at 232 non-blank lines against a ~180 estimate. The R29 backend ceiling is 67,111, so 843 lines of headroom remain and no pin was raised or allowance added.
