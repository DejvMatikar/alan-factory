# Auto-continue

## Purpose

`alan/auto_continue.py` lets a project opt out of the human-in-the-loop propose/approve inbox flow. When a workspace lands on a stuck/terminal status and its project's `autonomy` dial (P4.4, `alan/project.py`) is `"auto_continue"`, `maybe_auto_continue` dispatches the next team unattended instead of waiting for a human to approve it from the inbox.

It is a policy gate over the *existing* dispatch path — it reuses propose-mode's deterministic `propose_team` resolution (no model call to pick the team) and `alan.dispatch.dispatch_team_core` to actually enqueue, so there is no separate auto-continue dispatch mechanism to keep in sync.

## Entry point

```python
maybe_auto_continue(
    ws: Workspace,
    queue: Queue,
    pubsub: PubSub,
    enqueue_workflow: EnqueueWorkflowFn,
    *,
    workspace_index: WorkspaceIndex | None = None,
) -> bool
```

Called from `alan._dispatcher_base._schedule_manager_summary` — the same transition hook that fires `alan._dispatcher_base._maybe_trigger_investigator` — for every non-triage workspace that reaches a stuck/terminal status. Returns `True` iff a dispatch was made, which tells the caller to skip triggering the investigator for the same transition (both would otherwise redundantly diagnose a workspace that just moved on).

## Gates, in order

1. **Kill switch** — `ALAN_AUTO_CONTINUE_DISABLE` truthy (`1`/`true`/`yes`/`on`) disables auto-continue fleet-wide regardless of any project's dial.
2. **Project autonomy** — `ws.meta.project_id` must resolve to a `Project` with `autonomy == "auto_continue"`. No project, an unknown project, or `autonomy == "propose"` all short-circuit to `False` (falls through to the normal inbox/investigator path).
3. **Budget headroom (P7.1)** — see below.
4. **Resolvable target** — `_resolve_auto_continue_target` derives the single-workspace incident for `ws` (via `alan.incident.derive_incidents`) and resolves the next team via `propose_team(latest_handover)`, the same deterministic `next_hop`-over-`to_team_suggestions` precedence propose-mode uses. An `ESCALATED` workspace whose latest handover carries a **blocking** open question is never a target — that is a human *decision*, not a rescuable incident, regardless of what team a resolver might suggest. The resolved team must also be in `AUTO_CONTINUE_TEAMS` (a subset of `MANUALLY_DISPATCHABLE_TEAMS`); a team resolvable in propose-mode but not allow-listed here still stops here.

Only after all four gates pass does `maybe_auto_continue` call `dispatch_team_core` and record a dispatch audit row.

## Budget headroom gate

A project can be `auto_continue` *and* have burned through its P4.4 budget dial (`Project.budget_usd`). Dispatching more unattended work past that point is the one case worth pausing for — a human should decide whether to raise the budget, not have the fleet keep spending against it.

`_budget_exhausted(project_id, workspace_index)` reuses `alan.proposal_items.resolve_project_burn` (promoted to a public name — see `alan/project_budget.py`) over the caller-supplied `WorkspaceIndex`'s rows (`index.rows()`, which refreshes, then `index.archived_snapshot()`, which reads the same refresh cycle's archived rows without triggering a second scan), and gates on `remaining_usd is not None and remaining_usd <= 0` — i.e. a project with no `budget_usd` set (`remaining_usd is None`) never trips this gate; only a project with an explicit budget that's fully spent does.

`workspace_index` is threaded from a single source of truth: `alan.routes.infra.lifespan` builds one `WorkspaceIndex` at startup and passes it to the `Dispatcher` constructor (keyword-only, defaults to `None`). The dispatcher forwards it through `_schedule_manager_summary` and `alan.workspace_dag`'s `advance_workspace_dag` / `maybe_surface_stall` / `maybe_recover_frozen_chain` (all the places that can trigger the transition hook) down to `maybe_auto_continue`. When `workspace_index` is `None` (e.g. a test `Dispatcher` built without one), the gate is a no-op and auto-continue behaves exactly as it did pre-P7.1 — this is deliberate: resolving burn requires a full row scan, so it only runs for the opted-in `auto_continue` path off an already-cached index, never as a fleet-wide hot-path scan (the fleet has prior fd-exhaustion history from unbounded scans).

When the gate trips, `maybe_auto_continue` writes a `skipped: true` audit row (see below) and returns `False` — the caller falls through to `_maybe_trigger_investigator`/the normal inbox flow exactly as if no auto-continue target had resolved.

## Audit trail

Every decision — dispatch or budget-skip — appends one JSON line to `$ALAN_LOGS_ROOT/auto_continue_audit.jsonl` (`~/.alan/logs/` if unset) via `alan.teams._audit.append_jsonl_audit`.

A normal dispatch row:

```json
{"task_id": "...", "workspace_id": "...", "project_id": "...", "team": "implementation", "incident_class": "park_crash", "reason": "...", "inv_id": "..."}
```

A budget-skip row carries `"skipped": true` instead of `team`/`incident_class`/`inv_id`:

```json
{"task_id": "...", "workspace_id": "...", "project_id": "...", "skipped": true, "reason": "Auto-continue skipped: project budget is exhausted (remaining_usd <= 0)."}
```

**Write-only since delete-the-inbox-http-surface (2026-09).** `GET /api/inbox` (and the whole
`alan/routes/inbox.py` surface it lived on) is deleted — the `fyi`-item aggregator that used to tail
this file (`_fyi_item`, branching on the `skipped` field to render either the "Auto-continued:
dispatched to X" card or the "Auto-continue skipped: project budget exhausted" card) went with it.
`auto_continue_audit.jsonl` still gets a row on every decision (below) — the write path is completely
unchanged — but the file now has no reader anywhere in the codebase, not just no UI: this is stricter
than the earlier remove-inbox-from-web-ui state (2026-09, prior slice), where the API still derived
and returned the item even though no page rendered it. The Project-screen autonomy help copy already
stopped claiming "Every auto-dispatch shows up as an FYI card in the inbox" in that earlier slice.

## Cross-item advancement

`alan/project_auto_continue.py` generalizes this one level up: from "next step inside this workspace" (`maybe_auto_continue`, above) to "next item in the project's roadmap." When a workspace tied to a roadmap item (`ws.meta.roadmap_item_id`) reaches a **delivered** terminal outcome — terminal status (`COMPLETED`/`ARCHIVED`) AND any handover carrying a merged `pr_number` — the project is considered for advancement to its next roadmap item.

It is a distinct module from `auto_continue.py` on purpose: `auto_continue` is workspace-DAG-scoped (an *existing* workspace, a handover-derived incident, the next *team*); cross-item advancement is project-scoped (a *new* workspace, no handover, the next *item*, seeded straight from the item's stored text). Merging them would overrun this codebase's CCN<8/length<=60 gates and conflate two different failure modes. `project_auto_continue.py` imports and reuses `auto_continue`'s gates (`auto_continue_disabled`, `_project_autonomy_is_auto_continue`, `_budget_exhausted`) rather than re-implementing them — the same pattern `auto_continue.py` itself uses to reuse `alan.dispatch`'s private transition-graph helpers.

### Entry points

- **`next_unattempted_item(project_id, *, workspaces_root=None)`** — the first `RoadmapItem`, in stored order, with zero workspaces ever stamped with its id (`roadmap_item_id`). An item that was attempted but never delivered (failed, awaiting a human, or still in flight) still counts as attempted and is never re-selected — it stands as a human-attention signal instead. Gated on cold-start: a project with *no* stamped workspace anywhere returns `None`, never `items[0]` — the very first roadmap item is never seeded through this path; starting a project's first item stays a human-initiated action outside this module — the project-lifecycle roadmap's L2.2 replaces that human-initiated first dispatch with the L2.1 `ready` gate.
- **`next_proposable_item(project_id, *, workspaces_root=None)`** — like `next_unattempted_item`, but additionally requires every *prior* stamped item to have delivered; returns `None` the moment it finds one that hasn't. This is the propose-mode gate: a project only gets an automatic "next item" suggestion once nothing behind it is stuck. Inherits the same cold-start gate as `next_unattempted_item`.
- **`advance_project(project_id, *, workspaces_root, queue, pubsub, enqueue_workflow)`** — the atomic seed-and-dispatch primitive both call sites below share. Seeds a new workspace (`workspace_mod.create`, stamped with `project_id`/`roadmap_item_id`, title/description = the item's stored title/text) and dispatches it to `_ENTRY_TEAM` ("implementation") directly — no `implementation_plan` step, no AI planning call; the roadmap item's own text is the complete brief. The attempted-check (`next_unattempted_item`) and the seed-and-dispatch both run inside one module-level `threading.Lock`, reading fresh on-disk workspace state (`workspace_mod.list_workspaces`, never a cached `WorkspaceIndex`) — this is what makes two near-simultaneous callers (a human's "advance" tap and the unattended seam) safe against starting the same item twice. If dispatch throws, the seeded workspace is rolled back (deleted) before the exception propagates.
- **`maybe_advance_project(ws, queue, pubsub, enqueue_workflow, *, workspaces_root=None, workspace_index=None)`** — the unattended seam entry point (see below).
- **`propose_next_item_proposal_item(project, *, workspaces_root=None)`** — the propose-mode card, pull-computed on every read from `next_proposable_item` (no persisted pending record, no speculative workspace) — idempotent by construction, since a started item naturally stops being proposable. Built via `alan.proposal_items.next_item_proposal_item`, which uses the `direct_dispatch` action kind (one-tap start) pointed at `POST /api/projects/{project_id}/roadmap/advance`, instead of `link` (navigate-then-decide).

### Dispatcher seam

`maybe_advance_project` is called from `alan._dispatcher_base._schedule_manager_summary` — the same terminal-transition hook `maybe_auto_continue` and the investigator use — strictly *after* `maybe_auto_continue` has been resolved (dispatched or not) for the same transition, inside the `if not auto_continue_mod.maybe_auto_continue(...):` branch. This ordering is what makes "finish this item's within-workspace DAG first, only then consider the next item" hold: if `maybe_auto_continue` just re-activated the same workspace for more work, cross-item advancement is never even considered for that transition. There is no standalone loop — each delivered workspace causes at most one advancement consideration, and the chain re-arms only when the newly-started workspace itself later reaches its own delivered (or non-delivered) terminal outcome through this same seam.

Unattended advancement (`ws.meta.project_id`'s `Project.autonomy == "auto_continue"`) stops — leaving the project to simply rest — under any of:

1. **Roadmap end** — `next_unattempted_item` finds nothing further.
2. **Budget exhausted** — reuses `auto_continue`'s read-and-skip `_budget_exhausted` gate verbatim (no enforcement — see the budget section above); skip is audited.
3. **Non-delivered terminal outcome** — the workspace that just finished was terminal but never merged a PR (failed, or the router parked it awaiting a human). `maybe_advance_project` returns `False` before ever calling `advance_project`, so the item stands as-is; barreling past a stuck item unattended is explicitly wrong.

`_unattended_project_id` also re-reads the project and stops if it is `archived` — a project archived while a workspace it dispatched was still running must not have a new roadmap item seeded for it once that workspace lands, even though the autonomy dial itself was never touched. This mirrors the archived-project rejection the two HTTP endpoints below already apply.

### HTTP endpoints

`GET /api/projects/{project_id}/roadmap/next-item-proposal` and `POST /api/projects/{project_id}/roadmap/advance` both 404 on an unknown project and 409 on an archived one (`project.status == "archived"`), the same rejection every other project-mutation/proposal endpoint in `alan/routes/projects.py` applies — an archived project must never surface a "Start" decision card or accept a tap-to-start. `GET .../next-item-proposal` has no frontend caller today; it exists as a single-project lookup, but the card a human actually sees comes from the aggregator below, not this endpoint.

### Propose-mode card surfacing (post delete-the-inbox-http-surface, 2026-09)

The fleet-wide aggregator that used to fold this card into `GET /api/inbox`
(`alan.routes.inbox._next_item_proposal_items`) is deleted along with the rest of the inbox HTTP
surface. `propose_next_item_proposal_item` (renamed from `propose_next_item_inbox_item`, built on
`alan.proposal_items.next_item_proposal_item` — renamed from `alan.inbox_items.InboxItem`/
`alan/inbox_items.py`, which is itself renamed to `alan/proposal_items.py` and shrunk to the five
symbols `project_auto_continue`/`project_budget` still import) now has exactly one live caller: the
per-project `GET /api/projects/{project_id}/roadmap/next-item-proposal` lookup endpoint (below),
which has no frontend caller today. The card is no longer reachable from any fleet-wide feed.

A bug inside `maybe_advance_project` is caught and logged by the dispatcher seam (`# noqa: BLE001` — best-effort, must never crash the worker or block the manager-summary/investigator work that already ran for the same transition).

### Audit trail

Every advancement decision — a dispatch or a budget-skip — appends one JSON line to `$ALAN_LOGS_ROOT/project_advance_audit.jsonl` (`~/.alan/logs/` if unset), via the same `alan.teams._audit.append_jsonl_audit` helper `auto_continue.py` uses. No new user-facing configuration setting is introduced; the kill switch and the budget dial are the same ones `auto_continue.py` already exposes.

### Import-linter placement

`alan/project_auto_continue.py` sits in `[tool.importlinter]`'s layers list between `dispatcher` and `auto_continue` — a layer `dispatcher.py` can call into (the seam), that can itself call into `auto_continue.py`, `alan.dispatch`, `alan.proposal_items`, `alan.project_roadmap`, `alan.workspace`, and `alan.project` (all lower layers).

## Re-dispatch safety

`maybe_auto_continue` derives its incident straight from the on-disk workspace state at call time, so a repeat call for a workspace that `dispatch_team_core` already advanced out of a stuck status (see `alan.incident.STUCK_WS_STATUSES`) naturally resolves no incident and returns `False` — there is no separate de-dup bookkeeping. This only holds because every status `dispatch_team_core`/the router can leave a workspace in is also in `alan.dispatch._WAKEABLE_STATUSES`; `tests/test_auto_continue.py::test_wakeable_statuses_covers_every_stuck_status` pins that invariant.

## Beside auto-continue: the unblock sweep

`maybe_auto_continue` refuses any workspace whose latest handover carries a blocking question — that refusal is the boundary between executing a decision already made and making one. The host-side recipe unblocker ([unblocker.md](unblocker.md)) is the mechanism that does make those decisions, under a verified recipe and a ceiling, and it deliberately runs **beside** this module rather than at its seam: `LoopMixin._maybe_sweep_unblocks` on the dispatcher loop, never inside `_schedule_manager_summary`'s workspace lock. The two never touch the same workspace at once: auto-continue acts on a terminal transition before the workspace is stuck, the sweep only on a workspace that is already stuck with no running job.
