# Health first-class surface

## Overview

Replaces the triage workspace surface with a deterministic Health concept: sensors compute findings, humans dismiss/restore/complete them directly from the read-only `/health` view. The manual remediate/merge dispatch lane and the `remediation`/`remediation_verify` review teams that once fixed findings automatically were removed in P3.0 (2026-07-05) — see the Tombstone section below.

## Core module: `alan/health.py`

### Constants

- `SWEEP_CADENCE_SECONDS = 1800.0` — owned here; triage imports it as `_CADENCE_SECONDS`.
- `_STALE_SECONDS = SWEEP_CADENCE_SECONDS * 2` — health_state older than this is suppressed at the API layer.
- `_DISPATCHED_META_KEY = "dispatched_finding_ids"` — queue_meta key tracking remediation-dispatched finding IDs.
- `_DISMISSED_META_KEY = "dismissed_finding_ids"` — queue_meta key tracking user-dismissed finding IDs; pruned against live active set in `save_health_state`.
- `_HEALTH_CLEANUP_SENTINEL_KEY = "health_cleanup_v1_done"` — queue_meta sentinel; set by the one-time startup cleanup so it never repeats.

### Types

- `HealthFinding` — dataclass: `finding_id`, `kind`, `severity`, `description`, `workspace_id`, `pr_number`, `blocker_flags`, `action`, `in_flight: bool = False`.
  - `in_flight` is `True` when the finding is a `build_task` with `job_running=True` in its observations. Serialised in `_finding_to_dict`; tolerantly deserialised in `_build_finding` (defaults to `False` when absent).
- `HealthState` — dataclass: `verdict`, `blocked_count`, `in_flight_count`, `findings`, `computed_at`.

### Verdict

- `"healthy"` — no blocked surfaces.
- `"degraded"` — ≥1 blocked surface.
- `"stale"` — not persisted; derived by `is_stale(state)` at the API layer when `computed_at` is older than `_STALE_SECONDS` or has no timezone.

### Severity and action mapping

Derived from `(kind, blocker_flags)`, never from a `kind` dict:

| kind | blocker flag | severity | action |
|---|---|---|---|
| `build_task` | `pr_checks_failing`, work running (`obs["job_running"]` true) | medium | `dispatch_remediation` |
| `build_task` | `pr_checks_failing`, no work running (false or absent) | critical | `dispatch_remediation` |
| `build_task` | `job_failed` | high | `dispatch_remediation` |
| `build_task` | `pr_approved_unmerged` | high | `merge_pr` |
| `build_task` | `ws_stuck` (only) | medium | `deep_link` |
| `main_ci` | any | critical | `dispatch_remediation` |
| `deploy` | any | critical | `dispatch_remediation` |
| `recurring_errors` | any | high | `dispatch_remediation` |
| `forge_degraded` | any | high | `deep_link` |
| `driver_loop_unhealthy` | any | high | `deep_link` |
| `driver_loop_attention` | any | medium | `deep_link` |
| `credential_expiry` | any | high | `deep_link` |
| `host_toolchain` | any | critical | `deep_link` |
| `auto_update` | any (`blocked`/`red` verdict, a rollback, a failed rollback, or a stale status record) | high | `deep_link` |

`build_task` is the third exception to the "derived from `(kind, blocker_flags)`" rule: its severity/action is dispatched from `_surface_to_finding` via `_build_task_severity_action(flags, in_flight, fix_cycle_escalate)`, not from the shared `_severity_action(kind, flags)` dispatcher — `in_flight` is the same `_is_in_flight(surface)` predicate that populates the finding's own `in_flight` field, read once and fed to both, so the two can never disagree. Only the `pr_checks_failing` branch is conditional; `job_failed`, `pr_approved_unmerged`, and the flag-less fallthrough are unaffected by `in_flight` or `fix_cycle_escalate`. An absent `obs["job_running"]` key (rather than an explicit `false`) is treated the same as `false` — unknown holds what it guards, so severity stays critical. The medium-severity/`in_flight`-marker biconditional holds only on this `pr_checks_failing` + `dispatch_remediation` path; it does not apply to the resolvable/`complete_workspace` override (always medium) or the flag-less `deep_link` fallthrough (always medium), both of which are independent of `job_running`. The overall verdict and `blocked_count` (`health.py:405-408`) remain derived from finding *existence*, not severity — a build task whose severity drops to medium because work is running still counts as blocked and still degrades the verdict.

**Fix-cycle escalation (X2 fix-cycle-ceiling-per-pr).** `fix_cycle_escalate` is a third, defaulted (`= False`) parameter on `_build_task_severity_action`, computed once in `alan/teams/triage_pregate.py::_classify_build_task` (via `_fix_cycle_obs`) and carried on the surface's `obs["fix_cycle_escalate"]` — `health.py` reads it off `obs`, it never recomputes it and never imports the threshold constant (`_pr_state_paths.FIX_CYCLE_ESCALATE_AT`, currently 3), keeping `alan.health -> alan.teams.triage_pregate` the single existing import-linter exception rather than adding a second. When `in_flight` and `fix_cycle_escalate` are both true, the `pr_checks_failing` branch reports `critical` instead of `medium` — the count has reached the threshold at which continued autonomous dispatch is no longer treated as routine. The not-running branch is untouched: it was already `critical` regardless of cycle count, and stays that way. `_describe_build`'s existing "— work is running" suffix grows a `(N fix cycles)` parenthetical (via `_fix_cycle_running_tag`) whenever `obs["fix_cycle_count"]` is non-zero, independent of whether the escalation threshold has been crossed. See [loop_guard.md](loop_guard.md)'s "Fix-cycle ceiling" section for the counter itself, its two increment sites, the answerable park at the ceiling, and the override.

`driver_loop_unhealthy`/`driver_loop_attention` are two further exceptions to the "derived from `(kind, blocker_flags)`" rule above: `alan/teams/_triage_driver_loops.py` computes them from `alan.driver_loop_status.snapshot()` directly (overdue/backing-off/fast-forward-failed/last-run-failed for the former; ran-with-denials/held-by-a-live-interactive-session for the latter), and `alan/health.py` maps both kinds straight to a fixed severity with no flag lookup. See [driver_loops.md](driver_loops.md)'s "Alarm" section for the full reason vocabulary, precedence, and the suppression rule that silences both kinds while a loop is `not_supervised`/`disabled`/`paused`.

`main_ci` is sourced from the `Tests` workflow specifically and `deploy` from the `CI Deploy` workflow (`tests.yml` / `ci-deploy.yml`); see [triage.md](triage.md) for why the workflow filter matters (a newer green run of an unrelated workflow used to mask a red `Tests` run). The home strip's Main CI chip reads the same declared `ci.main_ci_workflow`, per-job (check-suite scoped) rather than per-run — see [repo_status.md](repo_status.md)'s "One definition of a red main". The agreement holds **only on the path where that workflow's run resolves**: when it does not, the chip falls back to counting every default-branch check-run, and in that state an advisory-workflow or deploy failure can redden the chip while this surface reports `main_ci` green or unknown. The fallback names itself in the field's provenance detail, which is the thing to read before concluding the two surfaces disagree. The `deploy` describer reads `obs["conclusion"]` and renders "Main branch deploy is failing (…) — live build is stale". Both route through the shared `_OBS_DESCRIBERS` dispatch in `_describe`.

`forge_degraded` is a pure `queue_meta` read (`forge_degraded_v1`, written by the dispatcher's usage-limit handler when a `ForgeRateLimited` trips the queue pause — see [queue_and_dispatcher.md](queue_and_dispatcher.md)), gathered by `gather_forge_degraded` in `triage_pregate`. The surface auto-expires: it reports blocked only while the recorded `reset_at` is in the future, so no explicit clearer exists and staleness is impossible by construction. The describer renders "GitHub API degraded (rate limited) — dispatch paused, auto-resumes at HH:MM UTC".

### `credential_expiry` (delete-the-inbox-http-surface, 2026-09)

Ported from the deleted `/api/inbox` surface, which was the only place an expiring credential ever
reached a person. The `credential_expiry_surfaces` row of `_GUARDED_STEPS` in `TriageTeam._augmented_surfaces` (no injected hook —
it calls `alan.secrets_store.expiring_secrets(now)` directly, sitting below `teams` in the
import-linter layers list) builds one level-triggered `ClassifiedSurface` per record inside the
warning window, `is_blocked=True`, `obs={"name", "expires_at", "engine_key"}` where `engine_key` is
`meta.name.split(":", 1)[0]` (the secrets-store record name's engine half, already the repo slug
`_is_hidden_finding` compares hidden repos against). `secrets_store.expiring_secrets` is deliberately
**not** `due_expiry_warnings` — that function is self-consuming (it stamps `expiry_warned_at` so it
fires exactly once), which would make a credential's finding vanish from `/api/health` after one
sweep tick even though the credential is still unrotated; `expiring_secrets` re-derives the same
window on every call and writes nothing, so the finding stays visible for as long as the underlying
condition holds — the posture `compute_health_state` expects of every other sensor.

`HealthFinding.engine_key` is derived from `obs["engine_key"]` directly when the surface's `obs`
carries one (`_surface_to_finding`'s `engine_key` field prefers `_opt_str_obs(surface.obs,
"engine_key")` over the workspace-lookup fallback `_engine_key_for_workspace`) — `credential_expiry`
surfaces carry no `workspace_id`, so the pre-existing workspace-derived path would otherwise always
resolve to `None` and the finding could never be hidden by repo. Every other kind's `obs` never sets
`"engine_key"`, so this is purely additive: `build_task`'s existing workspace-derived `engine_key`
resolution is unchanged.

This is a deliberate posture change from FYI to `degraded`: the old `/api/inbox` `fyi` item never
raised `needs_you_count` or affected the fleet-wide verdict; a `credential_expiry` finding is
`is_blocked=True`, so it counts toward `blocked_count` and flips `verdict` to `degraded` like any
other blocked surface. An expiring credential is exactly the kind of thing `blocked_count` exists to
surface.

### `worker_pool`

`worker_pool` (`alan/worker_liveness.py`, gathered by `gather_worker_pool` in `triage_pregate`) now self-expires the same way `forge_degraded` does, but on a heartbeat rather than a computed deadline: `queue_meta` key `dispatcher_worker_state_v2` (`WORKER_STATE_META_KEY`) carries a `DispatchLoopReading(live_loops, configured_loops, observed_at)`, republished every 60s by the still-running dispatch loop (`LoopMixin._maybe_republish_worker_liveness`) and on every worker-thread exit (`LoopMixin._on_worker_exit` → `_publish_worker_state`). A reading older than `HEARTBEAT_TTL_S` (180s) is treated by `worker_liveness.classify` as absence of signal, not an alert — a deliberate three-heartbeat safety margin over the republish cadence.

The measured quantity is **dispatch loop threads**, not worker processes: `live_loops`/`configured_loops` are `Dispatcher._alive_worker_count`/`_concurrency`, the count of the dispatcher's own internal polling threads. A worker subprocess that intentionally survives a service restart (`ALAN_DETACHED_WORKERS`, see [worker_records.md](worker_records.md)) is invisible to this count by design, so `classify` corroborates a genuine-looking thread deficit against two independent signals before alerting — `running_jobs > 0` (`queue.list_running()`) and `worker_liveness.probe_active_workers()` (a `worker_records.load_all()` scan for a live, `is_detached_survivor` process) — either one silences the finding. `probe_active_workers` is only invoked once the cheap checks (reading absent, stale, no deficit, running jobs) have all failed, so a healthy steady-state sweep never pays for the filesystem scan.

On a clean `Dispatcher.stop()`, `_publish_worker_state` deletes the `queue_meta` key outright (`self._stop.is_set()` is checked inside the same lock that guards the write) rather than recording the `live_loops=0` value a shutting-down dispatcher would otherwise leave behind — this is what stops an ordinary restart from being read as an active problem by a health sweep that lands in the gap before the next process publishes a fresh reading.

`/api/health` additionally suppresses a `worker_pool` finding at read time (`alan.routes.infra._suppress_worker_pool`) whenever the live in-process `dispatcher.live_worker_count >= dispatcher.configured_worker_count` — a prompt, zero-extra-I/O clear that does not wait for the next triage sweep once the full configured pool is visibly healthy again. A genuine partial deficit (some, but not all, loops live) is deliberately left unsuppressed here, since `live_worker_count > 0` alone would otherwise mask a real standing deficit that `worker_liveness.classify` correctly flagged.

**Accepted non-detection**: a dispatch-loop thread that is alive but wedged (hung inside a single `run_once()` iteration, never reaching the next poll or the republish call) is invisible to this heartbeat, in one of two mutually exclusive ways depending on how many of the configured loops are wedged at once. `_alive_worker_count` is only decremented on an actual thread exit (`_on_worker_exit`), never merely because a thread stopped progressing, so a wedged thread still counts as "live" for as long as it exists.

- **Partial wedge** (at least one loop still healthy): the healthy peer's own `_maybe_republish_worker_liveness` keeps writing a reading with the wedged thread still counted in `live_loops`, so the reading stays fresh and full (`live_loops == configured_loops`) indefinitely — `classify`'s no-deficit check silences the finding forever, and the reading never even approaches the TTL boundary.
- **Total wedge** (every configured loop is wedged): no thread ever reaches `_maybe_republish_worker_liveness` again, so the heartbeat stops being written; once it ages past `HEARTBEAT_TTL_S` with an idle queue, `classify`'s staleness check silences the finding for a different reason — absence of a fresh signal, not a full healthy count.

Either way, `classify` has no way to distinguish "busy" from "stuck" from the heartbeat alone. This is deliberate: the brief's contract is "trustworthy or silent," and building an out-of-process liveness probe for a hung-but-alive thread is a new detection mechanism outside this fix's scope.

### `host_toolchain`

`alan/worker_liveness.py::probe_host_toolchain()` runs a version-probe subprocess against `git` and `python3`, resolved via `shutil.which` and invoked through the same environment the fleet's worker spawn inherits — no env argument is passed, since worker subprocesses inherit the service process's `os.environ` verbatim (`_worker_env` in `alan/claude_cli.py` never touches `PATH`), so plain inheritance *is* the worker's own resolution. On a macOS host with an Xcode licence gate re-armed by a major OS upgrade, every toolchain-resolved binary — including these two — refuses to run, and this is the probe that catches it. `gather_host_toolchain` (`alan/teams/triage_pregate.py`) calls `probe_host_toolchain` (injected as a callable so tests never touch a real host) and classifies the result via `worker_liveness.classify_host_toolchain`.

A non-zero exit, a timeout, an `OSError`, or the tool being missing from `PATH` all collapse into the same gated `ToolProbe(ok=False, message=...)` result — one reporting branch, so the probe never distinguishes *why* a tool failed from *whether* it failed. The finding maps to critical severity and `deep_link` (above), since recovery needs elevated privileges no agent holds — the description names both resolved paths, quotes the failing tool's own message, and states the recovery command `recovery_command()` extracted from that message (the first line starting with `sudo `, or a documented best-effort fallback constant when no such line is present — see `docs/architecture/deploy.md`'s "Host toolchain licence gate" runbook section).

`ClassifiedSurface.fp_obs` carries only `resolved_paths`, `failing_tools`, and the failing message's first line — not the full message — so a volatile trailing detail (a stderr tail that changes run to run) doesn't defeat `triage_pregate.decide()`'s per-`surface_id` fingerprint suppression and re-trigger diagnosis on every sweep for what is really the same standing condition.

The probe holds no persisted state of its own: it is called fresh on every sweep, so a healthy toolchain produces no finding and a repaired one simply stops producing one — the finding clears on the first sweep after recovery, with no explicit clearer, the same posture `forge_degraded` and `stale_live_process` already have.

**Documented limitation.** This probe tests the *service process's* own resolution of `git`/`python3`, not a worker's. A worker ultimately runs tools through a `claude` CLI subprocess whose Bash tool can re-source a shell profile, which can change which binary a name resolves to relative to the service's own inherited environment — in either direction: a profile that puts a working toolchain ahead of a gated one can make the probe report gated while a worker is actually fine (a false positive), and a profile that resolves a *different*, still-gated binary than the one the probe found healthy can make the probe report healthy while a worker is actually blocked (a false negative). Neither direction is bounded away by this probe. That is acceptable for an advisory-only finding — no gate in this repository holds dispatch on any health finding — but it is a genuine two-way limitation, not a one-directional safety margin, and it is recorded here as exactly that.

### Persistence

`save_health_state(queue, state)` serializes to `queue_meta` key `health_state_v1`, appends a run summary and (if findings are present) an escalation entry, and prunes both `dispatched_finding_ids` and `dismissed_finding_ids` against the current active set — this gives re-surface-on-recur for free.

`load_health_state(queue)` deserializes with tolerant field extraction — never crashes on schema evolution, returns `None` on any malformed data.

### Dismiss / restore / clear

- `dismiss_finding(queue, finding_id)` — appends `finding_id` to `dismissed_finding_ids` (idempotent; tolerates corrupt existing JSON).
- `restore_finding(queue, finding_id)` — removes `finding_id` from `dismissed_finding_ids` (no-op if absent or key missing).
- `clear_health_history(queue)` — resets `health_run_summaries_v1`, `health_escalations_v1`, and `dismissed_finding_ids` to `[]`. Does **not** reset the one-time cleanup sentinel.

## Integration: `alan/teams/triage.py`

`TriageTeam.run()` calls `compute_health_state(surfaces, now)` and `save_health_state(queue, health)` through the `health_state` guarded step **before** the no-op / diagnostic branch. Surfaces come from the full `gather_all()` result, not the truncated `eligible` list.

## One-time startup cleanup: `alan/reconciler.py`

`run_one_time_health_cleanup(queue, workspaces_root)` — sentinel-guarded step wired in `lifespan` between the job reconciler and `seed_triage_sweep`:

1. Reads `health_cleanup_v1_done` from queue_meta; returns immediately if already set.
2. If the triage workspace exists: resets its `history.json` to `[]` and deletes all its queue jobs.
3. Calls `clear_health_history(queue)` to wipe accumulated run history.
4. Sets `health_cleanup_v1_done = "1"`.

Exceptions are swallowed (logged at ERROR) so a disk problem never prevents the server from starting.

## API: `/api/health` (GET)

Declared response shape (`alan.routes.infra.HealthResponse`, exported via `alan.server.openapi_json()` — see "OpenAPI export" below), the exact twelve keys the handler serves:
```json
{
  "ok": true,
  "queue_paused": false,
  "paused_until": null,
  "health_state": {
    "verdict": "healthy|degraded",
    "blocked_count": 0,
    "in_flight_count": 0,
    "computed_at": "2026-06-02T12:00:00+00:00",
    "findings": []
  },
  "last_computed_at": "2026-06-02T12:00:00+00:00",
  "dispatch_loops_alive": 3,
  "dispatch_loops_configured": 3,
  "finisher_rate": null,
  "quality_metrics": {"...": "see below"},
  "digest_overdue": false,
  "sweeper": {"...": "see below"},
  "served_sha": "deadbeef"
}
```

- `health_state` is `null` when no state has been persisted or when `is_stale(state)` returns true; when present it is `alan.routes.infra.HealthStateResponse`, exactly the five keys shown above.
- `health_state.findings` contains only **visible** (non-dismissed) findings, each a directly-serialised `alan.health.HealthFinding` dataclass (`finding_id`, `kind`, `severity`, `description`, `workspace_id`, `pr_number`, `blocker_flags`, `action`, `in_flight`, `repo`, `engine_key`, `observed_at`) — `blocked_count` and `in_flight_count` are recomputed from that list server-side.
- `last_computed_at` is the raw `computed_at` from the persisted state, returned **regardless of staleness** (so the frontend can show "Updated 2 hours ago" even when stale). `null` only when no state has ever been saved.

**Retired frontend-only fields (2026-09, X2.4).** Declaring this response shape surfaced live drift between the handler and the frontend's hand-written `HealthResponse` interface (`alan/web/src/api.ts`): the frontend declared `resolved_findings`, `dismissed_findings` and `remediations`, none of which this handler has served since the P3.0 remediation-team removal (Tombstone section below) — all three are read nowhere in `alan/web/src/` outside `api.ts` itself, so retiring them from the frontend declaration is free. The same frontend declaration never named `dispatch_loops_alive`, `dispatch_loops_configured` or `digest_overdue`, all three of which this handler has always served; they are now part of the declared shape above.

### `quality_metrics` (archive-neutral, build-scoped success rate)

`_build_quality_metrics(queue, workspaces_root)` in `alan/routes/infra.py` computes, on every `/api/health` request, one `alan.routes.infra.QualityMetrics` (`extra="forbid"`, nineteen declared fields — the seven below plus the six `format_error_*`/`recovery_*` fields and the six `rescue_*` fields documented further down) via a single `QualityMetrics.model_validate({...})` call that merges the base metrics dict with `_format_correction_metrics()`'s and `_rescue_metrics()`'s output; `_cached_quality_metrics` caches the resulting model (not a dict) behind a 30-second in-process TTL, keyed by `workspaces_root`:

```json
{
  "build_success_rate": 0.82,
  "in_flight_build_count": 3,
  "unproven_clause_count": 3128,
  "first_pass_approval_rate": 0.75,
  "mean_fix_cycles_to_merge": null,
  "sample_size": 40,
  "computed_at": "2026-06-02T12:00:00+00:00"
}
```

- The unit of measurement is the **workspace**, not the pull request: a workspace with several tasks scores zero unless every task finished cleanly — there is no partial credit. The measure reads **recorded completion state and evidence**, not whether code shipped, so a workspace whose PR merged successfully can still score zero. Because of this, a low number most often means **a human had to reach in and finish things by hand**, not that the work was wrong. `build_success_rate` replaces the former `completed_fraction`, which perversely worsened every time a successfully-finished workspace was archived (it excluded `Status.ARCHIVED` from its denominator). It is computed **live** from `workspace.list_workspaces()` (archive-inclusive, no persistence) rather than from the persisted finisher-rate blob, so it stays independent of the 30-day funnel window:
  - **Scope**: only workspaces where `alan.finisher_rate._is_build_pipeline(meta)` is true (any task ran under a build team — `implementation`, `implementation_plan`, `implementation_fix`, `verification`, `pr_merge`, `decomposer` — or a build-team revisit exists). Research/brainstorming/architecture/internal-automation workspaces never enter the numerator or denominator.
  - **Denominator**: build-pipeline workspaces whose `meta.status` is in `alan.finisher_rate._BUILD_TERMINAL_STATUSES` (`completed`, `failed`, `escalated`, `partial`, `archived`) — a set dedicated to this metric, distinct from the funnel's own `_TERMINAL_STATUSES` (which includes `awaiting_input` and excludes `partial`). `archived` is included so archiving a finished workspace never changes the ratio; `partial` is included per `docs/architecture/partial_workspace_status.md`.
  - **Numerator**: of those, the ones where `_is_build_success(meta)` is true (`alan/routes/infra.py:527-532`) — all three must hold:
    1. `meta.tasks` is non-empty (`infra.py:528`) — a workspace with zero tasks is never a success.
    2. Every task is `TaskStatus.COMPLETED` **and** every one of its `dod_clauses` has non-null `evidence` (`_task_completed_without_gap`, `infra.py:519-524`) — one task left `failed` (or otherwise not `COMPLETED`), or one clause missing evidence, fails the whole workspace with no partial credit.
    3. `acceptance.all_clauses_satisfied(meta)` (`alan/acceptance.py:231`) — all acceptance clauses are satisfied.

   Evidence is stamped by `workspace.record_task_evidence` and untouched by archiving, so success survives archiving with no extra I/O.

   Illustrative example (invented for exposition — not drawn from real recorded work):

   | Workspace | Team | Status | Counted? | Result | Why |
   |---|---|---|---|---|---|
   | `ws-alpha` | build | `completed` | yes | **success** | every task `COMPLETED` and evidenced; acceptance clauses satisfied |
   | `ws-bravo` | build | `partial` | yes | **zero** | its PR merged, but a sibling task was left `failed` — `_task_completed_without_gap` is false, so no partial credit |
   | `ws-charlie` | build | `archived` | yes | **success** | archived after finishing cleanly; `archived` is a terminal build status, so archiving changed nothing |
   | `ws-delta` | build | `active` | no | — | still in flight; appears only in `in_flight_build_count` |
   | `ws-echo` | research | `completed` | no | — | not build-scoped; never enters numerator *or* denominator |

   2 successes (`ws-alpha`, `ws-charlie`) ÷ 3 terminal build-pipeline workspaces (`ws-alpha`, `ws-bravo`, `ws-charlie`) = **0.67**. `ws-delta` is excluded because it is still active, not terminal; `ws-echo` is excluded because it is out of build scope entirely — neither appears on either side of the fraction. If `ws-charlie` were un-archived, it would still be counted and still be a success — the ratio would not change.
  - Returns `null` when there are zero scoped build-pipeline workspaces in a terminal state.
- `unproven_clause_count` (`alan.health_repo_metrics.unproven_clause_count`) reports, over the same terminal build-pipeline population `build_success_rate` scores, the total number of acceptance clauses (`Meta.requirements` rows) across those workspaces that carry neither a `test_ref` nor coverage by any subtask `TaskDoDClause` — `acceptance.unproven_clause_ids(meta.requirements, meta.tasks)`, summed. It is reported **beside** `build_success_rate`, never folded into it: `is_build_success` and the numerator above are unchanged by this field, so a workspace whose every clause is unproven still counts as a build success when its tasks completed cleanly — that is the same pre-existing lie `docs/architecture/acceptance_gate.md` now documents plainly, made visible here as a number instead of staying silent. **Recommended follow-up, not acted on here:** reconsider whether an unproven workspace should count toward `build_success_rate`'s numerator at all — that is a deliberate redefinition of a tracked fleet metric and needs its own before/after accounting, not a side effect of a visibility change.
- `in_flight_build_count` counts build-pipeline workspaces still running, via a **positive** allowlist (`active`, `paused`, `awaiting_input`) rather than "not terminal" — a negative definition would miscount a stuck `partial`/`escalated` workspace as in-progress. It is informational only and never dilutes `build_success_rate`.
- `first_pass_approval_rate` is unchanged: derived from `alan.learning_metrics.first_pass_approval_rate` over the persisted `learning_metrics_v1` trajectory blob, keyed by verdict rather than workspace status, so it is unaffected by archiving.
- `mean_fix_cycles_to_merge` is always `null`. The underlying trajectory join (`alan/trajectory.py`) undercounts fix cycles by roughly an order of magnitude in production; `alan/learning_metrics.py::mean_fix_cycles_to_merge` itself groups correctly per `(task_id, pr_number)`, but the upstream join feeding it is not yet verified against real audit rows. The metric is hidden at the API boundary until that join is fixed, per the "hide until it can be corrected" rule — `sample_size`/`computed_at` still describe `first_pass_approval_rate`'s freshness.
### `format_error_rate` / `recovery_rate` (structured-block correction rates)

`_build_quality_metrics` additionally merges in `_format_correction_metrics()`, which calls `alan.health_repo_metrics.format_correction_rates_from_audit_dir(_audit_dir())` on every `/api/health` request (behind the same 30-second `_cached_quality_metrics` TTL); its six-key dict is merged verbatim into the single `QualityMetrics.model_validate({...})` call described above, not returned or cached separately:

```json
{
  "format_error_rate": 0.12,
  "format_error_sample": 42,
  "format_error_low_data": false,
  "recovery_rate": 0.8,
  "recovery_sample": 10,
  "recovery_low_data": false
}
```

Source: `block_correction_audit.jsonl` (tailed via the shared `read_audit_tail`/`AUDIT_TAIL_BYTES`), one row per rejection or correction attempt across every host-parsed structured block, fleet-side and in the interactive proposal flow — including sites that only ever emit a `"clean"` first-attempt row, which is what makes the denominator fleet-wide rather than error-only.

- **`format_error_rate`** — of first-emission attempts (`attempts == 1`), the share whose `outcome` was not `"clean"`: how often a worker's structured output was rejected before any correction was attempted.
- **`recovery_rate`** — of second-emission attempts (`attempts == 2`), the share whose `outcome` was `"corrected"`: how often a single correction turn recovered a rejected block.
- Both are `None`/`null` with the paired `*_low_data` flag `true` **only** when the relevant sample is exactly zero (`compute_format_error_rate`/`compute_recovery_rate` in `alan/health_repo_metrics.py`) — unlike the `LOW_DATA_MIN_SAMPLE`-gated repo metrics below, there is no minimum-sample threshold here, just presence/absence of any recorded attempt. `HealthMetricsPanel.tsx` renders this as an explicit "not measured yet" string rather than a bare dash or a `0%`, since a zero sample and a zero rate mean different things.
- Fleet-wide only, by design: `_scopedQualityMetrics` (the repo-scoped code path driven by `RepoHealthRow`, which carries no format-correction fields) always renders both as not-measured, regardless of what the fleet-wide numbers say — there is deliberately no per-repository breakdown for these two metrics.

- `HealthMetricsPanel.tsx` labels the rate tiles by window to avoid presenting any of them as a single "completion score": the finisher-rate tile is "Finisher funnel (30d)" (`FinisherSection`, sourced from the persisted `finisher_rate` funnel blob, alongside the "Fully autonomous (30d)" tile), while `build_success_rate` is "Build success rate (all-time)" (`QualitySection`). Every value carries a persistent explainer paragraph wired via `aria-describedby` (not a tooltip) for accessibility parity across the card. The funnel stage for `not_needed_human` is labeled "No human gate" — it only says the run never *ended* stuck on a human, which is deliberately distinct from the fully-autonomous rate below.
- The finisher-rate funnel classifies **outcome-first**: a task present in the merge set (`merge_ok` rows in `pr_merge_audit.jsonl`) is `success` — or `not_evidenced` when a DoD clause lacks evidence — regardless of crash rows in `job_failure_audit.jsonl` or spiral rows in `spiral_audit.jsonl`. Production showed transient events (`external_gate_blocked` CI flakes, phantom crash rows) on tasks whose PRs merged; a recovered hiccup must not count as a completion failure. Only unmerged tasks fall through the failure ladder `crashed` → `not_converged` → `needed_human` → `not_merged`, and the spiral index behind `not_converged` skips the `chain_decay`/`chain_reset` bookkeeping kinds (see `docs/architecture/loop_guard.md`). `_tally_stage` keeps `FunnelCounts` monotone.

### `autonomous_rate` (fully-autonomous build rate, 30d)

`FinisherRate.autonomous_rate` rides the same funnel computation, persistence blob (`finisher_rate_v1`), and `/api/health` key as the finisher rate. It answers a stricter question than `rate`: of all build workspaces dispatched in the window, how many reached an evidenced success **without any recorded human touch**?

- **Numerator** (`_is_autonomous` in `alan/finisher_rate.py`): stage classified `success` (merged + every DoD clause evidenced) **and** `meta.interventions` is empty **and** terminal status is not `awaiting_input`/`escalated`. The status check is not redundant with `success`: classification is outcome-first, so a merged run whose terminal status is `awaiting_input` still classifies `success` — but it did end needing a human, so it is not autonomous. **Denominator**: `dispatched` (same as `rate`). `FunnelCounts.autonomous` is ≤ `dod_evidenced` by construction and is deliberately **not** rendered as a sixth funnel stage — it is a filter over successes, not a pipeline step.
- **`meta.interventions`** (`alan/workspace.py`, `Intervention` = `{action, timestamp_utc}`, appended by `record_intervention`) is stamped by the **route layer only** — the human/API surface. Automatic mechanisms (`alan/auto_continue.py`, `alan/project_auto_continue.py`, the dispatcher's handover routing, create-and-dispatch) call `dispatch_team_core` directly and never record. Recorded actions:
  - `answer` — POST `.../tasks/{t}/answer` (recorded only when the answer is actually persisted; the idempotent replay and 4xx paths record nothing).
  - `redispatch` — POST `.../tasks/{t}/dispatch` when the task already has prior work (`prior_handover_for_task` non-null). A first dispatch records nothing. The inbox propose-approval endpoint dispatches through `perform_task_team_dispatch(..., record_redispatch=False)`: approving a proposal is posture gating, not a rescue, and counting it would mark every propose-gated workspace non-autonomous from birth (owner ruling, 2026-08-04). The approval itself is still durably logged in `inbox_approval_audit.jsonl` if that decision is ever revisited.
  - `resume` — POST `/{id}/resume` (the 409 path records nothing).
  - `force_complete` — POST `.../tasks/{t}/complete`, both the `force` and `verified` paths (the dispatcher-reject 409 records nothing).
  - `scenario_override` — PUT `.../tasks/{t}/scenarios`.
  - `health_complete` — POST `/api/health/complete` (merged-ghost card).
- **Known blind spots** (the metric is a floor on intervention / ceiling on autonomy — the UI explainer says "recorded control-plane interventions" for this reason):
  - Rescues outside the control plane: manual `gh pr merge`, CI reruns, review dismissals, direct file surgery — see the tier model in `scripts/intervention_report.py`.
  - Workspaces created before this feature deployed carry no markers, so the rate is optimistic until the 30-day window rolls past the deploy date.
  - `meta.json` writes are last-writer-wins (no file lock) except the small set of fresh-apply writes enumerated in [dispatcher_concurrency.md](dispatcher_concurrency.md)'s "`meta.json` write semantics" section; `record_intervention` is one of those — it re-reads fresh meta before writing and returns the new `Workspace` for call sites to thread onward — but the read-then-write is still not atomic, so a worker writing in the same millisecond can still drop a marker — failure direction is metric-optimistic.
  - `stop`, `archive`/`unarchive`, and note edits are deliberately uncounted (they park or annotate work rather than rescue it).

## API: `/api/health/repos` (GET)

Per-repository health metrics backing the global health page's summary table (`alan/health_repo_metrics.py`, `alan/routes/health.py`). Declared response shape: `alan.routes.health.RepoHealthResponse` (`computed_at`, `rows: list[RepoHealthRow]`), where each `RepoHealthRow` field is an `alan.routes.health.RepoRateValue` — the `{value, sample, low_data}` triple below is now a declared model, not an ad hoc dict; `value` is typed `int | float | None` specifically so an integer count (`in_flight`) serialises as an integer rather than being coerced to a float. Returns one row per **visible** repository (hidden engines omitted, same `EngineConfig.hidden` flag `hidden_repo_slugs()` uses) with, per row:

```json
{
  "computed_at": "2026-06-02T12:00:00+00:00",
  "rows": [
    {
      "repo": "DejvMatikar/alan",
      "outstanding_findings": {"value": 4.0, "sample": 2, "low_data": false},
      "build_success_rate": {"value": 0.82, "sample": 40, "low_data": false},
      "outcome_rate_30d": {"value": 0.7, "sample": 12, "low_data": false},
      "autonomous_rate_30d": {"value": 0.6, "sample": 12, "low_data": false},
      "in_flight": {"value": 3, "sample": 3, "low_data": false},
      "first_pass_approval_rate": {"value": 0.75, "sample": 15, "low_data": false}
    }
  ]
}
```

Every value carries its own `sample`/`low_data` pair — `low_data` is a de-emphasis signal only (`sample < health_repo_metrics.LOW_DATA_MIN_SAMPLE`, currently 10), never a suppression: the rate is still `numerator / sample` whenever `sample > 0`, matching the finisher-rate funnel's own "never introduce a minimum-sample suppression" rule extended per-repository. `outstanding_findings` and `in_flight` are exact counts, not statistical estimates, so their `low_data` is always `false`.

`computed_at` is the same sweep timestamp `/api/health`'s `last_computed_at` uses (`HealthState.computed_at`, written by the triage sweep) — a fresh install with no swept blob (`load_health_state` returns `None`) returns `{"computed_at": null, "rows": []}` with HTTP 200 rather than computing anything. Every row's `outstanding_findings` is a severity-weighted sum over that persisted sweep's findings (`critical=3, high=2, medium=1`, grouped by `engine_config.canonical_scope_key(finding.engine_key).slug` so a `None` engine_key folds into the default repository's own row rather than being dropped or bucketed separately) — it is not recomputed live and does not share the `/api/health` `health_state` payload's per-finding suppression rules (there are none left post-P3.0 dismiss removal).

The other five values are computed live from a single `workspace.list_workspaces()` scan plus a few audit-log tail reads, behind a 30-second in-process TTL cache (`alan.routes.health._cached_repo_metrics`, keyed by `workspaces_root`, mirroring `alan.routes.infra._cached_quality_metrics`'s pattern) so polling this endpoint repeatedly does not repeatedly re-scan the workspace tree or re-run `git log`:

- `build_success_rate` / `in_flight` — `alan.health_repo_metrics.build_success_and_in_flight_by_repo`, grouping the same `is_build_success`/`classify_build_workspaces` logic `alan/routes/infra.py`'s fleet-wide `build_success_rate` already uses (relocated here so `alan/routes/health.py` can reach it without importing `alan/routes/infra.py`, which the "Routes siblings are independent" import-linter contract forbids) by `canonical_scope_key(ws.meta.engine_key).slug`, all-time, archive-inclusive.
- `outcome_rate_30d` / `autonomous_rate_30d` — `alan.health_repo_metrics.outcome_and_autonomous_by_repo`, the same funnel `alan.finisher_rate.compute_finisher_rate` computes fleet-wide (`window_cutoff`/`build_indices`/`filter_metas`/`aggregate`, all promoted to public so this module can reuse them instead of reimplementing the funnel), but `filter_metas` is applied **before** grouping by repo so each repo's rate is genuinely a 30-day figure and not an all-time rate mislabelled as one. `_rate_or_none` (the fleet-wide funnel's minimum-sample suppression helper) is never called on this path — the per-repo rate is `numerator / sample` unconditionally whenever `sample > 0`, with `low_data` carried alongside instead of used to null the value out.
- `first_pass_approval_rate` — `alan.health_repo_metrics.first_pass_approval_by_repo`, deriving `alan.trajectory.TrajectoryRecord`s the same way `alan/teams/triage.py`'s `_derive_learning_metrics` does (`node_turns_audit.jsonl` + `verification_audit.jsonl` + `pr_merge_audit.jsonl` tails, plus a `git log` read) and reusing the existing `alan.learning_metrics.sample_size()` as the denominator, but grouped per task's `engine_key` (read from the same `list_workspaces()` scan) instead of folded into one fleet-wide blob. This is the one metric of the six that has no repository attribution anywhere else in the app today.

`alan/health_repo_metrics.py` sits in the import-linter layers list immediately between `health` and `finisher_rate` — strictly below `health` (which it therefore cannot import; `alan/routes/health.py` is what combines this module's per-repo numbers with `alan.health.load_health_state`'s findings) and strictly above `finisher_rate`/`learning_metrics`/`trajectory`/`acceptance`/`workspace`/`engine_config` (which it imports downward from). It also now holds `read_audit_tail`/`AUDIT_TAIL_BYTES`, relocated verbatim out of `alan/teams/triage.py` (which was at the 800-line size-ratchet cap) as the public names; `triage.py`'s `_read_audit_text`/`_FR_AUDIT_TAIL_BYTES` are a one-line wrapper and an aliased import over the relocated versions, preserving both names (and therefore every existing test that monkeypatches `triage._FR_AUDIT_TAIL_BYTES` to force the tail-truncation path) exactly as they were.

The `/api/health` payload and its 5-second app-wide poll (`HealthProvider`) are untouched by this endpoint. This PR ships the backend API only — no frontend call site exists yet; the global health page's comparison table is separate, later work. When that table lands, `/api/health/repos` is intended to be fetched only from the global health page, not from every screen, so the 30-second cache above is what will keep a realistic repository count cheap without touching the existing poll's cost.

## Windowed health metrics: `alan/health_windows.py`

The single source of the 7-day-versus-30-day comparison. `compute_health_windows(sources, now=...)` returns one `HealthWindows` — `computed_at`, `fleet` (a `ScopeWindows`), `repos` (slug → `ScopeWindows`, one per repository that has a workspace or a trajectory record, hidden engines included — filtering to visible repositories is the route's job) and `unattributed` — and is not wired into any route yet; `alan/routes/health.py` and `alan/health_repo_metrics.py` still serve the pre-existing shapes. The module sits in the import-linter layers immediately above `unblock_activity`, so routes may import it and it imports downward from `health_repo_metrics`, `finisher_rate`, `learning_metrics`, `trajectory`, `unblock_ledger`, `incident`, `acceptance`, `workspace` and `engine_config`.

`ScopeWindows` carries nine `MetricWindows` — `outcome`, `autonomous`, `build_success`, `first_pass_approval`, `unproven_clauses`, `format_error`, `correction_recovery`, `rescue_success`, `rescue_load` — plus `funnel: FunnelWindows`, the per-window `FunnelCounts` (`dispatched` → `not_crashed` → `not_needed_human` → `converged` → `merged_pr` → `dod_evidenced`, plus `autonomous`) behind the "x of N" sub-rows. A `MetricWindows` is `d7`, `d30` and one `MetricVerdict`; each window is a `Window(value, sample, covered_days)`. `value` is `numerator / sample` whenever `sample > 0` and `None` otherwise — never suppressed for being small; the sample floor only withholds the verdict. "In flight" is not windowed and is not here.

### Date bases

| Metric | Population | Dated by |
|---|---|---|
| `outcome`, `autonomous` | the finisher funnel's own population (`finisher_rate.filter_metas`: build pipeline, funnel-terminal status) | `created_at`, floored at `FINISHER_EPOCH` (`max(now − window, FINISHER_EPOCH)`) — the same basis `outcome_rate_30d` always used |
| `build_success` | build-pipeline workspaces in a build-terminal status (archived included) that carry `Meta.terminal_at` | `Meta.terminal_at`; undated workspaces are excluded |
| `unproven_clauses` | the same windowed finished build workspaces | `Meta.terminal_at`; value = unproven live clauses ÷ live clauses (`acceptance.unproven_clause_ids` over `live_clauses`), **sample = the clause count**, not the workspace count |
| `first_pass_approval` | `TrajectoryRecord`s with `team == "implementation"` and a verdict | `TrajectoryRecord.verified_at`; a record with no or unparseable `verified_at` is in neither window |
| `format_error` (attempt-1 rows not `clean`) and `correction_recovery` (attempt-2 rows `corrected`) | block-correction rows | the row's `ts` |
| `rescue_success`, `rescue_load` | `unblock_ledger.rescue_stats(..., window_days)` | ledger row `ts`; the population, stuck set and `load_sample` are the same for both windows |

A window edge is inclusive (`ts >= now − N days`). Archiving never changes `build_success` or `unproven_clauses`: `terminal_at` is stamped on entry into a finished status and archiving neither stamps nor clears it, so an archived workspace stays in the window it finished in. Unarchiving back into a finished status does not re-stamp `terminal_at` either (`lifecycle.stamps_finish` is false whenever the previous status is `ARCHIVED`): a restored `COMPLETED` workspace keeps the window it originally finished in, and one restored as `awaiting_input` gets no stamp until it finishes again.

### Attribution

Fleet-wide, every dated row counts. Per repository:

- workspaces, the funnel, `build_success`, `unproven_clauses` and the rescue population group by `canonical_scope_key(meta.engine_key).slug`;
- `first_pass_approval` groups each record by its task's workspace engine key (`engine_keys_by_task`), so a record for a task with no workspace folds into the default repository — the behaviour `first_pass_approval_by_repo` already had;
- `format_error` and `correction_recovery` attribute a row **only when its `task_id` matches a known workspace**. Any other row — no `task_id`, or one that matches nothing — counts fleet-wide only and increments `HealthWindows.unattributed`, which counts the rows within the 30-day window that contributed to either rate (attempt 1 or 2) and could not be attributed. The page says per-repository rates are computed from attributable records only; this count is what lets it say how many were left out.

Correction rows reach the module through the `CorrectionRow` protocol (`ts`, `task_id`, `attempts`, `outcome`), which `health_repo_metrics.BlockCorrectionRow` satisfies once it carries a parsed `ts`; the module never reads `block_correction_audit.jsonl` itself.

### Verdict

`compare_windows(d7, d30, higher_is_better=...)` yields a `MetricVerdict(verdict, higher_is_better)`, one of `better`, `worse`, `same`, `insufficient`. The verdict is `insufficient` if either value is `None`, if either window has fewer than `LOW_DATA_MIN_SAMPLE` (10) samples, or if `d30.covered_days <= 7` (a 30-day window that reaches back a week or less is the 7-day window under another name). Otherwise `|d7 − d30| <= 0.05` (with a `1e-9` tolerance, so a difference of exactly five points is `same` despite float noise) is `same`, and beyond that the sign decides: a higher 7-day value is `better` when `higher_is_better`, `worse` otherwise, mirrored for a lower one. Only `format_error` and `unproven_clauses` are lower-is-better. The polarity travels with the verdict so no consumer has to re-derive it.

### Coverage (`covered_days`)

`covered_days = floor((now − max(now − window, reach)) / 1 day)`, clamped at 0, where `reach` is the oldest moment the metric's source still holds (`None` means the source loses nothing, so the window is fully covered). Each metric is bounded by its own source:

- `outcome`/`autonomous` — `SourceReach.funnel`: `FINISHER_EPOCH`, or the oldest kept row of the job-failure, merge or spiral audit if that file was truncated by the tail read (whichever is latest);
- `build_success`/`unproven_clauses` — the earliest `terminal_at` across all workspaces; with no stamp anywhere the reach is `now` and coverage is 0. `terminal_at` only exists from the release that stamped it, so this is the honest length of the recorded history;
- `first_pass_approval` — `SourceReach.first_pass`: the oldest kept row of the node-turns or verification audit when truncated (the later of the two);
- `format_error`/`correction_recovery` — `SourceReach.corrections`: the oldest dated row when `block_correction_audit.jsonl` is larger than `AUDIT_TAIL_BYTES` and so was tail-read;
- `rescue_success`/`rescue_load` — unbounded: the ledger is a complete table and `rescue_stats` windows it by `ts`.

"Truncated" means the audit file is larger than `AUDIT_TAIL_BYTES` (256 KiB), so `read_audit_tail` dropped its head; a file read in full bounds nothing, because a quiet source is not a short one. `read_window_sources(queue, audit_dir, workspaces, corrections)` performs the tail reads, derives the trajectories (the verdict, team and `verified_at` need no merge audit or `git log`, so neither is read) and builds the `SourceReach`.

## OpenAPI export

`alan.server.openapi_json()` returns `app.openapi()` serialised byte-stably (`json.dumps(..., sort_keys=True, indent=2)` plus a trailing newline) — it is reachable at import time, since `app.openapi()` only introspects routes and never runs `lifespan`. `scripts/dump_openapi.py` is a thin argparse wrapper around that function; it does not compute anything of its own. `--out PATH` writes the schema to a file, and the status line goes to stderr; omitting `--out` writes the schema itself to stdout instead, so callers can pipe it (`dump_openapi.py | openapi-typescript ...`) without a temp file. The export is independent of whether a built frontend bundle (`alan/web/dist/`) exists on disk, because `mount_ui`'s SPA mount adds no schema paths. No exported schema file is checked into this repository by this change — the artefact lands with the CI drift gate that will validate it.

## API: `/api/health/digest` (GET)

Read-only projection of two folds from the already-persisted daily digest (`alan/digest.py`, `alan/digest_sweep.py` — generation, cadence, and storage are unchanged by this endpoint; see [digest.md](digest.md)) onto the Health page — Health is now the only human fleet-signal surface, the entire inbox HTTP surface having been deleted (delete-the-inbox-http-surface, 2026-09). No new schedule, thread, or recomputation: `_digest_snapshot` (`alan/routes/health.py`) reads `load_latest_digest(queue)` and the same `digest_last_date`/`resolve_digest_deadline` pair `alan/digest_sweep.py` and `alan/routes/infra.py`'s `_digest_overdue_flag` already use for their own overdue checks.

```json
{
  "recovery": {"current_count": 4, "previous_count": 2, "direction": "up"},
  "residue": [{"fingerprint": "a1b2c3d4e5f6a7b8", "title": "Unclassified anomaly ..."}],
  "digest_overdue": false
}
```

- `recovery` is `alan.digest.recovery_totals(doc.recovery_trend)` — the per-incident-class trend array summed into one aggregate (current count, previous count, direction) before it ever crosses the API, so the frontend never renders a row-per-class breakdown. `null` only when `load_latest_digest` returns `None` (no digest has ever been produced, or the stored blob is unreadable) — a measured zero (`current_count: 0`) is always distinguishable from not-yet-measured, on the wire and in the UI.
- `residue` is `list[ResidueItem] | None`, mirroring the `recovery` field's own null-vs-empty contract: `null` only when `load_latest_digest` returns `None`; `[]` once a digest has ever been produced with a clean window; a populated list otherwise. This is a widening (residue used to be `list[ResidueItem]`, defaulting to `[]` in the no-digest case) — the frontend must render `null` and `[]` differently (see "Residue tile" below), the same way it already must for `recovery`. Each entry mirrors `doc.residue` (`alan.digest.ResidueEntry`) verbatim — fingerprint and title only, in payload order. These are the digest's currently-fresh unclassified anomalies (no typed handler covers them yet, and they are neither closed nor suppressed).
- `digest_overdue` is the same `digest_overdue(digest_last_date, now, resolve_digest_deadline(now))` check used elsewhere, so the digest sweep's liveness stays visible on Health now that the inbox page is gone.

### Synthetic finding kinds: removed

`unclassified_anomaly` (one per digest residue entry) and `pending_disposition` (one per investigator RCA draft with no recorded human disposition) used to be folded into `GET /api/health` at read time by `alan.digest.residue_findings` and `alan.digest.pending_rca_findings`, called from `alan/routes/infra.py`'s `_digest_synthetic_findings`. Both producers and that call site are deleted: the two findings asked the operator to hand-classify anomalies, work the Health page no longer demands (the activity view below is an observation surface instead). `GET /api/health` now returns only the persisted sweep's own findings (after the engine/project filter and `_suppress_worker_pool`), so `blocked_count`, `in_flight_count`, `verdict` — and therefore Home's `needsUserCount` — no longer include either kind. **That drop is intended, not a defect**: the count falls by exactly the number of residue entries plus the number of undispositioned RCA drafts that used to be folded in (`tests/test_health_residue_fold.py` pins the arithmetic on the fixture that used to produce both). The digest itself is unchanged: `DigestDoc.residue` is still computed and still feeds the `/api/health/digest` residue tile.

### API: `POST /api/health/incidents/{fingerprint}/disposition`

The route survives with **no UI caller** — the Health page's disposition control is removed (see "Findings table" below), deleting a working route is outside the activity view's scope, and `alan.incident_store.set_disposition` keeps its other callers. Request body `{"disposition": "<Disposition value>"}`, one of the seven `alan.incident.Disposition` values (`root_caused`, `mechanically_recovered`, `known_flake`, `transient_external`, `by_design`, `duplicate_of`, `needs_human`); an unrecognized value is a `422` (Pydantic validates enum membership natively). Response: `{"fingerprint", "disposition", "closed_at"}`. Thinly wraps `alan.incident_store.set_disposition(queue, fingerprint, body.disposition, now=datetime.now(UTC))` — last write wins (a second POST for the same fingerprint overwrites the first), and a fingerprint with no existing `incident_dispositions_v1` record gets one created on the spot (`incident_class=""`, since the caller supplies no class). `disposition_agreement_rate` in `scripts/investigator_report.py` joins an investigator's `proposed_disposition` against a human-confirmed disposition purely by shared fingerprint; with the UI control gone nothing in the product records new human dispositions, so that metric stops gaining data points (it goes dark rather than erroring).

### Frontend: recovery figure, residue tile, and the disposition control on the fleet-wide Health view

Both digest signals are fleet-wide only — the Health page's per-repository scoped view (`?engine=`) never fetches `/api/health/digest` (`useHealthDigest`, `alan/web/src/features/health/hooks/useHealthDigest.ts`, gates its fetch through `useChangeRefetch`'s `enabled` parameter on `scopeEngine === null`, riding the same `"health"` change-kind `useRepoHealthMetrics`/`useDriverLoops` already poll) and never renders either signal, so a fleet-wide number can never misrepresent a single repository's scope.

- **Recovery figure** — `DigestSignalSection.tsx` renders a labelled "Daily digest" sub-section inside `RepoHealthTable.tsx`'s existing `repo-health-panel` card, below the comparison table, separated by the card's existing `border-t` rule. It reuses `HealthMetricsPanel.tsx`'s exported `MetricCell` tile — the same persistent-explainer, `aria-describedby`-associated convention every other Health tile uses — for the count, plus a plain-text trend line (a glyph and the word "up"/"down"/"steady" together, never color alone) for direction. A `null` recovery renders `MetricCell`'s existing low-data/not-measured-yet treatment (`lowData` prop) instead of a bare `0`.
- **Residue tile** — a second `MetricCell` beside the recovery one, in the same `DigestSignalSection.tsx`, rendering `residue === null ? "not measured yet" : String(residue.length)` with `lowData={residue === null}` — mirroring the recovery tile's own null-vs-zero distinction, so "no digest has ever run" reads differently from "the last digest's window was clean." An `AggregateChip` beside the section heading carries `digest_overdue` as "Overdue"/"On time", keeping the sweep's liveness visible now that the inbox page is gone.
- **Findings table** — `FindingsTable.tsx` no longer has a Disposition column: `IncidentDispositionControl.tsx` (the `<select>` plus submit control keyed by fingerprint), `DispositionCell` and the `unclassified_anomaly`/`pending_disposition`-only `_DISPOSITIONABLE_KINDS` gate are deleted, and `api.setIncidentDisposition` with them. The table is read-only again (severity/category badges, description, blocker-flag chips, links). The residue *tile* above is unaffected.

## API: `/api/health/unblocker-activity` (GET)

Read-only projection of the autonomous unblocker's ledger (`alan/unblock_activity.py`, see [unblocker.md](unblocker.md)'s "Activity view" section for the contract and its invariants) — what the host did over the last `RESCUE_WINDOW_DAYS` (30) days, by kind of park and by how each attempt ended. Optional `engine` query param (a repo slug):

- absent — **fleet** view: `scope: "fleet"`, aggregates only (`aggregates[]` grouped by park shape with separate attempt- and report-outcome tallies, `events_total`, `workspaces_touched`, the two deduplicated counts `still_stuck_workspaces` (distinct workspaces currently stuck) and `gave_up_workspaces` (distinct workspaces with a `report` row in the window), `waited_workspaces`, `diagnosed_workspaces`/`diagnosable_workspaces`, `ever_recorded`, `unattributed_events`). The payload is a different Pydantic model from the scoped one, so it structurally has no `events`, `workspaces`, `workspace_key` or `issue_url` field; hidden engines (`_visible_repo_slugs`) are excluded.
- present — **repo** view: `scope: "repo"`, the same summary (both new counts included) computed over that repository's rows only, plus `events[]` (newest first, capped at `EVENT_LIMIT` = 50, `events_total` reports the uncapped count), `workspaces[]` (one row per touched workspace, computed over the full window before the event cap and never capped itself, so its length equals `workspaces_touched`; each row carries `workspace_key`, `last_at`, `shape`, `events_total`, `events_listed`, `attempts_total`, `attempts_ceiling`, `clearance`, `gave_up`, `issue_url`, `diagnosis`) and `fleet_ever_recorded`. Any slug the caller names is served, hidden engines included; a slug that resolves to nothing returns a well-formed empty payload, never a 404 (no slug-enumeration oracle).

The handler is GET-only and never writes. Results are cached for `_ACTIVITY_TTL_S` (30 s) keyed by `(queue, workspaces_root, engine)`, so one repository's payload is never served for another.

### Health page composition (unblocker activity view)

`HealthView.tsx` mounts `UnblockerActivityPanel` (`features/health/components/UnblockerActivityPanel.tsx`, data from `useUnblockerActivity`, refetched through `useChangeRefetch("health", ...)` like the digest hook) below the scope-specific section and directly above the "Other health findings" disclosure, so the health metrics stay at the top of the page:

- **Repository selected (`?engine=`)**: scope indicator, `HealthMetricsPanel`, `RecurringProcessesPanel`, unblocker activity, other health findings.
- **No repository selected**: `RepoHealthTable`, `DriverLoopsPanel`, unblocker activity, other health findings.

The panel keeps its `h2` heading, subtitle and the loading, error and stale (`role="alert"`) notices and the empty-window notices (`unblocker-activity-empty-*`) outside the disclosure, so a failure or an empty window is readable without a click. Only when there is activity to show does it render a native `<details>` disclosure (`unblocker-activity-disclosure`), **collapsed by default**, mirroring `FindingsDisclosure` and `RecurringProcessesPanel`: a `summary` with the same `min-h-11` hit area, the `▸`/`▾` glyph hidden from assistive tech, and the shared `isDisclosureActivationKey` (Enter/Space) handler. The summary reads `Unblocker activity — N events · M workspaces touched` and appends `K still stuck` and `G gave up` only when non-zero (`unblockerDigestLine`, `lib/unblockerSummaryFields.ts`). The open state lives in the panel, not the disclosure, so a refetch never collapses a panel the reader opened; arriving from a repository status strip chip lands on the collapsed panel exactly as arriving from a recurring chip does. One expand reveals both tables:

- **Park kinds** (`UnblockerParkKindsTable.tsx`): one row per kind — event total, workspace count, the current cleared / still-stuck / gone / retried tallies, and the attempt-outcome and give-up-outcome breakdowns as separate chip cells (never one sum). The `Cleared` column header's `title` repeats that cleared is not proof the action worked.
- **Workspaces** (`UnblockerWorkspacesTable.tsx`, row model in `unblockerWorkspaceRows.ts`, repository view only): one aggregated row per touched workspace built from the payload's `workspaces[]` — the workspace (linked to its own page), last-acted-on time, park kind, event count, `Attempt N of 5` (an em dash when `attempts_total` is 0), the clearance chip, `Gave up` (its `title` names the three causes: attempt ceiling reached, a human-only authority holder, or a terminal park), the filed-issue link and the latest diagnosis. Rows whose workspace is still stuck or was given up on sort above the rest; within each band the most recent activity is first. Each workspace is its own `<tbody>`: the toggle is a `Button` (`iconOnly`, `min-h-11 min-w-11`) with `aria-expanded` and an `aria-controls` listing the ids of its child rows. The child rows — one `<tr>` per listed event, a single wrapping flex line of time, outcome chip, clearance chip and detail, no field grid — are always rendered and `hidden` while collapsed. There is never a `<details>` inside a `<tbody>`.

Both tables sit in an `overflow-x-auto` region that is keyboard-focusable and labelled (the `RepoHealthTable.tsx` recipe), so a phone-width viewport scrolls the table instead of the page.

Honesty about the event cap: `events[]` is capped at `EVENT_LIMIT` (50) but every `workspaces[]` aggregate (`events_total`, `attempts_total`, `gave_up`, clearance) is computed over the whole window, and its length equals `workspaces_touched`, so the row count never contradicts the "Workspaces touched" metric. Where a workspace's child rows were cut, its expanded region opens with a `Showing k of n events for this workspace` row, and the section states once that expanded events show only the latest of the total while each workspace's counts cover the whole window (`unblocker-events-cap`).

The fleet view has no `events` or `workspaces`, so it renders the park-kinds table and a plain sentence saying per-workspace activity is listed for one repository at a time (`unblocker-workspaces-unavailable`), never an empty table.

The "cleared is not proof the unblocker's action worked" note (`unblocker-clearance-note`) sits at the top of the expanded body. The panel has no control that changes fleet state; its only interactive elements are the disclosure summary, the per-row toggles and links out to the workspace and the filed issue. There is no window selector, filter, search, sort control, pagination or export.

### Repository status strip (unblocker summary)

`RepoStatusStrip.tsx` renders `UnblockerSummaryRow` (`features/home/components/UnblockerSummaryRow.tsx`) as a fourth row after the recurring summary. It reuses the scoped `useUnblockerActivity(engineKey)` hook — the same `/api/health/unblocker-activity?engine=` fetch and 30 s server cache the health page uses, so the strip adds no endpoint — and loads without blocking the rest of the strip: while it is loading, or when the load fails, or when the repository has no unblocker activity in the window (`events_total` and `workspaces_touched` both 0), the row renders nothing (never a row of zeros, never an error line). Otherwise it shows a chip per count — events in the window and workspaces touched always (`unknown` tone), and still-stuck and gave-up workspaces only when non-zero (`red` tone) — built by `unblockerCountEntries` (`lib/unblockerSummaryFields.ts`). Each chip is a `min-h-11` `Link` to `/health?engine=<slug>`, exactly like `RecurringGroup`'s chips, and arriving there lands on the collapsed panel.

## API: `/api/health/dismiss` (POST)

Request: `{ "finding_id": "string" }` — adds to dismissed list. Returns 204.

## API: `/api/health/restore` (POST)

Request: `{ "finding_id": "string" }` — removes from dismissed list. Returns 204.

## API: `/api/health/clear` (POST)

No body. Resets run history, escalations, and dismissed IDs. Returns 204.

## API: `/api/driver-loops`

Backs the fleet-level Driver loops panel on the unscoped `/health` view (`alan/routes/driver_loops.py`) — see [driver_loops.md](driver_loops.md) for the runner this surface reads and for the exact hold-reason/outcome vocabulary. `GET` is open (no control token, matching every other status read on this page); the three mutations below are gated by `require_control_token`, following `alan/routes/engines_visibility.py`.

### `GET /api/driver-loops`

```json
{
  "supervised": true,
  "loops": [
    {
      "name": "example-loop",
      "enabled": false,
      "dedicated_clone": true,
      "paused": false,
      "decision": {"action": "hold", "reason": "disabled", "detail": null, "next_wake_at": null},
      "last_outcome": null,
      "final_message": "",
      "denial_count": 0,
      "run_now_pending": false
    }
  ]
}
```

Serializes `alan.driver_loop_status.LoopStatusView` plus a directly-read `paused` flag (`driver_loop_pause.is_paused`, kept independent of `decision` because a loop that is both paused and disabled reports `decision.reason == "disabled"` — the higher-precedence hold — never `"paused"`, per `driver_loop_schedule.decide`'s fixed precedence). `final_message` is truncated to 2000 chars at the source (`driver_loop_runs.finalize_from_log`/`finalize_live_run`); no stdout/stderr log path, PID, or command line is ever serialized. `supervised` mirrors `alan.detached_mode.resolve_detached_mode()` — the frontend renders no mutation controls at all when it is `false`.

### `POST /api/driver-loops/{name}/pause`, `POST /api/driver-loops/{name}/resume`

Write or remove the loop's PAUSE file through `driver_loop_pause.pause`/`resume`, for any name — including a disabled entry, so a hand-run interactive `/loop` session honours the same file. Refuses `409 {"detail": "not_supervised"}` outside production (the same refusal `driver_loop_pause` itself raises). Publishes `driver_loop_changed` (routed to the health SSE surface only, per `alan/routes/events.py`'s `_HEALTH_KINDS`) on success. Returns `{"name": "<name>", "paused": true|false}`.

### `POST /api/driver-loops/{name}/run-now`

Goes through `driver_loop_status.request_run_now`: `404 {"detail": "unknown_loop"}` for a name absent from the registry, `409` with the blocking hold reason (`disabled`/`running`/`paused`/`usage_pause`/`interactive_lock`/`fast_forward_failed`/`backoff`/`not_supervised`) while the loop can't run now, otherwise publishes `driver_loop_changed` and returns the loop's refreshed status.

## Tombstone — remediation_verify anti-cheat invariant (revived by P3.4)

**Status:** REMOVED (P3.0, 2026-07-05). The `remediation`, `remediation_verify`, and `retrospective` teams, the `POST /api/health/remediate` endpoint, and the `remediations_v1` tracking ring are deleted — no LLM surface ever read their output (2026-07-04 usage audit). The `remediation_verify` reviewer's anti-cheat invariant is preserved here **verbatim** because the investigator's evidence-linked RCA work explicitly plans to port this exact contract into its evidence-linked verifier. Do not re-derive it from memory when P3.4 lands — copy it from here.

Former `_REVIEWER_PROMPT` (`alan/teams/remediation_verify.py`) output contract, quoted verbatim:

```
OUTPUT — required PLAIN TEXT lines at the very END of your response (no markdown, no bold, exactly as shown):
DIFF_FINDINGS_CRITICAL: <n>  (n = count of cheat patterns, 0 if none)
VERDICT: APPROVE  (only when n=0)
(use VERDICT: REQUEST_CHANGES when n>0)
```

Former `_enforce_invariant`, quoted verbatim:

```python
def _enforce_invariant(result_text: str) -> Literal["APPROVE", "REQUEST_CHANGES"]:
    verdict = parse_verdict(result_text)
    critical_count = parse_critical_count(result_text)
    if verdict is None or critical_count > 0:
        return "REQUEST_CHANGES"
    return verdict
```

The invariant: never APPROVE when the reviewer reports any critical cheat-pattern finding, and never APPROVE on a malformed/missing verdict — both fail closed to `REQUEST_CHANGES`.

## Frontend

### `HealthProvider` (`alan/web/src/api/HealthProvider.tsx`)

Single polling context (5 s / 1 s when paused). Computes:
- `queuePaused`, `pausedUntil` (forwarded from `/api/health`).
- `healthState` — `HealthState | null`.
- `blockedCount` — `health_state.blocked_count` (filtered; excludes dismissed).
- `needsUserCount` — `blocked_count - in_flight_count`.
- `resolvedFindings` — from `resolved_findings` response field.
- `dismissedFindings` — from `dismissed_findings` response field.
- `lastComputedAt` — from `last_computed_at` response field.
- `refetch` — stable callback to trigger an immediate poll. Unused since P3.0 shrunk `HealthView` to read-only (the dismiss/restore/clear/remediate actions that used to call it were removed with the buttons).

### `HealthIndicator` (`alan/web/src/components/HealthIndicator.tsx`)

In the Shell header, beside the wordmark. Four states:
- Stale/Unknown (grey dot + "Status unknown") — `healthState === null`.
- Healthy (green dot) — `blockedCount === 0 && !queuePaused`.
- Paused (amber dot + "Paused") — `blockedCount === 0 && queuePaused`.
- Degraded (red text "N issues · M needs you") — `blockedCount > 0`.

Beside the status, shows a freshness indicator ("Updated N minutes ago") with absolute timestamp on hover when `lastComputedAt` is present. Hidden when `lastComputedAt` is null.

Links to `/health`.

### `HealthView` (`alan/web/src/features/health/pages/HealthView.tsx`)

Route `/health`. Mounts the collapsed unblocker activity panel just above the collapsed findings disclosure (see "Health page composition" above), so the health metrics stay at the top of the page. The findings list was shrunk to read-only in P3.0 (2026-07-05): **header** ("System Health" heading, no action buttons); a stale notice when `healthState` is `null`; a paused notice when the queue is paused; an empty state when there are zero findings; otherwise a findings table (severity badge, category badge, in-progress chip, description, blocker-flag chips, and links to the origin workspace/PR). No dismiss, restore, clear, remediate, or "show triage" controls remain in this view — HealthCard's ambient link into `/health` is unaffected.

The unscoped view (no `?engine=`) additionally mounts `DriverLoopsPanel` (`features/health/components/DriverLoopsPanel.tsx`), titled "Driver loops", alongside `RepoHealthTable` — see the `/api/driver-loops` section above. Per loop it shows a state chip (`AggregateChip`), next wake, last outcome, final message, and denial count, and covers loading/error/empty/not-supervised states plus a held-by-a-live-interactive-session state chip. It renders no mutation controls at all when the instance isn't supervised (`GET`'s `supervised: false`); when supervised, the ghost `Pause`/`Resume` and (enabled-loops-only) `Run now` buttons stay focusable but `aria-disabled` without a control token (`useControlTokenPresent`), with a `HoverReveal` explaining why — the same disabled-but-focusable convention `components/SubduedActionControl.tsx` already uses. A `role="status"` region reports the outcome of the last mutation the operator triggered from this panel. There is no Restart control and no raw log tail anywhere in this surface.

The unscoped view also renders the daily digest's recovery-trend and residue-tile figures inside `RepoHealthTable.tsx`'s panel via `DigestSignalSection.tsx` (`/api/health/digest`, see above). `useHealthDigest` (`features/health/hooks/useHealthDigest.ts`) gates its fetch off `scopeEngine === null`, so the scoped view issues no digest request and renders neither tile. Residue no longer has any findings-table rendering path: the `unclassified_anomaly`/`pending_disposition` synthetic findings are removed (see "Synthetic finding kinds: removed" above), so the tile is the only place digest residue is shown.

### `WorkspaceList` (`alan/web/src/pages/WorkspaceList.tsx`)

"Show triage" button **removed** from the workspace-list header (predates P3.0).

## Safety / dedup

- `action == "deep_link"` findings are never dispatchable through a health-surface action (no such action remains after P3.0).
- Dismissed findings are filtered server-side; the home-page count and the findings table never show them.
- A dismissed finding re-surfaces automatically if the underlying problem clears and later recurs (because `save_health_state` prunes dismissed IDs against the live active set — once a finding_id leaves the active set, the prune removes it from dismissed, so if the same surface_id later becomes blocked again it is fresh/undismissed).
