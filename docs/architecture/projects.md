# Projects — thin project entity (P4.1 / Phase 4 T1)

## Overview

`alan/project.py` gives the feature/vision spanning many chats and workspaces an actual object:
`Project` (`id`, `title`, `status`, `doc_ref`, `created_at`, `updated_at`). There is **no
`workspaces` field** — a persisted ordered-ref list would be a second source of truth that drifts
every time a workspace changes status or a sibling merges. Instead, `GET /api/projects/{id}` derives
the workspace ledger fresh on every request from `alan.workspace.list_workspaces()`, filtered by
`project_id` and sorted by `created_at`. Derived state can't drift; it costs one `list_workspaces`
call.

## Storage — pure leaf, mirrors `workspace.py`

`alan/project.py` is a pure import-leaf: stdlib + pydantic only, zero `alan.*` imports (import-linter
layer `project`, placed directly above `queue` in `pyproject.toml`'s `[[tool.importlinter.contracts]]`
layers list — the same position class as `workspace`). It mirrors `workspace.py`'s storage half:

- `alan.roots.projects_root()` — default `~/.alan/projects`, override via `ALAN_PROJECTS_ROOT`, resolved at call time (`project._root` delegates to it directly; there is no local `PROJECTS_DIR` constant).
- `<id>/project.json`, atomic tmp write (`_write_json`, the same tmp-then-`Path.replace` pattern as
  `workspace._write_json`).
- `id` claimed via the same slug + atomic-mkdir-loop pattern as `workspace._claim_workspace_dir`
  (`_claim_project_dir`), so a title collision appends `-2`, `-3`, … rather than overwriting.
- Defensive load: `_read_project` raises `ProjectError` on a malformed `project.json`, mirroring
  `workspace._read_meta`'s `WorkspaceError` on `ValidationError`.
- `create`/`read`/`list_projects`/`update`/`delete` are the only mutators — `update(project, updates,
  *, root=...)` takes a plain `dict[str, Any]` of the fields to change (built by the route layer via
  `PatchProjectBody.model_dump(exclude_unset=True)`) and `model_copy`s it onto the loaded entity, so
  omitted fields are left untouched. `delete(project_id, *, root=...)` `shutil.rmtree`s the project's
  directory outright — see "Delete — block-while-referenced, not cascade" below for the route-level
  guard that decides when it's safe to call.

`Project.status` has three values: `ProjectStatus.ACTIVE` (default), `ARCHIVED`, and `COMPLETED`.
`ACTIVE` ↔ `ARCHIVED` remains a plain field toggled via `PATCH /api/projects/{id}` — zero lifecycle
machinery beyond the list filter described below. `COMPLETED` is the one controlled transition in
the model: it is reachable only through `POST /api/projects/{id}/complete` (see below), and `PATCH`
explicitly rejects an incoming `status=completed` with `400` so `/complete` is the sole entry path
into that state. There is still no project-level DoD/acceptance-gate machinery — completion is
gated purely on the derived workspace ledger being all-terminal, or on an explicit `force` override.
`DELETE /api/projects/{id}` permanently removes a project — see "Delete — block-while-referenced,
not cascade" below for the guard and cleanup it performs.

## `Meta.project_id` — nullable, back-compat-free

`alan/workspace.py`'s `Meta` gained one nullable field: `project_id: str | None = None`. This PR adds
the field only — nothing in `alan/workspace.py` stamps it yet (a later PR wires `project_id`
acceptance into `create_workspace` / `workspace.create()` and both chat session models). Because
`Meta` loads via `Meta.model_validate_json` with a Pydantic default, every existing `meta.json` with
no `project_id` key validates unchanged and reads as `project_id=None` — the same back-compat
mechanism that already carries `team`, `note`, etc.

## `/api/projects` — CRU, not CRUD

`alan/routes/projects.py` mounts `projects_router` at `/api/projects` (registered in `alan/server.py`
alongside the other routers). It is the only module that joins `alan.project` with
`alan.workspace` — `alan/project.py` itself never imports `alan.workspace`.

- `POST /api/projects` — `{title, doc_ref?}` → `201` with the created `Project`.
- `GET /api/projects` — list of `ProjectListEntry` (U5.2b): every `Project` field plus two
  additive, derived fields, `workspace_count: int` and `total_spend_usd: float` (an explicit field
  list, not a `Project` subclass — mirrors `ProjectDetail`'s "derive, don't persist" stance).
  `?include=archived` includes archived projects; the default response excludes any project whose
  `status` is `ARCHIVED` only — `COMPLETED` projects remain visible by default (the "list filter"
  the status field exists for is archival, not completion), mirroring `GET /api/workspaces`'s
  existing `?include=triage` convention in `alan/routes/workspaces.py`. `workspace_count`/
  `total_spend_usd` come from `alan.workspace_index.project_aggregates(rows, archived)`, resolved
  through `WorkspaceIndexDep` and called exactly once per request regardless of project count — an
  O(W) fold over the rows the read model already loaded, not a per-project workspace scan (see
  `docs/architecture/workspace_index.md`). A project with no workspaces gets `workspace_count=0`,
  `total_spend_usd=0.0`. These two figures fold the same `total_cost_usd` field, over the same
  active+archived row buckets, that `Project.tsx`'s `useProjectBurn` sums client-side from two
  `GET /api/workspaces` fetches — the two are parity-tested
  (`test_list_projects_total_spend_matches_project_detail_ledger_burn`,
  `test_list_projects_workspace_count_matches_project_detail_ledger`,
  `tests/test_routes_projects.py`) rather than sharing code, since one runs server-side over
  `WorkspaceRow` and the other client-side over `WorkspaceSummary` JSON.
- `GET /api/projects/{id}` — `404` on unknown id; otherwise the `Project` plus a derived
  `workspaces: ProjectLedgerEntry[]` ledger (`task_id`, `title`, `status`, `pr_number`) and a derived
  `ready_to_complete: bool`. The ledger is computed by `_project_ledger`: `alan.workspace.list_workspaces()`
  filtered to `meta.project_id == id`, sorted by `meta.created_at`. `pr_number` is the **last** number
  `alan.workspace.pr_numbers_from_tasks` extracts (`PR #(\d+)`, `re.finditer` — every reference in
  every DoD-clause's evidence, not just the first per clause) across the workspace's tasks —
  **no GitHub call**, since the DoD-clause evidence string (`"PR #7"`) is already stamped onto the
  workspace by the existing `record_task_evidence` / self-heal / health-complete code paths.
  `pr_numbers_from_tasks` is the single, shared definition of this extraction — see "PR-number
  extraction" below for why it lives in `alan/workspace.py` and not here or in `triage_pregate.py`.
  `ready_to_complete` is `_project_readiness(ledger)` — `True` iff every ledger entry's `status` is
  terminal (`COMPLETED`/`ARCHIVED`), including vacuously `True` for a project with no workspaces yet —
  the same predicate `POST /{id}/complete` gates on.
- `PATCH /api/projects/{id}` — per-field update of `title`/`status`/`doc_ref`; `404` on unknown id.
  An unrecognized `status` value is rejected automatically by Pydantic/FastAPI request validation
  (`422`) before the handler runs. `status=completed` is rejected explicitly by the handler (`400`)
  — `POST /{id}/complete` below is the only way into that state. Symmetrically, once a project's
  *current* status is already `completed`, any incoming `status` field (`active` or `archived`) is
  rejected with `409 project_already_completed` — `completed` is a one-way door once reached, so
  archive/unarchive (see below) never resurfaces a shipped project. A `PATCH` that omits `status`
  entirely (e.g. a title-only rename) is unaffected by this guard and still succeeds against a
  completed project.
- **Archive / unarchive is plain `PATCH`, not a dedicated route.** Unlike workspaces (`POST
  .../archive` and `.../unarchive`), a project's `ACTIVE ↔ ARCHIVED` toggle is just `PATCH
  /api/projects/{id}` with `{"status": "archived"}` or `{"status": "active"}` — no new endpoint was
  added for it. Both directions round-trip freely for an active/archived project (mirrored in the
  web UI's archive/unarchive toggle on `Project.tsx`) and never touch `project.json`'s other fields,
  the project's doc, or its amendment history — archiving is soft and reversible, the opposite of
  `DELETE` below. The one restriction is the completed-is-terminal guard immediately above.
- `POST /api/projects/{id}/complete` — `{force?: bool, reason?: str}` → the completed `Project`.
  `404` on unknown id; `409 project_already_completed` if the project is already `completed`.
  Unforced (`force` omitted or `false`), it succeeds only when `_project_readiness` — every child
  workspace in the derived ledger (the same `_project_ledger` scan `GET /{id}` uses) has a terminal
  `Status` (`COMPLETED` or `ARCHIVED`) — otherwise `409 project_not_ready`. `force=true` bypasses the
  readiness check but requires a non-blank `reason` (`422` on a missing/whitespace-only reason with
  `force=true`). On success the project's `status` flips to `completed` via the same `project_mod.update`
  path as `PATCH`, and `alan.project_doc.append_completion_summary` appends a human-readable summary
  (completion time, force/reason, and each ledger entry's title/status/PR number) into the **Decision
  Log** section only — Discovered Facts, Vision, and Scope are untouched, and no new data is captured
  beyond what the ledger and request already carried.
- `DELETE /api/projects/{id}` — see "Delete — block-while-referenced, not cascade" below.

### Delete — block-while-referenced, not cascade

`DELETE /api/projects/{id}` (`204` on success) permanently removes a project. Archiving
(`PATCH .../{id}` with `status=archived`, and back to `active`) remains the soft, reversible path —
delete is the separate, irreversible one, and the two do not interact: a project need not be
archived first to be deleted.

- **Guard: `409 project_has_workspaces`.** The handler reuses `_project_workspaces` — the same
  `alan.workspace.list_workspaces(root=...)` scan filtered to `ws.meta.project_id == id` that
  `_project_ledger` sorts into the detail-page ledger — rather than authoring a second scan. If that
  filter returns *any* workspace, **regardless of its `Status`** (active, paused, completed, or
  archived all count), the delete is refused with `409 project_has_workspaces` and nothing is
  touched: no workspace is mutated, detached, or deleted, and the project itself is left intact. The
  guard's message directs the caller to the archive path or to detaching/removing the referencing
  workspaces first. This is deliberately block-while-referenced, not cascade (deleting a project
  never deletes its workspaces) and not orphan (a workspace's `project_id` never survives pointing at
  a project that no longer exists) — the only way to make a project deletable is for zero workspaces
  to reference it.
- **Cleanup on success.** Once the guard passes, the handler purges the project's pending amendments
  via `project_amendment_mod.delete_pending_amendments_for_project(queue, project_id)` — an atomic
  read-modify-write under the module's existing `_lock` (the same lock `claim_pending_amendment`
  uses) that drops every pending record whose `project_id` matches and leaves every other project's
  pending records byte-for-byte untouched — and then calls `project_mod.delete(project_id)`, which
  `shutil.rmtree`s the project's directory (`project.json` and, if `ensure_doc` had created one,
  `project.md`) in one shot. `project_mod.delete` mirrors `workspace_mod.delete` structurally: it
  404-equivalents (`ProjectError`) both the up-front missing-directory case and a `FileNotFoundError`
  raised mid-`rmtree` (the race where the directory vanishes between the `is_dir()` check and the
  `rmtree` call), so both the ordinary and racy not-found paths surface as the same `ProjectError`
  the route already translates to `404`.
- **The cross-project amendment *approval* audit log is intentionally unscrubbed.** The purge above
  only touches the mutable pending-amendments blob (`Queue` meta key
  `PENDING_AMENDMENTS_META_KEY`) — it never rewrites `project_amendment_audit.jsonl`
  (`_AMENDMENT_AUDIT_FILE`), the shared, append-only, cross-project history of *already-approved*
  amendments (`record_amendment_approval`). A deleted project's past approvals remain in that log
  exactly as a deleted workspace's history remains in other durable logs elsewhere in the system —
  the audit trail outlives the entity it describes by design; nothing reads that log back through a
  `project_id` that must still resolve.
- **Accepted TOCTOU window.** The guard-then-delete sequence (read project → scan workspaces → purge
  amendments → rmtree) is not wrapped in a cross-request lock. A workspace create request that stamps
  a fresh `meta.project_id == id` after the guard's scan has already returned empty, but before the
  `rmtree` completes, can still lose its parent project out from under it — the same accepted race
  window `workspace_mod.delete`'s own racy-`rmtree` handling documents for workspaces, just one level
  up. This is a deliberate scope choice (matching the workspace-delete precedent) rather than an
  oversight; closing it would require a project-wide lock held across a workspace-create call, which
  no other project/workspace interaction in this codebase does.
- **Chat-session `project_id` refs are out of scope.** Build/explore sessions can carry a
  `project_id` (see "`project_id` threading" above) but are not part of the delete guard — deleting a
  project does not check for, and does not clean up, chat sessions that reference it. A chat session
  whose `project_id` points at a deleted project is left exactly as any other dangling `project_id`
  reference is already handled elsewhere in this codebase: `project_mod.cached_read` fail-opens to
  `None` rather than raising, so a caller resolving that session's project title sees "no project"
  rather than an error. Only the workspace side is guarded, because only the workspace side is named
  in this PR's scope.
- **UI: `alan/web/src/features/project/pages/Project.tsx`.** The project page's archive/unarchive control (a plain
  ghost-styled toggle button, `data-testid="project-archive-toggle"`) is hidden entirely once a
  project is `completed`, matching the backend's completed-is-terminal guard above — the control
  simply doesn't render rather than surfacing a 409 the user can't act on. The delete control
  (`data-testid="project-delete-button"`) checks the same derived `workspaces` ledger the detail
  page already fetched: if it is non-empty the button renders `disabled` with an inline hint
  pointing at archive/detach, so the guard is visible before a click rather than only after a
  failed request; if empty, clicking it opens a confirmation `<dialog>`
  (`data-testid="delete-project-dialog"`, built on the same `activeModalDismissHandlers`/
  `blockedModalDismissHandlers` modal-key convention as the amendment-propose modal) whose confirm
  action calls the new `api.deleteProject`. A `409 project_has_workspaces` surfaced inside that
  dialog (the accepted TOCTOU race above) gets an appended, actionable hint rather than the raw
  code alone. A successful delete navigates to `/projects`.

`test_derived_ledger_cannot_drift` (`tests/test_routes_projects.py`) is the load-bearing proof for
the "derive everything" design: it creates a project and two workspaces stamped with its
`project_id`, mutates one workspace's status and stamps a PR-bearing DoD-clause evidence string on
the other, and asserts the ledger reflects both changes on the next `GET` while `project.json` on
disk is byte-for-byte unchanged — nothing about a workspace status change or a PR merge ever touches
the project's own file.

## `project_id` threading (P4.1 T2) — creation-time association only

A nullable `project_id` is now accepted at every create endpoint and stamped once, at creation —
there is no re-parenting path anywhere.

- **Workspaces:** `CreateWorkspaceBody.project_id` (`alan/routes/workspaces.py`) threads through
  `create_workspace` into `workspace.create(..., project_id=...)` (`alan/workspace.py`), which stamps
  it onto the new `Meta`. `Meta.project_id` itself predates this PR (added in T1) — this PR is the
  first thing that actually sets it to a non-`None` value.
- **Chat sessions:** both surviving conversation-session families share the same `_chat_core.ChatSession`
  model and its nullable `project_id` field — `alan._chat_core.ChatSession.project_id`, threaded
  through `create_session(..., project_id=...)` in `alan/build_chat.py` / `alan/explore_chat.py` (and
  accepted as an optional body field on `POST /api/build-chat/sessions` / `POST /api/explore-chat/sessions`).
- **Back-compat:** every one of these fields is a Pydantic-default `None`, so a `meta.json` or
  `session.json` written before this PR (missing the key entirely) still validates unchanged and
  reads as `project_id=None` — the same mechanism that already carries `team`,
  `origin`, etc. A workspace or session with `project_id=None` matches no project's derived ledger
  filter (`ws.meta.project_id == project_id` is never true against `None`).
- **`ALAN_PROJECTS_ROOT` is now registered in all three isolation surfaces** that every other
  `ALAN_*_ROOT` env var requires (see the GOTCHA in the P4.1 brief): `serverEnv()`
  (`alan/web/e2e/fixtures/server-env.ts`), the module-level `os.environ.setdefault` block in
  `tests/conftest.py`, and `_WORKER_SANDBOX_ROOT_ENVS` in `alan/claude_cli.py` (a production worker's
  `uv run pytest` now redirects `ALAN_PROJECTS_ROOT` into its per-worker sandbox alongside the other
  six roots, so it can never mutate a live `~/.alan/projects`).

## `project_id`/`project_title` on `GET /api/workspaces` (U5.2b) — read-side join

`WorkspaceSummary` (`alan/routes/workspaces.py`) carries the affiliation the sections above only
described as write-side: a nullable `project_id` (copied straight off `WorkspaceRow.project_id`,
itself copied from `Meta.project_id`) plus a nullable `project_title`, resolved at the route layer
by `_resolve_project_titles` via `alan.project.cached_read` and a per-request cache — see
`docs/architecture/workspace_index.md`'s "Project aggregation and the `project_id`/`project_title`
join" section for the full mechanics (memoization, dangling-id behavior, and why the title is never
persisted onto `WorkspaceRow` or the archived blob). Both `project_id: null` (never affiliated) and
`project_id` set with `project_title: null` (affiliated to a project that no longer resolves) are
valid, non-error responses.

## What this PR does not do

- No project-selector UI on workspace/chat creation — `project_id` acceptance is API-only; the
  workspace-create and chat-create UIs do not yet offer a project picker (documented fast-follow).
- No re-parenting: a workspace or chat session's `project_id` is fixed at creation and never mutated
  afterward.
- No project list/detail UI, no "New project" affordance.

## Roadmap ingestion — `alan/project_roadmap.py`, the `roadmap_item_id` stamp, and the status fold

A project owner imports a prepared markdown roadmap once; it becomes a durable, ordered list of
items attached to the project, each carrying a delivery status that is **always derived**, never
stored — the same "derive, don't persist" stance as the workspace ledger above.

### Storage — a second pure leaf beside `project.py`

`alan/project_roadmap.py` is import-linter layer `project_roadmap`, placed directly above `teams`
in `pyproject.toml`'s layers list (it needs to import `alan.project` for `project_dir`, so it must
sit above `project` in the list; "above teams" was the placement instruction). It is otherwise a
pure leaf: stdlib + pydantic + `alan.project` only.

- `<project_dir>/roadmap.json` — one file per project, living under
  `project_mod.project_dir(project_id)`, so it inherits `ALAN_PROJECTS_ROOT` for free. **No new
  environment variable was introduced.**
- `RoadmapItem(id, title, text)` — deliberately **no status field**. `id` is opaque and
  globally-unique (`itm_` + `secrets.token_urlsafe`), minted once at seed time and never
  reassigned. Position in the list *is* the order — there is no separate `order` field on the
  stored model; `GET /api/projects/{id}` derives `order` from list index when it builds the read
  view (see below).
- Atomic writes: the same tmp-then-`Path.replace` pattern as `project.py`/`workspace.py`, guarded
  by a module-level `threading.Lock` (this is a single-process app; the lock is what makes
  seed/reorder/edit safe against concurrent requests, not cross-process locking).
- `parse_roadmap_markdown(markdown) -> list[ParsedRoadmapItem]` is a pure function with no I/O: a
  minimal H2 grammar — `## Heading` (exactly two `#`, not three-plus) starts an item, the verbatim
  text up to the next `## ` heading (or EOF) is its body, prose before the first heading is
  dropped, and a heading whose title is empty/whitespace-only is skipped entirely (its body is
  discarded, not merged into a neighboring item).
- `seed_items`/`import_roadmap` are the only way to create `roadmap.json`, and they are
  **idempotent by construction**: under the lock, if `roadmap.json` already exists they raise
  `RoadmapAlreadyImportedError` rather than overwriting — the route layer turns that into `409`.
  This holds even for a markdown document that parses to zero items; existence of the file, not a
  non-empty item list, is the "already imported" signal.
- `reorder_items(project_id, ordered_ids)` rewrites the stored list in the given order and raises
  `RoadmapReorderMismatchError` unless `ordered_ids` is exactly a permutation of the current item
  id set (same length, same set) — it can only ever change *position*; it looks items up by id and
  copies them verbatim, so `id`/`title`/`text` are byte-identical before and after.
- `edit_item(project_id, item_id, *, title=None, text=None)` patches only the given field(s) on one
  item by id via `model_copy`; the id and every other item are untouched. Raises
  `RoadmapItemNotFoundError` for an unknown id.

### `/api/projects/{id}/roadmap/*` — import, preview, and the derived read

`alan/routes/projects.py` owns all of this; `alan/project_roadmap.py` itself is never imported by
anything except `routes/projects.py` and `alan/server.py` (the amendment-approve branch, below).

- `POST /api/projects/{id}/roadmap/preview` — parses `{markdown}` with
  `parse_roadmap_markdown` and returns the resulting `{title, text}` pairs. Pure preview: no
  write, can be called any number of times, never conflicts with a real import.
- `POST /api/projects/{id}/roadmap/import` — parses and seeds in one call
  (`project_roadmap_mod.import_roadmap`); `201` with the minted `RoadmapItem[]` (ids included) on
  first call, `409` on any repeat — this is the idempotent "import is a once action" guarantee.
- `GET /api/projects/{id}` gained a `roadmap: RoadmapItemView[]` field, computed fresh on every
  request by `_roadmap_items`, which reuses the *same* `list_workspaces()` scan
  `_project_ledger` already performs (via `_project_workspaces`) rather than paying for a second
  full scan. For each stored `RoadmapItem` (in stored order, which becomes `order` in the view):
  - **Join key**: workspaces whose `meta.project_id == project_id` **and**
    `meta.roadmap_item_id == item.id` (`_stamped_workspaces_by_item`) — both fields must match, so
    a stray `roadmap_item_id` stamped under a different project can never leak into this project's
    fold.
  - **`delivered`** iff at least one stamped workspace is terminal (`Status.COMPLETED` or
    `Status.ARCHIVED`) **and** `pr_numbers_from_tasks` extracts at least one PR number from its
    DoD-clause evidence — checked via an explicit `bool(...)` emptiness test, not `any()` on the
    raw number list, so a workspace whose only evidence is `PR #0` still counts (`any([0])` is
    `False` in Python; `bool([0])` is `True`). `ARCHIVED` counts alongside `COMPLETED` because
    `alan/retention.py` auto-archives stale `COMPLETED` workspaces without touching their
    `tasks`/DoD evidence — a delivered item must not regress to `not_started` just because its
    stamped workspace aged out.
  - **`merged_prs`** is the deduped, sorted union of PR numbers across **every** stamped
    workspace, not just the one that flipped `delivered` — this is what makes the "an item can
    accumulate several stamped workspaces over its life (a superseded first attempt, follow-up
    fixes)" case render truthfully: if two different stamped workspaces each landed a PR, both PR
    numbers show up together, not just the most recent.
  - **`in_flight`** iff not delivered and at least one stamped workspace is non-terminal
    (`Status` not in `{COMPLETED, ARCHIVED}`).
  - **`not_started`** otherwise — including the common case of zero stamped workspaces.
  - Status is **never** written to `roadmap.json`; it is recomputed from scratch on every read, so
    a workspace status change or a newly-recorded PR is reflected on the very next `GET` with zero
    migration and zero drift risk, exactly like `_project_ledger`/`ready_to_complete` above.

### `Meta.roadmap_item_id` — a one-field clone of `Meta.project_id`

`alan/workspace.py`'s `Meta` gained `roadmap_item_id: str | None = None`, structurally identical
to `project_id`: nullable, create-time-only (accepted by `workspace.create(..., roadmap_item_id=)`
and threaded through `CreateWorkspaceBody.roadmap_item_id` in `alan/routes/workspaces.py`), never
re-parented, and **not validated against the roadmap store** — creating a workspace with a
`roadmap_item_id` that doesn't (yet, or ever) resolve to a real `RoadmapItem` is accepted silently,
exactly as a dangling `project_id` is. This is deliberate: the stamp is the *only* link between a
roadmap item and the work done for it, so validating it at creation time would create a second
place that could disagree with the roadmap store instead of just being a plain foreign key the
read-time fold resolves (or fails to resolve) on every read. A workspace created without the field
leaves its item's fold at `not_started` (or whatever it already was) — the two states ("stamped"
vs "unstamped") are what the read-time fold branches on, and both are covered by tests
(`tests/test_roadmap_item_id_threading.py`).

### PR-number extraction — one definition, in `alan/workspace.py`

`_PR_NUMBER_RE = re.compile(r"PR #(\d+)")` and `pr_numbers_from_tasks(tasks: list[Task]) ->
list[int]` live in `alan/workspace.py` — the lowest layer in the `[tool.importlinter]` stack,
already imported by every consumer that needs this extraction: `alan/routes/projects.py`,
`alan/workspace_index.py`, and `alan/teams/triage_pregate.py`. Before this PR the regex was
defined twice (`routes/projects.py` and `teams/triage_pregate.py`, first-match-per-clause via
`.search()` in the former, all-matches-per-clause via `.finditer()` in the latter); a third
near-identical copy would have been needed for `workspace_index.py`'s new `pr_number` field.
Consolidating into one `finditer`-based definition means `routes/projects.py` moved from
first-match-per-clause to a strict superset of what it used to extract — a DoD-clause evidence
string referencing two PRs (`"superseded PR #1, landed as PR #2"`) now surfaces both numbers
instead of silently dropping the second (pinned by
`test_ledger_pr_number_picks_last_of_two_refs_in_one_clause` and
`test_get_project_roadmap_merged_prs_includes_both_refs_in_one_clause`,
`tests/test_routes_projects.py`). `_roadmap_is_delivered` also moved from `any(_pr_numbers(ws))`
to an explicit `bool(pr_numbers_from_tasks(...))` emptiness check — `any()` on a list of ints
treats a lone `PR #0` as falsy, which would have wrongly reported a delivered item stamped only
with `PR #0` as `not_started`; the emptiness check keeps this in agreement with the row-level
`pr_number is not None` test `workspace_index.py`'s fold uses (pinned by
`test_get_project_roadmap_pr_zero_is_delivered`). Putting the shared helper in
`alan/workspace.py` mints zero new import edges (all three consumers already import it) and adds
no module to the exhaustive layers list — the alternative, a new bottom-layer module solely to
hold one regex, was rejected as unnecessary indirection for a single function.

### `roadmap_items_total`/`roadmap_items_delivered` on `GET /api/projects` — a fold split across two layers

`ProjectListEntry` gained two additive fields — `roadmap_items_total: int` (`M`) and
`roadmap_items_delivered: int` (`K`) — the list-level "K of M" a repo-table home screen needs per
project without a per-project detail fetch. Composing them touched two modules because the
underlying data spans two import-linter layers that cannot see each other.

**Why the fold is split, in layer terms.** `workspace_index` sits *below* `project_roadmap` in the
CI-enforced `[tool.importlinter]` layers list (`pyproject.toml`) — it cannot legally import
`alan.project_roadmap` and has no notion of a `RoadmapItem`. Before this PR, `WorkspaceRow` also
carried no join key back to a roadmap item at all. So the roadmap-totals fold **cannot** live
inside `workspace_index`'s existing zero-disk-read `_row_from_meta` fold, full stop — not a style
choice, a layer-contract fact. (An earlier draft of this roadmap item described the totals as
derived "in the same `project_aggregates`-style fold"; that phrasing describes an implementation
that cannot exist under the current layer contract and was corrected, not amended — the
requirement binds the fields and their parity tests, not that specific fold's location.) The
resolution: `workspace_index.py` gains the join key (`WorkspaceRow.roadmap_item_id`, copied
verbatim from the now-existing `Meta.roadmap_item_id`) plus enough evidence to know whether a row
*delivered* that item (`status`, `pr_number` — see `docs/architecture/workspace_index.md`'s
"Roadmap delivery evidence" section for `delivered_roadmap_item_ids`), all at zero new disk reads.
`routes/projects.py`, which already imports both `workspace_index` and `project_roadmap`, is the
only layer legally positioned to join the two: it composes `delivered_roadmap_item_ids(rows,
archived)` against one `project_roadmap.read_items(project_id)` call per project inside
`_roadmap_totals`.

**This is a strict improvement over the counterfactual naive implementation, not a regression.**
`GET /api/projects/{id}` (detail, one project) already derives roadmap status via
`_stamped_workspaces_by_item` → `_project_workspaces` → a full `alan.workspace.list_workspaces()`
scan of the entire workspace root, filtered down to one project's `project_id` after the fact. Had
this PR reused that same detail-page fold to add totals to the *list* endpoint, it would cost
N × (full workspace-root scan) for N projects — exactly the N+1-shaped cost class
`WorkspaceIndex`/`project_aggregates` already exists to avoid elsewhere on this same endpoint (see
`docs/architecture/workspace_index.md`). The chosen design instead costs N × (one small
`roadmap.json` read), with the expensive part — the workspace scan — paid exactly once, via the
same `index.rows()`/`index.archived_snapshot()` call `project_aggregates` already makes for this
request. A reviewer scanning `list_projects_endpoint` will see a per-project loop with a disk read
inside it and may reflexively read that as an N+1 regression; it is not — the loop reads a
project-scoped roadmap file, not the workspace tree, and N of those was already the cost model
`project_aggregates` set for this endpoint's other per-project figures.

**Why the totals are computed on every request and never cached or persisted.** Every count on
this screen already derives from read models the API serves elsewhere: `WorkspaceRow` (via
`WorkspaceIndex`) and `RoadmapItem` (via `project_roadmap.read_items`). Storing
`roadmap_items_total`/`roadmap_items_delivered` anywhere — on `Project`, in a cache, in a
denormalized store — would create a second source of truth that drifts the moment a workspace's
status or PR evidence changes without a write-time hook remembering to update it, which is exactly
the failure mode `docs/architecture/workspace_index.md`'s "push notifies, read validates" principle
and this repo's §0 "derive, don't persist" rule exist to rule out. The cost of deriving on every
request is one O(W) fold already paid for `project_aggregates` plus one small file read per
project — cheap enough that caching would be optimizing a cost that was never the bottleneck.

**No dedicated read endpoint.** These fields extend the existing `GET /api/projects` response
(`ProjectListEntry`), the same additive pattern `workspace_count`/`total_spend_usd` already
established — no `GET /api/home` or similar was introduced, and none is planned unless this
composition is later measured to be too chatty, which would be an amendment routed back through
the roadmap driver rather than a unilateral change.

### `alan/project_amendment.py` — generalized with a `kind` discriminator + opaque `payload`

Reorder and edit ride the *existing* propose/approve amendment gate instead of a parallel editing
surface. `PendingAmendment` gained two fields: `kind: AmendmentKind` (`"vision_scope"` |
`"roadmap_reorder"` | `"roadmap_edit"`) and `payload: dict[str, Any]` (opaque — the amendment store
itself never interprets it; only the approve endpoint does). `propose_amendment`'s `vision`/
`scope`/`decision_entry` keyword arguments are now defaulted to `""` and a new `kind`/`payload`
pair defaults to `("vision_scope", {})`, so every existing call site (`vision_scope` proposals)
is unchanged.

- `amendment_fingerprint` folds `kind`+`payload` into the hash **only when `kind != "vision_scope"`**
  — for the default kind the hashed raw value is still exactly
  `json.dumps([project_id, vision, scope, decision_entry], sort_keys=True)`, which is byte-for-byte
  the same string the old `json.dumps([project_id, vision, scope, decision_entry])` produced (the
  list has no nested dicts, so `sort_keys=True` is a no-op on it) — **every legacy `vision_scope`
  fingerprint is unchanged**. A non-`vision_scope` kind appends `[kind, payload]` before hashing, so
  a reorder and an edit proposal with the same otherwise-empty vision/scope/decision fields still
  fingerprint differently.
- Two new propose routes on `projects_router` build the payload and call the generalized
  `propose_amendment` with the matching `kind`:
  - `POST /api/projects/{id}/roadmap/reorder` — `{ordered_ids: string[]}` →
    `kind="roadmap_reorder", payload={"ordered_ids": [...]}`.
  - `POST /api/projects/{id}/roadmap/items/{item_id}` — `{title?, text?}` (at least one required)
    → `kind="roadmap_edit", payload={"item_id": ..., "title"?: ..., "text"?: ...}`.
  Both return the same `ProposeAmendmentResponse` shape (`fingerprint`, `project_id`,
  `proposed_at`) the vision/scope propose route already returns, and are gated the same way
  (`404` unknown project, `409` archived project).
- **The single approve endpoint** (`POST /api/projects/{id}/amendments/{fingerprint}/approve`,
  `alan/server.py`) is unchanged in its claim/audit ordering (claim-then-audit-then-apply, so a
  losing race on a duplicate approve never applies or audits) but now branches on `claimed.kind` in
  `_apply_amendment`: `vision_scope` still calls `project_doc_mod.apply_vision_scope_amendment`
  exactly as before; `roadmap_reorder` calls `project_roadmap_mod.reorder_items` with the payload's
  `ordered_ids` (validated through a pydantic `TypeAdapter(list[str])`); `roadmap_edit` calls
  `project_roadmap_mod.edit_item` with the payload's `item_id`/`title`/`text` (validated through a
  small `_RoadmapEditPayload` model). A payload that fails validation, or a reorder whose
  `ordered_ids` no longer matches the roadmap's current id set (e.g. the roadmap changed between
  propose and approve), surfaces as `409` — the pending record has already been claimed (popped) by
  that point, so a failed apply is not retried, matching the existing "approve is claim-once" design
  rather than introducing a new retry path.
- `record_amendment_approval`'s audit line now also carries `kind`/`payload`, so the append-only
  `project_amendment_audit.jsonl` log stays a complete history across all three amendment kinds, not
  just `vision_scope`.

### What this section does not do

- No propose-side validation that a `roadmap_edit`/`roadmap_reorder` payload is internally
  consistent with the *current* roadmap state at propose time — that check happens once, at
  approve time, in `project_roadmap.py` itself (`RoadmapReorderMismatchError`/
  `RoadmapItemNotFoundError`), so a stale proposal fails loudly on approve rather than silently on
  propose.
- No UI for the import wizard, the project-page items section, or the reorder/edit review surface
  — this is backend contract only; the guided import flow and items section are a separate,
  UI-focused build on top of this API.
- No automatic dispatch of the "next" roadmap item, and no roadmap-level budget tracking —
  explicitly out of scope. (Budget tracking shipped separately — see R6.3 below.)

## R6.3 — project budget enforcement, `alan/project_budget.py`

Before this PR, `Project.budget_usd` was purely advisory: it was displayed and already consulted at
the two unattended-continuation seams (`alan.auto_continue.maybe_auto_continue`,
`alan.project_auto_continue.maybe_advance_project`), but a project that had burned through its
budget could still be offered new roadmap work by the human-facing propose-mode card and could still
be started through the one-tap `POST /{id}/roadmap/advance` door. This PR closes both holes without
touching either existing unattended seam's own behaviour.

### `alan/project_budget.py` — the one place burn and budget state are resolved

A new pure-ish leaf module (import-linter layer `project_budget`, placed directly below
`auto_continue` and `project_auto_continue` — both now delegate to it — and above everything the
existing burn fold needs, in particular `proposal_items`):

- `BudgetState = Literal["normal", "approaching", "paused"]` and `WARN_FRACTION = 0.8` — a
  module-level constant, not a `Project` field, a `PATCH` knob, or an `ALAN_*` env var; there is no
  per-project configurable threshold.
- `budget_state(burn: ProjectBurn | None) -> BudgetState` — pure: `None` or `budget_usd is None`
  → `"normal"`; `remaining_usd <= 0` → `"paused"`; `burn_usd >= WARN_FRACTION * budget_usd` →
  `"approaching"`; else `"normal"`. `"paused"` is checked before `"approaching"`, so crossing the
  hard limit supersedes the soft warning rather than showing both.
- `resolve_project_burn(project_id, workspace_index) -> ProjectBurn | None` **delegates to
  `alan.proposal_items.resolve_project_burn`** — the same fold `GET /api/projects` list aggregates
  already use — so `total_cost_usd` is never re-summed a second, possibly-divergent way. Fails
  *open* exactly like the pre-existing `_budget_exhausted` did: `None` when the caller has no cached
  `WorkspaceIndex` (burn requires a fleet-wide row scan), and `None` when the underlying
  `project_mod.cached_read` fails open on an unresolvable `project_id` — a `None` burn reads as
  `budget_state("normal")`, i.e. not exhausted, so an unresolvable burn never blocks new work.
- `resolve_burn_for_project(project, workspace_index) -> ProjectBurn | None` is the same fold for a
  caller that already holds the loaded `Project` (every loop site iterating `list_projects()`):
  it skips straight to `proposal_items.burn_for_resolved_project` rather than re-reading
  `project.json` a second time through `resolve_project_burn`'s own `project_id` lookup.
- `resolve_budget_state`/`budget_exhausted` compose `resolve_project_burn` + `budget_state`.

`alan.auto_continue._budget_exhausted` is now a one-line delegate to
`project_budget.budget_exhausted`, and `alan.project_auto_continue` imports `budget_exhausted`
directly from `project_budget` instead of the old cross-module private import of
`auto_continue._budget_exhausted` — both existing seams (`maybe_auto_continue`,
`maybe_advance_project`) are otherwise untouched and stay byte-identical in behaviour.

### Two new enforcement points

1. **Propose-mode's "next: item N" card** (`alan.project_auto_continue.propose_next_item_proposal_item`,
   renamed from `propose_next_item_inbox_item` when the inbox HTTP surface that used to aggregate this
   card fleet-wide was deleted — see [auto_continue.md](auto_continue.md)) gained an optional
   `workspace_index: WorkspaceIndex | None` keyword. When supplied and the project
   is budget-exhausted, the function returns `None` instead of a card — checked *after* the free
   `next_proposable_item` lookup (not before), so a project with no actual roadmap candidate never
   pays for a burn resolution it doesn't need. The check itself calls `resolve_burn_for_project` with
   the `Project` the caller already loaded (rather than `resolve_project_burn`'s `project_id` path),
   so it never pays for a second `project.json` read either.
   `GET /{id}/roadmap/next-item-proposal` (`alan/routes/projects.py`) threads a `WorkspaceIndexDep`
   through to the same function (so a budget-paused project's direct hit on this endpoint is nulled
   too) and now returns a `budget_state: BudgetState` field alongside `proposal` on every response.
2. **`POST /{id}/roadmap/advance`** gates *at the route*, after the existing archived-project 409 and
   before calling `advance_project`: `project_budget.budget_exhausted(project_id, index)` (via the
   route's own new `WorkspaceIndexDep`, added from the existing `_deps.py` dependency — `_deps.py`
   itself is untouched) raises `409` with `_BUDGET_PAUSED_DETAIL` ("project budget is exhausted") —
   a detail string distinct from `_NO_NEXT_ROADMAP_ITEM_DETAIL`, so a budget-paused project is never
   confused with a project whose roadmap is simply finished. `advance_project`'s own signature and
   its `None` return semantics (still meaning "no un-attempted item") are untouched; the route-level
   409 writes **no** audit row — `maybe_advance_project` remains the single owner of both the
   unattended budget check and `_record_budget_skip_audit`.

Explicitly **not** gated (budget is a brake on new automated work, not a system-wide kill switch):
build-chat/explore-chat dispatch, the decomposer, human re-dispatch of existing work, and anything in
the per-invocation execution path — a workspace already running always finishes through to its PR.

### `ProjectDetail.budget` — the ops-console read

`GET /api/projects/{id}` gained `budget: ProjectBudgetView | None` (`state`, `burn_usd`,
`budget_usd`, `remaining_usd`), computed by `_project_budget_view` off the route's own
`WorkspaceIndexDep`. It is `null` exactly when `project_budget.resolve_project_burn` fails open (the
project resolved fine for the rest of the response via `_read_project_or_404`, but the *separate*
internal `cached_read` inside burn resolution hit a transient failure) — a visible unknown state, not
a silently-hidden section, is the point: an enforcement signal must never vanish.

### Tombstone — the soft-warning and advance-skip `fyi` cards (removed, 2026-09)

`alan/routes/inbox.py` used to add two pull-computed `fyi` cards: a soft budget-approaching warning
(`_budget_warning_items`, recomputed from current spend on every feed build, no persisted
"already warned" flag) and an advance-skip notice (`_project_advance_skip_fyi_items`, rendering a
skip row from `PROJECT_ADVANCE_AUDIT_FILE` as a card linking to `/projects/{project_id}`). Both were
deleted with the rest of the inbox HTTP surface (delete-the-inbox-http-surface, 2026-09) and were
not ported anywhere — unlike the credential-expiry finding (see [health.md](health.md)), a health
finding was not the recommended port for either signal in that slice. `PROJECT_ADVANCE_AUDIT_FILE`
(`project_advance_audit.jsonl`) still gets a skip row on every budget-exhausted unattended advance —
the write path in `alan/project_auto_continue.py` is unchanged — but the file now has no reader
anywhere in the codebase.

### Deferred — conversational/chat spend

`burn_usd` still folds only workspace (execution) cost — build-chat/explore-chat run
summaries are not yet included, and `Project.tsx`'s "Execution spend only" caption stays accurate and
unchanged. Folding chat spend in safely (without an unguarded, per-request fleet-wide chat-transcript
re-parse) is a parked fast-follow.
