# SonarCloud (advisory)

SonarCloud is advisory: it runs and reports on pushes and same-repo PRs, but never blocks a merge (extended to the owner's engine on 2026-09-28 after the analysis began failing on the plan's lines-of-code limit). Three places enforce that, and all three must stay in step:

- **CI:** the `sonar` job, `SonarCloud (advisory)`, is skipped for fork PRs (no `SONAR_TOKEN`), Dependabot runs, and runs where `test-backend`, `test-frontend` and `lint-frontend` all failed to succeed (no coverage to upload); when it runs, it runs the scan step with `continue-on-error: true` and without `sonar.qualitygate.wait`, so neither a rejected analysis nor a failed quality gate turns the job red.
- **Branch protection:** neither `SonarCloud (advisory)` nor `SonarCloud Code Analysis` is a required status check on `main`.
- **Merge gate:** the owner's engine profile has empty `forge.authoritative_checks`, so `resolve_check_outcome` reads the Sonar check as `ABSENT` and `merge_recovery`'s `sonar_*` block classes never fire for it. They remain for any engine that lists a Sonar check as authoritative. A red Sonar check no longer costs a fix cycle at the three merge-lane decisions either — see "Advisory check failures" below.

The quality gate configured in the SonarCloud UI (new-code coverage 100% lines / ≥ 95% branches) still shows on the analysis for review. The `sonar` CI job deliberately does not `need` `e2e-frontend` — it only consumes the two coverage artifacts, so it runs in parallel with E2E.

## Why advisory, and optional on copies

A 2026-09-30 audit of two months of alan's own fix runs found:

- SonarCloud findings reached 15% of `implementation_fix` runs, and 95% of them were cleared on the next push.
- They were mostly maintainability rules that lizard, xenon and ESLint already bound, plus a false-positive family: `dataclasses.replace` read as `DataclassInstance`, while pyright strict passed.
- While Sonar blocked merges, 25 fix cycles existed only because its check was red.

The decision informed by that audit keeps SonarCloud as an optional, advisory signal that nothing depends on:

- Merges ignore it ("Advisory check failures" below).
- The fix loop's wording follows whether the engine makes Sonar authoritative ("Surfacing findings on PRs" below).
- Connecting SonarCloud on a copy is an opt-in setup step (roadmap item P1.9d): a copy is ready, dispatches and passes its `gate` check without it, and setup can skip it ("How a copy's SonarCloud is configured" below).

## Advisory check failures

`_pr_ready`'s rollup bucketing and `pr_merge`'s `classify_checks` read every check whose conclusion is `FAILURE`/`ERROR`/`TIMED_OUT` as failing, and a completed analysis that the SonarCloud app posts as failing (the plan's lines-of-code limit, or a failed quality gate) is one of them. Left alone, each such red cost a fix cycle at three decision points: verification's external-gate downgrade, the dispatcher's merge gate (`merge_gate.attempt_recovery`, also reached from the answer route and manual dispatch), and the merge team's own check classification. All three now share one definition of "advisory", implemented once in `alan/teams/_pr_ready.py` (`advisory_failing_checks`).

**Definition.** A failing check is advisory only when both hold:

- the app that posted it is positively identified from the head commit's REST check-run listing (`GET repos/{slug}/commits/{sha}/check-runs`, the one 100-item page `_pr_branch._iter_check_runs(strict=True)` already reads) — every listed run with that name carries a non-empty `app.slug` that is not `github-actions`;
- it is not named in the engine's `forge.authoritative_checks`.

Everything else is blocking (fail-closed): an unknown or missing app, a check absent from the listing (including one past the first 100 runs), a listing read failure (audited as `advisory_check_runs_query_failed`), a missing head SHA, and any commit status context — `PrReadiness.failing_status_contexts` carries the failing rollup items that have a `context` and no `name`, so a check run and a legacy status sharing a name cannot be told apart by the name-only join and the name reads as blocking. GitHub's own "required" flag is deliberately not consulted: a red required third-party check is left to GitHub's merge refusal (`gh pr merge` is never called with `--admin`), and neither install has one.

The listing is read only when something is red, and only when every red check could still be advisory (a red authoritative check or status context short-circuits before the read). A PR with no red check makes no read it did not make before.

**All-or-nothing.** `advisory_failing_checks` returns the advisory checks only when every red check that is not quarantined is advisory; otherwise it returns nothing and the lane sees today's readiness object unchanged. A Sonar red next to any blocking red therefore shows everywhere exactly as before — the downgrade text, the actionable-check list, the unblock recipe. An allowlisted flaky red next to an advisory red still gets no rerun (`flaky_only_failing` needs every red to be allowlisted) and is downgraded. A quarantined red (pre-existing on the base, or a known flake) does not veto the advisory checks; it stays in the view.

**How each decision point uses it.** Filtering happens at the three decisions, never in the shared lists: `check_pr_merge_readiness`, `check_pr_verifier_blockers` and `_bucketed_check_names_from_rollup` keep returning every failing check. Each decision derives a lane-local copy (`dataclasses.replace`) that drops the advisory names from `failing_checks`/`quarantined_checks` and rebuilds `failed_reasons` with the lane's own reason builder.

- **Verification** (`_verification_gates._verifier_readiness`): both verifier readiness reads — the initial one in `_resolve_gate_readiness` and each poll in `_settle_wait` — go through `verifier_lane_without_advisory`. An approving verdict is not downgraded when the only red checks are advisory, and such a PR waits for pending checks exactly as a PR with no red check (the pending wait is skipped only when `failed_reasons` is non-empty). A conflicting (`DIRTY`) PR is still downgraded.
- **Merge gate** (`merge_gate._attempt_recovery_for_readiness`): `merge_lane_without_advisory` is the first statement, before `readiness_after_freshness_check` and `readiness_after_closing_links_check` — both skip when `failed_reasons` is non-empty, so applying the view later would diverge from the all-green path. `merge_recovery.classify_block` never sees an advisory-only red; the `gate_path`/park records downstream of the view carry the filtered readiness while `advisory_check_failures_ignored` names what was ignored.
- **Merge team** (`_pr_merge_checks.resolve_checks_kind`): only when `classify_checks` returns `failed` (which it does after the pending test, so the pending path and today's tolerance for pending advisory checks are untouched). The merge team reads readiness through `readiness_or_unavailable` (a transient forge error keeps the `failed` verdict rather than newly raising), requires every failing local check to be in the advisory set, marks those entries `SUCCESS` (never deleting them), reclassifies, and continues through the unchanged `confirm_ready_kind` and `_apply_ownership_gate`. It still refuses on any red blocking check, unresolved review threads, CODEOWNERS ownership and `DIRTY`. `alan/teams/pr_merge.py` is not edited.

**Merge state.** GitHub reports `UNSTABLE` for a red non-required check and `BLOCKED` for a red required one, and a Sonar-only red was observed reading `UNSTABLE` once and `BLOCKED` twice — the merge state cannot tell an advisory red from a required one. The view therefore rewrites `UNSTABLE` to `CLEAN` only when nothing is pending and no red remains after the removal; `BLOCKED` and `DIRTY` are always kept (`BLOCKED` cannot be attributed to the advisory check, and `DIRTY` must still downgrade and reconcile), so a `BLOCKED` advisory-only PR is classified as `blocked_no_signal` exactly as the same PR with green checks.

**Head-SHA pinning.** The listing is read for the `headRefOid` of the same `gh pr view` payload that named the red checks, so the identities and the red names describe one commit. Nothing is cached between reads. The merge itself is `gh pr merge --match-head-commit <sha>` on the head the merge team just confirmed, so a push between classification and merge is refused by the forge.

**Audit rows** (`impl_audit.jsonl`): every advisory-only skip writes `advisory_check_failures_ignored` with `lane` (`verification`, `merge_gate` or `pr_merge`), `task_id`, `pr_number`, `head_sha` and `ignored_checks`, with no dedup state; a listing read failure writes `advisory_check_runs_query_failed` with `task_id`, `pr_number` and `reason`.

**The fix loop's gate block filters per check.** `implementation_fix` lists red checks under "Failing required checks" and tells the fixer to make each one pass. Before rendering, `_fix_gate_checks.without_advisory` drops every check that `_pr_ready.third_party_advisory_checks` names. That applies to both sources, the verifier's gate block and the live readiness read. `third_party_advisory_checks` uses the same definition of advisory as above, but it is **not** all-or-nothing: an advisory Sonar red is dropped even when a blocking red sits beside it. Otherwise that block would order a fix for the same check the SonarCloud findings block calls advisory. Any failed read keeps every check, which is fail-closed.

**Deliberately unfiltered readers.** The clause audit, the unblock recipes, the triage pregate, `_pr_merge_no_signal` and the merge team's escalation context (`concurrent_blockers`) keep reading the shared failing lists, so they see exactly the checks they saw before. Advisory jobs that GitHub Actions itself posts (the `SonarCloud (advisory)` CI job, the coverage comment) stay blocking, because they are Actions-posted.

**Per engine.** The owner's engine declares no `authoritative_checks`, so the filter is active there and a red `SonarCloud Code Analysis` no longer stops any of the three decisions. A public copy's profile (`alan/default_profile_writer.py`) makes `gate` authoritative, so `gate` stays blocking while Sonar is advisory. An engine that lists a Sonar check as authoritative keeps it blocking and keeps the `sonar_*` recovery rungs in `merge_recovery.py`.

## Custom quality profiles (taint engine disabled)

The project is assigned custom quality profiles `Sonar AI Typescript` (TS) and `Sonar AI javascript` (JS) with every rule from the `tssecurity`/`jssecurity` taint-rule repositories deactivated (verified 2026-07-04: `JsSecuritySensorV2` 13m10s → 31ms, sonar job 46s). The SonarJasmin taint engine runs its whole-frontend scan (all web-src files) if EITHER language's assigned profile has ANY active `tssecurity:*`/`jssecurity:*` rule — deactivating one language alone changes nothing, and a freshly copied profile starts with all 49 active again. Find/bulk-deactivate them via the Rules page filtered by `repositories=tssecurity` (resp. `jssecurity`) + the profile's Active facet. Being custom profiles, they do NOT auto-track new Sonar way rules — rule updates must be adopted manually.

## Surfacing findings on PRs

Per-line rule details live on the `SonarCloud Code Analysis` check-run annotations, not the Sonar API. `_pr_sonar.list_sonarcloud_annotations(pr_number)` fetches them, and `implementation_fix` surfaces them as `## SonarCloud findings` through `_sonar_fix_block`. The block's wording depends on the engine:

- **Advisory** (the default): the check is not in the engine's `forge.authoritative_checks`, as on the owner's engine and on copies. The block says the findings never block the merge and rank below the judge review, PR threads and failing required checks. The fixer fixes a finding only when it is a real defect or a small in-scope change; otherwise it names it in its summary with a one-line reason. A security finding (injection, path traversal, request forgery, an unsafe regular expression, logging user-controlled data, or any reported vulnerability) is always either fixed or declined with the specific reason it is safe. It never adds a suppression (`NOSONAR`, an issue acceptance, a Sonar exclusion).
- **Authoritative** (`SonarCloud Code Analysis` is listed): the block keeps the must-fix wording, so a PR blocked solely on a Sonar rule self-resolves on the next push. Only that exact check name (`_pr_sonar.SONAR_CHECK_NAME`) counts. An engine that lists some other Sonar-named check gets the advisory wording.

See `pr_iteration_pipeline.md`.

## How a copy's SonarCloud is configured

SonarCloud is optional on a copy. A copy that skips it is complete: its profile lists only `reviewer_credential` as a requirement (`code_quality` is no longer a default one, so readiness never waits for SonarCloud), setup reports "SonarCloud: not connected (optional)" as passing, and the CI overlay's `sonar` job ends without an analysis and without an error while `SONAR_TOKEN` or either variable is blank, so `gate` passes. The owner's install is unchanged: its profile declares no requirements and its own CI workflow is untouched.

A copy that connects it is set up by `scripts/setup_macos.sh`'s `sonar` step (`docs/architecture/setup.md`, "SonarCloud"), not by hand. The step's first prompt asks for the admin token and says SonarCloud is optional: pressing Enter (or end of input) skips it, and `--no-sonar` skips it without prompting. A skip records no project and sets no repository variable and no secret; running the step again later connects it. On an install that is already connected the step reports "already configured" and asks nothing, and `--no-sonar` changes nothing there. It finds the one SonarCloud project bound to the copy's repository through an admin token, and writes the same organization and project key three ways: the installed profile's `code_quality` block, the repository variables `SONAR_ORGANIZATION` and `SONAR_PROJECT_KEY`, and, from a second (analysis) token, the repository secret `SONAR_TOKEN`; the CI overlay's `sonar` job reads exactly those names. The analysis token must come from a different SonarCloud user than the admin token unless the user passes `--accept-sonar-user-token`; a token equal to the admin token is always refused. Setup only reads SonarCloud's Automatic Analysis setting and prints a browser instruction when it is not confirmed off.

It never blocks a merge, for the same reason as the owner's install (PD15): the copy's profile makes only `gate` authoritative, so the Sonar check is read as advisory, and setup neither requires the Sonar check on the branch nor lists it as authoritative. Whether SonarCloud reports a branch analysis on `main` at all is proven only by the P3.7 rehearsal; if it does not, setup stays incomplete and never reports a false complete.

Setup then proves the configuration once: the `trigger` step dispatches one `tests.yml` run on `main` (connecting SonarCloud discards a run recorded before, so the run that is waited for includes the analysis), and the `latch` step waits for that run's `gate` job to succeed and, only when a SonarCloud project is recorded, for SonarCloud's own app (`app.slug` `sonarqubecloud`) to post a check run or commit status on the run's head commit after the run was created. Only then does it write `~/.alan/engines/code_quality.latch` with the profile's organization and project key, which is what clears the `code_quality` readiness requirement on a profile that lists it. A connected SonarCloud that posts nothing within the wait is a warning on a profile that does not list `code_quality`: setup still ends complete with exit 0 and says SonarCloud has not reported. A profile that lists `code_quality` (an older one, or one edited by hand) keeps the strict behaviour: it is not ready until SonarCloud reports for its project, and setup stays incomplete. A Sonar-named check posted by `github-actions` and anything created before the trigger never count. SonarCloud stays advisory throughout: the latch only records that it reported, the final report reads `dispatch_readiness` and treats a warning as a pass, and a red Sonar job on a private repository is explained (SonarCloud's Free plan does not analyse private projects) but is not something setup can fix. See `docs/architecture/setup.md`, "The CI trigger and the latch".

## Which check run is the Sonar check

`_pr_sonar` picks the analysis check run through one selector, `_sonar_runs`, used by both `list_sonarcloud_annotations` and `retrigger_sonar_analysis`, in two passes over the head commit's check-run listing:

1. **Pass 1**: every run whose name is exactly `SonarCloud Code Analysis`, except a run whose `app.slug` is `github-actions`. A run with no `app` at all still matches by name. This is the owner's check and it is what both functions used before the fallback, so the owner's picks are unchanged.
2. **Pass 2**: only when pass 1 found no run at all (not merely no run with annotations or a suite), every run whose `app.slug` is `sonarqubecloud`, under any name. A copy's SonarCloud app can post its analysis under a different name.

A `github-actions` run is never picked, whatever its name: an Actions job named like the check would otherwise be mistaken for the app's analysis. The callers keep their own rule for choosing among the selected runs (the first with annotations, respectively the first with a check suite id).
