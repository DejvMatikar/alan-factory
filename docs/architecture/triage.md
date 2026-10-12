# Failure Triage Sweep

**Source:** `alan/teams/triage.py`, `alan/teams/triage_pregate.py`, `alan/reconciler.py`, `alan/dispatcher.py`, `alan/routes/infra.py`

## Overview

`TriageTeam` is a self-rescheduling, read-only, **fully deterministic** sweep that surfaces failure signals across the system: stuck workspace jobs, main-branch CI failures, stalled PRs, recurring log errors, and finisher-rate regressions. It runs as a normal dispatcher job (no separate daemon) and re-enqueues itself after each run. It makes **zero LLM/subprocess-model calls** — see the P3.0 tombstone below.

## Tombstone — the three-role LLM pipeline (retired P3.0, 2026-07-05)

Prior to P3.0 the sweep ran a three-role Haiku pipeline (`failure_scanner` → `pattern_correlator` → `triage_advisor`) gated behind a deterministic pre-gate: when the pre-gate found at least one newly-changed blocked surface, the sweep spent a model call synthesizing a YAML `findings`/`patterns`/`advisory` block into `triage_audit.jsonl`. The 2026-07-04 usage audit found this output was never read by anything — no route, no UI, no downstream team consumed it. Detection (the pre-gate, the sensors, the fingerprinting) was healthy; the accounting layer (the LLM synthesis) was pure cost with no reader.

P3.0 deleted the LLM diagnostic and everything that only fed it: the three role prompts (`_FAILURE_SCANNER_PROMPT`/`_PATTERN_CORRELATOR_PROMPT`/`_TRIAGE_ADVISOR_PROMPT`), `_PROMPTS`, `_format_prior_outputs`, `_build_user_text` (and its LLM-only signal helpers — `_recent_error_rows`, `_is_stuck`, `_linked_pr_states`, `_match_prs_to_tasks`, `_format_snapshot_line`/`_build_snapshot`/`_format_error_block`), `_run_diagnostic`, the LLM-content `_write_audit` payload (`result_preview`/`signals_hash`), and the `haiku`-derived `_MODEL`. `scripts/ab_eval_haiku_triage.py` (the pre-merge sonnet-vs-haiku A/B harness for `_FAILURE_SCANNER_PROMPT`) was deleted outright since its only import was the retired prompt.

Everything the pre-gate actually computes — surface classification, fingerprint/suppression state, health-state computation, finisher-rate regression detection, and learning-metrics derivation — was **preserved unmodified**. Only the unread LLM synthesis on top of it is gone. (The frozen-`deploy_verify` red→green watcher this sentence used to name here was itself removed later — see the tombstone near the bottom of this document.)

## The deterministic sensor node

`ROLE_SPECS` is reshaped to a single deterministic task spec and **keeps its symbol name** (never delete it — `reconciler.py` seeds `tasks=list(ROLE_SPECS)`; an empty/absent spec would mean no enqueued node, i.e. a silent heartbeat death — the precedent is the 2026-07-03 43-hour-dark incident, see `docs/audits/incident-2026-07-03-triage-slot-exhaustion.md`):

```python
_NODE_ROLE = "sensor_sweep"
_MODEL = "none"
ROLE_SPECS: tuple[PendingTaskSpec, ...] = (
    PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=_MODEL),
)
```

`run()` mirrors `alan/teams/dependency_sweep.py`'s no-LLM node shape: `graph.start_node(_NODE_ROLE, ...)` → the deterministic sensor work (`_run_sensors`) → `graph.complete_node(_NODE_ROLE)`, all inside `workflow_lifecycle`. There is no branch — every sweep, whether or not a surface is newly blocked, runs this same single node. `_run_sensors()` preserves the exact pre-P3.0 ordering:

```
gather_all → the guarded finisher-rate step (appends a finisher_regression surface when floor_alert) → compute_health_state → _compute_learning_metrics_safe → decide → (mark_diagnosed if eligible)
```

### Guarded sensor steps

Eleven steps of the sweep are guarded by one shared helper, `_guarded(step, degraded, call, fallback)`, and declared as the eleven rows of the `_GUARDED_STEPS` table (a `_GuardedSteps` named tuple of `_GuardedStep(degraded, log_msg)` rows, in sweep order). A raised exception is logged with the row's `log_msg`, the row's `degraded` name is appended to the sweep's degraded list when it has one, and the step contributes its fallback (no surfaces, or no `finisher_regression` surface) so every later step still runs. `_guarded` is the only code that appends a table-row name. The table holds each row's metadata only — the steps keep their own inputs and outputs and are passed to `_guarded` as closures.

| Row | Degraded name |
|---|---|
| `credential_expiry` (the `check_credential_expiry` hook) | `credential_expiry` |
| `finisher_rate` | `finisher_rate` |
| `retention_capacity` | `retention_capacity` |
| `stale_live_process` | none — logs only, never marks the sweep degraded |
| `driver_loops` (status check) | `driver_loops` |
| `driver_loop_quarantine` | `driver_loops` (same name) |
| `credential_expiry_surfaces` | `credential_expiry_surfaces` |
| `auto_update` | `auto_update` |
| `main_ci_red` (the `on_main_ci_red` hook, right after the surfaces are complete) | `main_ci_red` |
| `health_state` (the save) | `health_state` |
| `reconcile_incidents` | `reconcile_incidents` |

`main_ci_red` is the red-main recovery trigger. When `TriageTeam` is built with an `on_main_ci_red` hook (the production wiring in `routes/infra._build_triage_team` passes `partial(seed_advisory_checks, queue, workspaces_root)`), `_run_sensors` hands it a `RedMainRun(slug, run_id, sha)` for every blocked `main_ci` surface of the sweep — a list that is empty when main is green, so a wired hook is called on every sweep. The hook only enqueues; triage itself stays deterministic and read-only. A raising hook marks the sweep degraded as `main_ci_red` and the sweep continues. See [dep_sweep.md](dep_sweep.md) "Red-main advisory recovery".

`_run_sensors` owns the degraded list and passes it explicitly to `_augmented_surfaces`; nothing is ambient, so a guarded step has no outside-a-sweep mode. The writers that report through the same list — learning metrics, the generic meta save, merge-gate recovery, restart recovery, and the surface-state save — take it as an explicit `degraded` argument, as does the retention hook, and are not table rows. The `config_quarantine_surfaces()` call at the end of `_augmented_surfaces` is deliberately unguarded.

`decide()`'s `eligible`/`new_state` output still gates `_mark_diagnosed` (so the fingerprint-suppression semantics in `triage_pregate.py` are unchanged), but no longer gates whether any work happens — the sensors, health-state write, and learning-metrics computation always run.

The whole `workflow_lifecycle` block plus the audit write is wrapped in an outer `try/finally` so that **a raise anywhere in `_run_sensors` still reschedules the next sweep** — closing a latent gap in the pre-P3.0 code, where a `gather_all`/`decide` exception raised *before* entering the no-op/diagnostic branch would skip `_schedule_next_sweep` entirely. (If the raise happens before `_run_sensors` returns, `new_state` is left `None` and the surface-state save is skipped rather than persisting an empty/partial blob over the real one — but the reschedule always fires.)

## Audit log

Each run appends one deterministic JSON line to `~/.alan/logs/triage_audit.jsonl` (override root with `ALAN_LOGS_ROOT`):

```json
{"action": "sweep_complete", "task_id": "...", "inv_id": "...", "surface_count": 7, "blocked_count": 0}
```

No `result_preview`/`signals_hash` fields — there is no LLM output to preview. The handover's `full_synthesis` is likewise deterministic: `"[SENSOR_SWEEP] surfaces=<n> blocked=<n>"`.

Heartbeat-liveness and GC actions written to the same file:

```json
{"action": "invocation_slots_pruned", "task_id": "...", "removed": 899}
{"action": "handover_slots_pruned", "task_id": "...", "removed": 999}
{"action": "revisit_keys_pruned", "task_id": "...", "removed": 997}
{"action": "seed_triage_failed", "source": "startup|reschedule", "error": "..."}
{"action": "triage_workspace_archived", "task_id": "...", "archived_to": "..."}
```

## Workspace and team label

The triage team owns a single long-lived canonical workspace (title "Failure Triage Sweep") created once at service startup. The workspace is labelled with `meta.team = "triage"` so the route handler can filter it out of the default workspace list, and so `alan/dispatcher.py`'s `_schedule_manager_summary` can skip it (see below).

`GET /api/workspaces` and `GET /api/workspaces/{task_id}` return 404 for triage workspaces by default. Pass `?include=triage` to include them.

## Seeding (startup) + canonical selection and GC

`reconciler.seed_triage_sweep(queue, workspaces_root)` is called from `lifespan` in `alan/routes/infra.py` immediately after `_run_job_reconciler`. `_seed_triage_sweep_impl`:

1. Scans `list_workspaces` for every workspace with `meta.team == "triage"`.
2. **Deterministically selects exactly one canonical workspace** via `_select_canonical_triage_workspace` — `min(workspaces, key=lambda ws: (ws.meta.created_at, ws.task_id))`, i.e. the oldest, with `task_id` as a stable tie-break.
3. **Archives every non-canonical duplicate** via `_archive_duplicate_triage_workspaces`/`_do_archive_triage_workspace`: deletes the duplicate's queue rows (`queue.delete_jobs`), then `shutil.move`s its entire on-disk workspace directory to `<workspaces_root>/_archived_triage/<task_id>-<timestamp>` — a **timestamped backup, not a hard delete**, since the directory carries the duplicate's own handovers/workflow history. Writes a `triage_workspace_archived` audit row. Exception-safe per-workspace (one bad duplicate never blocks archiving the rest or seeding the canonical sweep). This self-heals accumulated `failure-triage-sweep-2..-9` shells (e.g. from a prior startup race) on the very next sweep — no hand-scripted state wipe needed.
4. If a canonical workspace exists, checks for pending or running triage jobs on it; if any exist, returns (no duplicate seeding).
5. If none existed, creates the workspace with `workspace.create(..., team="triage")`.
6. Prunes old invocation slot dirs, all prior handover files, and stale triage revisit-count keys via `triage.prune_triage_invocations` (see "Slot & workspace-meta hygiene" below).
7. Calls `workflow_mod.seed_pending_workflow` with `tasks=list(ROLE_SPECS)` (the single sensor node) and `queue.enqueue` to start the first sweep.

If the impl raises, `seed_triage_sweep` logs the exception **and writes a `seed_triage_failed` audit row** (`{source: "startup", error}`) to `triage_audit.jsonl`.

## Self-rescheduling

After each `run()` — whether it succeeds or raises — `_schedule_next_sweep` is called from a `finally:` block:

1. Verifies the workspace still exists (skip reschedule if deleted).
2. **Checks for pending jobs. If one exists, skips.** This is the guard against duplicate concurrent sweeps.
3. Prunes old invocation slot dirs, all prior handover files, and stale triage revisit-count keys via `prune_triage_invocations` (see "Slot & workspace-meta hygiene" below).
4. Seeds a new `workflow.yaml` with `tasks=list(ROLE_SPECS)` and enqueues the next sweep job with `eligible_at = time.time() + _CADENCE_SECONDS` so the dispatcher will not claim it until the cadence window (30 minutes) has elapsed.
5. Updates `triage_last_scheduled_at` in `queue_meta` (observability only — not read as a scheduling gate).

If scheduling raises, the wrapper logs the exception **and writes a `seed_triage_failed` audit row** (`{source: "reschedule", error}`) so a mid-chain death is visible in `triage_audit.jsonl`, not just the process log.

The cadence deferral is implemented via the `eligible_at` column in the jobs table (the same mechanism used for server-error backoff), keeping the chain alive indefinitely while the dispatcher's existing claim predicate (`eligible_at IS NULL OR eligible_at <= ?`) enforces the cadence.

## Manager-summary suppression

`alan/dispatcher.py`'s `_schedule_manager_summary` — the single choke point for every `trigger_manager_summary` call site (7 sites: recoverable-subtask parks, workspace-fail, DAG-terminal advance, surface-routing errors, task-failure cascades, etc.) — early-returns when `ws.meta.team == "triage"`, before computing `ws_path`/`title`/`status` or calling `trigger_manager_summary`. Every other workspace (including `dependency_sweep`, which has no `meta.team` label) is unaffected. This closes the other half of the P3.0 "zero background-summary calls on the periodic path" requirement: a Haiku manager-summary call on every triage stop/park would otherwise fire every ~30 minutes forever, same as the retired diagnostic, for a summary nothing reads.

## Slot & workspace-meta hygiene

The sweep workspace is the only workspace that lives forever, and it consumes three per-run resources every 30 minutes that every other workspace consumes only a handful of times:

1. **Invocation slots** (`inv_001..inv_999`, `_MAX_INV_SLOT = 999` in `workflow.py`) — a hard lifetime of ≈ 21 days, after which every reschedule and every startup reseed raised `WorkflowError: no free invocation slot` into a broad `except` and the heartbeat died silently (the 2026-07-03 incident, ~43 h dark across multiple restarts; see `docs/audits/incident-2026-07-03-triage-slot-exhaustion.md`).
2. **Handover slots** (`handover_<task>_001..999`, `_MAX_SEQ = 999` in `handover.py`) — the same arithmetic, hit on 2026-07-05 (two days after the inv-slot fix): every sweep completed its sensors but died at `save_handover` with `HandoverError: no free handover slot`, parking the workspace as `crash` and minting one `job_failure_audit.jsonl` row (→ one inbox failure item) per 30-minute tick for three days.
3. **`Meta.revisit_counts` keys** — each sweep runs with `prior=None`, so `chain_id_for` mints a fresh chain id, and triage being terminal means the dispatcher's `increment_revisit_count` else-branch fires every run: one dead `<ws>|<task>|triage|<chain>` key per sweep, unbounded (997 keys ≈ 104K `meta.json` when found).

Both seeding sites therefore call `triage.prune_triage_invocations(ws)` before `seed_pending_workflow`. It runs three prunes, each audited (`{action, task_id, removed}`, written only when `removed > 0`):

- `workflow.prune_invocation_dirs(ws)` keeps the newest `_INV_SLOT_KEEP = 100` slot dirs by mtime and removes the rest (freed low-numbered slots are reused by `create_invocation_dir`, so slot ids wrap — mtime, not the `inv_NNN` sequence, is the age signal) → `invocation_slots_pruned`.
- `handover.prune_handover_files(ws)` deletes **all** of the task's handover files — keep-zero, not keep-N, because `_claim_handover_slot` reuses the lowest free seq while `latest_handover` picks the lexicographically last filename: retaining any old high-seq file would make `latest_handover` return stale data after the next low-seq save. Triage handovers are pure exhaust (`to_team_suggestions=[]` so cross-team compression skips them; neither enqueue site sets `parent_handover_id`), so between sweeps exactly one handover file exists — the latest → `handover_slots_pruned`.
- `workspace.prune_revisit_counts_for_team(ws, "triage")` drops every revisit key whose team segment is `triage` (both the 4-part and the legacy 2-part key formats). Safe because nothing ever routes *to* triage (terminal policy, sweeps enqueued directly), so triage keys are write-only exhaust that no loop-guard read consults → `revisit_keys_pruned`.

Pruning is **triage-scoped by call site**, not wired into `create_invocation_dir`/`save_handover`/the dispatcher: other teams' invocation dirs and handovers are load-bearing for resume and routing (`claude_session_id`, per-role `<role>.result.json` caches, `parent_handover_id` resolution — see `docs/architecture/workflow_visibility.md`) and must never be pruned. Triage is `supports_resume=False`; its old slots are pure exhaust, and the durable record of every sweep lives in `triage_audit.jsonl`, which pruning never touches.

## Routing

`TEAM_TRANSITION_POLICY["triage"] = frozenset()` — the team is terminal. The router emits `done` after the run and workspace transitions to `COMPLETED`. The next enqueued sweep job is placed in PENDING by `_schedule_next_sweep`; the dispatcher transitions the workspace status to ACTIVE when it claims and starts that job.

## Credential-expiry surface (delete-the-inbox-http-surface, 2026-09)

`TriageTeam._augmented_surfaces` folds in one more sensor beyond the pre-gate: it calls `alan.secrets_store.expiring_secrets(now)` directly (no injected constructor hook, unlike `retention_capacity_findings`/`reconcile_incidents`/`run_retention` — `secrets_store` sits low enough in the import-linter layers list that `teams` can import it directly) and maps each returned `SecretRecordMeta` to a `ClassifiedSurface` of kind `credential_expiry`. The `credential_expiry_surfaces` row of `_GUARDED_STEPS` guards only the `expiring_secrets(now)` call: a raised exception is logged, appends `"credential_expiry_surfaces"` to the sweep's explicit degraded list, and contributes `[]` rather than failing the sweep; the mapping to surfaces runs outside the guard. See [health.md](health.md)'s "`credential_expiry`" section for the surface shape, the severity/action pair, the `engine_key`-based hide-scoping mechanism, and the deliberate FYI→`degraded` posture change. This replaces the deleted `/api/inbox` surface's `_credential_expiry_fyi_item`, which was the only place an expiring credential ever reached a person — this sensor is what makes that a must-pass port rather than a dropped feature.

This is independent of the pre-existing `check_credential_expiry` constructor hook (`alan/routes/infra.py`'s `_check_credential_expiry`, wired at `TriageTeam.__init__`), which still runs the *edge-triggered* `alan.secrets_store.due_expiry_warnings` and still writes `credential_expiry_audit.jsonl` on every newly-due warning — that write path is unchanged and untouched by this slice, but the file is now write-only: its only reader was the deleted `/api/inbox` surface's `_credential_expiry_fyi_item`.

## Pre-gate (`triage_pregate.py`) — unchanged in behaviour

The pre-gate runs at the top of `TriageTeam._run_sensors()`. It is entirely deterministic (no model invocations) and read-only, and P3.0 did not modify it.

### Surfaces

The pre-gate classifies these surfaces each sweep (build tasks, per-engine CI/deploy, recurring errors; `gather_all` also appends worker-pool and stale-job surfaces):

1. **Build tasks** — workspaces tied to the implementation/pull-request pipeline. A workspace qualifies if `meta.team` is in `{implementation, implementation_fix}`, or any handover has `from_team` in that set, or any handover has `pr_number` set. Triage workspaces are excluded. For each, the pre-gate reads workspace status, queue job status (failed/running), and PR state via at most two batched `gh pr list` calls. The first (`_fetch_raw_prs`) is the cheap **ref list** — `--state all --search head:teams/ --json number,state,headRefName --limit 100`, about one GraphQL point — and doubles as the merged-ghost index (`_build_pr_number_index`) shared with `/api/health/complete`; `--state all` must stay because the ghost check needs MERGED PRs. The second (`_fetch_open_pr_rollups`) is the **rollup list** — `--state open --search head:teams/ --json number,reviewDecision,mergeStateStatus,statusCheckRollup --limit 30` — and is issued only when at least one PR matched to a live build task is OPEN (`_merge_open_rollups`): `statusCheckRollup` is the expensive GraphQL field (~100 points per list), `reviewDecision`/`mergeStateStatus`/failing-checks only ever matter for OPEN PRs (`_is_pr_approved_unmerged` requires `state == OPEN`), and a `teams/` branch is short-lived so 30 open PRs is a generous ceiling. Rollup rows are merged onto the matched OPEN entries by PR number; an OPEN PR absent from the rollup list (transient `gh` failure, or beyond the limit) keeps its ref fields and reads as unknown review/merge state, which is not a blocker. Before the 2026-09 quota incident a single `--state all` list carried the rollup field for up to 100 PRs on every sweep.

2. **Per-engine CI/deploy health** (`main_ci`/`deploy`) — `gather_all` iterates `alan.engine_config.registered_engines()` and, for each `EngineConfig`, calls `gather_ci_surfaces_for(config)`. An engine whose `EngineConfig.ci` is `None` yields no surfaces at all. Otherwise it always emits one `main_ci` surface — `gh run list --repo <that engine's repo> --workflow <config.ci.main_ci_workflow> --branch main` (for the default `alan` engine this is `tests.yml`) — and, only if `config.ci.deploy_workflow` is set, one `deploy` surface against that workflow (`ci-deploy.yml` for `alan`). A deploy-less engine therefore gets no `deploy` row at all, not an "unknown"/greyed one. Surface ids are namespaced by the full `owner/repo` slug — `main_ci:<slug>` / `deploy:<slug>` — so multiple engines never collide and ids stay deterministic across sweeps (the `SurfaceRecord` first_seen/last_seen bookkeeping and `completed_finding_ids` pruning depend on that stability); `kind` stays the plain `"main_ci"`/`"deploy"` string so `_SIMPLE_SEVERITY_ACTION`/`_KIND_LABEL`/`_OBS_DESCRIBERS` (all keyed by `kind`) are unaffected. Each classified surface's `obs` carries a `"repo"` key (the slug) that `alan/health.py` threads onto `HealthFinding.repo` for the UI; the *unknown*-read path (`_unknown_main_ci`/`_unknown_deploy`, used both when the `gh` call itself fails and when it succeeds but returns an empty run list — `_latest_main_run` returns `None` in both cases) deliberately keeps `obs == {}` with no `"repo"` key, so `triage.py`'s `_is_unknown_main_ci_read` empty-obs guard still works. A failing conclusion (`main_ci`) or a conclusion in `{failure, timed_out, action_required}` (`deploy`) is a blocker; a `skipped` deploy is **not** flagged — that case only happens when the paired `main_ci` is already red, which surfaces that failure on its own.

3. **Recurring background errors** — the same action signature appearing ≥ 2 times in the recent JSONL audit window. A single blip does not count. Failure classification (`_is_failure_line`) is a keyword substring scan over `_FAILURE_KEYWORDS`, minus an exact-whole-name exclusion set `_NON_FAILURE_ACTIONS` checked first: `verifier_settle_fail` is excluded because it records a correct observation that a PR's checks are red, not a failure of the fleet, and counting it manufactured a fleet-wide recurring-error alarm out of ordinary verifier behaviour. `verifier_settle_timeout` deliberately still counts — a timeout means checks never resolved within the verifier's full (roughly hour-long) polling budget, which is a genuine condition, not an observation that a PR is red. The exclusion is whole-name equality, checked before the substring scan: a name that merely contains `verifier_settle_fail` as a substring (e.g. a hypothetical `verifier_settle_fail_extra`) is not in the set and still counts as a failure. A prefix or substring exclusion rule would reproduce the defect being fixed one layer down, or silently swallow unrelated future `verifier_settle`-prefixed actions.

### Blockers (build tasks)

- Workspace status in `{AWAITING_INPUT, ESCALATED, FAILED}`
- Most-recent job FAILED (suppressed when a new RUNNING job exists for the same task)
- PR has failing checks (`statusCheckRollup` conclusion in `{FAILURE, TIMED_OUT, ACTION_REQUIRED}`)
- PR is APPROVED + CLEAN (mergeStateStatus) but not merged — suppressed when a RUNNING job is detected (pr_merge already in progress)

Transient `gh` failures leave the PR obs as all-null (unknown state), which is not classified as blocked.

### Fingerprinting and suppression

Each surface's observable state is hashed (`sha256(json.dumps(obs, sort_keys=True))[:16]`). Per-surface state is stored in `queue_meta["triage_surface_state"]` as a JSON blob with fields: `fingerprint`, `first_seen`, `last_seen`, `last_diagnosed_fp`.

`decide()` marks a surface eligible (`_mark_diagnosed`, which now just advances `last_diagnosed_fp` bookkeeping rather than gating an LLM call) when:
- The surface is currently blocked, **and**
- Its `last_diagnosed_fp != current_fp` (not already recorded for this exact state), **and**
- Its `first_seen` (reset on every fingerprint change) is within the last hour — otherwise it's a **stale drop**.

A surface re-arms (resets `first_seen`, clears suppression) whenever its fingerprint changes.

### Rationale for the pre-gate's existence

Even with the LLM diagnostic retired, the pre-gate's fingerprint/suppression machinery still matters: `compute_health_state` and the finisher-rate/learning-metrics computations run every sweep regardless, but the eligible-surface bookkeeping is what a future reader (see the roadmap's P3.1 typed-inbox item) will consume instead of a prose advisory.

## TEAMS registry

`TriageTeam` is NOT in the static `alan.teams.TEAMS` dict (it needs constructor-injected `Queue`). It is instantiated in the lifespan and merged into the dispatcher's team registry at startup. It is not user-dispatchable because it is absent from `TEAMS`, which is the source of truth for `MANUALLY_DISPATCHABLE_TEAMS`.

## Tombstone: auto re-dispatch of frozen `deploy_verify` escalations (Phase 2, P2.1 — removed)

This sweep used to deterministically check whether a merge was stranded by a red `main` branch (`ci-deploy.yml` triggered only on a successful `Tests` run, so `main` staying red meant `ci-deploy` never fired at all — a "frozen" escalation) and, on `main`'s next red→green edge, re-dispatch every workspace stuck in that state (`TriageTeam._maybe_redispatch_frozen_deploy_verify`, `_redispatch_frozen_deploy_verifies`, `_do_maybe_redispatch_frozen_deploy_verify`, keyed off `queue_meta["triage_main_ci_state"]` and `alan.teams.deploy_verify.FROZEN_DEPLOY_MARKER`).

Deploy verification no longer depends on `main`'s test result at all — `ci-deploy.yml` now triggers on every push to `main` unconditionally, and post-merge confirmation is answered by direct local observation (`docs/architecture/deploy_verify.md`), never by inferring from `ci-deploy` run conclusions. The frozen state this recovery mechanism existed to un-stick is therefore structurally unreachable, and the whole mechanism — the method, its helpers, `_MAIN_CI_STATE_META_KEY`, and the `frozen_deploy_verify_redispatch` audit action — is deleted outright rather than left standing as dead code. `_find_surface`/`_DEFAULT_MAIN_CI_SURFACE_ID`/`_DEFAULT_DEPLOY_SURFACE_ID` survive unchanged — they are also used by the unrelated `_stale_live_process_surface` sensor (`docs/architecture/deploy.md`'s "Stale live process alerting" section), which this removal does not touch.

---

## Architecture decision record

### Options considered (original v0/v1 ADR, retained for history)

Three shapes were evaluated before settling on the self-rescheduling dispatcher-job approach.

**Option A — Extend `maintenance` team (add action authority + trigger)**

- Trigger model: manual dispatch only (user or `principal_proxy`, removed 2026-07-13 — see [principal_proxy.md](principal_proxy.md))
- Action authority: read-only report → could escalate to dispatch
- Invariant risk: adds `TEAM_TRANSITION_POLICY` edges from `maintenance` to fix teams; risks muddying `maintenance`'s single-responsibility (routine upkeep vs. reactive triage)
- Implementation cost: ~1 PR; low gate impact
- Reversibility: high — changes are isolated to `maintenance.py`
- Rejected because: `maintenance` is manually triggered; failure triage needs a periodic sweep that fires without user action, and conflating the two would make the cadence logic awkward.

**Option B — New `incident_triage` orchestrated team (dispatcher-level failure index, cross-workspace)**

- Trigger model: scheduled sweep as a dispatcher job (same shape chosen)
- Action authority: read-only scan → could auto-dispatch to `implementation_fix` / `pr_merge`
- Invariant risk: high — new `TEAM_TRANSITION_POLICY` edges (`triage → implementation_fix`, `triage → pr_merge`) bypass the user-confirmation step; risk of runaway auto-dispatch
- Implementation cost: 3–4 PRs; requires policy-table changes + new orchestrated team class
- Reversibility: medium — policy-table changes affect the whole router
- Rejected for v1 because: auto-dispatch authority makes invariant risk too high before the read-only sweep has proven reliable in production.

**Option C (chosen) — Lightweight `TriageTeam` as a self-rescheduling dispatcher job (read-only)**

- Trigger model: startup seed + self-reschedule after each run (every 30 minutes, via `eligible_at` deferral)
- Action authority: read-only report only; no auto-dispatch
- Invariant risk: none — `TEAM_TRANSITION_POLICY["triage"] = frozenset()` makes it terminal; it can never route to another team
- Implementation cost: 1 PR; zero policy-table changes to other teams
- Reversibility: high — deleting the triage workspace stops the sweep; the dispatcher-team-registry merge at startup is the only coupling point

### v1 → P3.0: from a 3-role LLM pipeline back to zero LLM calls

v1 (retired above) split a single `failure_triage_analyst` role into three roles (`failure_scanner` → `pattern_correlator` → `triage_advisor`) to separate scanning/correlation/advising concerns. P3.0's usage audit found none of that output had a reader, so the pipeline — prompts, inter-role context passing, and the allowlist that scoped it — was deleted wholesale rather than trimmed. The `try/finally` wrapping `_schedule_next_sweep` still lives at the team-level `run()` tail, now widened to also cover the sensor computation itself (see "The deterministic sensor node" above) so a mid-sweep crash still reschedules.

### `TEAM_TRANSITION_POLICY` edges

Currently: `"triage": frozenset()` — terminal, no outgoing edges.

If auto-dispatch authority is added in a future PR (Option B path), the legal edges would be:
- `triage → implementation_fix` (for stuck-job remediation)
- `triage → pr_merge` (for unblocking PRs with green CI and an approved review)
- `triage → maintenance` (for recurring log-error patterns)

Any such extension requires a separate ADR and must add the corresponding `TEAM_TRANSITION_POLICY` entries before dispatching.

### Disposition of `auto-clear-dependabot-pr-batches`

`auto-clear-dependabot-pr-batches` is a single-failure-class sweep workspace created manually; it handles exactly one recurring failure pattern (stale Dependabot batch PRs). It is **not absorbed** into `TriageTeam`. Reasons:

1. It already works and is deployed.
2. `TriageTeam` is intentionally read-only (no `gh pr close/merge` write verbs); absorbing Dependabot cleanup would require escalating triage's action authority.
3. The two can coexist — triage surfaces signals via its deterministic sensors, and the human (or a future auto-dispatch path) decides whether to trigger the Dependabot cleanup workspace.

### Adjacent scope resolved

| Question | Decision |
|---|---|
| Extend maintenance vs. wrap vs. sibling? | New sibling (`TriageTeam`); maintenance unchanged |
| Trigger model decision | Startup seed + self-reschedule (no external cron/webhook needed) |
| TEAM_TRANSITION_POLICY for triage | `frozenset()` — terminal read-only; escalation edges deferred to a future ADR |
| Absorb `auto-clear-dependabot-pr-batches`? | No — keep separate; triage surfaces signals, cleanup workspace acts |
| P3.0 sensor-node shape | Single deterministic `PendingTaskSpec` mirroring `dependency_sweep`'s no-LLM node; `ROLE_SPECS` symbol name preserved |
| Duplicate canonical workspaces | Deterministically selected (oldest `created_at`, tie-broken by `task_id`); non-canonical duplicates archived (timestamped backup), never hard-deleted |

## ADR re-open (2026-09-19) — Option B under bounded authority

Option B above ("New `incident_triage` orchestrated team … auto-dispatch to `implementation_fix` / `pr_merge`") was rejected for v1 with the stated reason: "auto-dispatch authority makes invariant risk too high before the read-only sweep has proven reliable in production." That precondition is now re-examined against measured production data (`~/.alan/queue.db`, taken 2026-09-18) rather than by assertion.

**The precondition is narrowed, not met.** Read literally, "the read-only sweep has proven reliable" is not a claim the numbers support, and this ADR does not declare it met:

- Investigator RCA drafts ever written: **78**, of which **37 verified (47%)**.
- Drafts proposing `root_caused`: **28**, of which **5 verified (18%)**.
- Verified drafts whose citations still resolve against today's tree: **3 of 37**.
- Incidents that ever received an RCA draft: **58 (7%)**; 52 of those are `escalation`.
- Incident store: **848 records** — 766 never dispositioned, 81 closed and all 81 `by_design`, 0 `auto_disposed`.

47% verified, 18% of proposed `root_caused` drafts verified, and only 3 of 37 stored `verified=True` drafts still resolving today, is not a track record of accuracy. What the evidence *does* support is narrower and structural rather than statistical: the citation resolver is **fail-closed** and demonstrably rejects unsupported diagnoses — 53% of drafts fail `all_resolved` and are stamped `verified=False`. Bounded autonomous action is admissible on the strength of that fail-closed property, not on planner accuracy, and authority is gated on re-verification **at execution time** (see [unblocker.md](unblocker.md)'s "Executor" section, Ruling 9), never on a stored `verified=True` flag or on planner confidence.

**The new authority model.** A bounded-execution unblocker (`docs/architecture/unblocker.md`) is authorized to run second, after the mechanical recovery ladders in `merge_recovery.py`/`_pr_reconcile.py`/`_pr_flake_ledger.py` have already run, against exactly the six incident classes the investigator already covers (`escalation`, `terminal_failed`, `terminal_partial`, `park_crash`, `park_routing_error`, `park_stall` — see [investigator.md](investigator.md)). It is capped by four ceilings (2 per fingerprint, 5 per workspace, $250, 10 invocations, plus a separate 2-withdrawal + 2-quarantine bar-lowering cap), all counted on attempt rather than success and immune to re-dispatch reset. Every direct-API move — option selection, clause withdrawal, flake quarantine, proceeding past `pre_existing_red` — re-runs the citation resolver against the live tree immediately before acting and refuses a plan whose citations no longer resolve or whose base SHA has moved; a stored `verified=True` is a filter for which plans are worth re-checking, never the authority itself. When the unblocker genuinely cannot proceed, it produces a capability-gap terminal artifact and dispositions `needs_human` — it never self-re-arms. **Ruling 11 (disclosed deviation from the original brief):** the per-workspace ceiling is keyed on `workspace_key` (`ws.task_id`), not the brief's literal `scope_ref` — `alan/incident.py:107-109` derives `scope_ref` as `f"ws:{ctx.ws.task_id}"` before a PR exists and `f"pr:{pr}"` once one does, so a `scope_ref`-keyed ceiling would silently reset the moment a pre-PR unblock attempt opens a PR, defeating the backstop the ceiling exists to provide. See [unblocker.md](unblocker.md)'s "The four ceilings" section for the full argument. Full design: [unblocker.md](unblocker.md).

`triage.md:218`'s second precondition — a separate ADR plus the corresponding `TEAM_TRANSITION_POLICY` entries before dispatching — is mechanical, not a judgment call, and is satisfied by a four-surface change: `alan/router.py`'s `TEAM_TRANSITION_POLICY` (derived from `EngineConfig.transition_graph`, `alan/router.py:15-18`), `alan/engine_config.py`'s `_TRANSITION_GRAPH`/`transition_graph` default, `alan/web/src/api.ts`'s corresponding type assertion, and the drift fixture that guards the mirror between the two (`tests/test_router.py` / `tests/test_dispatch_gate_invariant.py`). See [unblocker.md](unblocker.md)'s "Import-linter placement" and slice-3 plan for the concrete edits.
