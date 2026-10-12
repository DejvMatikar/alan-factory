# CI job duration budgets

## Purpose

Between 09-26 and 09-29 the `Backend tests + coverage` job grew from about 140s to about 375s (+165%) in four steps of roughly +16%, +23%, +15% and +45%. Frontend unit tests (+36%) and frontend lint (+34%) crept the same way the week before. No single PR looked alarming, so nothing flagged it. Comparing a PR with whatever `main` measured yesterday cannot catch this, because each step is small against the last. The reference has to be a fixed number committed to the repository.

Two mechanisms hold that line:

1. **A per-PR check** — the `duration-budget` job in `.github/workflows/tests.yml`, displayed as `Duration budgets (advisory)`. It compares every job of the PR's own run with its committed budget: silent when close, a warning when moderately over, and a FAIL verdict when far over. **It is advisory for now** (see *Advisory mode* below): a FAIL is reported as a warning and does not fail the run.
2. **A drift watch** — the daily `.github/workflows/ci-duration-drift.yml`. It compares the median of each job's last 7 successful `main` runs with its budget and files a GitHub issue when a job drifts above it or sits well below it.

Out of scope: fixing the current backend regression, splitting or sharding test jobs, runner capacity, budgets for the fleet's local gate runs, and any UI. Nothing here loosens an existing gate or makes an advisory check blocking. The one `timeout-minutes` change, backend from 8 to 10 minutes, was the owner's explicit call, and the new check itself ships advisory. Missing timing data never blocks a PR.

## The budget file — `.github/ci-duration-budgets.json`

```json
{ "<workflow file>": { "<job display name, or job id when the job has no name>": <int seconds> } }
```

The file holds only numbers: no `_meta` key, because the ratchet detector reads it as `{group: {key: number}}`. A key is the job's display name exactly as the Jobs API reports it (`Backend tests + coverage`), or the job id when the job has no `name:` (`ci-deploy.yml`'s `deploy`). The check reads the `tests.yml` group; the drift watch reads every group. `alan/ci_budget.py` `load_budgets` rejects a non-mapping, a non-number, a boolean, NaN, infinity and any value at or below zero, and a rejected file degrades the check to a visible warning (below).

### Seeding rule

A budget is the **median of the job's last 7 successful `main` runs** at the moment it is written (the last 7 successful PR runs for a job that only runs on PRs), then clamped so its failure point stays clear of the job's own timeout:

```
fail_point(b) = b + max(0.5 b, 20)  <=  0.95 x timeout_seconds
```

The budget file is listed in the `backend_support` path filter of `tests.yml`, so a budget-only PR still runs the backend suite instead of skipping it. A job can therefore never hit its `timeout-minutes` before the check reports, because a timed-out job reads as `cancelled` and is not measured. `tests/owner/test_ci_budget_structure.py` enforces the inequality for every committed budget and names any that break it. For `Backend tests + coverage`, the owner raised `timeout-minutes` from 8 to 10 (600s) on 09-30 so a slow run finishes instead of being cancelled. The largest legal budget is therefore 380s (fail point 570s, exactly 95% of 600s). At seeding the 7-run median was 301s, after #1901 took the job back from about 375s, so the budget is **301s**.

### Seeded budgets (09-30)

Seeded from the Actions API on 09-30: the last 7 successful `main` runs per job, or PR runs for `CLAUDE.md drift gate` and `Coverage PR comment`, which never run on `main`. No budget needed clamping. `main-red-report` runs only on failure and `ci-duration-drift.yml` has no history yet, so both are unbudgeted. The nightly jobs are seeded from their last 7 successful `main` runs, which for the weekly `behavioral_eval.yml` reach back to July.

## Advisory mode

`FAIL_BLOCKS_PR` in `alan/ci_budget.py` is `False` and pinned by `test_thresholds_are_pinned`. While it is off, a FAIL verdict is still computed and named, but:

- `ci_budget_check` exits 0;
- the annotation is a `::warning`, not an `::error`;
- the message ends with a note that the check is advisory during calibration.

Why: replaying the unchanged rules over the captured 09-20 to 09-29 history (see *Backtest*) fails 8 of the 137 merged PRs of the quiet period, 09-20 to 09-26. Most are single-job outliers that the contention rule cannot demote because their peers were normal:

- CCN at 58s against 13s (#1640);
- backend at 212 to 255s against 138s (#1726, #1691, #1541);
- UI ratchet at 114s against 74s (#1760);
- frontend tests at 96s against 62s (#1768);
- security audit and E2E together (#1772);
- SonarCloud at 180s against 78s (#1774).

A blocking FAIL goes to a fix worker and is never re-run, so about 6% of healthy PRs would have got a pointless fix loop. No threshold separates this noise from the real growth: #1726 was +54% of noise, while the real regression on 09-28 was +42 to +46% for #1853 to #1855. So the tiers stay as they are and only blocking is withheld. The drift watch is unaffected.

Follow-up: tune the FAIL and contention rules against the recorded quiet-period outliers until `_meta.quiet_period_fails` in `tests/owner/ci_budget_pr_history.json` is empty, then set `FAIL_BLOCKS_PR` to `True` and rename the job back to `Duration budgets (blocking)`. Both are human decisions taken in that follow-up, not fixes to make in passing.

## The per-PR check — `Duration budgets (advisory)`

The job is terminal in the PR run:

- `needs` every suite job (`changes`, `drift-gate`, `test-backend`, `lint-python`, `lint-python-pyright`, `lint-imports`, `ccn-gate`, `security-audit`, `lint-frontend`, `api-types-drift`, `test-frontend`, `e2e-frontend`, `mobile-audit-gate`) but not `sonar`, `coverage-comment` or `main-red-report`, which are reporting jobs. `sonar` and `coverage-comment` run alongside it rather than before it, so `SonarCloud (advisory)` and `Coverage PR comment` are also listed in `PER_PR_EXCLUDED_JOBS` (below): their rows would otherwise flip between *not measured (in progress)* and a real duration depending on timing. It is not a `needs` of `main-red-report`: it never runs on a push.
- `if: ${{ !cancelled() && github.event_name == 'pull_request' }}`. There is no `success()`, so it still reports when a suite job fails or is skipped.
- Runs on the `ci` runners through `astral-sh/setup-uv` (Python 3.11) and `uv run --no-project --python 3.11 python -m alan.ci_budget_check`, with **no** `uv sync`: a registry outage that breaks the suite jobs' `uv sync` must not also silence the reporter. The seven modules behind it are stdlib-only, which `tests/owner/test_ci_budget_structure.py` checks by import.
- Job permissions are `contents: read`, `actions: read` (Jobs API and artifact download), `pull-requests: write` and `issues: write`.
- A same-run terminal job, not a `workflow_run` workflow: a `workflow_run` check would leave a window where every other check is green and the fleet merges before this one exists. Like every other gate, the PR supplies the checker code from its own merge ref.

Modules (all in the `alan` layers list, stdlib only, placed so a lower module never imports a higher one): `ci_budget` (pure rules and types), `_ci_drift` (pure drift rules), `_ci_jobs_api` (urllib reads), `_ci_test_timings` (JUnit and Playwright parsing), `_ci_budget_render` (annotations, summary, comment and issue text), `ci_budget_check` and `ci_budget_drift` (the two CLIs).

### What is measured

Duration is the job's own run time: `completed_at - started_at` from `GET /repos/{repo}/actions/runs/{run_id}/jobs?filter=latest`. Queue and concurrency wait sit before `started_at` and are excluded, the same convention `scripts/e2e_lane_timing.py` uses (`created_at` to `started_at` is wait). `filter=latest` reads the most recent attempt after a re-run.

The PR-only step `Stress changed specs (repeat-each 5, no retries)` (in `e2e-frontend`, run only on PRs that touch specs) is subtracted, so a spec-touching PR is not compared against a budget seeded from `main` runs that never contain it. `PR_ONLY_STEPS` names it and the structural test pins that it exists in `tests.yml`.

Only `success` and `failure` conclusions are measured. `skipped`, `cancelled` (which is also how a job stopped by its own timeout reports) and in-progress jobs are listed as *not measured* in the summary and never get a verdict. `PER_PR_EXCLUDED_JOBS` is excluded entirely: the check job itself plus `SonarCloud (advisory)` and `Coverage PR comment` (`REPORTING_JOB_NAMES`), which the check does not `needs`. Excluded jobs get no row, no verdict and no vote in the contention rule. `SonarCloud (advisory)` keeps its budget for the drift watch, which reads `main` runs. `Coverage PR comment` never runs on `main`, so its budget is enforced by neither mechanism.

### Tiers and their minimums

With `over = duration - budget`:

| Verdict | Condition | Effect |
| --- | --- | --- |
| OK | anything else | silent |
| WARN | `over > 10%` **and** `over >= 10s` | annotation, step summary and PR comment; the job passes |
| FAIL | `over > 50%` **and** `over >= 20s` | annotation, summary, comment; the job exits 1 only when `FAIL_BLOCKS_PR` is on |
| UNBUDGETED | measured, no budget | warning asking for a budget; never a failure |

Both tiers need the ratio **and** the absolute minimum, so a short job cannot trip on noise. The constants are `WARN_RATIO`, `WARN_MIN_S`, `FAIL_RATIO`, `FAIL_MIN_S` in `alan/ci_budget.py`, pinned by `test_thresholds_are_pinned` so that changing one is a visible edit to a test whose purpose is to say it is a human decision, not a fix.

The evidence behind each number (measured over merged PRs in the stable period, 09-20 to 09-26, plus the growth episode):

- **Long jobs sit within about ±5% of their trend.** A 10% warning line is twice that noise; a job crossing it is real growth, not jitter.
- **About 1 run in 10 is +25% to +48% slower, and the slowdown usually hits every job of that run at once** — host contention rather than code. A 50% failure line sits above the worst measured spike, so a spike alone cannot fail a job that is on budget.
- **Jobs of 10 to 20s swing by up to about 8s (±30 to 50%).** The 10s warning floor clears that swing; the 20s failure floor is 2.5 times it.
- **Suites grow about 2% a day as tests are added.** The warning line is crossed after about 5 days of unbudgeted growth, which is the cadence at which a budget should be revisited, not ignored.
- The growth episode the check exists for: the backend job moved +16%, +23%, +15%, +45% in steps. Against a fixed 140s budget the +45% step (about 367s at PR #1881, which added about 190 tests that drive a real setup script) is far past the 50% and 20s line; against a rolling reference none of the steps was.

### The contention rule

A FAIL is demoted to a **CONTENTION** warning when the run looks uniformly slow:

- take every *other* measured job whose budget is at least `CONTENTION_MIN_BUDGET_S` (60s) — a leave-one-out set, so the failing job never vouches for itself;
- require at least `CONTENTION_MIN_PEERS` (2) of them;
- demote when the median of their `duration / budget` is at least `CONTENTION_MEDIAN_RATIO` (1.20).

The message says every peer is also slow, names host contention as the likely cause, and does not fail. Why these numbers: jobs under 60s carry the 30 to 50% relative noise above, so they cannot serve as evidence about the host; one peer is a single draw, two is the smallest set with a median; and 1.20 separates the populations, since normal runs put the peer median near 1.0 to 1.05 and spikes put it at 1.25 to 1.48. A spike on top of stale budgets (two weeks of 2% growth is about 1.3x) can push a job past 1.5x, which is why demotion exists at all. A single-job regression such as #1881 leaves its peers near 1.0, so it is not masked. Uniformly stale budgets can themselves push peers to 1.20; the drift watch is the backstop for that case and for a slowdown in shared setup that looks like contention. The backtest below replays these constants over the captured history: no quiet-period run has a peer median at or above 1.20, so the rule never fires on that data, and the values are unchanged.

### Unavailable data, and what stays loud

The check passes with a visible `::warning` (annotation, summary line and PR comment) when:

- the Jobs API is unavailable (`URLError`, timeout, `OSError`, invalid JSON, or an HTTP error such as 403 or 429; `ApiUnavailableError`), or
- the budget file is missing or invalid (`BudgetFileError`), or
- the changed-files lookup for the slowest-file list fails (the verdict stands; only that section is dropped).

It never fails a PR because timing data is missing. **Any other exception propagates** and turns the step red: a code bug in the checker must not pass silently.

### What the arguments may point at

The workflows pass fixed arguments, but both entry points validate them so a mistyped or hostile value cannot read or write elsewhere or redirect the token:

- `--budgets`, `--comment-path` and `--resolved-path` (and `--budgets` of `ci_budget_drift`) go through `workspace_path`: resolved against the working directory with symlinks followed, and a path that lands outside it is a usage error (exit 2). That is a configuration bug, not missing data, so it turns the step red instead of passing with a warning.
- The Jobs API client sends the token only to `https://api.github.com`. Any other `--api-url` or `GITHUB_API_URL` is an `ApiUnavailableError`, so the check passes with a warning and sends nothing. The repository must be `owner/name` made of letters, digits, `_`, `.` and `-` (never `.` or `..`); the run id and workflow file name are percent-encoded.

### Output

- Annotations, errors first, capped at 10 (`MAX_ANNOTATIONS`); `%` and newlines are escaped so a job or file name cannot inject a workflow command.
- The full table in `$GITHUB_STEP_SUMMARY`: budget, measured, difference and verdict per job, plus the not-measured list and the thresholds.
- A sticky PR comment identified by `<!-- ci-duration-budget -->`: created on a warning or failure, updated in place to a one-line "resolved" text on a later clean push, and never created on a clean run. Posting is best effort (`continue-on-error`), since fork and dependency-bot tokens are read-only.

### The FAIL message

It names the job, its budget, the measured duration and the difference in seconds and percent, and says the fail point. For jobs with a timing source it lists the slowest test files the PR added or changed, labelled *cumulative test time, summed across workers* because the parallel runners sum per-test time:

| Job | Source |
| --- | --- |
| `Backend tests + coverage` | `--junitxml=junit.xml`, uploaded as `junit-backend` |
| `Frontend tests + coverage (100/100/100/100)` | vitest `junit` reporter, uploaded as `junit-frontend` |
| `Frontend E2E (Playwright, blocking)` | the existing `playwright-results-json` artifact (uploaded only when the main suite passed) |

Changed files come from `GET /repos/{repo}/pulls/{pr}/files` (added, modified and renamed, by new name, three pages) and match the reported files by suffix after dropping the `alan/web/` prefix. Missing artifacts drop only this section.

It then says: do not raise the budget in this PR (`.github/ci-duration-budgets.json` is a tightening-only ratchet), find what slowed the job, and a rerun will not change the verdict. **Fleet recovery:** the check is not in the flake allowlist, so a FAIL is treated as a real failure and routed to a fix worker, never auto-rerun; a rerun would re-read the same timings, and a new push re-measures, which is also the recovery from a rare contention false-fail.

## Raising and lowering a budget

- **Lowering is always allowed.** When a job gets faster, lower its number; the drift watch files a *slack* issue proposing the value.
- **Raising is a deliberate human decision.** A raise is a hand-edit of `.github/ci-duration-budgets.json` in its own PR, visible in the diff, made by the repository owner. Fix workers must not do it: the FAIL message forbids it, and the companion change that registers the file as a tightening-only ratchet makes the fleet's loosening detector (`docs/architecture/ratchet_direction.md`) flag a raise the way it flags the other baselines. A raised budget still has to satisfy the timeout inequality above; the structural test fails the PR otherwise.
- Every budget edit on `main` marks open PRs stale against the measured-baseline list, the same as any other ratchet file. This is accepted.
- A new job needs a budget in the PR that adds it; until then it warns.
- An edit to a job the shipped workflow runs also edits the public overlay copy in the same PR.

## The drift watch — `ci-duration-drift.yml`

Private and daily (`workflow_dispatch` also). `watch` runs `python -m alan.ci_budget_drift` and `file` files what it found. For every workflow key in the budget file (the PR lane, the nightly workflows and deploy) it fetches about 14 successful `main` runs (`branch=main&status=success&exclude_pull_requests=true`) and their jobs, then per job:

- `after` = median of the last 7 runs containing the job, requiring **at least 4 samples**; `before` = median of the 7 runs before those (shown only with at least 4);
- **slow** when `after > 1.10 x budget`; **slack** when `after < 0.80 x budget` **and** `budget - after >= 20s`.

Why: the median of 7 consecutive runs moves only ±2 to 3%, so 10% is three to four times its noise, and a single spike cannot move a 7-run median; the slack pair of conditions mirrors the warning pair, so a short job is not reported for jitter.

Each finding becomes one entry of a matrix. The `file` job, one matrix leg per finding, calls the unchanged `.github/actions/file-finding-issue` action with:

- a **per-job label**, `ci-slow:<slug>-<hash6>` or `ci-budget-slack:<slug>-<hash6>` (at most 50 characters, the hash keyed on workflow and job), so the action's open-issue lookup by label gives one issue per job;
- a **dedup marker** `<direction>:<budget>:<bucket>` where the bucket is the median as a percentage of the budget, floored to 5%, so an unchanged situation comments nothing on the nightly repeat and a further 5% of drift does;
- a body with the job, the budget, the median before and after, the **merge window** (the PRs of the runs from three before to the run where the rolling median first crossed the line — a step change at run k crosses a 7-run median at run k+3 — read from the `(#N)` in each head commit subject), and a **proposed budget** with the reminder that a raise is a human decision.

The matrix body reaches the filing step through an `env:` variable and `printf`, never inlined into a `run:` block, and is capped at 20 findings a day. Both jobs are `continue-on-error: true`, `ci_budget_drift` exits 0 on every handled condition (an unavailable workflow history is skipped with a warning), so the watch never turns a run red.

What it cannot see: workflows with no entry in the budget file, because it walks only the budget file's workflow keys (today `hotspots.yml`, `auto-merge-label.yml` and `ci-duration-drift.yml` itself; a weekly pip-audit or snapshot-style workflow would be unwatched until it gets budgets); jobs that never run on `main` (`drift-gate`, `coverage-comment`, `duration-budget`); the failure-only `main-red-report`; and a weekly workflow with fewer than 4 runs in the fetched window. Sampling successful runs only carries survivorship bias; a job that has started failing is `main-red-report`'s concern. Weekly and nightly workflows run on the same hosts but at different times, so their medians are not comparable with the PR lane's.

## Backtest

`tests/owner/test_ci_budget_backtest.py` replays the rules over the history captured on 09-30. It seeds budgets from the median of every successful `main` run from 09-20 to 09-26 (backend 138s) and takes the last attempt of each merged PR's final-head `tests.yml` run. The drift watch is replayed with its production view of the last `RUNS_PER_WORKFLOW` (14) runs.

The brief expected four results, and the captured data refuted two of its premises:

| Brief expectation | Measured | Asserted |
| --- | --- | --- |
| Fail on #1881 and every later PR | True for every PR from #1881 to 09-29 that ran the backend job. PRs whose path filter skipped the backend (#1883, #1896) cannot fail. | Every later PR that ran the backend job fails. |
| Fail on the 09-28 PRs | 9 of 12 backend PRs fail. #1853, #1854 and #1855 were at +42 to +46%, just under the 50% line, so they only warn. | Every 09-28 backend PR is flagged; the warn-only set is pinned in `_meta.spike_day_warn_only`. |
| Fail nothing from 09-20 to 09-26, including three spike runs | 8 of 137 fail (see *Advisory mode*). No quiet-period run has a peer median at or above 1.20, so the "three spike runs" can't be identified in this data. | The failing set equals `_meta.quiet_period_fails`. |
| Drift fires by 09-27, naming #1845 or #1847 | It fires at 09-27 01:54 after #1841, and the window names #1838 to #1841: the jump started at #1838 (222s), before #1845. It also fired briefly on 09-20 and 09-21, when the backend on `main` alternated between about 130s and about 185s on all three runners. | The sustained finding, the last streak that runs to the end of the history, starts by 09-27 and names #1838. |

The pins are records, not targets: a rule change that moves a set updates `_meta` in the same PR, and that diff is where the change gets reviewed.

Fixture shape: each file is `{"_meta": {"captured", "seed_window", "seed_rule", "population", ...}, "runs": [...]}`. A PR entry is `{"pr", "date", "run_attempt", "jobs": {name: seconds}}` and a main entry is `{"run_id", "date", "pr", "durations"}`, storing PR numbers only. The durations are job run time as `alan/ci_budget.py` `measured_duration_s` computes it.

`started_at` check: on three `e2e-frontend` jobs from 09-29, the Jobs API `started_at` sits 1s before the first step starts, while the queue wait from `created_at` ran 15s to 2m42s. So the measured span excludes the queue, as intended.

## Copies

A copy's CI runs the PR-lane jobs only; `duration-budget` and the drift watch are not part of it. Its budget file holds only those PR-lane jobs, with the same values.
