# Engine preflight — `alan/engine_preflight.py` + `POST /api/engines/{owner}/{repo}/preflight`

Before a human raises an engine's posture or dispatches its first real workspace,
`run_preflight(cfg)` runs six static, read-only checks against the engine's OWN
configuration and credentials and returns a `PreflightResult` — so a misconfiguration
(wrong token, tools the engine's own allowlist can't reach, an unreachable repo, an
invalid flow graph, incompatible branch protection) surfaces as a readable report instead
of failing three phases into a real dispatch. Dry-run warmup — actually spawning a
scratch clone and running gate/warmup commands — is explicitly OUT of scope here
(deferred; natural fast-follow home is R3.3's engine-editor "test run" button, since it's
the only piece that executes host argv and needs the scratch-clone lifecycle + timeout +
posture≥`propose` safety gate that a purely static preflight doesn't).

A seventh row, `dispatch_readiness`, is appended after `overall` is computed and never moves it; see "Dispatch readiness" below.

## The six checks

Each `_check_*` helper returns exactly one `Finding` (`{check, status: pass|fail|unknown,
message, remedy}`), except check 6 which can contribute between 1 and 5 (see
`branch_protection_compat.md`):

1. `author_token_valid` — the engine's transport credential resolves via
   `secrets_store.transport_env`, the same resolution path `alan/teams/_pr_push.py`'s
   push machinery uses.
2. `reviewer_distinct_from_author` — resolves the author's GitHub login (from the
   transport credential, via `gh api user`) and the reviewer's login (from
   `secrets_store.verifier_gh_env`, reached through the port's verifier mode) and asserts they differ. See "Two-credential
   model" below for how the reviewer credential resolves and why the default engine
   legitimately reports `fail` today.
3. `repo_clonable` — `git ls-remote <repo> HEAD` under the SAME `transport_env` identity
   pools use for real clones (R1.1). Deliberately NOT a real clone — no scratch-clone
   lifecycle, cleanup, or timeout, which is exactly the warmup machinery this workspace
   deferred.
4. `gate_warmup_tools_reachable` — every `cfg.gate_commands`/`cfg.warmup_commands` argv is
   non-empty and reachable from `render_allowed_tools(cfg, "implementation")` — the
   ENGINE's OWN tool allowlist (`base_tools` + `team_tools["implementation"]`), never the
   process-wide `BASE_ALLOWED_TOOLS` default a narrower engine has overridden away.
   Reachability is Bash-glob prefix matching: an allowlist entry `Bash(uv run:*)` matches
   any argv starting with `["uv", "run"]` and rejects `["uv", "publish"]`.
5. `flow_graph_and_posture_valid` — reuses `engine_config._validate_before_register` (the
   exact function `create_engine`/`update_engine` already call), so preflight can never
   disagree with what registration itself would accept.
6. branch-protection compatibility — delegates to
   `alan.branch_protection_compat.compare_branch_protection`.

## Two-credential model

Author and reviewer are two independent secrets-store records per engine, both mirroring
the same store-first/env-fallback shape:

- Author/transport: `secrets_store.transport_env` resolves
  `resolve_secret(repo_slug, secrets_store.TRANSPORT_PURPOSE,
  env_fallback=credential_env_name)` and raises `MissingSecretError` when neither
  resolves — the git-transport identity pools use for real clones/pushes.
- Reviewer: `secrets_store.verifier_gh_env` (moved there from `alan.teams._pr_branch` in X2.7 PR22) resolves
  `resolve_secret(repo_slug, secrets_store.REVIEWER_PURPOSE,
  env_fallback=credential_env_name)` and returns `{}` when neither resolves. This is a
  deliberate asymmetry from `transport_env`: a missing reviewer credential must not
  raise, since review-posting call sites (`dismiss_stale_verifier_reviews` and its
  siblings) already treat `{}` as "skip", and preflight's `_reviewer_login` treats the
  resulting `None` as an `unknown` finding rather than a crash.

Both purposes fall back to the SAME `ForgeConfig.credential_env_name` for their env
fallback — writing a distinct `reviewer` store record is what actually separates the two
identities; leaving it unwritten means both legs resolve to the same env var and
legitimately resolve to the same login.

Example `POST /api/secrets` payload writing a sprocket engine's reviewer credential
(naming trap: the JSON field named `engine` holds the repo **slug**, not an engine key):

```json
{"engine": "acme-org/sprocket", "purpose": "reviewer", "value": "ghp_..."}
```

The default alan engine has no `reviewer` store record, so both legs fall back to the
same `ALAN_VERIFIER_GH_TOKEN` env var — author and reviewer resolve to the same login,
and `reviewer_distinct_from_author` correctly reports `fail`. This is expected, not a
defect: findings here are advisory-only exhaust (see "Overall, persistence, and SSE"
below) and this one does not gate, block, or alter live review posting.

**Known limitation:** `dismiss_stale_verifier_reviews`'s changes-requested parsing
(`_parse_changes_requested_ids` / `_extract_verifier_review_id`, at the time of writing
in `_pr_branch.py` — the review-cluster split moved all three to `alan/teams/_pr_post.py`,
so that path no longer holds) matches reviews by `verifier_login()`, which stays keyed on the
single default `ALAN_VERIFIER_LOGIN` env var (an agreed scope cut — no per-engine
override). Until a second engine's reviewer bot gets its own login wired there, a
non-default engine's reviewer-bot `CHANGES_REQUESTED` reviews will not be matched or
dismissed by that path.

## Never-raises contract

`run_preflight` must never 500 — a preflight that crashes on the exact input it exists to
validate defeats its purpose. Each `_check_*` helper catches its own
`subprocess.TimeoutExpired` / `OSError` / `secrets_store.MissingSecretError` locally and
downgrades to a `status="fail"` or `"unknown"` `Finding`; nothing escapes to
`run_preflight` itself. `compare_branch_protection` upholds the same contract
independently (see `branch_protection_compat.md`). `_persist` treats the write itself as
best-effort: it swallows `OSError` (a full state-dir, a permissions problem, a failed
`os.replace`) and `run_preflight` still returns the computed `PreflightResult` to the
caller — a disk problem must not turn a successful preflight computation into a 500.

## Overall, persistence, and SSE

`overall` is the worst status across the six checks' findings (`fail` ≻ `unknown` ≻ `pass`,
`_worst`); the `dispatch_readiness` row is appended after it is computed. The `PreflightResult`
(`slug`, `checked_at` ISO-8601 UTC, `overall`, `findings`, `reviewer_fingerprint`) is persisted as `engine_preflight.json` under the SAME per-forge state
directory (`alan.teams._pr_state_paths._engine_state_dir(forge)`) the sibling `pr_*.json`
writers already use, atomically (temp file + `os.replace`, matching those writers) — no
new lock, since each engine's preflight write is independent of every other engine's.
`load_last_preflight(forge)` reads it back, returning `None` when absent or malformed.

`POST /api/engines/{owner}/{repo}/preflight` (control-token guarded, `routes/engines.py`,
following the `set_engine_posture` template) looks the engine up and raises 404 on
`cfg is None` (the `update_engine` idiom, not `set_engine_posture`'s
`EngineNotFoundError` catch — `lookup_by_repo` returns `EngineConfig | None`, not an
exception), runs `run_preflight` in a threadpool, and publishes
`TeamEvent("preflight_completed", {"slug": ...})` on the global pubsub.
`routes/events.py`'s `_ENGINE_KINDS` maps `preflight_completed` to the `"engines"` SSE
kind alongside `engine_created`/`engine_updated`/`engine_deleted`/
`engine_posture_changed`/`pool_lifecycle_changed` (see `events.md`). The persisted result
is read-only exhaust for now; R3.4 renders it on the project page as pass/fail rows
without re-parsing English — findings are structured, not prose.

## Dependency-sweep enable gate (`check_dependency_sweep_enable`)

A seventh, separately-invoked check reuses the same `Finding` shape but is not part of
`run_preflight`'s six-check bundle: `check_dependency_sweep_enable(cfg) -> Finding | None`
gates `POST /api/engines/{owner}/{repo}/dependency-sweep` (`routes/engines.py`) when the
caller asks to turn the sweep **on** (see `engine_config.md` "Per-engine dependency sweep" and
`dep_sweep.md`). It runs two checks in order and returns the first non-`pass` `Finding` (or
`None` if both pass, meaning the toggle may proceed):

1. `dependabot_config_present` — `gh api repos/{slug}/contents/.github/dependabot.yml`
   against the engine's own forge target (`forge_target_for_workspace(cfg.repo.slug, cfg)`)
   and transport credential. `fail` names the missing file and states how to add it; a
   `gh`/network fault downgrades to `unknown` rather than blocking the toggle indefinitely.
2. `dependency_sweep_roster_present` — when `cfg.transition_graph` is non-empty, the union of
   its keys and destination lists (the same "roster" `dispatch._assert_on_roster` enforces at
   dispatch time) must contain `pr_merge` and `implementation_fix` — the two teams the sweep
   actually dispatches proposals to. `dependency_sweep` itself is deliberately excluded from
   this requirement: it is `internal_only` in `_DEFAULT_FLOW_POLICY` and its recurring
   workspace is bootstrapped directly via `queue.enqueue`, never through roster dispatch, so
   requiring it as a graph node would refuse re-enabling the sweep on alan's own convention-
   following config. An empty `transition_graph` passes trivially (nothing to enforce yet).
   This front-loads, at enable-time, the same roster gap that would otherwise only surface as
   a 422 the first time the sweep tried to dispatch a fix/merge job.

Enabling never persists when either check fails — `routes/engines.py` raises 422 with the
`Finding` as the response body before calling `update_and_persist`. Disabling the sweep never
runs these checks (a low-stakes single toggle, never gated).

## Dispatch readiness (`evaluate_readiness`)

A copy of alan whose install profile declares `requirements` runs nothing until they hold: no new workspace, no human dispatch, no autonomous or sweep job. The requirements come from the install's default profile (`alan/default_profile.py`, `engines/default_profile.yaml`) and are the closed enum `ProfileRequirement` — `reviewer_credential` and `code_quality`. An unknown name is a `DefaultProfileError` when the profile is resolved, which happens when `alan.engine_config` is first imported, so a misspelt requirement stops start-up instead of silently requiring nothing. Requirements are install-wide: every engine is evaluated against the same profile, including a repository connected after install. Sonar here gates readiness to dispatch, never a merge.

The logic lives in this module because it is the one existing layer entry importable by `routes`, `app`, `_dispatcher_loop` and `dispatch` — the layer list is unchanged.

### Evaluation

`evaluate_readiness(cfg, *, preflight=None) -> tuple[ReadinessIssue, ...]`; an empty tuple means ready. It never raises and never reads GitHub: no `active_forge` call and no `git` spawn — only the local secrets store, the latch file and the saved preflight. Order:

1. `Source.unconfigured` → one `not_ready` issue with requirement `default_engine`. An unconfigured install is always not ready.
2. An empty `requirements` list → `()` before any read (the owner short-circuit below).
3. Each distinct requirement's checker, in profile order. An exception inside one checker becomes an `unknown` issue for that requirement (the message carries the exception class name only) and the others still run.
4. An exception outside the checkers (for example resolving the profile) becomes one `unknown` issue for `default_engine`.

`ReadinessIssue` is `{engine, requirement, state, message, fix}` with `state` one of `not_ready` (holds), `unknown` (allows the dispatch and is logged as a warning), or `warning` (allows the dispatch, non-blocking, never a hold). A satisfied requirement produces no issue.

`reviewer_credential`, evaluated per engine against `forge_target_for_workspace(None, cfg).repo.slug`, in this order:

- **Exists** — `secrets_store.reviewer_record_state`, a local read that comes first and separates `absent` (→ `not_ready`) from `unreadable` (an `OSError` other than not-found, undecodable or non-object JSON → `unknown`). `_load_record` conflates the two, which is why this is a separate probe. The environment fallback (`credential_env_name`) and the machine's signed-in GitHub account never count: that fallback is the author credential, so counting it would make the reviewer "exist" by construction. A missing or unreadable credential reports alone — there is nothing to be distinct from or to warn about yet.
- **Not expired** — `ReviewerRecordDetail.expired_on(today_utc)`; on or after the recorded `expires_at` → `not_ready`, reported alone in place of distinctness and the expiry warning below.
- **Distinct from the author** — the ledger below.
- **Nearing expiry** — `ReviewerRecordDetail.expiry_warning_on(today_utc, REVIEWER_EXPIRY_WARNING_DAYS)`; true from `REVIEWER_EXPIRY_WARNING_DAYS` (14) days before the recorded `expires_at` through the day before it (UTC calendar date) produces a non-blocking `warning` naming the ISO expiry date, reported alongside the distinctness issue (if any) rather than instead of it — `blocking_issues` still counts only `not_ready`, so this warning never holds a dispatch or refuses a workspace creation. `expiry_warning_on` and `expired_on` are evaluated against the same boundary and are never both true: the window ends the calendar day before `expires_at`, and `expired_on` takes over exactly on `expires_at` itself. No recorded expiry, or one still more than 14 days out, produces no warning.

`code_quality`, install-wide — the latch below; a required `code_quality` whose profile has no SonarCloud project is `not_ready` (the profile still loads). `code_quality` is no longer a default requirement: the profile `alan/default_profile_writer.py` writes lists only `reviewer_credential`, and a profile that lists `code_quality` is checked exactly as before.

Whenever an engine's set of `(requirement, state)` pairs changes, exactly one log line is written (WARNING when any issue is present or changes — including a non-blocking `warning`, INFO when the engine becomes ready). Identical repeated evaluations, including every health poll, log nothing.

### Why enforcement sits at creation and at job pickup

Two seams cover every path. **Creation** — `workspace_creation.resolve_engine_key` — runs in the route's threadpool before any workspace exists and outside `lane_limit._creation_lock`, so a refusal leaves nothing to roll back and the lane lock is never held across a file read. **Human dispatch** — `dispatch.assert_team_dispatchable`, shared by manual dispatch and answering a question — refuses the same way after the task-known and archived checks, ahead of the posture and roster checks so an unconfigured install is told to run setup rather than that its `observe` posture admits nothing. An answer is validated before it is recorded, so a refused question stays pending.

The human-path check alone does not cover the rest: triage and dependency sweeps, the unblocker, merge-gate remediation, auto-continue and the router's autonomous hand-offs put jobs on the queue — some with `Queue.enqueue` directly, others through `dispatch_team_core` — without ever passing through `assert_team_dispatchable`. So the second seam sits at **job pickup**, in `LoopMixin._execute` between `_read_workspace_and_pool` and `set_active_pool`: every job, whoever enqueued it, is checked when a worker picks it up and before any slot, worker record or session is taken. `team_is_dispatchable` — which the unblocker (`unblock_sweep`) and merge-gate remediation use to decide whether a team is dispatchable — deliberately does not consult readiness; their dispatches are held at pickup like any other job.

Workspaces the service seeds for itself — the recurring triage and dependency-sweep parents, a dependency PR's workspace and an auto-continue successor — are created without a creation-seam check, on purpose. Each is created together with a job, and pickup holds that job, so nothing runs. Refusing the creation instead would drop work that a held job resumes by itself once the requirements hold: the triage and dependency-sweep parents are seeded only at start-up, so a refused seed would not come back until the next restart, and a refused auto-continue would leave the roadmap item unattempted with nothing to retry it.

### How a hold works

At pickup a non-empty `blocking_issues(cfg)` (only `not_ready` counts) calls `Queue.hold_until_ready(job_id)`, the `JobTransition.HOLD_UNTIL_READY` row of `lifecycle.JOB_TABLE`: `RUNNING → PENDING`, `started_at` NULL, `eligible_at` SET to now + `READINESS_HOLD_DELAY_S` (60 s), every other column — `error`, `resume_inv_id`, `interruption_count`, `requeue_count`, `server_error_retry_count` — KEEP. It is a new row rather than a reuse because `REQUEUE_PENDING`, `REQUEUE_SERVER_ERROR` and the failed-job requeues each bump a counter that means something else (interruptions, server-error retries, human re-queues), and `REQUEUE_PENDING` also clears `eligible_at` and `resume_inv_id`. The hold writes no job error and publishes no event, and a `QueueError` (the job stopped being `RUNNING`) is logged and swallowed because `_execute`'s catch-all would otherwise fail the job. The hold reason is not stored on the job: it is in the per-engine readiness report and in the one change-of-state log line. `_CLAIM_SQL` skips a job whose `eligible_at` is in the future, so the job resumes by itself at the first pickup after the requirements hold, with its counters and resume pointer intact. See [queue_and_dispatcher.md](queue_and_dispatcher.md).

### Where the distinctness answer comes from, and how it goes stale

Checking that reviewer and author are different GitHub accounts needs `gh api user` for each credential — a GitHub read, which the creation, dispatch and health paths must never make. The answer is therefore read from the engine's last saved preflight (`load_last_preflight`), specifically its `reviewer_distinct_from_author` finding: `fail` → `not_ready`, `pass` → satisfied, `unknown` or no such finding → `unknown`. No saved result also means `unknown`. `run_preflight` supplies its own fresh result to the evaluation it runs for the `dispatch_readiness` row, so that row reflects the run that just happened rather than the file on disk.

The answer is a snapshot and goes stale when the reviewer credential changes after the preflight ran. `run_preflight` records `PreflightResult.reviewer_fingerprint` — the stored reviewer record's `fingerprint` (a 12-hex SHA-256 prefix, never the value) — and readiness compares it with the current record's fingerprint. A mismatch, a saved result taken with no stored reviewer, or a stored record with no fingerprint is `unknown`, not the old verdict: a stale `fail` stops holding once the credential is replaced, and a stale `pass` stops vouching for the new one, until the preflight runs again. Rewrites that do not change the value (the expiry-warning stamp) keep the fingerprint and so do not stale it. Results saved before the field existed load with `reviewer_fingerprint = None` and read as stale.

### The code-quality latch

`alan/engine_preflight.py` ships the latch's format, its writer and its check; setup's entry point for writing it belongs to the setup item.

- **Location**: `roots.engines_root() / "code_quality.latch"` — under the existing isolated `ALAN_ENGINES_ROOT`, so no new isolation root or env var. Engine records there are `*.json`; the `.latch` name can never match that glob, so it cannot be mistaken for an engine record.
- **Format**: JSON `{"version": 1, "organization": ..., "project_key": ..., "recorded_at": <ISO-8601 UTC>}`, mode `0600`, written atomically (`store.write_text_mkstemp`). `CodeQualityLatch` forbids extra keys and any `version` other than 1, and `recorded_at` must parse as an ISO-8601 timestamp with a zero UTC offset (`+00:00` or `Z`); a naive or non-UTC timestamp is malformed.
- **Writer contract**: call `write_code_quality_latch(organization, project_key)` exactly when SonarCloud has reported for that project — the latch records the organization and project key it was set for, not merely that setup ran. Calling it again replaces the record.
- **Check**: the latch counts only while both values still equal the profile's current `code_quality` organization and project key (exact comparison). A different organization or project, or no latch at all ("Sonar never reported"), is `not_ready`. A missing file is not an error; an unreadable or malformed file is `unknown`.

### Fix texts

`readiness_fix(requirement, cfg)` is the only place fix texts live. Install-level requirements and the default engine — every `code_quality` issue, `default_engine`, and the default engine's `reviewer_credential` — are fixed by `./setup.sh`. A connected repository's reviewer is fixed by `store a reviewer credential for <owner>/<repo>`. Refusals (`assert_engine_ready`, HTTP 409) read `engine <slug> is not ready to dispatch: <requirement>: <message> (fix: <fix>)`, naming the engine, the failing requirement and the fix; manual dispatch and answer use the identical text.

### The health report and the preflight row

`GET /api/health` carries `dispatch_readiness: list[ReadinessIssue]` on every request. It is `readiness_report()` computed with `run_in_threadpool` — never on the event loop — over the default engine and every registered engine (one entry per slug), independent of the triage sweep and of `health_state` freshness, and unaffected by the `engine`/`project_id` query filters. It is empty when every engine is ready and is never cached. The field has a default in the response model, so the generated schema marks it optional. The preflight's `dispatch_readiness` finding maps each issue's state to a verdict (`not_ready`→`fail`, `unknown`→`unknown`, `warning`→`pass`) and reduces the worst across all issues with the same `fail` ≻ `unknown` ≻ `pass` precedence `_worst` uses for the six checks: `pass` when the engine is ready or every remaining issue is a non-blocking `warning`, `fail` when anything is `not_ready`, otherwise `unknown` — with the distinct fixes as its remedy. A warning-only result therefore reads as `pass` with the warning's message and fix still populated in the row, never introducing a new `Finding.status` value.

### The owner's short-circuit, and why production is unchanged

`config/owner/default_profile.yaml` declares no `requirements`, so on the owner's install step 2 returns `()` before any secret, latch, preflight or GitHub read: `resolved()` is cached in memory and the engine registry is in memory. The creation and dispatch seams therefore add one in-memory evaluation, a pickup never holds, and the health report is `[]`. `tests/owner/test_readiness_owner.py` proves it by making the secret, latch, preflight and GitHub readers fail and showing that the owner's creation, dispatch, health and job pickup still succeed with none of them touched; P1.1's owner characterization tests pass unchanged. The same holds for any install with no requirements.

Enforcing the triage-sweep switch is out of scope here; the reviewer credential's 14-day expiry warning is covered above (`REVIEWER_EXPIRY_WARNING_DAYS`, `ReviewerRecordDetail.expiry_warning_on`).

## Layering

Placed directly above `teams` in `[tool.importlinter]` layers (imports
`alan.forge` for `active_forge()`, whose `viewer_login(as_verifier=True)` is how the reviewer identity is
reached — the adapter derives the reviewer credential itself through `secrets_store.verifier_gh_env`, so
this module imports no `alan.teams._pr_branch` —
`alan.teams._pr_state_paths` for `_engine_state_dir`, and
`alan.teams._base.render_allowed_tools`), and therefore also above
`branch_protection_compat`, `secrets_store`, `engine_config`, and
`engine_config_validation` (all further down the layer list). The route handler itself
lives in `alan/routes/engines.py`, in the `routes` layer, per usual.
