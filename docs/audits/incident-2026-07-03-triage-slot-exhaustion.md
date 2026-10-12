# Incident: triage heartbeat death by invocation-slot exhaustion

Ledger-format RCA. This document doubles as the format seed for the ROADMAP P3.1 incident
ledger and as its first gold-label entry: symptom → evidence-linked timeline → immediate
cause → root cause → contributing factors → disposition → recurrence signature → residuals.
Written 2026-07-04 during the live investigation, in the same session that produced the
Phase-3 observability amendment.

## Record

| Field | Value |
|---|---|
| incident_id | `2026-07-03-triage-slot-exhaustion` |
| class | `watcher_liveness` / silent expiry |
| opened | 2026-07-03T04:37Z (death) |
| detected | 2026-07-04T~21:30Z — **~43 h later, by a human mid-discussion, not by any alarm** |
| blast_radius | `health_state` stale→suppressed (`null`); `HealthIndicator` grey "Status unknown"; all pre-gate sensors dark (build tasks, main-CI, deploy, recurring errors); P2.1 frozen-`deploy_verify` red→green watcher (#544, merged 2026-07-04) dead on arrival — it rides the sweep cadence and never ticked in production |
| disposition | `root_caused` → fixed by this PR (`fix/triage-heartbeat-slot-exhaustion`): slot pruning at both seeding sites + loud seed failure |
| recurrence_signature | `seed_triage_failed` rows in `triage_audit.jsonl`; `/api/health` returning `health_state: null` with a stale `last_computed_at` while the jobs table keeps advancing |

## Symptom

The triage sweep — the system's only periodic watcher, carrier of health-state computation
and the P2.1 deploy_verify auto-recovery — stopped self-rescheduling after the
2026-07-03T04:36:58Z sweep. The service kept running normally: **~283 queue jobs were
processed after the death**, across multiple deploy restarts, while the health surface
showed "Status unknown" and computed nothing.

## Timeline (evidence-linked)

- **2026-05-26** — `failure-triage-sweep` workspace created (dir mtime). Its invocation
  budget is `_MAX_INV_SLOT = 999` (`alan/workflow.py`), consumed at ~48 sweeps/day when the
  chain is alive: a hard lifetime of ≈ 21 days of continuous operation.
- **June 2026** — two earlier, *different* silent deaths of the same subsystem: the cadence
  self-termination bug (fixed by #351, 2026-06-13; 80.9 h dark) and the triage-auth-outage
  `success`-as-failure misclassification (P1.5 class). Each fix addressed that death; none
  added a liveness contract.
- **2026-06-30 → 07-01** — eight sibling `failure-triage-sweep-2..-9` workspaces appear in
  the production root (meta: `team=triage`, `status=active`, 1 invocation each). These are
  **leaked test workspaces** from the un-sandboxed worker-pytest era (root-caused separately;
  fixed by #466/#491), not prior triage generations — recorded here because unlabelled
  residue from one incident masquerading as evidence in another is precisely what an
  incident ledger prevents. They are invisible in the UI (the workspace list filters
  `team == "triage"`), so nobody saw them.
- **2026-07-03T04:36:58Z** — sweep #999 completes (last `triage_audit.jsonl` row 04:37;
  queue job #3254, the last triage job ever enqueued). The in-run `_schedule_next_sweep`
  then calls `seed_pending_workflow` → `create_invocation_dir` finds all 999 slots taken →
  `WorkflowError: no free invocation slot in failure-triage-sweep` → swallowed by the
  `except Exception` wrapper as one ERROR log line. Chain dead.
- **2026-07-03 → 07-04** — every service restart re-runs `reconciler.seed_triage_sweep`,
  which finds the exhausted workspace, hits the same `WorkflowError`, and swallows it
  (`seed_triage_sweep failed; triage will not start` in `alan.error.log`). No pending triage
  job exists in the queue from here on.
- **2026-07-04T15:45Z, 16:27Z** — the autonomous roadmap-driver journal logs "health clean":
  it read `/api/health`'s stale-suppressed `health_state: null` as *no problems*. Absence of
  signal rendered as absence of problems for the human **and** the autonomous proxy
  simultaneously.
- **2026-07-04T22:12:43Z** — restart after the #549 deploy; seed fails again (traceback in
  `alan.error.log`: `reconciler.seed_triage_sweep` → `workflow.seed_pending_workflow`).
- **2026-07-04T~21:30–22:45Z** — death detected and root-caused manually during a roadmap
  discussion (evidence pulled from `triage_audit.jsonl`, `queue.db` jobs table,
  `alan.error.log`, and a 999-count `ls` of the invocations dir).

## Immediate cause

`create_invocation_dir` allocates `inv_001..inv_999` and raises `WorkflowError` when all are
taken. The triage workspace — the only workspace that lives forever and burns a slot every
30 minutes — reached 999/999. Both seeding sites (startup seed and mid-chain reschedule)
propagate the error into a broad `except` that logs and gives up.

## Root cause

The sweep's carrier is a normal workspace with a finite invocation budget, but the sweep is
an infinite job. Nothing pruned the budget, so **the watcher had a built-in expiry date**;
and because both seeding sites failed silent-by-construction (log line only, no audit row,
no alarm, no retry-with-recovery), the expiry manifested as a silent death. Third distinct
silent death of this subsystem in six weeks — the class-level defect is that watcher
liveness has no contract, so every new death mode is invisible until a human goes digging.

## Fix (this PR)

- `workflow.prune_invocation_dirs(ws)` — keeps the newest `_INV_SLOT_KEEP = 100` slot dirs
  by mtime, removes the rest, returns the removed count; defensive against races
  (any `OSError` ends the pass early with a partial count).
- Called at both seeding sites before `seed_pending_workflow`: `reconciler.
  _seed_triage_sweep_impl` (startup) and `TriageTeam._do_schedule_next_sweep` (per-sweep),
  via `triage.prune_triage_invocations`. **Triage-scoped by call site**: other teams'
  invocation dirs are load-bearing for resume (session ids, per-role result caches) and are
  never pruned; triage is `supports_resume=False`, so old slots are pure exhaust — the
  durable record is `triage_audit.jsonl`, which is untouched.
- Loud failure: both seeding sites now write a `seed_triage_failed`
  `{source: startup|reschedule, error}` audit row when seeding raises; pruning writes
  `invocation_slots_pruned` `{task_id, removed}` when it removed anything.
- Self-healing deploy: the first post-merge service boot finds 999/999, prunes to 100, and
  reseeds — no manual state surgery on the production workspace.
- Regression tests at all three layers: slot-exhaustion → prune → `create_invocation_dir`
  succeeds again (`tests/test_workflow.py`), reschedule recovery + audit rows
  (`tests/test_teams_triage.py`), startup-seed recovery + failure row
  (`tests/test_reconciler.py`).

## Residuals (tracked, deliberately not fixed here)

1. **No dead-man's switch.** A *new* silent death mode still shows up only as
   `seed_triage_failed` rows that nobody is contracted to read. The real fix is the daily
   digest + absence-alarm (ROADMAP P3.2 amendment, same session).
2. **`manager_summary` fires on every sweep completion** — the trigger is any terminal
   workspace transition and the triage workspace completes each tick, so ~48 Haiku calls/day
   generate an unread, overwritten sidecar in the sweep workspace. Fold into P3.0.
3. **Unbounded sweep-workspace growth elsewhere**: `history.json` and `handovers/`
   still grow without cap (~1k entries per slot-lifetime; a slow leak, not lethal). P3.0
   decides whether pipeline deletion moots this.
4. **Eight leaked `failure-triage-sweep-2..-9` shells** remain in the production root
   (team-filtered out of every UI list). Cleanup candidate in P3.0; the leak itself is
   already fixed (#466/#491).
5. `seed_triage_sweep` still picks the *first* `team == "triage"` workspace when several
   exist; harmless post-prune (any pick recovers), but the ambiguity goes away with the P3.0
   cleanup.
