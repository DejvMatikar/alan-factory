# Deploy Verify

## Purpose

`deploy_verify` is a post-merge gate that confirms a merged PR was actually deployed to production before the workspace is marked complete. It is **live**: it runs after every successful `pr_merge` and before completion, adding successful production deployment as an additional precondition on top of the existing acceptance-criteria gate.

Not to be confused with `alan/teams/verification.py` (`VerificationTeam`, the `verification` team): that is the pre-merge, in-PR-review external-gate check (CI status + judge review) that runs *before* `pr_merge`. `deploy_verify` is the post-merge, post-deploy confirmation that runs *after* `pr_merge` has already merged the PR — a different subsystem despite the name overlap.

## Module: `alan/deploy_verify.py`

Pure verdict module with no alan imports — a leaf below `teams` in the import-linter layer stack.

### Public API

```python
@dataclass(frozen=True)
class DeployRun:
    run_id: str
    created_at: datetime
    status: str
    conclusion: str | None

DeployArm = Literal["restart_required", "restart_free"]

@dataclass(frozen=True)
class DeployObservation:
    arm: DeployArm
    checkout_contains_merge: bool
    slot_contains_merge: bool
    served_contains_merge: bool
    health_ok: bool

def is_live(observation: DeployObservation) -> bool: ...
```

`DeployRun` survives from the pre-local-observation design purely as the shape `alan/deploy_runs.py` returns for the *diagnostic* fetch on an escalation (see below) — it plays no role in the live-or-not verdict any more.

### `is_live()` logic

`is_live` is a pure boolean function of a `DeployObservation` — no I/O, no alan imports, no run conclusions:

- **`restart_required` arm**: confirms only when `served_contains_merge` (the live process's `served_sha()` contains the merge as an ancestor) *and* `health_ok` (the `/api/health` probe). `checkout_contains_merge`/`slot_contains_merge` are not consulted.
- **`restart_free` arm**: confirms when `checkout_contains_merge` *and* `slot_contains_merge` both hold — the deployed checkout's `HEAD` and the active frontend slot's `.deploy_sha` both contain the merge as an ancestor. `served_contains_merge`/`health_ok` are not consulted, since a restart-free merge never bounces the process that would need to be healthy.

Both arms use ancestor containment (`git merge-base --is-ancestor`, via `alan/deploy_observe.py`), not exact-sha equality, so one deploy that sweeps up several merges at once still confirms each of them.

## Module: `alan/deploy_observe.py`

The I/O leaf that supplies every input `DeployObservation` needs, and nothing else — no decision logic lives here. It sits in the `pyproject.toml` layers list between `deploy_runs` and `deploy_verify`: below `deploy_gate_sweep` (which calls it) and `deploy_runs` (which sits adjacent to it in the layers list but does not import or call it — `deploy_runs` only imports `DeployRun` from `deploy_verify`) and above `deploy_verify` (from which it imports the pure `DeployArm`/`DeployObservation` types).

```python
def checkout_head(repo: Path) -> str | None: ...          # uncached `git rev-parse HEAD`
def active_slot_sha(web_dir: Path) -> str | None: ...      # reads <active dist slot>/.deploy_sha
def live_served_sha() -> str | None: ...                   # alan.served_sha.served_sha(), cached at process start
def probe_health(url: str) -> bool: ...                    # GET url, expects {"ok": true}
def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool: ...
def classify_arm(repo: Path, merge_sha: str) -> DeployArm: ...
def observe(merge_sha: str, *, health_url: str | None, repo=..., web_dir=...) -> DeployObservation: ...
```

`classify_arm` is the answer to "which arm does this specific merge need" — and it is answered from **the merge commit's own changed paths alone** (`git diff-tree --no-commit-id --name-only -r <merge_sha>`, classified path-by-path through the existing `alan.deploy_restart.is_safe_path`), never from `alan.deploy_restart.decide` against the `restarted_sha` marker. That marker-based classifier answers a different question — "has the service already restarted past this point" — used only by the merge-time restart-deferral gate (`alan/teams/_deploy_restart_probe.py`, unchanged by this work, see `docs/architecture/deploy.md`). Confusing the two would let a merge whose *own* diff is safe, but which lands after a run of hot-path merges since the last restart, be misclassified against the wrong window. A merge with an unresolvable diff (git failure, empty diff-tree) classifies conservatively as `restart_required` — fail-closed, matching the rest of this subsystem's posture.

`observe()` is the one-call assembler both `alan/teams/deploy_verify.py` (passing the real health URL) and `alan/deploy_gate_sweep.py` (passing `health_url=None`, which short-circuits `probe_health` and forces `health_ok=True` unconditionally, per the "no gh subprocess, no network health probe" contract on the periodic sweep) call. Neither caller talks to git or urllib directly.

## Team: `alan/teams/deploy_verify.py`

Pure-code (no LLM) team modelled on `pr_merge`. Registered as `"deploy_verify"` in the `TEAMS` registry.

### Attributes

| attribute | value |
|---|---|
| `name` | `"deploy_verify"` |
| `supports_resume` | `False` |
| `restart_rederivable` | `True` |
| model | `"none"` |

### Evaluate-once, park-and-promote

`deploy_verify` used to poll in a blocking loop, occupying a dispatcher worker and a wall-clock budget for the entire wait. That poll loop is gone. `DeployVerifyTeam.run()` now calls `_evaluate_once(workspace, pr_number, gate_instructions)` exactly once per invocation:

```
if not _pipeline_available_for(workspace):        # forge target != default engine's repo
    CONFIRMED  → terminal handover, no delivered_at/live_at stamped
                 (audit: deploy_verify_skipped_no_pipeline)

info = read_pr(<pr>, state,mergedAt,mergeCommit)  # _pr_merge_info, one ForgePort read — orthogonal
                                                 # to the ci-deploy pipeline: confirms the PR
                                                 # itself actually merged, unaffected by this work
                                                 # rate_limited/transient raise here for
                                                 # dispatcher-level retry — see below
if info.mergedAt is None:
    if info.state in (CLOSED, OPEN) and info.gh_ok:
        ESCALATE immediately — no merge, or still open; race budget not consumed
    else:                                        # state MERGED, or gh call failed
                                                   # (auth/unknown/domain_absent/empty payload)
        mint-or-reuse race_deadline from ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S;
        past it → ESCALATE (race_budget_exhausted); else → WAIT
else:
    observation = deploy_observe.observe(merge_sha, health_url=ALAN_HEALTH_URL)  # local only
    elapsed     = now(UTC) - info.mergedAt
    deadline    = mint-or-reuse from ALAN_DEPLOY_VERIFY_BUDGET_S
    if is_live(observation):
        CONFIRMED  → terminal handover (workspace → done), stamps delivered_at + live_at
    elif now >= deadline:
        ESCALATE (variant "never_live") → escalation handover (workspace → escalated)
    else:
        WAITING    → terminal handover carrying `deploy_wait` (workspace → done, then re-parked)
```

The engine-scope check runs first and unconditionally: an engine whose forge target differs from the default engine's own repo (a second engine today) has no `ci-deploy` pipeline at all, and the merge/mergedAt/observation machinery below assumes the default repo's checkout — running it against a foreign PR number would misread an unrelated result. `merge_sha` for the observation step comes from the `GateRecord` the merge-time restart-deferral gate already resolved (`alan/teams/_deploy_restart_probe.py`'s `classify_merge`, unchanged); when it is unresolvable (`missing_marker`, `merge_sha_unavailable`, …) `_merged_step` cannot observe anything and simply waits out the budget, then escalates `never_live` with no `arm=` note.

A `WAITING` verdict no longer sleeps and re-loops. It produces an ordinary `handover_type="terminal"` handover (so it routes through the same `done` path as `CONFIRMED`) with an additional `deploy_wait: DeployWaitSignal` field (`alan/handover.py`) naming the `merge_sha`, the absolute-UTC `deadline` (and `race_deadline` when the wait is specifically for `mergedAt` to resolve), and the human-readable `reason`. `alan/deploy_wait_gate.py::apply` — wired into `Dispatcher._apply_router_decision` (`alan/dispatcher.py`) immediately *after* `_enforce_loop_guard` and before the decision is handled — intercepts exactly this shape (`decision.kind == "done"`, `handover.from_team == "deploy_verify"`, `handover.deploy_wait is not None`) and rewrites it back into `kind="enqueue", team="deploy_verify"` with `eligible_at` set to the deadline and a `GateRecord(path="awaiting_restart", ...)` (when `race_deadline` is set) or `GateRecord(path="awaiting_live", ...)` (once `mergedAt` is known — this path was named `awaiting_deploy_run` before this change; `GateRecord.from_json` still accepts that literal as a parse-time alias so a job parked across the rename survives) carrying the same `deadline`/`race_deadline` forward as `instructions`. Because this rewrite happens *after* `_enforce_loop_guard`, the loop guard never sees the wait cycle as an `enqueue`-kind decision — a legitimately-waiting job can re-park indefinitely without ever risking `REVISIT_CAP` escalation or spiral detection (see [queue_and_dispatcher.md](queue_and_dispatcher.md)).

The net effect: while a deploy is genuinely in flight, `deploy_verify`'s job row sits `PENDING` with a future `eligible_at` — no worker occupied, no wall-clock budget ticking down in-process. The event this is actually waiting for is the service restart itself (or, for the `awaiting_live` stage, the merge simply becoming observable locally). `deploy_gate_sweep.promote_covered_jobs` (unchanged mechanism for `awaiting_restart`, re-based-on-local-observation for `awaiting_live` — see "Process-start promotion" below) runs once at every process start and pulls `eligible_at` forward to "now" for any covered job — so the restart (or the local observation becoming live) is what wakes the job, not the passage of time. If the event never happens, the `deadline`/`race_deadline` minted on first evaluation is the backstop: it is a plain field on the `GateRecord`/`Job` row, so it survives a process that never comes back, and the next boot's `promote_covered_jobs` sweep (or an operator re-dispatch) re-evaluates against it.

Because `elapsed` is still re-derived from `mergedAt` on every evaluation, and `deadline`/`race_deadline` are minted once and carried forward on the `GateRecord` rather than re-derived from a relative countdown, a deploy that takes 85 minutes resolves exactly the same way a deploy that takes 5 minutes does — the only difference is how many times `_evaluate_once` runs before it does.

### Skipping engines with no deploy pipeline

`_pipeline_available_for(workspace)` compares the workspace's own forge target (`alan.teams._pr_forge.forge_target_for_ws`) against `alan.engine_config.default_engine_config()`'s hardcoded repo — deliberately not the *registered* default (`get_registered()`), since the check must hold even before any engine has registered. Today only the default `alan` engine deploys anything; **an unconfigured second engine has no deploy pipeline of any kind** and this check recognizes that up front, before any `gh` call, and completes the workspace immediately with `reason` naming `deploy_verify_skipped_no_pipeline` (also the audit `action`) rather than stamping `delivered_at`/`live_at` — those two fields mean "independently confirmed live," which such an engine's workspaces cannot claim. This is a **correctness fix**, not new behavior: before this change, a second engine's merge's `deploy_verify` run still fired the same `gh pr view`/`gh run list` calls the default engine uses, from the alan checkout's own `cwd`, silently reading the wrong repository's PR/run data. What is *not* fixed by this check: the merge-time restart-deferral gate (`alan/deploy_gate_route.py`, dispatcher-level, unaffected by this work) still defers such a PR's `deploy_verify` enqueue by the same ceiling window as an alan PR, since it has no workspace context to consult. That deferral is harmless — once the job is eventually claimed, `_evaluate_once`'s engine-scope check fires immediately and skips, rather than misbehaving.

### Deploy workflow per engine

The deploy workflow the diagnostic lists is no longer a module constant in `alan/deploy_runs.py` (`_WORKFLOW_NAME = "ci-deploy.yml"` is gone); it is the deploy workflow of the workspace's own engine, resolved by `alan.teams._pr_forge.deploy_workflow_for_ws(workspace)`, which sits beside `governing_config_for_ws`. It takes the first of these whose `ci` is not `None` and returns that config's `ci.deploy_workflow`:

1. the workspace's pinned `engine_snapshot.config`,
2. the registry record for the workspace's `engine_key` (`engine_config.lookup_by_slug`),
3. `engine_config.default_engine_config()`.

A `ci: null` config falls through to the next source rather than resolving to "no deploy workflow", so a record that carries no CI section (a second engine with no CI configured) and an older snapshot with no config both stay on the default engine's `ci-deploy.yml`. Only a config that *has* a `ci` section and leaves `deploy_workflow` null means "this engine has no deploy workflow": the resolver returns `None`, `fetch_deploy_runs(forge_target, deploy_workflow)` returns `[]` before `active_forge()` is touched, and `_diagnostic_detail` reports `"no deploy workflow configured"` — the forge is never contacted. The resolver deliberately skips `get_registered()` (unlike `governing_config_for_ws`), so a workspace that names no engine reads the default engine's workflow, exactly as it did when the name was hard-coded. The owner's lookups therefore issue the identical `gh run list --workflow ci-deploy.yml` call as before.

### Merge/forge-race variants (`mergedAt` unavailable)

The PR read can return no `mergedAt` for a PR that in fact just merged — GitHub's read replica hasn't caught up, or `_pr_merge_info` classified the failure as `auth`, `unknown`, `domain_absent`, or an empty decoded payload (the JSON decode happens at the `ForgePort` adapter, which folds an unparseable or non-object payload into empty fields). A `rate_limited` or `transient` failure no longer reaches this branch at all: `_pr_merge_info` routes every non-zero exit and subprocess exception through `_forge_errors.raise_for_infra`, exactly as `pr_merge._gh_pr_checks`/`_gh_pr_state` already do, so those two classes raise `ForgeRateLimited`/`ForgeTransientError` straight out of `_evaluate_once` for dispatcher-level retry instead of degrading to `gh_ok=False` — an unreachable forge now yields no verdict rather than a false negative. `_merge_race_step` distinguishes what's left:

- **`state == "CLOSED"`**: closed without merging — escalates immediately as `closed`, no race budget consumed.
- **`state == "OPEN"`**: still open — escalates immediately as `open`, no race budget consumed.
- **`state == "MERGED"`, or the `gh` call itself failed non-transiently (`gh_ok=False`)**: a genuine merge/forge race. Mints (or reuses, from a re-parked `GateRecord.race_deadline`) an absolute deadline `ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S` out; each evaluation before that deadline returns `wait`, parking the job rather than sleeping in-process. Past the deadline, escalates as `race_budget_exhausted`. The escalation text names the observed `state` (`gh call failed` when the `gh` call failed with an `auth`/`unknown`/`domain_absent`/unparseable result; `state missing from gh response` when `gh` succeeded but the payload omitted `state`).

### No `pr_number` → escalate

When dispatched without a `pr_number` in the prior handover, the team emits node events (workflow node visible in task graph) and returns an escalation handover immediately.

### The single escalation variant: `never_live`

The prior four-way classification (`frozen`/`transient_hiccup`/`deploy_in_progress`/`service_wedge`) is gone, along with the run-conclusion inference it was built on. Budget exhaustion without a live observation produces exactly one escalation: `_never_live_outcome` audits `action="deploy_escalate", variant="never_live"` (the audit action vocabulary is otherwise frozen — `deploy_confirmed` / `deploy_escalate` / `escalate_no_merged_at` — so `alan/repo_status.py`'s `_DEPLOY_CONFIRMED_ACTIONS`/`_DEPLOY_ESCALATE_ACTIONS` needed no edit) and builds a reason naming the observation `arm` (when one was resolvable) plus a **diagnostic-only** detail line from `_diagnostic_detail(workspace)`. That helper resolves the workspace's deploy workflow (`_pr_forge.deploy_workflow_for_ws`, see "Deploy workflow per engine" above), calls `alan.deploy_runs.fetch_deploy_runs(_pr_forge.forge_target_for_ws(workspace), workflow)` — the same real `ForgeTarget` this module's other port calls already carry, not a repository slug reconstructed by hand — and summarizes the single latest run's `status`/`conclusion` — but the fetch is soft-failing by design: any exception (network, auth, unparseable output) degrades to `"deploy-run diagnostics unavailable"` rather than raising, and an empty run list degrades to `"no <name> runs observed"`. The `<name>` in that wording (and in the latest-run text, `"latest <name> run status=… conclusion=…"`) is `Path(workflow).stem`, so the owner's deploy workflow `ci-deploy.yml` still reads `ci-deploy` and an engine with a differently named deploy workflow (`ship-it.yml`) reads its own name (`no ship-it runs observed`); an engine with no deploy workflow reads `"no deploy workflow configured"` and never reaches the fetch. Nothing about this fetch's outcome changes the verdict — it is prose attached *after* the escalation decision is already made, never consulted before it. This is the direct consequence of the decoupling this document describes: a `ci-deploy` run's own conclusion (or its total absence, the old "frozen" signal) is no longer evidence about whether the merge is live, so it can no longer be more than a footnote on the one ruling that matters.

`escalate_no_merged_at` (with its own `variant` sub-field: `closed`/`open`/`race_budget_exhausted`) is unaffected by any of this — it belongs to the orthogonal "did the PR itself actually merge" question, described above, and never touches `ci-deploy` at all.

The now-unreachable frozen-deploy state's companion recovery mechanism — the triage-hosted red→main→green auto-redispatch (`TriageTeam._maybe_redispatch_frozen_deploy_verify` and everything it called) — is deleted outright from `alan/teams/triage.py`, along with the `FROZEN_DEPLOY_MARKER` constant it keyed off in `alan/teams/deploy_verify.py`. See `docs/architecture/triage.md`.

### Environment variables

| variable | default | purpose |
|---|---|---|
| `ALAN_HEALTH_URL` | `http://127.0.0.1:8585/api/health` | independent health probe target |
| `ALAN_DEPLOY_VERIFY_BUDGET_S` | `5400` (90 min) | **how long before we conclude the restart is never coming** — not a deploy-duration estimate. Minted once as an absolute deadline (`time.time() + budget`) on the first evaluation after `mergedAt` resolves, then carried forward on the `GateRecord`/`Job` row across every re-park; never re-derived from a relative countdown. Because nothing runs while a parked job waits out this deadline, setting it generously costs nothing — the value is sized to comfortably exceed the observed p99 merge-to-confirmation tail (a historical 5090s / ~85 min case), not to bound CI queue depth. |
| `ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S` | `300` (5 min), in-code default only | how long to wait when `mergedAt` is momentarily unavailable (merge/forge race) before escalating; deliberately not threaded into the plist, CI workflow env, or `serverEnv` |
| `ALAN_DEPLOY_VERIFY_AUDIT_DISABLE` | unset | set to `1` in tests to suppress audit writes |

### Routing (live)

```
TEAM_TRANSITION_POLICY["deploy_verify"] = frozenset()   # no forward routing
```

Terminal handovers route to `done`; escalation handovers route to `escalated` — both handled by the router's `handover_type` early-exit branches before `_resolve_forward` is reached.

The gate itself is wired at the dispatcher, not inside `pr_merge`: `pr_merge` still emits an ordinary `handover_type="terminal"` handover on success (unchanged). `Dispatcher._enforce_deploy_gate` (`alan/dispatcher.py`) intercepts the router's resulting `kind="done"` decision whenever `handover.from_team == "pr_merge"` and `handover.pr_number` is set, and rewrites it to `kind="enqueue", team="deploy_verify"` before the decision is applied — so `deploy_verify` runs on every successful merge with no per-PR opt-in. `pr_merge` having `"deploy_verify"` in its `TEAM_TRANSITION_POLICY` allowed set documents this edge for the layered-transition validator; the actual forwarding decision is made by the gate, not by a `to_team_suggestions` entry on the handover.

#### Restart gate (H8.3 / H8.5 — signal-driven start)

`_enforce_deploy_gate` no longer builds the `RouterDecision` by hand — it calls `alan.deploy_gate_route.deploy_gate_decision(pr_number)`, which in turn calls `alan.teams.deploy_verify.restart_gate_terms(pr_number)` for the `(reason, eligible_at, instructions)` triple the decision is built from. `alan/teams/deploy_verify.py` cannot import `alan.router` (`teams` sits below `router` in the `pyproject.toml` layer list), so `alan/deploy_gate_route.py` — which sits above both — is the one that assembles the `RouterDecision`; `restart_gate_terms` only supplies the terms. `router.py` itself stays I/O-free: it only grew an `instructions: str | None = None` field.

The blind 20-minute wait this gate used to impose on every merge is gone. Verification now begins as soon as one of three paths fires, evaluated in this precedence order:

```python
_RESTART_WINDOW_S: float = 600.0        # fallback ceiling only; rollback lever

def restart_gate_terms(pr_number: int) -> tuple[str, float | None, str | None]:
    window = 0.0 if dry_run_enabled() else restart_window_s()
    if window <= 0:
        return f"post-merge deploy gate for PR #{pr_number}", None, None
    return _restart_free_terms(pr_number, window)

def _restart_free_terms(pr_number: int, window: float) -> tuple[str, float | None, str]:
    restart_free, reason, merge_sha = _deploy_restart_probe.classify_merge(pr_number)
    if restart_free:
        return (..., None, GateRecord(merge_sha, "restart_free", None).to_json())
    if merge_sha is not None and deploy_signal.merge_is_served(merge_sha):
        return (..., None, GateRecord(merge_sha, "signal", None).to_json())
    deferred_at = time.time()
    return (..., deferred_at + window, GateRecord(merge_sha, "ceiling", deferred_at).to_json())
```

1. **`restart_free`** (H8.4, unchanged) — `_deploy_restart_probe.classify_merge` reports the merge could not have restarted the service. Evaluated **first**, unconditionally: this exemption must never degrade into waiting for a signal that will never arrive, and ordering it before the signal check makes that true by construction rather than by convention.
2. **`signal`** (new, H8.5) — `alan.deploy_signal.merge_is_served(merge_sha)` reports the live process is already running code that contains the merge (`git merge-base --is-ancestor <merge_sha> <served_sha()>` — an ancestor check, not an exact match, so one deploy sweeping up several merges at once still covers each of them). Only reached when `classify_merge` did *not* call the merge restart-free (a restart-required merge can still turn out to already be served, e.g. a later merge's deploy already picked it up) and a merge SHA was actually resolved.
3. **`ceiling`** (fallback, was the *only* path before this change) — nothing observable said the merge is safe or served, so the job defers by `_RESTART_WINDOW_S` from the moment of this decision. Within `restart_gate_terms` this is the only path that sets `deferred_at`; `alan/deploy_wait_gate.py` separately stamps a fresh `deferred_at` whenever it re-parks a `wait` outcome onto the `awaiting_restart` path (see the `GateRecord` payload section below).

These are the same two evaluation points as before (inside the gate at merge-completion time, described here) plus the process-start sweep below — the signal can only change when the live process is replaced, so a periodic poller would add nothing but a window where the job sits `RUNNING` and can overlap the very restart it exists to survive.

Whichever path fires, `eligible_at` (when non-`None`) makes `enqueue_with_pending_workflow` insert the `deploy_verify` job as `PENDING` with a future `eligible_at` — `Queue.claim()` (`alan/queue.py`) excludes any row with `eligible_at > now` from its `SELECT`, so the job simply cannot be claimed, and therefore cannot be running, until the window has passed. A restart during the window tears down nothing of `deploy_verify`'s because nothing of it is running yet; the row survives the bounce in the queue db unchanged, the same way `pr_merge`'s remediation-retry delay (`_maybe_retry_pr_merge`) already relies on `eligible_at` to defer a re-enqueue. `restart_free` and `signal` both set `eligible_at=None`, so the job is claimable immediately — starting verification before the deploy pipeline itself has finished is safe and expected, because `elapsed` inside `_evaluate_once` stays anchored to `merged_at` (see "Delivery confirmation" below), not to when the team started running.

**The `GateRecord` payload.** Whichever path fires, it also produces a `GateRecord(merge_sha, path, deferred_at, deadline, race_deadline)` (`alan/deploy_verify.py` — the same pure leaf module `DeployArm`/`DeployObservation`/`is_live` already live in) whose `to_json()` rides as the `instructions` string on the enqueued `Job` — `Job.instructions` already round-trips through `Queue.enqueue` and was previously discarded by `DeployVerifyTeam.run` (`del instructions`), so this adds no new persistent store, no `ALAN_*` isolation surface, and no cleanup lifecycle. `path` is one of `restart_free` / `signal` / `ceiling` / `awaiting_restart` — the fourth, `awaiting_restart`, is what `alan/deploy_wait_gate.py` stamps onto the re-parked job on every `wait` cycle, and is the observable that answers "is this workspace deliberately waiting for an event, or has verification simply not started yet": a `PENDING` `deploy_verify` job with no `GateRecord` at all (or a `ceiling`/`restart_free`/`signal` record) has not evaluated yet; one with an `awaiting_restart` record has evaluated at least once and is parked on a live `deadline`/`race_deadline`. The JSON carries a `"kind": "deploy_gate_v1"` discriminator; `GateRecord.from_json` returns `None` unless that key matches, so an `instructions` string that doesn't happen to look like a gate record degrades to the `None` case below rather than being misparsed. The discriminator only guards against *accidental* collisions, not a producer that deliberately emits matching JSON shape — it is not an authentication mechanism. `waited_s(record, *, now)` returns `0.0` when `record is None` or `deferred_at is None`, and `max(0.0, now - deferred_at)` otherwise (clamped against clock skew). `deferred_at` is set on the `ceiling` path and is carried forward unchanged when `deploy_gate_sweep` promotes a job to `path="signal"` (see below), so `waited_s` can be `>0` for a `signal`-path record after promotion — it reflects time actually spent waiting, not the path name alone. `alan/deploy_wait_gate.py::_reenqueue_decision` also sets `deferred_at`, but freshly (`time.time()` at re-park, not carried forward from the previous record) every time it re-parks a `wait` outcome onto `path="awaiting_restart"` — so `waited_s` on an `awaiting_restart` record measures time since the *most recent* re-park, not cumulative time since the original deploy-gate decision. `deadline`/`race_deadline` are independent optional fields, deliberately not collapsed with `min()`: `deadline` is the "restart never came" backstop minted inside `_merged_step` once `mergedAt` is known, `race_deadline` is the separate, shorter "`mergedAt` never resolved" backstop minted inside `_merge_race_step`. Keeping them apart is what lets the two no-event escalations (`frozen`/`service_wedge` from an expired `deadline`, versus `race_budget_exhausted` from an expired `race_deadline`) stay distinct classifications even though both are, mechanically, "a deadline expired with no event."

**Attribution: the `verify_start` audit line.** `DeployVerifyTeam.run` now threads its `instructions` argument into `_evaluate_once`, which — after the dry-run short-circuit, before doing anything else — writes exactly one `verify_start` audit entry (`deploy_verify_audit.jsonl`): `{action: "verify_start", pr_number, path, waited_s}`. `path` and `waited_s` are derived from `GateRecord.from_json(gate_instructions)`; a `None` or unparseable record (dispatched with no gate instructions, or on a code path that predates this change) degrades to `path="ceiling", waited_s=0.0` rather than raising. This is the observable record answering "which path caused verification to begin, and how long the wait actually was" — the same data H8.3's `restart_gate_decision` audit line already recorded for the restart-free-or-not question, extended to the full four-way vocabulary, and is what makes the 600s ceiling value tunable from real data later without re-deriving it from raw timestamps. Because `_evaluate_once` runs once per invocation and every re-park re-enters the team fresh, a genuinely long wait produces one `verify_start` line per evaluation rather than one line for the whole wait — the count of `verify_start` lines for a given PR is itself a coarse measure of how many times the job was evaluated before it resolved.

**Process-start promotion (`alan/deploy_gate_sweep.py`).** A merge on the `ceiling` or `awaiting_restart` path with a known `merge_sha` might still become served before its deadline elapses (a later, faster-deploying merge's restart happens to sweep it up too). `deploy_gate_sweep.promote_covered_jobs(queue)` runs once, in `alan/routes/infra.py`'s `lifespan`, right after `worker_reaper.reap_and_reserve` and before pool reset/warm — i.e. exactly at process start, the only moment the signal can have changed since the gate (or the previous evaluation) last checked it, and exactly the moment a restart-driven wait is actually waiting for. It scans `PENDING` jobs, keeps only `deploy_verify` jobs whose `GateRecord.path` is `ceiling` or `awaiting_restart` and names a `merge_sha`, and for each whose `merge_sha` is now served, calls `Queue.advance_eligible_at(job.id, time.time(), instructions=<same record with path="signal">.to_json())` — the record's `deadline`/`race_deadline` fields ride through this `replace()` unchanged, so a promoted job that somehow still doesn't confirm on the next evaluation still has its original backstop intact rather than a reset one. `advance_eligible_at` is pull-forward-only and `PENDING`-scoped in SQL (`eligible_at IS NOT NULL AND eligible_at > ?`, `status = ?`), so it is structurally impossible to push a job's `eligible_at` later, and a job a concurrent claim already moved to `RUNNING` simply falls out of the `WHERE` clause and wins by construction — no explicit lock needed. The whole sweep is wrapped in `except Exception` (`logger.warning` with the job id, merge SHA, and exception type folded into the message so a persistently broken signal doesn't degrade silently — it stays greppable — then `return 0`), because a throwing sweep must never break process startup; every job it can't promote simply pays the deadline exactly as if the sweep didn't exist.

#### Second-stage wake: `awaiting_live` (confirm on local observation, not backstop timer)

The `restart_free`/`signal`/`ceiling` machinery above answers "has the service restarted yet" — but once `_merged_step` actually runs (mergedAt known) and returns `WAITING` because local observation hasn't yet shown the merge live, re-parking that wait onto `path="awaiting_restart"` again is a dead subscription: `deploy_signal.merge_is_served(merge_sha)` is *already* true by construction (that's what let this evaluation start in the first place, whether via `restart_free`, `signal`, or a ceiling/awaiting_restart promotion). `deploy_gate_sweep`'s merge-is-served check has nothing left to detect — the event it watches for already fired — so a dedicated second-stage path exists to keep polling the thing that actually hasn't resolved yet: local observation.

`_merged_step`'s `WAITING` outcome carries `merged_at` and `repo` (`DeployVerifyOutcome.merged_at`/`.repo`, threaded into `DeployWaitSignal.merged_at`/`.repo` in `_wait_handover`; `repo` is an `"owner/repo"` slug resolved via `alan.teams._pr_forge.forge_target_for_ws(workspace)`). `alan/deploy_wait_gate.py::_reenqueue_decision` uses `race_deadline` to pick the re-park path: a wait with `race_deadline is None` (mergedAt is known — this is the `_merged_step` wait) re-parks onto **`path="awaiting_live"`** carrying `merged_at` and `repo` on the `GateRecord`, instead of `awaiting_restart`. This path was named `awaiting_deploy_run` before this change — the literal is renamed, and `GateRecord.from_json` accepts the old literal as a parse-time alias so a job parked across the deploy that ships this rename still resolves. The merge/forge-race wait (`race_deadline is not None`, mergedAt itself unresolved) is untouched — it still re-parks onto `awaiting_restart` with `repo=None`. The wait's human-readable `reason` (`_merged_wait_reason`) reads "confirming the merge is live via local observation."

`alan/deploy_gate_sweep.py` runs a second, independent promotion path for `awaiting_live` records: for every `PENDING` `deploy_verify` job whose `GateRecord.path == "awaiting_live"` and names a `merge_sha`, the sweep skips it outright if `GateRecord.repo` names anything other than `alan.engine_config.default_engine_config()`'s own slug (only the default engine's merges can ever resolve locally against this process's own checkout), then calls `alan.deploy_observe.observe(merge_sha, health_url=None)` and `alan.deploy_verify.is_live(...)` — **no `gh` subprocess, no network health probe**: `health_url=None` short-circuits `probe_health` and forces `health_ok=True` unconditionally, so only the containment probes (`checkout_head`/`active_slot_sha`/`is_ancestor`) can flip the promotion. `advance_eligible_at` is pull-forward-only and `PENDING`-scoped in SQL, so a job a concurrent claim already moved to `RUNNING` simply falls out of the `WHERE` clause and wins by construction. Each candidate's `deploy_observe.observe` call is individually wrapped: one job's git failure logs (with the job id and merge sha) and is skipped rather than aborting the whole sweep, so a broken checkout for one candidate never suppresses another's promotion in the same pass. The existing `ceiling`/`awaiting_restart` promotion branch (`_ceiling_record_for`/`_promote_job`, keyed on `deploy_signal.merge_is_served`) is completely untouched.

**Cadence: an in-process monotonic timestamp on `LoopMixin`, not a per-tick sqlite read.** The sweep needs to run far more often than "once at process start" for the healthy case to actually resolve in minutes, but it must not add a sqlite read to the dispatcher's 0.05s poll loop (`_DEFAULT_POLL_INTERVAL`) on every one of `N` worker threads. `alan/_dispatcher_base.py`'s `DispatcherState` carries `_deploy_gate_sweep_next_at: Monotonic` and `_deploy_gate_sweep_lock: threading.Lock`, initialized in `Dispatcher.__init__` (`alan/dispatcher.py`) to `now + _DEPLOY_GATE_SWEEP_INTERVAL_S` (60s) at construction time — deliberately *not* an already-due sentinel, so dispatcher startup never doubles up with the explicit boot-time `promote_covered_jobs` call described above; the periodic cadence supplements it rather than repeating it on every restart. `alan/_dispatcher_loop.py`'s `LoopMixin._maybe_sweep_deploy_gates`, called at the top of every `_loop()` iteration (before `run_once()`), compares `self._clock.monotonic()` against that timestamp under the lock; only the one worker thread that wins the race (if any do) advances the timestamp by another `_DEPLOY_GATE_SWEEP_INTERVAL_S` and calls `deploy_gate_sweep.promote_covered_jobs(self._queue)` — every other thread's check is a cheap in-process comparison, no I/O, no lock contention beyond the timestamp check itself. This is purely additive: `alan/routes/infra.py`'s existing `lifespan` call to `promote_covered_jobs` at process start (right after `worker_reaper.reap_and_reserve`, before pool reset/warm) is untouched, so `test_served_sha_boot_order.py`'s boot-order pin still holds. The 60s cadence means a healthy merge confirms within roughly a minute of local observation going live, not the full 90-minute backstop — while the backstop itself and the no-worker-occupied property are unchanged. Each sweep tick's local git probes are fast (local subprocess calls, no network), so unlike the pre-change `gh run list` cadence there is no meaningful per-tick blocking concern for the dispatcher loop thread.

**Visibility: `pending_deploy_gate` on the workspace detail API.** `GET /api/workspaces/{task_id}` now returns an optional `pending_deploy_gate` field (`alan.routes._workspace_detail.PendingDeployGate`, resolved by `resolve_pending_deploy_gate`) alongside the existing `_fetch_terminal_outcome`/`_detail_timing` helpers, which this same sibling module absorbed (`alan/routes/workspaces.py` sits at a hand-tight shrink-only size pin — see the Gates & conventions section — so this wiring had to move out rather than grow the pinned file; the same module now also hosts the relocated `WorkspaceDetail`/`ManagerSummary` models). The field is populated only when the workspace's `PENDING` `deploy_verify` job carries a `GateRecord` whose `path` is `awaiting_restart` or `awaiting_live` — i.e., only once verification has evaluated at least once and is deliberately parked on a live `deadline`/`race_deadline`/`merged_at`. A freshly-queued job with no `GateRecord`, or one still on `restart_free`/`signal`/`ceiling` (decided but not yet evaluated), yields `pending_deploy_gate: null`. This is presentation-only — it surfaces `path`, `deadline`, `race_deadline`, `merged_at`, and `waited_s` (via the existing `alan.deploy_verify.waited_s`) so a human looking at the workspace can tell "this is waiting on purpose, and here is what it's waiting for" without reading the audit log; rendering that into the workspace UI is separate, follow-on work (the one exception is the `live_at` timestamp described under "Delivery confirmation" below, which is a plain field on `WorkspaceDetail`, not a UI element).

**Closed by the periodic sweep: a restart-skipped merge whose own confirmation is delayed.** A merge on the `restart_free`/`signal` path (no restart needed or already served) whose local observation (checkout/slot containment) hasn't landed yet has the exact same second-stage wake as a restart-required merge: `_merged_step` runs identically regardless of which path (`restart_free`, `signal`, `ceiling`, or a promoted `awaiting_restart`) got the job to this evaluation, and any resulting `WAITING` outcome with a known `mergedAt` re-parks onto `awaiting_live` the same way.

`_RESTART_WINDOW_S` remains a plain module constant — no env var, no self-tuning. Its default dropped from the unmeasured **`1200.0` (20 min)** to **`600.0` (10 min)**, a deliberately chosen starting value from observed deployment behaviour rather than a computed statistic: it is expected to be revisited once the `verify_start` audit line above has accumulated real signal-vs-ceiling data, but that measurement is out of scope for this change (see H8.3's original note below on why `gh`-based measurement was rejected).

The `_RESTART_WINDOW_S + budget headroom` invariant this section used to pin (`600 + 900 <= 2700`, back when `_budget_s()` was a fixed relative window rather than an absolute, per-evaluation deadline) no longer applies and is deliberately not replaced by an equivalent numeric inequality: the two quantities aren't comparable the same way anymore. The invariant that matters now is **the sweep is the event, the deadline is only the backstop**: a restart-required merge is expected to resolve via `deploy_gate_sweep.promote_covered_jobs` at the next process start (the restart itself), not by outlasting a timer, and `ALAN_DEPLOY_VERIFY_BUDGET_S` only exists to bound the case where that event never arrives. `elapsed` inside `_evaluate_once` stays anchored to `merged_at`, not to when the team started running, so deferring the enqueue does not add a single second to total time-to-escalate — the wait is bounded by construction, not by a new timer racing the existing one. A ceiling shorter than some deployments' actual restart time is an accepted outcome (not a defect): `DeployVerifyTeam.restart_rederivable = True` means a restart that does happen re-derives the job from scratch afterward rather than losing it, and `test_served_sha_boot_order.py` pins that `served_sha()` is warmed, and `deploy_gate_sweep.promote_covered_jobs` runs, before `Dispatcher.start()` in `alan/routes/infra.py`'s `lifespan` — so the event this whole mechanism waits for is only ever observed from a process that has actually restarted, never from a lazily-re-read `HEAD` inside an unhealthy, still-running process.

**Rollback lever:** set `_RESTART_WINDOW_S = 0.0` (or delete the constant and inline `0.0` in `restart_gate_terms`) to fall back to an immediate enqueue with no deferral — `restart_gate_terms` returns `eligible_at=None, instructions=None` for any `window <= 0`, test-pinned by `test_restart_gate_terms_immediate_when_window_zero`. This dry-run/zero-window short-circuit is evaluated **before** any signal check, so e2e and stage — which already zero the window via `ALAN_DEPLOY_VERIFY_DRY_RUN` (`dry_run_enabled()`) — keep their existing immediate-enqueue path unchanged.

**Original H8.3 note on the fallback value's provenance, kept for context.** `_RESTART_WINDOW_S` was originally meant to be set from the measured p95 of merge-to-`ci-deploy`-completion (`gh run list --workflow ci-deploy.yml` joined against `gh pr list --state merged` on `mergeCommit.oid == headSha`, using `updatedAt - mergedAt` for successful runs, +120s slack, clamped to `[600, 1800]`). The worker sandbox that implemented H8.3 could not run `gh` — it requires interactive approval and none is available headlessly — so it shipped an unmeasured default rather than guessing a p95. This change replaces the blind-wait problem that provenance gap left behind with the signal above, rather than attempting the `gh`-based measurement a second time; the 600s value is supplied directly rather than computed.

**Accepted tradeoff (narrowed by this change, not eliminated).** A merge that still lands on the `ceiling` path sits `ACTIVE` with a `deploy_verify` job queued but not yet running for up to the width of the window (now 10 rather than 20 minutes, and shorter still if the process-start sweep above promotes it early), and nothing surfaces that fact beyond the ordinary job list — no new SSE event was added; this remains a known, deliberately out-of-scope gap. `has_active_work` counts `PENDING` jobs, so `archive`/`delete` on that workspace correctly return `409` during the window (`alan/routes/workspaces.py`), `alan/retention.py`'s sweep skips it, `TriageTeam`'s frozen-redispatch sweep skips it (queue work in flight), and `alan/proactive_rebase.py` defers behind it — all of these were already true for any `PENDING` job and required no change.

**Accepted degradation.** A merge whose restart-free-ness is undeterminable (`classify_merge` returns one of `missing_marker`, `unknown_marker_sha`, `service_not_loaded`, `merge_sha_unavailable`, `fetch_unavailable`, `classifier_error`) never triggers a restart from this gate's point of view, so no signal ever arrives for it either — `merge_sha` is typically unresolved in these cases too. Such a merge always pays the full ceiling, exactly as the fail-closed table below describes. This is intentional: any situation where the signal cannot be read or trusted falls back to the ceiling rather than proceeding as if verification were ready, preserving H8.3's original fail-closed posture unchanged.

#### Restart-free shortcut (H8.4)

`_restart_free_terms` asks `alan.teams._deploy_restart_probe.classify_merge(pr_number)` — mechanics, ordering-problem resolution, and fail-closed table: `docs/architecture/deploy.md`'s "Restart-gate deferral shortening (H8.4)" — for a `(restart_free, reason, merge_sha)` verdict before deciding the terms; the third element is new (H8.5) and feeds the `signal` path and the process-start sweep above. A restart-free merge (`restart_free=True`, e.g. `reason="safe_paths_only"`) returns `eligible_at=None` immediately: the workspace no longer sits idle for the width of the window on the ~34% of merges that never restart anything, closing the exact gap the previous revision of this section left as a follow-up. A merge that is restart-required, or whose restart-free-ness could not be determined (`reason` one of `missing_marker`, `unknown_marker_sha`, `service_not_loaded`, `hot_paths`, `merge_sha_unavailable`, `fetch_unavailable`, `classifier_error`), falls through to the `signal` check (when a `merge_sha` is available) and only then to the deferred `ceiling` branch — fail-closed, unconditionally, exactly as before this change whenever no signal is available.

The reason string itself names *why* the workspace is doing what it's doing, folding `classify_merge`'s own `reason` in verbatim (never a hand-maintained parallel vocabulary) — `"... — restart-free deploy (safe_paths_only) — verifying now"` for the `restart_free` path, `"... — the live service already serves this merge — verifying now"` for the `signal` path, `"... — deferred {window}s past the deploy restart window ({reason})"` for the `ceiling` path. This is also what answers the acceptance criterion "someone inspecting a workspace can tell whether verification is waiting deliberately or is simply not running yet": the reason string on the queued job says which of the three it is and why. A `restart_gate_decision` audit line (`deploy_verify_audit.jsonl`, `{pr_number, reason, deferred}`) reuses the same `classify_merge` `reason` string for the same identity-preserving reason, unchanged by this revision; the `verify_start` audit line above is the new, complementary record naming the *gate's own* path (which of `restart_free`/`signal`/`ceiling`) rather than the restart classifier's reason.

## Delivery confirmation: `mark_delivered` and `live_at`

`_confirmed_outcome` (the `is_live`-confirmed branch of `_merged_step`) is the one place a workspace's production delivery gets recorded durably. Before building the audit entry it calls `_mark_delivered(workspace, live_at=datetime.now(UTC))`, which:

1. Derives the workspace's active-fleet-time union as of *now* via `alan.fleet_timing.active_fleet_time_for_workspace(workspace.path)` — see `docs/architecture/workflow_visibility.md`'s "Active intervals feeding `alan.fleet_timing`" and `docs/architecture/workspace_index.md`'s "Active fleet time and lead time" for the merge algorithm. This is a live snapshot: any node still open across any invocation of the workspace (e.g. a decomposed sibling task still mid-flight) contributes up to the moment of confirmation.
2. Calls `alan.workspace.mark_delivered(workspace, settled_active_seconds=<that snapshot>, live_at=<that timestamp>)`, which re-reads `meta.json` from disk, and — only if `Meta.delivered_at` is still unset there — stamps `delivered_at=now(UTC)`, `settled_active_seconds`, and `live_at` in the same write. If another confirmation already delivered the workspace (a decomposed workspace can produce more than one PR, each running its own `deploy_verify`), the call is a no-op: none of the three fields move on a repeat confirmation.

`live_at` is the one addition to the confirmed path beyond what it did before this change — a small, independently-timestamped label answering "when did this actually go live," distinct from `delivered_at` (which conceptually could predate real deployment, before this change did, when confirmation was inferred rather than observed). It is surfaced on `alan.routes._workspace_detail.WorkspaceDetail.live_at` (`GET /api/workspaces/{task_id}`).

Only the live `_confirmed_outcome` path stamps `live_at` (and calls `_mark_delivered` with a non-`None` value for it):
- The `ALAN_DEPLOY_VERIFY_DRY_RUN` short-circuit (`_dry_run_outcome`) does not call `_mark_delivered` at all, since dry-run mode exists precisely to skip real verification (used in tests/dev) and must not fabricate a delivery timestamp.
- The engine-scope skip (`_no_pipeline_outcome`, see above) also does not call `_mark_delivered` — such an engine's workspaces complete, but nothing here independently confirmed they went live, so `delivered_at`/`live_at` both stay `null` rather than fabricating a claim the check couldn't make.
- Escalations never mark delivery either; a workspace that never reaches confirmed keeps `delivered_at`/`live_at: null` indefinitely, which is the correct "not yet delivered" state for `lead_seconds` on `GET /api/workspaces{,/{id}}` (null until confirmed).

## A crashed `deploy_verify` job reads as merged-and-live, not as a failed build

A `deploy_verify` job can fail genuinely — a raised exception mid-evaluation, an
unexpected local error — after the PR it's watching has, in fact, already merged and
gone live; the merge already happened in `pr_merge`, and `deploy_verify` exists purely
to *confirm* that, not to gate it. Before this, `Dispatcher._fail_workspace` always
parked such a crash under the generic `synthetic_blocker(str(exc))` reason, which reads
identically to a genuine build failure — misleading for a workspace whose actual code
change is already live in production.

`alan/_dispatcher_selfheal.py::merged_stamp_blocker(ws, failed_job)` recognizes this
shape from routing state already on disk, with no new persisted record: it returns a
`Blocker` only when `failed_job.team == "deploy_verify"`, the workspace's handover chain
carries a PR number (`list_handovers` walked in reverse, same pattern as
`proactive_rebase._latest_pr_number`), and — the load-bearing check — the workspace's
own pinned `engine_snapshot.transition_graph` routes **only** `pr_merge` into
`deploy_verify` (no other team's outgoing edges name it). That last condition is what
makes the inference sound: `deploy_verify`'s own existence as a job for this workspace
is possible only because `pr_merge` already succeeded and routed here, so the job's
mere existence is proof of a merge, not a hint. A missing or malformed snapshot, or any
unexpected error while checking, degrades to `None` (the ordinary crash park) rather
than ever raising or guessing — `merged_stamp_blocker` never crashes the park path it is
consulted from.

`Dispatcher._fail_genuine` consults this predicate before choosing which park branch to
take and — when it fires — threads the resulting single-element blocker list into
`_fail_workspace`'s new optional `blockers` parameter (default `None`, preserving every
other caller's existing `synthetic_blocker(reason)` behavior unchanged). The park reason
itself is deliberately still `ParkReason.CRASH` — no new park reason was added, per the
existing boundary that a new park reason is a bigger surface than this fix needs — but
the blocker attached under it carries its own distinct name, `delivery_stamp_pending`,
with a detail stating plainly that the change is merged and live and only the delivery
stamp (`delivered_at`/`live_at`) is pending. A crash on a workspace with **no** PR at
all — nothing for `pr_merge` to have routed from — parks exactly as before, with the
generic crash reason. The accepted, stated cost: the workspace-detail re-queue affordance
this park class would otherwise show is not specially rendered for `delivery_stamp_pending`
today (no new field was added to `WorkspaceDetail`, since `alan/web/src/api.ts` is held by
a parked, unrelated PR) — a dedicated header for this park class is recorded as a
follow-up once that module is free, not shipped here.

## The resumable-job signal is a property of the most recent failed job, not the workspace

`alan/queue.py`'s `_FINISH_SQL` (backing both `complete()` and `fail()`, and therefore
every terminal transition off `RUNNING` that isn't itself a resumable interruption) now
also clears `resume_inv_id = NULL`. Before this, a job that had once been requeued for
resume (`requeue_for_resume`) but then ran to a clean terminal completion or a genuine
failure kept a stale `resume_inv_id` on its row forever — a job that no longer has
anything to resume from still looked resumable to any reader of the table.
`mark_interrupted_and_fail`'s own transition rows (`INTERRUPT_WITH_RESUME` /
`INTERRUPT_CLEAR_RESUME` in `lifecycle.JOB_TABLE`) are untouched, so the legitimate case — a job genuinely
interrupted with a live session to resume — still sets `resume_inv_id` correctly; only
the *terminal* paths were leaking a stale one.

`alan/routes/_workspace_detail.py::has_resumable_failed_job(queue, task_id)` replaces
the old `bool(queue.list_resumable_failed_by_workspace(task_id))` check at both of its
call sites in `alan/routes/workspaces.py` (the workspace-detail derivation and the
`/resume` endpoint's own guard) with a narrower, symmetric one: take the **most recent**
failed job for the workspace (`list_jobs_for_workspace(..., status=JobStatus.FAILED)`,
already ordered by id, `jobs[-1]`), and report resumable only when that job both carries
a `resume_inv_id` and belongs to a team `alan._reconciler_never_started.team_supports_resume`
recognizes — the same exported capability helper the boot-time reconciler already uses,
imported without modification. Restricting to the *most recent* job (rather than any
failed job ever, which the old workspace-wide query effectively allowed) and gating on
team capability closes the gap the old check left open: a `deploy_verify` crash (never
resume-capable) sitting after an older, genuinely resumable `implementation` failure no
longer shows a `/resume` affordance that would silently no-op or misroute. Deriving the
detail flag and the endpoint guard from the identical helper means the button a human
sees and the request it fires against always agree — the old code queried the two
sites with the same underlying SQL but never guaranteed the same *team* was resumable
at both; now there is exactly one predicate.

## Import-linter layer

`deploy_verify` sits between `deploy_observe` and `compression` in the layered architecture contract (`pyproject.toml`). `teams` imports it (and `deploy_observe`); it imports nothing from alan. (This refers to the pure `alan/deploy_verify.py` verdict module; the team wrapper `alan/teams/deploy_verify.py` lives inside the much-higher `teams` layer and additionally imports `alan.deploy_observe`, `alan.fleet_timing`, and `alan.workspace.mark_delivered` for the local-observation and delivery-confirmation hooks above.)
