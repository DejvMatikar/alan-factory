# Per-repo status rollup — `alan/repo_status.py` + `GET /api/engines/{owner}/{repo}/status`

Home's repo-first layout (W1.1/W1.2) needs a titled "Repository Status" strip per repo
section that answers *is this repo okay* before *what work is in flight*. No per-repo
rollup source existed before this module — `get_repo_status` is the single read backing
that strip: six independently-typed fields (main CI, open PRs, cost, delivery time, last
deploy, health), each carrying its own `Provenance` (`status: fresh|stale|refreshing|
unknown`, `detail`), sourced under **that engine's own** forge credential rather than the
default engine's.

## Never-raises vs 404 — the one-sentence reconciliation

"Never-raises" governs **data gathering**: a missing credential, absent `gh`, timeout,
non-zero exit, unparseable JSON, or empty audit each becomes a typed `unknown` field with
provenance, never a 500. A **404 remains correct for addressing a repo that does not
exist** — `get_repo_status` returns `None` (not a raised exception) when
`_resolve_engine` can't place the identity, and `routes/engines.py` turns that `None`
into the 404 `EngineConfig` lookups already use. A **registered engine with no forge
configured is still an addressable repo**, so it returns `200` with every field
`unknown` (`_all_unknown_response`) rather than a 422 that would make the strip vanish —
vanishing is the exact failure this module exists to prevent.

`_resolve_engine` resolves the **default** repo identity via `get_registered()` or
`default_engine_config()` even when it isn't (yet) in the live registry, so a fresh
install's default strip renders before onboarding ever registers anything. Every other
unregistered repo keeps the hard 404.

## Two speeds

Only the forge-sourced half — main CI (default-branch check-run conclusions) and open
PRs (total + authored-by-alan, via `gh pr list ... --author @me`) — is cached and
background-refreshed; both live in one `ForgeStatus` unit per forge, since they share the
same two credential-gated `gh` probes. Cost, delivery time, the health finding count, and
last deploy have no forge dependency and are computed fresh on every request.

The read **always returns immediately**: `_forge_fields` serves the cached `ForgeStatus`
(or, on a cache miss, an all-`unknown` pair carrying `status="refreshing"`) and only
*schedules* a background refresh — it never blocks on one. `FORGE_STATUS_TTL_S = 600.0`
is a named module constant, not an `ALAN_*` env var: `ALAN_*` vars are state-isolation
redirects only (never user-facing configuration, see `CLAUDE.md`), and this TTL is a
rate-limit self-defence floor on an **ungated** endpoint that Home can call once per repo
section on every paint — not a UI-freshness knob a human would ever want to tune. The TTL
was raised from 120 s to 600 s during the 2026-09 GitHub-quota incident: one refresh is
four `gh` calls, so at 120 s the two registered repos refreshed ~60×/h ≈ 224 `gh`
calls/h — half the backend's whole `gh` volume — for two fields (default-branch CI,
open-PR counts) that move on a tens-of-minutes scale. At 600 s a repo refreshes at most
6×/h, and a `stale` badge after ten minutes is an acceptable price; the frontend poll
interval is untouched because every read is cache-served and spends no `gh` call. A cache
entry older than the TTL is served with its `fresh` fields downgraded to `stale`
(`_mark_stale`) while a refresh is kicked in the background; an `unknown` field (a real,
persisted failure — e.g. no credential) is left as `unknown`, not upgraded to `stale`,
since there is no last-known-good value to show as stale.

Cache lives at `_pr_state_paths._engine_state_dir(forge) / "repo_status_forge.json"` — the same per-forge
state directory `engine_preflight.json` and the `pr_*.json` writers already use — written
atomically (temp file + `os.replace`), best-effort (a write failure is logged and
swallowed; the request already has its answer).

## Single-flight refresh, capped fan-out

`begin_refresh(slug) -> bool` is the single-flight decision, expressed as a return value
so tests can drive it directly with no sleeps: `True` claims the slot (adds `slug` to the
in-flight set under a lock) and the caller then owns clearing it; `False` means a refresh
for that forge is already in flight and the caller must not start another.
`refresh_forge_status(forge)` is pure and synchronous — its first statement checks for a
transport credential with `secrets_store.transport_credential(forge)` (the shared derivation
that strips the secret, so a whitespace-only secret counts as absent), and with none it
returns the all-unknown result, `"no credential available for this repo's forge"`, before any
forge request is made. Otherwise it runs the two probes through `ForgePort` (`active_forge()`)
with no I/O side effects of its own, so its whole failure matrix (missing credential, `gh`
absent, timeout, non-zero exit, unparseable JSON, wrong-shape response) is directly
unit-testable against the fake forge without threads. `schedule_refresh` is a thin
wrapper: `begin_refresh` then, only on success, submits `_do_refresh_and_publish` (which
runs `refresh_forge_status`, writes the cache, publishes, and clears the in-flight flag
in a `finally`) to a module-level `ThreadPoolExecutor(max_workers=2)` — the fan-out cap
that keeps N repo sections rendering at once from spawning unbounded concurrent 30s `gh`
subprocesses against an ungated endpoint.

## Field sourcing

- **Main CI** — three credential-scoped reads: `gh api repos/{slug}` for the default
  branch, `gh api repos/{slug}/commits/{branch}/check-runs`, then
  `gh api repos/{slug}/actions/workflows/{ci.main_ci_workflow}/runs?head_sha={sha}&per_page=1`
  for that run's `check_suite_id`. Green/red are counted over the check-runs whose
  `check_suite.id` matches — i.e. the jobs of the engine's declared main CI workflow, and
  nothing else. `counted_over` is `"main_ci_workflow"` or `"all_checks"`.

  The workflow-runs call is keyed on the **head SHA taken from the check-runs payload**,
  never on `branch`. Querying by branch returns the newest run, whose `head_sha` can lag
  the tip by a commit; the suite id would then match zero check-runs and the field would
  report a confident `0 green · 0 red`. Keying on the SHA makes counts and commit
  inseparable. The workflow name is `urllib.parse.quote`d: `CiConfig.main_ci_workflow` is
  `Field(min_length=1)` with no pattern and reaches us from engine config, so an
  unescaped value is a path-injection vector into `gh api`.

  A four-rung fallback ladder degrades to `"all_checks"` — counting every returned
  check-run, with the provenance detail naming the reason — when (1) the engine declares
  no `ci.main_ci_workflow`, (2) the check-runs carry no head SHA, or (3) no run of that
  workflow exists for the commit or the call fails. As before, the field is never
  rendered `unknown` merely for lacking a declared scope.

  `ForgeTarget.authoritative_checks` is **not consulted here at all**. It is a merge-gate
  concept (see `merge_recovery.md`, `branch_protection_compat.md`); using it to answer a
  main-health question is what made `red_count` structurally unable to go non-zero for an
  engine whose only authoritative check is SonarCloud, while `Tests` was failing (alan#1596).

  `cancelled` counts red, via the shared `_CI_FAILURE_CONCLUSIONS`. Under suite scoping a
  cancelled entry means the latest attempt at that job on the tip did not complete
  successfully. This is deliberately **narrower than** health's `_MAIN_CI_FAILING_CONCLUSIONS`
  (which omits `cancelled` and includes `action_required`) and than
  `main_red_issue_filing.md`'s superseded-reading; reconciling the three sets is its own
  change, not this one.

  The `counted_over` `Literal` no longer admits the retired `"authoritative_checks"`, so
  a cache carrying that value fails `ForgeStatus` validation and is discarded by
  `_read_forge_cache`'s existing `ValidationError` arm — no migration code, no TTL wait.
  **This covers only engines that declared an authoritative set** (alan and any other engine that declares one). A cache
  written by the old `"all_checks"` arm — an engine with no `authoritative_checks` — still
  validates, so for up to one `FORGE_STATUS_TTL_S` (600s) after deploy such an engine is
  served pre-change counts that the new scope would have narrowed. It self-heals at the
  next refresh. A cache-version field or a persisted scope identity would close that
  window deterministically; it is not carried here because the whole field is a
  600s-TTL cached dashboard value whose staleness contract already covers it, and because
  a "refresh whenever `all_checks` is cached" shortcut would force a `gh` round-trip on
  every read for any engine legitimately sitting on the fallback rung.

### One definition of a red main

Three subsystems answer "is main red", and they now agree on the same input:

- `alan/teams/triage_pregate.py`'s `_latest_main_run` reads `ci.main_ci_workflow` via
  `gh run list --workflow ... --branch main` (`health.md`).
- the `main-red-report` job files its issue off `tests.yml`'s test-suite jobs, explicitly
  excluding the sonar and coverage-comment jobs as "quality-gate/reporting jobs, not the
  test suite itself" (`main_red_issue_filing.md`).
- this field now scopes to the same declared workflow's check-suite, per job.

On the workflow-scoped path — `counted_over == "main_ci_workflow"` — `SonarCloud Code
Analysis`, the `deploy` check-run from `ci-deploy.yml`, and the nightly advisory
workflows (`boundary-smoke.yml`, `e2e-stress.yml` — both labelled "advisory, files
issues" and both of which do fail their job on failure) are deliberately **out of scope**
of this chip. Widening that path to include them is a change to what "main is red" means,
not a bug fix: it would let a flaky nightly stress run paint the strip red while `health`,
six inches away in the same UI, stays green.

**The `"all_checks"` fallback is the stated exception, not a contradiction of the above.**
When the ladder degrades, every default-branch check-run *is* counted, advisory and
`deploy` included, and the chip can go red for a reason `health` does not share. That is
the deliberate trade: a rung that could not resolve the declared workflow reports what it
can actually see and says so in the provenance detail, rather than silently reporting a
narrower green. Read `counted_over` before treating a red chip as a red `Tests` run.
- **Open PRs** — two reads through the forge port (`active_forge()`, see `forge_port.md`):
  `open_prs` (`gh pr list --repo {slug} --state open`) for the total, and `prs_by_author`
  with `"@me"`, `("number",)` and `state="open"` for the authored-by-alan count. Each
  returns a `RowsRead`, so a failed listing stays distinct from an empty one and either
  failure makes the whole field `unknown`; the adapter, not this module, parses the JSON
  array and runs the subprocess.
- **Cost** — no forge call. `CostField.total_cost_usd` is the sum of `WorkspaceRow.
  total_cost_usd` (all six `workspace_cost` categories — team runs, status summary,
  discussion chat, collision check, investigator, preparation chat) across **every**
  workspace attributable to this repository, live and archived, whatever its current
  status: a workspace is in scope when its `project_id` is one of this repo's projects
  (`project.list_projects()` filtered by `engine_config.same_repo_scope(p.engine_key,
  slug)` — canonicalising, never a raw `==` on the stored strings), or, for a
  `project_id is None` row, when its own `engine_key` canonicalises to the same repo
  via `delivery_stats.in_repo_scope`, the row-level predicate's one home (see "The
  null-engine ruling" below). This deliberately
  "chat spend is excluded from burn" caption was already stale by the time it read
  `WorkspaceRow.total_cost_usd`, which has summed all six categories since the workspace
  cost feature landed; carrying that claim into `CostField` would have restated the same
  defect one level up.  A scoped row with `total_cost_usd is None` contributes nothing to
  the sum but increments `unknown_count`, so `unknown_count > 0` is the caller's signal
  that the total is a **floor**, not a total — never silently folded to zero like the old
  `_unassigned_burn`/`_project_burns` code did (`row.total_cost_usd or 0.0`). A `None`
  `total_cost_usd` on the field itself means zero rows were in scope (an unregistered or
  brand-new repo), not zero dollars — the two are kept visually distinct the same way a
  per-workspace `None` cost is distinct from a real `$0.00` figure. Budget enforcement
  did not move: spend limits still halt automated work via `project_budget.
  resolve_burn_for_project` / `budget_state` / `budget_exhausted`, and the operator is
  still warned through the existing notification path (`routes/inbox.py`'s
  `_budget_warning_fyi_item`) — only this display's budget-vs-burn figure was replaced
  with the corrected plain total; nothing about enforcement's data path changed.
- **Delivery time** — no forge call. `DeliveryTimeField.by_size` is always exactly five
  `SizeStat` entries, one per `alan.workspace_size.SizeClass` (`XS`/`S`/`M`/`L`/`XL`) in
  that fixed order, each carrying `median_active_seconds` (`None` when no delivered
  workspace of that size exists yet — an explicit empty state, never a fabricated `0`)
  and `delivered_count` (how many delivered workspaces the median rests on, so a
  one-sample figure is never mistaken for an established one). Sourced via
  `alan.delivery_stats.delivered_rows_for_repo` (the same canonicalising repo-scoping
  rule as Cost above — both folds call `delivery_stats.in_repo_scope`, so a row can
  never appear in one figure and be silently excluded from the other) then
  `delivery_time_by_size` (median, not mean — a single outlier long-running
  workspace must not drag every future estimate toward it), both pure functions over
  already-loaded `WorkspaceRow` lists. "Delivered" means: `status` is `COMPLETED` or
  `ARCHIVED` (`workspace_index.DELIVERED_STATUSES`), `resolved_scope_ref()` starts with
  `"pr:"` (matched to a real merged PR, not just any terminal workspace), and
  `active_closed_seconds is not None` (the workspace actually recorded fleet time).
  `Meta.delivered_at` is deliberately **not** part of the predicate — `mark_delivered`
  is a comparatively new write site and `delivered_at` is `None` on the entire historical
  population, so gating on it would render every bucket empty on day one. Archived rows
  are included on purpose: this repo's automatic archiving of old completed work must
  never erode the sample the statistic is computed over.

**The null-engine ruling.** (a) A `None` (or unregistered) engine reference is not "no
repository" — it folds to the default engine, via `engine_config._resolve_scope_config`'s
`get_registered() or default_engine_config()` fallback, the same fallback
`canonical_scope_key` has always used. (b) Every repo-scoping fold — Cost's and Delivery
time's `project_id`/`engine_key` membership above, and Health's finding filter below —
canonicalises via `engine_config.same_repo_scope` and never compares raw engine-key
strings; the row-level predicate (a `project_id is None` row's own `engine_key`) has
exactly one home, `delivery_stats.in_repo_scope`, called from both `_cost_field` and
`_delivery_time_field` so neither figure can silently diverge from the other by forking
its own copy of the comparison. Both of those callers loop over every workspace row
attributable to the repo, so each precomputes `engine_config.canonical_scope_key(slug)`
once and passes it as `in_repo_scope`'s optional `target`, instead of re-resolving the
same static target (registry lock + slug parse) on every row — the per-row half of the
comparison, `canonical_scope_key(row.engine_key)`, is still the one true primitive, just
invoked with the static side already folded. (c) These two axes are deliberately different, not a
contradiction: an unbound project's *spend* is always **attributed** to the default
engine (its `engine_key` is `None`, which folds to default under (a)), while an unbound
project carrying a recorded `intended_engine_key` is **placed** in the UI under that
intended repository — a project can visually sit under "Sprocket" while its cost still
rolls into the default repo's Cost strip, by design. (d) `intended_engine_key` is
provenance, never authority: it is never read by any repo-scoping fold in this module,
never gates a bind, and an unknown or malformed value degrades to the provenance-less
population rather than rejecting anything — see `alan/routes/projects.py` for the field
itself and `PatchProjectBody.engine_key` for the one adjacent field that *is* gated
authority, by contrast. Every other client-supplied field across `CreateProjectBody` and
`PatchProjectBody` — `title` and `doc_ref` (both bodies), `autonomy` and `budget_usd`
(patch only) — shares that same provenance-not-authority property: each is accepted and
stored verbatim, and none of them gates a bind or feeds `_check_engine_rebind_gates`;
`PatchProjectBody.engine_key` is the one deliberate exception, because it is the field
that actually performs the bind.

- **Last deploy** — **default engine only**; every other engine renders `unknown`
  *without reading the audit file at all*, since deploys are alan-global by construction
  and `deploy_verify_audit.jsonl` (tailed via `alan.teams._pr_audit_log.audit_dir()`, i.e.
  under `ALAN_LOGS_ROOT`) is engine-blind. No per-workspace "latest deploy outcome" query
  exists or is invented here.
- **Health** — the engine-filtered finding count via `engine_config.same_repo_scope`,
  the same canonicalising predicate `routes/infra.py`'s `GET /api/health?engine=`
  achieves through an equivalent direct `canonical_scope_key(...) == canonical_scope_key(...)`
  comparison — not a third fork of the raw-string comparison this ruling retires. The
  global `verdict` is populated only when `is_default` — never attributed to a
  non-default engine's strip. A missing or stale `HealthState` renders `unknown` rather
  than a misleading `finding_count=0`.

## SSE

`_do_refresh_and_publish` publishes `TeamEvent("repo_status_refreshed", {"slug": ...})`
on the global pubsub; `routes/events.py`'s `_ENGINE_KINDS` maps it to the existing
`"engines"` SSE kind alongside `engine_created`/`preflight_completed`/etc. (see
`events.md`) — no new SSE kind, Home's existing engines-change listener picks it up.

## Layering

`repo_status` sits between `auto_continue` and `delivery_stats` in `[tool.importlinter]`'s
layers list — **not** alongside `engine_preflight` — because it imports
`alan.delivery_stats` directly, and `delivery_stats` itself imports `alan.workspace_index`
and `alan.workspace_size`, both layered well below `engine_preflight`; placing
`repo_status` there would make that import an upward edge. `repo_status` no longer
imports `alan.project_budget` at all (that import went away with `SpendField` — see
"Cost" above), so nothing about this module's position depends on `project_budget`'s
layer anymore. It reaches the forge only through `alan.forge` (the port's `active_forge()`); it imports
no subprocess runner, and `alan.teams._pr_branch` is not among its imports. From its
current position it can also reach `alan.teams._pr_audit_log` (`audit_dir`),
`alan.teams._pr_state_paths` (`_engine_state_dir`), `alan.teams.deploy_verify`
(`_AUDIT_FILE`), `alan.health`, `alan.workspace_index`, `alan.workspace_size`,
`alan.project`, `alan.queue`, `alan.events`, `alan.engine_config`, and
`alan.secrets_store` — all layered further down. `routes/engines.py` (layer `routes`, at
the top) imports `repo_status` the same direction every other route imports its backing
module.

`delivery_stats` sits immediately below `repo_status`, immediately above
`project_budget`, and imports only `alan.workspace_index` and `alan.workspace_size` — it
has no forge, project, or budget dependency, and exposes two presentation-free functions
(`delivered_rows_for_repo`, `delivery_time_by_size`) operating on plain `WorkspaceRow`
lists and returning plain tuples/dicts, never a pydantic model — `repo_status` is the
only caller and is the one that wraps the result in `DeliveryTimeField`/`SizeStat`. A new
field on `RepoStatus` that needs a repo-scoped fold over `WorkspaceRow`s should be
threaded through the single `_scoped_rows(identity, workspace_index)` call already made
in `get_repo_status` (one `project.list_projects()` call, one
`workspace_index.validated_rows()` call) rather than adding a second index read — this is
what keeps `cost` and `delivery_time` from doubling the per-request folds that removing
`SpendField` was partly meant to avoid.

## Recurring-process status — `alan/recurring_status.py` + `alan/recurring_forge.py` + `GET /api/engines/{owner}/{repo}/recurring-status`

A sibling read, not a field on `RepoStatus`: the six existing fields answer "is this repo
okay right now"; recurring-process status answers "is the cadence of unattended work on
this repo still firing" — scheduled CI workflows, Dependabot's per-ecosystem update runs,
alan's own per-repo dependency sweep, the two service-wide sweeps (failure triage, daily
digest), and the state backup. `recurring_status.get_recurring_status(identity, *, queue,
workspace_index, pubsub) -> RecurringStatus | None` mirrors `get_repo_status`'s contract
exactly: `None` only when `_resolve_engine` can't place the identity (→ 404 at the route),
never a raised exception for a data-gathering failure. `routes/engines_recurring_status.py`
is its own sibling router (`engines_recurring_status_router`, `prefix="/api/engines"`,
included in `server.py` and listed in the independence contract) rather than a new handler
on `routes/engines.py` — that module was already at the H4.1 800-line cap, and CLAUDE.md's
size ratchet requires new content to land in a new sibling module, never a raised cap.

**Layering.** Both new modules sit *above* `repo_status` in `[tool.importlinter]`'s layers
— `recurring_status` immediately below `auto_continue`, `recurring_forge` immediately
below that, `repo_status` immediately below `recurring_forge` — specifically so
`recurring_status` can import `repo_status.Provenance`/`unknown_provenance`/`_resolve_engine`/
`_scoped_rows` downward (the four-state envelope and the engine-resolution/repo-scoping
primitives are reused, not reforked) and so `recurring_forge` can reuse
`repo_status.REFRESH_EXECUTOR`, `begin_refresh`, `end_refresh`, `now_iso`,
`unknown_provenance` downward — public names, imported plainly, with no identity re-export
alias. The forge reads themselves no longer travel down that path: each module reaches the
forge through `ForgePort` (`active_forge()`), and `repo_status`'s two `gh` helpers no longer
exist. This ordering also resolves what would otherwise be a cycle: assembly (which
must call the forge fetcher) has to sit above the fetcher, while the fetcher needs the
`Provenance` envelope repo_status already defines — `recurring_forge` therefore returns its
own plain fetch-result models (`WorkflowSchedule`, `DependencyEcosystemUpdate`,
`RecurringForgeStatus`), and `recurring_status`'s assembly layer is the sole place that
transforms them into the public `RecurringProcess` shape; `recurring_forge` never imports
`recurring_status`.

**The `duration_state`/`overdue.state` tri-state design.** Every `RecurringProcess` carries
`duration_state: measured|not_applicable|unknown` (flat field — Dependabot processes are
always `not_applicable`, never a fabricated duration) and a nested `overdue: Overdue` whose
own `state: on_time|overdue|unknown` is a second, independent tri-state — deliberately two
different shapes so a bare `None` is never asked to mean both "not measured" and "not yet
computed" at once. `overdue` is never trusted from a cache: `_finalize_overdue` recomputes
it from `(cadence, last_run.at)` against `now` on every read, in `get_recurring_status`
itself, after any cached forge data is merged in — a `RecurringForgeStatus` blob can sit in
its 900s cache for a while, but "is this now overdue" must reflect wall-clock time at
request time, not fetch time.

**Cadence kinds.** `Cadence.kind` is `interval_seconds` (a numeric interval, from a parsed
cron, an engine's `dependency_sweep.cadence_seconds`, `SWEEP_CADENCE_SECONDS`, Dependabot's
`schedule.interval`, or the backup job's cadence), `cron` (a workflow whose cron
entries didn't parse — the raw expression is kept in `cron_expression` for display even
though `interval_seconds` is `None`), `manual` (a workflow whose only trigger is
`workflow_dispatch` — no schedule to compare against, `compute_overdue` always returns
`unknown`), or `rides_on` (the daily digest, nested as `triage_sweep`'s sole `sub_steps`
entry rather than a sibling top-level process with an invented cadence — `_finalize_overdue`
skips the generic interval math for a `rides_on` node and leaves the value the digest
computed for itself via `alan.incident.digest_overdue`/`alan.health.resolve_digest_deadline`
untouched, since a daily wall-clock deadline is a different rule than "N seconds since last
run"). `cron_interval_seconds` is a small in-house parser (three independently-CCN-low
helpers: a `*/N` minute step, a `*/N` hour step with a fixed minute, and a fully-fixed
`minute hour` with either a wildcard or fixed day-of-week for daily/weekly) — it recognises
exactly the four shapes alan's own crons and GitHub Actions schedules actually use and
returns `None` for anything else; there is no `croniter` dependency.

**Scheduled CI discovery.** `recurring_forge` reads each repo's *own* workflow files on its
default branch every refresh (`gh api repos/{slug}/actions/workflows`, then one
`contents` fetch per workflow to parse `on:` — note PyYAML's 1.1 resolver turns a bare
`on:` key into the boolean `True`, not the string `"on"`; `_on_section` checks both), so a
schedule added or removed shows up within one TTL window with no separate registration
step. A workflow is in scope only if it declares a `schedule:` trigger (→ `scheduled_ci`,
cadence `cron`) or is triggerable *only* by `workflow_dispatch` with no `schedule` (→
`manual`); a normal push/pull_request-triggered workflow is out of scope here — that's
what `main_ci` above already covers. Each in-scope workflow's most recent run comes from
one more `gh api .../runs?per_page=1` call, bounding the fan-out to `1 + 2N` calls (list,
then content + latest-run per workflow) rather than anything unbounded.

**Forge-contract canary caveat.** `forge-contract.yml`'s `forge-contract` job intentionally
concludes `success` — mapped to `RunState.succeeded` by `_forge_run_state` below like any
other green run — even when its scheduled run found real contract drift and filed a
`forge-contract-drift` issue: the finding is the product, not the job's exit code. Only an
`unverifiable` outcome (the canary itself failing to run — missing tooling, a timeout, a
rate limit — never a claim about the contract) turns the job red. A reader of this
`scheduled_ci` entry must not take a green `forge-contract` run as "no drift was found";
the filed issue, not the run conclusion, carries that answer. See
`scripts/alan_forge_contract_check.py`'s module docstring and
`.github/workflows/forge-contract.yml` for the full three-outcome design.

**Dependabot.** `dependency_update` entries are read from `.github/dependabot.yml` (or
`.yaml`) on the default branch, one `RecurringProcess` per declared `package-ecosystem`,
`duration_state` hardcoded `not_applicable` unconditionally — Dependabot exposes no
queryable "last run" API, so `last_run.at` is approximated from the single most recent
`app/dependabot`-authored PR across the whole repo (one `gh pr list` call, shared by every
ecosystem entry) with the approximation stated plainly in `last_run.provenance.detail`,
never presented as ecosystem-precise. A 404 reading `dependabot.yml` is distinguished from
any other read failure (`ForgePort.repo_file`'s `absent` outcome versus its `failed` and
`raised` ones; `_fetch_file_text_or_404` maps only `absent` to a confirmed absence)
specifically so "this repo genuinely has no Dependabot config" — the common case —
renders as a correct empty list with `fresh` provenance, not a scary `unknown`.

**System sweeps and backup.** The per-repo dependency sweep (`alan.teams.dependency_sweep`)
and the two service-wide sweeps are all family `system_sweep`; only the triage sweep,
daily digest, and backup job gate on `is_default` — `get_recurring_status` appends them to
`processes` only for the repo whose identity resolves to alan's own default engine, and
sets `service_scope_note` (a plain sentence, not a duplicated entry) on every other repo's
response instead. `duration_state`/`last_outcome`/`last_run.at` for the triage sweep and
the per-repo dependency sweep are tailed from `triage_sweep_status.json` and
`dep_sweep_status.json` respectively — both now written unconditionally in a `finally`
block by their teams (see `git log` on `alan/teams/triage.py` /
`alan/teams/dependency_sweep.py`), so "never fired" and "fired and failed" are
distinguishable the same way the backup job now is.

**The `RunState` design.** Every `RecurringProcess.last_run` carries a `state: RunState`
(`succeeded`, `failed`, `attention`, `cancelled`, `in_progress`, `queued`, `never_ran`, or
`unknown`) rather than the old `succeeded: bool | None` tri-state — eight readings instead
of three, so the UI can distinguish a genuine failure from the several non-failure endings
a bare boolean used to collapse into "failed". Two independent, pure classifiers populate
it — recurring_forge never classifies anything itself; assembly remains the sole transform
layer. `_forge_run_state(status, conclusion, confirmed_absent=...)` reads GitHub Actions' own
two-field run vocabulary plus one extra bit only recurring_forge can supply: `status is None`
→ `never_ran` only when `confirmed_absent` is true — i.e. `_fetch_latest_run` got a
well-formed `workflow_runs: []` response, an affirmative "zero runs exist" — and → `unknown`
otherwise, since a bare fetch failure or malformed response (`WorkflowSchedule`'s
`last_run_confirmed_absent`, `False` by default and on any legacy cache payload) must not be
reported as the stronger claim "this workflow has never run";
`in_progress` → `in_progress`; `queued`/`waiting`/`requested`/`pending` → `queued`;
`completed` fans out over all nine GitHub Actions conclusion values via `_CONCLUSION_STATE`
— `success` → `succeeded`; `failure`/`timed_out`/`startup_failure` → `failed`, the
alarming state, reserved for genuine failures; `action_required` → `attention`, a warning
weight distinct from both success and failure; `neutral`/`cancelled`/`skipped`/`stale` →
`cancelled`, a deliberately neutral non-alarm reading for a run that ended without
completing normally; any other status → `unknown`. `_sweep_run_state(outcome)` covers
alan's own sweeps' three-valued `success`/`degraded`/`failure` string via `_SWEEP_STATE`
(`success` → `succeeded`, `degraded` → `attention`, `failure` → `failed`) — this is the
stated boundary: a sweep is a synchronous in-process call that only ever reports a
completed outcome, so it has no `in_progress`, `queued`, or `cancelled` reading and can
only ever reach `succeeded`/`failed`/`attention`/`unknown`. The five placeholder branches
where no status/receipt has ever been persisted (`_digest_process`'s no-digest-yet,
`_triage_process`'s no-status-yet, both `_dependency_sweep_process` branches — disabled
and enabled-but-no-status-yet — and `_backup_job_process`'s no-receipt-yet) all inherit
`LastRun.state`'s own `"unknown"` default rather than asserting `never_ran`: unlike
GitHub's affirmative "this workflow has zero runs in its history" answer, a missing status
file on alan's own side cannot distinguish "genuinely never run" from "ran but hasn't
persisted its first status yet", so `unknown` is the honest reading — `never_ran` is
reserved for the one source that can actually back the claim. `LastRun.url` carries an
already-fetched external address (a GitHub Actions run's `html_url`, or a Dependabot PR's
`url`) straight through from `recurring_forge`'s raw fetch as an additive `None`-defaulted
field on `WorkflowSchedule` (`last_run_status`/`last_run_conclusion`/`last_run_url`) and on
`DependencyEcosystemUpdate` (`last_pr_url`); the frontend owns every in-app destination
(React Router paths for alan's own sweeps and the dependency-sweep's engine-config page) in
`alan/web/src/lib/recurringLinks.ts`, keeping the SPA's route table out of the
backend. `LastRun.outcome` is unchanged and keeps carrying the raw display string (GitHub's
conclusion, or alan's own outcome string) for captions that want the precise word rather
than the coarser `state` bucket. Overdue computation is unaffected by any of this:
`_finalize_overdue` still recomputes purely from `(cadence, last_run.at)` against
wall-clock `now`, so an `in_progress` or `queued` run that started on schedule is already
not overdue by virtue of its own start timestamp — no special-casing needed for the new
states.

**Backup coverage.** `BackupCoverage` is a `RecurringStatus` field on *every* repo's
response (unlike the backup-job process itself, it is not `is_default`-gated — every repo
needs to know whether its own work is protected) and is read from the same
`backup_receipt.json` the backup-job process reads (the backup job writes
it from a `finally` block, skipped on `--dry-run`). `included_in_last_snapshot` is `True`
only when the most recent run's `outcome == "success"`; `changed_since_count` folds this
repo's `_scoped_rows` (the same primitive `_cost_field`/`_delivery_time_field` above use)
against the receipt's `finished_at`, counting workspaces `updated_at` after that snapshot —
an honest "how much could be lost", not a guess. `excluded_caveat` is a fixed sentence
naming per-repo engine configuration as structurally excluded from every snapshot; this
module only surfaces that gap, it does not change what the backup includes
(closing the gap is separate, already-scoped work on the state-backup tooling).

**Caching.** `recurring_forge` reuses `repo_status.REFRESH_EXECUTOR` (no second thread
pool) with its own `recurring:{slug}` in-flight key — sharing the module-level
`_INFLIGHT_SLUGS` set with `repo_status`'s own `{slug}`-keyed entries by construction,
since the prefix makes collision impossible — and its own 900s-TTL cache file
(`recurring_status_forge.json`, `RECURRING_FORGE_TTL_S`) in the same per-forge state
directory `repo_status_forge.json` already uses. Unlike `repo_status`'s `_forge_fields`,
this cache does not distinguish `stale` from `fresh` on repeated reads — a cache miss
schedules a refresh and reports every forge-derived process placeholder-`unknown` with
`status="refreshing"`; a hit, fresh or past-TTL, is served as-is (past-TTL still schedules
a background refresh). No new `ALAN_*` env var was added for the TTL, matching
`FORGE_STATUS_TTL_S`'s own rationale above.
