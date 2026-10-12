# Router

**Source:** `alan/router.py`

Hybrid policy router. Given a fresh `Handover`, decides what to do next.

## `TEAM_TRANSITION_POLICY`

`dict[str, frozenset[str]]` declaring which `from_team → to_team` transitions are legal. The router filters every candidate downstream team through this.

Terminal teams have an empty frozenset (e.g. `maintenance`, `triage`, `deploy_verify`).

Two transitions matter for the human-in-the-loop PR flow:

- `implementation → verification` is the only legal forward edge from `implementation`. There is no `implementation → implementation_plan` re-plan loop.
- `verification → {implementation, retrospective}`. The `implementation` edge is what makes `REQUEST_CHANGES` iterate on the same PR (the verification team sets `to_team_suggestions=("implementation",)` based on the judge's verdict — see [implementation_pipeline.md](implementation_pipeline.md#verificationteam)). On `APPROVE` the verification team emits `to_team_suggestions=()`, the router returns `await_human`, and the workspace pauses for the human to merge the PR. `verification → implementation_plan` and `verification → maintenance` are intentionally NOT in the policy: the original "replan on failure" loop was wasteful (most REQUEST_CHANGES reviews are addressable by code edits, not a fresh plan) and is gone.

`implementation_plan → decomposer` (the plan is handed to the `decomposer`, which decides split-vs-keep before any implementation runs — see [decomposer.md](decomposer.md)), and `decomposer → {implementation, architecture, research}`. On KEEP the decomposer forwards to `implementation`; on SPLIT it emits a terminal handover whose `injected_tasks` the dispatcher auto-applies. The human can still dispatch `implementation` manually via `POST /api/workspaces/<id>/dispatch`; that endpoint loads the most-recent handover and attaches it as `parent_handover_id` so the implementation team inherits `files_to_edit` / `test_scenarios` / `pr_number`.

### Frontend copy

`TEAM_TRANSITION_POLICY` is backend-only. The web UI used to keep a hand-maintained mirror of it in `api.ts` (feeding a fixture chain: an exported JSON fixture, a backend test pinning the fixture to the live registry, and a frontend drift test pinning the frontend copy to the same fixture) so a `RosterEditor` component could render a checkbox grid of transition edges. That mirror, the fixture chain, and `RosterEditor` are all deleted (X2.11 PR2) rather than served from an endpoint, for two reasons:

- `RosterEditor`'s only production caller was the editable engine-config form, which #1128 removed — the component has had no reachable caller since. `useTeamCatalog`, the hook that would have backed a served version, was itself already deleted in #1556.
- `GET /api/teams` (`alan/routes/infra.py`) lists the 15 registered teams, and that set doesn't match `TEAM_TRANSITION_POLICY`'s 14 keys: two registered teams are absent from the policy and one policy key names a team that isn't registered. A `transitions` field added to that endpoint couldn't mirror the policy faithfully, so serving it there was never a viable replacement for the deleted mirror.

## `select_next_team(handover, teams) -> RouterDecision`

Order of operations:

1. **Escalation handover** → `_escalation_decision(handover)` builds `kind="escalate"` with `reason=f"escalation handover from {handover.from_team}: {cause}"` and `blockers=(Blocker(kind="reason", name="escalation", detail=cause),)`, where `cause = terminal_outcome._first_line(handover.full_synthesis) or terminal_outcome._FALLBACK_ESCALATED` — the same first-line helper and 200-char cap the terminal banner's `_escalated_reason` uses, so the blocker and the banner say literally the same string. Falling back matters: `Blocker.detail` is `min_length=1`, and constructing it straight from an empty (or whitespace-only) `full_synthesis` would raise inside the router, parking the workspace `routing_error` instead of `escalated` — strictly worse than the constant this replaced. `instruction` is deliberately left unset (an escalation is not fix-worker-actionable, unlike the credential-park blocker which does set it as a remediation recipe), and `block_class` stays `None` so `park_reason_for_decision` resolves `ParkReason.ESCALATED` rather than `MERGE_BLOCK`. One accepted divergence: for a whitespace-only-but-non-empty synthesis, the banner's `if h.full_synthesis` guard is truthy and renders `""`, while the blocker's `.strip()` inside `_first_line` yields `""` and falls back to the constant — the banner path is unchanged and out of scope, so the two surfaces diverge in this one edge case.
2. **Terminal handover** → `kind="done", reason="terminal handover"`. `pr_merge` and `maintenance` emit `handover_type="terminal"` on success; the router short-circuits before any policy lookup, directly completing the workspace.
3. **Blocking question resolver** — first blocking `OpenQuestion` is routed via `_resolve_blocking`:
   - `resolver_role="human"` → `await_human`.
   - `resolver_role` is some other registered team AND it is in `TEAM_TRANSITION_POLICY[handover.from_team]` → `enqueue` resolver.
   - Otherwise → `await_human`.
   - Both `await_human` branches attach `blockers=(Blocker(kind="reason", name=question.id, detail=question.question[:500]),)` on the returned `RouterDecision` — a blocking-question park now carries the question as a structured `Blocker` (same `park.Blocker` model used for merge-gate blockers), not just prose in `reason`. `dispatcher._finalize_blocked_decision` already prefers `decision.blockers` over its synthetic fallback, so this flows straight through to the workspace's `blockers` field and the inbox clarification item — e.g. an orchestrated team's low-completeness verifier gate (see [handover.md](handover.md)'s 1.16 row) surfaces its score and gaps directly on the park panel instead of the generic "no allowed suggestion" blocker.
4. **Forward** via `_resolve_forward` → `_pick_forward_target` → `enqueue`, `await_human`, or `done` (see precedence below). The router itself reads no gate markers: a verification handover whose external-gate downgrade is flaky-only, DIRTY, or otherwise non-attributable to the diff (`GATE_ACTIONABLE: no`) arrives with `to_team_suggestions=["pr_merge"]` already — `alan/teams/_verification_routing.suggestions_for_verdict` never names `implementation_fix` for a gate-owned block (see `pr_iteration_pipeline.md`'s routing table).

`RouterDecision` shapes: `enqueue` (with `team` set), `await_human`, `escalate`, `done`.

## Forward target precedence

`_pick_forward_target(handover, teams, allowed)` picks the first match from:

1. **`handover.next_hop`** if set AND in `allowed` AND registered in `teams`.
2. **`handover.to_team_suggestions`** iterated in order; first entry in `allowed` AND `teams` wins.
3. Otherwise → no target:
   - If `allowed` is **empty** (terminal team like `maintenance`) → `RouterDecision(kind="done")` — dispatcher writes `Status.COMPLETED`.
   - If `allowed` is non-empty but nothing matched → `RouterDecision(kind="await_human")` — dispatcher writes `Status.AWAITING_INPUT`. This is what makes exploratory teams (`brainstorming`, `research`, `architecture`, `opportunity_scout`) pause for the human: each has `to_team_suggestions=()`, so the router cannot find a forward target and hands the workspace back instead of chaining.

In short: a producer can pin a specific downstream with `next_hop`; absent that, suggestions are tried in declared order. Both lists are always filtered by the legal transitions of `handover.from_team` and the registry of currently-registered teams. `pr_merge` and `maintenance` take the faster terminal path — their `handover_type="terminal"` fires the early-return at step 2, skipping policy lookup entirely. (Backlog item `handover-design-backlog-residual` #8.)

## Post-decision guards (dispatcher-side)

`select_next_team` stays pure (no I/O). The dispatcher layers additional checks on top of the returned `RouterDecision` before acting on it:

1. `Dispatcher._enforce_loop_guard` consults [loop_guard.md](loop_guard.md) and may convert an `enqueue` into `escalate` when the per-team revisit cap is exhausted.
2. `Dispatcher._enforce_pr_merge_gate` runs only for `enqueue → pr_merge` with `handover.pr_number` set: it calls `alan.teams._pr_ready.check_pr_merge_readiness` (previously lived on `_pr_branch`; the readiness-cluster split moved `check_pr_merge_readiness` to `alan/teams/_pr_ready.py` — call shape unchanged) (`gh pr view --json mergeStateStatus,reviewDecision,statusCheckRollup`) and, when GitHub reports the PR as not mergeable, hands off to `alan.merge_recovery.attempt_mechanical_recovery` (the successor to the now-deleted `_classify_pr_merge_blocker`), which classifies the block and first attempts to recover it mechanically before falling back to the remediation ladder. Remediable-but-unrecovered blockers (`mergeStateStatus != CLEAN/HAS_HOOKS` — i.e. BLOCKED for unresolved review threads, `reviewDecision=CHANGES_REQUESTED`, or failing required checks) convert the decision to `enqueue → implementation_fix` so the loop self-heals without human intervention. `DIRTY` (merge conflicts) is deliberately not among them — since D19, it first gets a mechanical rebase attempt (`alan/teams/_pr_reconcile.reconcile_branch`, onto a freshly-fetched base) and, once that budget is exhausted, falls to a bounded retry-then-`await_human` ladder carrying an explicit manual-recovery instruction, never `implementation_fix` (a plain worktree checkout cannot resolve a branch conflict). Pending checks and transient gh failures stay permissive — `pr_merge`'s own polling remains the final gate. Only when `implementation_fix` is not in the registered teams dict does the gate fall back to `await_human` for the other remediable classes. `COMMENTED` reviews are intentionally not treated as blockers. See [pr_iteration_pipeline.md §D10](pr_iteration_pipeline.md) and [§D19](pr_iteration_pipeline.md).
