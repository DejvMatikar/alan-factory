# Implementation pipeline

**Source:** `alan/worktree.py`, `alan/teams/implementation.py`, `alan/teams/verification.py`. Shared PR-branch primitives (`resolve_existing_branch`, audit helpers) live in `alan/teams/_pr_branch.py`; the subprocess runner `run_subprocess` lives in `alan/process_runner.py` (X2.7 PR22 moved it out of `_pr_branch`, where it lived when this doc was written); the push/reconcile primitives (`push_branch`, `push_fix_branch_with_reconcile`) live in `alan/teams/_pr_push.py` — at the time this doc was written all of them lived on `_pr_branch`, but the H5.1 shrink program's push-reconcile-cluster move split them out, so that no longer holds. Phase 2's `implementation_fix` / `pr_merge` teams reuse both ([pr_iteration_pipeline.md](pr_iteration_pipeline.md)). The H5.3 shrink program's PR-2 moved `append_audit` / `_write_review_artifact` into `alan/teams/_verification_artifacts.py`, and the review-post retry ladder plus the `review_post_failed`/`verification_escalation`/`judge_parse_failed`/`clause_pr_state_escalation` emitters into `alan/teams/_verification_review_post.py` — the sink file, `log_label`, and every audit row shape are unchanged; `verification.py` re-exports the symbols it still calls internally. `_write_review_artifact` persists the full, untruncated judge output so `implementation_fix` can read it via `ArtifactRef` instead of inheriting the 2500-char `summary_for_downstream` clip — see [pr_iteration_pipeline.md](pr_iteration_pipeline.md) §D7. PR-4 of the same program moved the grounded-checkout acquisition path and the cannot-verify emission path into `alan/teams/_verification_grounding.py`, and `dry_run_enabled` into `alan/teams/_verification_artifacts.py` — see the "Fail-closed grounding guard" section below for the detail.

The end-to-end "make a change to the repo" pipeline. Three teams cooperate via Layer-3 handovers:

1. `implementation_plan` produces decisions + `files_to_edit` + `test_scenarios`, then **auto-forwards** to `decomposer` (it sets `to_team_suggestions=["decomposer"]` — see [router.md](router.md#forward-target-precedence)). `decomposer` decides split-vs-keep and, on "keep", forwards straight to `implementation` (`next_hop="implementation"`) with the plan's decisions, `files_to_edit`, and the untruncated plan synthesis (`plan_synthesis` — see [handover.md](handover.md)) carried forward; no human dispatch step is required in the normal path. The implementation team inherits `files_to_edit`, `test_scenarios`, etc. from this handover chain.
2. `implementation` opens a worktree, edits/commits inside it, pushes the branch, opens a PR. On iteration (verification → implementation), it reuses the existing PR's branch instead of cutting a new one — see below.
3. `verification` reviews the diff, posts `gh pr review`, then sets its own `to_team_suggestions` based on the verdict:
   - `REQUEST_CHANGES` (or `APPROVE` with `DIFF_FINDINGS_CRITICAL > 0`) → suggests `implementation`; the router auto-iterates.
   - `APPROVE` → empty; the workspace pauses at `AWAITING_INPUT` for the human to merge the PR manually.

## Worktree primitive

`alan/worktree.py`:

- `implementation_worktree(task_id, inv_id, *, existing_branch=None) -> contextmanager[WorktreeHandle]` creates `<repo_root>/.worktrees/teams-<task_id>-<inv_id>`. Branch behavior:
  - `existing_branch is None` (first implementation run for the task) → branch `teams/<task_id>-<inv_id>` cut from `origin/main` (falls back to local HEAD if fetch fails).
  - `existing_branch="teams/<task>-<old_inv>"` (iterating on an open PR) → fetches that branch from origin, creates the worktree on it (resets to `origin/<branch>`). The handle's `base_ref` is set to the checkout SHA of `origin/<branch>` at worktree-creation time (not `origin/main`), so `captured_commits` reports only the commits added in THIS iteration, and `push_fix_branch_with_reconcile` uses it as the explicit lease anchor.
- `WorktreeHandle` carries `path`, `base_ref`, and `branch` (the actual branch name in use).
- Validates the branch came out as expected.
- Yields the handle.
- On exit: `git worktree remove --force` + `shutil.rmtree`.
- `WorktreeError` wraps all git CLI failures.
- `task_id` and `inv_id` are validated by strict regex (path-traversal defense).
- `captured_commits(handle)` lists `<sha> <subject>` lines made on the worktree since `handle.base_ref`.

## `ImplementationTeam`

Bespoke — **not** a `SingleAgentTeam`.

- Opens a worktree.
- Runs one streaming `claude` invocation inside it (`cwd=<worktree_path>`):
  - `_SYSTEM_PROMPT` — charter-derived.
  - `model="sonnet"`.
  - `--permission-mode acceptEdits` (overrides the default `auto`; the `Edit` allowlist + `auto` would require per-action approval that never arrives in headless `-p`).
  - `allowed_tools=_ALLOWED_TOOLS` — `Read`/`Grep`/`Glob`, `Edit`/`Write`, per-command `Bash(git|uv|npm|npx|node|python|python3|pytest|ls|cat|pwd|mkdir|mv|cp|rm:*)`. When set, `_build_cmd` appends `--allowedTools "<space-joined>"` — this pre-approves those tools rather than pulling them from the operator's `~/.claude/settings.json`, but it is not the runtime permission surface itself: the Bash safety analyzer governs what actually runs (see `docs/audits/auto-mode-boundary-2026-07/README.md`).
- Captures commits via `captured_commits` after the run.
- Emits a Layer-3 handover:
  - `decisions` — one per commit.
  - `artifacts_produced` — commit SHA + subject.
  - `forbidden_actions` — the baseline list only: `["don't push", "don't open a PR", "don't pip install -e .", "don't modify files outside the plan's scope"]`. The prior plan's `forbidden_actions` are NOT carried on the outgoing handover; they are inlined into the **worker's prompt** via `_forbidden_actions_block` (which does concatenate prior + baseline) so the worker observes the union, but the downstream handover only re-publishes the baseline. If you need the original prior list downstream, walk `revisit_of` / `parent_handover_id` back to the plan's handover.
- Inbound user_text prepends the prior plan's synthesis — `_prior_plan_block` prefers `prior.plan_synthesis` when set (scrubbed of acceptance-scenario fences, then sliced to just the `## Implementation steps` section via `_plan_implementation_steps`), falling back to `prior.full_synthesis` (scrubbed, then sliced to `[:DEFAULT_MAX_CHARS]`) only when `plan_synthesis` is absent (see [handover.md](handover.md#the-plan_synthesis-carrier)) — then `decisions` + `artifacts_produced`, then appends the `_forbidden_actions_block` (prior + baseline concatenated — not deduplicated; a forbidden action named in both lists appears twice). See "Implementer blindness to acceptance scenarios" below for what the scrub removes and why.
- **Subtask scope (`## Your task`).** For a decomposer DAG subtask, `compose_user_text` looks up the current task via `_pr_handover.lookup_task(workspace, task_id)` (the job's `task_id`, which `run()` no longer discards) and renders a `## Your task` block (`title` + `### Instructions` + `### Definition of Done`) immediately after the base title/description and **before** the prior plan. This is the authoritative per-subtask scope, independent of the whole-feature description and the inherited prior — it prevents a dependent subtask's worker from mistaking an already-merged sibling's code for its own completed work (the `surface-a-failed-verifier-review` no-commits/escalation loop). The free-form dispatch-`instructions` addendum renders **after** a `## Your task` block, not instead of it, followed by a precedence notice: the addendum is in-scope work added to the subtask's standing scope for this run, and wins where the two conflict, but still never widens scope to the whole-feature description or a sibling's files. Until 2026-07-26 the block suppressed the addendum entirely, which made an operator's only in-band lever for re-scoping a stuck subtask — `POST .../dispatch` with `instructions` — a silent no-op for every injected subtask (found while unblocking `fix-chat-stop-then-redirect`: a dispatch naming two failing gates reached the worker with the gate scope stripped, and the worker fixed unrelated review nits instead). When no DAG subtask matches (legacy single-task flow) the block is omitted and the addendum renders alone, as before. Defense-in-depth: `_SYSTEM_PROMPT` warns that sibling/merged code in the worktree is **not** proof of completion, and `_prior_plan_block` relabels an **escalation** prior as a `Previous FAILED attempt … UNRELIABLE` so a poisoned "already done" synthesis can't reinforce the false conclusion on redispatch.

`run_or_resume_node` always supplies both: `_overrides_to_kwargs` sets `allowed_tools` on every run (a team's `WorkerOverrides.allowed_tools` when given, else `BASE_ALLOWED_TOOLS`), so `_build_cmd` always appends an explicit `--allowedTools` — no team inherits tool permissions from `~/.claude/settings.json`. `ProcessConfig(cwd=opts.cwd)` is likewise always passed; `cwd=None` (the default for every team except `implementation`/`implementation_fix`, which set it to the worktree path) just means the subprocess runs in the process's ambient working directory, not that settings are inherited from `~/.claude/settings.json`.

### Implementer blindness to acceptance scenarios

The "dark factory" invariant: the team writing the implementation never sees the acceptance scenarios that will judge it. This is enforced by the system, not requested of a worker via prompt instructions — a worker that formats its output unusually, omits an expected section, or emits sections in an unexpected order cannot cause a leak. `alan/teams/_plan_projection.py` — a pure module with no team-specific knowledge — is applied at two independent points so that no single upstream formatting choice can reopen it:

- `strip_scenario_fences(text)` runs inside **both** branches of `_prior_plan_block`, always *before* any truncation. On the `plan_synthesis` branch it scrubs the raw plan text before `_plan_implementation_steps` ever slices it, so a worker that omits the `## Implementation steps` heading (previously: the whole plan, tail fence included, forwarded verbatim) still can't leak the fence. On the `full_synthesis` fallback branch it scrubs before the `[:DEFAULT_MAX_CHARS]` slice, so the leak no longer depends on where that 2500-char offset happens to land relative to the fence. Fence detection is **structural, not semantic** — a line-based paired-fence scanner, not a `re.DOTALL` regex: does this line open a fence, does this later line close it? It drops any fence whose info string names `decisions.yaml`/`decisions.yml` (the synthesizer's own fenced-output convention, see [teams_taxonomy.md](teams_taxonomy.md)) or whose body declares a `test_scenarios:` key, tolerating trailing whitespace, CRLF line endings, and unrelated fences earlier in the text, and — since a flagged fence that never finds a matching close cannot be safely assumed to stop leaking at the point truncation cut it off — drops an unclosed flagged fence all the way to end-of-text rather than leaving a dangling fragment in the output.
- `redact_scenarios(text, scenarios)` runs once, over the *fully composed* `user_text`, inside `_pr_handover.compose_user_text` — gated on `TeamConfig.scenario_blind` (`True` only for `implementation`'s `_TEAM_CONFIG`; `implementation_fix` stays `False`, unchanged, since a fix worker legitimately needs to discuss the acceptance command a review already flagged). It scrubs every literal occurrence of a scenario's `id` or `when` command (never `then`, which is short generic prose that would false-positive against unrelated retained text) to a visible placeholder. This is the backstop for the one channel no structural fence-filter can see — the free-text dispatch `instructions` addendum, assembled by `format_dispatch_instructions` straight from whatever an operator or upstream automation typed — and for anything `strip_scenario_fences` missed. A `when` string that legitimately reappears in retained Implementation-steps prose is over-redacted on purpose: an exact acceptance command is acceptance material wherever it turns up. A scrub hit is non-fatal and audits `scenario_leak_redacted` (`task_id`, `count`) to `impl_audit.jsonl` via `_pr_audit_log.append_audit`.

Nothing downstream of composition changed by this: scenarios still reach `verification`'s `scenario_runner` exactly as before, via `Handover.test_scenarios` — a separate structured field this projection never touches — and the acceptance-evaluation classification (`DOCUMENTED`/`SKIPPED`/`ADVISORY` non-blocking, only `FAIL` blocks) is untouched; see [acceptance_gate.md](acceptance_gate.md). **Expected side effect:** closing this leak raises the rate at which a stale or drifted acceptance reference surfaces as a genuine miss instead of a lucky verbatim echo, since the implementer can no longer coincidentally match a leaked reference it was never supposed to see. That is honest signal about real drift, not a regression, and an unresolvable reference was already non-blocking before this change (see acceptance_gate.md's `ADVISORY`/`SKIPPED` taxonomy) — this build does not, and must not, reclassify that outcome as blocking.

### Failing acceptance clause block

Before this block existed, an `implementation_fix` worker dispatched behind a failed acceptance clause audit saw only the judge's prose paraphrase of the failure and the review's clause table keyed by clause identifier with the auditor's evidence. The judge never sees the clause text, so its paraphrase routinely offered a cheaper alternative remedy that did not satisfy the clause; the worker took it in good faith, the next audit failed the same clause, and the workspace parked for a human. `_fix_forward.render_failing_clause_block(workspace, prior, task_id)` closes that gap: `implementation_fix._mid_blocks` places its result immediately after the judge-review block, as one unconditional list element (`compose_user_text` drops empty strings, so an empty render costs no branch).

- **Source of truth.** The block renders from `prior.clause_outcome` only — there is no handover-chain walk. `handover.record_answer` copies the blocking handover's fields, so an answer-recording `revisit_amend` inherits the outcome (or its absence) and the re-dispatch behind a human answer is covered by the same single read. `alan/teams/_clause_stall.py` is neither imported nor changed; the stall detector behaves as before.
- **What renders.** `## Failing acceptance clauses`: one bullet per actionable clause id, sorted by id, carrying the clause text stored on the outcome, followed by exactly three numbered instructions — satisfy each clause as written; a remedy the review offers that does not satisfy the clause text is not a remedy; name each clause identifier alongside the commit that satisfies it under `## Decisions`. The auditor's evidence is not repeated; the block points at the clause table already present in the judge review.
- **Trust.** The block is trusted host content and sits outside the `UNTRUSTED_CONTENT_NOTICE` fence that wraps the judge review. Stored clause text is truncated when the outcome is recorded (`_clause_outcome_from_rows`) and whitespace-collapsed to a single line at render time, so a multi-line or `##`-prefixed clause cannot read as prompt structure; it is not otherwise sanitised.
- **Subtask scope.** The block is skipped when `ClauseOutcome.task_id` differs from the dispatched task id. The dispatched id is `task_id or workspace.task_id`: an undecomposed workspace supplies no subtask id, and comparing `None` against the outcome's id would silence the block everywhere except decomposed subtasks. This is the same fallback `verification._effective_task_id` applies when it records the outcome, so the outcome's `task_id` is never empty.
- **Liveness.** A clause whose `Requirement` carries a withdrawal marker (`acceptance.is_withdrawn` over `workspace.meta.requirements`) is dropped. A clause id with no matching `Requirement` still renders — absence from meta is stale data, not a human withdrawal. A clause whose stored text is empty, whitespace-only or missing is omitted rather than rendered empty, and when no clause survives the block is the empty string.
- **Absent outcome.** A prior handover with no `clause_outcome` yields no block and no error. Read that as "no outcome recorded", never as "clauses passed": `_clause_audit.apply` also records nothing when the audit mode is off or report, when no requirements were selected, when the auditor's output degraded, or when the stand-down path fired.

### Per-engine worker instructions

`_SYSTEM_PROMPT` / `ENVIRONMENT_BLOCK` / `ALLOWED_TOOLS` / `_MODEL` / `_EFFORT` remain module constants in `implementation.py` — they are the **baseline** for Alan's own engine — frozen at the engine-config split and since then changed only deliberately (the test-run budget below is one such change), never as a side effect of the generic path. What changed is that `ImplementationTeam.run`/`plan_workflow` no longer read those constants directly: both resolve the workspace's governing `EngineConfig` via `_pr_forge.governing_config_for_ws(workspace)` (a teams-layer mirror of `dispatcher.governing_engine_config` — duplicated rather than imported because `dispatcher` sits above `teams` in the import-linter layer stack) and pass it through a set of `render_*` functions:

- `render_environment_block(cfg)` — returns `_render_default_environment_block(cfg)` (the existing 16-gate/2-warmup positional template) when `cfg == _ENGINE_DEFAULT`, else `_render_generic_environment_block(cfg)`, which iterates `cfg.gate_commands`/`cfg.warmup_commands` as plain lists (any length, including empty — no positional unpack) and never reuses Alan's own wording. Both forms carry the test-run budget: the default template tells the worker to iterate with targeted `--no-cov -n 0` runs of the touched test files, run the bare full suite at most once as the last gate — the captured form (`… > pytest_full.log 2>&1; rc=$?; tail -40 pytest_full.log; (exit $rc)`) is the only full-suite gate the bullet lists, so the run keeps its own exit status and is read back with `tail`/`grep` from the gitignored file, never re-run just to re-read output — and run it a second time only after fixing a red first run (hard cap two per session); the generic block states the stack-neutral form (each gate at most once, at the end, long output captured to a gitignored file in the checkout). The execution-discipline and SUCCESS text of both `implementation` and `implementation_fix` say the same thing: a failing test re-runs its own file, never the suite, and the full run is the single end-of-session coverage proof.
- `render_allowed_tools(cfg, team_name)` — `cfg.base_tools`/`cfg.team_tools[team_name]` when the engine declares them, else falls back to `BASE_ALLOWED_TOOLS`/`_IMPL_EXTRAS` (a permission-surface default, not an "instructions" fallback).
- `render_system_prompt(cfg, worktree_path)` — returns the frozen `_SYSTEM_PROMPT` for the default engine (ignoring `worktree_path` entirely), else `_compose_generic_system_prompt(cfg, worktree_path)`: a stack-agnostic prompt whose SUCCESS section (`generic_success_block`) is generated from `cfg.gate_commands` instead of hardcoding `uv run pytest`/`fail_under=100`, and which appends `repo_guidance_block(worktree_path)` — a `@CLAUDE.md` reference — only when that file exists in the checked-out worktree (checked after the worktree is created, since the default path never inspects it and the generic path must never fall back to Alan's own CLAUDE.md).
- Per-role model/effort: `_pr_forge.role_model_effort(cfg, role, default)` returns `cfg.roles[role]` when declared, else the passed-in default `RoleConfig` — used by both `plan_workflow` (workflow.yaml bookkeeping) and `run` (the actual `run_agent_streaming` call), so both agree even before the worker starts.

`implementation_fix.py` mirrors this exactly (`_pr_forge.governing_config_for_ws`, its own `render_system_prompt`/`_compose_generic_system_prompt` built from `implementation.render_environment_block`/`generic_success_block`/`repo_guidance_block`, its own frozen `_SYSTEM_PROMPT` baseline) — the two teams changed in the same commit so neither import breaks collection. `verification.py` only converts its per-role model selection: `roles_for_config(cfg)` resolves `diff_reviewer`/`scenario_runner`/`judge` models from `cfg.roles` (falling back to `_ENGINE_DEFAULT.roles[role]`) while the three role prompts stay fixed — the verification loop's own bookkeeping conventions (`test_scenarios.yaml`, `SCENARIOS_MISSING`, etc.) are Alan's orchestration protocol, not the governed repo's gates, so they are not genericized.

**The `_role_system_prompt` seam (`StaticPipelineTeam`, separate from the rendering above).** `implementation`/`implementation_fix` are bespoke teams, not `StaticPipelineTeam` subclasses, so the per-engine rendering described above is their own. The six `StaticPipelineTeam` subclasses (`brief_refine`, `decomposer`, `architecture`, `implementation_plan`, `maintenance`, `verification`) share a separate, narrower seam for the same class of problem — the planning team's own guidance text, not a worker's environment/system-prompt block. `StaticPipelineTeam._role_system_prompt(role, workspace)` (`alan/teams/_static_pipeline.py`) defaults to returning `role.system_prompt` unchanged; `ImplementationPlanTeam` is the only override, returning `scenario_authoring.synthesizer_prompt(cfg)` for the `synthesizer` role (`cfg` resolved the same way, via `_pr_forge.governing_config_for_ws(workspace)`) and `role.system_prompt` for every other role. The seam is read at **both** `_run_role` (the role's normal turn) and `_parse_or_invalidate`'s re-ask (the correction turn) — applying it at only one site is the known wrong shape: the correction turn is precisely where an authoring-time acceptance-scenario rejection lands (see [acceptance_gate.md](acceptance_gate.md#authoring-time-scenario-validation-planning-stage)), so a worker being asked to fix that rejection must see the same engine-aware guidance it saw on its first turn, not the guidance for a different engine or none at all. `brief_refine.py` and `verification.py` both bypass `_run_role`/`_parse_or_invalidate` entirely for their own correction turns (`brief_refine.py`'s single-role re-ask builds its system prompt from `ROLES[0].system_prompt` directly; `verification.py`'s `roles_for_config`/`judge_system_prompt` assemble role prompts outside the shared role runner), so the seam is inert for those two teams regardless of whether they override it — only `architecture`, `decomposer`, and `maintenance` exercise the *default* seam behavior in production today. `run_team()` (`POST /api/plan`, `implementation_plan.py`'s standalone non-workflow entry point) has no workspace to resolve an engine from, so it keeps calling `role.system_prompt` directly and always renders the default-engine text — a third, different-shaped bypass from `brief_refine`'s and `verification`'s.

### Turn budget

The global `MAX_TURNS=150` (flat ceiling in `alan/teams/_base.py`) applies to every team by default, and `WorkerOverrides.max_turns` is the orthogonal per-run escape hatch. These two teams currently override it via module-local `_MAX_TURNS` constants (temporary upscale): `implementation` runs at `_MAX_TURNS=500` and `implementation_fix` at `_MAX_TURNS=300`. Both run on Sonnet at `xhigh` effort (`_MODEL="sonnet"`, `_EFFORT="xhigh"`, passed as the per-call `effort` override so the worker runs above the default `sonnet→high` mapping). Revert `_MODEL`/`_EFFORT`/`_MAX_TURNS` to change the model, effort, or drop back to the 150 turn default.

### Salvage: uncommitted worker output and turn-ceiling exhaustion

Both write-enabled worker teams (`implementation`, `implementation_fix`)
share a uniform salvage handler from `alan/teams/_pr_handover.py` so a worker that
finishes its edits but never commits — or that runs out of turn budget mid-edit —
never dead-ends as an orphaned branch, a discarded diff, or an empty human
escalation. Each team's `_run_worker` helper wraps the single streaming call:

`salvage_uncommitted_wip` returns a three-way `SalvageOutcome` (`"clean"` /
`"salvaged"` / `"failed"`) rather than a bare bool, so a failed salvage commit is
distinguishable from a tree that was never dirty in the first place — a worker can
commit part of its own work, leave a second change dirty, and have the host's own
`git commit` fail (disk full, hook rejection, etc.); collapsing that into the same
falsy value as "nothing to salvage" would make the ensuing narration claim the tree
was clean when a diff was in fact dropped on the floor.

- **Normal completion** (`run_or_resume` returns): the team calls
  `_pr_handover.salvage_uncommitted_wip_detail(handle, task_id, team_name)` before
  computing `captured_commits(handle)`, and captures its `SalvageResult` (`outcome` +
  `skipped_count`) return. Rather than a blind `git add -A`, it runs one
  `git status --porcelain -z` and partitions the entries into `staged`/`skipped` —
  see "Build-artifact filter on salvage" below for the exact partitioning rule. When
  `staged` is non-empty, it commits those paths under a dedicated host identity —
  author/committer `alan-salvage <alan-salvage@users.noreply.github.com>` — with a
  message carrying a `Salvaged-by: alan-salvage` trailer, so a reviewer can tell the
  commit was created by the host recovery path rather than authored by the worker or
  a human, and returns `"salvaged"` on success or `"failed"` if the `git add`/`git
  commit` step itself errored or returned non-zero. A tree with nothing to stage
  (genuinely clean, or dirty only with skipped artifacts) is a no-op commit-wise and
  returns `"clean"`. The team then passes that outcome through
  `_pr_handover.with_salvage_notice(synthesis, salvaged, skipped_count=...)` before
  using the result downstream: `"salvaged"` appends a fixed notice stating the host
  recovery path committed the work under the alan-salvage identity and that the
  worker's own gate result was never observed on this commit and must be re-run
  before downstream verification or fix loops rely on it; `"failed"` appends a
  distinct notice stating the diff was NOT recovered and may be missing from the
  commits below; `"clean"` returns the synthesis byte-identical (no claim is made,
  since nothing happened). Neither notice ever tells downstream to judge the salvaged
  commits like any other, already-gated contribution — a salvaged commit's local
  gates were never run by anything. The (possibly salvaged) commit list then flows
  through the team's ordinary push/PR/finalize path exactly as if the worker had
  committed it itself — `implementation` additionally still applies the drift-gate
  backstop resume on top of this.
- **`TurnCeilingReached`** (CLI `subtype=error_max_turns`): each team's `except
  TurnCeilingReached as exc:` handler calls `_pr_handover.salvage_on_ceiling(handle,
  task_id, team_name, exc)`, which runs the same salvage step via the thin
  `salvage_uncommitted_wip` wrapper (again capturing its `SalvageOutcome`), recomputes
  `captured_commits(handle)`, and appends a `ceiling_salvage` audit entry (always,
  even when there are zero commits — fields: `team`, `branch`, `commit_count`,
  `has_commits`). If commits are present (worker-made and/or salvaged), it returns a
  synthesized `(synthesis, commits)` pair built by `ceiling_salvage_synthesis(team_name,
  commits, salvaged=...)`, which branches on that outcome into three distinct
  narrations instead of asserting the same claim unconditionally: `"salvaged"` (the
  worktree was dirty and the host committed it) states the host recovery path
  committed the work under the alan-salvage identity and is routing it into the
  team's normal push/PR/finalize flow, with the worker's own gate result never
  observed on these commits and needing a re-run; `"clean"` (the worktree was already
  clean at ceiling time but commits exist because the worker committed its own work
  before idling) states plainly that these are the worker's own commits, routed the
  same way, with the same never-observed-gate caveat (the ceiling means the worker
  never reached its own success-block gate run either); `"failed"` (the host's own
  salvage attempt failed) states that any uncommitted diff was NOT recovered and may
  be lost, while the listed commits are still the worker's own, routed the same way.
  Every narration is produced before that flow runs, so none of them assert that a
  push or PR already happened — push/PR-open can still fail or be skipped in dry-run
  — and none of them tells downstream verification/fix to judge the commits like any
  other, already-gated contribution; each instead states the worker's own gate result
  was never observed and must be re-run (accounting for a possibly missing diff in
  the `"failed"` case). The team proceeds through its normal push/PR/finalize flow
  instead of raising, so the workspace routes into verification
  (`implementation`/`implementation_fix`) rather than terminating as a failed job
  with an orphaned branch. If there are no commits at all — nothing dirty to salvage
  and no commits of the worker's own — `salvage_on_ceiling` re-raises the caught
  `TurnCeilingReached` exception explicitly and the existing turn-ceiling failure
  behavior is unchanged — turn-budget ceilings themselves are not altered by any of
  this; see "Build-artifact filter on salvage" below for how this re-raise now also
  covers an artifact-only tree.

The recovery path never judges correctness: a salvaged change may open (or update) as
a failing PR, and the existing CI/verification/fix loop is responsible for detecting
and repairing it, exactly as it would for any other contribution.

### Build-artifact filter on salvage

Salvage no longer blindly `git add -A`s the worktree. `alan/teams/_pr_handover.py` runs a
single `git status --porcelain -z` call and partitions its entries into `staged` and
`skipped` via `_partition_porcelain`/`_is_artifact_path`: an entry is skipped **iff** its
status columns are exactly `??` (untracked) **and** any `/`-separated path component is
one of a closed set (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`,
`.DS_Store`, `DerivedData`, `.xcresult`, `htmlcov`, `.coverage`, `node_modules`, `.venv`,
`.tox`). A tracked modification is always staged regardless of its path — the `??`
requirement falls straight out of git's own porcelain columns, so a repository that
currently tracks a file matching one of these names keeps salvaging it normally. Staging
itself moved from `git add -A` to `git add --pathspec-from-file=- --pathspec-file-nul`
fed the `staged` list over stdin, avoiding both an `ARG_MAX` ceiling and pathspec
quoting bugs on unusual filenames.

The filter's granularity is exactly git's own default `--untracked-files=normal`
behavior: an untracked directory that itself doesn't match the artifact set is reported
(and staged) as a single line, so a brand-new directory that happens to nest an artifact
inside it (e.g. a new `feature/` containing `feature/__pycache__/x.pyc`) is staged
wholesale — this is a deliberate, accepted residual, not a bug, and is exactly why the
filter is host-level and engine-independent rather than dependent on any repository's
`.gitignore`: `alan/slot_pool.py`'s `_clean_slot` deliberately *preserves* bytecode
across slot reuse (mtime-based Python cache invalidation makes that safe and keeps warm
boots fast), so a `.gitignore`-based filter would still let a slot-pool worktree's own
untouched `__pycache__` leak into a commit the moment it happened to sit inside a
newly-created directory.

`salvage_uncommitted_wip_detail` returns a `SalvageResult(outcome, skipped_count)`
instead of a bare `SalvageOutcome`; the thin `salvage_uncommitted_wip` wrapper still
returns just the outcome for existing callers. A tree whose only dirt is artifacts
partitions to `staged=[]` with `skipped` non-empty: this reports `"clean"` (no commit)
but — unlike a genuinely empty diff — writes a `wip_salvage_artifact_only_clean` audit
row (`skipped_count`) so the two "clean" cases are distinguishable after the fact. A
real commit's audit row (`wip_salvage_commit`) gains `staged` (path-capped) and
`skipped_count` alongside the existing `ok`/`team` fields. **Intended outcome change:**
because `salvage_on_ceiling` (unedited) re-raises `TurnCeilingReached` whenever no
commits resulted, an artifact-only tree at the turn ceiling now parks as a crashed
workspace instead of opening a PR containing nothing but a worker's synthesis and zero
real diff.

Workers additionally stop writing Python bytecode into their checkouts at all:
`alan/fleet_load.py::fleet_env` sets `PYTHONDONTWRITEBYTECODE=1` in every fleet worker's
environment, a `python`/`pytest` subprocess-level switch independent of both the git
filter above and the slot pool's own bytecode-preserving `_clean_slot`.

`with_salvage_notice` takes an optional `skipped_count` keyword; when the outcome is
`"salvaged"` and the count is non-zero, the notice appended to the worker's synthesis
names how many artifact paths were left out of the commit, so a human reading a thin or
parked PR can tell a false-positive skip from a genuinely small diff.

`push_branch` / `push_fix_branch_with_reconcile` handle their own subprocess errors
internally (return a failure value, never raise), so the salvage path cannot mask a
push failure — an unpushable salvage still surfaces via the ordinary
`iteration_push_failed` / `PushOutcome.PUSH_FAILED` escalation paths. Audit rows land
in `~/.alan/logs/impl_audit.jsonl` (override via `ALAN_LOGS_ROOT`): `wip_salvage_commit`
(`ok` bool, `team`) whenever a dirty tree was found, and `ceiling_salvage`
(`commit_count`, `has_commits`) on every turn-ceiling exhaustion. `implementation_fix`
only additionally writes `scenario_block_unparsed` (`task_id`, `team`, optional
`parse_error`/`source_line`) to the same file, whenever a positively-identified
`test_scenarios.yaml` block yields zero scenarios after per-entry recovery — see
[acceptance_gate.md](acceptance_gate.md#scenarios_missing-self-heal-implementation_fix-re-emit--forward)
for the full parser/recovery/audit contract.

### No-commit self-heal (work already merged by a sibling)

Salvage rescues work that *exists but was not committed*. The complementary case is a
worker that makes **zero commits on a genuinely clean tree** because its deliverable
already landed on `main` via a **sibling subtask's merged PR** (e.g. the first subtask
of a decomposer DAG over-delivered and implemented a later sibling's scope too). That
outcome is a `no_commits_handover` escalation (`{team} produced no commits —
escalating`), which the router maps to `escalate` → a human — a false negative for a
leaf that is genuinely done.

`Dispatcher._maybe_self_heal` turns that false escalation into a grounded completion.
It runs in `_route_after_handover` **outside the per-workspace lock** — alongside the
slow `_enforce_pr_merge_gate` `gh pr view`, honoring the invariant that the workspace
mutex is never held across external subprocess calls (scenario execution can spawn
`pytest`). It fires **only** when all of: the decision is `escalate`; the handover is a
no-commits escalation (`loop_guard.is_no_commit_escalation`, keyed on the
`_NO_COMMITS_SENTINEL`); the workspace has a merged sibling
(`terminal_outcome.has_merged_sibling` / `merged_sibling_pr_number`); and the escalating
leaf carries runnable `scenarios`. It then executes the leaf's **own DoD acceptance
scenarios against merged `main`** via `_scenario_runner.run_scenarios` (the same
allowlist-gated executor the verifier uses; the run is exception-safe — any failure
yields no results via `_run_self_heal_scenarios` and falls back to escalation) and, iff
`≥1` scenario is `PASS` and `0` are `FAIL` (`_scenarios_confirm_done`;
`DOCUMENTED`/`SKIPPED` are non-blocking), rewrites the decision to `done` and stamps the
merged sibling PR onto the handover. The locked section then applies the normal
`_advance_workspace_dag` path — marking the leaf `COMPLETED`, stamping
`PR #<merged-sibling>` DoD evidence, and advancing the DAG. Any `FAIL`, no runnable
scenarios, or no merged sibling leaves the decision as `escalate` exactly as before, so
the heal is grounded by execution and never trusts the worker's prose (consistent with
the "sibling/merged code is not proof of completion" guard above). A
`self_heal_audit.jsonl` row (`task_id`, `workspace_id`, `pr_number`, per-scenario
statuses) records each heal.

## Loop closer

Before the worktree is opened, the team looks at `prior.pr_number` (set by the upstream verification handover). If non-`None`, `_resolve_existing_branch(pr_number)` calls `gh pr view <n> --json headRefName -q .headRefName` to look up the PR's head branch. On success it threads `existing_branch=<that branch>` into `implementation_worktree`; on any failure (gh missing, timeout, non-zero return, empty stdout) it audits `pr_view_failed` / `pr_view_empty` and falls back to the fresh-branch path (which will open a brand-new PR — the orphan PR is left for the human to close).

After `captured_commits` succeeds, `_push_changes` runs and returns a frozen `PushChangesResult` (`pr_number`, `credential_failure`, `pushed`) rather than a raw tuple — `pushed` is `True` for the no-commits, dry-run, and existing-PR-reuse branches, and `False` only when the actual `git push` did not land (`push_result.outcome is not PushOutcome.PUSHED`). This is the one point in the pipeline that knows whether the commits themselves are safe on the remote, as opposed to merely absent a PR — see "Escalation text distinguishes a failed push from a failed PR create" below for why that distinction is threaded downward instead of re-derived:

```bash
git push -u origin <handle.branch>
# Only when no existing PR was resolved:
gh pr create --title "<task title>" --body "<synthesis>" --base main --head <handle.branch>
```

A rate-limited or network-failed `gh pr create` raises `ForgeRateLimited`/`ForgeTransientError` for a dispatcher-level pause/retry (see [queue_and_dispatcher.md](queue_and_dispatcher.md) §"Forge (GitHub) failures ride the same two paths") instead of returning `None` and escalating as "committed work was lost"; only domain-shaped failures (e.g. "a pull request for branch X already exists") and unclassified ones keep the `None` + `pr_create_failed` audit path. PR number parsed from the `gh pr create` output via `_PR_URL_RE`. When iterating on an existing PR, the push lands new commits on the same branch and `gh pr create` is skipped entirely — the handover returns the original `pr_number` so verification re-reviews the same PR. Each action logs one JSON line to `~/.alan/logs/impl_audit.jsonl` (override via `ALAN_LOGS_ROOT`; file mode `0o600`).

- Live by default in the launchd deploy: push + `gh pr create` run on every implementation invocation with commits AND no inbound `pr_number`. Export `ALAN_IMPL_DRY_RUN=1` (truthy: `1`, `true`, `yes`, `on`) to opt back into dry-run + `action=dry_run` audit — useful for prompt experimentation or running against a repo the launchd user can't push to. In dry-run, the existing `pr_number` is still propagated forward so verification can still inspect the open PR.
- Push / PR-create failures escalate rather than forwarding to verification:
  - **Iteration, still targeting an existing PR's branch** (`existing_pr is not None` — `_resolve_existing_branch(prior_pr, ...)` resolved a branch for this run and `worktree_iteration` checked it out): escalate with a `push to PR #N failed` message; `pr_number` is carried on the escalation so a human can locate the open PR. The worktree's new commits are lost when the worktree is removed. This wording is threaded down as `continuing_pr=True` and is not further gated on `pushed` in that branch — a genuinely continuing iteration can only reach this escalation in the first place when the push itself failed (a successful push against an already-resolved existing PR returns that PR's number from `_push_changes`, so `iteration_push_failed` is never set and `push_failed_handover` is never called).
  - **First run, or a prior `pr_number` that this iteration could not resolve to a branch** (`continuing_pr=False` — either there was no existing PR at all, or `_resolve_existing_branch` failed against a stale/closed `pr_number` this run, e.g. the PR was closed or `gh pr view` hiccupped, so `implementation.py`'s `existing_pr` comes back `None` and the run committed to a brand-new branch instead): escalate with a `no PR is available` message naming the branch; `pr_number` on the escalation is still whatever the inbound handover carried (a stale reference in the second case — see the open question in `push_failed_handover`'s history for why that isn't rewritten to `None`). **The escalation text depends on `pushed`.** When `pushed` is `False` (the `git push` itself failed), the wording is unchanged from before: "committed work was lost when the worktree was removed" — the worktree and its commits are removed on teardown and the branch name in the escalation is the only recoverable reference. When `pushed` is `True` (the push landed but `gh pr create` failed, or its output couldn't be parsed for a PR number, or — in the stale-`pr_number` case — the branch simply couldn't be resolved this run), the escalation instead states the commits are SAFE on the named remote branch and tells the reader not to re-implement — open a PR from that branch by hand, or re-dispatch `implementation` to reuse it. Before `continuing_pr` existed, a stale inbound `pr_number` alone was enough to take the "lost" branch above regardless of what actually happened this iteration, which could send an operator hunting for destroyed work that was in fact sitting untouched on `origin` under a brand-new branch name. This path is narrower than it once was — a rate-limited or transient `gh pr create` failure now raises for dispatcher retry (previous paragraph) instead of ever reaching this escalation — so what remains here is an authentication failure, an unclassified/unrecognised `gh pr create` failure, a failure to parse the PR number back out of a successful `gh pr create`'s output, or an unresolvable existing branch.
  - **Dry-run** (`ALAN_IMPL_DRY_RUN=1`): push is skipped; `pr_number` stays `None` for fresh runs, but the handover is forwarded (not escalated) because dry-run is not a failure. `PushChangesResult.pushed` is `True` here too (nothing was lost — the push was deliberately skipped), though it's moot since a dry-run never sets `iteration_push_failed`.

### The pull request body is a worker-authored deliverable

`_open_pr` no longer derives the PR body from the closing synthesis via a bare `_pr_body.build_pr_body(synthesis)` call — it calls `_pr_body.authored_body(synthesis)`, which is `build_pr_body(extract_authored_body(synthesis) or synthesis)`. A worker that wants to control what the public PR body says emits a fenced ` ```pr_body ` block anywhere in its final response; `extract_authored_body` parses it out (via a break-proof fence — the block's own opening backtick run is the required closing run, so a body that itself contains a fenced snippet can simply widen the delimiter) and that text, not the raw synthesis, becomes the body. A worker that supplies no block gets exactly the old derived-from-synthesis behavior — the fallback is the literal `or synthesis` branch, not a separately-maintained code path, so there is no way for the two behaviors to drift apart. Both paths share one `PR_BODY_MAX_CHARS=60000` truncation-with-marker normalization, same as before this change.

Both `implementation` and `implementation_fix` append the same `alan/teams/_pr_body.py::PR_BODY_BLOCK_INSTRUCTIONS` string to the *result* of `prompt_templates.render_prompt(...)` in their respective `render_system_prompt` functions — one instruction constant, appended post-render so it reaches a worker regardless of whether the alan-default, generic-engine, or stored-prompt-template path composed the rest of the system prompt. The instructions tell the worker three things the codebase's no-comments convention makes load-bearing: this text becomes the *public* PR body and the permanent squash-merge record of *why* a change was made; it is a different artifact for a different audience than the `## Summary` / `## Decisions` synthesis the next internal stage reads (both should be written, and neither substitutes for the other); and, specific to `implementation_fix`, a supplied block **replaces the live body wholesale** rather than patching it. `implementation_fix` additionally amends the *live* PR body after a successful push — see [pr_iteration_pipeline.md](pr_iteration_pipeline.md) §D20 for the amendment mechanism, its ordering relative to the push, and how a body-only fix round (no code change, only a revised body) still counts as forward progress into re-verification rather than a no-commits escalation.

### Escalation text distinguishes a failed push from a failed PR create

`_push_changes` is the only code that knows which of "the push failed" vs "the push succeeded but PR create/PR-number-parse failed" actually happened; that distinction used to be discarded the moment `_push_changes` returned, collapsing both into the same `pr_number=None` shape. `PushChangesResult.pushed` now carries it forward, unchanged, through `ImplementationTeam.run` → `_pr_handover.finalize_handover` → `_pr_handover.build_handover` → `_pr_handover.push_failed_handover`, which is where the escalation text is composed — the wording is never re-derived after the fact from `impl_audit.jsonl`. `pushed`, together with `continuing_pr`, `branch`, `credential_failure`, and `forge_target`, is threaded as one frozen `PushFailureContext` (a `push_failure` keyword on both `finalize_handover` and `build_handover`) rather than five separate keywords — `build_handover` unpacks it into `push_failed_handover`'s own flat keywords only on the `iteration_push_failed` branch, which is the sole place any of the five is read. `push_failure` itself defaults to `None`, and `PushFailureContext()`'s own field defaults (`pushed=True`, `continuing_pr=True`) apply whenever `build_handover` substitutes an empty context — so `implementation_fix`'s existing call sites (which never pass `push_failure`) are behaviourally unchanged: `implementation_fix.run` already bails out via its own no-PR escalation before `prior_pr is None` could ever reach `push_failed_handover`'s no-prior-PR branch, so the default value is inert for that team — see [pr_iteration_pipeline.md](pr_iteration_pipeline.md) for the fix-branch reconcile push this doesn't touch.

A second, independent signal travels inside the same `PushFailureContext` as `pushed`: `continuing_pr` answers "is this iteration actually still targeting the PR named on the inbound handover?", not merely "did the inbound handover carry a `pr_number`?" — `ImplementationTeam.run` computes it as `existing_pr is not None` (`existing_pr` is itself `prior_pr if existing_branch is not None else None`, i.e. `_resolve_existing_branch` had to succeed *this run*). The two signals answer different questions and both matter: without `continuing_pr`, a stale inbound `pr_number` whose branch this run couldn't resolve (closed PR, forge hiccup) would take the "push to PR #N failed / lost" wording even when the push to the *fresh* branch this run actually cut had succeeded — the exact false-loss report this whole mechanism exists to prevent. `push_failed_handover` only takes that wording when `continuing_pr` is `True`; it deliberately does not additionally gate that branch on `pushed`, because the only way `continuing_pr=True` reaches `push_failed_handover` at all from `ImplementationTeam.run` is a genuine push failure — a successful push against an already-resolved existing PR returns that PR's number from `_push_changes`, short-circuiting before `iteration_push_failed` is ever set. `PushFailureContext.continuing_pr` defaults to `True` for the same reason `pushed` does: `implementation_fix` never passes a `push_failure` and always operates on an already-resolved existing branch (its own bail-out guard), so the default reproduces its unchanged historical behavior.

## Fail-closed grounding guard

Two independent mechanisms stop paid worker runs from ever proceeding against a working directory that isn't a real repository checkout — one at the PR level, one structural. Both were added to close a failure mode where verification, dispatched without a resolvable PR, silently substituted the workspace's orchestration-metadata directory (`workspace.path` — never a git checkout) as its working directory and ran its full multi-role pipeline there: every git/PR-dependent step failed, the run produced no usable signal, and the resulting rejection verdict routed the workspace into a pointless fix-then-reverify loop, each blind pass costing real money.

### Merged-PR short circuit (`VerificationTeam.run`, precedes the PR precondition)

Immediately after resolving `(pr_number, branch)` — and before the `pr_number is None or branch is None` precondition check below decides what a `None` branch means — `alan/teams/_verification_merged_guard.py::terminal_if_merged` gets one shot at short-circuiting the whole run. It exists because a squash-merge deletes the PR's head branch: without this guard, an escalation answered (or a stray re-dispatch) after a PR has already merged would resolve `branch = None` and fall straight into `_pr_precondition_failed_escalation` below, which reports "PR missing" — a park, not a completion, for a workspace that is in fact done. Placing the guard *after* branch resolution (so a squash-deleted branch is still looked up, since that lookup is what a normal open-PR pass needs anyway) but *before* the precondition's null check is what lets a merged-but-branchless PR read as "already merged," never as "PR missing."

`pr_state_name(pr_number, forge_target)` makes one `gh pr view --json state` call and returns the raw state string, or `None` on any failure — missing `gh` binary, timeout, non-zero exit, empty stdout. This is a genuine three-way posture, not a boolean: `"MERGED"` short-circuits, any other non-empty state (`"OPEN"`, `"CLOSED"`) falls through to the ordinary pipeline unchanged, and `None` — a transient forge blip — **also** falls through rather than being treated as evidence of anything. A `gh` hiccup must never silently terminate a live verification pass; the guard is only ever affirmative, never a default-closed gate. Only an exact `"MERGED"` match, from a call made fresh on this invocation, ends the run — there is no caching, no polling, and no second consultation of an earlier pass's answer.

On a confirmed merge, `terminal_if_merged` appends a `verification_merged_short_circuit` row to `verification_audit.jsonl` and returns a `build_terminal_handover` carrying the merged PR's number — never the missing-PR escalation handover (whose `None` return is already overloaded to mean "branch gone," which here would misreport a completion as a park), and never a bare `return` that skips `workflow_lifecycle` (which would leave the seeded invocation `pending` forever). The workspace finishes; it does not park, and no worker — diff_reviewer, scenario_runner, judge, clause_auditor — is ever spawned for a PR verification has nothing left to do on.

### PR precondition (`VerificationTeam.run`)

Once the merged-PR short circuit above has had its chance and found nothing, `VerificationTeam.run` re-examines the same `pr_number`/`branch` pair it already resolved. If `pr_number is None` **or** the branch can't be resolved (closed/unknown PR, or a `gh` outage — the two are indistinguishable from the caller's side and both must fail closed), `VerificationTeam._pr_precondition_failed_escalation` runs instead of the pipeline:

- Enters and exits `workflow_lifecycle` without starting any node, so the invocation's seeded `workflow.yaml` still finalizes (`status="success"`, tasks left `pending`) instead of lingering at `pending` forever — the same invocation-status invariant `implementation_fix._no_pr_escalation_with_workflow` already upholds for its own no-PR case. Unlike `implementation_fix` (a single-node `implementer` pipeline, whose escalation starts and completes that one node), verification's pipeline plans multiple nodes (`diff_reviewer` / `scenario_runner` / `judge`); none of them ran, so marking any one of them `success` would misrepresent that a stage executed. Leaving all planned tasks `pending` while the invocation itself finalizes `success` is the deliberate choice here — the two escalation shapes intentionally differ at the task level while sharing the same invocation-level invariant.
- Looks up `_pr_handover.find_last_pr_reference(workspace)` — the newest handover in this workspace's chain that carries a non-null `pr_number` — and folds its `handover_id` / `from_team` / `from_invocation_id` into both the escalation's reason string and a `pr_precondition_failed` audit row (or notes explicitly that no prior handover in the chain ever carried one). This is provenance only: recovering the lost reference (re-deriving it from a branch, walking the chain, parsing dispatch instructions) is out of scope here — the human's exit from this state is manual.
- Builds the escalation Handover through the shared `_pr_handover.HandoverContext.build` + `escalation_skeleton` (via the new `no_pr_escalation_handover` helper), carrying `dependency_bot_authorized` forward from the inbound prior rather than defaulting it — verification, unlike `implementation_fix`, has a live prior to inherit from.

`implementation_fix` already had an equivalent precondition (`run()`'s `prior_pr is None or existing_branch is None` branch, unchanged by this work) — its `_no_pr_escalation` now routes through the same `no_pr_escalation_handover` helper (with `dependency_bot_authorized` explicitly `False`, matching its pre-existing observable output byte-for-byte) so the two teams no longer hand-roll their own escalation Handover construction. `pr_merge` and `deploy_verify` need no equivalent: they poll `gh` from the host process and never spawn a worker, so they can't repeat this failure mode.

The grounded-checkout acquisition path and the cannot-verify emission path now live in `alan/teams/_verification_grounding.py` (H5.3 shrink program's PR-4): `_try_acquire_grounded_checkout`, `_cannot_verify_trigger_active`, `_grounded_slot_graph`, `_post_cannot_verify_comment`, and `_emit_cannot_verify` all moved there together, since `_cannot_verify_trigger_active` is `_try_acquire_grounded_checkout`'s sole gate and `_post_cannot_verify_comment` is `_emit_cannot_verify`'s sole GitHub-posting path — splitting them across modules would have manufactured an import cycle back into `verification.py`. `verification.py` re-exports only `_try_acquire_grounded_checkout` and `_emit_cannot_verify` (the two it still calls from `VerificationTeam.run`); the other three are internal to the grounding module and have no facade. `dry_run_enabled` — read by both `_post_cannot_verify_comment` (grounding module) and `_maybe_post_review` (`verification.py`) — moved in the same PR into `alan/teams/_verification_artifacts.py`, the shared leaf both modules already import from, and is re-exported from `verification.py`; both call sites share the single `ALAN_VERIFICATION_DRY_RUN` env read rather than each polling it independently.

Once the precondition passes, `_try_acquire_grounded_checkout(workspace, pr_number, branch, task_id, inv_id, emit)` takes the pre-resolved `branch` directly — it no longer resolves the branch itself, so a grounded checkout never triggers a second `gh pr view`. It still checks `_cannot_verify_trigger_active(task_id)` first (E2E bypass, unchanged) and still leases the worktree via `implementation_worktree`, auditing `verifier_checkout_ok` / `verifier_checkout_failed`. **The `branch_lookup_failed` arm this function used to have is gone** — an unresolvable branch is now caught by the precondition above and never reaches this function at all. What survives here is a lease/worktree failure on an otherwise-resolved branch (disk full, a slot pool contention timeout, etc.): the team still emits a `cannot_verify` TeamEvent, posts a `--comment` GitHub review, and returns an empty-suggestions handover (routes to `await_human`) — never a merits-based rejection. E2E trigger for the bypass: the `ALAN_E2E_VERIFICATION_CANNOT_VERIFY` path env var (`_cannot_verify_trigger_active` — the file must exist and its content be empty or equal to the task_id, consistent with the content-scoped stub triggers; see `e2e_testing.md`).

### Structural seam guard (`_require_grounded_cwd`)

The PR precondition only covers the missing-PR case. The broader structural guard in `alan/teams/_base.py` catches every way a role's *deliberately chosen* working directory can stop being a real checkout — a worktree that vanished after a slot reset, a stale path, a future regression that loses grounding some other way — regardless of which team or code path produced the bad `cwd`.

`_require_grounded_cwd(overrides, *, task_id, inv_id, team, role, node_id)` is the first statement in both `_run_fresh_node` and `resume_workflow_node` (via the shared `_begin_node_run` helper), running before `_start_node_locked`/`graph.resume_node` ever mutates the workflow graph and before any subprocess spawns. It is a no-op — by construction, not by a team allowlist — whenever `overrides is None` or `overrides.cwd is None`: every team except `implementation`, `implementation_fix`, and `verification` builds its `WorkerOverrides` without a `cwd` at all (they run in the process's ambient working directory, which is always a valid checkout already), so planning/architecture/triage/research and the rest are entirely unaffected by this guard's existence. When `overrides.cwd` is set, the guard checks `alan.worktree.is_repo_checkout(overrides.cwd)` — `path.is_dir() and (path / ".git").exists()`, deliberately accepting both a real clone (`.git` directory) and a `git worktree add` checkout (`.git` file pointing at the main repo's gitdir) — and raises `UngroundedRunError` if it fails. `workflow_lifecycle`'s existing `except BaseException` finalizes the graph `failed` and re-raises unchanged; no new plumbing was needed there. Downstream, `Dispatcher._fail_genuine` routes the propagated `UngroundedRunError` exactly like any other genuine worker crash — into `_fail_workspace` (leaf workspace) or `_fail_recoverable_subtask` (DAG subtask), both landing at `AWAITING_INPUT` / `ParkReason.CRASH`.

### Audit trails

Each mechanism writes a positive, grep-able audit row to its own stream — so a trip is never something inferred from the *absence* of a success record — but the two streams are **not** the same file:

- `pr_precondition_failed` — written by `VerificationTeam._pr_precondition_failed_escalation` via verification's own `append_audit`, landing in `_audit_dir() / "verification_audit.jsonl"` (`ALAN_LOGS_ROOT`-relative, same file every other verification audit row uses). Keyed by `action` (not `event`); fields: `task_id`, `inv_id`, `action="pr_precondition_failed"`, `pr_number`, plus `last_pr_handover_id` / `last_pr_from_team` / `last_pr_from_invocation_id` when a prior PR reference was found in the chain.
- `ungrounded_cwd_blocked` — written by `_require_grounded_cwd` via `append_jsonl_audit(grounding_audit_path(), ...)`, landing in the dedicated `node_turns_audit_dir() / "grounding_audit.jsonl"` (also `ALAN_LOGS_ROOT`-relative; no new env var). Keyed by `event` (not `action`); fields: `event="ungrounded_cwd_blocked"`, `task_id`, `inv_id`, `team`, `role`, `node_id`, `cwd`, `reason`.

An operator debugging a precondition failure should grep `verification_audit.jsonl` for `action: pr_precondition_failed`; `grounding_audit.jsonl` only ever carries the structural-guard trip.

## `VerificationTeam`

Subclass of `StaticPipelineTeam`. `run()` invokes the diff_reviewer / scenario_runner / judge pipeline, then `_build_verdict_handover`. See "Fail-closed grounding guard" above for the PR precondition that runs before any of this and the grounded-checkout seam it hands off to.

1. `effective_verdict(judge_output)` parses `VERDICT: APPROVE|REQUEST_CHANGES` and `DIFF_FINDINGS_CRITICAL: <n>` and downgrades `APPROVE → REQUEST_CHANGES` when critical > 0.
2. `github_review_event(judge_output)` picks the GitHub review event independently of the routing verdict: an external-gate-only block (`REQUEST_CHANGES` + `DIFF_FINDINGS_CRITICAL: 0` + `## External Gate Status` marker) posts a non-sticky `--comment` when it names no actionable check, and a sticky `--request-changes` when it names at least one; otherwise the verdict maps to `--approve`/`--request-changes`. See `pr_iteration_pipeline.md` §D10a and §D27.
3. `_maybe_post_review` shells out to post the review and returns a tri-state: `"posted"` (success), `"skipped"` (no pr_number / missing VERDICT / dry-run), or `"failed"` (gh POST returned non-zero or raised).
4. **Inline→legacy fallback**: when CRITICAL findings are present, `_apply_pr_review` first attempts `gh api .../pulls/<n>/reviews` (inline comments). If that POST fails, it audits `review_failed_inline` + `review_inline_fallback`, then falls back to `gh pr review --request-changes` (PR-level body). Only when both fail does `_apply_pr_review` return `False`.
5. **Review POST failure routing**: when `_maybe_post_review` returns `"failed"`, `_emit_review_post_failed` is called — it emits a `review_post_failed` TeamEvent, writes a `review_post_failed` audit row with an actionable message naming `ALAN_VERIFIER_GH_TOKEN`, persists the judge output to the review artifact, and returns a handover with `to_team_suggestions=[]` (router yields `await_human`). Downstream does not treat the PR as reviewed.
6. **Judge VERDICT retry**: `VerificationTeam` overrides `_collect_outputs` to detect, before the workflow is finalized, either of two conditions via `_judge_retry_reason(workspace, judge_output) -> Literal["missing_verdict", "missing_in_scope_count"] | None` — the judge's output is missing a parseable `VERDICT:` line, or a `## IN-SCOPE ACCEPTANCE OUTCOMES` block is present in the prompt yet the judge omitted `DIFF_FINDINGS_CRITICAL_IN_SCOPE:` (PA3.1/PA-31b: previously this second case silently force-flipped an APPROVE to REQUEST_CHANGES via the raw-count backstop with no second attempt). When either fires, a single `judge_retry` node (same model, same system prompt) is appended to the live graph with a nudge added to the user text (`## RETRY NUDGE` block, constant `_JUDGE_RETRY_NUDGE` — renamed from `_JUDGE_VERDICT_MISSING_NUDGE` since the trigger is no longer verdict-only) asking for a COMPLETE re-emit of every mandatory line, consistent with the retry wholesale-replacing the prior judge output rather than patching one line. The diff_reviewer and scenario_runner outputs are reused from cache — no re-pay. The retry's output replaces the original judge output. If the retry emits a valid verdict (and, when a scope block is present, the in-scope count line) the run proceeds normally; if a `VERDICT:` line is still absent, `_emit_judge_parse_failed` returns a handover with `to_team_suggestions=[]` and a blocking `OpenQuestion` (id `verif-judge-parse`) naming the parse failure, so the workspace lands `AWAITING_INPUT` with an actionable reason instead of a generic void. Audit rows: `judge_verdict_missing_retry` (retry attempted; carries a `reason` field set to the triggering `_JudgeRetryReason`, action string unchanged for backward-compatible log queries), `judge_parse_failed` (retry also failed to produce a `VERDICT:` line). TeamEvent: `judge_parse_failed`. See `pr_iteration_pipeline.md` §D16 for the companion reviewer-count fallback and count-flip banner that also land on the verdict-assembly path.

`gh pr merge` is **not** called — manual merge stays for now.

The outgoing handover's `to_team_suggestions` is set as follows:

- `REQUEST_CHANGES` + review `"posted"` → `["implementation_fix"]` → router auto-iterates.
- `APPROVE` + review `"posted"` + pr_number → `["pr_merge"]` → gate-check CI + merge.
- `APPROVE` (or unparseable verdict) + review `"skipped"` → `[]` → router returns `await_human`.
- review `"failed"` (either path, any verdict) → `[]` via `_emit_review_post_failed` → router returns `await_human`.
- judge VERDICT still missing after `judge_retry` → `[]` via `_emit_judge_parse_failed` + blocking `OpenQuestion` → router returns `await_human`.

Audit rows emitted by the review POST path:
- `review_ok` — legacy `gh pr review` succeeded.
- `review_ok_inline` — inline `gh api` review succeeded.
- `review_failed` — legacy `gh pr review` returned non-zero or raised.
- `review_failed_inline` — inline `gh api` review returned non-zero.
- `review_inline_fallback` — inline failed; falling back to legacy (emitted before the legacy attempt).
- `review_post_failed` — both inline and legacy failed (or legacy-only failed on non-inline verdict); routing → `await_human`.
- `skip` — no pr_number / missing VERDICT / dry-run.
- `dry_run` — dry-run mode; `ALAN_VERIFICATION_DRY_RUN=1`.
- `judge_verdict_missing_retry` — retry node spawned; `reason` field is `"missing_verdict"` or `"missing_in_scope_count"`.
- `judge_parse_failed` — retry also produced no VERDICT; blocking open_question emitted.

- Live by default: `gh pr review` posts on every verification invocation with a parseable verdict. Export `ALAN_VERIFICATION_DRY_RUN=1` to opt back into log-only (the PR is unchanged, the judge verdict still lives in the workspace handover). Dry-run returns `"skipped"`, so routing is unchanged.
- Audit log: `~/.alan/logs/verification_audit.jsonl`.
- Verification carries `pr_number` forward on its outgoing handover.

## E2E coverage

`implementation-flow.spec.ts` exercises the full worktree flow end-to-end. Stub `claude` (see [e2e_testing.md](e2e_testing.md)) writes a marker file + commits inside `$PWD` (= worktree path). Playwright forces `ALAN_IMPL_DRY_RUN=1` + `ALAN_VERIFICATION_DRY_RUN=1` so no real GitHub mutation happens.

`verification-scenario-runner.spec.ts`'s scenario_runner spec now seeds a `pr_number` on its handover — with the PR precondition in place, a `pr_number=null` handover never reaches the scenario_runner stub at all, and the stub `gh` resolves a branch for any PR number so the precondition passes. The judge stub's "stub impl_fix" marker still drives an immediate APPROVE, which — now that a `pr_number` is present — routes through `pr_merge` (`ALAN_E2E_PR_MERGE_FLOW=1`, same `gh-pr-state.json`/`gh-checks-state` stub setup as `pr-iteration-happy-path.spec.ts`) to a `completed` terminal state instead of stalling at `awaiting_input`. The companion cannot_verify spec is unaffected: its `pr_number=999999999` still resolves a branch under the stub `gh`, so the precondition passes and the E2E-trigger-file bypass inside `_try_acquire_grounded_checkout` is what actually short-circuits it, unchanged.

`tests/test_grounding_guard_e2e.py` covers the two fail-closed mechanisms end-to-end at the Python level: zero `claude` spawns and a `pr_precondition_failed` audit row when `verification`/`implementation_fix` are dispatched without a resolvable PR, and both `Dispatcher._fail_genuine` branches (`_fail_workspace` for a leaf workspace, `_fail_recoverable_subtask` for a DAG subtask) landing at `AWAITING_INPUT` / `ParkReason.CRASH` with one `ungrounded_cwd_blocked` row when the structural seam guard trips.

## Import-linter layer

`worktree` slots into the import-linter layer stack between `queue` and `claude_cli`.
