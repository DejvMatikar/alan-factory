# Retention — auto-archival and the recurring-workspace registry

Two independent mechanisms live under this name: `alan/retention.py`'s
flag-gated auto-archival of stale `COMPLETED` workspaces, and
`alan/retention_registry.py`'s declarative registry of recurring workspaces
(triage, dependency sweep) and their invocation-slot retention policy.

## Auto-archival — `alan/retention.py`

`archive_stale_completed(queue=..., workspaces_root=..., now=...)` archives
**only `COMPLETED`** workspaces whose `updated_at` is older than
`ALAN_AUTO_ARCHIVE_DAYS` days and that have no active queue work
(`queue.has_active_work`). Never `FAILED`/`ESCALATED`/`AWAITING_INPUT`/`PARTIAL` —
those are unresolved human work and must stay visible. The env var is the single
switch: **unset, empty, non-numeric, or ≤0 = disabled** (the function is a no-op).
`ALAN_AUTO_ARCHIVE_DRY_RUN=1` audits the candidates without archiving — the
advisory rollout mode. E2E and CI never set the flag, so retention is inert there.

Trigger: the triage sweep. `TriageTeam` accepts an injected `run_retention`
callable (the same inversion as `reconcile_incidents`, so `alan.teams` never
imports upward through the `retention` import-linter layer, which sits between
`incident` and `retention_registry`); `routes/infra.py::_build_triage_team` wires
it as a `partial(archive_stale_completed, ...)`. The hook is exception-contained
(`_maybe_run_retention`) — a retention failure can never break the sweep.

Every action (or dry-run candidate) appends one row to
`~/.alan/logs/retention_audit.jsonl` (`{task_id, updated_at, min_age_days,
dry_run, archived_at}`) via the shared `append_jsonl_audit` helper, so every
auto-archive is traceable. The status write flows through the normal
`workspace.transition` → `_write_meta` path, so the `WorkspaceIndex`
reclassifies the row into the archived blob on its next read-validated scan and
the triage job's own lifecycle events refresh the UI over `/api/events`.
Unarchive stays one click (`POST /{id}/unarchive`). The retention archive records
`Meta.pre_archive_status = COMPLETED` (`workspace.transition` saves the status it
replaces on every `ARCHIVED` write), so unarchive restores `COMPLETED` — never
`ACTIVE`, and without queueing or starting any job. The restore does not re-stamp
`terminal_at` but does move `updated_at` forward, so the quiet period restarts from
the restore: the workspace takes no lane seat and counts as no unfinished work, and
retention sweeps it again only after another `ALAN_AUTO_ARCHIVE_DAYS` days without
activity.

Rollout (live since 2026-07): `ALAN_AUTO_ARCHIVE_DAYS=14` is set directly in
`deploy/ai.alan.plist`'s `EnvironmentVariables` — committed and reviewed in
version control, not machine-local `~/.alan/secrets.env` state — with no
observation/dry-run stage. It takes effect only through CI Deploy's
render/`bootout`/`bootstrap` cycle (`deploy/render_plist.sh` re-renders
`~/Library/LaunchAgents/ai.alan.plist` from the tracked template, then
`launchctl bootout` + `bootstrap` re-reads that rendered file — see
`docs/architecture/deploy.md`); `launchctl kickstart -k` restarts the process
but does **not** re-read `EnvironmentVariables`, so a kickstart-only reload
silently keeps the old (possibly unset) value in effect. `~/.alan/secrets.env` is sourced by
`run_alan.sh` *after* the plist environment is loaded, so any stray
`ALAN_AUTO_ARCHIVE_DAYS` or `ALAN_AUTO_ARCHIVE_DRY_RUN` line left over there
from earlier manual testing silently wins over the plist value — check for
one before relying on the committed setting. A malformed or non-numeric value
is caught by `resolve_min_age_days`'s existing parsing: it returns `None` and
auto-archival no-ops, logging a warning but never raising.

Turning the threshold on also activates `compress_archived_streams` as a side
effect: `_compression_enabled()` is `resolve_min_age_days() is not None and
not dry_run_enabled()`, and since this rollout sets no dry-run flag,
compression goes live in the same deploy. Its first pass gzips up to
`_MAX_COMPRESSIONS_PER_PASS` (200) stream files out of the entire *pre-existing*
archived backlog, not just newly-archived workspaces, and drains the
remaining backlog 200 files per subsequent sweep. Treat enabling the
threshold as activating two capabilities at once, not one.

The active-workspace list surfaces the same configured threshold to the UI
via `WorkspaceSummary.auto_archive_days` (only populated for rows whose
status is `is_archivable_status`-eligible), so `WorkspaceList.tsx` can render
an "auto-archives in N days" badge. The badge's countdown is derived purely
from `updated_at + auto_archive_days` versus wall-clock time — it has no
visibility into `queue.has_active_work`, so it can occasionally warn on a row
that survives the next sweep because work resumed. It can never miss warning
a row that is actually archived, since the sweep predicate is a strict subset
of "idle past the threshold." E2E and CI never set `ALAN_AUTO_ARCHIVE_DAYS`,
so `auto_archive_days` is `null` on every row in those environments and the
badge renders nowhere. `GET /api/workspaces` resolves the threshold via
`resolve_min_age_days(log_invalid=False)` — the frequently-polled route
parses a malformed value quietly, since the periodic sweep already logs the
misconfiguration once per pass; the route logging on every poll would spam
`retention: invalid ALAN_AUTO_ARCHIVE_DAYS=...` for as long as the
misconfiguration is live.

## Recurring-workspace registry — `alan/retention_registry.py`

A "recurring workspace" is one the dispatcher re-seeds forever on a cadence
(triage's "Failure Triage Sweep", dependency_sweep's "Dependency Sweep", and dependency_sweep's event-triggered "Dependency Advisory Check", which shares `dep_sweep_audit.jsonl` with the weekly parent).
Each reseed claims a fresh `invocations/inv_NNN` slot and handover file; left
unpruned, either could exhaust its 999-slot allocator over a long enough
uptime. `REGISTRY` is the exhaustive, declarative table of every known
recurring workspace, keyed by **`(team, title)`** — never `team` alone, since
`_dep_sweep_workspace_if_idle` already demonstrates that a team can host more
than one titled recurring workspace and team-alone lookup would silently
apply the wrong entry. Each `RetentionEntry` names the workspace's own audit
log file, so pruning stays traceable in the same `*_audit.jsonl` the team
already writes to (`triage_audit.jsonl`, `dep_sweep_audit.jsonl`) rather than
fragmenting audit trails across a new file.

`seed_recurring_workspace(ws, *, hard_fail=True)` is the mechanical guardrail:
called from `reconciler.py`'s `_seed_triage_sweep_impl` and
`_seed_dep_sweep_impl` at the point a recurring workspace is (re)validated or
created, it looks up `(ws.meta.team, ws.meta.title)` in `REGISTRY` and prunes
stale invocation dirs, handover files, and that team's revisit-count keys
(the same three prunes `alan.teams.triage.prune_triage_invocations` already
performs on every sweep cycle — this is the one-time startup/reconcile-time
counterpart). A registry miss **hard-raises `RetentionRegistryError`** by
default: no recurring workspace can be seeded without a declared policy. A
prune failure, by contrast, is contained and logged — it must never block the
seed.

`_registry_capacity_findings(workspaces_root)` scans every workspace once
(`list_workspaces()` is called a single time) and looks each one up via
`lookup(team, title)` — never rescanned per registry entry — emitting a
`ClassifiedSurface` with `kind="retention_near_cap"` when a workspace's
invocation-slot usage crosses
`_NEAR_CAP_RATIO = 0.9` of the 999-slot cap (~900 slots). Usage counts
existing `invocations/inv_NNN` **directories**, not `list_invocations()`'s
workflow.yaml-having subset: `workflow.create_invocation_dir` claims a slot
by creating the directory before the invocation's `workflow.yaml` is
written, so a workspace interrupted between those two writes still holds
the slot and must still count toward the cap. `alan.health` maps
that kind to `severity="high"`, `action="deep_link"` — `HealthAction` is a
closed literal, so `action="none"` is not an option. The finding's
`workspace_id` is set from the near-cap workspace's own `task_id`, so the
"Retention near cap" row on the health page deep-links straight to it.

### Why the capacity check is injected, not imported

`alan.retention_registry` sits in the import-linter layers list directly
after `retention` — above `alan.teams` and `alan.health` — so it may import
`alan.teams.triage_pregate.ClassifiedSurface` and low primitives
(`workflow`/`handover`/`workspace`) directly, but `alan.teams.triage` and
`alan.health` may **not** import it back (that would be a lower layer
importing a higher one). `TriageTeam` therefore takes an optional
`retention_capacity_findings` callable — the same inversion used for
`run_retention` and `reconcile_incidents` — and calls it
exception-contained inside `_run_sensors`, folding any near-cap surfaces into
the same list the rest of the sensor sweep produces.

As of this PR, no call site wires `retention_capacity_findings` into the
production `TriageTeam` built in `routes/infra.py::_build_triage_team` — the
capability ships declarative-first and inactive, matching the roadmap's
existing "ship the mechanism, activate manually" pattern. Wiring it (and
deciding whether `dependency_sweep`'s own recurring re-seed in
`alan/teams/dependency_sweep.py` should call `seed_recurring_workspace` too)
is a follow-up.
