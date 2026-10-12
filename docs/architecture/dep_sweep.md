# Autonomous dependency-update pipeline (`dependency_sweep`)

Self-triggering, per-engine recurring sweep that drives Dependabot proposals to merge for
every engine opted in (`EngineConfig.dependency_sweep.enabled`, see `engine_config.md`) and,
for Python engines, audits the locked dependency set for newly-published vulnerabilities —
weekly, and immediately whenever the triage sweep sees main CI red (see "Red-main advisory
recovery" below).
Deterministic orchestration only (`_MODEL = "none"`) — it reuses the existing `pr_merge`,
`implementation_fix`, and merge-recovery capabilities rather than introducing a new
code-writing path. Only the Dependabot-PR half is generalized per engine; failure triage
(`alan/teams/triage.py`) stays fleet-global and is unaffected by any of this.

## Engine-awareness (`SweepContext`, per-engine cadence, tagging)

Every forge-reaching helper in `alan/teams/dependency_sweep.py` takes a trailing,
required keyword-only `ctx: SweepContext` parameter instead of reading a
module-level `_REPO_SLUG` singleton (removed):

```python
@dataclass(frozen=True)
class SweepContext:
    forge: ForgeTarget        # which repo + credential_env_name to act against
    run_vuln_scan: bool       # cfg.dependency_sweep.enable_python_vuln_scan
    posture: Posture          # live_floored_posture(cfg.posture, engine_key)
```

`DependencySweepTeam._sweep` resolves the real `SweepContext` once per run from the
workspace being swept — `governing_config_for_ws(workspace)` for the `EngineConfig` and
`forge_target_for_ws(workspace)` for the `ForgeTarget` (both from `_pr_branch.py`, the same
snapshot-first resolution every other forge-consuming call site uses). The sweep no longer
carries a credential: its forge interactions go through `ForgePort` (`alan/forge.py`),
and the adapter (`alan/forge_gh.py`) resolves the engine's own transport credential
(`secrets_store.TRANSPORT_PURPOSE`, falling back to `forge.credential_env_name`, never
raising: an unresolved credential degrades to ambient `gh` auth, rather than crashing the
sweep) per operation — for the proposal listing and the advisory audit's default-branch and
file reads, but not for the changed-files read, which keeps the ambient identity by decision
(`forge_port.md`, "X2.7 PR6"). Every port call carries the target
`forge_target=ctx.forge`, so a second engine's sweep never silently reads or writes against
alan's own ambient checkout. There is no default context: every call, production and test,
builds and passes an explicit `SweepContext`, so a caller that forgets it is a type error
rather than a silent fallback to alan's target. The real dispatch path
(`DependencySweepTeam.run` → `_sweep` → `_run_sweep`) builds the context from the workspace
being swept; the sweep tests pass a context on alan's target with the vulnerability scan on
(`forge_port.md`, "X2.7 PR19").

Parent and child workspaces are tagged with `engine_key` (threaded as a separate, simpler
keyword-only parameter alongside `ctx` through `_run_sweep` → `_dispatch_batch` →
`_act_on_proposal` → `_dispatch_proposal`/`_escalate_upstream_blocked` → `_ensure_pr_workspace`)
so both list and detail views resolve to the correct engine and any downstream fix/merge job
dispatches against the right repository.

`cfg.dependency_sweep.enable_python_vuln_scan` gates `_run_vuln_scan` — when `False` (the
default for any engine that doesn't explicitly opt in, e.g. a non-Python engine) it returns immediately, running no `pip-audit` and opening no
advisory workspaces. The same flag gates the red-main trigger. alan's own default engine
config sets this `True`.

## Scheduling

- Seeded at startup by `reconciler.seed_dep_sweep` (wired in `alan/routes/infra.py`
  alongside `seed_triage_sweep`), which now fans out over `engine_config.registered_engines()`
  (mirroring `triage_pregate.gather_all`'s per-engine iteration) with a per-engine
  try/except — one engine's seed failure is logged and never blocks another engine's sweep.
  An engine with `dependency_sweep.enabled=False` is skipped entirely (no workspace, no
  queue-meta read/write for it).
- Cadence gate: `dep_sweep_next_run` (`queue_meta`) is namespaced per engine as
  `f"dep_sweep_next_run:{slug}"`; a lookup that misses the namespaced key falls back to the
  legacy bare `dep_sweep_next_run` key **only for the default (alan) engine's slug** — a
  one-time migration read so an existing production schedule isn't lost the first time this
  shipped. Every write goes to the namespaced key only; once written, the bare key is never
  consulted again for that engine.
- Alignment: `_next_tuesday_09_utc` — one day after the Dependabot batch, so the sweep
  defers to and never duplicates the bot. This alignment function is unchanged and applies
  identically to every opted-in engine (it is a UTC wall-clock alignment, not alan-specific).
- `DependencySweepTeam._do_schedule_next_sweep` re-enqueues itself
  `+cfg.dependency_sweep.cadence_seconds` (default 7 days) after each run, recording the
  per-engine-namespaced `f"dep_sweep_last_scheduled_at:{slug}"` queue-meta key (write-only
  telemetry; nothing reads it back) and persisting a `DependencySweepStatus` snapshot (below).
- The `dependency-sweep` workspace is internal (created with `team="dependency_sweep"`,
  not surfaced in the default workspace list) — like `triage` — for every engine; multiple
  engines' parent workspaces share the literal title `DEP_SWEEP_TITLE = "Dependency Sweep"`
  and are told apart by `Meta.engine_key` (`canonical_scope_key(ws.meta.engine_key) ==
  cfg.repo`), not by title.
- The event-triggered "Dependency Advisory Check" workspace is a second, separate hidden
  workspace per engine. It is never scheduled by this cadence: the weekly seed, the
  `dep_sweep_next_run:{slug}` key and the self-rescheduling above belong to the weekly
  parent alone.

## Per-engine status (`DependencySweepStatus`)

`DependencySweepTeam._do_schedule_next_sweep` persists a small status snapshot after every
run, atomically (temp file + `os.replace`, best-effort — an `OSError` is logged and swallowed)
under the SAME per-forge state directory (`alan.teams._pr_state_paths._engine_state_dir(forge)`)
the sibling `pr_*.json`/`engine_preflight.json` writers use, as `dep_sweep_status.json`:

```python
class DependencySweepStatus(BaseModel, frozen=True):
    slug: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
```

`load_dependency_sweep_status(forge)` reads it back, returning `None` when absent or
malformed. No new `ALAN_*` env var — this reuses the existing `ALAN_STATE_ROOT`-rooted
per-forge state tree. `GET /api/engines/{owner}/{repo}/dependency-sweep` (no control token
required, mirroring the preflight GET) surfaces it to the ops-console tile as
`{slug, enabled, last_run_at, next_run_at}` — `enabled` is always read live from
`EngineConfig.dependency_sweep.enabled` (never from the persisted status snapshot, which
would go stale the moment an operator toggles after a run), while `last_run_at`/`next_run_at`
come from the snapshot and are both `null` until the sweep has run at least once. The
missing-config `Finding` itself is never fetched by this GET — it only ever surfaces as the
422 body of the `POST .../dependency-sweep` toggle attempt that `check_dependency_sweep_enable`
blocks, so routine status polling never shells out to `gh`.

## Per-run flow (`alan/teams/dependency_sweep.py`, `alan/dep_sweep.py`)

1. `_fetch_open_proposals` lists open `app/dependabot` PRs; `_to_proposals` tags each
   with the lockfiles it touches (`uv.lock`, `package-lock.json`, `yarn.lock`,
   `poetry.lock`) and probes its failing CI job for an npm ERESOLVE peer-cap signature
   (`_detect_upstream_block` → `parse_peer_cap`, see "Upstream peer-cap blocks" below).
2. `group_proposals` unions proposals that share a lockfile into a `ProposalBatch`.
   `_dispatch_batch` dispatches **at most one PR per batch per sweep** — the
   shared-lockfile serialization invariant (two PRs touching one lockfile are never
   processed concurrently; the rest of the batch waits for a later sweep). The batch is
   skipped only when a sibling is genuinely **in flight** (a PENDING/RUNNING queue job);
   a proposal whose `dep/<N>` workspace is **terminally stuck** (`_needs_human` →
   `awaiting_input`/`escalated`/`failed`/`paused`) is *skipped, not re-dispatched*, so the
   sweep advances to the next open sibling instead of churning a head-of-line PR forever.
3. `classify_proposal`: a proposal carrying an `upstream_block` → `UPSTREAM_BLOCKED`
   (escalated, never dispatched — see "Upstream peer-cap blocks" below);
   `CONFLICTING` → `NEEDS_REBASE` (→ `implementation_fix`); otherwise `MERGE_READY`
   (→ `pr_merge` when `_merge_enabled()`).
4. `_ensure_pr_workspace` creates a `dep/<N>` sub-workspace and seeds a bootstrap
   handover with `dependency_bot_authorized=True`.
5. `_run_vuln_scan` runs `dep_advisory.run_advisory_check` with trigger `weekly` (when
   `enable_python_vuln_scan`): the same check the red-main trigger runs — it opens fix /
   no-fix workspaces and never files an issue or writes a suppression. It runs **before** the
   proposals are listed and dispatched (the numbering above is the Dependabot half's own order),
   so a fix opened in this run already holds back the `uv.lock` batches. See "Red-main
   advisory recovery" below.

## Red-main advisory recovery

A newly published advisory against a locked Python dependency turns main's security audit red
with no change on main. That happened three times in six weeks (pip on 08-21, anyio on 09-18,
urllib3 on 10-01), and each needed a lockfile-only PR. The fleet now notices and fixes it through the normal
workspace → PR → gates → merge path. Detection is deterministic (no model calls); only the fix
itself is model work, and it is the existing `implementation` team.

### One audit definition (`alan/pip_audit_gate.py`)

CI and alan's check share one module, so scope and suppressions cannot drift:

- `EXPORT_ARGV` is `uv export --frozen --all-extras --no-emit-project --format requirements-txt`
  — the exported lock with every extra, excluding the local project. `audit_argv` builds
  `python -m pip_audit --strict --disable-pip -r <requirements>` plus one `--ignore-vuln` per
  suppressed ID (and `--format json` for alan's parser). `run_audit(project_dir, ...)` exports,
  then audits, inside `project_dir`.
- The `security-audit` job in `.github/workflows/tests.yml` runs
  `uv run python -m alan.pip_audit_gate --check` (the `deps` path filter on that step is
  unchanged: when CI runs the audit is out of scope). `tests/test_pip_audit_gate.py` parses the
  workflow and asserts the step runs exactly that command and that no workflow carries its own
  `--ignore-vuln`.
- There is one suppression list: `[tool.pip-audit] ignore-vuln` in `pyproject.toml`
  (`read_suppressed_ids`). An absent table means no suppressions; a malformed one raises
  `GateConfigError` (exit 2 in CI, `failed` in alan's check), never silently "none". The two
  standing no-fix suppressions — `PYSEC-2025-183` (pyjwt, disputed, no fix version) and
  `MAL-2026-4750` (fastapi's `standard` extra, not resolved into our lock) — live there with
  their rationale, moved verbatim from the workflow.
- `pip-audit` and `packaging` are runtime dependencies (`[project].dependencies`), not dev-only:
  the production venv is synced without the dev extra, so the weekly scan used to run
  `uv run pip-audit` against a venv that did not have it, got empty stdout and recorded "no
  vulnerabilities" — a silent no-op. `packaging` backs the version arithmetic below.

### The audit (`alan/teams/_dep_advisory_audit.py`)

`audit_locked_dependencies(forge)` resolves the default branch (`ForgePort.default_branch`),
fetches `pyproject.toml` and `uv.lock` at that ref (`ForgePort.repo_file`, decoded by
`forge.decode_contents_payload`), writes both into a `TemporaryDirectory` and runs the shared
export + audit there with `sys.executable -m pip_audit`. It never reads or writes the service's
live checkout. The result is `AuditResult(outcome, vulns, suppressed, pyproject_text, detail)`:

- `ok` — parsed advisories (`parse_audit_json` accepts the `{"dependencies": [...]}` shape and
  the legacy bare list; entries carrying `skip_reason` are packages pip-audit could not audit,
  not advisories, and are skipped; every other entry must carry a string `name`, a string
  `version` and a list `vulns` — an empty `{}` or truncated entry is a failure, never a clean
  audit; anything unparsable is a failure).
- `no_lock` — the repo has no `uv.lock`.
- `failed` — every other outcome fails closed: unreadable default branch or files, a malformed
  suppression table, `uv` missing, a timeout, a non-zero export, `pip_audit` not importable, or
  a non-zero/zero audit whose stdout does not parse. A failed audit is recorded as
  `vuln_audit_failed`, never as "nothing found".

Limits: a uv workspace whose members or path sources are not in those two files cannot export
and becomes `failed`; `--frozen` trusts the lock without checking it against `pyproject.toml`;
the GitHub contents API carries files up to 1 MB inline (`uv.lock` is about 360 KB), beyond
which the read fails closed.

### Trigger (`alan/dep_advisory_seed.py`)

The triage `main_ci_red` step (see [triage.md](triage.md)) calls
`seed_advisory_checks(queue, workspaces_root, runs)` with one `RedMainRun(slug, run_id, sha)` per
red `main_ci` surface — for any reason main is red. Per engine, each in its own try/except so
one engine's failure is logged and never blocks another:

- skipped unless the engine is registered, not hidden, and has both
  `dependency_sweep.enabled` and `enable_python_vuln_scan`;
- skipped when `dep_advisory_checked_run:{slug}` already equals the red run id, so the audit
  runs once per distinct red CI run however many sweeps see it;
- single-flight: skipped while the engine's advisory-check workspace has a PENDING or RUNNING
  job;
- otherwise it prunes (`retention_registry.seed_recurring_workspace`), wakes the workspace to
  ACTIVE (a finished run leaves it COMPLETED, and retention may have archived it), seeds a
  pending `dependency_sweep` workflow (`ADVISORY_SPEC`, the `sweep_runner` node), records
  `dep_advisory_pending_run:{slug}` = the run id and enqueues the job. The sequence is
  transactional: if it fails after the workspace was created this round, the new workspace and
  its jobs are deleted; if it fails after the workflow was seeded on a reused workspace, the
  pending run and the seeded invocation are dropped. Either way the next sweep retries from a
  clean single-flight state instead of accumulating orphaned invocations.

The job records the pending run as checked (`dep_advisory_checked_run:{slug}`) only after an
audit whose outcome is not `failed` and that left nothing to retry; a failed audit, or a run that
recorded `vuln_fix_deferred`, `vuln_workspace_failed`, `vuln_ledger_write_failed` or
`vuln_workspace_dry_run`, is therefore retried by the next sweep over the same red run. Seeding never touches `dep_sweep_next_run:{slug}`,
`dep_sweep_last_scheduled_at:{slug}` or `dep_sweep_status.json`.

### Run modes are title-based

The hidden workspace is `team="dependency_sweep"`, `title == DEP_ADVISORY_TITLE = "Dependency
Advisory Check"`, provenance `DEP_ADVISORY_CHECK`, one per engine, registered in
`retention_registry` (it shares `dep_sweep_audit.jsonl`). `DependencySweepTeam` branches on
`dep_advisory.is_advisory_workspace(ws)`:

- **advisory mode** (trigger `main_ci_red`): runs only the check. It skips the
  Dependabot-proposal half and `_schedule_next_sweep`, so the weekly sweep's cadence is
  unchanged. It still honours `enable_python_vuln_scan` (a disabled engine records
  `vuln_scan_disabled`).
- **weekly mode** (trigger `weekly`): runs the same check first, then the Dependabot half, so
  the lockfile serialization below also covers an advisory found in this very run.

The weekly parent lookup (`reconciler._is_dep_sweep_parent_for`) matches the exact title
`DEP_SWEEP_TITLE`, so it never mistakes the advisory workspace for the weekly parent, and the
advisory lookup never mistakes the weekly parent for its own.

### Lowest clearing version (`alan/dep_sweep.py`)

`plan_fixes(vulns, suppressed)` drops suppressed IDs, groups advisories by canonical package
name and returns `AdvisoryPlan(fixes, unfixable)`. For one package the target is the smallest
version, drawn from the union of the advisories' stable fix versions above the installed
version, that clears every advisory of that package: a candidate clears an advisory when some fix
of that advisory is at or below it within the same major, or when it is at or above that
advisory's highest fix. A max-of-minimums rule fails across release branches (installed 1.26.5,
one advisory fixed in 1.26.18 and 2.0.7, another in 2.0.2: the answer is 2.0.7); this rule always
yields a clearing target and never merely the latest release. Pre-releases and non-PEP 440 fix
strings are never targets. An advisory with no usable fix is `unfixable`; a package with both
fixable and unfixable advisories gets a fix for the former and a no-fix entry for the latter. Should
a package's fixable advisories ever share no clearing version, the whole package group is returned
as `unfixable` (never dropped), so it escalates as no-fix instead of reading as "nothing found".
`widen_requirement(requirement, target)` returns the requirement widened to admit the target
(upper bound to `<{major+1}`, or `<0.{minor+1}` for 0.x; `==x` becomes `==target`; `~=` becomes a
`>=`/`<` pair; a blocking `!=` is dropped; extras and markers are preserved) or `None` when it
cannot (a lower bound above the target).

### Workspaces (`alan/teams/dep_advisory.py`)

All three kinds are `team="dependency_sweep"`, provenance `DEP_ADVISORY_FIX`, **visible on the
board by default**, tagged with the engine's `engine_key`, and titled so the owner can see a
red-main fix is under way:

| kind | title | what happens |
|---|---|---|
| fix | `sec/<package>: <advisory>` (`(+N more)` when the run has more advisories) | one workspace for every fixable advisory found in the run; a bootstrap handover carries the brief as `plan_synthesis`, `forbidden_actions`, `files_to_edit` and `dependency_bot_authorized=True`, then an `implementation` job is enqueued |
| no fix | `sec/<package>: <advisory> (no fix)` | one workspace for every no-fix advisory of the run, ESCALATED immediately with a terminal handover naming each advisory, package and installed version and stating that no fixed release exists; no job is enqueued |
| posture | same as fix | the engine's live posture is below `propose`: ESCALATED straight away with the fix brief in the handover instead of a dispatch |

The fix then travels the existing path — `implementation` → `verification` → `pr_merge` → merge
through the normal gates, with no human approval. `dependency_bot_authorized=True` makes
verification treat it as scenario-exempt exactly like a Dependabot bump
(`scenario_binding._legacy_waived`), and a `pr_merge` failure routes through the dep-bot path to
`implementation_fix`, bounded by the loop guard's `REVISIT_CAP`, which escalates instead of
looping. The host never writes commits or edits the checkout.

The brief (`_dep_advisory_brief.build_fix_brief`) is a plan whose single `## Implementation
steps` section (the only part `implementation` reads) carries: widen the declared range of every
direct dependency whose range excludes the target first (the exact old and new specifier line,
checked against `[project].dependencies`, each optional-dependency extra and each dependency
group); one exact `uv lock --upgrade-package <package>==<version>` per package, stopping and
reporting the resolver output if a parent package caps it; when `pyproject.toml` changed and
`alan/drift_gate.py` exists, a dated line under `## Advisory fix log` in this file so the drift
gate is satisfied; the exact verify commands (`uv export … --output-file` into an engine-scoped file under
`/tmp`, then `pip-audit` with the engine's suppressions); and the forbidden list — never edit `ignore-vuln`, never weaken a
gate, touch nothing beyond `uv.lock`, `pyproject.toml` and this file. Only advisory IDs, package
names and versions matching a strict `[A-Za-z0-9._-]` (versions also `+` and `!`) charset ever
reach a prompt or a title; anything else is dropped by `sanitize_plan` and counted as
`vuln_dropped_invalid`, and free-text OSV fields (descriptions, aliases) are not even kept by the
parser.

### Dedup ledger

`dep_advisory_ledger.json` lives in the per-forge state directory
(`_engine_state_dir`, the same tree as `dep_sweep_status.json`) and maps each advisory ID to the
workspace that covers it (`task_id`, `kind`, `recorded_at`). One module-level `threading.Lock`
holds load → decide → create → save together, so concurrent checks cannot both open a
workspace for the same advisory. A corrupt ledger loads empty and is rewritten by the next save.

- **Covered** (`is_covered`): the workspace exists and is neither COMPLETED nor ARCHIVED — so
  in-flight, escalated, failed and paused workspaces all cover — or is COMPLETED less than six
  hours ago. A deleted or archived workspace does not. A no-fix entry covers only while the
  advisory still has no fix; once it becomes fixable a fix workspace opens beside the escalated
  one. A covered set records `vuln_already_covered` and creates nothing.
- **Deferral**: a new fixable set defers (`vuln_fix_deferred`, nothing recorded) while another
  fix workspace is ACTIVE and younger than 24 hours, so two fix PRs never compete for the lock.
  The bound keeps a stuck workspace from blocking recovery forever. No-fix and posture
  workspaces are never deferred.
- **Rollback**: if creating or enqueueing fails, the half-built workspace and its jobs are
  deleted (`vuln_workspace_failed`); if the ledger cannot be saved, every workspace the run just
  opened is removed (`vuln_ledger_write_failed`). The next trigger retries.

### Shared-lockfile serialization (revised)

The "at most one PR per lockfile at a time" invariant now spans both producers: `_dispatch_batch`
skips a batch containing a `uv.lock` while `dep_advisory.fix_in_flight(forge, workspaces_root)`,
so a Dependabot lockfile PR is never processed beside an advisory fix. The weekly sweep opens its
advisory fix before dispatching any batch, so the check sees a fix found in the same run. Other
lockfile batches are unaffected, and the weekly batching and cadence are unchanged.

### Audit trail and dry run

Every `dep_sweep_audit.jsonl` `sweep_complete` row carries `trigger` (`weekly` or `main_ci_red`)
and so does every entry: the advisory check stamps its own entries, and `_write_audit` stamps the
row's trigger on the rest (the weekly `dispatch` and `escalate_upstream_blocked` proposal entries),
keeping any trigger an entry already carries. The advisory vocabulary: `vuln_none_found`, `vuln_audit_failed`,
`vuln_audit_no_lock`, `vuln_scan_disabled`, `vuln_fix_workspace_opened`,
`vuln_no_fix_workspace_opened`, `vuln_posture_workspace_opened`, `vuln_already_covered`,
`vuln_fix_deferred`, `vuln_dropped_invalid`, `vuln_workspace_failed`, `vuln_ledger_write_failed`
and `vuln_workspace_dry_run`.

`ALAN_DEP_SWEEP_DRY_RUN` (truthy set; set in the e2e `serverEnv` because the reconciler seeds a
real `dependency_sweep` server on startup) now gates workspace creation: the check audits and
records `vuln_workspace_dry_run` with the would-be kind and title, and creates no workspace and
no ledger entry.

### Removed

Security GitHub issue filing (`_open_gh_issue` and its search/label helpers), the
`[tool.pip-audit]` suppression write (`_append_suppression`), `classify_vuln`/`VulnClass` and the
"open Dependabot proposal covers it" ignore, and the `ALAN_DEP_SWEEP_PYPROJECT` override. The
advisory check never suppresses an advisory automatically and never writes to the service's live
checkout; the dedicated workspace replaces both the issue and the suppression. The test-suite
`gh` backstop in `tests/conftest.py` (`ci_isolation.md`) still raises on any real state-mutating
`gh` call, independent of this module.

## Parent vs child visibility

The recurring parent workspaces, each per-PR child workspace and each advisory workspace carry
`team="dependency_sweep"`, but they are treated differently for default visibility:

- **Parent** (`title == DEP_SWEEP_TITLE = "Dependency Sweep"`): hidden from the default list, exactly
  like `triage`. It is background cron plumbing — the user does not need to watch it.
- **Advisory-check parent** (`title == DEP_ADVISORY_TITLE = "Dependency Advisory Check"`): hidden from
  the default list too — it is the red-main audit's plumbing.
- **Advisory workspaces** (`title == "sec/<package>: <advisory>"`, with an optional `(+N more)` and
  `(no fix)`): visible by default, so the owner can see a red-main fix is under way or waiting on
  them.
- **Children** (`title == "dep/<pr_number>"`): visible by default so a stalling dependency update can
  be monitored like any other piece of work. `is_hidden_by_default` in `alan/app/workspace_creation.py`
  applies this distinction on both the list and detail surfaces. `?include=triage` reveals the hidden
  parents.

The discriminating field is the workspace title because it is set deterministically at creation time:
`reconciler._dep_sweep_workspace_if_idle` uses the constant `DEP_SWEEP_TITLE` for the parent,
`dep_advisory_seed._advisory_workspace_if_idle` uses `DEP_ADVISORY_TITLE` for the advisory-check parent,
`dependency_sweep._ensure_pr_workspace` uses `f"dep/{pr_number}"` for each child and
`dep_advisory` titles each advisory workspace with `fix_title`/`no_fix_title`. The summary row
also carries a `team` field (sourced from `meta.team`) so the frontend can badge dep children
without inferring team membership from the title.

## Single switch

`ALAN_DEP_SWEEP_MERGE` (`dep_sweep._merge_enabled`, default on): `"0"` makes the sweep
stop just short of merge (`merge_target` returns `None` for `MERGE_READY`), leaving
proposals for human merge while still rebasing conflicts and auditing vulns. It does not gate the
advisory fix, which is not a Dependabot proposal.

## Upstream peer-cap blocks (`UPSTREAM_BLOCKED`)

Some bumps cannot be merged by *any* lockfile rebase because an upstream package
peer-caps the bumped dependency below the proposed version (e.g. `typescript` 7 vs
`typescript-eslint@8.64.0`, whose peer range is `typescript >=4.8.4 <6.1.0`). The bump
has no git conflict, so it classifies `MERGE_READY`, but `npm ci` aborts at install with
an `ERESOLVE` "Conflicting peer dependency" error. Left to the bot-authorized recovery
below, such a PR loops `pr_merge ↔ implementation_fix` — `npm install` re-fails ERESOLVE
each time — until `REVISIT_CAP` escalates it with a generic "revisit cap exceeded".

To short-circuit that futile loop the sweep detects the block at classification time:

- The probe runs only for PRs that touch an npm lockfile (`_touches_npm_lockfile`:
  `package-lock.json` / `yarn.lock`) — a peer cap is an npm concept, so Python-only
  (`uv.lock`/`poetry.lock`) bumps skip it and make no extra `gh` calls.
- `_detect_upstream_block` (`_to_proposals`) reads the PR's `statusCheckRollup`
  (`_pr_branch.fetch_failing_job_ids` → every failing check, using the shared
  failing-conclusion set `{FAILURE, TIMED_OUT, ACTION_REQUIRED}`), and scans each
  failing job's `gh run view --log-failed` (`_pr_branch.fetch_failed_log`) —
  returning the first log that `parse_peer_cap` (`alan/dep_sweep.py`) resolves into a
  `PeerCapBlock(dependency, capping_package, peer_range)`. Scanning all failing jobs (not
  just the first) means an ERESOLVE in a non-leading job is still caught. Any
  missing/passing/unparseable check yields `None` and today's behaviour is unchanged.
  `fetch_failing_job_ids`/`fetch_failed_log` (along with the pure `failed_job_id`/
  `failing_job_ids` helpers) now live in `alan/teams/_pr_branch.py`, not
  `dependency_sweep.py` — they moved so the E2E-failure fetch path
  (`_pr_branch.fetch_failing_e2e_tests`, see `merge_recovery.md`) could reuse the same
  job-id primitives instead of duplicating them. Since 2026-08-20 (#1162) that E2E path
  reads each failing job's check-run annotations rather than its rendered log, so
  `fetch_failed_log` had exactly one consumer: this ERESOLVE scan, which needs the npm
  error text itself, not a workflow-command annotation. That is no longer true — it has
  two consumers. `_fix_gate_checks.evidence_for` also calls it as the fallback when a
  failing check's annotations are insufficient (see `pr_iteration_pipeline.md`'s
  failing-check section), tail-bounded and filtered there. `dependency_sweep`'s own
  behaviour is unchanged. `_detect_upstream_block`
  now calls them with `forge_target=ctx.forge` instead of the module's former
  `ctx: SweepContext` convention. Each helper reaches the forge through the port
  (`fetch_failing_job_ids` through `status_check_rollup` and `fetch_failed_log` through
  `failed_job_log`, X2.7 PR15), whose adapter resolves the forge-scoped `GH_TOKEN`
  (`secrets_store.transport_credential`, the same `secrets_store.TRANSPORT_PURPOSE`
  credential it resolves for the sweep's listing, search and write operations), so a
  non-default forge's `gh pr view`/`gh run view --log-failed` calls stay authenticated
  instead of falling back to ambient `gh` auth. One function, `_to_proposals`, reads a
  proposal's files under the ambient identity (`read_pr`) and its failing jobs under the
  transport identity, on purpose. Behaviour
  for `_detect_upstream_block`/`parse_peer_cap` callers is otherwise unchanged.
- `classify_proposal` returns `UPSTREAM_BLOCKED` whenever a proposal carries an
  `upstream_block`; `merge_target` returns `None` for it (never merged).
- `_dispatch_batch` → `_act_on_proposal` escalates a blocked PR instead of dispatching it:
  `_escalate_upstream_blocked` seeds the `dep/<N>` workspace, saves a terminal handover
  whose `full_synthesis` is a specific, actionable diagnostic (`_escalation_message`:
  which dependency, the capping peer + range, and the recommended
  `.github/dependabot.yml` semver-major `ignore` rule — mirroring the eslint precedent,
  PR #402), and sets the workspace `ESCALATED`. It returns `False` so the escalation does
  **not** consume the batch's single dispatch slot — a genuinely-mergeable batch sibling
  still gets dispatched this sweep. `_needs_human` skips the escalated PR on every later
  sweep, so it escalates exactly once.

The escalation is a recommendation, not an action: the sweep never closes the PR or edits
`dependabot.yml` (those stay human/`gh` writes, consistent with the read-only stance of
`triage`). Which durable fix a human applies depends on *who owns the cap*, and
`_escalation_message` cannot tell the two classes apart — it proposes the `ignore` entry
for both:

- **Third-party cap.** The capping package does not ship the capped one, so no version of
  either resolves the conflict: `eslint-plugin-jsx-a11y` caps `eslint` (PR #402),
  `typescript-eslint` caps `typescript` (PR #751). Here the `dependency-name` semver-major
  `ignore` entry the message proposes is correct — the bump really is unresolvable.
- **Same-family lockstep split.** The capping package ships from the same project as the
  capped one and peer-pins it to the *exact* version (`@vitest/coverage-v8@X` declares
  `peer vitest@"X"`). Nothing upstream is blocking anything: the pair only arrived as two
  mutually-unmergeable PRs because the `frontend-deps` group is scoped
  `update-types: ["minor", "patch"]`, so a major leaves the group and Dependabot opens one
  PR per package — each of which alone fails `npm ci` with ERESOLVE. The durable fix is a
  Dependabot **group** that covers `major`, listed *before* `frontend-deps` so it wins the
  first-match-wins ordering: the `vitest` group added by PR #1535, which landed vitest 5
  after #1385 and #1386 had each sat red for a day. An `ignore` here would freeze a healthy
  dependency against a cap that does not exist.

An exact peer version (`peer vitest@"4.1.11"`, not a range) is the tell for the second class.

## Bot-authorized failure recovery (`pr_merge`)

A stale lockfile is **not** a git conflict — such a PR is `MERGEABLE`, classifies as
`MERGE_READY`, and its staleness only surfaces as a failing `uv sync --locked` CI check
*inside* `pr_merge`. Verify locally with `uv sync --locked --extra dev` — the `dev` extra
  pulls in the dev-only deps (`pytest-asyncio`, `pytest-cov`, etc.) the full gate suite needs, so a
  bare `uv sync --locked` can look clean while `pytest` still fails on a missing plugin. To
  keep these proposals moving without a human:

- `pr_merge._build_handover`: when the outcome is `failed`/`timeout` **and** the prior
  handover carries `dependency_bot_authorized`, it routes to `implementation_fix` via
  `_dep_bot_rebase_handover` (instead of `_await_human_handover`). The forward handover
  carries `_DEP_REBASE_DIRECTIVE` (merge `main`, regenerate lockfiles with `uv lock` /
  `npm install`, never weaken a gate) and re-sets `dependency_bot_authorized=True`. Because
  `implementation_fix` is a Layer-3 decision-required target, the handover **must** carry a
  non-empty `decisions` list (`_dep_rebase_decision`) or `save_handover` rejects it
  (`handover._validate_layer3_decisions`) and the job fails — mirroring the existing
  `_copilot_decision` / `verification._decision_from_judge` reroutes.
- `implementation_fix` checks out the PR branch, the host pushes (force-with-lease) →
  CI re-runs. The same path absorbs stale-lock, behind-`main`, and flaky-gate failures
  (a rebase+push re-runs CI). `_authorization_block` (`_pr_handover.py`) already injects
  the pre-authorization prompt section when the flag is set.
- The flag is also threaded through `_escalation_handover` / `_await_human_handover` so
  authorization survives every re-dispatch (it was previously reset to `False` on each
  `pr_merge` hop). The `implementation_fix ↔ pr_merge` loop is bounded by the loop
  guard's `REVISIT_CAP`, which escalates to a human if regeneration does not converge —
  the "stop on genuinely human conditions" safety valve.
- `pyproject.toml` is one of the CLAUDE.md drift gate's `LOAD_BEARING_FILES`
  (`alan/drift_gate.py`), so a routine `python-deps` Dependabot bump also fails the
  drift-gate CI check, not just `uv sync --locked`. `_dep_bot_rebase_handover`'s rebase
  step does not touch docs on its own; the `implementation_fix` pass handling the recovery
  must also add a small touch under `docs/architecture/*.md` (this file, for lockfile-only
  bumps) alongside the lockfile regeneration commit so the drift gate clears together with
  the coverage/lint gates.
- After the rebase, `implementation_fix` forwards to `verification`, which treats a
  `dependency_bot_authorized` inbound handover as **scenario-exempt**:
  `verification._scenarios_waived` returns `True` on the flag, so a lockfile-only bump routes
  through the `SCENARIOS_WAIVED` path instead of stalling on `SCENARIOS_MISSING` (a dep bump has
  no meaningful test scenarios and is gated by the full CI suite). Real correctness gating is
  unaffected — `effective_verdict` still forces `REQUEST_CHANGES` on any critical diff finding.
  See [acceptance_gate.md](acceptance_gate.md).
- A pure dependency-floor bump (e.g. `fastapi`/`uvicorn`/`import-linter` minimum-version
  raises with no code touched) never needs an `alan/` or `tests/` change — `uv lock`
  resolves the new floor and the rebase commit only needs to also touch a file matching
  `_DOC_PATTERNS` (this file, `CLAUDE.md`, or another `docs/architecture/*.md`) to satisfy
  `drift_gate.drift_gate_violated`; the drift gate only checks path globs, not content, so
  the doc edit can be as small as this bullet.
- Re-running `uv lock` against a merged, up-to-date `main` can pull in *more* than the
  Dependabot-proposed floors: transitive pins (e.g. `mypy`, `ruff`, or build-tool-only
  packages like `ast-serialize`/`librt`) resolve forward too if the merge base had moved
  since the proposal was opened. This is expected — the rebase step regenerates the whole
  lock, not just the bumped line — and is still covered by the "no code touched" case above
  as long as the diff stays confined to `uv.lock`/`pyproject.toml`.
- A proposal branch left open across several sweep cycles can fall dozens of commits
  behind `main` before it is picked up (dependabot only ever touches `pyproject.toml`,
  so the merge/rebase itself is conflict-free even after a wide divergence); the
  regenerate-lock-and-touch-a-doc recovery above applies unchanged regardless of how far
  behind the branch had drifted.
- `main` itself can independently touch `pyproject.toml` in the same window (e.g. new
  `[tool.importlinter]` `layers` entries for modules added after the proposal was opened) —
  the merge stays conflict-free because dependabot's version-bump lines and main's
  unrelated additions land in disjoint parts of the file; `uv lock` afterward resolves
  against the merged, not the proposal's original, `pyproject.toml`.
- Observed instance (PR #1042, `python-deps` group): dependabot's raised floor for
  `starlette` was `>=1.5.0,<2`, but `uv lock` against the merged `main` resolved it to
  `1.6.0` — one minor ahead of the proposed floor, still inside the same `<2` ceiling the
  proposal declared. This is the "pulls in more than the proposed floors" case above, not a
  drift-gate or coverage failure to chase.
- Observed instance (PR #1102, `python-deps` group): the proposal branch was 24 commits
  behind `main` when picked up. Merging `main` was conflict-free (dependabot's four floor
  bumps — `starlette`, `uvicorn`, `hypothesis`, `ruff` — land in disjoint `pyproject.toml`
  lines from everything `main` had touched meanwhile), and the post-merge `uv lock` pulled
  `hypothesis` one patch further forward (`6.165.7` proposed → `6.165.10` resolved) than the
  proposal's own floor, same "pulls in more than proposed" pattern as PR #1042.
- Observed instance (PR #1229, `python-deps` group): the proposal touched only
  `pyproject.toml` (six floor bumps — `idna`, `uvicorn`, `hypothesis`, `ruff`, `mypy`,
  `lizard`, `types-PyYAML`) with no companion `uv.lock` regeneration, so `uv sync --locked`
  failed immediately in CI. The branch was also one commit behind `main` (an unrelated
  frontend-only `package-lock.json`/`package.json` bump, PR #1228). Merging `main` was
  conflict-free, and `uv lock` against the merged tree resolved every bumped floor exactly
  as proposed with no further forward drift.
- Observed instance (PR #1265, `python-deps` group): a single-line `ruff` floor bump
  (`>=0.16.4,<1` → `>=0.16.5,<1`) with no companion `uv.lock` regeneration, so
  `uv sync --locked` failed in CI. The branch was also dozens of commits behind `main`
  (unrelated work landed in the interim, none touching the same `pyproject.toml` lines).
  Merging `main` was conflict-free and `uv lock` against the merged tree resolved `ruff`
  exactly to the proposed floor with no further forward drift.
- Follow-up to the PR #1229 instance above: the `lizard` 1.23.0 → 1.24.0 bump in that same
  group changed the tool's CCN counting algorithm, pushing three pre-existing, byte-unchanged
  functions (`_fetch_hotspot_keys` in `alan/teams/_pr_sonar.py`, `_incident_from_audit_row`
  in `alan/incident.py`, `_commit_scaffold_files` in `alan/repo_scaffold.py`) from CCN 6-7 to
  7-9, i.e. across the `CCN < 8` gate with no functional change of their own. Resolved by
  extracting one repeated sub-block out of each function (a helper for the list-comprehension
  filter, a helper for the pr/workspace/audit scope fallback, and a helper for the
  run-subprocess-then-raise-on-nonzero pattern) so each parent function dropped back under the
  threshold under the new counting — CLAUDE.md's "don't downgrade gates to land a PR" ruled
  out silently pinning `lizard` to the older minor via a Dependabot ignore rule instead. A
  future analyzer-version bump that regresses CCN on unrelated code should default to this
  same "shave a branch off the flagged functions" recovery over widening a gate.
- Observed instance (PR #1383, `python-deps` group): the proposal touched only
  `pyproject.toml` (three floor bumps — `hypothesis`, `ruff`, `import-linter`) with no
  companion `uv.lock` regeneration, so `uv sync --locked` failed in CI. The branch was also
  two commits behind `main`. Merging `main` was conflict-free, and `uv lock` against the
  merged tree resolved `hypothesis` and `import-linter` one minor further forward
  (`6.167.1` → `6.168.0`, `2.14` → `2.15`) than the proposal's own floors, plus a transitive
  bump of `import-linter`'s own `grimp` dependency (`3.14` → `3.17`) — same "pulls in more
  than proposed" pattern as PR #1042/#1102.
- Observed instance (PR #1482, `python-deps` group): the proposal touched only
  `pyproject.toml` (five floor bumps — `hypothesis`, `ruff`, `pyright`, `import-linter`,
  `types-pyyaml`) with no companion `uv.lock` regeneration, so `uv sync --locked` failed in
  CI. The branch was also 30 commits behind `main`. Merging `main` was conflict-free; `uv
  lock` against the merged tree found `hypothesis` and `import-linter` already at their
  proposed floors from `main`'s own prior sweeps, and only needed to bump `pyright`
  (`1.1.411` → `1.1.414`), `ruff` (`0.16.6` → `0.16.7`) and `types-pyyaml`
  (`6.0.12.20260815` → `6.0.12.20260906`) to match — same recovery shape as PR #1383.
- Observed instance (PR #1691, `python-deps` group): the proposal touched only
  `pyproject.toml` (three floor bumps — `idna`, `uvicorn`, `ruff`) with no companion
  `uv.lock` regeneration, so `uv sync --locked` failed in CI. The branch was also 37
  commits behind `main`. Merging `main` was conflict-free, and `uv lock` against the
  merged tree resolved `idna`, `uvicorn`, and `ruff` exactly as proposed with no further
  forward drift — same recovery shape as PR #1265.
- Observed instance (PR #1857, `python-deps` group): the proposal touched only
  `pyproject.toml` (four floor bumps — `starlette`, `hypothesis`, `ruff`, `hatchling`) with no
  companion `uv.lock` regeneration, so `uv sync --locked` failed in CI. The branch was also
  behind `main`. Merging `main` was conflict-free, and `uv lock` against the merged tree
  resolved `starlette` (`1.6.0` → `1.7.0`) and `ruff` (`0.16.8` → `0.16.9`) exactly as
  proposed and `hypothesis` two patch releases past its proposed floor (`6.168.0` →
  `6.168.3`); `hatchling` is a build-system requirement and does not appear in `uv.lock` —
  same recovery shape as PR #1691.
- Observed instance (PR #1967, `python-deps` group): the proposal touched only
  `pyproject.toml` (six floor bumps — `fastapi`, `packaging`, `uvicorn`, `hypothesis`, `ruff`,
  `mypy`) with no companion `uv.lock` regeneration, so `uv sync --locked` failed in CI. The
  branch was also behind `main`. Merging `main` was conflict-free, and `uv lock` resolved every
  bump as proposed (`fastapi` `0.141.1` → `0.142.2`, `packaging` `26.2` → `26.3`, `uvicorn`
  `0.53.0` → `0.54.0`, `ruff` `0.16.9` → `0.16.10`, `mypy` `2.3.1` → `2.4.0`, `hypothesis`
  `6.168.3`) and pulled in `opentelemetry-api` as a new transitive dependency of `fastapi`
  `0.142` — same recovery shape as PR #1857.

## Advisory fix log

Dated one-line entries appended by advisory-fix PRs that widen a declared dependency range, so
the CLAUDE.md drift gate sees a documentation change alongside the `pyproject.toml` edit.
Empty until the first widening.
