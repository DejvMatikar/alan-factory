# UI/Backend Gap Inventory — iteration 1

> Status: iteration-1 inventory (2026-06-09). The `Recommendation` / `Effort` columns are
> pre-filled suggestions to react to, not decisions. Edit the verdict column, then iteration 2
> turns the FINISH rows into build briefs and the DELETE rows into removal PRs.

## Method

Three-tier cross-reference audit. For each backend surface we diffed three sources against the React client:

- **Tier 1 — orphan endpoints**: route decorators in `alan/routes/*` and `alan/server.py` vs. `api.ts` fetch wrappers + production callers (anchor hrefs, hooks, components). An endpoint counts as orphaned only if no production code path can reach it.
- **Tier 2 — unrendered response fields**: Pydantic response models vs. `api.ts` interfaces vs. actual JSX reads. A field is "dropped" if the backend serializes it but no component renders or branches on it (declared-in-TS-but-unread still counts).
- **Tier 3 — unobservable events/statuses**: `TeamEvent` emit sites in `dispatcher.py`/`reconciler.py` and status enums vs. `workflowLive.ts` handled sets (`NODE_EVENT_KINDS`, `WORKSPACE_LIFECYCLE_KINDS`, the `"stream"` branch). An event is "unobservable" if it falls through `onEvent` entirely, and "payload-dropped" if it only triggers a generic `onWorkspaceChanged()` refetch with its detail fields unread.

**How to read the tables**: `Recommendation` is a pre-filled DELETE (remove dead backend code / stop serializing) vs. FINISH (wire it into the UI) call. `Effort` is S (<½ day, one component/field), M (new small component or event handler + payload typing), L (new cross-cutting surface, e.g. a notification feed). Rationale is one line; edit freely.

Verified against the code as of inventory capture (before the iteration-1 phases below landed): `dag_stall` had **zero** matches in `alan/web/src`; `injection_overflow` was whitelisted in `workflowLive.ts` to trigger a generic refetch, but its `dropped_count` payload was never read by the UI (the event string also appears in tests); `/api/chat/*` had no `api.ts` caller; `revisit_counts` / `team_runs_total` / `detail.history` had no render site in `Workspace.tsx` / `WorkspaceList.tsx`.

---

## Tier 1 — Orphan endpoints

| # | Capability/Gap | Backend location | What the user can't do/see | Recommendation | Effort | Rationale |
|---|---|---|---|---|---|---|
| 1 | `POST /api/chat/sessions` (legacy create) | `alan/routes/chat.py:338` | Cannot create a legacy chat session from UI | DELETE | S | Frontend entry removed 2026-06-02; superseded by open/build/explore namespaces. |
| 2 | `GET /api/chat/sessions` (legacy list) | `alan/routes/chat.py:346` | n/a — list never fetched | DELETE | S | Same legacy router; ResumeDraftsPanel uses namespaced lists. |
| 3 | `GET /api/chat/sessions/{id}` (legacy detail) | `alan/routes/chat.py:363` | Cannot hydrate a legacy session | DELETE | S | Dead with the rest of the legacy router. |
| 4 | `DELETE /api/chat/sessions/{id}` | `alan/routes/chat.py:372` | Cannot delete a legacy session | DELETE | S | Dead legacy router. |
| 5 | `GET /api/chat/sessions/{id}/events` (legacy SSE) | `alan/routes/chat.py:386` | `chat:*` channel events reach no client | DELETE | S | Namespaced surfaces have own `/events`. |
| 6 | `POST /api/chat/sessions/{id}/interrupt` | `alan/routes/chat.py:402` | Cannot interrupt a legacy turn | DELETE | S | Dead legacy router. |
| 7 | `POST /api/chat/sessions/{id}/messages` | `alan/server.py:453` | Cannot send a message on legacy router | DELETE | S | Dead legacy router. |
| 8 | `POST /api/chat/sessions/{id}/propose` | `alan/server.py:501` | Entire legacy proposal-extraction flow unreachable | DELETE | M | `propose_chat_task` retired 2026-06-03; ProposalReady/Workspace/NeedsInfo union also drops. Slightly more wiring to remove cleanly. |
| 9 | `GET /api/explore-chat/consultants` data never reaches UI | `alan/routes/explore_chat.py:144-149` | **Closed in X2.11 PR2.** Every consultant-showing surface (`ConsultantModal`, the command palette's explore step, `ExploreChat`, drafts list, conversation rail, workspace prep-chat link, prep-chat heading) now reads this endpoint through `useConsultants`/`useConsultantChoices`; the hardcoded `CONSULTANT_LIST` is gone. | DONE | — | Wired via `alan/web/src/hooks/useConsultants.ts` and `ConsultantModal.tsx`'s `useConsultantChoices`. |
| 10 | `GET /api/workspaces/{id}/artifacts` has no `api.ts` wrapper | `alan/routes/workspaces.py:563` | — (correction: it IS reachable) | KEEP (no-op) | — | NOT orphaned: `handoverSections.tsx:73` builds the URL into an `<a href>` download link used by `ArtifactsSection`. First-pass flag was a false positive (no `api.ts` fetch wrapper, but anchor-href reaches it). No action; recorded to close the finding. |

---

## Tier 2 — Unrendered response fields

| # | Capability/Gap | Backend location | What the user can't do/see | Recommendation | Effort | Rationale |
|---|---|---|---|---|---|---|
| 11 | `WorkspaceSummary.team_runs_total` not in list table | `alan/routes/workspaces.py:168` (pop. :278) | Can't see accrued team-run count from the list | FINISH | S | One column in WorkspaceList; cheap loop-cost signal. Low value but trivial. |
| 12 | `WorkspaceDetail.revisit_counts` not rendered | `alan/routes/workspaces.py:188` (pop. :506) | No visibility into how close a team is to `REVISIT_CAP` | FINISH | S | Useful for diagnosing loop-guard stalls; small per-team badge on the DAG. |
| 13 | `WorkspaceDetail.history` not rendered | `alan/routes/workspaces.py:192` (pop. :510) | Can't see the append-only event/answer log | FINISH | M | Real observability value (answers, status transitions); needs a small history panel + typing the entries (currently `Record<string,unknown>`). |
| 14 | `WorkflowTask` cache-token + `usage_raw` fields dropped | `alan/workflow.py:69-72` | No cache hit/miss ratio; only input/output/cost shown | FINISH (your call) | S | Low value — prompt-cache breakdown is cost-nerd insight; add to NodeDetailModal usage row only if cost tuning matters. Defensible to skip. |
| 15 | `WorkflowTask.effort` + `claude_session_id` dropped (not even in TS type) | `alan/workflow.py:58,66` | Per-node thinking-effort and resumable session id invisible | FINISH (your call) | S | `effort` is mildly useful (xhigh vs high per node); `claude_session_id` is debug-only. Add both to NodeDetailModal as plain text. Low priority. |
| 16 | `WorkflowTask.stream_path` + `dispatched[]` not rendered | `alan/workflow.py:63-64` | Fan-out relationship not visualized; stream log path hidden | FINISH (your call) | M | `dispatched` could enrich DAG edges (currently derived from `depends_on`); only worth it if fan-out vs dependency divergence matters. `stream_path` is debug-only. |
| 17 | `NodeResultDoc` role/model/completed_at not rendered (only `result_text`) | `alan/workflow.py:574-578` | Per-result role/model/timestamp dropped | DELETE (stop serializing) or skip | S | Redundant with the node card. Lowest value of the set — either drop from the response or ignore. |

---

## Tier 3 — Unobservable events & statuses

| # | Capability/Gap | Backend location | What the user can't do/see | Recommendation | Effort | Rationale |
|---|---|---|---|---|---|---|
| 18 | `dag_stall {reason, workspace_id}` completely ignored | `alan/dispatcher.py:1048` | Workspace silently deadlocks; no banner, no refetch, no reason | FINISH | M | HIGH value. Event isn't in ANY handled set in `workflowLive.ts` (verified: 0 matches) — add to handled kinds AND surface `reason` in a banner. Silent deadlock is the worst failure mode. |
| 19 | `router_escalate/await_human/enqueue/done` `reason`+`next_team` dropped | `alan/dispatcher.py:933` | Terminal `escalated` status shows but never the WHY | FINISH | M | HIGH value. Escalation reason is exactly what an operator needs. Read payload, show reason near StatusBadge / in history panel. |
| 20 | `injection_overflow` `dropped_count` discarded (generic refetch only) | `alan/dispatcher.py:962` | Can't see that injected work was dropped or how much | FINISH | M | Silent data loss. Whitelisted at `workflowLive.ts:72` but `dropped_count` never read — needs a toast/banner. |
| 21 | `queue_paused {reset_at, mode}` SSE event ignored; pause-why is poll-only | `alan/dispatcher.py:575` | Banner only appears after next `/api/health` poll; mode/reason never explained | FINISH | M | Event not in any handled set; QueuePausedBanner derives state from polling. Wire the SSE event for instant + reasoned pause display. |
| 22 | `job_retry_scheduled {attempt, eligible_at}` not surfaced | `alan/dispatcher.py:546` | Transient failure looks stuck; no "retry attempt N at <time>" | FINISH | M | Medium value. Avoids "is it dead?" confusion. Read payload, show a retry countdown/badge. |
| 23 | `job_paused {inv_id, mode}` mode discarded | `alan/dispatcher.py:680` | Can't tell user-Stop vs system pause; no auto-resume hint | FINISH (your call) | S | Low-medium. `mode` discriminator → distinct PAUSED label. Cheap once a payload-reading handler exists. |
| 24 | `job_failed {…, error}` SSE error string dropped | `alan/dispatcher.py:632` | Job-level failure with no matching failed node shows no message | FINISH (your call) | S | Edge case — node `error` from polled doc usually covers it; only matters for pre-doc-update or non-node failures. |
| 25 | `job_started {resume_inv_id}` dropped | `alan/dispatcher.py:766` | Can't distinguish auto-resume from fresh start | FINISH (your call) | S | Low value cosmetic distinction. Defensible to skip. |
| 26 | `workspace_interrupted` / `workspace_resuming` detail unsurfaced | `alan/reconciler.py:328` | No notice that a server restart interrupted+recovered work | FINISH (your call) | S | Low value; refetch already updates state. A "recovering after restart" toast is nice-to-have. |
| 27 | **No event-specific notification surface (toast/activity feed)** | cross-cutting (`workflowLive.ts:142-143`) | All lifecycle events collapse to a silent refetch; no transient signal anywhere | FINISH | L | ROOT CAUSE of rows 19–26. A shared toast/activity-feed sink makes most of the above S-effort follow-ups. Build this first. |

### Tier 3 (cont.) — Health-finding fields (Tier-2 by classification, grouped here because they share one render site)

| # | Capability/Gap | Backend location | What the user can't do/see | Recommendation | Effort | Rationale |
|---|---|---|---|---|---|---|
| 28 | `HealthFinding.kind` never rendered | `alan/routes/infra.py:251` | Can't filter/group/icon findings by category (build_task/main_ci/recurring_errors) | FINISH | S | Drives different remediation semantics; add a category badge in HealthView rows. |
| 29 | `HealthFinding.blocker_flags` never rendered | `alan/routes/infra.py:256` | Can't see WHY a PR is blocked (BEHIND/CHANGES_REQUESTED/failing checks) | FINISH | S | High operator value — precise red gate vs. generic description. Same render site as #28. |
| 30 | Per-finding `HealthFinding.in_flight` never read | `alan/routes/infra.py:258` | Can't mark individual rows in-progress or suppress Remediate when a job is already running | FINISH | S | Only the aggregate count is used (HealthProvider.tsx:45). Per-row flag prevents double-dispatch. Same render site as #28/#29. |

---

## Decision summary

- **Tier 1**: 10 findings → **8 DELETE** (legacy `/api/chat/*` router, rows 1–8), **1 FINISH** (consultants source-of-truth, row 9), **1 KEEP/no-op** (artifacts false positive, row 10).
- **Tier 2**: 7 findings → **1 DELETE-or-skip** (row 17, NodeResultDoc redundant fields), **6 FINISH** (rows 11–16; 14/15/16 explicitly low-priority "your call").
- **Tier 3**: 13 findings → **0 DELETE**, **13 FINISH** (rows 18–30; rows 23–26 low-priority, row 27 is the L-effort enabling surface).

**Totals: DELETE 8 (+1 conditional), FINISH 20, KEEP/no-op 1.**

Recommended sequencing:
1. DELETE the legacy `/api/chat/*` router to shrink surface (rows 1–8).
2. Build the notification feed (row 27, L) to unblock the cheap Tier-3 FINISH rows.
3. Ship the high-value observability gaps: `dag_stall` reason (18), router escalation reason (19), `injection_overflow` count (20).
4. Batch the health-row fields (28–30) in one HealthView edit.

---

## Iteration 1 — remediation build phases (dispatched 2026-06-09)

Three build phases were derived from the findings above and dispatched to the build team as
independent workspaces. They are **file-disjoint** (no overlapping edits), so lanes 1/2/3 run in
parallel; within lane 2, C `depends_on` B (shared frontend files).

> Status legend: **dispatched** = handed to the build team, not yet confirmed merged. The next
> analysis pass should run the *Verify* checks below to confirm each actually landed (a phase may
> have partially landed, been rejected by gates, or drifted from the brief). Update the Status
> column to **landed** / **partial** / **dropped** on the next pass.

| Lane | Phase | Covers (rows) | Scope summary | Status |
|---|---|---|---|---|
| 1 | **A — remove legacy `/api/chat/*`** | 1–8 | Delete `alan/routes/chat.py` + 2 `server.py` endpoints + `__init__`/`__all__` exports + dedicated tests + frontend MSW mock. One coordinated M PR (re-scored from the audit's per-row "S"). | dispatched |
| 2 | **B — workspace notice/activity surface** | 27 | New `WorkspaceNotice` model + `notices` on `LiveWorkspaceState` in `workflowLive.ts`; `WorkspaceActivityFeed.tsx`; capped/reset feed wired into `Workspace.tsx`. The enabler for C. | dispatched |
| 2 | **C — surface stall/escalation/overflow reasons** (`depends_on: B`) | 18, 19, 20 | Handle `dag_stall` (currently in no handled set) + carry `reason`/`next_team` for `router_escalate`/`router_await_human` + `dropped_count` for `injection_overflow`; promote error/warn notices to a badge-adjacent banner. | dispatched |
| 3 | **D — render `HealthFinding` fields** | 28, 29, 30 | Render `kind` (category badge), `blocker_flags` (chips), per-row `in_flight` (in-progress marker + disable remediate) in `HealthView.tsx`. Frontend-only; fields already served + typed. | dispatched |

### Verify (next analysis runs these to confirm landing)

- **A landed when:** `rg -n 'chat_router' alan/server.py alan/routes/__init__.py` returns nothing; `alan/routes/chat.py` is gone; `GET /api/chat/sessions` → 404; `tests/test_routes_chat.py` and the legacy parts of `tests/test_server_chat.py` are removed; backend coverage still 100%.
- **B landed when:** `WorkspaceNotice` + `notices` exist in `alan/web/src/features/workspace/hooks/workflowLive.ts`; `alan/web/src/components/WorkspaceActivityFeed.tsx` exists and is rendered in `Workspace.tsx`; lifecycle events produce feed entries (not just a silent refetch).
- **C landed when:** `rg -n 'dag_stall' alan/web/src` is **non-empty** (was 0 — see Method note); `reason`/`next_team`/`dropped_count` are read from `event.data` for the four target events; `router_enqueue`/`router_done` still produce no notice.
- **D landed when:** `HealthView.tsx` references `kind` / `blocker_flags` / per-finding `in_flight`; the remediate control is disabled/hidden when `in_flight === true`.

### Deferred in iteration 1 (still open — pick up next pass)

- ~~**Tier 1:** row 9 (consultants source-of-truth)~~ — closed in X2.11 PR2: every consultant surface reads `GET /api/explore-chat/consultants` and the hardcoded `CONSULTANT_LIST` is gone. Row 10 is a confirmed false positive (no action).
- **Tier 2:** rows 11–17 (`team_runs_total`, `revisit_counts`, `history`, token/effort/session fields, `NodeResultDoc` extras) — none dispatched; 14–16 explicitly low value.
- **Tier 3:** rows 21–26 (`queue_paused` SSE, `job_retry_scheduled`, `job_paused.mode`, `job_failed.error`, `job_started.resume_inv_id`, `workspace_interrupted` detail) — deferred; these plug into Phase B's notice registry cheaply once B lands, so iteration 2 should batch them onto B.

### Backlog item added by the detached-workers restart-attach block

An attached node's catch-up window should read differently from a fresh run — the reconnecting UI currently shows the same bare "node started" state either way. Cause: attach (`alan.teams._node_attach`) re-emits a plain `node_started` event and no new pubsub event type carries "this is a reattach, not a fresh start" (see `docs/architecture/worker_records.md`#attach). Reference pair for tone/severity: `workspace_interrupted` / `workspace_resuming` (`alan/web/src/components/NodeStatusBadge.tsx:8`, `alan/web/src/features/workspace/hooks/workspaceNotices.ts:29-30`) already distinguish "we lost this" from "we're getting it back" at the workspace level; an attached node wants the equivalent at the node level. Not built here — this PR's scope is backend attach mechanics only.

### Method blind spot to carry into iteration 2 (Tier 4 — unexposed backend state)

The three-tier audit anchors on **endpoints + emitted events**, so it is structurally blind to backend
state that is *never exposed by any endpoint at all* — arguably the largest "user can't observe"
category. Not covered by iteration 1; a separate sweep should inventory:
`~/.alan/logs/job_failure_audit.jsonl`, `predispatch_findings.jsonl`, `node_turns_audit.jsonl`, the
trajectory/learning records, and `queue_meta` keys without a `/api/health` projection. These have no
route to diff against, so a fourth tier (or a "backend artifacts with no read path" search) is needed.
