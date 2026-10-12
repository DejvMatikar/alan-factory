# Repo scaffold & protect — `alan/repo_scaffold.py` + onboarding endpoints

`alan/repo_scaffold.py` is the two-phase engine-ops onboarding backend behind the
"onboard" action: it takes a bare existing GitHub repository whose engine is already
registered (repo identity, forge credential, stack preset) and drives it to
first-workspace-dispatchable, doing everything the engine's own forge credential can
do through the GitHub API, and naming everything it can't as a checklist item.

## Phase 1 — scaffold (`scaffold`)

`scaffold(cfg, *, project_title, project_doc) -> OnboardingState` opens exactly one
pull request on the target repo (`cfg.repo.slug`), using the engine's OWN forge
credential (`secrets_store.transport_env`/`resolve_secret` against
`ForgeTarget.from_forge_config(cfg.forge)` — never alan's own checkout or credentials)
containing three stack-matched artifacts:

1. `.github/workflows/ci.yml` — the stack's CI workflow, whose single job is named
   after the stack's authoritative check (see "Identity" below).
2. `.github/dependabot.yml` — the stack's dependency-update configuration.
3. `CONVENTIONS.md` — a conventions document assembled **mechanically**, never by a
   language model: the stack's `conventions.md.template` (a `str.format` shell with
   one `{project_title}` placeholder) followed by a literal `## Project context`
   section holding the project's own doc text verbatim. `project_title`/`project_doc`
   arrive as plain strings from the caller — `repo_scaffold` never imports
   `alan.project` or `alan.project_doc`; `alan/routes/engines.py`'s
   `_project_context_for` resolves the project that has `engine_key == cfg.repo.slug`
   (scanning `project.list_projects()`) and reads its `project_doc.doc_path(...)` text,
   defaulting to `("", "")` when no project is bound yet or its doc is unreadable.

The three artifacts are the stack's own template files under `alan/scaffold_templates/`
(pure data — no Jinja, no LLM). `scaffold` clones the target repo into a scratch
`tempfile.TemporaryDirectory`, checks out `alan/repo-scaffold`, writes the three files,
commits, pushes via `alan.teams._pr_push.push_branch` (the same push primitive
`implementation`/`pr_merge` use — at the time this doc was written it lived in
`alan.teams._pr_branch`; the H5.1 shrink program's push-reconcile-cluster move
relocated it to `alan.teams._pr_push`, so that no longer holds), and opens the PR
with a bare `gh pr create --head` (no `--base` — the target repo's default branch,
not an assumed `main`).

**Idempotent, opens no duplicate PR.** Before doing any of the above, `scaffold`
checks two things in order: the persisted `OnboardingState` (below) for an already-known
`scaffold_pr_number`, and — for the case that local state was lost — a live
`gh pr list --head alan/repo-scaffold` lookup. Either hit short-circuits straight to
returning the current state (or adopting the discovered PR) with no new commit, push,
or PR. `_union_authoritative_checks` (see "Identity" below) only runs on the
first-PR-opening path — never on an idempotent re-run — since it stomping a
since-customized `authoritative_checks` set on every poll would be a footgun.

`ScaffoldError` (a `RuntimeError`) is the one way this module raises past its own
boundary: no forge configured, no transport credential, or any of the git/gh
subprocess steps failing. `alan/routes/engines.py`'s `POST .../scaffold` translates it
to `422`. This is deliberately a hard failure, not a checklist Finding — unlike
`protect` below, a scaffold precondition failure (no credential at all, an unreachable
repo) isn't the "alan lacks a specific GitHub permission" shape this feature exists to
handle gracefully; it means the action cannot do anything yet.

## Identity — the one shared value per stack

`alan/scaffold_templates/AUTHORITATIVE_CHECKS: dict[str, frozenset[str]]` (one entry per
stack: `python`, `node`, `dotnet`, `ios`) is the **single source** for the value that has
to be identical in four places, or a merge silently stops being gated on the CI that's
actually running:

1. The stack's `ci.yml` job `name:` (e.g. `python-ci`) — the string GitHub's Checks API
   reports as the check-run name.
2. `AUTHORITATIVE_CHECKS[stack]` itself.
3. `EngineConfig.forge.authoritative_checks` after `scaffold` runs —
   `_union_authoritative_checks` unions `AUTHORITATIVE_CHECKS[stack]` into whatever the
   engine already had and persists via `engine_config.update_and_persist`.
4. The `required_status_checks.contexts` branch-protection payload `protect` (below)
   sends to GitHub, and `branch_protection_compat.compare_branch_protection`'s
   `required_checks` row reads back — both go through `forge.authoritative_checks`, so
   (3) is what actually makes (4) meaningful; before `scaffold` runs, a fresh preset's
   `authoritative_checks` is empty and the `required_checks` row passes trivially.

`tests/test_scaffold_identity.py` proves this chain end to end per stack.

The `ios` stack's `conventions.md.template` is the first template in this tree whose
content collides with the `str.format` placeholder syntax `_render_conventions`
(`alan/repo_scaffold.py`) uses to fill in `{project_title}`: its module map is written
with brace-delimited path shorthand (e.g. `App/Sources/{{AppMain,AppFeature,AppRoute}}.swift`
in the raw template file), so every brace other than the placeholder itself must be
doubled to survive rendering. `tests/test_scaffold_templates.py` asserts the rendered,
single-brace form of that structure, not just that rendering succeeds — a template that
avoids the crash by deleting the module map instead of escaping it fails the same as one
that never renders.

## Phase 2 — protect (`protect`)

`protect(cfg) -> OnboardingState` calls
`branch_protection_compat.apply_branch_protection(forge)` (the write-side counterpart
to `compare_branch_protection`, added to the same module) and persists which
`COMPATIBILITY_MATRIX` rows it could apply versus which it couldn't.
`apply_branch_protection` never raises past its own boundary: no credential, an
admin-scope 403, or a failed `gh api` write all collapse to `ApplyOutcome(status="deferred",
finding=Finding(...))` for the affected row(s) — using that row's `CompatibilityRow.remedy`
text **verbatim** and a `remedy_url` pointing at
`https://github.com/{slug}/settings/branches` — never a hard action failure. A
succeeding write yields `ApplyOutcome(status="applied")` (no `finding`) for that row.
Two live `gh api` calls do the actual writing: `PATCH repos/{slug}` (squash-merge +
delete-branch-on-merge) and `PUT repos/{slug}/branches/{default_branch}/protection`
(conversation-resolution, zero required approvals, and `required_status_checks.contexts
= sorted(forge.authoritative_checks)`) — both via a JSON body over `gh api --input -`,
never `-f` flags, so nested boolean/object values round-trip correctly.

`protect` never raises for a GitHub-permission-shaped failure — only `_require_forge`'s
"no forge configured at all" precondition (mirroring `scaffold`) surfaces as
`ScaffoldError` / `422`, since that isn't a permission gap this feature is meant to paper
over.

## The readiness checklist (`build_checklist`, `GET .../onboarding`)

`build_checklist(cfg, state) -> tuple[Finding, ...]` always returns, in order, four
named findings the onboarding UI can never see fewer than, plus every deferred
protection row from the last `protect` call:

1. `push_repository` — `git ls-remote <repo> HEAD` under the transport credential
   (mirrors `engine_preflight`'s `repo_clonable` check; reimplemented locally here
   rather than imported, since `alan.engine_preflight` sits ABOVE `alan.repo_scaffold`
   in the `[tool.importlinter]` layers — this module cannot import it).
2. `mint_credentials` — both the transport secret and a `REVIEWER_PURPOSE` secret
   resolve for this repo slug.
3. `workflow_write` — whether the stored transport credential can perform the one
   write `scaffold` always makes: a CI workflow file under `.github/workflows/`.
   GitHub refuses workflow-file pushes from a classic personal access token that
   lacks the `workflow` scope — the default shape of an ordinary GitHub PAT, not an
   edge case — so this check exists to say so before the user clicks the button that
   needs it, rather than after the push fails. `_check_workflow_write_capability`
   (`alan/repo_scaffold.py`) calls `github_repos.probe_workflow_scope(token)`, which
   runs `gh api -i user` (the `-i` flag dumps response headers) and reads the
   `X-OAuth-Scopes` header GitHub returns for classic-PAT-authenticated requests,
   returning one of three outcomes the check maps to a `Finding`:
   - `"present"` (the header lists `workflow`) → `pass`.
   - `"absent"` (the header is present but omits `workflow`) → `fail`, naming the
     cause and the remedy (regenerate the token with the `workflow` checkbox
     ticked, save it again under the same repository and purpose).
   - `"unverifiable"` (the `gh api` call raised, returned non-zero, or — the
     accepted gap — the header is simply absent, which is what GitHub does for a
     **fine-grained** token; there is no equivalent cheap read for a fine-grained
     token's scopes) → `unknown`, with a forward-looking remedy naming the
     fine-grained token's own permissions screen. A **definitive** probe for a
     fine-grained token is deliberately not attempted: the only way to learn
     whether one can write workflow files is to try writing one, and this is a
     read-only readiness check — it must not touch the user's repository unbidden.
   No transport credential at all → `unknown` (never `fail`), mirroring
   `_check_push_repo`'s own precedent for the same state: a `fail` here would
   flip `overall_status` from `unknown` to `fail` for every freshly-registered,
   credential-less engine and would duplicate the blocker `mint_credentials`
   already owns. The probe is uncached — every `GET .../onboarding` read costs one
   more `gh api` subprocess, the same cost `push_repository` and
   `review_and_merge_scaffold_pr` already pay per read.
4. `grant_reviewer_collaborator_access` — always `status="unknown"`: alan has no cheap
   API read for "is this login a collaborator with push access," so this step is named
   but never live-verified.
5. `review_and_merge_scaffold_pr` — `fail` naming "scaffold has not run yet" when
   `state.scaffold_pr_number is None`; otherwise a live `gh pr view --json state`, `pass`
   only once the PR's state is `MERGED`.

When the scaffold push itself is rejected, `_open_or_find_scaffold_pr` raises
`ScaffoldError(f"git push failed: {reason}")` where `reason` is
`push_result.detail or "no further detail from git"` — `PushResult.detail`
(`alan/teams/_pr_push.py`) carries every `remote:`-prefixed line from the failed
push's stderr, joined and truncated to 400 characters (cut back to the last
whitespace before the limit, so GitHub's own sentence is never cut mid-word). This
is the forge's own stated reason — the same text `git push` prints to the user's
terminal — surfacing through the API's `422` detail instead of the bare
`PushOutcome` enum name a caller would otherwise see. The plain-sentence fallback
applies only when the stderr had no `remote:` lines to extract from (an exception
path, or a failure with no server-side message at all) — no internal enum or type
name ever reaches user-facing text, on either path. This 400-char user-facing
truncation is deliberately separate from `append_audit`'s own 300-char stderr cut
in the same code path — one is sized for a human reading a UI banner, the other
for a compact audit-log line — and a future edit to one must not be assumed to
cover the other.

`overall_status(checklist)` is the same worst-of-three (`fail` ≻ `unknown` ≻ `pass`)
shape `engine_preflight._worst` uses, independently defined here for the same
layering reason as (1) above.

Once the scaffold PR is merged and the four checklist items plus every protection row
`protect` applied are satisfied, `alan.engine_preflight.run_preflight`'s own six checks
(author/reviewer credentials, repo clonability, tool reachability, flow-graph validity,
and `compare_branch_protection`) report `overall == "pass"` with **no code path shared
between the two modules** — they are wired together purely through the live GitHub state
`protect`/`scaffold` write and `compare_branch_protection`/`run_preflight` read back, the
same way `pr_merge` and `merge_recovery` are wired to branch protection today.

## State — `repo_onboarding.json`

`OnboardingState` (`slug`, `stack`, `scaffold_pr_number`, `scaffold_pr_url`,
`authoritative_checks`, `protection_applied_keys`, `protection_deferred`) persists as
`repo_onboarding.json` under the SAME per-forge state directory
(`alan.teams._pr_state_paths._engine_state_dir(forge)`) `engine_preflight.json` and the
`pr_*.json` writers already use — atomic temp-file + `os.replace`, best-effort (an
`OSError` is swallowed the same way `engine_preflight._persist` swallows one; a disk
problem must not turn a successful scaffold/protect call into a 500). No new persistent
store, no new `ALAN_*` environment variable.

## Routes (`alan/routes/engines.py`)

`POST /api/engines/{owner}/{repo}/scaffold` and `POST .../protect` are
control-token-gated (`require_control_token`), run their `repo_scaffold` call in a
threadpool, translate `ScaffoldError` to `422`, and publish `TeamEvent("engine_scaffolded",
...)` / `TeamEvent("engine_protected", ...)` on the global pubsub —
`alan/routes/events.py`'s `_ENGINE_KINDS` maps both to the `"engines"` SSE kind
alongside `preflight_completed`/`dep_sweep_changed` (see `events.md`).
`GET .../onboarding` is ungated (read-only, mirrors `GET .../preflight`) and returns
`OnboardingReadiness{state, checklist, overall}` — `state` and `checklist` reused
as-is from `repo_scaffold`, `overall` from `repo_scaffold.overall_status`.

## Layering

`alan.repo_scaffold` sits directly below `engine_presets` and above `teams` in
`[tool.importlinter]`'s layers list — it imports `alan.process_runner`
(`run_subprocess`; X2.7 PR22 moved it there from `alan.teams._pr_branch`, a public
stdlib-only leaf appended last to the layers list), `alan.teams._pr_state_paths`
(`_engine_state_dir`), `alan.teams._pr_push`
(`push_branch`/`PushOutcome`; at the time this doc was written both lived on
`alan.teams._pr_branch` alongside `run_subprocess`, but the H5.1 shrink program's
push-reconcile-cluster move relocated them to `alan.teams._pr_push`, so that no
longer holds), `alan.branch_protection_compat` (`Finding`/`ApplyOutcome`/
`apply_branch_protection`), and `alan.github_repos` (`probe_workflow_scope`, for
the `workflow_write` check above), all below it — `alan.teams` remains one atomic
import-linter layer, so this move needed no `[tool.importlinter]` edit — and is in
turn imported by `alan.routes.engines` (routes layer) — never the reverse;
`alan.engine_preflight` sits above it too and could import it, but does not (see
"readiness checklist" above for why). `alan.scaffold_templates` (pure data, zero
`alan`-internal imports) sits directly below `repo_scaffold`, the only module that
reads it.
