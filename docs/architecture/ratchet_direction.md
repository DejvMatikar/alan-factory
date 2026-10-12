# Ratchet direction observation

**Source:** `alan/teams/_ratchet_direction.py`, `alan/teams/_review_context.py`, `alan/teams/_pr_freshness.py`.

## Why this exists

Twice, a fleet worker widened a tightening-only ratchet baseline to turn a failing check green, and the verification pass approved with zero critical findings: `3a4cf784` (merged to `main` 2026-08-07, adding a spec to `alan/web/e2e/flaky-baseline.json`, citing the documented last-resort accurately) sat on `main` for four days until `#1047` emptied the file again; and `a4b7d8bd` on `origin/teams/fix-the-per-repo-action-notice-r-inv_004`, never merged, reverted by the operator after the loosening masked a real race the same change introduced. Both incidents cited the host's own quarantine-proposal text (`_pr_flake_ledger._flake_proposal_body`), which used to present "add this spec to the flake baseline" as one of two equivalent remedies. This build removes that sanction and adds detection; it does not add a merge-blocking gate (see "Deliberately non-blocking" below).

**No historical sweep runs.** The two instances above are recorded here, not rediscovered by scanning history. If a future sweep is ever built, it must take attribution from the fleet commit ledger, the branch prefix and the commit message, never from the commit author field or `Co-Authored-By` trailers, which do not reliably identify who made a commit.

## The canonical path list — extended, not duplicated

`alan/teams/_pr_freshness.py`'s `MEASURED_BASELINE_PATHS` was already the list the merge gate uses (via `is_measured_baseline_stale`) to decide whether an open PR's baseline comparison is stale relative to `main`. This build adds a ninth entry, `tests/test_size_ratchets.py`, and reuses the same list as the ratchet-direction classifier's rule-table key set — a structural test (`tests/test_ratchet_direction.py`) asserts the two sets are exactly equal, so the classifier can never silently drift out of sync with the merge gate's own notion of "a measured baseline file." The ninth entry was required because the DoD's "stale aggregate bound literal" case — the four `<= 44075/12462/26/9` bounds inside `tests/test_size_ratchets.py`'s two `test_pinned_*_does_not_grow` functions — was previously absent from the list entirely.

The eleventh entry, `.github/ci-duration-budgets.json`, registers the per-job CI duration budgets (`docs/architecture/ci_duration_budgets.md`) as a tightening-only ratchet: the budget file is hand-edited, raising a budget is the loosening direction, and lowering one is always allowed. The same one-line registration that put it on the merge gate's staleness list also put it on this classifier's rule table, and `tests/test_ratchet_direction.py`'s key-set equality test is what forced both halves to land together.

Extending the existing list, rather than declaring a second one, is deliberate: this repository already has a gate (`lint-imports`, the size-ratchet gate, and general review discipline) against exactly the "two enumerations that must stay in sync" drift class, and duplicating `MEASURED_BASELINE_PATHS` here would recreate it.

## Per-path direction table

| Path | Loosening direction |
|---|---|
| `alan/web/e2e/flaky-baseline.json` | a spec name present in HEAD, absent from BASE (shrinking is tightening) |
| `alan/web/e2e/stress-floor.json` | `minExecutedTests` or `minSpecFiles` decreasing |
| `alan/web/.jscpd.json` | `threshold` increasing (`minLines`/`minTokens` are deliberately out of scope — the DoD narrows this clause) |
| `alan/web/e2e/mobile-audit/baseline.json` | any `screen.dimension` count increasing, both-sides-present only |
| `alan/web/e2e/desktop-audit/baseline.json` | same, both-sides-present only |
| `alan/web/e2e/mobile-audit/baseline-high-water.ts` | any `screen.dimension` pin value increasing, both-sides-present only. The pin is a TypeScript object literal, not JSON — `_ts_object_literal` normalizes bare and quoted keys and strips trailing commas before delegating to the same `_screen_finding` logic `baseline.json` uses |
| `alan/web/e2e/desktop-audit/baseline-high-water.ts` | same, both-sides-present only |
| `tests/size_baseline.py` | `TEST_FILE_CAP`, `SOURCE_MODULE_CAP`, or `CLAUDE_MD_BYTE_CEILING` increasing. Pin dict values and `PINNED_*_TOTAL`/`PINNED_*_COUNT` are out of scope — the existing aggregate-bound tests (`test_pinned_test_baseline_does_not_grow`, `test_a_raised_test_pin_would_fail_the_aggregate_bound`, and their source-side twins) already guard those; duplicating that guard here was explicitly ruled out. `PINNED_FRONTEND_TEST_FILES`/`_TOTAL`/`_COUNT` stay out of scope here for the same reason as the other pin dicts — `test_pinned_frontend_baseline_does_not_grow` in `tests/test_size_ratchets.py` guards them via the row below, not this one |
| `tests/test_size_ratchets.py` | any `<= <int>` bound literal inside a `test_pinned_*`-named function increasing, keyed by `(function name, left-hand expression)` so reordering the assertions inside a function doesn't change identity |
| `tests/gh_read_baseline.py` | any test key added to `GH_READ_STUBBED_TESTS` (the shrink-only list of legacy tests whose read-only `gh` calls the suite stubs — `docs/architecture/ci_isolation.md`). Both sides must parse as `from __future__` + one `frozenset({...})` of string constants; anything else is not compared |
| `.github/ci-duration-budgets.json` | any `workflow.job` budget (seconds) increasing, both-sides-present only. The file is a plain `{workflow: {job: seconds}}` JSON object, so the rule is `_audit_baseline_rule` reused unchanged — the same `_screen_finding` logic the UI audit `baseline.json` files use, with the workflow file name in the screen position and the job name in the dimension position. A lowered budget, a new job key, and a new workflow key are not findings; `docs/architecture/ci_duration_budgets.md` covers who may raise a budget and why |

`detect_ratchet_loosening` is only the `_RULES` loop over the table above: it reads no roadmap file, and no rule is handled outside that table.

## The both-sides-present rule, and why the false-positive replay targets stay green

A finding requires the compared key (a spec name, a screen/dimension pair, a named cap, a `(function, expression)` bound) to be present and structurally valid on **both** BASE and HEAD. A key missing from BASE, missing from HEAD, or unparseable on either side produces no finding for that key — not a conservative "flag it anyway."

This single rule is what makes three real historical PRs replay clean without any per-case special-casing:

- **#1289** (a new UI-audit-baseline key) — the new screen key exists only in HEAD; `base_dims` for it is `None`, so the per-screen loop skips it. Adding a key is not loosening.
- **#1128** (a key rename that reset a count) — the old key disappears from HEAD (never iterated) and the new key is absent from BASE (skipped when looked up). A rename is not loosening even though the count reset to a lower number under the new name.
- **#1276** (a floor raise) — `stress-floor.json`'s floor increasing is the *opposite* of the loosening direction for that file (a floor loosens by decreasing, not increasing), so the rule does not fire — this is not a both-sides edge case at all, just the correct direction test.

## Base-unresolved is a distinct, explicit outcome

`detect_ratchet_loosening` calls `alan.base_ref.resolve(checkout)` exactly once (the same `git fetch origin main` contract used by `_changed_e2e.build_changed_e2e_scenario`) and returns `None` — not an empty tuple — when the base cannot be resolved. `render_ratchet_block(None)` renders a distinct block stating the comparison was not made and no direction is inferred from one side, rather than silently reading as "this PR loosened nothing." `render_ratchet_block(())` (base resolved, zero findings) renders no block at all — there is nothing to say.

## Where the finding lands: the verification pass's review input, not a check annotation

`alan/teams/_verification_gates.py`'s `_failing_e2e_tests` only reaches `fetch_check_run_annotations` when the named check is among the PR's currently-*failing* checks — a green check's annotations are read by no machine in this fleet. Since a ratchet-widening commit is specifically the kind of change that turns a check from red to green, an annotation-only surface would be invisible for exactly the case this build exists to catch.

Instead, `alan/teams/_review_context.py::diff_reviewer_prefix(pr_number, workspace, task_id, *, checkout)` computes `_ratchet_direction.render_ratchet_block(_ratchet_direction.detect_ratchet_loosening(checkout))`, joins it with the PR evidence block, and `VerificationTeam.run()` (`alan/teams/verification.py`) sets this as the `diff_reviewer` role's user-text prefix unconditionally — via the always-on `diff_reviewer_prefix`, with no flag-gated opt-out to silently fall back to. A detector reachable only behind a flag would risk passing every unit test and firing zero times in production — the same silent-failure shape this work exists to close.

A non-empty finding also appends a `ratchet_loosening_detected` audit row (`task_id`, `pr_number`, `paths`) via the shared `alan.teams._pr_audit_log.append_audit`, so the detection has an independent, greppable trail beyond the LLM-visible prompt text.

## Deliberately advisory, deliberately non-blocking, and why

The block states plainly that it is advisory and that the host applies no mechanical block: no `DIFF_FINDINGS_CRITICAL` entry, no readiness failure, no check annotation. Promotion to a merge-blocking check is an explicit, separate, evidence-backed decision to be made later, after observing this detector's real-world firing rate — not a design left toothless by omission. The reason: measured mechanical recovery on a genuine merge block sits at zero of thirty-five (`docs/architecture/merge_recovery.md`), so on a fleet already reporting degraded, every false fire of a new blocking gate costs a human or a fix-worker turn rather than fixing anything. A human-readable inline warning may exist as a secondary surface, but the review-input path above is the only one that is guaranteed to be read, since it rides the same pipeline stage that reads everything else the judge decides on.

## The high-water pin is cited as evidence, never copied as a model

`alan/web/e2e/mobile-audit/baseline-high-water.ts` / `desktop-audit/baseline-high-water.ts` exist so a raising diff has an unambiguous, reviewable *shape* — see `docs/architecture/e2e_testing.md`'s "Ratchet-baseline gate" section, which documents this as a reviewability device, explicitly not an enforcement or detection mechanism. It has already failed this way once: a merged change's own commit message records that the audit pins were never lowered back to match a fixed regression, leaving the baseline permanently grandfathered at a stale, looser number until a later manual catch. That failure is exactly the evidence this build cites for why "reviewable" and "detected" are different properties — it is not a template this detector reimplements.

## Legitimate raises still have a channel — moved, not deleted

`alan/teams/_pr_flake_ledger.py::_flake_proposal_body`'s spec-level remedy no longer reads "add this spec to the flake baseline" as an in-place edit. It now reads: open a **separate** pull request whose only content is the baseline addition, referencing the quarantine issue and the rerun-rescued evidence that issue already accumulated (`_maybe_open_flake_proposal`'s existing `_DEFAULT_FLAKE_QUARANTINE_THRESHOLD` — three distinct PRs, each counted only when the check was red before a rerun and green on the same head SHA after it). The inequality this closes: the quarantine channel already demands that rerun-rescued evidence across three PRs, while an in-PR baseline edit demanded none — the fix is to make the legitimate path go through the channel that already has an evidence bar, not to prohibit raises outright (a flat prohibition collides with that legitimate channel and gets argued around).

## Fleet roles now carrying the rule where they read it

- `alan/house_rules.py`'s `HOUSE_RULES` (version bumped 1 → 2) gained `house-no-ratchet-widening` (`direction="must_not"`), reaching `alan/teams/brief_refine.py`, its consumer.
- `alan/teams/implementation.py`'s `_DEFAULT_FORBIDDEN_ACTIONS` gained the same rule text, engine-independently: it renders into the "## Forbidden actions" section of every implementation worker's user text via `alan/teams/_pr_handover.py`'s `_forbidden_actions_block`/`compose_user_text`, regardless of whether the governing engine is the default one or a generic (non-default) one — closing a gap the ENV-block-only prose (below) could not close on its own, since the generic environment-block renderer never included that prose at all.
- `alan/teams/implementation.py`'s default-engine environment block (`_ENV_BLOCK_TEMPLATE`) had its existing flake-ratchet paragraph de-scoped: it used to say "never loosen a threshold… when CI's flake ratchet names a spec **this diff adds or edits**" — precisely the case both incidents fell outside of, since neither incident's ratchet loosening was itself the newly-flaky spec's own diff. It now applies to any spec the ratchet names, and points at the same relocation channel above instead of a bare prohibition.
- `alan/teams/_fix_forward.py`'s `ABSOLUTE_FORBIDDEN_ACTIONS` gained the rule as an **absolute** prohibition (cannot be overridden by any dispatch instruction) — its enumerated baselines now end "a UI audit baseline, the duplication threshold, or a CI duration budget", so a fix worker is forbidden to raise a job's budget in `.github/ci-duration-budgets.json` in the pull request it is fixing — which `alan/teams/implementation_fix.py` renders into both its default and generic system prompts — closing the gap that the role which authored the offending commit in both incidents previously carried no ratchet prohibition of any kind.

## A parallel guardrail counter for the autonomous unblocker, not a new path onto `MEASURED_BASELINE_PATHS`

The autonomous build-workspace unblocker ([unblocker.md](unblocker.md)) dispatches `implementation_fix` runs with bounded, previously-human-only authority (clause withdrawal, flake quarantine, proceeding past `pre_existing_red`). Its observability plan (Ruling 6 of that design's owner rulings) wires this detector's existing `Loosening` result as a guardrail metric — but as a **parallel counter of `Loosening` results reported specifically on unblocker-authored PRs**, not by adding a path to `MEASURED_BASELINE_PATHS` above.

The reason is the same inspection this doc already relies on for its own structural test: that eleven-path frozenset (`alan/teams/_pr_freshness.py::MEASURED_BASELINE_PATHS`) is consumed by **both** this detector's rule-table key set **and** `is_measured_baseline_stale`, which gates whether an open PR's baseline comparison is stale enough to block merge. Appending a twelfth path to widen the unblocker's own visibility would silently change merge-gate staleness semantics for every PR in the fleet, not just unblocker-authored ones — an entirely different blast radius than the guardrail metric is meant to have. The unblocker's counter therefore rides this detector's existing, unmodified findings (filtered by PR authorship via the fleet commit ledger) rather than asking this module's own canonical list to grow for a caller with a narrower need. `docs/architecture/unblocker.md`'s "Observability and guardrails" section is the authoritative spec for the UI surface (`StatusGroupChips` in `RepoStatusStrip.tsx` plus one column on `RepoHealthTable.tsx`) — no new screen, no mobile-audit pin.

## Consulted, not duplicated: the merge gate's bypass-label precedent

`alan/drift_gate.py`'s bypass label is read from the job-level condition off the original event payload, so a label added after a run started can never be observed mid-run. That shape is deliberately not used here: this detector produces no blocking check at all, so there is nothing to bypass yet, and inventing a bypass mechanism ahead of a blocking gate that does not exist would be scope creep this build's boundaries explicitly rule out.
