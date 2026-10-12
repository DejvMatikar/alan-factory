# Main-branch CI failure issue filing

## Purpose

The full test suite in `.github/workflows/tests.yml` runs on every push to
`main` (every merge), exactly as it does on every PR. It is a precondition
for nothing on that path — deploying and this suite passing are independent.
A red main-branch run therefore used to be visible only in the Actions tab,
easy to miss. The `main-red-report` job turns a red run into a single
GitHub issue instead, so it reaches whatever is already watching the issue
tracker (a human, or the `issue-driver` sweep) without anyone polling CI.

This job **gates nothing**. It has no `needs` edge pointing at it from any
other job or workflow, and nothing downstream reads its result. It does not
touch `.github/workflows/ci-deploy.yml`. Whether the merge deployed is a
separate, already-independent question — see `docs/architecture/deploy_verify.md`.

## The job — `tests.yml`'s `main-red-report`

Depends (`needs:`) on `changes` plus the ten jobs that make up the actual
pass/fail test suite — `test-backend`, `lint-python`, `lint-python-pyright`,
`lint-imports`, `ccn-gate`, `security-audit`, `lint-frontend`,
`test-frontend`, `e2e-frontend`, `mobile-audit-gate`. Every one of those ten
already runs unconditionally on `push` (each carries `||
github.event_name == 'push'` in its own `if:`), so on a push to `main` where
`changes` itself succeeds, none of them is ever `skipped` —
`contains(needs.*.result, 'failure')` only ever sees `success` or `failure`,
never stale skip noise. `changes` is listed as a `needs:` in its own right,
not just an upstream dependency of the other ten: none of those ten jobs'
`if:` conditions call `always()`/`failure()`, so GitHub implicitly ANDs in
`success()` on their own `needs: changes` — a `changes` failure would
otherwise skip all ten (`result` "skipped", never "failure") and leave
`contains(needs.*.result, 'failure')` false despite the run being red from a
broken path filter. `sonar` and `coverage-comment` are deliberately left out
of the `needs:` list: they are quality-gate/reporting jobs, not the test
suite itself.

The job's own `if:` requires all four: `!cancelled()` (not `always()` — a
push superseded by a newer one on the same ref is cancelled via the
workflow's own `concurrency` group, and that must read as "superseded",
never as "red"), `github.event_name == 'push'`, `github.ref ==
'refs/heads/main'`, and `(contains(needs.*.result, 'failure') ||
contains(needs.*.result, 'cancelled'))`. A PR run never reaches this job at
all — the ref/event checks are enough on their own, but `needs:` pointing
only at jobs that already gate PRs the normal way means a red PR never
routes here either.

The trailing clause checks both `'failure'` and `'cancelled'` because
`needs.*.result` is a narrower vocabulary than the Checks API conclusion
`failing_jobs()` reads below: it only ever holds `success`, `failure`,
`cancelled`, or `skipped` — there is no `'timed_out'` value at that layer.
A dependency job stopped by exceeding its own `timeout-minutes` (for
example `test-backend`'s 8 minutes or `lint-python-pyright`'s 3) therefore
surfaces to `needs.*.result` as `'cancelled'`, not `'failure'`, even though
`failing_jobs()`'s own `_FAILURE_CONCLUSIONS` set already treats
`timed_out` as a failure once it reads that job's real conclusion from the
Jobs API. Checking only `'failure'` in the gate would let a timed-out gate
job report result `'cancelled'` and silently skip `main-red-report`
entirely. Adding `'cancelled'` to the gate can't reopen the
superseded-push case the leading `!cancelled()` term exists to exclude:
when the whole run is cancelled via the concurrency group, `main-red-report`
itself reads as cancelled and `!cancelled()` already short-circuits the
condition to false before the trailing clause is ever evaluated.

Needs no `gh` login on its runner: it never shells out to `gh`, only to
the GitHub REST API over HTTPS via `urllib` (see below), and other
jobs already do exactly that today through `actions/github-script` (the
`coverage-comment` job's frontend-coverage step) — this job's job-level
`permissions:` (`actions: read`, `checks: read`, `pull-requests: read`, plus
`issues: write` to file) is additive on top of `tests.yml`'s workflow-level
`issues: write`.

The render step invokes `scripts/render_main_ci_failure.py` with the
runner's system `python3` directly — no `astral-sh/setup-uv`, no
`uv sync --locked --extra dev`. The script is stdlib-only (see its module
docstring), and every suite job above already runs its own `uv sync`; an
identical sync in this job would let a registry or dependency-resolution
outage fail the reporter at the exact moment it exists to report that class
of failure.

## `scripts/render_main_ci_failure.py`

Renders the issue body and title from three GitHub REST API reads, all via
`GITHUB_TOKEN` (never `gh`, and never raw log text):

1. **Failing job names** — `GET /repos/{repo}/actions/runs/{run_id}/jobs`,
   filtered to a `conclusion` of `failure`, `cancelled`, `timed_out`, or
   `startup_failure` (`_FAILURE_CONCLUSIONS` — the same CI-failure
   classification `alan/repo_status.py`'s `_CI_FAILURE_CONCLUSIONS` already
   uses elsewhere in this repo, duplicated here rather than imported so the
   script stays stdlib-only). Scoped to *this run's own* jobs
   (`GITHUB_RUN_ID`), not every check on the commit, so a second workflow
   that happens to also trigger on the same push can never bleed into the
   report.
2. **Originating PR** — `GET /repos/{repo}/commits/{sha}/pulls`
   (`GITHUB_SHA`, the push event's own commit — for a squash-merge this
   *is* the merge commit). Missing or unassociated renders as `unknown`
   rather than failing the render.
3. **Structured annotations** — for each failing job id,
   `GET /repos/{repo}/check-runs/{id}/annotations`. An Actions job id and a
   Checks-API check-run id are the same numeric id, so the job ids from (1)
   are reused directly with no extra lookup. Capped at 20 total across all
   failing jobs (`_MAX_ANNOTATIONS`) and the whole body at 50,000 characters
   (`_MAX_BODY_CHARS`, comfortably under GitHub's 65,536 issue-body limit).

Every network call goes through one `_get_json` helper that returns `None`
on any `URLError`/`TimeoutError`/`ValueError`/`OSError` — a malformed
response, an auth fault, or a timeout degrades the affected field (empty job
list, `unknown` PR, no annotations) rather than crashing the render step.
The run is already red from the test jobs in that case; this reporter must
not itself go red and suppress the one finding it exists to file.

Two budgets keep the whole render step inside the job's 5-minute
`timeout-minutes`, even under partial API degradation: each `_get_json` call
carries a 10-second timeout (`_REQUEST_TIMEOUT_S`), and
`collect_annotations`'s per-failing-job loop — the one place that can issue
more than a couple of sequential requests, up to one per failing job — stops
once an aggregate 60-second wall-clock budget (`_ANNOTATIONS_BUDGET_S`) is
exceeded, returning whatever annotations it already gathered rather than
risking the job's own timeout killing it before the body is written and the
issue filed.

`main()` writes the rendered body to `--body-path` and a `title=` line to
`$GITHUB_OUTPUT`, which the workflow step reads into
`${{ steps.render.outputs.title }}`.

## Filing and dedup — `.github/actions/file-finding-issue`

The render step's output feeds the repo's existing shared filing action
(the same one `boundary-smoke.yml`, `forge-contract.yml`, `e2e-stress.yml`, and
`behavioral_eval.yml` already use) with `label: main-red` — the label the
`issue-driver` skill's sweep already picks up as an ordinary open issue —
and `dedup-marker: ${{ github.sha }}`.

Because the marker is the merge SHA itself (not a derived fingerprint), the
dedup contract is exactly: *the same merge* failing again comments on the
already-open `main-red` issue instead of filing a duplicate (this only
happens when a run is re-run via GitHub's re-run-jobs feature, which
replays the original `push` event and its `github.sha` rather than
generating a new one, since a merge commit is otherwise filed at most once
per push event); a *different* merge failing
while an issue is still open still comments on it, because the action's
open-issue lookup is by label only, not by marker match — the marker only
suppresses a byte-identical repeat, never a second distinct failure while
the first is unresolved. Closing the issue re-arms filing: the action's
`listForRepo({ state: "open", labels: label })` lookup then finds nothing
and opens a fresh issue on the next red run. A clean run produces no issue,
no comment, and no notification — the job's `if:` never even lets it start.
