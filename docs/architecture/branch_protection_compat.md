# Branch-protection compatibility — `alan/branch_protection_compat.py`

alan's `pr_merge`/`verification` choreography (`docs/architecture/pr_iteration_pipeline.md`,
`docs/architecture/merge_recovery.md`) makes several silent assumptions about a target
repo's GitHub settings: unresolved review threads must block merge visibly (not silently),
zero human approvals must be required (autonomous merge posts at most one verifier
review), squash-merge and delete-branch-on-merge must be enabled (`pr_merge` always calls
`gh pr merge --squash --delete-branch`), and the checks `merge_recovery` treats as
authoritative-and-blocking must actually be required by GitHub. This module makes those
assumptions explicit as a fixed 5-row `COMPATIBILITY_MATRIX` (`CompatibilityRow`: `key`,
`assumption`, `required_setting`, `remedy`) and a `compare_branch_protection(forge)`
function that reads the live settings via `gh api` and flags each mismatch with its
named remedy — a `Finding(check, status: pass|fail|unknown, message, remedy)`.

## Read path and credentials

`compare_branch_protection` resolves the SAME transport credential
`alan/teams/_pr_branch.py`'s push/PR machinery already uses
(`secrets_store.resolve_secret(forge.repo.slug, TRANSPORT_PURPOSE,
env_fallback=forge.credential_env_name)` — no new credential path), then makes two `gh
api` calls under a subprocess wrapper local to this module (`_gh_api_json`, not a new
HTTP client): `repos/{slug}` for repo-level settings (`allow_squash_merge`,
`delete_branch_on_merge`, `default_branch`), then
`repos/{slug}/branches/{default_branch}/protection` for the branch protection rule. This
is a single read per human-triggered preflight (`alan/engine_preflight.py`), so there is
deliberately no caching or rate-limit handling.

No credential resolvable → a single `status="unknown"` `Finding` and zero subprocess
calls — never a crash, never a silent "compatible". Reading branch protection requires
repo-admin; a 403 from EITHER `gh api` call whose stderr contains GitHub's specific
admin-rights error text (`_is_admin_scope_403` matches "403" AND "admin rights",
case-insensitively) collapses to one distinct `Finding(check="branch_protection_readable",
status="fail", message="cannot read branch protection — token lacks admin scope")` — this
is its own finding class, not conflated with "compatible" (a false pass) or an unhandled
exception (a crash). A 403 from another cause (SAML/SSO enforcement, rate limiting/abuse
protection) does NOT match that text and is deliberately NOT classified as an admin-scope
failure — it falls through to the generic path below with the true cause in the message,
since misclassifying it would suggest the wrong remedy. Any other non-zero `gh api`
outcome (that other-cause 403, network blip, rate limit, malformed JSON) collapses to
`status="unknown"` with a trimmed stderr excerpt in the message, so a transient or
unrecognized failure never reads as definitively either compatible, incompatible, or
specifically an admin-scope problem.

## The matrix diff

On a successful read, `_diff_against_matrix` walks the 5 `COMPATIBILITY_MATRIX` rows
(`conversation_resolution`, `required_approvals`, `squash_merge_enabled`,
`delete_branch_on_merge`, `required_checks`). Four rows go through a data-driven
`key -> extractor` dispatch table (`_ROW_EXTRACTORS`) that each produce one boolean,
turned into a `Finding` by `_row_finding` — `status="pass"` with empty `remedy`, or
`status="fail"` with the row's named `remedy`.

**`required_checks` is the one row routed to its own finding builder
(`_required_checks_finding`), not the generic dispatch table**, because it can report up
to three independent gaps in a single `Finding` rather than one pass/fail:

1. **Superset** (unchanged): `forge.authoritative_checks` (the set `merge_recovery`
   already treats as blocking — empty for the owner's engine since SonarCloud went
   advisory) must be a subset of the protection rule's required contexts, merging both
   the legacy `contexts` list and the newer `checks[].context` shape
   (`_required_check_contexts`).
2. **Per-check app pin** (P1.11, PD17): `forge.authoritative_check_apps`, filtered to the
   authoritative set by `_effective_check_apps` (a pin naming a non-authoritative check
   is silently ignored — never added as a required check, never checked for drift), names
   the GitHub App id each authoritative check must come from. The live shape read is
   `required_status_checks.checks[]`, whose `app_id` GitHub may report as `null`
   (`_live_check_app_pairs` treats `null`, a missing entry, and a different id all as
   drift for that check — never a false pass).
3. **Admin enforcement** (P1.11, PD17): when `forge.enforce_admins` is `True`, the live
   `enforce_admins` block must be a dict with `enabled: true`
   (`_admin_enforcement_enabled`) — a missing block, `{"enabled": false}`, or any
   non-dict shape are all drift.

Each failing part contributes its own fix text to the row's single `remedy`, joined with
`'; '`: the superset gap's text is exactly `row.remedy` (unchanged from before this
settings pair existed); each unsatisfied pin gets
`"Require '<check>' from GitHub App id <id> in the branch protection rule's required
status checks"`; a failed admin-enforcement check gets one fixed sentence naming "Do not
allow bypassing the above settings". **An engine that pins no check and leaves
`enforce_admins` off produces the exact same verdict, message and remedy as before this
settings pair existed** — `_required_checks_remedies` returns an empty list when there is
nothing to pin and the switch is off, so the row falls back to the single-gap shape
byte-for-byte. This also holds for every *other* row and for the matrix's row count
(still 5): an engine requesting neither setting is unaffected end to end.

Adding a new *boolean* assumption unrelated to `required_checks` still means adding one
`CompatibilityRow` + one extractor function + one dispatch-table entry, with no change to
`_diff_against_matrix` or `_finding_for_row` — which is what keeps every function under
the lizard CCN/length gate.

## Consumers and layering

This module has no route of its own and no mutable per-engine state — a pure
read-and-compare. It is consumed by `alan/engine_preflight.py`'s check #6 (R1.4) and by
`alan/github_repos.py`'s pre-engine token probe (`github_repos.md`), which imports
`_invoke_gh_api`/`_ADMIN_SCOPE_MESSAGE`/`_GhApiCallError` cross-module (the same private-import
convention `engine_preflight.py` uses for `engine_config._validate_before_register`). Placed
in `[tool.importlinter]` layers directly above
`secrets_store` (it imports `secrets_store.resolve_secret`/`TRANSPORT_PURPOSE` and
`engine_config.ForgeTarget`/`forge_target_for_workspace`) and below both `engine_preflight`
and `github_repos` (each imports from here — the A→B DAG edges are real imports, not just a
task-ordering artifact) and below `repo_scaffold` too (see below).

Note on the merge lane (2026-08-04, corrected 2026-09-05): `pr_merge` does NOT
consume this module — its required-context awareness comes from GitHub's own
`mergeStateStatus`, cross-checked by `_pr_merge_checks.confirm_ready_kind` before
every merge attempt (`pr_iteration_pipeline.md`). That is deliberate: the
`required_checks` row of `COMPATIBILITY_MATRIX` asserts only the *authoritative*
subset (SonarCloud), while the branch ruleset also requires contexts outside it
(the E2E check, `copilot_code_review`), and the protection API read needs admin
scope that can 403 mid-merge.

`mergeStateStatus` reports `CLEAN` for a commit whose checks have **not been
created yet** — the 2026-09-05 incident that prompted `_pr_ready.results_absent`
falsified the prior claim here that it "folds all required contexts into one
unprivileged signal." It distinguishes "a required context failed or is pending"
from "mergeable," but it does **not** distinguish "no results exist for this
commit" from "all results are good": a check-run freshly reset by `gh run rerun`,
or a commit whose checks simply have not been scheduled yet, can both read
`mergeStateStatus=CLEAN` with an empty `statusCheckRollup`. Absence is therefore
judged as its own state, separately from the merge state, at two points:
`_pr_ready.results_absent(readiness)` (`check_count == 0` and all three bucketed
check-name tuples empty — the raw rollup entry count is the discriminator, not
bucketed emptiness, so a skip-green docs-only PR with only `SKIPPED`/`NEUTRAL`
rollup entries is never misread as absent) demotes both the readiness judgement
(`PrReadiness.failed_reasons` gains `"no check results"`) and the merge lane's own
`_pr_merge_checks.confirm_ready_kind` (which demotes an apparently-ready poll back
to `"pending"` rather than letting it reach `gh pr merge`). The merge is additionally
held by `merge_recovery.MergeBlockClass.NO_CHECK_RESULTS` — the last rung of
`_classify_block_head`, below `DIRTY`/`STALE_MEASURED_BASELINE` — rather than by the
merge state itself, since `mergeStateStatus` never reports anything but `CLEAN` for
this shape.

## Write side — `apply_branch_protection` / `ApplyOutcome`

`apply_branch_protection(forge) -> tuple[ApplyOutcome, ...]` is the write-side
counterpart to `compare_branch_protection`, added directly alongside it: one
`ApplyOutcome(key, status: applied|deferred, finding: Finding | None)` per
`COMPATIBILITY_MATRIX` row, in row order. It resolves the same transport credential
`compare_branch_protection` does, then issues exactly two live `gh api` writes over a
JSON body (`gh api <path> -X PATCH|PUT --input -`, never `-f` flags, so nested
boolean/object values round-trip): `PATCH repos/{slug}` sets
`allow_squash_merge`/`delete_branch_on_merge` (covers the `squash_merge_enabled` and
`delete_branch_on_merge` rows), and `PUT repos/{slug}/branches/{default_branch}/protection`
sets `required_conversation_resolution`, `required_pull_request_reviews.
required_approving_review_count = 0` (covers `conversation_resolution` and
`required_approvals`), and `required_status_checks` (covers `required_checks`).

**`required_status_checks` (P1.11, PD17).** With no *effective* pin
(`_effective_check_apps(forge)` empty — either `authoritative_check_apps` is empty, or
every pin it carries names a non-authoritative check), the block is byte-for-byte what it
always was: `{"strict": false, "contexts": sorted(forge.authoritative_checks)}`. With any
effective pin, the block switches to `checks[]` instead of `contexts` — never both keys at
once, since GitHub treats them as alternatives — sorted by context: a pinned check is
`{"context": "<check>", "app_id": <id>}`, an unpinned one is `{"context": "<check>"}`
alone. An unpinned check is never widened to GitHub's `-1` ("any app") sentinel; omitting
`app_id` is GitHub's own "the app that most recently provided this check" default, the
same behaviour the plain `contexts` list already has today. `enforce_admins` in the same
PUT body is `true` when `forge.enforce_admins` is on, `null` (GitHub's own disabled
default, unchanged from before) when off — the body never sends `false`.

Re-applying protection on the owner's repositories with these two settings live is still
The owner's step (P3.4) — merging this changes nothing live on its own.

**Never raises past its own boundary, mirroring `compare_branch_protection`.** No
credential → every row deferred with `status="unknown"`. An admin-scope 403 reading
`repos/{slug}` → every row deferred with `status="fail"` and
`Finding.message == _ADMIN_SCOPE_MESSAGE`. Either write call failing (non-zero `gh api`
exit) → only the rows that write covers deferred with `status="fail"`, message naming
the row's `required_setting`. Every deferred row's `Finding` carries its
`CompatibilityRow.remedy` text **verbatim** (the same remedy prose
`compare_branch_protection` would show for that row) plus a `remedy_url` set to
`https://github.com/{slug}/settings/branches` — the settings page a human would need to
finish the row by hand. A row that DID apply carries no `Finding` at all
(`ApplyOutcome(status="applied", finding=None)`).

`Finding` itself gained one new optional field for this, `remedy_url: str = ""`
(default-backward-compatible — every existing `Finding(...)` call site across
`engine_preflight.py`/`github_repos.py` is unaffected) — the mechanism the onboarding
checklist (`repo_scaffold.md`) uses to make a deferred protection row an actionable link
rather than just remedy prose.

`alan.repo_scaffold`'s `protect(cfg)` is the only caller today: it persists which rows
came back `applied` versus `deferred` into `repo_onboarding.json`, and folds every
deferred row's `Finding` straight into the onboarding readiness checklist alongside the
manual boundary steps — see `repo_scaffold.md` for the full two-phase story and the
per-stack `AUTHORITATIVE_CHECKS` identity that `required_status_checks.contexts` reads.
