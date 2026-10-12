# Workspace index — the derived read model behind `GET /api/workspaces`

`alan/workspace_index.py` holds `WorkspaceIndex`, an in-memory, self-validating read
model that serves the home-page workspace list without walking per-workspace YAML on
the hot path. It replaced the per-request `_compute_invocation_data` loop
(`routes/workspaces.py`, removed 2026-07-08) that deep-copied every cached
`WorkflowDoc` (~2,549 docs across 310 workspaces) and re-read every
`manager_summary_stream.jsonl` on each 5 s poll — one of the two scans behind the
2026-07-07 fd-exhaustion wedge.

## Governing principle: push notifies, read validates

Events (a future global SSE channel) only ever mean "something changed, refetch".
Correctness never depends on a write-site hook: every read re-validates freshness
against disk with cheap `(st_mtime_ns, st_size)` stat signatures — the established
cache idiom (`_read_meta_cached`, `load_workflow_cached`) — except for the five
per-workspace directories (handovers, invocations, chat sessions, cleared-chat cost
records, archived preparation-chat sessions), which are validated by a listing check
rather than a directory timestamp (see "Change detection without directory
timestamps" below). Out-of-band
writes (operator recovery hand-edits of `meta.json`, e2e specs seeding workspace
files mid-test) are therefore visible on the next request with no special
machinery: the read *is* the reconciler. The single deliberate exception is
`unarchive` (below), because archived directories are excluded from scanning
entirely.

## Shape

- `WorkspaceRow` (frozen pydantic): everything `WorkspaceSummary` needs except
  `invocations_total` (queue-derived via one grouped `count_jobs_by_workspace` SQL at
  the route — keyed on the `workspace_id` column so a decomposed workspace's subtask
  jobs count under the root), plus `park_reason`, `scope_ref` (latest PR-bearing handover → `pr:N`, else
  `ws:<task_id>`; derived through `load_handover_cached` only when the handovers
  listing fingerprint changes, snapshotted into archived rows at archive time, `None` in
  old blobs → `resolved_scope_ref()` falls back) and `handovers_mtime` — `scope_ref`
  resolves an audit-derived `pr:N` incident back to a workspace (see [triage.md](triage.md)),
  and `handovers_mtime` is what flips a workspace's signature when an in-place
  `answer_open_question` rewrite touches a handover with no other observable change.
  `handovers_mtime` is still the handovers directory's `st_mtime_ns`, stat'd on every
  read into `_Guards.handovers_dir_mtime` and copied onto the row; it no longer gates
  re-derivation. `scope_ref` is derived alongside two more handover-sourced signals —
  `_Guards.plan_files_to_edit`/`architecture_files_to_edit`, feeding `size_class` below —
  in a single reverse pass over the already-listed `handover_paths_under` result
  (`_derive_handover_signals`), guarded by `_Guards.handovers_listing`, the
  `(name, inode, size)` fingerprint of every listed handover, so adding the two new
  signals costs no new disk reads over what `scope_ref` already paid for.
- `roadmap_item_id` (verbatim `Meta.roadmap_item_id`, the join key mirroring
  `project_id`), `tasks_total`/`tasks_done` (`len(meta.tasks)` / a count of
  `TaskStatus.COMPLETED` only — a `failed` task never counts), and `pr_number`
  (the latest number folded from every task's DoD-clause evidence via
  `workspace.pr_numbers_from_tasks`, `None` when empty) — all four are folds over
  `meta.tasks`/`meta.roadmap_item_id`, already loaded by this fold, so they cost
  zero new disk reads. See "Roadmap delivery evidence" below for how the route
  layer composes `pr_number`/`roadmap_item_id` into per-project totals.
- Internal `_Guards` per non-archived row: `meta.json` sig, the `handovers/` listing
  fingerprint, per-invocation `WorkflowRowSummary` memos, the per-invocation failure
  memo (`inv_failures`), and the `ChatCostGuards` that hold the `chat/`, `prep-chat/` and
  `cleared-chat-cost/` listing fingerprints. No directory timestamp gates any re-listing.
- `workflow.load_workflow_summary_cached(path)` (`alan/workflow.py`, next to
  `load_workflow_cached`) returns a small frozen `(team, status, cost)` record from
  the same mtime+size cache shape **without deepcopy** — the summary is immutable,
  so cache hits are free. In-place rewrites of any invocation doc stay visible
  (locked in by `test_list_workspaces_reparses_only_the_changed_workflow`).

## Timestamp normalisation: `updated_at` is always aware

`Meta.updated_at` (`alan/workspace.py`) and `WorkspaceRow.updated_at` each carry an
after-validator (`_coerce_updated_at_utc`, both built on the shared `alan.workspace.as_utc`
helper) that stamps a zone-less value to UTC. It is applied at both real construction
paths: `Meta.model_validate`/`model_validate_json` (the on-disk `meta.json` read via
`_read_meta`) and `WorkspaceRow.model_validate` (`_row_from_meta`, and `_parse_blob_rows`'s
deserialization of the `ARCHIVED_ROWS_META_KEY` blob — the one archived-row path that never
passes through `Meta` at all). `model_copy` does not re-run validators, so every other
`WorkspaceRow`/`Meta` construction site relies on the value already being aware by the time
it is copied, which the two `model_validate` entry points guarantee.

The coercion never rejects. `_read_meta` converts a genuine `ValidationError` into
`WorkspaceError`, which `list_workspaces` and `WorkspaceIndex._classify_new`/`_reread_meta`
both swallow — a strict validator here would silently drop the workspace from every listing
and from the UI while its jobs kept failing with a message the operator cannot trace to a
cause, and on the archived path `_parse_blob_rows` would just as silently undercount archived
workspaces rather than raise. Normalising at the model boundary instead of rejecting is what
keeps a zone-less value from ever reaching those failure paths.

`alan.recurring_status._backup_coverage` and `alan.retention._is_stale_completed` both compare
`updated_at` against an aware reference timestamp with no guard of their own — the invariant
holding at the model boundary is what makes those comparisons safe, not anything in either
call site.

## Refresh cycle (`refresh()`, single-flight, no debounce)

One root `iterdir`, then per known non-archived row: stat `meta.json` (re-parse on
sig change), list `invocations/` through `list_invocation_ids` on every read and
look each listed invocation's `workflow.yaml` up through the summary cache, fold the
Phase-0-cached `read_manager_summary_usage` cost, stat the `handovers/` dir for
`handovers_mtime`, list `handovers/` and stat each listed handover to fingerprint it
(re-derive `scope_ref`/`size_class` signals only when the fingerprint changed), then
list `chat/`, `prep-chat/` and `cleared-chat-cost/` and fingerprint each listing
(re-derive the session ids or cleared records only when it changed). New dirs are
classified via `workspace.read`; vanished dirs drop their rows (self-heals `DELETE`
and manual `rm -rf`). Warm cost is directory listings and stat calls only — no YAML
parse, no deepcopy, no handover load, no stream or cost-record open — so the scan runs
on every read (`rows()` / `archived_rows()`), keeping reads always-fresh. The exact
per-workspace counts are in "Change detection without directory timestamps" below.

## Change detection without directory timestamps

Linux takes file timestamps from a clock that advances once per kernel tick, so an entry
added, replaced or removed within the same tick as the index's previous look leaves the
parent directory's `st_mtime_ns` unchanged. The index used to compare that one number for
five per-workspace directories and kept serving stale rows until something else moved the
timestamp or the service restarted (#1945: the handover size-class test passed on macOS,
whose timestamps carry nanosecond resolution, and failed on Linux; #1949: the P2.4 Linux
run was red because the branch that drops a vanished preparation-chat session never ran,
the parent timestamp not having moved). No directory timestamp gates a re-listing any more.

**The listing primitive.** Four of the five directories share one check, in `alan/store.py`
(the bottom import layer, which both `workspace_index` and `workspace_cost` already import):

- `store.list_dir(path)` returns the sorted entries as a fully materialised `list[Path]`,
  or `[]` on `OSError`.
- `store.listing_entry(path, dirs_only=False)` returns `(name, st_ino, st_size)`, or `None`
  when the path no longer stats (or, with `dirs_only`, is not a directory).
- `store.listing_fingerprint(paths, dirs_only=False)` is the tuple of those entries for
  the paths that still stat. The `store.ListingEntry` alias names one entry.

No directory or entry timestamp — modification or change time — enters a fingerprint, so
a detection that works on a coarse-tick filesystem works on a fine-grained one too, and a
test that restores `st_mtime_ns` cannot mask a change. A guard field starts as `None`,
which forces the first derivation; afterwards the derived state is rebuilt only when the
fingerprint differs from the stored one. Every guard holds plain tuples; no directory
handle or iterator outlives a listing.

**The directories.**

| Directory | Listing | Guard | Re-derived on change |
| --- | --- | --- | --- |
| `handovers/` | `handover_mod.handover_paths_under` | `_Guards.handovers_listing` | `scope_ref`, `plan_files_to_edit`, `architecture_files_to_edit` |
| `chat/` | `store.list_dir`, names matching the session-id regex, `dirs_only` | `ChatCostGuards.chat_listing` | `session_ids`; drops the state of vanished sessions |
| `prep-chat/` | same as `chat/` | `ChatCostGuards.prep_listing` | `prep_session_ids`; drops the state of vanished sessions |
| `cleared-chat-cost/` | `store.list_dir`, names ending `.json` | `ChatCostGuards.cleared_listing` | re-reads every record into `cleared_costs` |
| `invocations/` | `workflow_mod.list_invocation_ids` | none (see below) | summaries for new ids; removed ids dropped |

What a fingerprint sees for each change:

- An added or removed entry changes the set of names. A session moved into an existing,
  empty `prep-chat/` (the archive sequence: `archive` creates the directory, then moves
  the session in) is such an addition.
- `save_handover` reserves the numbered slot as an empty file (`_claim_handover_slot`),
  then writes a temp file and renames it over the slot. A read that lands in between
  sees `(name, inode, 0)`; the rename replaces the inode and the size, so the filled
  slot always fingerprints differently, whatever the timestamps say.
- An entry that vanishes between the listing and its `stat` is dropped from the
  fingerprint (`listing_entry` returns `None` on `OSError`); the derivation pass skips it
  too, because the load raises.
- A regular file whose name looks like a session id is excluded by `dirs_only`, the same
  filter `is_dir()` applied before.

**Invocations.** A fingerprint of `invocations/` would be the wrong signal. An invocation's
directory is created (`create_invocation_dir`, from `seed_pending_workflow`) before its
`workflow.yaml` is written, and writing the document does not change the parent listing,
so a document that appears after its directory was listed would never move the fingerprint.
The signal is instead the set of invocations that currently have a workflow document:
`_update_inv_summaries` calls `workflow_mod.list_invocation_ids` on every read (it lists
through `store.list_dir` and keeps entries whose `workflow.yaml` is a file), drops the
summaries and failure memos of ids no longer listed, and reads each listed id through
`load_workflow_summary_cached`, whose `(mtime_ns, size)` cache is what keeps an in-place
rewrite of a document visible. This also closes a window that existed on macOS: a late
document used to be missed whenever the directory had been listed before it was written.

**Failure memo.** A workflow document that fails to load (`WorkflowError`) used to be
popped from the summaries and, with the directory timestamp unchanged, never retried. Now
that every listed id is visited on every read, that would re-parse a corrupt document on
each poll. `_Guards.inv_failures` records the `stat_signature` of a failed document
(taken before the load, so a rewrite that lands mid-load is not memoised); a later read
skips the id while the signature is unchanged, clears the entry on a successful load, and
prunes it once the id is no longer listed. A repaired document therefore reappears on the
next read, and an unchanged corrupt one costs one `stat`.

The `handovers/` directory is still stat'd on every read into
`_Guards.handovers_dir_mtime`, which is what `WorkspaceRow.handovers_mtime` carries — the
field's meaning and the archived blob version (`_BLOB_VERSION`, 6) are unchanged.

**Cost.** Per live workspace, per read with nothing changed:

- Handovers: one `handovers/` listing, one `listing_entry` per listed handover and the
  one directory `stat` for `handovers_mtime`. `load_handover_cached` is not called at all,
  not even for cache hits (`test_unchanged_handovers_are_not_reloaded_on_refresh` spies on
  it).
- Invocations: one `list_invocation_ids`, then per listed invocation one `stat_signature`
  (the failure-memo check) and one `load_workflow_summary_cached` lookup. No
  `workflow.yaml` is parsed (`load_workflow` is not called).
- Chat cost: exactly three `list_dir` calls (`chat/`, `prep-chat/`, `cleared-chat-cost/`),
  one `listing_entry` per session-named directory and per cleared record (`n + p + c`), and
  `2 × (n + p) + 1` `stat_signature` calls — a stream and a collision stream per chat and
  preparation session, plus the investigator stream. For `n = 3` chat sessions, `p = 2`
  preparation sessions and `c = 2` cleared records that is 3, 7 and 11
  (`test_warm_refresh_listing_and_signature_calls_match_exact_formula` pins them with
  `==`). `derive_run_summary` and `ClearedSessionCost.model_validate_json` are not called
  (`test_warm_read_parses_no_stream_or_cleared_record`), and a read with the legacy
  categories off (`include_new_categories=False`) lists none of the three.

`Path.iterdir` is exhausted inside `store.list_dir`, `Path.glob` closes its scandir handle
before returning, and the index holds no directory iterator across calls — this is the hot
path whose predecessor caused the 2026-07-07 fd-exhaustion wedge (see the top of this
doc), so no handle outlives the listing. The cold archived path pays the same price once
per workspace: `backfill_archived` does the same listings and stats per archived
workspace, off the request path.

**Accepted limitations.**

- An out-of-band rewrite of an existing handover in place, at the same size and within
  the same tick, is not detected. Because no timestamp enters the fingerprint, the same
  holds across ticks: a hand-edit that keeps the inode and the byte length is invisible
  until the listing changes some other way or the service restarts. Every write made
  through `save_handover` changes the inode or the size, and removals change the name
  set, so only operator hand-edits are exposed.
- The same holds for a cleared-chat cost record rewritten in place with the same inode and
  size: its fingerprint is unchanged, so the old figure keeps counting. Records are written
  once, when a chat session is cleared, so only a hand-edit is exposed.
- `handover._HANDOVER_CACHE` keys on `(mtime_ns, size)`, so a same-path, same-size
  replace within the same tick can still serve a stale parse once the fingerprint
  triggers a re-derivation. Follow-up.
- The legacy handover chain-index cache (`handover._build_chain_index`) still keys on a
  directory timestamp. Its only path is migrating old handover files, which current code
  never writes, so it is recorded here rather than changed.
- A session directory's `st_size` is part of its fingerprint and can move when files are
  added to or removed from it on filesystems that report directory sizes. That causes a
  harmless re-derivation of the session ids (the same tuple is rebuilt, and no stream is
  re-parsed, because each stream keeps its own `stat_signature`), never a stale result.

## Archived rows: out of every hot path

A status flip to `ARCHIVED` (observed via the meta sig) moves the row into an
archived store persisted as `queue_meta["workspace_index_archived_v1"]`
(`{"version": 1, "complete": bool, "rows": {...}}` — the established versioned-blob
pattern of `health_state_v1` et al.). **Archived directories are never stat'd
again.** On first deploy the blob is absent: archived dirs are classified meta-only
(cost/latest fields null) and a daemon backfill thread
(`backfill_archived`, spawned in lifespan via `_start_workspace_index`) derives the
full rows once, off the request path, then marks the blob `complete`. Blob loading
is tolerant of corruption/malformed rows (drops them, mirrors `incident_store`).

Consequences and escape hatches:

- `POST /{id}/unarchive` and `DELETE /{id}` call `index.invalidate(task_id)` — the
  only mandatory write-site hooks (an archived dir is otherwise invisible to the
  scan; a deleted one must leave the blob).
- Hand-editing an *archived* workspace's `meta.json` is the one blind spot:
  `POST /api/admin/reindex` (`routes/infra.py`) runs `reindex()` — full rebuild +
  synchronous archived re-derive — and is the recovery/e2e escape hatch.

## Route contract (`GET /api/workspaces`)

- Default (no `bucket`): all **non-archived** rows — the frontend renders these under
  a single **Active** tab (the former Active/Backlog split was merged 2026-07-08).
- `bucket=archived`: served from the blob store; the frontend fetches it lazily on
  Archived-tab activation and polls it only while that tab is active
  (`WorkspaceList.tsx` `usePolledWorkspaces`).
- `include=triage` filtering is unchanged. Wire shape (`WorkspaceSummary`) is
  unchanged.

The dep (`workspace_index_dep`, `routes/_deps.py`) resolves through
`workspaces_root_dep`/`queue_dep` DI (so test `dependency_overrides` compose) and
lazily creates per-root indexes in `app.state.workspace_indexes`; lifespan
pre-builds the real root's index.

`GET /api/health`'s quality metrics (`_build_quality_metrics`, the other former
per-poll full scan) are now served through `_cached_quality_metrics`, a 30 s TTL
cache keyed by root — `build_success_rate` legitimately includes archived history,
so it keeps the full `list_workspaces` scan but pays it at most twice a minute.

## Project aggregation and the `project_id`/`project_title` join

Every `WorkspaceRow` carries the workspace's `project_id` verbatim from
`Meta.project_id` (folded in by `_row_from_meta`, in both live and archived rows).
Two consumers fold that field with zero extra disk I/O beyond the rows already
loaded:

- `project_burn_usd(rows, archived, project_id)` sums `total_cost_usd` (`None` ->
  `0.0`) across both buckets for **one** project — the primitive behind
  `alan.proposal_items.resolve_project_burn` (used by the propose-mode
  next-roadmap-item card) and `alan.project_budget`'s budget-state reads.
- `project_aggregates(rows, archived)` does the same fold for **every** project
  at once, one O(W) pass over the same two lists, returning `{project_id:
  ProjectAggregate(workspace_count, total_spend_usd)}`. `routes/projects.py`'s
  `GET /api/projects` calls this once per request (`rows()` + `archived_snapshot()`
  via `WorkspaceIndexDep`) to attach `workspace_count`/`total_spend_usd` to each
  `ProjectListEntry` — no per-project workspace scan, so N projects cost the same
  one index read as N=1 (the access pattern a past fd-exhaustion incident ruled
  out; see the note at the top of this doc). `rows()` calls `refresh()` (the full
  scan, which also syncs the archived store — see "Refresh cycle" above); the
  route deliberately reads `archived_snapshot()` rather than `archived_rows()`
  afterward so the request pays that scan once, not twice.

`GET /api/workspaces` performs a parallel but distinct join, at the route layer
rather than in the index: `routes/workspaces.py`'s `_resolve_project_titles`
resolves each row's `project_id` to a `project_title` via
`alan.project.cached_read`, memoized in a per-request `dict[str, Project |
None]` — at most one `project.json` read per *distinct* project id in the
response, never per row. A dangling `project_id` (the project was deleted, or
predates `project_id` entirely in an old archived blob) surfaces as `project_id`
set with `project_title: null` rather than an error. `WorkspaceRow` and the
archived blob never carry the title itself — only the route layer joins it in,
so the archived-blob schema doesn't grow and pre-`project_id` archives don't
need a migration; they read back as `project_id: null` (and therefore
`project_title: null`) until an admin `POST /api/admin/reindex` backfills them.

## Roadmap delivery evidence and the `delivered_roadmap_item_ids` fold

`GET /api/projects` (list) additively carries `roadmap_items_total`/
`roadmap_items_delivered` (`K of M`) per project — see
`docs/architecture/projects.md` for the full design write-up. The layer
constraint that shapes this feature: `workspace_index` sits *below*
`project_roadmap` in the CI-enforced `[tool.importlinter]` layers list
(`pyproject.toml`), so this module can never import `alan.project_roadmap` or
know what a `RoadmapItem` is — the roadmap-totals fold cannot live here.

What *can* live here, at zero new disk reads, is delivery **evidence**:
`delivered_roadmap_item_ids(rows, archived) -> dict[project_id, set[roadmap_item_id]]`
folds every row's `roadmap_item_id`, `status`, and `pr_number` — a row counts
once terminal (`COMPLETED`/`ARCHIVED`) and carrying a non-`None` `pr_number`,
the same "delivered" test `routes/projects.py._roadmap_is_delivered` applies
per workspace — across every project at once, one O(W) pass, the same shape
as `project_aggregates` above. `routes/projects.py` calls it once per request
off the same `rows()`/`archived_snapshot()` the route already reads for
`project_aggregates`, then composes the resulting per-project id sets against
`project_roadmap.read_items(project_id)` — one small JSON read per project,
not a workspace-root rescan — to turn "which items were delivered" into
"K of M". This module never sees a `RoadmapItem`; it only ever hands back
ids the route already agreed to look up.

## Workspace cost: five categories, one derivation (`alan/workspace_cost.py`)

`total_cost_usd` folds every category of model spend attributable to a workspace,
derived once and shared by both `GET /api/workspaces` (list) and
`GET /api/workspaces/{id}` (detail) — neither surface recomputes it independently,
so the two figures can no longer drift:

- **Team/agent runs** — the existing per-invocation `WorkflowRowSummary.cost` fold.
- **Status summary** — the existing `read_manager_summary_usage` read of
  `manager_summary_stream.jsonl`.
- **Discussion chat** — every workspace-chat session's `chat/<sid>/stream.jsonl`,
  summed across **all** session dirs found under `<ws>/chat/`. A workspace can
  accumulate several chat sessions over its life (a chat gets cleared and a new
  one started); only the directory listing sees every one.
  Utility turns (e.g. the chat's summarize-for-brief call) need no separate
  category — `run_summarize` sinks its usage to that same `stream.jsonl`.
- **Collision check** — the propose-time overlap check, read per-session from
  `chat/<sid>/collision_stream.jsonl`. Only the per-session file is
  workspace-attributable; the module-level `state_dir()/collision/stream.jsonl`
  used by other collision call sites is not (it's shared across every propose door
  in the process) and is deliberately never read here.
- **Investigator** — the read-only RCA sidecar's `investigator_stream.jsonl`, a
  single file at the workspace root (not per-session).

Session discovery lists `<ws>/chat/` through `store.list_dir`, keeps names matching the
session-id UUID regex and fingerprints them with `dirs_only` — the same filter shape
`_chat_core._iter_valid_sessions` uses. `chat/` also holds a stray `system_prompt.txt`,
a legacy on-disk artefact from the now-removed workspace-discussion chat, that the
`dirs_only` check excludes on its own; the regex additionally excludes any non-UUID
directory.

`workspace_cost` sits below `_chat_core`, `collision` and
`investigator` in the import-linter layers (it sits directly below
`workspace_index`, just above `handover`), so it cannot import their filename
constants — it duplicates the three stream filenames
(`stream.jsonl`/`collision_stream.jsonl`/`investigator_stream.jsonl`) and the
session-id regex as its own module-level constants, locked equal to the
originals by an equality test in `tests/test_workspace_cost.py`.

### Freshness: same cheap change-detection, no new re-parsing

Warm reads stay listing-and-stat-only, matching the existing invocation/manager-usage
cache idiom:

- A listing fingerprint of `chat/` (and of `prep-chat/`) guards session discovery — the
  session ids are rebuilt only when a session directory is added, removed or replaced.
  No directory timestamp is consulted (see "Change detection without directory
  timestamps" above). Appending to an existing session's `stream.jsonl` does **not**
  change that fingerprint, so per-session freshness needs its own signal:
- Each of a session's two stream files (`stream.jsonl`, `collision_stream.jsonl`)
  carries its own `(mtime_ns, size)` signature; `derive_run_summary` (the
  open+parse) only runs when that signature changes.
- The investigator file gets the same one `(mtime_ns, size)` signature at the
  workspace root.
- A listing fingerprint of `cleared-chat-cost/` guards the cleared records; they are
  re-read only when the set of record files changes.

Per live workspace this bounds a warm cost read at exactly three directory listings
(`chat/`, `prep-chat/`, `cleared-chat-cost/`), `n + p + c` `listing_entry` calls (chat
sessions, preparation sessions, cleared records) and `2 × (n + p) + 1` `stat_signature`
calls (a stream and a collision stream per chat and preparation session, plus the
investigator file) — zero `Path.open`, zero `derive_run_summary` and zero cleared-record
parses when nothing changed.

`WorkspaceRow.cost_totals` (`WorkspaceCostTotals`, five nullable floats) carries
the per-category breakdown; `total_cost_usd` stays the flat summed field it always
was, so `project_burn_usd`, `project_aggregates`, `proposal_items`, and budget reads
need no changes — they already fold `total_cost_usd` and pick up the fuller number
for free. `alan.workspace_cost.synthesize_cost_breakdown` is a pure function
(no I/O) turning `WorkspaceCostTotals` into a `RunSummary`-shaped list for display,
built on the same `RunSummary` type the manager-summary/collision/chat run
summaries already use — the frontend's existing `RunPricing` component can render
it without a new one-off. It runs once per detail `GET`, never per list row.

`GET /api/workspaces/{id}` reads its row through `WorkspaceIndex.refresh_one(task_id)`
rather than `rows()`: it stats and re-derives only the requested workspace (archived
rows return straight from the blob, no stat at all) instead of the full root
`iterdir()` + per-row scan that a poll of the list needs anyway. Detail-page load
stays O(1) in workspace count instead of O(N).

### Archived rows: the version bump *is* the migration

Adding `cost_totals` to `WorkspaceRow` bumped `_BLOB_VERSION` 2→3. Since
`_parse_blob_rows` discards the whole blob on a version mismatch (the established
pattern — see `test_old_version_archived_blob_discarded_and_rederives_engine_key`
for the precedent this migration follows), every archived workspace stored under
the old schema is treated as absent on the next `rebuild()`: re-classified
meta-only on the live scan (`compute_costs=False` — team-runs and status-summary
are still read eagerly there, exactly as before this feature; only the three
*new* categories, discussion/collision/investigator, are held back to avoid
turning a cold-start scan across hundreds of archived dirs into a read storm),
then fully re-derived — all five categories — by the existing `backfill_archived`
daemon (spawned in `lifespan` via `_start_workspace_index`,
`alan/routes/infra.py:328`), off the request path, with no admin action. This is
exactly how a pre-`project_id` or pre-`engine_key` archived blob already
self-healed; the cost categories ride the same mechanism.

Adding `roadmap_item_id`/`tasks_total`/`tasks_done`/`pr_number` bumped
`_BLOB_VERSION` 3→4 for a related but distinct reason. Unlike the cost
categories, these four fields are cheap in-memory folds over
`meta.tasks`/`meta.roadmap_item_id` — already loaded, not gated by
`compute_costs` — so they are populated correctly on *every* live re-derivation,
including the meta-only reclassify pass. The bump is still required because a
**persisted** v3 blob row, loaded straight off disk via
`WorkspaceRow.model_validate`, has no idea these fields exist: pydantic's field
defaults would silently serve `tasks_total: 0` / `pr_number: null` for every
already-archived row regardless of its real task history, rather than raising
or re-deriving. Discarding the whole v3 blob on version mismatch is what forces
every archived row through one fresh `_row_from_meta` call — via the same
meta-only-then-`backfill_archived` two-step above — before it can serve these
fields at all. This one-time re-derivation is the explicit, documented
exception to "zero new disk reads": it costs one `meta.json` read per archived
workspace, exactly once, off the request path, the same price every prior
`_BLOB_VERSION` bump has paid.

### Accepted limitations

- **Last-write-wins spend.** Both the status-summary and investigator sidecars
  reset (`unlink`) their stream file before each new run, so only the *most
  recent* run of each is ever readable — historical spend from earlier runs of
  either is already unrecoverable by the time this derivation runs. Durable
  accumulation of that spend is out of scope for this feature.
- **Cleared discussions (historical).** Deleting a chat session (via the
  now-removed `delete_ws_chat_session`) used to remove its whole directory,
  `stream.jsonl`/`collision_stream.jsonl` included — its spend left the
  workspace total. No live surface deletes an individual discussion-chat
  session directory today; this note is retained because the cost reader's
  directory-listing derivation (above) still depends on the same on-disk
  shape.
- **Per-session collision-check history.** `check_collision()` unlinks
  `chat/<sid>/collision_stream.jsonl` before each run (`collision.py:305-306`),
  so a session that proposes a dispatch twice only ever contributes its most
  recent check's cost — the first check's spend is unrecoverable by the time
  this derivation reads the file. Same last-write-wins class as the
  status-summary/investigator limitation above, scoped to one session's file
  instead of the workspace root.

## Active fleet time and lead time (`alan/fleet_timing.py`)

Two distinct timings, both derived at zero new disk cost off data this module
already loads:

- **Active fleet time** — the union (not sum) of `[started_at, completed_at)`
  across every non-sentinel workflow node of every invocation of a workspace.
  Concurrent DAG fan-out is never double-counted; queue waits, human waits, and
  overnight idle time between invocations contribute zero because nothing
  outside a node's own interval is ever counted. See
  `docs/architecture/workflow_visibility.md`'s "Active intervals feeding
  `alan.fleet_timing`" for how `WorkflowRowSummary.active_intervals` is derived
  and clamped per node, and `alan/fleet_timing.py` for the interval-merge sweep
  itself.
- **Lead time** — calendar elapsed time from `Meta.created_at` to
  `Meta.delivered_at`, `null` until delivery is confirmed. `delivered_at` is
  written exactly once, by `alan.workspace.mark_delivered`, called from
  `deploy_verify`'s `_confirmed_outcome` — see `docs/architecture/deploy_verify.md`'s
  "Delivery confirmation" section.

`WorkspaceRow` stores active fleet time as two cache-safe scalars rather than a
pre-computed total: `active_closed_seconds` (the union duration of every
*bounded* interval — either genuinely completed, or a crashed/interrupted node
clamped to its terminal doc's `completed_at`) and `active_open_since` (the
start of the still-open tail, `None` when nothing is currently running). The
full rebuild path (`_row_from_meta`) derives both via `_active_time_fields(meta,
guards)`; the cheap in-place refresh path (`_row_with_updated_costs` — see
"Refresh cycle" above, the branch that fires when `invocations/` changed but
`meta.json` didn't) runs the same fold but gates on the *row's* already-known
`delivered_at` instead of re-reading `meta.json`, since that path never has a
fresh `Meta` in hand. Either way the fold is
`fleet_timing.active_fleet_time_for_summaries(guards.inv_summaries.values())`.
Neither scalar has `now` baked in: `WorkspaceRow.live_active_seconds(now)` (a thin wrapper over
`fleet_timing.ActiveFleetTime.live_seconds`) adds `now - active_open_since` to
`active_closed_seconds` **at serialization time**, in `routes/workspaces.py` —
so a workspace with a running agent node reports a larger `active_seconds` on
every subsequent `GET`, even when nothing changed on disk and no row rebuild
ran in between. Both scalars are `None` when no non-sentinel node has ever
started for the workspace (`live_active_seconds` then returns `None` too) —
the explicit empty state the feature's DoD requires, rather than a fabricated
zero.

Once `Meta.delivered_at` is set, `_active_time_fields` stops recomputing from
`inv_summaries` entirely and returns `(meta.settled_active_seconds, None)`
verbatim — the settled figure `mark_delivered` snapshotted at confirmation
time, frozen forever after. This is what keeps a workspace's `active_seconds`
from silently growing if a sibling decomposed task keeps running (or a
recovery re-dispatch happens) after delivery was already confirmed.

`routes/workspaces.py` exposes `active_seconds` (via `row.live_active_seconds(now)`)
and `lead_seconds` (via `fleet_timing.lead_seconds(created_at=, delivered_at=)`)
as raw `float | None` on both `GET /api/workspaces` (list, one shared `now` per
request) and `GET /api/workspaces/{id}` (detail) — never pre-formatted
strings; formatting is a frontend concern. `WorkspaceRow` also carries
`delivered_at` verbatim (folded from `Meta.delivered_at` in `_row_from_meta`,
same as every other meta-derived field) so the route layer never needs a
second `meta.json` read to compute `lead_seconds` for list rows.

### Archived rows: version bump 4 → 5

Adding `active_closed_seconds`/`active_open_since`/`delivered_at` to
`WorkspaceRow` bumped `_BLOB_VERSION` 4 → 5, following the same discard-whole-blob-
on-mismatch precedent as the two migrations above. Unlike the `project_id`
precedent (where a missing field's pydantic default was actively *wrong*), a
`None` default here is not misleading by itself — but the bump still buys
something real: because `_active_time_fields` is a plain in-memory fold over
already-loaded `inv_summaries` (not gated by `compute_costs`), forcing every
archived row through one fresh `_row_from_meta` re-derivation lets historical
workspaces that never went through this feature's code path still get a
correctly-derived `active_closed_seconds`/`active_open_since` from their
existing `workflow.yaml` timestamps — data that was always on disk, just
never folded before. `delivered_at`/`lead_seconds` genuinely stay `null` for
that historical population regardless of the bump, since no code ever wrote
`Meta.delivered_at` for them; that empty state is expected, not a defect.

## Size class (`alan/workspace_size.py`) and version bump 5 → 6

`WorkspaceRow.size_class` (`alan.workspace_size.SizeClass | None` — `XS`/`S`/`M`/`L`/`XL`,
or `None` when unsized) is the size-and-delivery-time feature's only new persisted field,
and `workspace_size.py` itself is a plain, dependency-free module: an `Enum`, a
module-private `_THRESHOLDS = (3, 6, 10, 16)` ladder, and two pure functions —
`size_class_from_file_count(count)` (the ladder itself: `count <= 3` → XS, `<= 6` → S,
`<= 10` → M, `<= 16` → L, else XL) and `derive_size_class(*, plan_files_to_edit,
architecture_files_to_edit, dag_task_count)`, which tries each signal in order and
returns the first one that's present:

1. `len(set(plan_files_to_edit))` — the `implementation_plan` team's handover carries the
   authoritative, hand-parsed `files_to_edit` list for the workspace's actual plan. This
   is the only measured signal that spreads real workspaces across more than one bucket:
   `test_scenarios` and `decisions` are prompt-length-capped and collapse toward one
   size, and `Meta.tasks`-derived fields are empty on effectively the entire historical
   population (see below).
2. `len(set(architecture_files_to_edit))` — a fallback for the (common) case where a
   workspace never ran `implementation_plan`: the `files_to_edit` field carried on
   whichever handover has `from_team` of `"architecture"` or `"decomposer"`. The
   `architecture` team's own prompt explicitly forbids populating `files_to_edit`
   (decisions only), so in practice this fallback's value is whatever the chain carried
   forward from further upstream (`decomposer._build_keep_handover`/
   `_build_split_handover` copy `prior.files_to_edit` verbatim) — this is a best-effort
   second signal, not a second source of truth.
3. `dag_task_count` (`len(meta.tasks)`) when non-zero — a workspace decomposed into
   multiple `Task`s without ever going through a file-listing plan step.
4. Otherwise `None` — rendered as an explicit "unsized" state, never coerced to a
   default bucket.

**Deriving size is never allowed to read execution outcomes** — no elapsed time, no run
count, no cost, nothing `derive_size_class` couldn't have computed the instant the plan
was written. The size feature's whole point is to report *observed time by planned
size*; letting size leak information from execution would make that report a circular
restatement of its own definition.

`_row_from_meta` (full derivation) computes `size_class` from `guards.
plan_files_to_edit`/`architecture_files_to_edit` (populated by
`_derive_handover_signals`, see "Shape" above) and `len(meta.tasks)`. The warm in-place
refresh path, `_row_with_updated_costs`, recomputes it too — from the same two guard
fields plus the row's already-known `tasks_total` (no fresh `Meta` in hand on that path)
— since a new handover can land without `meta.json`'s stat signature changing, and a
row's size must stay current with the plan signals exactly like `scope_ref` does.

Adding `size_class` bumped `_BLOB_VERSION` 5 → 6, discarding the whole archived blob on
mismatch — the same precedent every prior bump in this file follows. The bump forces
every archived row through one fresh `_row_from_meta` (via the meta-only-reclassify-then-
`backfill_archived` two-step below), which is what lets *already-delivered, pre-existing*
workspaces get a real `size_class` immediately rather than only workspaces created after
this feature shipped — the size statistic would otherwise stay empty for months.

### Archived rows stay provisional until backfilled — and that's now self-healing

A cold-discovered archived directory (`_classify_new` finding a workspace whose
`meta.json` already says `ARCHIVED` — i.e. `_store_row(..., refresh_archived=False)`)
has always produced a *provisional* row: `compute_costs=False` skips the three
newer cost categories, so `total_cost_usd` and `cost_totals` on that row understate the
truth, and the cheap path also skips `_update_inv_summaries`, so `active_closed_seconds`
is wrong for any row without a `meta.delivered_at` timestamp to fall back on — both stay
wrong until `backfill_archived()` re-derives them fully. `size_class` is *not* held
back on this path: `_update_handover_signals` runs unconditionally in `_store_row`
regardless of `refresh_archived`, so `_row_from_meta`'s size derivation from
`plan_files_to_edit`/`architecture_files_to_edit` is already correct on the
cold-discovered row. Before this feature, the cost/active-time provisional state could
persist indefinitely if `backfill_archived()` had already run to completion
(`_archived_complete = True`) earlier in the process's life — a `_store_row` call for a
*newly-discovered* archived directory after that point would add a provisional row but
never trigger another backfill pass, silently leaving that one row's cost (and, for a
row with no `delivered_at`, its active time) blank forever without an operator running
`POST /api/admin/reindex`.

`_store_row`'s cheap (`refresh_archived=False`) branch now clears
`self._archived_complete = False` in the same locked section that stores the provisional
row (invariant comment at that seam records the rationale — this is the CLAUDE.md
concurrency-seam exception). `WorkspaceIndex.validated_rows()` — `refresh()` then
`backfill_archived()` then a locked snapshot of both buckets — is the new read entry
point `repo_status._scoped_rows` calls instead of `rows()`/`archived_snapshot()`
separately, so every read that needs trustworthy archived costs or sizes pays for a
backfill pass first if (and only if) one is owed. `backfill_archived()` was already
idempotent and lock-scoped per row, so two readers racing into `validated_rows()`
concurrently both re-entering `backfill_archived()` is safe — the second call either
finds `_archived_complete` already `True` (no-op) or redundantly re-derives a few rows
that were about to be re-derived anyway. This is what makes archived cost and
`active_closed_seconds` trustworthy without any operator action, restart, or manual
reindex — the brief's "cost values remain correct without operator intervention"
requirement.

## Reverse lookup: `resolve_archived_prep_chat` (session_id → task_id)

`WorkspaceIndex` also holds `_prep_session_index: dict[session_id, task_id]`, an
in-memory-only reverse map from an archived preparation-chat's original session id
(the build-chat/explore-chat id it had before `alan.prep_chat.archive` moved it
under the workspace it produced — see `docs/architecture/prep_chat.md`) back to the
`task_id` of the workspace now serving it. It is what lets `GET
/api/build-chat/sessions/{id}` and `GET /api/explore-chat/sessions/{id}` redirect a
dead draft-session id to `GET /api/workspaces/{task_id}/prep-chat` instead of just
404ing, both routes calling the one shared helper,
`alan.routes._prep_chat_redirect.archived_prep_chat_redirect`.

The map is populated opportunistically, at zero new disk reads, piggybacking on
work this module already does: every full row derivation — `_store_row`'s live and
archived branches, `_refresh_one`'s warm in-place-cost-update path, and
`_derive_archived_row` (the `backfill_archived` path) — already calls
`_update_cost_totals(guards, include_new_categories=True)` for a row it's
recomputing costs for, which (via `alan.workspace_cost.derive_cost_totals` →
`_update_prep_chat_cost_guards`) scans `ws_path/prep-chat/` into
`guards.cost_guards.prep_session_ids` purely to price the `preparation_usd` cost
category. `WorkspaceIndex._record_prep_session(task_id, guards)` reads that same
already-scanned tuple's first element (structurally there is at most one archived
prep-chat per workspace, mirroring `prep_chat.find_archived_session_id`) straight
back out and records `session_id -> task_id`, right after each such derivation.
Deliberately **not** hooked into the `compute_costs=False` cold-discovery path (the
provisional row a freshly-seen already-`ARCHIVED` directory gets, per "Archived
rows stay provisional..." above) — `_update_prep_chat_cost_guards` stays gated on
`include_new_categories` exactly as it already was; this feature adds a reader of
an existing scan, not a new scan of its own.

`resolve_archived_prep_chat(session_id) -> str | None` is the sole public entry
point and the single caller of the private bare lookup (`_prep_chat_task_id`): it
calls `refresh()` then `backfill_archived()` before reading the map, so a session
archived moments ago (the common case — a workspace freshly created and dispatched
from a build/explore chat, still `ACTIVE`) is always found, since `refresh()`'s
live-row scan derives full costs unconditionally on every call. An id that was
never valid, or belongs to no conversation at all, simply isn't a key in the map
either way and the caller 404s exactly as before.

**Surviving a restart:** an already-`ARCHIVED` workspace's prep-session entry
depends on that workspace's row having been derived with `compute_costs=True` at
least once *in this process's lifetime* — `_prep_session_index` is in-memory only,
never part of the persisted `ARCHIVED_ROWS_META_KEY` blob. A routine service
restart (see `docs/architecture/deploy.md`'s note on `KeepAlive`/frequent
restarts) always starts a fresh `WorkspaceIndex` with an empty map, so this can't
lean on the same `_archived_complete` flag that gates the cost-row blob: that flag
is normally loaded straight off the persisted blob as `True`, and `_scan()` never
re-touches an archived directory once it's in `self._archived` (see "Archived
rows: out of every hot path" above), so a `backfill_archived()` gated solely on
`_archived_complete` would short-circuit immediately post-restart and this reverse
index would never repopulate for any workspace archived before the current
process started — exactly the "come back later" case the redirect exists to serve.
`WorkspaceIndex` therefore tracks the reverse map's completeness with its own
`_prep_session_index_complete` flag, independent of `_archived_complete` and
always reset to `False` in `rebuild()` regardless of what the loaded blob's
`complete` field says. `backfill_archived()` only short-circuits when **both**
flags are `True`, so the first `backfill_archived()` call in a fresh process's
lifetime always re-derives every archived row at least once — even when the
persisted blob already reports `complete: true` — which is what lets
`_record_prep_session` run again and rebuild the reverse map before either flag
is set. This trades away part of the blob's restart optimization (one full
archived-row re-derivation pass per process lifetime, instead of zero) for
correctness of the redirect; `POST /api/admin/reindex` remains the escape hatch
for a hand-edited archived `meta.json`, unrelated to this path. A live
(non-archived-status) workspace's prep-chat never had this gap, since its row is
always fully re-derived on every `refresh()`.

## Layering

`workspace_index` sits between `injection` and `handover` in the import-linter
layers (`pyproject.toml`): it imports `handover`-and-below (`workflow`, `workspace`,
`manager_summary`, `queue`) and is imported only by `routes` and by `alan.delivery_stats`
(see `docs/architecture/repo_status.md`'s "Layering" section — `delivery_stats` sits
between `repo_status` and `project_budget`, well above this module, so importing
`workspace_index` from there is an ordinary downward edge). `workspace_cost`
sits immediately below it (still above `handover`), imported by `workspace_index`
to derive `cost_totals` and by `routes/workspaces.py` to synthesize the detail
page's breakdown. `workspace_size` sits immediately below `workspace_cost` — a
leaf module with no internal `alan` imports of its own — imported by `workspace_index`
to derive `size_class` and by `alan.delivery_stats`/`alan.repo_status` to type the
per-size delivery-time statistic. `fleet_timing` sits between `handover` and `workflow` —
below `workspace_size`, imported by `workspace_index` to derive the active-fleet-time
fields above, by `routes/workspaces.py` to derive `lead_seconds`, and by
`alan/teams/deploy_verify.py` to snapshot the settled figure at delivery.
