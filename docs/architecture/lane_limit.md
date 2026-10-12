# Per-engine in-flight lane limit

**Source:** `alan/lane_limit.py`, `alan/routes/engines_lane.py`, `alan/queue.py` (`Queue.seat_job_facts`), `alan/engine_registry_store.py` (`append_lane_limit_audit`).

An optional per-engine ceiling on the number of live workspaces, enforced only when a new workspace is created. Unset (the default for every engine, including any connected second engine and the default `DejvMatikar/alan` engine) means no limit — creation behaves exactly as it did before this module existed. This is a runtime dial, not a piece of reviewed configuration: it lives beside the dispatcher's other runtime state in the queue's key-value store, never on `EngineConfig`, never in `Meta`, and never as a JSON file under the engines root.

Issue #1416's own "Desired end state" sketch named an `EngineConfig.max_in_flight` field set through the existing `PATCH /api/engines/{owner}/{repo}`. The shipped design deliberately diverges from that sketch: the PR body that scoped this build ("Per-engine in-flight cap on workspace create") narrows storage to the queue-side key-value store precisely to rule out a snapshot-on-dispatch field, and puts out of scope "any new engine-configuration field, and any change to the existing whole-config engine update." The `EngineConfig`/`PATCH` shape in the issue was never load-bearing acceptance criteria for this build — see the "Not on `EngineConfig`" bullet below for why a field there would be actively wrong, not just a stylistic alternative.

## Store

The limit for a repository is a single `queue_meta` key, `engine_lane_limit_v1:{owner/repo}`, holding a decimal string. `get_limit` reads it tolerantly: a missing key is `None` (no limit); a non-integer or a value below 1 is logged and treated as `None` rather than raised, so a corrupted or hand-edited row degrades to "no limit" instead of breaking workspace creation. `set_limit` writes the decimal string via `Queue.set_meta`, or calls `Queue.delete_meta` to clear it. A restart survives because `queue_meta` is a SQLite table in the same `queue.db` the dispatcher's worker-liveness and pause-until state already persist in.

Three places the limit deliberately does **not** live:

- **Not on `EngineConfig`.** The full `EngineConfig` is snapshotted verbatim into `Meta.engine_snapshot` at a workspace's first dispatch (see [engine_config.md](engine_config.md)). A field there would freeze whatever limit was live at dispatch time onto every workspace it governs, rather than reading the current live value — the opposite of what a fleet-wide cap needs.
- **Not in `Meta`.** `Meta` is per-workspace state; a lane limit is per-engine, read across every workspace on that engine, not carried by any one of them.
- **Not a `*.json` file under `ALAN_ENGINES_ROOT`.** `engine_registry_store.list_engines()` (called from `engine_config.load_persisted_engines()` at boot) treats every `*.json` file directly under that directory as a serialized `EngineConfig` record. A lane-limit file there would be misread as a malformed engine and quarantined, or worse, partially validated. The audit trail below uses the same directory but a `.jsonl` suffix, which the boot loader's `*.json` glob never matches.

## Seat rule

Every non-archived, non-completed, non-idle workspace on an engine holds one seat. `holds_seat(status, facts, created_at, *, now)` in `alan/lane_limit.py` is the single predicate:

| Condition | Holds a seat? |
|---|---|
| `status` is `COMPLETED` or `ARCHIVED` | No — always, regardless of job state |
| A `RUNNING` job exists for the workspace | Yes |
| A `PENDING` job exists and is pickable (its `workspace_id` is not in `paused_tasks`) | Yes |
| Neither of the above, and the workspace has been idle less than 7 days | Yes |
| Neither of the above, and the workspace has been idle 7 days or more | No |

Every other `Status` value (`ACTIVE`, `AWAITING_INPUT`, `ESCALATED`, `FAILED`, `PAUSED`, `PARTIAL`) falls through to the idle-clock rule — there is no separate carve-out for `FAILED`/`PARTIAL`, since a fleet operator still wants those counted until they genuinely go quiet.

"Pickable" mirrors `Queue`'s own claim SQL (`_CLAIM_SQL`'s `workspace_id NOT IN (SELECT task_id FROM paused_tasks)`), not `Status.PAUSED` — a workspace can be `PAUSED` at the `Meta` level while its queue rows are untouched (`repo_quiesce` writes `paused_tasks` directly, independent of status), and a paused workspace's pending jobs must not count as pickable even though they still exist as rows.

`Queue.seat_job_facts(workspace_ids)` computes, per workspace, in one grouped query: `has_running`, `has_pending_pickable` (pickability check inlined into the `CASE`), and `last_activity = MAX(COALESCE(finished_at, started_at, enqueued_at))` across every job row for that workspace. A workspace with no job rows at all is absent from the result map; callers fall back to an all-`False`/`None` `SeatJobFacts`.

## Idle clock

Idleness is measured from **job activity**, never from `Meta.updated_at`. `alan/reconciler.py`'s `_park_orphan` calls `workspace.transition` on every service restart to re-park an orphaned workflow, and `transition` always stamps `updated_at = datetime.now(UTC)` — so a workspace that has been genuinely silent for months still gets its `updated_at` re-stamped on every boot, which would make it look freshly active to any clock keyed on that field. Job timestamps (`enqueued_at`/`started_at`/`finished_at`) are written once, at the point work actually happened, and are never touched by an unrelated restart.

The effective last-activity timestamp is `max(seat_job_facts.last_activity, Meta.created_at)` — falling back to `created_at` when the workspace has no job rows at all (e.g. a subtask workspace whose root never enqueued a job under its own id), and taking the later of the two otherwise as a defensive floor. A workspace is idle, and so holds no seat past the fallback above, once `now - effective >= 7 days` (`_IDLE_WINDOW`, a fixed `timedelta`, not a configurable dial — see "Out of scope" in the PR this shipped with).

## Engine attribution

A workspace counts toward an engine via `alan.engine_config.canonical_scope_key(ws.meta.engine_key)` — the same resolver `collision.py` and `predispatch_scan.py` already use to decide "is this workspace in scope for engine X." `canonical_scope_key(None)` and `canonical_scope_key("DejvMatikar/alan")` both resolve to the default engine's `RepoIdentity`, so a workspace created with no explicit `engine_key` (the common case today) counts toward the default engine's limit exactly as one created with the explicit slug would. Triage and dependency-sweep workspaces, which are typically created with no `engine_key`, count toward the default engine for the same reason.

## Refusal contract

Only `POST /api/workspaces` (`create_workspace` in `alan/routes/workspaces.py`) can refuse a create for lane-fullness. `alan.lane_limit.create_within_limit(queue, *, root, engine_key, create)` wraps the actual `workspace.create(...)` call (passed in as a `functools.partial` so the wrapper never needs to know `workspace.create`'s full argument list, and so the `dispatch_provenance` AST supply gate — which requires a `provenance=` keyword on the nearest enclosing call around any `workspace.create` reference — is satisfied by the `partial(...)` call itself):

1. Resolve `repo = canonical_scope_key(engine_key)`.
2. Read the limit for `repo`. If unset, call `create()` immediately with no lock taken at all — the documented no-limit-is-a-no-op fast path.
3. Otherwise, take the process-wide lock, recompute `seat_holders(queue, root, repo)` fresh (a disk scan via `workspace.list_workspaces`, never a cached index — see "Lock scope" below), and compare its length against the limit.
4. At or above the limit, raise `LaneFullError` **before** anything is written — no workspace directory, no `meta.json`, no predispatch-log or clause-audit row.
5. Below the limit, call `create()` while still holding the lock.

The route maps `LaneFullError` to `HTTPException(409, detail=str(exc))`. The detail always starts with the literal `lane full:`, followed by the engine slug, the limit, the current seat count, and the seat holders ordered by `created_at` then `task_id`:

```
lane full: acme/widget limit=2 seats=2 holders=task-a (active), task-b (running)
```

Because the refusal happens before `workspace.create` runs, the request's prep-chat session (if any) is never archived — `create_workspace`'s prep-chat archive call happens only after a successful dispatch, further down the same handler, and a `LaneFullError` raised from `create_within_limit` exits the handler via the `except` clause well before that point. The chat stays live and the operator can retry, edit, or abandon it exactly as if the create had never been attempted. The frontend needs no new handling: `create_workspace`'s existing error banner already renders whatever `detail` string a failed create returns.

## Lock scope

`alan.lane_limit._creation_lock` is a single process-wide `threading.Lock`, held only around the seat count and the `create()` call inside `create_within_limit` — never around the predispatch duplicate-work scan, brief refinement, or the first dispatch that follows a successful create in `create_workspace`. Those steps run unlocked, both before (`scan_predispatch`) and after (`refine_and_dispatch_root_task`) the guarded section, exactly as they did before this module existed. The lock is also skipped entirely on the no-limit fast path, so an engine with no lane limit set pays no synchronization cost at all.

Because `seat_holders` re-reads workspaces from disk (`workspace.list_workspaces`) rather than the in-memory `WorkspaceIndex` cache, the second of two concurrent creates against the same one-seat-short engine observes the first one's freshly written `meta.json` and correctly refuses — the lock's only job is to make "read the count, decide, create" atomic against another thread doing the same thing; the freshness of the read is what the disk-scan (not index) choice buys.

## Never refused, but counted

Every path other than `POST /api/workspaces` that can create or revive a workspace is unconditionally allowed to proceed, even past a full lane: `POST .../answer`, `POST .../tasks/{task_id}/dispatch` (re-dispatch), `POST .../unarchive`, `alan.teams.dependency_sweep`, `alan.project_auto_continue`, and the reconciler's own recovery paths. None of these call `create_within_limit`. Every workspace they touch is still counted by the ordinary seat rule the next time anyone reads `GET .../lane` or attempts a new create — there is no separate "system-created, exempt forever" flag.

The contract this module gives an operator is: **the create path never pushes an engine's seat count past its limit.** It is explicitly not a guarantee that the seat count can never exceed the limit — a revival, a dependency-sweep workspace, or simply lowering the limit below the current count (which only blocks new creates; it never evicts or archives anything already live) can all leave an engine over its nominal cap. Reconciling that back down, if ever wanted, is a human or a future feature's job, not this one's.

## Endpoints

`alan/routes/engines_lane.py` — a route sibling of `alan/routes/engines.py`, listed in the "Routes siblings are independent" import-linter contract.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/engines/{owner}/{repo}/lane` | none | 404 if the engine is not registered (`engine_config.lookup_by_repo` returns `None`). Returns `LaneStatus {limit: int \| None, seats: int, holders: SeatHolder[]}`, where each `SeatHolder` is `{task_id, title, status}`. |
| PUT | `/api/engines/{owner}/{repo}/lane/limit` | control token | Body `{limit: int \| None}` — `limit` is a required key; a `StrictInt` `>= 1` or `null` to clear. A value below 1, or a non-integer (float, string, bool), is rejected with 422 at the pydantic layer before the handler runs. 404 for an unregistered engine — the same `lookup_by_repo(identity) is None` check as GET, deliberately not a 400: this route family already treats an unknown owner/repo as 404 everywhere else (accent, visibility, posture). Returns the same `LaneStatus` shape as GET, recomputed after the write. |

The PUT handler never calls `engine_registry_store.write_engine` or `workspace._write_meta` — setting a lane limit touches no `EngineConfig` record and no workspace's `meta.json`, only the `queue_meta` key above plus the audit line below. No SSE event is published for a limit change (`GET .../lane` is cheap enough, and no frontend view exists yet to repaint).

## Audit

Every actual change (old value != new value; setting the same value again is a no-op with no audit line) appends one line to `lane_limit_audit.jsonl` under the engines root, beside `posture_audit.jsonl`, via `alan.engine_registry_store.append_lane_limit_audit`. Both audit writers now share a private `_append_audit_line(filename, label, repo_slug, entry)` helper — the same best-effort `O_APPEND` file descriptor dance, logging and swallowing any `OSError` rather than failing the request. Each line:

```json
{"from": 2, "to": 3, "source": "api", "timestamp_utc": "2026-09-10T12:00:00+00:00", "repo": "acme/widget"}
```

`from`/`to` are `int | null`; `source` is always `"api"` today (the only writer is the PUT route) but is threaded through as a plain string so a future non-API writer (a driver script, say) can record its own source without a schema change. `.jsonl` files are never read by the boot loader, so this audit trail carries no risk of being misread as an engine record.

## Operator note

Setting a second engine's limit, and updating driver/babysit prompts to respect it, are follow-up operator actions after this module merges — this module only builds the mechanism (store, seat rule, refusal, both endpoints). See the "Out of scope" section of the PR that introduced this file.
