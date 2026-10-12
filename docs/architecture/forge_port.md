# Forge port

`alan/forge.py` defines `ForgePort`, a `Protocol` naming the forge operations callers reach by
domain name rather than by command line, and `alan/forge_gh.py` defines `GhForge`, an adapter
implementing it. Seven of its fifty-two operations are the merge family's (X2.3; its eighth,
`update_branch`, was deleted in X2.7 PR21, "X2.7 PR21: the update-branch operation is deleted" below); four are the
review-feedback family's (X2.7 PR1, "X2.7 PR1: the query-shaped operations" below); one,
`compare_base`, is the baseline-freshness probe's (X2.7 PR2, "X2.7 PR2: the compare operation"
below); four are the flake ledger's (X2.7 PR3, "X2.7 PR3: the issue/label domain and the raw patch"
below); one, `open_prs_by_author`, is the dependency sweep's (X2.7 PR6, "X2.7 PR6: the dependency
sweep" below); four — `open_prs`, `workflow_runs`, `viewer_login`, `repo_file` — are the collision
check's, the deploy-run listing's and the engine preflight's (X2.7 PR8, "X2.7 PR8: collision,
deploy-run and engine-preflight callers" below); three — `prs_by_head`, `create_pr`, `pr_state` —
are the onboarding scaffold's (X2.7 PR9, "X2.7 PR9: the onboarding scaffold's PR calls" below);
five — `default_branch`, `commit_check_runs`, `latest_workflow_run`, `workflows`, `prs_by_author` —
are the repository-status panel's and the recurring-workflow status's (X2.7 PR10, "X2.7 PR10: repo
status and recurring forge reads" below); three — `update_pr_body`, `reply_to_thread`,
`comment_on_pr` — are the PR-body amender's and the Copilot resolver's (X2.7 PR12, "X2.7 PR12: PR
body, thread reply and PR comment" below); five — `open_issues_by_title`, `create_numbered_issue`,
`comment_on_issue`, `prs_by_head_prefix`, `latest_branch_run` — are the recipe unblocker's issue
filer's and the triage pre-gate's (X2.7 PR13, "X2.7 PR13: unblocker issues and triage reads" below);
four — `pr_head_branch`, `request_branch_update`, `pr_open_state`, `open_pr_titles` — are the PR-branch
module's view reads' and branch update's (X2.7 PR14, "X2.7 PR14: PR view reads and branch update"
below), which also retypes `head_sha` in place; seven — `commit_check_runs_page`,
`check_run_annotations`, `transport_check_run_annotations`, `rerequest_check_suite`,
`rerun_failed_jobs`, `status_check_rollup`, `failed_job_log` — are the PR-branch module's check-run
reads' and gate reruns' (X2.7 PR15, "X2.7 PR15: check-run reads and gate reruns" below); one,
`create_pr_from_checkout`, is the implementation team's (X2.7 PR25, "X2.7 PR25: the implementation team
opens its pull request through the forge port" below); three — `resolve_thread_checked`, `unresolve_thread`,
`repo_permission` — are the merge gate's stranger-thread settling (P1.14 / D28, "P1.14: the stranger-thread settle operations" below).
X2.7 PR4 adds no operation: the verification team's cannot-verify comment, verdict review post
and already-merged check reach the forge through `post_review` and `read_pr`, which were already
there ("X2.7 PR4: the verification review posts" below). X2.7 PR5 adds none either: the
deploy-verification merged-state read is `read_pr` ("X2.7 PR5: the deploy-verification read" below).
X2.7 PR7 adds none either: the workspace delivery read, the merge-conflict state read and the
clause-audit PR-state read all reach the forge through `read_pr`, which was already there ("X2.7 PR7:
the PR view callers" below). X2.3
landed in three PRs. PR1 added the port and adapter, exercised by their
own tests and by the repository's transport canary, with production behaviour bit-for-bit what it
was before. PR2 added the strict test fake, the substitution point below, and migrated exactly one
caller as proof (`_pr_reconcile._resolve_head_base`, `read_pr`) — chosen for narrowness, not as the
start of the family migration. PR3 migrated the merge family's remaining **covered** forge calls —
seven call sites, listed below — and re-expressed their tests against the fake. "Covered" is the
binding word: only calls that map onto one of the eight port methods moved. Everything else the
family sends to the forge has no port method yet and is X2.7's (see "What PR3 left for X2.7").

## Method table

Every identity named in this table, "ambient" in particular, is the identity on the owner's install and on an unconfigured install. On a copy every operation carries the target's transport credential, or the reviewer credential where the call acts as the reviewer ("Identity on a copy" above).

| method | delegate | caller |
|---|---|---|
| `read_pr(pr_number, fields, *, forge_target, timeout)` | `gh pr view --json <comma-joined fields>` (no `-q`), parametrized by `fields` | `alan/teams/_pr_reconcile.py::_resolve_head_base` (PR2); `alan/teams/_pr_ready.py::_gh_pr_view_readiness` and `_fetch_pr_base_sha` (PR3); `alan/teams/_pr_freshness.py::_read_refs` (X2.7 PR2); `alan/teams/_verification_merged_guard.py::pr_state_name` (X2.7 PR4); `alan/teams/deploy_verify.py::_pr_merge_info` (X2.7 PR5); `alan/teams/dependency_sweep.py::_fetch_pr_files`, asked for `("files",)` under the ambient identity (X2.7 PR6); `alan/delivery.py::fetch_pr_delivery`, `alan/teams/_pr_merge_conflict.py::gh_pr_state_and_merge_status` and `alan/teams/_clause_audit.py::_fetch_pr_state` (X2.7 PR7); `alan/teams/_pr_closing.py::gh_pr_closing_issue_numbers`, asked for `("closingIssuesReferences",)` under the ambient identity, and `alan/teams/_deploy_restart_probe.py::_merge_commit_sha`, asked for `("mergeCommit",)` against `DEFAULT_FORGE_TARGET` with a 60 s bound (X2.7 PR11); `alan/teams/_pr_body.py::fetch_pr_body`, asked for `("body",)` under the ambient identity with `GH_TIMEOUT_S` (X2.7 PR12); `alan/teams/triage_pregate.py::_fetch_pr_by_number` (so `verify_pr_merged` and the ghost check), asked for `("number", "state")` against the default engine's repository under the ambient identity with a 30 s bound (X2.7 PR13) |
| `read_checks(pr_number, *, forge_target, timeout)` | `gh pr checks --json name,state` | `alan/teams/pr_merge.py::_gh_pr_checks` (PR3) |
| `merge_pr(pr_number, expected_head_sha, *, forge_target, timeout)` | `gh pr merge --squash --delete-branch --match-head-commit` | `alan/teams/pr_merge.py::_execute_merge` (PR3) |
| `compare_base(head_sha, base_ref, *, forge_target, timeout)` | `gh api repos/{slug}/compare/{head_sha}...{base_ref}`, the base ref percent-encoded with no safe characters; returns `CompareRead(ok, behind_by, files, truncated, returncode, stderr)` | `alan/teams/_pr_freshness.py::_compare_gap` (X2.7 PR2) |
| `head_sha(pr_number, *, forge_target)` | `gh pr view {n} --repo {slug} --json headRefOid -q .headRefOid`, under the ambient identity; returns `ApiResult`, so a raised fault, a non-zero exit with its error text and an empty stdout stay distinguishable; a transport exception is not caught | `alan/teams/_pr_branch.py::_fetch_pr_head_sha`, reached through `fetch_flake_guard_head_sha`, `fetch_failing_check_details` and `rerun_external_gate` (X2.7 PR14) |
| `changed_files(pr_number, *, forge_target)` | `gh pr diff --name-only {n} --repo {slug}` (`--name-only` before the number), under the ambient identity; the stripped non-blank lines as a `frozenset[str]`; `None` on a transport fault or a non-zero exit | `alan/teams/_pr_ready.py::_gather_quarantine_inputs` (PR3); `alan/teams/_pr_freshness.py::is_measured_baseline_stale` (X2.7 PR2); `alan/teams/_pr_branch.py::_fetch_diff_files`, whose public alias `fetch_diff_files` the ledger, the clause audit, the verification team and the dispatcher call (X2.7 PR14) |
| `pr_patch(pr_number, *, forge_target)` | `gh pr diff <n> --repo {slug}`; the raw patch text (`changed_files` returns filenames only and cannot serve it); `None` on a non-zero exit or a transport fault, `""` for a successful empty diff | `alan/teams/_pr_flake_ledger.py::fetch_pr_diff_patch` (X2.7 PR3) |
| `open_prs_by_author(author, fields, *, forge_target, limit)` | `gh pr list --repo {slug} --author {author} --state open --json {comma-joined fields} --limit {limit}`, run as the transport identity (resolved inside the adapter); `--state open` is pinned, not a parameter; `()` on a non-zero exit, a transport fault, unparseable output or a non-list payload; non-object items are dropped | `alan/teams/dependency_sweep.py::_fetch_open_proposals` (X2.7 PR6) |
| `search_open_issues(query, *, forge_target, label)` | `gh issue list --repo {slug} --label {label} --state open --search {query} --json title`; the fuzzy candidate list as `tuple[JsonObject, ...]`, `()` on a non-zero exit, a transport fault, unparseable output or a non-list payload; non-object items are dropped | `alan/teams/_pr_flake_ledger.py::_has_open_flake_proposal` (X2.7 PR3); `alan/teams/dependency_sweep.py::_has_open_security_issue` (X2.7 PR6). Runs as the transport identity since X2.7 PR6 |
| `create_label(name, *, forge_target, color, description)` | `gh label create {name} --repo {slug} --color --description --force`; `True` on exit 0, `False` on a non-zero exit or a transport fault | `alan/teams/_pr_flake_ledger.py::_ensure_flake_label`, which discards the result (X2.7 PR3); `alan/teams/dependency_sweep.py::_ensure_security_label` (X2.7 PR6); `alan/teams/unblock_issue.py::ensure_label`, which discards the result (X2.7 PR13). Runs as the transport identity since X2.7 PR6 |
| `create_issue(title, body, *, forge_target, label)` | `gh issue create --repo {slug} --title --body --label`; `True` on exit 0, `False` on a non-zero exit or a transport fault | `alan/teams/_pr_flake_ledger.py::_open_flake_issue` (X2.7 PR3); `alan/teams/dependency_sweep.py::_open_gh_issue` (X2.7 PR6). Runs as the transport identity since X2.7 PR6 |
| `reviews(pr_number, *, forge_target)` | `gh api --paginate --jq .[] /repos/{slug}/pulls/{n}/reviews`, run as the verifier (env derived inside the adapter); `None` on a non-zero exit or a transport fault | `alan/teams/_pr_post.py::fetch_review_stream`, itself called by `_fetch_changes_requested_ids`, `_pr_waiver_dismissal.dismiss_for_waiver` and `unblock_sweep.default_deps` (X2.7 PR1) |
| `dismiss_review(pr_number, review_id, *, forge_target, message)` | `gh api -X PUT /repos/{slug}/pulls/{n}/reviews/{id}/dismissals -f message= -f event=DISMISS`, run as the verifier; `False` on a non-zero exit or a transport fault | `alan/teams/_pr_post.py::_dismiss_one_review`, called by `dismiss_stale_verifier_reviews` and `_pr_waiver_dismissal.dismiss_for_waiver` (X2.7 PR1) |
| `feedback(pr_number, *, forge_target, timeout)` | `gh api graphql` with the feedback query (`_FEEDBACK_QUERY`, in `forge_gh.py`), default identity | `alan/teams/_pr_feedback.py::_fetch_feedback_raw` (X2.7 PR1) |
| `unresolved_threads(pr_number, *, forge_target, as_verifier)` | `gh api graphql` with the review-threads query (`_REVIEW_THREADS_QUERY`) | `alan/teams/_pr_threads.py::fetch_unresolved_threads` (`as_verifier=True`) and `list_unresolved_threads` (`as_verifier=False`) (X2.7 PR1) |
| `resolve_thread(thread_id, *, forge_target, as_verifier)` | `gh api graphql` with the resolve mutation (`_RESOLVE_THREAD_MUTATION`) | `alan/teams/_pr_threads.py::resolve_thread_as` (`as_verifier=True`) and `resolve_review_thread` (`as_verifier=False`) (X2.7 PR1) |
| `resolve_thread_checked(thread_id, *, forge_target)` | `gh api graphql` with `_RESOLVE_THREAD_CHECKED_MUTATION` (`resolveReviewThread`, selecting `thread{isResolved,comments{totalCount}}`) and `-f threadId=`, run only under `secrets_store.verifier_gh_env(forge_target)`; when that env is empty the adapter returns `_NO_VERIFIER_CREDENTIAL` (a failed `ApiResult`, returncode -1) without spawning `gh` and never falls back to the ambient token the way `_verifier_env` does; a transport exception is not caught | `alan/teams/_pr_feedback.py::_resolve_and_verify`, which catches `OSError` and `TimeoutExpired` (P1.14 / D28) |
| `unresolve_thread(thread_id, *, forge_target)` | `gh api graphql` with `_UNRESOLVE_THREAD_MUTATION` (`unresolveReviewThread`, the same selection), under the same strict reviewer-only env and no-credential result as `resolve_thread_checked`; a transport exception is not caught | `alan/teams/_pr_feedback.py::_undo`, which catches `OSError` and `TimeoutExpired` (P1.14 / D28) |
| `repo_permission(login, *, forge_target, timeout)` | `gh api repos/{slug}/collaborators/{login}/permission -q .permission`, `login` percent-encoded with no safe characters, run under the ambient identity (the adapter passes no environment); returns `PermissionRead(outcome, permission, returncode, stderr)`: `ok` with the stripped value, `failed` on a non-zero exit or an empty value, `raised` (returncode -1) on a transport fault | `alan/teams/_pr_feedback.py::_read_permission` (P1.14 / D28) |
| `post_review(pr_number, payload, *, forge_target, as_verifier)` | `gh api -X POST /repos/{slug}/pulls/{n}/reviews --input <payload file>` | `alan/teams/_pr_post.py::_post_review_payload` (X2.7 PR1), reached through `_pr_post::post_pr_review_with_inline_comments` by `alan/teams/_verification_grounding.py::_post_cannot_verify_comment`, `alan/teams/_verification_review_post.py::_apply_legacy_pr_review` and `alan/teams/_verification_review_post.py::_apply_inline_pr_review` (X2.7 PR4) |
| `update_pr_body(pr_number, body, *, forge_target)` | `gh api -X PATCH /repos/{slug}/pulls/{n} --input <payload file>`, the payload the JSON object holding only `body`; run under the ambient identity (the adapter passes no environment); returns `ApiResult`; a transport exception is not caught | `alan/teams/_pr_body.py::amend_pr_body` (X2.7 PR12) |
| `reply_to_thread(thread_id, body, *, forge_target)` | `gh api graphql` with the reply mutation (`_THREAD_REPLY_MUTATION`, `addPullRequestReviewThreadReply`) and `-f threadId=` / `-f body=`, under the ambient identity; `forge_target` does not reach the argv, because the mutation addresses the thread by node id; returns `ApiResult`; a transport exception is not caught | `alan/teams/_copilot_resolution.py::_reply_outcome`, called by `post_reply` and the acknowledge rows (X2.7 PR12) |
| `comment_on_pr(pr_number, body, *, forge_target)` | `gh pr comment {n} --repo {slug} --body {body}`, under the ambient identity; returns `ApiResult`; a transport exception is not caught | `alan/teams/_copilot_resolution.py::_comment_outcome`, called by `post_pr_comment` and the comment-acknowledge rows (X2.7 PR12) |
| `open_prs(*, forge_target, limit, timeout)` | `gh pr list --repo {slug} --state open --json number,title,url --limit {limit}`, run as the transport identity; returns `RowsRead(outcome, rows, returncode, stderr)` | `alan/collision.py::_fetch_prs` (X2.7 PR8) |
| `workflow_runs(workflow, *, forge_target, limit, timeout)` | `gh run list --repo {slug} --workflow {workflow} --json databaseId,createdAt,status,conclusion --limit {limit}`, run as the transport identity; returns `RowsRead` | `alan/deploy_runs.py::fetch_deploy_runs` (X2.7 PR8) |
| `viewer_login(*, forge_target, as_verifier, timeout)` | `gh api user -q .login`, under the transport identity (`as_verifier=False`) or the verifier identity (`as_verifier=True`); returns `LoginRead(outcome, login, returncode, stderr)` | `alan/engine_preflight.py::_author_login` (`as_verifier=False`) and `_reviewer_login` (`as_verifier=True`) (X2.7 PR8); `alan/teams/_verifier_thread_resolution.py::_confirm_identity` (`as_verifier=True`) (X2.7 PR11) |
| `repo_file(repo_path, *, forge_target, timeout, ref=None)` | `gh api repos/{slug}/contents/{repo_path}`, `repo_path` percent-encoded with no safe characters (`quote(repo_path, safe='')`, same rule as `compare_base`'s ref); when `ref` is not `None` the adapter appends `?ref={ref}`, itself percent-encoded with no safe characters, so the argv is byte-identical to before this parameter existed whenever a caller omits it; run as the transport identity; returns `FileRead(outcome, text, returncode, stderr)`, `text` the raw undecoded API response body | `alan/engine_preflight.py::_check_dependabot_config` (X2.7 PR8, no `ref`); `alan/teams/_pr_merge_checks.py::_read_codeowners_text` (I1.8, `ref=<the PR's base branch>`) |
| `prs_by_head(head_branch, fields, *, forge_target, limit, timeout)` | `gh pr list --repo {slug} --head {head_branch} --state all --json {comma-joined fields} --limit {limit}`, run as the transport identity; `--state all` is pinned, not a parameter; returns `RowsRead`, absorbing a raised transport fault the way `open_prs` does | `alan/repo_scaffold.py::_find_existing_pr` (X2.7 PR9) |
| `create_pr(title, body, head_branch, *, forge_target, workdir, timeout)` | `gh pr create --repo {slug} --title {title} --body {body} --head {head_branch}`, run with `cwd=str(workdir)` (the scaffold's clone) as the transport identity; returns `ApiResult`; a transport exception is not caught | `alan/repo_scaffold.py::_create_scaffold_pr` (X2.7 PR9) |
| `create_pr_from_checkout(title, body, head_branch, *, forge_target, workdir, timeout)` | `gh pr create --title {title} --body {body} --base main --head {head_branch}`, run with `cwd=str(workdir)` (the worktree) under the ambient identity: the adapter resolves no credential and passes no environment; `forge_target` is accepted and unused, so there is no `--repo`; `--base main` is fixed inside the adapter; returns `ApiResult`; a transport exception is not caught | `alan/teams/implementation.py::_open_pr` (X2.7 PR25) |
| `pr_state(pr_number, *, forge_target, timeout)` | `gh pr view {pr_number} --repo {slug} --json state -q .state`, run as the transport identity; returns `ApiResult`; a transport exception is not caught | `alan/repo_scaffold.py::_view_scaffold_pr_state` (X2.7 PR9) |
| `default_branch(*, forge_target, timeout)` | `gh api repos/{slug}`, run as the transport identity; returns `str \| None`: the `default_branch` value, or `"main"` when the payload is an object whose `default_branch` is missing, empty or not a string; `None` on a non-zero exit, a transport fault, or a successful exit whose body is not a JSON object | `alan/repo_status.py::_fetch_main_ci`; `alan/recurring_forge.py::_default_branch` (X2.7 PR10) |
| `commit_check_runs(ref, *, forge_target, timeout)` | `gh api repos/{slug}/commits/{ref}/check-runs` (`ref` not percent-encoded, as the callers always sent it; no `per_page`); returns `RowsRead` over the response's `check_runs` array (`failed` on a non-zero exit or a non-object body, `ok` with `rows=None` when `check_runs` is missing or not a list, non-object items dropped) | `alan/repo_status.py::_fetch_main_ci` (X2.7 PR10) |
| `latest_workflow_run(workflow, *, forge_target, timeout, head_sha=None)` | `gh api repos/{slug}/actions/workflows/{workflow}/runs?per_page=1`, or `...runs?head_sha={head_sha}&per_page=1` when `head_sha` is not `None`; `workflow` (a file name or a numeric id) and `head_sha` percent-encoded with no safe characters; returns `RowsRead` over the response's `workflow_runs` array, by the same rules as `commit_check_runs` | `alan/repo_status.py::_workflow_suite_id` (`head_sha` set); `alan/recurring_forge.py::_fetch_latest_run` (`head_sha` omitted, `workflow=str(workflow_id)`) (X2.7 PR10) |
| `workflows(*, forge_target, timeout)` | `gh api repos/{slug}/actions/workflows`, run as the transport identity; returns `RowsRead` over the response's `workflows` array, by the same rules as `commit_check_runs` | `alan/recurring_forge.py::_list_workflow_files` (X2.7 PR10) |
| `prs_by_author(author, fields, *, forge_target, state, limit, timeout)` | `gh pr list --repo {slug} --author {author} --state {state} --json {comma-joined fields} --limit {limit}`, run as the transport identity; `state` is `Literal["open", "all"]`; returns `RowsRead` with `_rows_read`'s rules, so a failed listing (`failed` / `raised`) stays distinct from an empty one (`ok`, `rows == ()`) and from an unparseable one (`ok`, `rows=None`) | `alan/repo_status.py::_fetch_open_prs` (`"@me"`, `("number",)`, `"open"`, limit 100); `alan/recurring_forge.py::_latest_dependabot_pr` (`"app/dependabot"`, `("createdAt", "url")`, `"all"`, limit 1) (X2.7 PR10) |
| `open_issues_by_title(title, *, forge_target, label)` | `gh issue list --repo {slug} --label {label} --state open --search "in:title {title}" --json number,title`, the search string one argv element built by the adapter, run as the transport identity with `GH_TIMEOUT_S` (no timeout parameter); returns `RowsRead` with `_rows_read`'s rules: `failed` on a non-zero exit, `raised` on any `OSError` or `subprocess.TimeoutExpired`, `ok` with `rows=None` for an unparseable or non-list payload, non-object items dropped. Coexists with the title-only `search_open_issues`, which returns no issue number and whose other callers it is not widened for | `alan/teams/unblock_issue.py::find_open_issue` (X2.7 PR13) |
| `create_numbered_issue(title, body, *, forge_target, label)` | `gh issue create --repo {slug} --title {title} --body {body} --label {label}`, run as the transport identity with `GH_TIMEOUT_S`; returns `ApiResult` whose `stdout` carries the issue URL the caller parses for a number; a transport exception is not caught. Coexists with the boolean `create_issue`, which discards the output and folds faults, because the unblocker needs the URL and must tell a timed-out create (which may have landed) from a refusal; `create_issue` is not widened | `alan/teams/unblock_issue.py::create_issue` (X2.7 PR13) |
| `comment_on_issue(number, body, *, forge_target)` | `gh api --method POST repos/{slug}/issues/{number}/comments --input <payload file>`, the payload the JSON object holding only `body`, run as the transport identity; returns `ApiResult`; a transport exception is not caught | `alan/teams/unblock_issue.py::comment` (X2.7 PR13) |
| `prs_by_head_prefix(prefix, fields, *, forge_target, state, limit, timeout)` | `gh pr list --repo {slug} --search head:{prefix} --state {state} --json {comma-joined fields} --limit {limit}`, run under the ambient identity (the adapter passes no environment); `state` is `Literal["open", "all"]`, the closed type `prs_by_author` uses, so the two list shapes share one operation; returns `RowsRead` with `_rows_read`'s rules | `alan/teams/triage_pregate.py::_fetch_raw_prs` (`"teams/"`, `("number", "state", "headRefName")`, `"all"`, limit 100) and `_fetch_open_pr_rollups` (`"teams/"`, `("number", "reviewDecision", "mergeStateStatus", "statusCheckRollup")`, `"open"`, limit 30), both against the default engine's repository with a 30 s bound (X2.7 PR13) |
| `latest_branch_run(workflow, branch, *, forge_target, timeout)` | `gh run list --repo {slug} --workflow {workflow} --branch {branch} --limit 1 --json databaseId,status,conclusion,headSha`, `--limit` before `--json`, run under the ambient identity; returns `RowsRead` with `_rows_read`'s rules. `workflow_runs` is not reused: it has a different field set and no branch filter | `alan/teams/triage_pregate.py::_latest_main_run`, called for each registered engine's main-CI workflow and, when configured, deploy workflow, with `branch="main"`, against that engine's own repository and a 30 s bound (X2.7 PR13) |
| `pr_head_branch(pr_number, *, forge_target)` | `gh pr view {n} --repo {slug} --json headRefName -q .headRefName`, under the ambient identity with `GH_TIMEOUT_S` (no timeout parameter); returns `ApiResult` carrying the raw, unstripped stdout; a transport exception is not caught | `alan/teams/_pr_branch.py::resolve_existing_branch` (X2.7 PR14) |
| `request_branch_update(pr_number, *, forge_target)` | `gh pr update-branch {n} --repo {slug}`, under the ambient identity; returns `ApiResult`; a transport exception is not caught. Only the attempt: the audit rows, the conflict classification and the `UPDATED` / `CONFLICT` / `FAILED` mapping stay in `_pr_branch.update_pr_branch_outcome` | `alan/teams/_pr_branch.py::update_pr_branch_outcome` (X2.7 PR14), which the merge team's `_behind_during_merge_outcome` and merge recovery's `_recover_behind_plus_transient` call directly (X2.7 PR21) |
| `pr_open_state(pr_number, *, forge_target)` | `gh pr view {n} --repo {slug} --json state -q .state`, under the ambient identity with `GH_TIMEOUT_S`; returns `ApiResult`; a transport exception is not caught. Runs the same command as `pr_state` but as a different identity and with no timeout parameter, so neither operation is reused for the other | `alan/teams/_pr_branch.py::pr_is_open` (X2.7 PR14) |
| `open_pr_titles(*, forge_target)` | `gh pr list --state open --json number,title --limit 100` with no `--repo`, under the ambient identity, so the listing answers for the checkout's own repository; `forge_target` is accepted and never reaches the command; returns `ApiResult`; a transport exception is not caught. Coexists with `open_prs`, which names the repository, also asks for `url` and runs as the transport identity | `alan/teams/_pr_branch.py::list_open_prs`, called by `alan/predispatch_scan.py` (X2.7 PR14) |
| `commit_check_runs_page(sha, *, forge_target)` | `gh api repos/{slug}/commits/{sha}/check-runs?per_page=100`, under the ambient identity with `GH_TIMEOUT_S` (no timeout parameter, no `env=` keyword); returns `ApiResult` carrying the raw stdout; a transport exception is not caught. Coexists with `commit_check_runs`, which sends no `per_page`, runs as the transport identity and returns parsed `RowsRead` | `alan/teams/_pr_branch.py::_iter_check_runs`, called by `fetch_failing_check_details`, `rerun_external_gate`, `_pr_ready._gather_quarantine_inputs`, `_pr_sonar._select_sonar_check_run`, `_pr_sonar.retrigger_sonar_analysis` and `_verification_gates._head_has_pending_check_runs` (X2.7 PR15) |
| `check_run_annotations(check_run_id, *, forge_target, timeout)` | `gh api repos/{slug}/check-runs/{check_run_id}/annotations?per_page=100`, under the ambient identity (the adapter passes no `env=` keyword); returns `ApiResult`; a transport exception is not caught | `alan/teams/_pr_branch.py::fetch_check_run_annotations` with `as_transport=False`, reached by `_pr_sonar.list_sonarcloud_annotations` (X2.7 PR15) |
| `transport_check_run_annotations(check_run_id, *, forge_target, timeout)` | the same command line as `check_run_annotations`, run as the transport identity (resolved inside the adapter; no `env=` value when no credential resolves); returns `ApiResult`; a transport exception is not caught. A separate operation because the two identities are two call patterns | `alan/teams/_pr_branch.py::fetch_check_run_annotations` with `as_transport=True`, reached by `fetch_check_annotations` (gate evidence) and `fetch_failing_e2e_tests` (X2.7 PR15) |
| `rerequest_check_suite(check_suite_id, *, forge_target)` | `gh api -X POST repos/{slug}/check-suites/{check_suite_id}/rerequest`, under the ambient identity with `GH_TIMEOUT_S`; returns `ApiResult`; a transport exception is not caught | `alan/teams/_pr_branch.py::_rerequest_check_suite`, called by `rerun_external_gate` and `_pr_sonar.retrigger_sonar_analysis` (X2.7 PR15) |
| `rerun_failed_jobs(run_id, *, forge_target)` | `gh run rerun {run_id} --failed --repo {slug}`, under the ambient identity with `GH_TIMEOUT_S`; returns `ApiResult`; a transport exception is not caught | `alan/teams/_pr_branch.py::_rerun_workflow_run`, called by `rerun_external_gate` (X2.7 PR15) |
| `status_check_rollup(pr_number, *, forge_target, timeout)` | `gh pr view {n} --repo {slug} --json statusCheckRollup`, run as the transport identity; returns `ApiResult` carrying the raw JSON; a transport exception is not caught | `alan/teams/_pr_branch.py::fetch_failing_job_ids`, called by `fetch_failing_e2e_tests` and `dependency_sweep._detect_upstream_block` (X2.7 PR15) |
| `failed_job_log(job_id, *, forge_target, timeout)` | `gh run view --repo {slug} --job {job_id} --log-failed`, run as the transport identity; returns `ApiResult` carrying the raw log; a transport exception is not caught | `alan/teams/_pr_branch.py::fetch_failed_log` (X2.7 PR15) |

Seven `gh pr` argv methods build their `gh` command directly against `_pr_branch.run_subprocess` —
never `_pr_branch.gh_checked` (deleted in X2.7 PR14). Three of them — `read_pr`, `read_checks`, `merge_pr` — do so because
`gh_checked` raises on every classified `rc != 0` while the callers of `read_pr` and `merge_pr`
(`_resolve_head_base`, `_execute_merge`'s policy-refusal and head-mismatch branches,
`_fetch_pr_base_sha`) deliberately swallow a non-zero return and decide what to do themselves;
routing through `gh_checked` would have been a silent behaviour change for them. The fourth,
`open_prs` (X2.7 PR8), bypasses it for a related but distinct reason: `_rows_read` needs to observe
the raw `ok`/`failed`/`raised` outcome itself, which `gh_checked`'s boolean contract cannot carry.
The last three — `prs_by_head`, `create_pr`, `pr_state` (X2.7 PR9) — are the onboarding scaffold's.
`prs_by_head` follows `open_prs`'s reasoning exactly: it goes through `_rows_read` for the same
raw-outcome need. `create_pr` and `pr_state` call `_pr_branch.run_subprocess` directly because each
propagates a transport fault straight to its caller, and `gh_checked`'s raise-on-every-classified-
failure contract does not distinguish a transport exception from a non-zero exit the way each of
those two callers needs to.
`read_pr` never emits `-q`, and cannot: the port takes a tuple of field names and returns the parsed
object, so a scalar extracted server-side is inexpressible through it. `resolve_existing_branch`'s
`gh pr view --json ... -q ...` reads stay on their direct call for that reason, and
`_pr_ready._fetch_pr_base_sha` — which used to pass `-q .baseRefOid` — now asks `read_pr` for
`("baseRefOid",)` and extracts the string in the caller: `fields.get("baseRefOid")` when it is a
`str`, stripped, `None` when it is empty, absent, JSON-null or not a string. `pr_state`'s own
`-q .state` is a separate operation built for one caller's need, not an exception carved into
`read_pr`'s never-emits-`-q` rule.

`repo_file`'s `text` is the raw JSON envelope the contents API returns, not file contents: the
caller decodes it. `alan/recurring_forge.py::_decode_contents` (called by `_fetch_file_text_or_404`,
which maps `repo_file`'s `absent` outcome to a confirmed absence) and
`alan/teams/_pr_merge_checks.py::_decode_contents` each independently parse that envelope
(`{"content": <base64>, "encoding": "base64"}`) and base64-decode `content` themselves — the port
returns the undecoded body uniformly regardless of caller, so a caller that wants the workflow-file
or CODEOWNERS text has to unwrap it.

## What PR3 migrated

Seven call sites, each swapped for its port method with the observable outcome unchanged:

| # | site | port method |
|---|---|---|
| 1 | `pr_merge._behind_during_merge_outcome` | `update_branch` |
| 2 | `pr_merge._execute_merge` | `merge_pr` |
| 3 | `pr_merge._gh_pr_checks` | `read_checks` |
| 4 | `_pr_ready._gh_pr_view_readiness` | `read_pr` |
| 5 | `_pr_ready._fetch_pr_base_sha` | `read_pr` |
| 6 | `_pr_ready._gather_quarantine_inputs` | `changed_files` |
| 7 | `merge_recovery._recover_behind_plus_transient` | `update_branch` |

`alan/merge_gate.py` has no covered site (its only forge call is the failing-e2e read) and
`_pr_reconcile.py` was PR2's, so neither changed. Three of the seven carry shaping cost beyond a
call swap, because the port returns structured results rather than process output:

- **`merge_pr` returns `MergeAttempt(ok, returncode, stderr)`, not a `CompletedProcess`.**
  `_handle_merge_failure`, `_merge_failure_outcome`, `_is_policy_refusal`,
  `_is_head_mismatch_refusal` and `_escalation_diagnostics` now take a `MergeAttempt` and read
  `.returncode`/`.stderr`. The port normalises `stderr` to `str`, so the callers' `or ""` guards
  went away. The `FileNotFoundError`/`subprocess.TimeoutExpired` → `ForgeTransientError` mapping
  stays at the call site.
- **The readiness parsers take the parsed field dict, not stdout.** `_parse_readiness_payload` and
  `_parse_verifier_readiness_payload` accept `_JsonObject`, `_resolve_readiness`'s `parser` is
  `Callable[[_JsonObject], PrReadiness | None]`, and `_READINESS_FIELDS`/`_VERIFIER_READINESS_FIELDS`
  are tuples. `GhForge.read_pr` folds unparseable stdout and non-object JSON into `fields={}` with
  `ok=True`, so each parser opens with `if not payload: return None`: that guard is what preserves
  the "gh pr view returned unparseable JSON" transient readiness (a `ReadinessUnavailable`) instead of
  letting it degrade into a real `PrReadiness` the merge gate would read as a block. The
  `pr_readiness_check` audit row keeps its `gh_ok: false` key and constant defaults for that case. The one
  behaviour change is deliberate and small: a payload that is a literal empty object `{}` used to
  parse into a not-ok, non-transient readiness with the "no check results" reason and now takes the
  transient path too. A real `gh pr view` never returns `{}` for the readiness fields, so no
  observed input changes.
- **`read_checks` moved the `_parse_checks_json` work into the adapter.** `_gh_pr_checks` keeps the
  decision logic only: `gh pr checks` exits non-zero for pending/failed rollups while still printing
  valid rows, so rows are trusted whenever there are any (`rc=8` with a real `FAILURE` row is the
  canonical "checks failed" signal). With no rows and `ok` false, rate-limit/network failures raise
  through `_forge_errors.raise_for_infra` for a dispatcher-level retry instead of burning the poll
  budget, and anything unclassified keeps the conservative `[PENDING]` fallback so the caller keeps
  polling rather than misreading the poll as `ready`. `_check_item` still normalises each row to
  string `name`/`state`. (This is the reasoning the deleted `_gh_pr_checks` docstring carried.)

**One name for the changed-files helper.** `forge_gh.py` reaches the diff through
`_pr_branch.fetch_diff_files` (a module-level assignment at `_pr_branch.py`'s tail) while
`_pr_ready` used to call the private `_fetch_diff_files` behind a suppression — one function, two
names. After the migration `_pr_ready` names neither: it calls `active_forge().changed_files`, and
the adapter keeps the public alias. The alias has other live consumers, so it is a real module
attribute rather than an unused `from x import y as y` facade and the re-export-facade gate never
applied to it.

## What PR3 left for X2.7

The family's other forge calls have no covered port operation and are unchanged: the flake-guard
head read (`_pr_branch.fetch_flake_guard_head_sha`), check reruns, the failing-e2e read, the
branch-update outcome read (`update_pr_branch_outcome`, which returns a richer enum than the port's
`bool`), the check-run listing (`_iter_check_runs`, on the port as `commit_check_runs_page` since X2.7 PR15), stale-review dismissal, the PR-state read and
the review-thread GraphQL read. `head_sha`, `reviews` and `dismiss_review` existed on the port but
stayed **unwired** in PR3: their callers reach them through audit-wrapped or callback-carrying
helpers (`fetch_flake_guard_head_sha`, `dismiss_stale_verifier_reviews`) whose audit rows and
credential handling the port does not model, so wiring them was a re-shaping decision that belonged
with the X2.7 work that owns those helpers rather than a call swap in PR3. Adding new port
methods there was ruled out for the same reason — the later item would re-shape them. X2.7 PR1
wired `reviews` and `dismiss_review` and added the four query-shaped operations; `head_sha` remains
unwired. X2.7 PR14 and PR15 then moved the head read, the check-run listing, the check reruns and
the failing-job reads onto the port ("X2.7 PR15: check-run reads and gate reruns" below).

**What is enforced.** `tests/test_forge_seam_merge_family.py` fails when any of five components
constructs a `gh pr view|checks|merge` argv or references `update_pr_branch`, `fetch_diff_files` or
`_fetch_diff_files`. Its table is the requesting brief's five — `pr_merge.py`, `_pr_ready.py`,
`merge_recovery.py`, `merge_gate.py`, `_pr_reconcile.py` — which differs from the five "family
modules" defined under "Boundary" below (`_pr_merge_conflict.py` and `_pr_waiver_dismissal.py` in
place of `merge_recovery.py` and `merge_gate.py`); the test ids say `brief-five-…` so the two lists
cannot be confused. The gate is a forbidden-name table with exact-name matching, not a general
ban on forge calls: `merge_recovery.py` legitimately calls `update_pr_branch_outcome` and
`pr_merge.py` legitimately calls `fetch_failing_e2e_tests`, both X2.7 seams that substring or
category matching would flag.

## Boundary and its consequence for the item's targets

The port covers the merge family's **direct** forge surface only: the argv sites the five family
modules (`pr_merge.py`, `_pr_ready.py`, `_pr_reconcile.py`, `_pr_merge_conflict.py`,
`_pr_waiver_dismissal.py`) build themselves, and the named `_pr_branch` / `_pr_post` helpers those
modules call directly. That is the definition of "the merge family" the port's method table was
designed against. The PR3 brief and the structural gate use a second five (`pr_merge.py`,
`_pr_ready.py`, `merge_recovery.py`, `merge_gate.py`, `_pr_reconcile.py`), because the covered call
sites PR3 migrated live in `merge_recovery.py` and `_pr_ready.py` and it had to name the files it
would touch; the gate enforces the brief's five (see "What is enforced"). `_pr_merge_post_update.py` is covered-but-not-counted pending the requester's
ruling — it does not build its own `gh` argv today, but its role in the family's control flow is
close enough that a future port method could plausibly serve it.

Operations the family reaches only *indirectly*, through another helper module, are not on this
port: calls inside `_pr_closing.py`, `_pr_flake_ledger.py`, `_pr_sonar.py`,
`_verification_gates.py`, `merge_recovery.py`, `proactive_rebase.py`, `collision.py`,
`dispatcher.py`, `verification.py`, and `_clause_audit.py` are that helper module's calls, not the
family's, and belong to X2.7. The port is additively extensible there — nothing here forecloses a
later method.

**Consequence for the item's numeric targets:** the "≤ 20 setattr sites across the 25 merge-family
test files" / "≤ 40 forge-family patches" targets are re-scoped to sites that patch the five family
modules' *own* forge calls. Helper-module patch sites are counted, listed, and attributed to X2.7 —
they are not this port's remaining-patch-site problem to solve. This is a definition-of-done
amendment the requester records separately in the roadmap's Status PR; this document states it
plainly.

X2.3's aggregate **"net ≤ +100 across the first three PRs"** clause is also a consequence of this
same below-`teams` layering constraint, and it is **infeasible**: this PR alone measures +131 net
backend lines because the port must rebuild, at its own layer, what `gh_checked` and the
`_forge_errors` classifiers already do one layer up, and PRs 2–3 add the test fake, its registry,
and the caller migration on top of that base. R29 (2026-09-20) amended this clause to ≤ +281 —
PR1's measured +131 plus the 150-line allowance below — alongside the setattr/patch-site re-scope
above. Separately, the repository-wide backend
ceiling was `BACKEND_LOC_MEASURED + BACKEND_LOC_BUFFER + sum(BACKEND_LOC_ALLOWANCES.values())`
(R29; since removed). PR2 and PR3 of this port landed under the 150-line
`X2.3:forgeport-merge-family` allowance that ruling had granted; neither the buffer nor the measured pin
was raised in a roadmap-item PR, and the driver's Status PR was to re-pin the measurement and retire the
allowance when PR3 landed. PR3 measured **−47 non-blank backend lines** (66,513 → 66,466 over
`alan/`): the migration deletes more shaping (`_parse_checks_json`, the `json.loads` parser
prologues, the 10-line `_gh_pr_checks` docstring, argv construction) than it adds. The two
constraints were independent: the per-PR budget was the requester's to re-scope, the ceiling's parts
were not.

## The substitution point

`alan/forge.py` also holds the port's substitution point: a module-level holder (`_active_forge`),
a setter `set_active_forge(forge: ForgePort) -> None`, and a total accessor
`active_forge() -> ForgePort` that raises `RuntimeError` rather than returning an optional —
every caller gets a forge or a loud failure, never a branch to test for absence. `GhForge` is
installed exactly once per process, from `alan/server.py`'s `lifespan`, because `alan.forge`
sits below `alan.forge_gh` in the layers list and cannot construct the adapter itself; module-import
installation would fire for every test importing `alan.server` and make import order load-bearing,
and a per-job installation would be noise, since every port method already takes its `ForgeTarget`
as a parameter. The test suite installs a strict `FakeForge` (`tests/fakes/forge.py`) before every
test via an autouse fixture, so a call a test did not explicitly stub raises immediately instead of
returning a silent default.

## Layering

`alan.forge` sits in `pyproject.toml`'s `[tool.importlinter]` "Layered architecture" layers list
between `config_files` and `engine_config` — the leanest legal slot immediately above the
`engine_config` types (`ForgeTarget`) the port's methods are typed against, and far below `teams`.
`alan.forge_gh` sits between `project_roadmap` and `teams` — just above `teams`, because it
delegates *into* `alan.teams._pr_branch` (its transport helpers and `verifier_gh_env`) and
therefore must be a higher layer than it. It no longer imports `alan.teams._pr_post`: since X2.7
PR1 the call direction is inverted, and `_pr_post` reaches the forge through `active_forge()`. The port itself imports nothing from `alan.teams` or above, type-only imports
included: it could not, since it sits below `teams` in the list, and importing from a lower-indexed
(higher) layer would be the layering violation this whole item exists to eliminate. This is why the
port's methods return primitives and the three small frozen result types defined in `forge.py`
itself, never a `teams`-owned type — `alan.teams._pr_branch.BranchUpdateOutcome`, for instance,
cannot be named from `alan/forge.py`. The port therefore exposes only the attempt (`request_branch_update`,
returning `ApiResult`); callers that need the classified outcome call `_pr_branch.update_pr_branch_outcome`
directly, because `BranchUpdateOutcome` is a `teams`-owned type (X2.7 PR21). The same constraint decides where the GraphQL query text lives: `_FEEDBACK_QUERY`,
`_REVIEW_THREADS_QUERY` and `_RESOLVE_THREAD_MUTATION` are module constants of `alan/forge_gh.py`,
because a port whose caller owns a query string is not a domain port.

## Error contract

The port classifies nothing. Thirty of its fifty-two operations propagate whatever their delegate or the transport raises — P1.14's `resolve_thread_checked` and `unresolve_thread` (`repo_permission` swallows, through `_attempt`, into `outcome="raised"`), X2.7 PR12's `update_pr_body`, `reply_to_thread` and `comment_on_pr`, X2.7 PR13's `create_numbered_issue` and `comment_on_issue`, and X2.7 PR14's four (`pr_head_branch`, `request_branch_update`, `pr_open_state`, `open_pr_titles`) plus the retyped `head_sha` among them, X2.7 PR15's seven (`commit_check_runs_page`, `check_run_annotations`, `transport_check_run_annotations`, `rerequest_check_suite`, `rerun_failed_jobs`, `status_check_rollup`, `failed_job_log`) and X2.7 PR25's `create_pr_from_checkout` ("X2.7 PR12: PR body, thread reply and PR comment", "X2.7 PR13: unblocker issues and triage reads", "X2.7 PR14: PR view reads and branch update", "X2.7 PR15: check-run reads and gate reruns" and "X2.7 PR25: the implementation team opens its pull request through the forge port" below);
`reviews`, `dismiss_review`, `changed_files` (X2.7 PR14: it moved from a delegate that swallowed a fault to an adapter that swallows it itself), X2.7 PR3's four, X2.7 PR6's `open_prs_by_author`, X2.7 PR8's four (`open_prs`, `workflow_runs`, `viewer_login`, `repo_file`), X2.7 PR9's `prs_by_head`, X2.7 PR10's five (`default_branch`, `commit_check_runs`, `latest_workflow_run`, `workflows`, `prs_by_author`) and X2.7 PR13's three (`open_issues_by_title`, `prs_by_head_prefix`, `latest_branch_run`) swallow, for the reasons in the X2.7 PR1, PR3, PR8, PR9, PR10 and PR13 exception tables above and below. The
six `gh pr` argv methods — `read_pr`, `read_checks`, `merge_pr`, `create_pr`, `pr_state` (X2.7 PR9) and
`create_pr_from_checkout` (X2.7 PR25) — call `_pr_branch.run_subprocess` with no `try`/`except`: a `FileNotFoundError` or
`subprocess.TimeoutExpired` raised by the underlying `subprocess.run` call propagates straight
through `GhForge` to the caller, unchanged; `compare_base` follows the same rule. On a completed process, the adapter decodes JSON and
returns a frozen result carrying `returncode` and `stderr` verbatim — it does not call
`alan.teams._forge_errors.classify_forge_failure` or `raise_for_infra`, because that classification
taxonomy lives in `alan.teams._forge_errors`, a `teams`-layer module the port cannot import. The
`returncode`/`stderr` fields on `PrRead`, `ChecksRead`, and `MergeAttempt` are the visible price of
that constraint: every migrated caller keeps its own existing `_forge_errors` handling, unchanged,
reading those two fields instead of a `CompletedProcess`. Each caller also keeps its own transport
fault handling: `_resolve_head_base` and `_fetch_pr_base_sha` swallow
`FileNotFoundError`/`subprocess.TimeoutExpired` and return `None`; `_gh_pr_view_readiness` and
`_execute_merge` re-raise them as `ForgeTransientError`; `_evaluate_checks` catches them around
`_gh_pr_checks` and reports an `error` outcome. PR3 settled the question PR2 left open — whether the
port should absorb transport faults for the whole family — as no: the callers disagree about what a
fault means (absent value, transient retry, error outcome), and a port-level swallow would erase
that distinction.

## Identity on a copy

A *copy* is an install with an installed default-engine profile: `default_profile.resolved().source is Source.profile`. The gate is evaluated on every call, through the module attribute, before any credential lookup. The owner's install (`Source.owner`) and an unconfigured install (`Source.unconfigured`) are not copies, and every operation there keeps the argv, keywords, environment and credential-lookup pattern recorded in the rest of this document: wherever the method table and the PR sections below say "ambient", they describe those installs only. `tests/test_forge_gh_owner_identity.py::test_owner_install_forge_operations_run_exactly_as_before` sweeps all fifty-two operations with the owner source set, a stored transport record and the fallback variable both resolvable, and pins each command line and environment to the literal values recorded before this change (`tests/forge_identity_table.py`).

**One seam.** Every `gh` spawn that a `GhForge` operation reaches runs through `_forge_run` in `alan/forge_gh.py`, except the strict paths named below. On the owner's install and an unconfigured install it forwards the call-site argv and keywords verbatim to `process_runner.run_subprocess`. On a copy it ignores the call-site environment and carries the target repository's transport credential (`secrets_store.transport_credential`: the stored record, then the target's `credential_env_name`) as `GH_TOKEN`; the argv, working directory and timeout are untouched. No operation gains a parameter, so the credential rule above still holds: the adapter derives the identity itself.

**Reviewer intent.** Five operations act as the reviewer: `reviews`, `dismiss_review`, and `post_review`, `unresolved_threads` and `resolve_thread` with `as_verifier=True`. On a copy they carry the reviewer credential when `secrets_store.verifier_gh_env` resolves one (the stored reviewer record, then the same fallback variable) and otherwise the transport credential, never ambient. A blank reviewer value counts as absent. When only the shared fallback variable is set, the reviewer and transport credentials are the same token.

**Strict operations are unchanged.** `viewer_login` still reports `no_credential`, without spawning `gh`, when its credential does not resolve, and `resolve_thread_checked` and `unresolve_thread` still return `_NO_VERIFIER_CREDENTIAL` without a reviewer credential; none of them falls back to the transport credential. Preflight's `reviewer_distinct_from_author` finding is therefore the same on a copy without a reviewer credential as on the owner's install (`unknown`, "could not resolve both author and reviewer identity").

**Refusal without a credential.** On a copy with no credential to carry, `_forge_run` does not spawn `gh`: the machine's own `gh` sign-in would otherwise answer, because `process_runner.run_subprocess` merges the environment over `os.environ`. It returns `CompletedProcess(argv, -1, "", "no transport credential")` and logs one warning naming the repository and the missing credential, never a value. The existing result mappers turn that return code into each operation's own failure result (`ok=False`, `outcome="failed"`, `None`, `()` or `False`). The message names no repository, so `classify_forge_failure` reads it as `unknown` rather than a rate limit, and no pause or retry follows. `tests/test_forge_gh_copy_no_credential.py` sweeps every operation with the runner patched to fail the test if it is reached.

**Ruling (b) is lifted for copies only.** `create_pr_from_checkout` carries the transport credential on a copy, with its command line otherwise unchanged (still no `--repo`, fixed `--base main`); see "X2.7 PR25" below. The owner ruling stands unchanged for the owner's install and an unconfigured install.

**Restart.** `default_profile.resolved()` is cached for the life of the process, so adding or removing an installed profile changes the gate only after a restart.

**Known limitations on a copy.**

- `rerequest_check_suite` cannot succeed with a fine-grained token: GitHub reserves checks write access to GitHub Apps. The call carries the transport credential and fails the way it would for any token lacking that access.
- `open_pr_titles` and `create_pr_from_checkout` have no `--repo`, so `gh` resolves the repository from the working directory's remote; the credential is the target's, but the argv does not name the target.
- Copilot thread resolution always targets the default repository; that is not fixed here.
- An engine registered without a forge reads through the default forge target (`forge_target_for_workspace`), so its calls carry the default engine's credential.
- Outside the port and not routed through the seam: branch-protection compare and apply (already compliant), the setup-time `gh` calls in `setup_sonar` and `github_repos`, and workers' own use of `gh`.

## X2.7 PR1: the query-shaped operations

X2.7 moves the remaining forge-family callers off subprocess-transcript
tests and onto this port. Its first PR migrated three caller surfaces — the pull-request feedback
reader (`_pr_feedback`), the review-thread reader and resolver (`_pr_threads`) and the review
poster and dismisser (`_pr_post`) — whose seven forge sites are all `gh api`, none `gh pr`. That is
why this was a port-design change and not a call swap: the eight existing methods are `gh pr`-shaped,
these are REST and GraphQL. The PR wired the two operations PR3 left unwired (`reviews`,
`dismiss_review`) by inverting them — the argv now lives in `forge_gh.py` and `_pr_post` is a port
caller, because wiring them while they still delegated into `_pr_post` would have built an import
cycle and left the argv in `teams`, so the transport count could never reach zero — and added four
operations.

**One new result type.** `ApiResult(ok, stdout, returncode, stderr)` is shared by all four new
operations. The callers disagree completely about what a failure means (`_fetch_feedback_raw`
classifies by return code and stderr and raises `ForgeRateLimited`/`ForgeTransientError`;
`list_unresolved_threads` audits and returns `()`; `fetch_unresolved_threads` returns `None`;
`resolve_thread_as` reports a capped `error`), so a raw-ish result is the only shape that keeps
every one of those semantics with the callers' own handling untouched. The port still classifies
nothing and still returns no `teams`-owned type.

**The credential rule — no port operation accepts an environment or credential mapping, ever.**
One caller needs verifier identity (`resolve_thread_as`, `fetch_unresolved_threads`, the review
post from `_verification_review_post`) and another does not (`resolve_review_thread`,
`list_unresolved_threads`); a named boolean, `as_verifier`, distinguishes them and the adapter
derives `_pr_branch.verifier_gh_env(forge_target)` itself (`_verifier_env`), exactly as `reviews`
and `dismiss_review` already did. An empty derived environment collapses to `None`, which
`_pr_branch.run_subprocess` treats identically to an empty mapping. Exposing a mapping here would
make every later caller family inherit a transport-shaped parameter and stop this being a domain
port. `tests/test_forge_gh_transport_ops.py` pins it: no operation on the port or the adapter takes
a parameter named `env`, `method`, `path`, `endpoint`, `credentials`, `headers` or `token`, and none
is typed as a string-to-string mapping. `post_pr_review_with_inline_comments` takes `as_verifier` as
a keyword-only argument in the slot the old positional `env` occupied, so a missed call site fails
at type-check time instead of silently binding a boolean into the old position.

**No generic passthrough.** An operation parameterised by HTTP method and path would serve many
sites at once and is forbidden: a test would then assert a path string — asserting command shape
with extra steps, which is the premise this item exists to remove. Each operation names a domain
action and owns its own argv.

**`resolve_review_thread` and the latent default.** The resolve mutation addresses a GraphQL node
id and never reads owner or name, so `forge_target` is inert at `as_verifier=False`;
`resolve_review_thread(thread_id)` passes `DEFAULT_FORGE_TARGET` and a test pins that the adapter
emits an identical argv for two different forge targets. Threading a real target through
`_copilot_resolution` is deferred — it is outside the three surfaces.

**Exception contract.**

| operation | on a non-zero exit | on `FileNotFoundError` / `TimeoutExpired` |
|---|---|---|
| `reviews` | `None` | swallowed inside the adapter, `None` |
| `dismiss_review` | `False` | swallowed inside the adapter, `False` |
| `feedback`, `unresolved_threads`, `resolve_thread`, `post_review` | `ApiResult(ok=False, ...)` | propagates; every caller already has its own `except` |
| `pr_patch` (X2.7 PR3) | `None` | swallowed inside the adapter, `None` |
| `search_open_issues` (X2.7 PR3) | `()` | swallowed inside the adapter, `()` |
| `create_label` (X2.7 PR3) | `False` | swallowed inside the adapter, `False` |
| `create_issue` (X2.7 PR3) | `False` | swallowed inside the adapter, `False` |

`reviews` and `dismiss_review` swallow because that is where the old behaviour lived
(`fetch_review_stream` and `_dismiss_one_review` swallowed), and their `str | None` and `bool`
contracts predate this PR. The four new operations propagate because each migrated caller already
catches exactly the faults it cares about, so failure handling stays byte-identical.

**The enumeration.** `tests/forge_argv.py` and `tests/test_forge_argv_enumeration.py` record which
production modules still assemble a `gh` argument vector. It is derived by parsing source rather
than by text search: a wrapped or helper-returned vector is invisible to a line grep (a literal
`["gh"` search finds 15 files, a line-continuation-tolerant one finds 30, and three of this PR's own
modules match neither), so the walker visits every list or tuple literal under `alan/` and flags
those whose first element is the literal `"gh"`. The assertion is exact-set equality against
`RECORDED_ARGV_MODULES` — not membership — so it fails both when an unlisted module builds a vector
and when a listed one stops, and the failure message names which direction. `alan/forge_gh.py`
is named in `ADAPTER_MODULES` and exempt, because the adapter is where argv is supposed to live; the
final X2.7 PR reconciles the recorded set to empty. The predicate is `gh`-prefix only, which keeps
`_pr_push.py` (all seven of its sites are `git`, and the version-control port is X2.19) out by
construction. `scripts/` is outside the walk on purpose: `scripts/alan_forge_contract_check.py`
keeps its own argv because the canary must exercise the raw `gh api graphql` contract its
three-outcome classifier is built on, and it imports `_FEEDBACK_QUERY` from `alan.forge_gh` rather
than copying it. The module is deliberately **not** a `MEASURED_BASELINE_PATHS` entry: it fails in
both directions, so it is a drift gate and not a tightening-only ratchet, and the ratchet-direction
detector would misread a legitimate removal as loosening. The same test asserts the three migrated
modules call none of `run_subprocess`, `run_gh_api_json_input` or `gh_checked` — all three transport
entry points, not only the one the item's text names. `_pr_post.py` still imports `_pr_branch` for
`verifier_identity()` (and the `VerifierIdentity` type) and `_fetch_pr_head_sha`, neither of which is one of those entry points.

**The self-updater's GitHub module is the second adapter.** `alan/self_update_github.py` is the
second `ADAPTER_MODULES` entry, and it earns the exemption for the same reason
`scripts/alan_forge_contract_check.py` keeps its own argv: it is a standalone program that sits
*below* the forge port, so it cannot go through the port. The self-updater (`docs/architecture/self_update.md`)
runs under a copy's own interpreter with a pinned stdlib-only import closure and lives in the
import layers beneath `forge_gh`, `teams` and `queue`; importing `forge_gh` would pull the forge
port, its contract types and everything they import into the one program whose job is to decide
whether the checkout may advance. The module therefore holds the only two GitHub reads the check
makes, each one inline literal argv `["gh", "api", "--hostname", "github.com", "--method", "GET", path]`
handed to `process_runner.run_subprocess`. Read-only is proven, not asserted: a structural test
requires every transport call in the module to take an inline list literal whose literal prefix is
exactly that GET prefix, with one path element, no unpacked sequence and no field or input flag, and a
runtime test records the argv of every read a full check makes. Building the command line
non-literally to slip past the enumeration is ruled out by the same test, because a variable-built
argv fails the literal-prefix predicate. Both reads carry the copy's stored transport credential,
the same record and key the port uses, as `GH_TOKEN` in the child environment only: there is no
environment fallback, and with no usable record the module refuses without spawning `gh`. In a check
the updater's credentialed fetch reads the same record first, through the module's `transport_token`
accessor, so a check with no usable record ends `error` / `fetch_failed` before either read; the
refusal is what a direct call to the read functions sees. The record path is mirrored from
`secrets_store._key_path`
rather than imported, to keep the standard-library-only closure, and a contract test that stores a
record through the service's `write_secret` pins the two together. `RECORDED_ARGV_MODULES` did not grow: it is still exactly
`{alan/branch_protection_compat.py}`, and the adapter subset check still fails for any module that
builds a `gh` argv without being listed in one of the two sets.

**Test infrastructure.** The strict `FakeForge` gained the four operations, and the four are
backed by the real `GhForge` by default: an unstubbed `feedback`, `unresolved_threads`,
`resolve_thread` or `post_review` reaches `_pr_branch.run_subprocess` (or `forge_gh.run_gh_api_json_input`,
which X2.7 PR15 moved into the adapter and which still reaches the runner through that attribute)
and, from there, the repository's `gh` guard, exactly as it did before the migration. That default
is what keeps the roughly seventy consumer tests in size-pinned files (`test_teams_pr_merge.py`,
`test_teams_implementation_fix.py`) green without touching them — replacing a one-line collaborator
stub with fake-provider setup grows a pinned file, and the rule there is refactor or narrow, never
re-pin. Every test in the three migrated surfaces' own files stubs the operation explicitly, so
they exercise the fake rather than a process double. `tests/test_forge_gh_transport_ops.py` is the
one place a process double remains legitimate: it is the transport seam itself.

**Amendments recorded, not absorbed.** Measured at HEAD when this PR was written:
(a) the item's `make_fake_run`/`_FakeProc` criterion is unreachable by this item at any PR — all ten
files are non-forge tests using a generic `subprocess.run` double from `tests/conftest.py`, and
`tests/test_claude_cli_journal.py` defines an unrelated `_FakeProc` of its own, so migrating every
forge caller moves the number by zero; (b) the module count is thirty, not fifteen — the walker
scans thirty-one before this PR, the thirty plus the adapter, and records twenty-seven after it;
(c) the caller universe is twenty-seven: the plan's Files line names twenty-eight, and
`_pr_merge_conflict.py` is already in it rather than omitted; (d) `alan/teams/_pr_branch.py` belongs
to none of the five named caller families yet holds fourteen subprocess sites, all three transport
entry points, 744 to 900 patch sites and roughly three quarters of the family mass, so the
repository-wide transport criterion is structurally unreachable while it exists and it needs its own
PR, making the item seven; (e) the patch-site ceiling of forty needs the same treatment, because the
surviving consumer stubs alone consume it entirely, and reconciling that repository-wide figure
belongs to the final X2.7 PR, not to this one.

**The family patch-site count, parsed and naive.** The family is the item's eleven modules — the
plan's eight (`_pr_branch`, `_pr_ready`, `_pr_feedback`, `_pr_threads`, `_pr_post`, `_pr_push`,
`_pr_flake_ledger`, `_pr_merge_conflict`) plus `_pr_reconcile`, `_pr_freshness` and `_pr_sonar` — and
a patch site is one `setattr` call whose target is one of them, however many source lines the call
wraps to, with the target named as an identifier, an attribute chain or a dotted-path string. The
family stays eleven modules after X2.7 PR2: `_pr_freshness`, `_pr_reconcile` and `_pr_sonar` have
left the migration's scope or been discharged, but they remain *measured* modules —
`tests/test_forge_patch_sites.py` pins `len(FAMILY_MODULES) == 11`, and other test files still patch
`_pr_freshness.is_measured_baseline_stale` — so `FAMILY_MODULES` is not edited.
`tests/forge_patch_sites.py` counts them by walking the parsed source of every `tests/**/*.py`, the
way `tests/forge_argv.py` walks `alan/`, and `tests/test_forge_patch_sites.py` pins the result in
exact equality (`RECORDED_PATCH_SITES`), so it fails in both directions and each later X2.7 PR lowers
the pin as it migrates callers. The parsed definition is the one the remaining X2.7 PRs are measured
against, and the forty-site ceiling is reconciled against it.

| Method | At X2.7 PR3's tip | At X2.7 PR2's tip | At X2.7 PR1's tip | At `a3944f55`, before PR1 |
| --- | --- | --- | --- | --- |
| Parsed (`count_family_patch_sites`) | **1,283 sites in 106 files** | 1,295 in 105 | 1,318 in 106 | 1,372 in 107 |
| Naive single-line text search | **931 in 102** | 942 in 101 | 963 in 102 | 1,013 in 103 |

The naive search is a `monkeypatch.setattr(` whose family target sits on the same line. At `a3944f55`
it reproduces exactly the 1,013 in 103 files this document first recorded; the item's own 1,032 at
`dd9b9d87` is a naive figure of the same kind. The 353-site gap at PR2's tip — about a third of the
naive figure, against a target in the tens — is 346 calls wrapped over several lines, which a line
search cannot see, plus 7 dotted-path targets such as
`"alan._dispatcher_selfheal._pr_ready.readiness_or_unavailable"` (PR1's tip had 355: 348 wrapped, the
same 7 dotted). PR2 removed the 24 parsed sites in `tests/test_pr_freshness.py`, which no longer
patches `_pr_branch` at all, and added one in `tests/test_forge_gh_adapter.py` — the adapter's new
`compare_base` tests reach the transport through a single `_patch_transport` helper — which is the
net −23 sites and −1 file. To reproduce, from the repository root:

```
uv run python -c "from tests.forge_patch_sites import count_family_patch_sites; print(count_family_patch_sites())"
git grep -hE 'monkeypatch[.]setattr[(](_pr_branch|_pr_ready|_pr_feedback|_pr_threads|_pr_post|_pr_push|_pr_flake_ledger|_pr_merge_conflict|_pr_reconcile|_pr_freshness|_pr_sonar),' -- tests | wc -l
git grep -lE 'monkeypatch[.]setattr[(](_pr_branch|_pr_ready|_pr_feedback|_pr_threads|_pr_post|_pr_push|_pr_flake_ledger|_pr_merge_conflict|_pr_reconcile|_pr_freshness|_pr_sonar),' -- tests | wc -l
```

The first prints `PatchSiteCount(sites=1283, files=106)`; the second and third print the naive site
and file counts (931 and 102), as at X2.7 PR3's tip. Insert a commit id (`a3944f55` for the pre-PR1 figures) before
`-- tests` in the `git grep` commands for that commit's naive figures, and pass a checkout of it as
`root` to `count_family_patch_sites` for its parsed one.

**The item-level criteria, in per-PR form.** Three of the item's criteria are written here in the
form a correct first PR can meet, and the item-level figure of each is the final X2.7 PR's to
reconcile. The enumeration reaches zero for this PR's three modules while the frozenset records the
rest; the final PR reconciles the recorded set to empty. Transport calls reach zero in the three
migrated modules across all three entry points; the repository-wide zero is the final PR's. The line
criterion for this PR is staying inside the granted 150-line `X2.7:forgeport-rest-of-family`
allowance, measured with the repository's own non-blank counter rather than a documented figure:
the backend total moved 66,522 → 66,525 (+3), and `tests/size_baseline.py` is untouched. The
item-level net-line target is the final PR's to reconcile, not this one's.

## X2.7 PR2: the compare operation

`alan/teams/_pr_freshness.py` — the merge gate's measured-baseline freshness probe — is the second
X2.7 caller off direct forge transport. It had three forge-facing sites, and only one needed a new
operation: the `headRefOid`/`baseRefName` read is the existing `read_pr` (asked for
`("headRefOid", "baseRefName")`), the conditional PR-diff read is the existing `changed_files`, and
the reverse compare is the one new port method, `compare_base(head_sha, base_ref, *, forge_target,
timeout) -> CompareRead`. `CompareRead` is a frozen `(ok, behind_by, files, truncated, returncode,
stderr)`, the same shape as `PrRead`, `ChecksRead` and `MergeAttempt`.

- **`behind_by` is GitHub's `ahead_by` on `head...base`.** The compare runs head → base, so the
  forge's "ahead_by" counts the base commits the head lacks — how far the PR is behind. The port
  names the domain fact rather than the forge's vantage-point-dependent word, so the rename happens
  once, in the adapter. It is `None` when the payload is absent, undecodable, or carries no integer
  `ahead_by`; a JSON `true`/`false` is not an integer here (the pre-migration caller's
  `isinstance(ahead_by, int)` accepted it). The audit reason string stays spelled `ahead_by_missing`,
  because the audit log's reason strings are a contract of their own.
- **`truncated` replaces page-size inference.** The compare API pages its `files` array at 300; the
  caller used to compare `len(names)` with `_GAP_FILES_PAGE_CAP`. That constant is gone from
  `_pr_freshness.py` and lives in the adapter as `_COMPARE_FILES_PAGE_CAP = 300`; the port exposes
  only the domain flag, so the page size crosses the boundary in neither direction — no parameter
  in, no count out. The flag is computed from the raw filename list *before* it collapses into the
  `files` frozenset, exactly as the caller did: dedup-first would silently un-truncate a full page
  that repeated a filename. The fail-toward-stale behaviour on truncation is unchanged.
- **The base ref is percent-encoded in the adapter, with no safe characters** (`quote(base_ref,
  safe="")`, so `release/1.2` becomes `release%2F1.2`), carried over verbatim from the caller;
  the port takes the raw ref. `files` keeps only string `filename` values of object entries, and a
  non-list `files` is empty.
- **Error contract.** `compare_base` propagates `FileNotFoundError` and `subprocess.TimeoutExpired`
  like the three `gh pr` methods and reads `returncode`/`stderr` verbatim. The probe still never
  raises: `_pr_freshness._guarded` catches exactly those two and audits, and
  `_pr_freshness._succeeded` audits a non-zero return — the two prologues the old `_run_gh_json`
  carried, now over primitives. They are not one helper over `PrRead | CompareRead`, because the two
  results share no base and the port should not grow one to save a prologue. All five audit reason
  strings (`gh subprocess error: …`, `gh returncode=…: …`, `json_malformed`,
  `head_or_base_ref_missing`, `ahead_by_missing`) are spelled as before.
- **Two failure-path audit shifts, accepted and recorded.** (1) On the compare path an undecodable or
  non-object body used to audit `json_malformed`; a parsed `CompareRead` cannot tell it from an
  object with no `ahead_by`, so it now audits `ahead_by_missing`. (2) On the refs path a body that
  decodes to `{}` used to audit `head_or_base_ref_missing`; `read_pr` folds `{}` and undecodable
  output into the same empty `fields`, so the probe now audits `json_malformed` for both. Both remain
  fail-open (`False`, no raise) and audit-only; nothing outside `merge_recovery.md` and the module's
  own tests reads those reason strings.

**What is enforced.** `alan/teams/_pr_freshness.py` moved from `RECORDED_ARGV_MODULES` (27 → 26) to
`MIGRATED_MODULES` (three → four), so `test_migrated_module_calls_none_of_the_three_transport_entry_points`
now holds for it, and `RECORDED_PATCH_SITES` was re-measured to 1,295 sites in 105 files.
`tests/test_forge_gh_transport_ops.py` counts thirteen operations on both the port and the adapter.
`compare_base` is strict in `FakeForge`: `_ADAPTER_BACKED_BY_DEFAULT` stays at the four X2.7 PR1
operations, because an adapter-backed compare would let `tests/test_pr_freshness.py` reach a real
`gh` and be refused by the repository's `gh` guard, while neither size-pinned consumer file
(`test_teams_pr_merge.py`, `test_teams_implementation_fix.py`) references the freshness probe.
`MEASURED_BASELINE_PATHS` is untouched: `alan/merge_gate.py` consumes it, `tests/test_pr_freshness.py`
pins its length and membership, and `tests/test_ratchet_direction.py` asserts exact set equality
against paths `_ratchet_direction.py` hardcodes rather than imports.

**Claimed, and not claimed.** The claim is that `_pr_freshness.py` makes no *direct* process-level
forge call. It does not claim command construction left the codebase for that line of the caller:
`update_branch`, `head_sha` and `changed_files` are delegating façades whose `GhForge` bodies call
straight back into `_pr_branch` (`update_pr_branch`, `_fetch_pr_head_sha`, `fetch_diff_files`), so
routing the PR-diff read through `changed_files` moved no transport — only nine of the port's
thirteen methods hold transport in the adapter. **Deferred decision:** inverting those three façades
so the transport moves into the adapter belongs to `_pr_branch`'s own PR, the hot module holding most
of the family's mass, and is not made here.

**Out of X2.7 PR2's scope, on measured evidence.** `_pr_sonar.py` is not a forge caller: its two
transport sites shell `curl` at the Sonar host with a Sonar token, and a Sonar method on the forge
port would make it mean "anything we shell out to". `_pr_reconcile.py` is already discharged — its
forge call is `read_pr` (X2.3), and its remaining `run_subprocess` is a `_run` helper whose callers
are all `git`, which belongs to the version-control port. `_pr_flake_ledger.py` is its own PR: three
of its four forge calls are the issue/label domain (`gh issue list --search --label`, `gh label
create --force`, `gh issue create`) the port has never modelled, and the fourth, `gh pr diff`, is a
raw patch `changed_files` cannot serve because it returns filenames. Two behaviours a naive port
would erase, recorded for that PR: `gh issue list --search` is fuzzy and the caller re-filters
exactly, and `gh label create --force` is fire-and-forget.

**Recorded, not amended here.** The item's text says six PRs and two
independent measurements now put it at eight; its definition of done says fifteen argv-building
modules where X2.7 PR1 measured thirty, against a 27-module caller universe. The item's own totals
(`run_subprocess(` outside the adapter, the enumeration reaching zero, the family patch-site ceiling,
net LOC) are item-level and reach their targets only at the item's final PR. The 150-line
`X2.7:forgeport-rest-of-family` allowance is not raised or newly granted.

## X2.7 PR3: the issue/label domain and the raw patch

`alan/teams/_pr_flake_ledger.py` — the subsystem that records which external checks a rerun
rescued, decides whether a red check is attributable to the branch's own diff, and opens quarantine
proposals once a check has flaked often enough — is the third X2.7 caller off direct forge transport.
It had four forge calls and all four moved, onto four new operations: three are an issue/label domain
the port had never modelled, the fourth is the raw patch text that `changed_files` cannot serve because
it returns filenames only.

- **`pr_patch(pr_number, *, forge_target) -> str | None`** is `gh pr diff`. A successful empty diff is
  `""`, only a fault is `None`, so the caller can still tell "nothing changed" from "could not read".
  The size ceiling (`_DIFF_PATCH_MAX_BYTES`, measured on the UTF-8 encoding, not the character count)
  and the skip-the-diff-join return above it stay in `fetch_pr_diff_patch`: a ceiling is the ledger's
  policy, not a property of the forge.
- **`search_open_issues(query, *, forge_target, label) -> tuple[JsonObject, ...]`** returns the
  candidate list, not "the matching issue". `gh issue list --search` is fuzzy, and
  `_has_open_flake_proposal` re-filters for an exact title (`item.get("title") == title`); a port
  method that answered the exact-match question would erase that filter. `_maybe_open_flake_proposal`
  still calls it twice, bracketing the label creation, as the deliberate re-check that closes the
  window between "no proposal" and "proposal opened" — the port keeps both calls observable because it
  returns candidates every time.
- **`create_label(name, *, forge_target, color, description) -> bool`** is `gh label create --force`.
  It is fire-and-forget: `_ensure_flake_label` discards the result, and a failure is not an error
  condition (the label usually already exists and `--force` makes that a no-op update).
- **`create_issue(title, body, *, forge_target, label) -> bool`** is `gh issue create`; its result
  feeds the `ok` field of the `flake_proposal_opened` audit row.

The flake-quarantine label, colour and description are the ledger's constants, passed as parameters,
so nothing on the port is specific to flakes and the argv is byte-identical to what the ledger built.

**Exception contract — the adapter swallows.** Every fault, whether a non-zero exit, a missing
executable (`FileNotFoundError`) or a timeout (`subprocess.TimeoutExpired`), yields the falsy result the
ledger's call sites already produced (`None`, `()`, `False`), through one private helper,
`_gh_or_none`, in `alan/forge_gh.py`. The ledger references no transport exception type; its
`import subprocess` and its `GH_TIMEOUT_S` import left with the four sites, and the timeout default now
comes from the adapter. Preserving byte-identical failure handling was a precedent in the earlier
pieces, not a law: honouring it literally would have kept a transport leak in the caller for a later
piece of work to remove.

**The four call the transport with the ledger's own keyword arguments.** They run as the default
identity, so `_gh_or_none` calls `_pr_branch.run_subprocess(argv, cwd=None, timeout=GH_TIMEOUT_S)`
directly rather than through `_run_gh`, which would add `env=None`. That is not cosmetic:
`fetch_pr_diff_patch` also serves `_review_context.pr_evidence_block`, and
`tests/test_verification_scenario_runner_scoping.py` installs a process double that takes only
`cwd` and `timeout`; an extra `env` keyword fails it with a `TypeError` about an unrelated evidence
block.

**Test infrastructure.** The four are added to `_FORGE_METHOD_NAMES` and to
`_ADAPTER_BACKED_BY_DEFAULT` in `tests/fakes/forge.py` in one hunk, so `FakeForge` now backs eight
operations with the real adapter. This is the highest-risk clause of the change: nine consumer test
files reach the ledger's surface, two of them through catch-all `run_subprocess` doubles installed
across whole `seed_and_run(...)` runs (`tests/test_teams_verification_gate_format_and_routing.py`,
`tests/test_teams_verification_gate_path_mode.py`) that return `rc=0` for everything and assert only on
`gh pr review` argv. A strict default would turn those silent pass-throughs into `unstubbed forge call`
failures reading as a problem with an unrelated review command. The ledger's own test files
(`test_pr_flake_ledger.py`, `test_pr_flake_ledger_forge.py`, `test_pr_flake_ledger_scoping.py`,
`test_pr_flake_ledger_spec_gate.py`) stub every operation they reach explicitly, so they exercise the
fake and never a process double; the adapter's argv and fault matrix lives in the new
`tests/test_forge_gh_issue_ops.py`, and the ledger's new forge-behaviour tests live in the new
`tests/test_pr_flake_ledger_forge.py`, because `test_pr_flake_ledger.py` (814 non-blank before this
change, unpinned) has nothing to shrink against under the 1,000-line test cap. The fault-shaped ledger tests that only asserted "a
non-zero exit / a missing `gh` / malformed JSON gives a falsy result" moved to the adapter's own file,
where that behaviour now lives.

**What is enforced.** `alan/teams/_pr_flake_ledger.py` moved from `RECORDED_ARGV_MODULES` (26 → 25) to
`MIGRATED_MODULES` (four → five), so it is held to calling none of the three transport entry points;
`RECORDED_PATCH_SITES` was re-pinned to the parsed count at merge, 1,283 sites in 106 files (see the
table above — no target figure was set, because the ledger is itself a measured family module and
replacing process-layer stubs with ledger-targeted ones is a legitimate net increase);
`tests/test_forge_gh_transport_ops.py` counts seventeen operations on both `ForgePort` and `GhForge`.
`FAMILY_MODULES` stays at eleven. The backend total moved by −1 non-blank line across the three
`alan/` files touched (`forge.py` +12, `forge_gh.py` +43, `_pr_flake_ledger.py` −56), inside the
granted 150-line `X2.7:forgeport-rest-of-family` allowance; `tests/size_baseline.py` is untouched.

**Amendments recorded, not absorbed:**

1. **The item's PR count moves from six to seven to eight.** The item's text says six; X2.7 PR1's
   amendment (d) made it seven, because `_pr_branch.py` needs its own PR; the flake ledger being its own
   piece of work, and this the third X2.7 PR, makes it eight.
2. **The operations-count pin is 17, not the brief's 16.** `ForgePort` and `GhForge` each had thirteen
   public methods; four call sites need four operations, so 13 + 4 = 17 on each owner. Sixteen is
   reachable only by deferring one of the four sites (which leaves a forge command vector in the ledger
   and fails the enumeration clause) or by folding label creation into issue creation (which destroys
   the re-check that brackets the label creation and the fire-and-forget behaviour of the label call).
   The pin asserts seventeen against both owners.
3. **Log detail collapses.** `fetch_pr_diff_patch` used to log `rc=%s stderr=%r` on a non-zero exit and
   the exception on a transport fault; the adapter is deliberately logger-free and returns `None` for
   both, so the ledger logs one warning per falsy `pr_patch` return and can no longer see the return
   code or the stderr. `_ensure_flake_label` and `_open_flake_issue` likewise no longer log a swallowed
   transport fault; the audit row's `ok` still records a failed issue creation.
4. **`fetch_diff_files` is left in place, as non-gating residue.** The ledger's diff-files helper call
   (`_diff_attributes_tests`) is neither a `gh` argument vector nor one of the three transport entry
   points, so both exact-set oracles pass with it untouched and it satisfies no clause of this piece of
   work. X2.7 PR14 ("X2.7 PR14: PR view reads and branch update" below) has since moved
   `_pr_branch.fetch_diff_files` onto `changed_files`, so the ledger's helper now reaches the port.
   Slice 2 of the `_pr_branch.py` migration still owns the check-run listing, the check-run
   annotations, the check-suite re-request, the workflow rerun, the status-rollup read, the failed-job
   log, the trigger helper and the credential helper; the ledger still reaches
   `fetch_failing_e2e_tests` (no port operation, named under "Operations deliberately not on the port").
5. **The adapter now holds transport in thirteen of the port's seventeen methods** (nine before, plus
   these four); at that point the three delegating façades (`update_branch`, `head_sha`,
   `changed_files`) still called back into `_pr_branch`, which was unchanged from PR2's deferred
   decision. Only `update_branch` still does since X2.7 PR14.

The item's own totals — the enumeration reaching zero, `run_subprocess(` outside the adapter reaching
zero, the family patch-site ceiling and the net line target — remain item-level and are reached only at
the item's final PR; nothing here claims them.

## X2.7 PR4: the verification review posts

Three verification-team modules made the last of that team's forge calls as raw `gh` transcripts:
`_verification_grounding.py` (the cannot-verify comment), `_verification_review_post.py` (the verdict
review) and `_verification_merged_guard.py` (the already-merged short-circuit). All three moved onto
operations the port already had. **No operation was added and the count stays at seventeen.** The
existing `post_review` is adopted as it stands: a second review-posting operation would give the
port two shapes for one act, and `tests/test_forge_gh_transport_ops.py` already forbids any operation
parameter named for an environment, header, token, credential, method, path or endpoint, so a
transcript-shaped second operation could not be expressed on it anyway. The merged-state read is
`read_pr` asked for `("state", "mergeStateStatus")`, which builds the same `gh pr view` argv the guard
built itself.

**Both posting paths go through the shared helper.** `_apply_legacy_pr_review`,
`_apply_inline_pr_review` and `_post_cannot_verify_comment` all call
`_pr_post.post_pr_review_with_inline_comments`; the legacy review and the cannot-verify comment pass an
empty findings sequence, which the helper's payload builder turns into an empty `comments` array.
Neither module imports the port, and the review-post module lost its `subprocess` import, its
`_GH_TIMEOUT_S` constant and its `_REVIEW_FLAGS` table; the grounding module lost its cross-module
private import of that constant and the `reportPrivateUsage` suppression that came with it. Only the
merged guard imports `alan.forge` (`active_forge`), which the layers list already permits from `teams`.

**Behaviour changes, accepted and recorded.** (1) Every verification review post is now the REST
`POST /repos/{slug}/pulls/{n}/reviews`, where the legacy and cannot-verify posts used `gh pr review`.
The inline-to-plain fallback therefore no longer switches API surface: the fallback post differs from
the inline one only by carrying an empty `comments` array. (2) The credential-failure classifier
(`_pr_branch.classify_credential_failure`) now reads REST error text rather than `gh pr review`
stderr; the inline path already ran REST text through it, so the residual risk is small. The
classifier still receives the forge result's `returncode` and `stderr` verbatim, and the retry count
and backoff are unchanged.

**Identity is requested, never assumed.** `_pr_post.verifier_identity_available(forge_target)` is the
one place the question "did a reviewer credential resolve" is answered
(`bool(_pr_branch.verifier_gh_env(forge_target))`). `_post_review_with_retries` calls it once per post
and hands the answer down as a keyword-only `as_verifier` — replacing the `env` mapping the private
functions used to carry — to both the inline and the plain attempt, to every retry, to the
`gh_user_override` field of every audit entry (`review_ok`, `review_failed`, `review_ok_inline`,
`review_failed_inline`) and to the classifier's `token_present`. Those cannot disagree because they
are one value, and on the owner's install a run with no resolved credential still posts under the ambient identity with
every entry recording `gh_user_override: false` (on a copy the same post carries the transport credential; "Identity on a copy" above). `_post_cannot_verify_comment` calls the same
predicate, so the two posting paths cannot derive identity differently. The adapter turns the boolean
into an environment (`_verifier_env`, `forge_gh.py`); no mapping crosses the port.

**Transport failures behave as before, by two different guards.** The port does not swallow
`FileNotFoundError` or `subprocess.TimeoutExpired`. `_pr_post._post_review_payload` still converts
them into a `ReviewPostOutcome` with `ok=False` and an `error`, which is why neither review-posting
module carries a `try`/`except` of its own any more (a kept one would be an unreachable branch). The
merged guard has no such helper, so `pr_state_name` keeps its caller-side `except (OSError,
subprocess.TimeoutExpired): return None`; it is deliberately the wider `OSError`, because the guard's
`PermissionError` case is what proves the narrower tuple insufficient. `pr_state_name` keeps all three
of its no-result paths: a failed read (`not read.ok`), an unparseable payload (`read_pr` folds it into
empty `fields`) and a payload whose `state` is missing, empty or not a string — the last two end at
the one `isinstance(state, str) and state` check rather than at a branch of their own.

**Audit entries.** `_add_failure_detail` writes the disjoint failure shapes both review-post paths
share: `error` alone for a transport failure, `returncode` plus `stderr` capped at 500 characters for a
non-zero exit. The plain path keeps its operator-visible warning log for a non-zero exit.

**Test infrastructure.** `read_pr` is deliberately **not** in `_ADAPTER_BACKED_BY_DEFAULT`: it is
absent from the fake's defaults, so an unstubbed call raises, and the merged guard's tests stub it
with `ForgeScript.queue("read_pr", pr_read(...))` — one of them pins the refusal to default-back it.
`post_review` is already adapter-backed by default, so consumers of the review post keep their
existing stubs. Identity is asserted as the recorded `as_verifier` keyword, never as an environment
mapping, because queueing an operation overrides adapter backing and no environment is observable
above the adapter; the twin pairs vary the credential at `_pr_branch.verifier_gh_env` (or
`ALAN_VERIFIER_GH_TOKEN`), and the adapter's own identity pair in
`tests/test_forge_gh_transport_ops.py` is not duplicated. Two coupling tests hold the two posting
paths together: one replaces the predicate and checks both paths record its answer, the other replaces
`verifier_gh_env` and checks both follow it — the second is what proves a shared derivation rather
than a shared name. The run-level verification tests that used to read `gh pr review` argv and
`--body` off a `run_subprocess` double (`test_teams_verification_run_and_retry.py`,
`test_teams_verification_gate_format_and_routing.py`, `test_teams_verification_gate_path_mode.py`,
`test_clause_audit_wiring.py`) queue a typed `post_review` result on `ForgeScript` and assert the
review `event` (and `body`) from `ForgeScript.review_payloads()` instead.

**What is enforced.** The three modules moved from `RECORDED_ARGV_MODULES` (25 → 22) to
`MIGRATED_MODULES` (5 → 8), so `test_migrated_module_calls_none_of_the_three_transport_entry_points`
now holds for them, and `RECORDED_PATCH_SITES` was re-measured from the enumeration's own assertion
message rather than counted by hand (1,261 sites in 108 files at this PR's tip; the migration removes
`_pr_branch.run_subprocess` patches from the guard's, the grounding and the run-level verification
tests and adds `_pr_post` patches to the new ones, so the direction was not predictable in advance).
`FAMILY_MODULES` stays at
eleven. `tests/size_baseline.py` is untouched and the change net-shrinks the backend rather than
spending the item's shared 150-line allowance.

**Deferred.** The other half of this caller family — `deploy_verify.py` and `dependency_sweep.py` —
was left to a later PR; `deploy_verify.py` moved in X2.7 PR5 below and `dependency_sweep.py` in X2.7
PR6 below, and the family's item-level definition of done is owned by the final PR.
`_pr_sonar.py` authors no forge argv but reaches the forge through the facade's check-run and head-sha
helpers; that access is the facade-inversion PR's, not this one's, and the facade module is not
modified here.

## X2.7 PR5: the deploy-verification read

The deploy-verification team had exactly one transport site: `_gh_pr_merge_info`, which ran
`gh pr view <n> --json state,mergedAt,mergeCommit` through `_pr_branch.run_subprocess` with no
environment. It is now `_pr_merge_info(pr_number, *, forge_target)`, a `read_pr` call asked for
`("state", "mergedAt", "mergeCommit")`, and the module no longer assembles a `gh` command line or
reaches any of the three transport entry points. **No operation was added and the count stays at
seventeen.** `alan/teams/deploy_verify.py` moved from `RECORDED_ARGV_MODULES` to `MIGRATED_MODULES`,
which the enumeration gate recomputes in both directions.

**A declared behaviour change: the read now addresses an explicit repository.** The old argv carried
no `--repo` and ran with `cwd=None`, so `gh` resolved the repository from the service's working
directory. `read_pr` always appends `--repo <slug>`, and `_evaluate_once` threads the workspace's own
`_pr_forge.forge_target_for_ws(workspace)` in as a required keyword-only `forge_target` — there is
deliberately no `DEFAULT_FORGE_TARGET` fallback, because one would silently restore the
working-directory resolution this change removes. In production `_pipeline_available_for` already
returns before the read for any workspace whose repository is not the default engine's, so the
change is one of correctness of the call rather than a newly reachable path. It is pinned by a
discriminating pair rather than a default-only assertion, because measured against the default
repository the fix is invisible: `test_evaluate_once_reads_the_pr_from_the_workspaces_own_repository_target`
forces the guard open for a workspace bound to a registered non-default engine and asserts the
recorded `forge_target` is that engine's slug and not the default's, and its default-engine control
asserts the default slug.

**The exception wrapper and the `subprocess` import stay.** `GhForge.read_pr` calls `run_subprocess`
bare and catches neither `FileNotFoundError` nor `subprocess.TimeoutExpired`, so
`except (FileNotFoundError, subprocess.TimeoutExpired)` is still what turns a missing binary or a
transport timeout into `_forge_errors.ForgeTransientError` for the dispatcher's retry. Deleting it on
the reasoning that the port handles transport faults would turn a handled timeout into a crash. The
three non-zero-exit outcomes are unchanged: a rate-limited read raises `ForgeRateLimited`, a network
fault raises `ForgeTransientError`, and a credentials failure (`auth`/`unknown`/`domain_absent`)
returns `_PrMergeInfo(gh_ok=False, ...)` without raising.

**`gh_ok` is `read.ok and bool(read.fields)`, as two branches.** `read_pr` folds a payload that is
`null`, empty, unparseable or not an object into `fields={}` with `ok=True`. The old caller decoded the
raw stdout itself and reported those as `gh_ok=False`; the merge-race check
(`_is_merge_race`: `state == "MERGED" or not gh_ok`) and the escalation text (`state=gh call failed`
versus `state missing from gh response`) both depend on that. Folding the empty case into
`gh_ok=True` would flip an empty read from the merge-race wait path to an immediate escalation
without failing any test that only asserts the handover type, so `_merge_info_from_fields` keeps
`if not fields: return gh_ok=False` as its own first branch and the tests assert both the
race-budget wait and the `state=gh call failed` reason. The old `ValueError` and
non-dict decode cases now live at the adapter (`tests/test_forge_gh_adapter.py`), which owns the raw
payload; the caller's tests convert to field-shaped equivalents rather than being deleted.

**What is enforced.** `deploy_verify.py` is absent from the set of modules that build a `gh` command
line and present in the migrated set; the family patch-site pin `RECORDED_PATCH_SITES` was re-measured
from the enumeration's own assertion message after the test edits settled (a consistency requirement
— recorded equals measured — never a direction, since a concurrent forge-family merge can red a
correct branch and the remedy is re-measuring). `read_pr` is still deliberately **not** in the fake's
`_ADAPTER_BACKED_BY_DEFAULT`: the deploy-verification tests stub it with
`ForgeScript.queue("read_pr", pr_read(...))`, and the no-deploy-pipeline test asserts the fake recorded
no call at all. The gate-start and wait-signal test files keep a local `("gh", "run", "list")`
double, because `deploy_runs.fetch_deploy_runs` is a different, still-recorded module.

**What this PR left, and where it went.** X2.7 PR5 did not close the item: `dependency_sweep.py` still
built `gh` command lines for all five of its forge calls. It moved in X2.7 PR6, next.

## X2.7 PR6: the dependency sweep, and which identity each of its calls runs as

`alan/teams/dependency_sweep.py` had five forge calls, each a bare `subprocess.run` with
`env={**os.environ, **ctx.gh_env}`. All five now go through the port, and the substance of the change
is the identity decision recorded here, not the transport swap. The module keeps `import subprocess`:
its `uv run pip-audit` is not a forge call and keeps its direct call, and `read_pr` calls the transport
bare, so the sweep's `except (FileNotFoundError, subprocess.TimeoutExpired)` around it stays. Every
transport claim in this section is about *forge* calls.

| interaction | operation | identity |
|---|---|---|
| list the dependency bot's open proposals | `open_prs_by_author` (new) | transport, resolved inside the adapter |
| read one proposal's changed files | `read_pr(n, ("files",), ...)` | **ambient** on the owner's install (the declared exception); the transport credential on a copy |
| search open issues by title | `search_open_issues` | transport, resolved inside the adapter |
| create the security label | `create_label` | transport, resolved inside the adapter |
| file the security issue | `create_issue` | transport, resolved inside the adapter |

**Decision: four calls keep the per-repository transport credential; the files read keeps the ambient
identity, on purpose.** The three calls that write to the forge with an author, and the listing that
decides which proposals those writes concern, ran as the `secrets_store.TRANSPORT_PURPOSE` secret for the
target repository (falling back to the engine's `credential_env_name`, `ALAN_VERIFIER_GH_TOKEN` for the
default engine) and still do. The changed-files read is the exception: it is a read of the default
engine's own proposals, authorship is not at stake, and giving it the transport identity would have
dragged two unrelated callers onto a shared read path (`read_pr` is also the merge family's, the
readiness gate's and deploy verification's) for no benefit. It is recorded here so that four
credentialed interactions beside one uncredentialed one are not later filed as a bug. A consequence a
reader should expect: `_to_proposals` reads one proposal's files under the ambient identity and its
failing jobs under the transport identity (`_detect_upstream_block` calls
`_pr_branch.fetch_failing_job_ids`, which since X2.7 PR15 reads the rollup through `status_check_rollup`), in the
same function, deliberately. On a copy the exception does not apply: every `GhForge` call carries the
transport credential, so the files read does too ("Identity on a copy" above).

**Where the credential is resolved.** The port forbids credential-shaped and string-mapping
parameters (`tests/test_forge_gh_transport_ops.py`), so the caller cannot pass one. `GhForge` resolves
it itself: a private `_transport_env(forge_target)` returns
`{"GH_TOKEN": token}` from `secrets_store.transport_credential(forge_target)`, or `None` without one (X2.7 PR10 moved it off `_pr_branch`; PR15 deleted the copy left there), the mirror of `_verifier_env`, and
`search_open_issues`, `create_label`, `create_issue` and `open_prs_by_author` pass it to
`_gh_or_none`/`_gh_succeeded`. Resolution is fused to those four operations and confined to them:
`pr_patch`, `read_pr`, `read_checks` and `merge_pr` keep the identity they had, because resolving there
would move authorship on merges and review posts, contradicting the documented scope of the verifier
credential. `_gh_or_none` forwards `env` to `run_subprocess` only when a token resolved, so a
process double taking only `cwd` and `timeout` (see "The four call the transport" above) still works
for `pr_patch` and for every operation in an environment with no token.

**The new operation.** `open_prs_by_author(author, fields, *, forge_target, limit)` is
`gh pr list --repo {slug} --author {author} --state open --json {comma-joined fields} --limit {limit}`;
`--state open` is pinned inside the adapter rather than a parameter, because this caller needs nothing
else and a state parameter would be the start of a generic passthrough that "Operations deliberately
not on the port" forbids. It returns `tuple[JsonObject, ...]`, `()` on a non-zero exit, a transport
fault, unparseable output or a non-list payload; non-object items are dropped. It is in
`_FORGE_METHOD_NAMES` but **not** in the fake's `_ADAPTER_BACKED_BY_DEFAULT`: an unstubbed listing
decides which proposals get merged, so it must fail as `unstubbed forge call` rather than reach a real
`gh`. The other five `gh pr list` callers (`repo_scaffold.py`, `collision.py`, `repo_status.py`,
`_pr_branch.py`, `triage_pregate.py`) remain raw; nothing here implies they migrate.

**The files read is the existing single-proposal read.** `read_pr(pr_number, ("files",), ...)` builds
`gh pr view <n> --repo <slug> --json files`, byte-identical to the old command, so no endpoint,
ordering or truncation question arises (`changed_files` is `gh pr diff --name-only`, a different
endpoint with different truncation, and was never a candidate). The timeout passed is the adapter's
own `GH_TIMEOUT_S`, imported, not a second `60.0`.

**Accepted cost: a quiet degrade, unchanged in shape, whose trigger moved.** If the ambient identity
cannot read a proposal, `_fetch_pr_files` returns `[]`, the proposal's `lockfiles` is
`frozenset()`, and `alan/dep_sweep.py::group_proposals` (via `_overlapping_indices`, which intersects a
group's lockfiles with a proposal's) treats an empty set as overlapping nothing, so the proposal becomes
its own singleton batch and is merged alone instead of batched with the other proposals sharing that
lockfile — the serial-conflict path batching exists to prevent. That degrade exists today and is
unchanged; what this change alters is *which credential's failure can trigger it*: the files read is
ambient, while the listing that named the proposals ran as transport, so a token that can list but an
ambient identity that cannot read is now the failing pair.
`tests/test_teams_dependency_sweep_receipts.py` pins both halves: a failed read yields an empty
lockfile set, and `group_proposals` keeps that proposal alone while two proposals sharing `uv.lock`
batch together.

**Other declared changes.**

- **The duplicate-issue search is narrower.** `search_open_issues` always passes `--label`, so the
  sweep now searches `--label security --state open --search "in:title <title>"` where it used to search
  without a label. A pre-existing open issue with the same title but no `security` label no longer
  suppresses a new one. This is accepted; the exact-title comparison in `_title_in_issue_list` stays on
  top of it. The search still runs before the label is created.
- **Per-call timeouts are dropped, not threaded.** Every production call already passed 60.0, the
  adapter's own default, so the sweep's `timeout_s` parameters left `_fetch_open_proposals`,
  `_fetch_pr_files`, `_has_open_security_issue`, `_ensure_security_label`, `_open_gh_issue` and
  `_handle_vuln`. `_to_proposals` keeps its own solely for `_detect_upstream_block`.
- **Non-fatal exactly where it was.** Every interaction still degrades to empty, `False` or a status
  string rather than raising, with the existing status strings (`open_issue`, `open_issue_failed`,
  `issue_already_open`, `open_issue_dry_run`) and the `dep_sweep: gh issue create` warning prefix
  preserved.
- **Failure detail is lost.** `create_label` and `create_issue` return `bool`, so the old
  `stderr[:200]` and the raised exception text are gone: the sweep logs `dep_sweep: gh issue create
  failed` and a new `dep_sweep: gh label create failed` with no detail. Widening the port's return type
  was out of scope. Because `create_issue` returns `False` without a reason, a wrong or absent
  credential on this path shows up only as `open_issue_failed` in the audit entries.
- **The flake ledger's issue authorship moves too.** `_pr_flake_ledger` reaches `search_open_issues`,
  `create_label` and `create_issue`, which now run as the transport identity where they used to run
  ambient. That is a conscious consequence of resolving the credential in the adapter, not an accident.
- **`SweepContext.gh_env`, `_gh_env_for` and the empty mapping in `_DEFAULT_SWEEP_CTX` are gone** from
  this caller; the equivalent resolver `verifier_gh_env` survives by design (`_pr_branch._transport_gh_env`
  did too, until X2.7 PR15 deleted it).

**Risk this PR carries.** It preserves a credential path with no observed production history:
`_gh_env_for` landed on 2026-07-23, and the security issues the sweep has filed on the repository are
older, so there is no post-wiring observation of which identity this path uses. The resolver reads the
store first, then the verifier token documented as the production bootstrap. The first live exercise
is a sweep that files issues and merges pull requests.

**What is enforced.** `alan/teams/dependency_sweep.py` moved from `RECORDED_ARGV_MODULES` to
`MIGRATED_MODULES`; measured against this change's base commit the raw-command-line set has strictly
fewer modules and the migrated set strictly more. The load-bearing gate is the one that detects an
assembled `gh` command line: the check that a migrated module calls none of the three transport entry
points was already green at the base, because this module never called any of them (it used
`subprocess.run` directly), so it is not evidence of migration. `tests/test_forge_gh_transport_ops.py`
pins eighteen operations on both `ForgePort` and `GhForge`. The family patch-site pair was re-measured
from the enumeration's own message after the test edits settled and did not move, because the sweep's
tests patched the `subprocess` module rather than a family module name. The item's own totals — the
enumeration reaching zero, direct transport calls outside the adapter reaching zero, any net line
target — are not claimed; they belong to the family's final deletion pass.

## X2.7 PR7: the PR view callers

Three modules whose only forge site is a bare `gh pr view <n> --repo <slug> --json <fields>` read are
the seventh X2.7 caller change: the workspace delivery read (`alan/delivery.py`), the merge-conflict
state read (`alan/teams/_pr_merge_conflict.py::gh_pr_state_and_merge_status`) and the clause-audit
pull-request state read (`alan/teams/_clause_audit.py::_fetch_pr_state`). Each reproduces the existing
`read_pr` operation's command line exactly, so the port needed no new method; the pinned operation
count stays at eighteen.

**Failure contracts, preserved per module, and they differ.** The merge-conflict read keeps raising: a
missing `gh` binary or a transport timeout still raises `_forge_errors.ForgeTransientError`, and a
non-zero exit still calls `_forge_errors.raise_for_infra` before degrading to `("", "")`. The delivery
read and the clause-audit read both keep degrading to their existing neutral value — `_empty(...)` and
`None` respectively — on the same four conditions (transport fault, timeout, missing binary, non-zero
exit). "Every failure stays swallowed and logged" is true of two of the three, not all three.

**The clause-audit empty-field trap.** `_clause_audit.PrState` is a four-field `NamedTuple`; an
instance whose fields are all `None` is a non-empty tuple and therefore truthy, so a naive port that
returned `PrState(None, None, None, None)` whenever `read_pr` reported an unparseable or malformed
payload would turn today's real `None` ("no state") into a populated-but-empty record that reads as
present everywhere `ClauseAuditContext.pr_state: PrState | None` is consulted. `_fetch_pr_state` keeps
a caller-side guard, `if not read.ok or not read.fields: return None`, because `GhForge.read_pr`
reports a malformed or non-object payload as `ok=True, fields={}` rather than a failure — the guard is
what restores the distinct "no state" result. This is sound specifically because this field set
(`isDraft`, `headRefName`, `baseRefName`, `body`) returns all four keys on a real successful
`gh pr view`, so an empty field set can only mean the read did not really succeed.

**The delivery entry point widens to `ForgeTarget`.** `fetch_pr_delivery` took `RepoIdentity` and now
takes `ForgeTarget`; its one production caller, `alan/routes/workspaces.py::get_workspace_delivery`,
already held a `ForgeTarget` and previously narrowed it to `target.repo` at the call site — the caller
now passes `target` unchanged. `ForgeTarget` carries a required `credential_env_name` that
`GhForge.read_pr` does not yet consult (`read_pr` always resolves the ambient `gh` identity, the same
as every other `gh pr view` caller on the port). Behaviour is unchanged today; this is recorded as an
accepted, dated (2026-09-22) trade — a later change that teaches the adapter to consult
`credential_env_name` will alter which credential this specific read authenticates as, and that is a
known, deliberate consequence of this port shape rather than a surprise regression.

**What moved onto the shared fake.** All three reads now go through `tests/fakes/forge.py`'s
`FakeForge` via `read_pr`, stubbed per test with `tests.forge_stubs.pr_read`/`always`/`ForgeScript`,
rather than faking the `subprocess` transport. `tests/forge_stubs.py` gained `queue_read_pr(fields,
*results)` on `ForgeScript`: it routes a `read_pr` call by the exact field tuple it requests to a
per-key queue, falling back to the existing unkeyed `queue("read_pr", ...)` list when no per-key queue
is registered for that call's fields — this is what let the pr_merge family's readiness reads (a
five-field tuple) and this PR's new `("state", "mergeStateStatus")` reads coexist in the same test
files without reordering either family's existing queue calls. `always()` also gained the ability to
install a raising stub directly from an exception instance, needed for the missing-binary and timeout
cases now that they are expressed as forge stubs rather than monkeypatched subprocess doubles. Reads
owned by modules this change does not migrate keep their existing direct `_pr_branch.run_subprocess`
fakes unchanged — that is not a shortfall of this change, just its boundary.

**Deferred, by name.** `alan/teams/_deploy_restart_probe.py` and `alan/teams/_pr_closing.py` each
had one `gh` site that looked eligible, but each also still invokes `git` through the shared
`_pr_branch.run_subprocess` transport helper after its `gh` site is migrated — `transport_calls`
matches by call name, so a `_pr_branch.run_subprocess` call carrying a `git` argv still counts, and
adding either module to the migrated set would fail
`test_migrated_module_calls_none_of_the_three_transport_entry_points`. This PR left both alone; the
two-tier statement that let them move is recorded under "X2.7 PR11" below. The generic API
passthrough callers remain deferred: they raise a design question of their own (one typed operation
per concrete call pattern, no method-and-path passthrough) that belongs in a change whose subject is
that question, and they are the next X2.7 PR's.

**What is enforced.** `alan/delivery.py`, `alan/teams/_pr_merge_conflict.py` and
`alan/teams/_clause_audit.py` moved from `RECORDED_ARGV_MODULES` to `MIGRATED_MODULES` in the same
commit: measured against this change's own base commit, the recorded-argv enumeration has strictly
fewer modules and the migrated enumeration strictly more — never stated as an absolute count, since a
concurrent merge could change either set's size on a correct branch.
`test_recorded_set_equals_the_modules_that_assemble_a_gh_argv` is the only forcing check on the
recorded side; nothing forces a module onto `MIGRATED_MODULES`, which is why moving both sides in the
same commit is this PR's own discipline rather than something the suite enforces.
`test_migrated_module_calls_none_of_the_three_transport_entry_points` now holds for all three.
`RECORDED_PATCH_SITES` was re-measured from `count_family_patch_sites()` after the test edits settled
(1,218 sites in 106 files); `FAMILY_MODULES` stays at eleven, since `_pr_merge_conflict` was already a
member. No new port operation was added — the pinned operation count on `ForgePort` and `GhForge`
stays at eighteen. Two contract-pinning pairs (`test_malformed_json`/`test_non_dict_json` in
`tests/test_clause_audit_selection.py`; `test_gh_pr_state_and_merge_status_non_dict_json_returns_empty`
/`..._unparseable_stdout_returns_empty` in `tests/test_teams_pr_merge_conflict.py`) changed only their
fixture line — the setup that produces the malformed or non-object payload — while every assert and
expected value stays byte-identical, because total branch coverage cannot distinguish a preserved
contract from a rewritten expectation.

## X2.7 PR8: collision, deploy-run and engine-preflight callers

Three areas that had nothing to do with the merge family or the review surface were still
assembling `gh` command lines directly: the duplicate-work collision check that runs ahead of
every build proposal (`alan/collision.py`), the deploy-run listing consumed by post-merge deploy
verification (`alan/deploy_runs.py`), and two of the engine preflight's checks
(`alan/engine_preflight.py`). Between them they held four forge call sites, migrated onto four new
port operations: `open_prs`, `workflow_runs`, `viewer_login`, `repo_file`. The onboarding-scaffold
area (`alan/repo_scaffold.py`) and any amendment to `read_pr`'s credential dimension are
deliberately excluded — the scaffold still shells out for version-control work and cannot meet this
PR's forge-transport-only faking criterion, and `read_pr`'s eight call sites belong to that PR, not
this one.

**Why the outcome literal diverges from the house `ok: bool`, deliberately.** `PrRead`, `ChecksRead`,
`CompareRead` and `MergeAttempt` all carry a plain `ok: bool`. The four new operations carry an
explicit `Literal` outcome (`RowsOutcome = Literal["ok", "failed", "raised"]`,
`LoginOutcome` adds `"no_credential"`, `FileOutcome` adds `"absent"` in `"raised"`'s place) because
each caller here needs a three- or four-way split a boolean cannot express without illegal
combinations: `open_prs`/`workflow_runs` must let a caller distinguish "the call failed or returned
an unusable payload" from "the call succeeded and there is genuinely nothing to return" — a boolean
`ok` plus an empty list collapses those two into one shape, which is exactly the ambiguity the
nearest existing precedent (`open_prs_by_author`, `()` on every failure) already has and which this
PR is required not to repeat. A later PR should not normalise this back to `ok: bool`; the
divergence is intentional and the four-way splits it carries (see the outcome tables below) are the
reason.

**`open_prs` is a new operation, not an amendment to `open_prs_by_author`.** `open_prs_by_author`
already returns `()` for every failure (rc≠0, transport fault, unparseable payload, non-list
payload) and is consumed by the already-migrated dependency sweep. Amending it to add an
`outcome` field would change a migrated caller's contract, and its existing `()`-for-everything
shape is precisely what DoD-4 forbids for `open_prs`'s two callers-to-be. `open_prs` also runs
unfiltered (`--state open`, no `--author`), a different query shape from `open_prs_by_author`'s
`--author {author}`.

**`viewer_login` short-circuits to `no_credential` before invoking transport, closing the
ambient-auth trap.** `_transport_env(forge_target)` returns `None` when no token resolves, and
`_gh_or_none(argv, None)` then runs `gh` under whatever ambient authentication the host process
happens to have — correct for the read-heavy operations that already accept that fallback, wrong
for an identity check whose entire purpose is telling one credential apart from another.
`viewer_login` resolves its credential env first (`_verifier_env(True, forge_target)` when
`as_verifier`, else `_transport_env(forge_target)`) and returns `LoginRead(outcome="no_credential",
login=None, returncode=-1, stderr="")` without calling `_attempt`/`run_subprocess` at all when that
env is `None` — proven in `tests/test_forge_gh_caller_ops.py` by asserting the transport double was
never called. `open_prs`, `workflow_runs` and `repo_file` keep the existing token-or-ambient
fall-through unchanged, since none of their callers is an identity check. That fall-through is the
owner's install and an unconfigured install; on a copy the seam refuses instead of falling through to the
ambient login ("Identity on a copy" above).

**`repo_file` returns the raw `gh api` response body and defers base64 decoding.** The GitHub
contents API response is either a base64-encoded file envelope or a JSON array (directory listing);
neither shape has a slot among the four required outcomes, and the sole caller here
(`_check_dependabot_config`) only tests existence, never reads content. Decoding is deferred to the
first caller that actually needs file text — that caller will also have to decide what "a directory
where a file was expected" means for its four-outcome contract, a decision this PR does not need to
make. `_HTTP_404_MARKER = "HTTP 404"` (`alan/forge_gh.py`) is matched against `stderr` as a plain
substring to distinguish `absent` from a non-404 `failed`. That marker now lives only in
`alan/forge_gh.py` among the recurring-workflow and repo-status modules: `alan/recurring_forge.py`
no longer holds a copy, because since X2.7 PR10 it reads `repo_file`'s `absent` outcome instead of
matching stderr itself.

**Two declared behaviour changes on the collision check's open-PR call (DoD-8).** (1) The old
`_run_gh_pr_list` never passed `--repo`, so `gh` resolved the repository from the service's working
directory; `open_prs` always appends `--repo {slug}`, and `alan/collision.py` names an explicit
`DEFAULT_FORGE_TARGET` (`engine_config.DEFAULT_FORGE_TARGET`, the backend's single default target),
since `collision.py` already imports `engine_config` and this adds no new import-linter exposure to
verify. (2) The old call ran under whatever `gh` credential the host process ambiently had;
`open_prs` resolves the configured transport credential inside the adapter
(`_transport_env(forge_target)`), the same resolver `open_prs_by_author` already uses. Both changes
are stated here as decisions, per DoD-8, not discovered as regressions.

**A transport failure in the collision check leaves a record (DoD-5).** `_fetch_prs` treats
`result.outcome != "ok" or result.rows is None` as one unavailable case: it logs
`logger.warning("collision pr list unavailable outcome=%s rc=%s stderr=%r", ...)` and returns `[]`.
The safe verdict at `check_collision` — green, no candidates — is unchanged; what changes is that a
failing transport now leaves an operator-visible record instead of degrading silently past three
layers (`_fetch_prs` → `_pr_candidates` → `check_collision`'s own broad `except Exception`) with
nothing to show for it.
`test_transport_failure_in_pr_list_returns_safe_verdict_and_logs_a_warning` in
`tests/test_collision.py` asserts both halves — the safe verdict and the captured warning — in one
test, so a future change cannot silently drop the warning while keeping the verdict green.

**The port absorbs raised transport faults for all four new operations (DoD-6).** A private
`_attempt(argv, *, env, timeout)` helper in `alan/forge_gh.py` catches `(OSError,
subprocess.TimeoutExpired)` and returns the exception text as a plain `str` instead of a
`CompletedProcess`; `_rows_read` and `_file_read` build on it and turn that `str` into
`outcome="raised"`. `viewer_login` calls `_attempt` directly. None of the three callers
(`alan/collision.py`, `alan/deploy_runs.py`, `alan/engine_preflight.py`) imports `subprocess` for
this reason any more — `engine_preflight.py` keeps `import subprocess` solely because
`_check_repo_clonable`'s `git ls-remote` at line ~150 is unchanged and still calls
`subprocess.run` directly (see "Boundary" below); `_attempt` is not reused there because
`_check_repo_clonable` is version-control transport, out of this PR's forge-only scope. `_attempt`
was written new rather than widening the existing `_gh_or_none`, because `_gh_or_none` is pinned to
`GH_TIMEOUT_S` and discards the exception text the deploy-run diagnostic's retryable message needs;
widening it would have touched the ten existing adapter methods that already call it.

**Each caller passes its own timeout explicitly; the new operations take no default.**
`open_prs`/`workflow_runs`/`viewer_login`/`repo_file` all declare `timeout: float` as a required
keyword parameter with no `GH_TIMEOUT_S` fallback. Collision passes `60.0` (unchanged from its old
`GH_TIMEOUT_S`-sourced value), `fetch_deploy_runs` passes `20.0` (unchanged), engine preflight
passes `_GH_USER_TIMEOUT_S = 30.0` for `viewer_login` and `_DEPENDABOT_CHECK_TIMEOUT_S = 30.0` for
`repo_file` (both unchanged). A `GH_TIMEOUT_S` default would have silently tripled the deploy-run
diagnostic's worst case from 20s to 60s inside an already-budget-constrained escalation path.

**`fetch_deploy_runs` no longer shells out at all** — its signature changes from `repo: str` to
`forge_target: ForgeTarget`, since a bare repo string cannot carry a credential. Its sole production
caller, `alan/teams/deploy_verify.py::_diagnostic_detail`, now passes
`_pr_forge.forge_target_for_ws(workspace)` — the same real forge target its other port call
(`_pr_merge_info`'s `read_pr`) already carries — rather than the slug-derived value `_repo_slug`
used to build. `_repo_slug` keeps its three other call sites unchanged. `fetch_deploy_runs` maps
`RowsRead` to its existing three-way retryable contract: `raised` → `ServerErrorRetryable(f"gh run
list --repo {slug} raised {stderr}")`, `failed` → `ServerErrorRetryable(...rc=...: stderr[:200])`,
`ok` with `rows is None` → `ServerErrorRetryable("gh run list returned unparseable or non-list JSON
payload")`. `parse_deploy_runs_payload(stdout: str) -> list[DeployRun] | None` keeps its exact
public signature (out of scope); both it and the new `fetch_deploy_runs` path now share one private
`_runs_from_rows(rows: Sequence[JsonObject]) -> list[DeployRun]` helper.

**Engine preflight's two `gh` sites move onto `viewer_login` and `repo_file`; `git ls-remote`
does not move.** `_author_login`/`_reviewer_login` used to build `gh api user -q .login` by hand
around a private `_resolve_login` helper (now deleted); they now call `active_forge().viewer_login(
forge_target=forge, as_verifier=False|True, timeout=_GH_USER_TIMEOUT_S).login`, returning `None` on
any outcome other than `"ok"` — the same "cannot determine" verdict `_check_reviewer_distinctness`
read before. `_check_dependabot_config` used to build `gh api repos/{slug}/contents/{path}` by hand
with an env dict assembled from `secrets_store.resolve_secret`; it now calls
`active_forge().repo_file(_DEPENDABOT_CONFIG_PATH, forge_target=forge,
timeout=_DEPENDABOT_CHECK_TIMEOUT_S)` and maps `raised` → the existing `unknown` Finding, **both
`absent` and `failed`** → the existing `fail` Finding verbatim (repo_file's port-level distinction
between the two is not surfaced as a different verdict here — the next caller to adopt `repo_file`
is the one that needs `absent` specifically), `ok` → the existing `pass` Finding. `_check_repo_clonable`'s
`git ls-remote` (line ~150) is unchanged and keeps `import subprocess`/`import os` alive in this
module — a test
(`test_check_repo_clonable_still_drives_subprocess_run_not_the_forge_port` in
`tests/test_engine_preflight.py`) asserts it still drives `subprocess.run` directly and that the
active fake forge records zero calls, the deliberate proof that DoD-3's no-forge-transport-faked
criterion is scoped to *forge* transport and does not (and could not yet) extend to the
repository-reachability check's `git` call. The now-unused `from alan.teams import _pr_branch`
import is removed from `engine_preflight.py`.

**Test infrastructure.** None of the four new operations is in `FakeForge`'s
`_ADAPTER_BACKED_BY_DEFAULT`: each must fail as `unstubbed forge call` when a test does not
explicitly stub it, because an adapter-backed default would let the collision check, deploy-run
listing or engine preflight reach a real `gh` process and be silently refused by the repository's
`gh` guard rather than exercising the intended scenario. `tests/forge_stubs.py` gained
`rows_read(rows=(), *, outcome="ok", returncode=0, stderr="")`, `login_read(login="octocat", *,
outcome="ok", ...)` and `file_read(text="", *, outcome="ok", ...)` builders so caller tests
construct results without repeating the dataclass literals. The adapter-level outcome matrix — argv
assembly, all outcome branches of `open_prs`/`workflow_runs`/`viewer_login`/`repo_file`, and direct
branch coverage of `_attempt`/`_rows_read`/`_file_read` — lives in the new
`tests/test_forge_gh_caller_ops.py`, sibling to `tests/test_forge_gh_transport_ops.py` (which stays
untouched apart from its pinned operation count). No bare `"clone"`, `"fetch"` or `"push"` string
literal appears inside any of the four new `GhForge` methods or the three new private helpers —
`tests/test_transport_canary.py`'s `_EXPECTED_FORGE_GH_NETWORK_FUNCTIONS` stays the empty set it was
before this PR.

**What is enforced.** `alan/collision.py`, `alan/deploy_runs.py` and `alan/engine_preflight.py`
moved from `RECORDED_ARGV_MODULES` to `MIGRATED_MODULES` in `tests/forge_argv.py`, so
`test_migrated_module_calls_none_of_the_three_transport_entry_points` now holds for all three and
`test_recorded_set_equals_the_modules_that_assemble_a_gh_argv` no longer finds a `gh`-argv literal
in any of them. `TRANSPORT_ENTRY_POINTS` is unchanged — it was never the reason `deploy_runs.py`
escaped the enumeration; `deploy_runs.py` used bare `subprocess.run`, which the AST walker's
`is_gh_argv` predicate (matching a literal `"gh"` first element) still caught independently of
`TRANSPORT_ENTRY_POINTS`. `tests/test_forge_gh_transport_ops.py`'s
`test_no_operation_accepts_an_environment_credential_or_transport_shaped_parameter` now counts
twenty-two operations on both `ForgePort` and `GhForge` (eighteen before this PR); `repo_file`'s
file-path parameter is named `repo_path`, not `path`, specifically because that same test's
`_FORBIDDEN_PARAMETER_NAMES` bans a parameter literally named `path` on any port operation — an
artifact of the generic-passthrough ban, not a signal that `repo_file` is one. `FAMILY_MODULES`
(`tests/forge_patch_sites.py`) is untouched at eleven: none of the three modules this PR touches is
a family module. `tests/size_baseline.py` is untouched; `tests/test_collision.py` lands under its
1381-line pin (measured 1349 non-blank lines after this PR), and the two other caller test files
(`tests/test_deploy_runs.py`, `tests/test_engine_preflight.py`) are unpinned and stay under the
1000-line test cap.

## X2.7 PR9: the onboarding scaffold's PR calls

`alan/repo_scaffold.py` had three `gh` call sites left: the head-branch PR lookup that makes
`scaffold()` idempotent across a lost state file (`_find_existing_pr`), the scaffold PR creation
(`_create_scaffold_pr`) and the merged-state read the onboarding checklist polls
(`_view_scaffold_pr_state`). All three now go through the port, onto three new operations —
`prs_by_head`, `create_pr`, `pr_state` — added in the same position (after `repo_file`) on the port,
the adapter, the fake and this table. **The operation count moves from twenty-two to twenty-five.**

- **`prs_by_head(head_branch, fields, *, forge_target, limit, timeout) -> RowsRead`** is
  `gh pr list --repo {slug} --head {head_branch} --state all --json {fields} --limit {limit}`.
  `--state all` is pinned inside the adapter, not a parameter — the caller needs "any PR for this
  branch, open or already merged," and a state parameter would be the generic-passthrough shape the
  port forbids. It goes through `_rows_read`, exactly as `open_prs` does, so it joins the swallow
  list: a raised transport fault or a non-zero exit both come back as a `RowsRead` the caller reads
  rather than a raised exception.
- **`create_pr(title, body, head_branch, *, forge_target, workdir, timeout) -> ApiResult`** is
  `gh pr create --repo {slug} --title {title} --body {body} --head {head_branch}`, run with
  `cwd=str(workdir)` — the scaffold's temporary clone — under the transport identity. It calls
  `_pr_branch.run_subprocess` directly with no `try`/`except`, so a missing `gh` binary or a timeout
  propagates straight to `_open_or_find_scaffold_pr`, unchanged from today: creation has never
  swallowed a transport fault, and this migration does not start.
- **`pr_state(pr_number, *, forge_target, timeout) -> ApiResult`** is
  `gh pr view {pr_number} --repo {slug} --json state -q .state`, the same server-side
  `-q`-extracted command line the caller built by hand. It also calls `_pr_branch.run_subprocess`
  directly with no `try`/`except`, because `_view_scaffold_pr_state` keeps its own
  `except (OSError, subprocess.TimeoutExpired)` — the network-unreachable "unknown" finding on the
  onboarding checklist depends on that caller-side catch, not a port-level swallow.

All three authenticate as the repository's transport credential, derived inside the adapter by the
same `_transport_env(forge_target)` the other X2.7 PR6/PR8 operations already use — no operation
takes an environment, a credential mapping or a parameter named `path`, `env`, `environ` or `token`.
`timeout` is a required keyword on all three, with no `GH_TIMEOUT_S` fallback; the caller passes its
existing `_GH_TIMEOUT_S = 60.0`. The scaffolder's own credential-absent guards
(`f"no transport credential for {forge.repo.slug!r}"` in `_open_or_find_scaffold_pr`,
`"no credential available to check PR state"` in `_check_scaffold_pr_merged`) stay ahead of every
call, so both messages still appear exactly as before and neither guarded path ever reaches the fake
forge — `tests/test_repo_scaffold.py` pins `_active_fake_forge.calls == []` for both.

**Rejected: amending `read_pr` instead of adding `pr_state`.** `pr_state`'s `-q .state` is a scalar
extracted server-side, which `read_pr`'s field-tuple contract cannot express, and giving `read_pr` a
credential parameter to reuse it here would widen a shared result type every other `read_pr` caller's
test file already asserts against — their recorded calls would change across PRs that have nothing
to do with this one. A new, narrow operation was cheaper than reshaping an operation eight other call
sites already depend on.

**Rejected: reusing `prs_by_head` for the state read.** The scaffold branch name is reused across
onboarding attempts, so a head-branch lookup can return a different, stale PR once the original is
closed and a new one opened on the same branch name; the state read needs the PR number the caller
already has on record, not a lookup that can silently answer a different question.

**Rejected: per-transport ledger re-modelling, and bare-subprocess `git` sites.** Splitting
`tests/forge_argv.py`'s ledger by transport kind mid-migration would be a gate change unrelated to
this PR's three call sites. Converting the scaffold's `git` sites to bare `subprocess.run` calls
would remove them from the enumeration without actually migrating them onto anything — gaming the
ledger rather than passing it — so the four `git` sites (clone, checkout -b, the add/status/commit
steps, and the `ls-remote` reachability probe) are unchanged, still calling `_pr_branch.run_subprocess`
(three of them) or `subprocess.run` directly (the probe).

**Rejected: swallowing a fault on creation.** A failed `gh pr create` has never been a "maybe" for
this caller — it either opens a PR or the scaffold run fails loudly — so absorbing the fault the way
`prs_by_head` does would be a behaviour change with no caller asking for it, not a migration.

**A declared widening on the lookup.** `_find_existing_pr`'s dead
`except (FileNotFoundError, subprocess.TimeoutExpired)` is deleted: those faults are now absorbed
inside the adapter's `_rows_read`/`_attempt` and come back as `RowsRead(outcome="raised", ...)`,
which the caller already reads as "no existing PR" alongside `outcome="failed"` and
`outcome="ok"` with empty or `None` rows. `_parse_pr_row`'s guard widens from `isinstance(parsed,
list)` to `isinstance(parsed, (list, tuple))`, because `RowsRead.rows` is a tuple, and
`GhForge`'s own `_json_objects` already drops non-dict rows before the caller ever sees them — a
JSON `[1, 2]` payload used to leave `_find_existing_pr` a raw list to index and mis-key against; it
now never reaches `_parse_pr_row` with anything but dict-or-nothing rows.

**What is enforced.** `alan/repo_scaffold.py` drops out of `tests/forge_argv.py`'s
`RECORDED_ARGV_MODULES` (14 → 13); it is deliberately **not** added to `MIGRATED_MODULES`, which
stays at sixteen, because three of its four version-control sites still call
`_pr_branch.run_subprocess` and the migrated set's own test
(`test_migrated_module_calls_none_of_the_three_transport_entry_points`) forbids any of the three
transport entry points, `git` argv included. Closing that gap is X2.19's, the version-control port —
this PR only removes the module's `gh` argv. `FAMILY_MODULES` (`tests/forge_patch_sites.py`) stays at
eleven: `repo_scaffold.py` was never a family module. `tests/test_transport_canary.py`'s
`_EXPECTED_FORGE_GH_NETWORK_FUNCTIONS` stays the empty set — none of the three new methods contains a
`clone`/`fetch`/`push` literal. `tests/size_baseline.py` is untouched; the net backend delta lands
under the granted `X2.7:forgeport-rest-of-family` allowance.

## X2.7 PR10: repo status and recurring forge reads

`alan/repo_status.py` (the repository-status panel's main-CI and open-PR fields) and
`alan/recurring_forge.py` (the recurring-workflow status's workflow list, latest runs and Dependabot
reads) each carried their own `gh` argv. Both now reach the forge only through `ForgePort`, and both
leave `RECORDED_ARGV_MODULES` for `MIGRATED_MODULES`. **The operation count moves from twenty-five to
thirty.**

**Untangling first.** `recurring_forge` used to reach `repo_status`'s private helpers through six
identity re-export aliases (each with a private-usage suppression): the refresh executor, the refresh
completion, the two `gh` helpers, the timestamp helper and the unknown-provenance builder. The two
`gh` helpers (`_gh_api_dict`, `_gh_pr_list`) are deleted with the migration. The four that remain
shared are public names in `repo_status` — `REFRESH_EXECUTOR`, `end_refresh`, `now_iso` and
`unknown_provenance` (not a bare `unknown`, which the local variables of three functions there would
shadow) — imported plainly by `recurring_forge` next to `Provenance` and `begin_refresh`, and
`recurring_status`'s one broken import became `unknown_provenance`. The other aliases stay: the
engine-state-directory alias in `recurring_forge`, the deploy-audit alias in `repo_status`, and
`recurring_status`'s `_resolve_engine` and `_scoped_rows`.

**The five operations, and why they reuse `RowsRead` and `str | None`.** `default_branch ->
str | None`, and `commit_check_runs`, `latest_workflow_run`, `workflows`, `prs_by_author` -> `RowsRead`
(the method table above has the argv and callers). `RowsRead` already carries the three-way split both
callers need — `failed`/`raised` (the read did not work), `ok` with `rows == ()` (it worked and there is
nothing) and `ok` with `rows=None` (it worked and nothing parseable came back) — and a new result type
per operation would have restated it. `default_branch` needs no split because both callers treat every
failure the same way ("could not read the repo's default branch"), so `None` is enough. No operation
takes a raw argument vector, a path, an environment or a credential; the transport identity is derived
inside the adapter, as for every PR6/PR8/PR9 operation.

**`_envelope_read`: the rule for reads of a JSON object that wraps a list.** `commit_check_runs`,
`latest_workflow_run` and `workflows` read `gh api` responses shaped `{"<key>": [...]}` through a new
private helper beside `_rows_read`. A raised transport fault is `raised`; a non-zero exit is `failed`;
**a zero exit whose body is not a JSON object is also `failed`** (return code and stderr kept), because
`repo_status` distinguishes "could not read check-runs" from "the response was missing a `check_runs`
array" and the old `_gh_api_dict` collapsed every non-object body into the former. A JSON object whose
key is missing or not a list is `ok` with `rows=None`; a list is `ok` with its non-object items dropped.
This differs from `_rows_read`, which reads a bare JSON list and reports a zero exit with an
unparseable or non-list body as `ok` with `rows=None` — that is what `open_prs`, `prs_by_head` and
`prs_by_author` callers already depend on and it is unchanged. `default_branch` does not use either
helper: a non-object body is `None` (the "could not read" verdict), and an object without a usable
`default_branch` is `"main"`. That fallback now lives in the adapter, so callers no longer carry it;
`alan/branch_protection_compat.py` keeps its own copy of the fallback until that module migrates.

**`prs_by_author` and `open_prs_by_author` coexist, and the `Literal` is not a passthrough.**
`open_prs_by_author` (PR6) is untouched: it folds every failure into `()`, which is right for the
dependency sweep and wrong here, where "the listing failed" must not read as "zero pull requests".
`prs_by_author` is the outcome-carrying counterpart, and it takes `state: Literal["open", "all"]`
because its two callers need both (`"@me"` open, `"app/dependabot"` all). The PR6 and PR9 passages
above say `--state open` (`open_prs_by_author`) and `--state all` (`prs_by_head`) are pinned rather than
parameters, and they stay true of those two operations. A closed two-value type is not the free-form
state parameter those passages reject: a caller cannot pick an arbitrary string through it. That
closure is enforced by the type checker, not by a runtime check in the adapter.

**The credential gate.** Each refresh — `refresh_forge_status` and `refresh_recurring_forge_status` —
begins with `secrets_store.transport_credential(forge)` as its first statement, before any
`active_forge()` call. With no credential every forge-backed field reports
`"no credential available for this repo's forge"` (the strings are unchanged) and no forge operation is
reached; a caller test in each module asserts the fake recorded zero calls. `transport_credential` is
the one public derivation, placed beside `resolve_secret`: the stripped secret, or `None`. The adapter's
`_transport_env` (which no longer reaches into `_pr_branch`) and both callers use it. The legacy
`_pr_branch._transport_gh_env` kept its private copy until X2.7 PR15 deleted it — "shared" means the
adapter and these two callers — and `secrets_store.transport_env` (the git-askpass variant) is X2.19's.
**Residual, on the owner's install and an unconfigured install:** the adapter does not refuse to run
without a credential, so if the secret disappears between the entry gate and a later operation of the
same refresh, that operation runs under the machine's ambient `gh` login. Closing that there would change
who production acts as and is out of scope. **On a copy the residual is closed:** the seam refuses
without a credential, so that operation returns its failure result and `gh` is not invoked ("Identity on
a copy" above).

**Declared behaviour change A: the adapter drops non-object list items.** `_envelope_read` (and the
`_rows_read` that `prs_by_author` shares) keeps only object items, as `open_prs` already did. The
consequences, all of them:

- A lone non-object workflow run now reads as confirmed-absent in the recurring view:
  `_fetch_latest_run` returns `(None, True)` where it returned `(None, False)`, so `last_run_confirmed_absent`
  is `True` and the process reads `never_ran` rather than `unknown`.
- In repo status's suite lookup (`_workflow_suite_id`), a non-object first run followed by a real one now
  uses the real run's `check_suite_id`; before, the non-object first entry ended the lookup with `None`
  and the field fell back to all checks. A lone non-object run still ends in `None`.
- Non-object pull-request rows are no longer counted: the open-PR `total` and `authored_by_alan` count
  objects only, and the latest Dependabot PR is read from the first object row rather than being lost
  when the first row is not an object.

The workflow list and the check-run list were already filtered to objects by their old callers, so they
are parity, not part of this change.

**Declared behaviour change B: a whitespace-only secret is no credential.** `transport_credential`
strips, so a secret that is only whitespace counts as absent and both views report
`"no credential available for this repo's forge"` in every forge-backed field. Before, it passed the
callers' truthiness check and every field then failed on its own read (`"could not read the repo's
default branch"`, `"gh pr list failed"`). A padded secret is now sent stripped as `GH_TOKEN` by these
two callers, as the adapter already did.

**Everything else is parity.** Every provenance detail string is unchanged (including `"gh pr list
failed"`, `"gh pr list --author @me failed"` and `"from gh actions workflow API"`); each read keeps the
30-second bound, still taken from `alan/branch_protection_compat.py::GH_API_TIMEOUT_S`, which both
modules keep importing; and on the open-PR count a successful call with no parseable rows still reads
`unknown`, never zero. One payload detail widens: the port's `open_prs` sends `--json number,title,url`
where the old repo-status call sent `--json number`; the counts do not depend on the extra fields.

**What is enforced.** `alan/recurring_forge.py` and `alan/repo_status.py` moved from
`RECORDED_ARGV_MODULES` (13 → 11) to `MIGRATED_MODULES` (16 → 18) in `tests/forge_argv.py`, so neither
assembles a `gh` argv or calls a transport entry point. The operation-count pin in
`tests/test_forge_gh_transport_ops.py` is thirty on both `ForgePort` and `GhForge`. Deleting the
subprocess-transcript seams from the two callers' tests lowered the family patch-site count from 1223 to
1221 (still 107 files), and `RECORDED_PATCH_SITES` in `tests/forge_patch_sites.py` is re-pinned to that
measured figure. `tests/size_baseline.py` is untouched, no `_`-private module was added and the import
layers did not change.

**Tests.** The adapter's transcripts for the five operations (argv, every outcome, the `"main"`
fallback, percent-encoding, change A, and the credential environment) are in the new
`tests/test_forge_gh_status_ops.py`; the pin-only changes are in `test_forge_gh_transport_ops.py` and
`test_forge_gh_caller_ops.py`. The two callers' tests drive the strict fake: none of the five names is in
`_ADAPTER_BACKED_BY_DEFAULT`, so an unstubbed call fails loudly, and the assertions read returned or
cached results because `REFRESH_EXECUTOR` futures swallow exceptions. Four caller tests make exactly one
operation adapter-backed (through the fake's `stub`, with the transport patched) to pin change A end to
end: `test_fetch_latest_run_reads_a_lone_non_object_run_as_confirmed_absent`,
`test_latest_dependabot_pr_reads_past_a_non_object_row_through_the_adapter`,
`test_fetch_open_prs_no_longer_counts_non_object_rows_from_the_adapter` and
`test_workflow_suite_id_ignores_a_lone_non_object_run_through_the_adapter`.

**Merge-lane dry run.** The roadmap asks every caller PR to be exercised under the merge-lane dry-run
switch. That is vacuous here: neither module is on the merge lane.

**Left for later.** The branch-protection compatibility module and the GitHub repository probe that
uses a pasted token are untouched (the probe waits on an explicit-credential operation), the two views'
duplicated cache and refresh machinery is a follow-up, and the other ten recorded modules and any `git`
call site (X2.19) are unchanged. Three of the other ten recorded modules moved in X2.7 PR11: the two
PR reads and the verifier's identity probe (see below).

## X2.7 PR11: the verifier identity probe, closing links and the restart probe's merge read

Three more production `gh` callers moved onto operations the port already had. **No operation was
added and none changed: the operation count stays thirty on the port and on the adapter, and
`FakeForge`'s method set and `_ADAPTER_BACKED_BY_DEFAULT` are untouched.**

- **The verifier's post-approval identity check**
  (`alan/teams/_verifier_thread_resolution.py::_confirm_identity`) was a hand-built
  `gh api user -q .login` carrying the verifier credential as an environment mapping. It is now
  `viewer_login(forge_target=..., as_verifier=True, timeout=GH_TIMEOUT_S)`; the adapter derives the
  environment itself, so the caller no longer builds or passes one.
- **The closing-issue links read** (`alan/teams/_pr_closing.py::gh_pr_closing_issue_numbers`) is now
  `read_pr(pr_number, ("closingIssuesReferences",), ...)`. It keeps its public name, signature and
  defaults, so `alan/merge_gate.py` and the by-name test monkeypatches are unchanged.
- **The deploy restart probe's merge-commit read**
  (`alan/teams/_deploy_restart_probe.py::_merge_commit_sha`) is now
  `read_pr(pr_number, ("mergeCommit",), ...)`.

**Which identity each call runs as.** The two PR reads run under the ambient identity, exactly as
before: `read_pr` builds no environment. The identity check runs as the per-repository reviewer
credential (`as_verifier=True`), the same credential the hand-built call carried.

**The credential gate stays caller-side, and no credential means no forge call.** `_prepare` asks
`_pr_post.verifier_identity_available(forge_target)` before anything else. With no reviewer credential
for the repository it audits `no_reviewer_credential` and returns without touching the forge at all,
and it never falls back to another repository's credential or to the default identity — the two tests
that pin this (`test_resolve_after_approval_holds_when_no_reviewer_credential` and
`test_secondary_engine_without_reviewer_credential_never_falls_back_to_default_login`) assert
`_active_fake_forge.calls == []`. The adapter's own `no_credential` outcome is only a race backstop
now: the credential resolved in the gate and vanished before the port call.

**Outcome-to-reason mapping.** The hold reasons are unchanged: `no_reviewer_credential`,
`identity_check_failed`, `login_mismatch`. `outcome == "ok"` with the expected login continues; `ok`
with another login holds as `login_mismatch` (`observed_login` recorded). Every non-`ok` outcome
holds as `identity_check_failed`, and its `error` text is derived from the `LoginRead` by
`_failure_text`: `outcome == "failed"` with `returncode == 0` — the adapter folds an empty login into
`failed` — is `"empty login"`, the text the audit row always carried; anything else is
`stderr[:500]`, which for a `raised` outcome is the exception text the old `except` clause stringified.
The `no_credential` race therefore yields `error == ""`.

**The probe's repository target.** Before, the probe's `gh pr view` named no repository and resolved
whichever one the working directory was in; now it passes `engine_config.DEFAULT_FORGE_TARGET`
explicitly. Parity holds because the service's `WorkingDirectory` (`deploy/ai.alan.plist`, rendered
to the alan checkout) is alan's own repository, so the implicit repository was alan's own. The 60-second bound is unchanged, and
`tests/test_teams_deploy_restart_probe.py` asserts the exact recorded call (fields, target and
timeout). The probe's classification outcomes are unchanged too: each unusable read — non-zero exit,
missing or `null` `mergeCommit`, empty `oid`, timeout, missing binary — is still
`merge_sha_unavailable`, and every test asserts the whole `(bool, reason, sha)` tuple, so an
unstubbed forge call (which `classify_merge` would swallow as `classifier_error`) cannot pass as a
generic deferral.

**The closing read keeps its failure semantics.** A `FileNotFoundError` or `subprocess.TimeoutExpired`
still raises `ForgeTransientError("gh pr view closingIssuesReferences raised …")` chained from the
cause, and a non-ok read still goes through `_forge_errors.raise_for_infra` (returning `None` when the
stderr is not an infrastructure failure). JSON-shape handling now belongs to the adapter's `read_pr`:
the module's own `safe_json` and top-level-dict check are gone, and so is the test that fed it a
non-dict payload.

**The two-tier set treatment.** `_pr_closing.py` and `_deploy_restart_probe.py` both still run `git`
through the shared `_pr_branch.run_subprocess` helper (commit-author emails and the diff stat in one,
`git fetch` in the other), which is the version-control port's, X2.19. Following the
`repo_scaffold.py` precedent (X2.7 PR9), a module whose remaining transport calls all carry `git`
leaves `RECORDED_ARGV_MODULES` — it builds no `gh` command line any more — but does not join
`MIGRATED_MODULES`, whose test forbids any of the three transport entry points. Moving those `git`
calls to bare `subprocess` calls to escape the ledger is rejected for the reason recorded under PR9.
`_verifier_thread_resolution.py` is different: nothing is left in it, so it joins the migrated set.

**What is enforced.** Measured against this change's base commit:

- `tests/forge_argv.py`: `RECORDED_ARGV_MODULES` 11 → 8 (`_deploy_restart_probe.py`,
  `_pr_closing.py` and `_verifier_thread_resolution.py` leave it); `MIGRATED_MODULES` 18 → 19, with
  `_verifier_thread_resolution.py` the only addition.
- `tests/forge_patch_sites.py`: `RECORDED_PATCH_SITES` moved from `(1232, 109)` to `(1212, 109)`.
  `FAMILY_MODULES` stays at eleven, and the credential-availability tests keep driving
  `stub_verifier_env` rather than patching `_pr_post`, which would have added family patch sites.
- The tests for the three migrated reads stub the forge operation instead of faking a subprocess.
  Subprocess fakes remain for `git` (the salvage tests, the restart probe's `git fetch`, which now
  fails an assertion if it ever sees a non-`git` argv) and for the operations the test forge backs
  with the real adapter by default (`post_review` in the wiring tests). Tests that reach the identity
  check indirectly stub `viewer_login`, including the review-post retry test, which used to count
  three subprocess calls and now counts two.
- `tests/size_baseline.py`, `pyproject.toml` and `CLAUDE.md` are untouched; the backend delta lands
  under the granted `X2.7:forgeport-rest-of-family` allowance and is a net decrease.

**Left for later.** `alan/teams/_pr_branch.py` (its own PR); `alan/branch_protection_compat.py`,
`alan/teams/implementation.py` and `alan/investigator.py` (each waits on an owner ruling — an
explicit-credential operation, the PR-author identity question, and how the gate should classify a
citation allowlist of `gh`-prefixed tuples); the four callers that need new operations —
`alan/teams/_pr_body.py` and `alan/teams/_copilot_resolution.py` (done in X2.7 PR12, below),
`alan/teams/unblock_issue.py` and `alan/teams/triage_pregate.py` (the next X2.7 PR); every `git` site (X2.19); the
duplicated `mergeCommit.oid` parse in `alan/teams/deploy_verify.py::_extract_merge_sha`, which accepts
an empty `oid` where the probe rejects one; and the deploy restart gate's missing engine input, a
latent cross-engine concern this PR keeps at parity rather than fixing.

## X2.7 PR12: PR body, thread reply and PR comment

Two more production callers stopped building their own `gh` command lines: the PR-body reader/amender
(`alan/teams/_pr_body.py`) and the Copilot review-resolution logic (`alan/teams/_copilot_resolution.py`).
**Three operations were added — the port and the adapter now have thirty-three — and `FakeForge` gained
the same three, none of them adapter-backed by default, so a test that forgets to stub one fails
loudly with `unstubbed forge call`.** Reading the PR body needed no new operation: it is `read_pr`.

- **`update_pr_body(pr_number, body, *, forge_target)`** replaces a pull request's description
  (`alan/teams/_pr_body.py::amend_pr_body`). It is the old `gh api -X PATCH .../pulls/{n} --input`
  call, unchanged on the wire.
- **`reply_to_thread(thread_id, body, *, forge_target)`** posts a reply on a review thread
  (`alan/teams/_copilot_resolution.py::_reply_outcome`). The mutation text is
  `_THREAD_REPLY_MUTATION` in `forge_gh.py`, beside the resolve mutation, for the reason given under
  "Layering": a port whose caller owns a query string is not a domain port. `_reply_outcome` passes
  `DEFAULT_FORGE_TARGET`, which is inert here — the mutation addresses the thread by node id — so
  `post_reply` keeps its public signature and the CLI is not touched by it.
- **`comment_on_pr(pr_number, body, *, forge_target)`** posts a conversation comment on a pull request
  (`_comment_outcome`). It is the old `gh pr comment --repo --body` call, now built by the adapter from
  the caller's target.
- **`fetch_pr_body`** is `read_pr(pr_number, ("body",), forge_target=..., timeout=GH_TIMEOUT_S)`; its
  command line is byte-identical to the old `gh pr view --json body`. JSON decoding and the top-level
  object check moved into the adapter, so a malformed payload arrives as `fields == {}` and is
  indistinguishable from a missing `body` key; both still return `None`, as do a non-zero exit, a
  non-string `body`, a missing binary and a timeout.

**Identity: strict parity, nothing moved.** No new operation takes an environment, credential, token,
header, method or path parameter (the op-count test pins thirty-three on both owners and keeps its
forbidden-parameter-name check). `update_pr_body` calls `run_gh_api_json_input` (a module function of `forge_gh` since X2.7 PR15) with no environment;
`reply_to_thread` and `comment_on_pr` go through `_run_gh(..., as_verifier=False)`, whose environment is
`None`. None of the three touches `_transport_env`: moving them onto the stored transport credential
would silently change who acts, which parity forbids. The adapter tests stub both credential
derivations to return a token and assert the recorded environment is still `None`.

**Faults propagate, and that is load-bearing.** None of the three has a `try`/`except`, and none goes
through `_attempt`, `_gh_or_none` or `_gh_succeeded`: a `FileNotFoundError` or `subprocess.TimeoutExpired`
reaches the caller unchanged, as it does for `create_pr` and `pr_state`. `_copilot_resolution` counts
exactly those two as `ambiguous`, which keeps the thread claim (or acked comment id) and the
acknowledgement budget, because a timed-out reply may already have landed. A port that folded a timeout
into a failed result would make the caller release both for a reply that exists, and the next round
would post it again — a duplicate-reply storm. `amend_pr_body` likewise maps a fault to
`ForgeTransientError("gh pr body amend raised ...")`, a retryable error a folded result would hide as an
ordinary not-ok. A non-zero exit is `ok=False` with `returncode` and `stderr` carried verbatim.

**Caller outcomes are unchanged.** `amend_pr_body`: a non-zero exit goes through
`_forge_errors.raise_for_infra("gh pr body amend", ...)` and otherwise reports `ok=False` with stderr cut
to 500 characters; success writes the `pr_body_amend_ok` audit row with the previous and new body.
`_reply_outcome` and `_comment_outcome`: a non-zero exit is `failed` (claim and budget released), a
missing binary or timeout is `ambiguous` (both kept), success is `posted`. The command-line tool keeps
collapsing `failed` and `ambiguous` to exit 1.

**The CLI installs the adapter for both modes.** `scripts/alan_copilot_resolve.py` used to call
`set_active_forge(GhForge())` only on the thread path, because only `resolve_thread` needed it.
`post_pr_comment` now reaches the forge through `active_forge()` too, so `comment_ack` mode would have
raised `no active forge installed` in the standalone process. `main()` now installs the adapter once,
before either mode; `test_main_comment_ack_mode_installs_the_gh_adapter_when_no_server_installed_one`
proves it, modelled on the thread-mode test.

**The two-tier set treatment.** `_pr_body.py` has nothing left that touches the transport, so it moves
from `RECORDED_ARGV_MODULES` to `MIGRATED_MODULES`. `_copilot_resolution.py` still runs
`git rev-parse HEAD` through `_pr_branch.run_subprocess` (`_current_head_sha`), which is the
version-control port's, X2.19. It builds no `gh` command line any more, so it leaves
`RECORDED_ARGV_MODULES`, but it stays off `MIGRATED_MODULES`, whose test forbids all three transport
entry points — the same precedent as `repo_scaffold.py` (X2.7 PR9) and `_pr_closing.py` (X2.7 PR11). A new
AST test in `tests/test_forge_argv_enumeration.py` pins the two-tier claim: every transport entry-point
call in `_copilot_resolution.py` has a list-literal first argument whose first element is `"git"`.

**What is enforced.** Measured against this change's base commit:

- `tests/forge_argv.py`: `RECORDED_ARGV_MODULES` 8 → 6 (`_pr_body.py` and `_copilot_resolution.py`
  leave it); `MIGRATED_MODULES` 19 → 20, with `_pr_body.py` the only addition.
- `tests/forge_patch_sites.py`: `RECORDED_PATCH_SITES` moved from `(1212, 109)` to `(1182, 109)`.
- `tests/test_forge_gh_transport_ops.py`: the operation-count pin moved from 30 to 33, and the three
  operations are in the shared success/failure/missing-stream/fault-propagation parametrization beside
  the PR1 ones, each with its own command-line test (the reply test compares against a literal mutation
  string, not the module constant).
- Tests that reached the PR-body read only through the suite's `gh` stub (return code 86) now stub
  `read_pr` explicitly, the way sibling files already did with `gh_unreachable()` — the
  implementation-fix live-checks tests and the verification scenario-runner scoping tests. The
  copilot-resolution and implementation-fix copilot tests stub the two write operations on the fake and
  keep their `run_subprocess` patches only for the `git rev-parse` and the adapter-backed `resolve_thread`.
- `tests/size_baseline.py`, `tests/gh_read_baseline.py`, `pyproject.toml` and `CLAUDE.md` are untouched.

**Left for later.** `alan/teams/unblock_issue.py` (the issue filer) and `alan/teams/triage_pregate.py`,
and all five operations they need — the next X2.7 PR (landed as "X2.7 PR13: unblocker issues and triage reads" below); `alan/teams/_pr_branch.py` (its own PR);
`alan/branch_protection_compat.py`, `alan/teams/implementation.py` and `alan/investigator.py` (each
waits on an owner ruling); every `git` site, including `_copilot_resolution.py`'s `rev-parse` (X2.19).

## X2.7 PR13: unblocker issues and triage reads

The last two callers that built their own `gh` command lines stopped doing so: the recipe unblocker's
issue filer (`alan/teams/unblock_issue.py`) and the triage pre-gate (`alan/teams/triage_pregate.py`).
**Five operations were added — the port and the adapter now have thirty-eight — and `FakeForge` gained
the same five, none of them adapter-backed by default.** Two reads needed no new operation: the triage
merged-state read is `read_pr` and the unblocker's label creation is `create_label`. The method table
above carries a row for each new operation and the new callers on those two.

- **Unblocker.** `find_open_issue` is `open_issues_by_title`; `ensure_label` is `create_label`
  (result discarded, as before); `create_issue` is `create_numbered_issue`; `comment` is
  `comment_on_issue`. `IssueClient`, `ensure_shape_issue` and the module's public names are unchanged.
- **Triage.** `_fetch_raw_prs` and `_fetch_open_pr_rollups` are `prs_by_head_prefix`;
  `_fetch_pr_by_number` (behind `verify_pr_merged`, which `alan/routes/workspaces.py` calls, and behind
  the ghost check `is_merged_ghost`, which `alan/routes/health.py` calls) is `read_pr`;
  `_latest_main_run` is
  `latest_branch_run`. Every triage catch-all is retained: the port folds only `OSError` and
  `subprocess.TimeoutExpired`, and the pre-gate has always turned *any* exception into "no data", so
  dropping the `except Exception` would have narrowed it. The routes import the same names from
  `triage_pregate` as before, and their tests are untouched.
- **Why two more issue operations beside the existing pair.** `create_issue` returns a boolean and
  swallows faults; the unblocker needs the URL (it parses the issue number from it) and must tell a
  timed-out create, which may have landed, from a refusal. `search_open_issues` returns titles only.
  Both keep their contracts for their other callers; neither is widened.
- **Fault conventions, per operation.** `create_numbered_issue` and `comment_on_issue` propagate, so
  `create_issue` and `comment` keep their `FileNotFoundError` / `subprocess.TimeoutExpired` catches,
  each with a warning log, returning `None` and `False`; any other exception (for example
  `PermissionError`) still propagates to the drain's catch-all. The three reads go through
  `_rows_read`. `find_open_issue` maps `failed` and `raised` to `IssueLookup(ok=False)` — the caller
  never files an issue after a failed lookup — and logs a warning on `raised`. The triage reads map
  `failed`, `raised`, an empty list and `rows=None` to an empty list, no pull request or an unknown CI
  surface, as before.

**Which repository the CI reads target.** Each registered engine's main-CI and deploy reads address
that engine's own repository: `_target_for(config.repo)` builds a `ForgeTarget` from `config.repo`
and the default forge's credential *name* (the reads run ambient, so the name is inert).
`forge_target_for_workspace` is forbidden here: for an engine with CI configured but no `forge` block
it falls back to the default forge, which would report another engine's CI as alan's own.
`alan/repo_status.py` calls the resolver only behind an explicit `cfg.forge is None` guard that
returns an unknown response; triage cannot use that guard, because a CI-configured engine without a
forge block must still be read. The tests pin this: a forge-less engine, an engine whose `forge`
block names a different repository, and two engines in one `gather_all` each send both reads with
their own slug, never the default one. Surface ids and observations still carry `config.repo.slug`.

**The pull-request reads still target only the default repository.** `_fetch_raw_prs`,
`_fetch_open_pr_rollups` and `_fetch_pr_by_number` use `_target_for(_ENGINE_DEFAULT.repo)`, as the old
module constant did. That is a latent multi-engine gap (a workspace of a second engine is matched
against the default repository's pull requests) and is kept at parity here, pinned by a test that
registers a second engine and asserts the reads still address the default repository.

**Identity.** The two triage operations pass no environment, so they run under the ambient `gh`
login exactly as the old `subprocess.run` calls did; the adapter tests stub `transport_credential` to
return a token and assert the recorded environment is `None`. The three issue operations run as the
transport identity through `_transport_env`, which is what the unblocker's `_env` resolved. **One
accepted identity deviation, two cases.** `_transport_env` trims the credential and treats a blank
one as absent, where the unblocker's `_env` sent the raw value. So a token with surrounding
whitespace is now sent trimmed, and a whitespace-only environment or store value now leaves the
ambient `gh` login in effect, where before it was sent as a `GH_TOKEN` override that could only fail.
`env={}` and `env=None` were already equivalent in `run_subprocess`, so an absent credential is not a
change.

**Degenerate payloads that crashed and now read as "no data".** The adapter parses, so a payload that
made the old inline `json.loads` code raise (caught by the catch-all, or not caught where the
consumer indexed into it) is folded: a non-list pull-request-list payload gives `rows=None` and so
`[]`; a list-shaped `pr view` payload gives `fields == {}`, which `verify_pr_merged` reads as not
merged; non-object items in a pull-request list or a run list are dropped, so a leading non-object
run no longer crashes `_classify_main_ci_run`. Garbage `pr view` stdout on a successful exit now
gives `{}` instead of `None`; both callers of `_fetch_pr_by_number` treat the two identically. On the
issue search, an `OSError` other than a missing binary (say `PermissionError`) now reads as a failed
lookup, so nothing is filed, instead of propagating to the drain's catch-all.

**Kept at parity: an unparseable successful listing.** When the issue search exits 0 with output
that is not a JSON list, `rows` is `None` and `find_open_issue` reports `ok=True, number=None` —
"no open issue" — exactly as `_matching_number` did, so the unblocker goes on to file. That is a
duplicate-filing risk when the listing is merely unreadable; it is not fixed here and is filed as
alan#1800.

**The fail-on-unstubbed check.** A production catch-all can swallow the fake's "unstubbed forge call"
assertion, so a test that forgets a stub used to pass silently. `FakeForge` now remembers which
operations a test stubbed explicitly (`stub()`), and when an operation that was *not* explicitly
stubbed is reached from a frame in `alan.teams.triage_pregate` or `alan.teams.unblock_issue` — the
first frame outside `tests/fakes/forge.py` decides — it records the name in `scoped_unstubbed` and
raises. Adapter-backed defaults (`create_label`) count as unstubbed, so the unblocker's label call
cannot reach a real `gh`. The autouse `_active_fake_forge` fixture is `installed_fake_forge()`, which
restores the previous forge and then fails the test at teardown if `scoped_unstubbed` is non-empty,
whatever a catch-all did with the exception. That covers the pre-gate's reads, the issue-intent drain
and the sweep's default issue client. No production catch is narrowed to get this. It is scoped to
those two paths on purpose: the same fake called from a test module keeps the plain `unstubbed forge
call:` message and records nothing, and an adapter-backed default reached from any other module is
untouched. `tests/test_forge_scoped_unstubbed.py` proves both halves: it fires through the pre-gate's
broad catch, the drain's catch-all and the sweep's default client, for each of the five new
operations, and stays silent for explicit stubs, test modules and unscoped callers.

**What is enforced.** Measured against this change's base commit:

- `tests/forge_argv.py`: `RECORDED_ARGV_MODULES` 6 → 4 (`triage_pregate.py` and `unblock_issue.py`
  leave it); `MIGRATED_MODULES` 20 → 22, with those two the only additions. A new AST test in
  `tests/test_forge_argv_enumeration.py` asserts neither module makes a `subprocess.*` call or a bare
  `run`/`Popen`/`check_output`/`call`/`check_call`; an `except subprocess.TimeoutExpired` reference
  is allowed.
- `tests/test_forge_gh_transport_ops.py`: the operation-count pin moved from 33 to 38 on both owners
  and keeps its forbidden-parameter-name check; `prs_by_head_prefix`'s `state` is pinned to
  `Literal["open", "all"]` beside `prs_by_author`'s.
- Error contract: eighteen of thirty-eight operations propagate (sixteen before).
- `tests/forge_patch_sites.py`: `RECORDED_PATCH_SITES` moved from `(1182, 109)` to `(1169, 109)`. The
  issue-op adapter tests reuse the file's existing `run_subprocess` recorder (the comment operation's
  payload is read at call time, so it needs no second patch), and the rewritten unblocker tests use
  the fake, which removed fourteen patch sites; the triage-op adapter tests add one.
- `tests/test_triage_pregate.py` only shrinks. Its subprocess-driven forge tests moved to fake stubs in
  `tests/test_triage_pregate_ci_reads.py` and `tests/test_triage_pregate_pr_lists.py`.
- `tests/size_baseline.py`, `tests/gh_read_baseline.py`, `pyproject.toml` and `CLAUDE.md` are untouched.

**Left for later.** Four recorded modules still build their own command lines:
`alan/branch_protection_compat.py`, `alan/investigator.py` and `alan/teams/implementation.py` each
wait on an owner ruling, and `alan/teams/_pr_branch.py`, the PR-branch helper, has its own PR. Every
`git` call site stays X2.19's. Re-expressing `IssueClient` in terms of `ForgePort` calls is not done here.

## X2.7 PR14: PR view reads and branch update

Slice 1 of the `alan/teams/_pr_branch.py` migration: six of the module's `gh` command lines moved into
the adapter, and the module kept the policy around each one — audit rows, failure classification,
outcome mapping and the `on_fail` reason strings. **Four operations were added — the port and the
adapter now have forty-two — and one was retyped in place.** `FakeForge` gained the same four and the
retyped one.

| Interaction | Operation | `_pr_branch` site |
|---|---|---|
| a PR's head branch name | `pr_head_branch` (new) | `resolve_existing_branch` |
| requesting a branch update | `request_branch_update` (new) | `update_pr_branch_outcome` |
| whether a PR is open | `pr_open_state` (new) | `pr_is_open` |
| a PR's changed file names | `changed_files` (rebuilt) | `_fetch_diff_files` |
| a PR's head commit | `head_sha` (retyped) | `_fetch_pr_head_sha` |
| open PR titles for the pre-dispatch scan | `open_pr_titles` (new) | `list_open_prs` |

Each command is byte-identical to the one the module used to build, including the scalar-extraction
`-q` expressions, `--name-only` sitting before the PR number, and the absence of `--repo` on the open-PR
listing. Each runs under the ambient identity and reaches the process runner through the
`_pr_branch.run_subprocess` module attribute, read at call time, with only `cwd=None` and
`timeout=GH_TIMEOUT_S` — no `env=` keyword. Identity parity is strict: `pr_open_state` runs the command
`pr_state` runs but as a different identity, and `open_pr_titles` is not `open_prs`, so neither
existing operation was reused. `head_sha` used to return `str | None` and had no production caller, so
retyping it to `ApiResult` costs nothing; it lets `_fetch_pr_head_sha` tell a raised fault, a non-zero
exit with its error text and an empty stdout apart, which is what its three `on_fail` reasons
(`gh subprocess error: …`, `gh returncode=N: …`, `head_sha_empty`) and the `sha_lookup_failed` branches
of the merge path depend on. The operations that return `ApiResult` let `FileNotFoundError` and
`subprocess.TimeoutExpired` propagate, so each `_pr_branch` caller keeps the `try`/`except` it had:
`resolve_existing_branch` raises `ForgeTransientError` from the fault, `update_pr_branch_outcome` maps
it to `FAILED`, `pr_is_open` maps any exception to unknown, and `list_open_prs` maps a missing
executable or a timeout to an empty list while any other `OSError` still propagates.
`_handle_pr_view_failure` now takes an `ApiResult`; `resolve_existing_branch` is its only caller. An
`ApiResult` carries `""` where a `CompletedProcess` could carry `None`, which no caller's outcome
depended on.

**Why these operations are adapter-backed by default.** PR12's and PR13's operations are strict in
`FakeForge`: an unstubbed call raises. These five are in `_ADAPTER_BACKED_BY_DEFAULT` instead, so an
unstubbed call reaches the real adapter and through it `_pr_branch.run_subprocess`. The reason is the
test files that cannot change in this PR: `tests/test_teams_pr_merge.py` and
`tests/test_teams_implementation.py` are size-pinned, `tests/gh_read_baseline.py` is a shrink-only
list, and those tests answer the head-commit read through a scripted `gh` process double keyed on the
exact argv rather than stubbing the port. A strict default would fail every one of them with `unstubbed
forge call`, and editing them would be a size-pin raise. The price is that a test which forgets a stub for one
of these five reaches the process layer (and the gh guard's read stub) instead of failing loudly; the
scoped-caller check from PR13 still covers `triage_pregate` and `unblock_issue`. **`changed_files` stays
strict**: no pinned test file reaches it through the process layer, so its consumers' tests can be made
to stub it without touching one. The one test file that did reach it that way,
`tests/test_clause_audit_record_kind.py`, now stubs it as `None`, the value a down `gh` gives.

**Termination.** `update_branch` still calls `_pr_branch.update_pr_branch`, which calls
`update_pr_branch_outcome`, which now calls `request_branch_update` — never `update_branch`. Wiring the
new operation back to `update_branch` would recurse forever in production while every fake-based test
stayed green, because the fake's `update_branch` never reaches the outcome logic.
`tests/test_pr_branch_no_recursion.py` installs the real adapter, doubles only the process runner and
asserts one process call for each of the three wrapper paths (`update_branch` on success, conflict and
failure — each also one audit row — `head_sha` and `changed_files`) and for `fetch_flake_guard_head_sha`
and `fetch_diff_files`. `head_sha` and `changed_files` build their commands in the adapter and call
nothing in `_pr_branch` but the process runner.

**`gh_checked` is deleted.** The generic run-and-raise-on-failure helper had no production caller since
X2.7 PR3. Its four tests, the three adapter tests that existed only to prove `read_pr`, `read_checks`
and `merge_pr` never used it, and the two adapter tests that asserted `head_sha` and `changed_files`
delegated to `_pr_branch` (an intentionally inverted claim now) are deleted with it. The detector
vocabulary keeps the name (`TRANSPORT_ENTRY_POINTS` in `tests/forge_argv.py` and the enumeration test's
fixture), so a reintroduced helper of that name is still recognised as a command-line builder.

**Dropped diff-failure warnings.** `_fetch_diff_files` used to log a warning on a transport fault and a
different one, with the return code and the first 200 characters of stderr, on a non-zero exit. The
adapter is deliberately logger-free (`pr_patch` set the precedent) and `changed_files` returns `None` for
both, so the helper can no longer tell them apart and logs nothing.

**What is enforced.** Measured against this change's base commit:

- `tests/test_forge_gh_transport_ops.py`: the operation-count pin moved from 38 to 42 on both owners and
  keeps its forbidden-parameter-name check, which now also covers the four new operations.
  `tests/test_forge_gh_pr_view_ops.py` writes each of the six argv vectors out in full and pins the
  runner keyword arguments to exactly `cwd` and `timeout`; its runner double takes no `env`, so a stray
  `env=` raises `TypeError`.
- `tests/fakes/forge.py` and `tests/test_forge_fake.py`: the strictness table names the five
  adapter-backed operations and keeps `changed_files` strict.
- `tests/forge_argv.py`: this slice left `_pr_branch.py` in `RECORDED_ARGV_MODULES` because it still built
  the check-run reads, the check-suite re-request, the workflow rerun and the failed-job log command
  lines. X2.7 PR15 moved those onto the port and moved the module to `MIGRATED_MODULES` (recorded four
  modules to three, migrated twenty-two to twenty-three).
- `tests/forge_patch_sites.py`: `RECORDED_PATCH_SITES` moved from `(1169, 109)` to `(1142, 111)`. The
  `_pr_branch` tests for these six interactions stub the port and stopped patching the process layer;
  the two new test files each patch `_pr_branch.run_subprocess` through a single fixture.
- `tests/test_teams_pr_merge.py`, `tests/test_teams_implementation.py`, `tests/gh_read_baseline.py`,
  `tests/size_baseline.py`, `pyproject.toml` and `CLAUDE.md` are untouched.

**Left for slice 2 (done in X2.7 PR15).** The check-run listing, the check-run annotations (and their two identities), the
check-suite re-request, the workflow rerun, the status-rollup read and the failed-job log; removing the
generic trigger helper; moving the JSON-payload helper into the adapter; removing the transport-credential
helper (`_transport_gh_env`, named in the notes of this document that slice 2 owns); and taking
`_pr_branch.py` off `RECORDED_ARGV_MODULES` and onto `MIGRATED_MODULES`. Rerouting the merge team off the
`update_branch` wrapper waits for the item's final deletion pass, as does moving the process runner.

## X2.7 PR15: check-run reads and gate reruns

Slice 2 of the `_pr_branch.py` migration (slice 1 is "X2.7 PR14: PR view reads and branch update"
above). Seven more operations take the module's last forge interactions behind the port, so
`_pr_branch.py` builds no `gh` command line and calls none of the three transport entry points; it
moved from `RECORDED_ARGV_MODULES` (four modules to three) to `MIGRATED_MODULES` (twenty-two to
twenty-three). The port and the adapter now have forty-nine operations each. The process runner,
`run_subprocess`, is still defined in `_pr_branch.py`; moving it, and rerouting the merge team off the
`update_branch` wrapper, wait for the item's final deletion pass.

| interaction | operation | site |
|---|---|---|
| a commit's check runs, 100 per page | `commit_check_runs_page` (ambient) | `_iter_check_runs` |
| a check run's annotations, ambient identity | `check_run_annotations` | `fetch_check_run_annotations(as_transport=False)`, the SonarCloud path |
| the same annotation read, transport identity | `transport_check_run_annotations` | `fetch_check_run_annotations(as_transport=True)`, the gate-evidence and failing-E2E paths |
| re-requesting a check suite | `rerequest_check_suite` (ambient) | `_rerequest_check_suite` |
| rerunning a workflow run's failed jobs | `rerun_failed_jobs` (ambient) | `_rerun_workflow_run` |
| a PR's status-check rollup | `status_check_rollup` (transport) | `fetch_failing_job_ids` |
| a failed job's log | `failed_job_log` (transport) | `fetch_failed_log` |

**Byte-identical commands, identity unchanged.** Each command line is the one the module used to build,
including `?per_page=100` on the listing and the annotation read, `-X POST` before the re-request path,
and `--failed --repo {slug}` after the run id on the rerun. The two annotation reads are two operations
because the identity is part of the call pattern; one private argv builder, `_check_run_annotations_argv`,
serves both so the command line cannot drift between them. No operation takes an environment or
credential mapping: the ambient ones run with none, the three transport ones resolve the credential inside
the adapter through `secrets_store.transport_credential`. Each reaches the process runner through the
`_pr_branch.run_subprocess` module attribute, read at call time, and returns slice 1's raw-result shape
(`ApiResult`); a missing executable or a timeout propagates to the caller, so every caller keeps the
`try`/`except` it had and every failure reason stays word for word.

**Two accepted runner-keyword differences.** The runner sees these keywords differently from before, and
the child process's environment does not change either time, because `run_subprocess` builds it as
`{**os.environ, **env}` when `env` is not `None` and inherits the parent's otherwise:

- With no transport token, the three transport reads (`transport_check_run_annotations`,
  `status_check_rollup`, `failed_job_log`) pass `env=None` where `_pr_branch._transport_gh_env` used to
  pass `env={}`.
- The ambient annotation read (`check_run_annotations`) passes no `env` keyword where
  `fetch_check_run_annotations` used to pass an explicit `env=None`.

**Why these operations are adapter-backed by default.** All seven are in `_ADAPTER_BACKED_BY_DEFAULT` in
`tests/fakes/forge.py`, for the reason PR14's five are: `tests/test_teams_pr_merge.py` (which answers the
rollup read from the merge team's state queue), `tests/test_teams_dependency_sweep.py`,
`tests/test_teams_implementation_fix.py`, `tests/test_teams_implementation.py` and
`tests/test_triage_pregate.py` are size-pinned and answer these reads through argv-keyed process doubles.
A strict default would fail them with `unstubbed forge call`, and editing them would be a size-pin raise.

**Removed and moved helpers.** `_gh_trigger_ok`, the generic run-`gh`-and-audit helper the re-request and
rerun shared, is removed, and so is `_transport_gh_env`, the module's private copy of the transport
credential derivation. `run_gh_api_json_input` moved into `alan/forge_gh.py` as a module-level function, not
a public adapter operation, so the operation count stays forty-nine. Its body still calls
`_pr_branch.run_subprocess` through the module attribute at call time: bound at import instead, the verifier's
review post, the PR-body update and the issue comment would fall through to the `gh` guard in the tests that
double the runner on `_pr_branch`.

**`_audited_forge_result` is not a trigger helper.** The caller-side private helper that replaces
`_gh_trigger_ok` in the listing, the annotation read, the re-request and the rerun takes a typed
`Callable[[], ApiResult]` — a port call — and never an argv, so it cannot become the generic passthrough the
owner's ruling forbids. It carries the verbatim `gh subprocess error: …` and `gh returncode=N: …` reasons
(stderr cut to 200 characters) and the per-site log labels, and it hands the audit callback back to the
caller's own `on_fail`.

**Signatures.** `_iter_check_runs`, `fetch_check_run_annotations`, `_rerequest_check_suite` and
`_rerun_workflow_run` take a `forge_target` in place of separate owner and repository names;
`fetch_check_run_annotations` replaces its `env` mapping with `as_transport: bool = False`. The callers in
`_pr_sonar`, `_verification_gates` and `_pr_ready` follow; `_pr_ready`'s edit is caller-only.
`fetch_failing_e2e_tests`, `fetch_failing_job_ids`, `fetch_failed_log`, `fetch_failing_check_details` and
`list_sonarcloud_annotations` keep their public names and signatures.

**Status-rollup read: call count and order.** `fetch_failing_e2e_tests` makes exactly one
`status_check_rollup` read per lookup, before any annotation read, then at most five annotation reads. The
E2E stub `gh` answers that read from the merge team's state queue (`ALAN_E2E_GH_PR_STATE_FILE`), so an
extra or reordered call would consume the wrong entry.

**The transport credential is resolved per call.** The E2E lookup used to resolve it twice (once for the
rollup and once for the loop's shared environment); it now resolves it once for the rollup and once for each
annotation read, 1+N times, with the same value each time.

**The re-request and the rerun are not dry-run gated.** The dry-run switch does not gate
`rerequest_check_suite` or `rerun_failed_jobs`, so they must not be exercised against a live PR; the safe
exercise is the E2E suite with the stub `gh`.

**What is enforced.**

- `tests/test_forge_gh_transport_ops.py`: the operation-count pin moved from 42 to 49 on both owners and
  keeps its forbidden-parameter-name check, which now also covers the seven new operations.
- `tests/test_forge_gh_check_run_ops.py` (new; every runner call is doubled, no test spawns `gh`): a full
  argv and keyword transcript per operation; the raw-result shape and the propagation of a missing
  executable and a timeout; the runner read at call time; the ordered rollup-then-annotations transcript of
  `fetch_failing_e2e_tests`; the identity split on a non-default forge; the reason and label parity of
  `_audited_forge_result` through the listing, the annotation read, the re-request and the rerun; and the
  absence of the removed helpers. Its isolation proof replaces `forge_gh._pr_branch` with a namespace holding
  only `run_subprocess` and calls all seven operations and `run_gh_api_json_input`: it passes only if no new
  adapter operation reads anything else in the PR-branch module.
- `tests/fakes/forge.py` and `tests/test_forge_fake.py`: all seven operations are adapter-backed by default.
- `tests/forge_argv.py`: `RECORDED_ARGV_MODULES` is three modules, `MIGRATED_MODULES` twenty-three, and
  `_pr_branch.py` is **not** added to the stricter raw-subprocess check, because its own `run_subprocess`
  body calls `subprocess.run`.
- `tests/forge_patch_sites.py`: `RECORDED_PATCH_SITES` moved from `(1142, 111)` to `(1144, 112)`, re-measured
  by hand. The new test file patches `_pr_branch.run_subprocess` through one fixture and one late-binding
  case, and the gate-rerun tests no longer patch the deleted `_transport_gh_env`.
- `tests/test_file_primitive_families.py`: the deliberately-unflagged writer example for
  `run_gh_api_json_input` now points at `alan/forge_gh.py`.
- `tests/test_teams_pr_merge.py`, `tests/test_teams_dependency_sweep.py`,
  `tests/test_teams_implementation_fix.py`, `tests/test_teams_implementation.py`,
  `tests/test_triage_pregate.py`, `tests/gh_read_baseline.py`, `tests/size_baseline.py`, `pyproject.toml`,
  `CLAUDE.md`, `alan/teams/pr_merge.py` and the E2E `gh` stub are untouched.

## X2.7 PR16: one default forge target, and the dispatcher's two leaking callers

The migration left the default engine's forge target computed in ten places, and two dispatcher call
paths that fell back to it where a non-default engine's workspace should have supplied its own. This
change consolidates the value and fixes those two callers. It moves no `gh` command line, adds no port
operation, and adds no import-linter layer.

**One constant.** `alan/engine_config.py` holds the backend's only module-level default forge target:
`DEFAULT_FORGE_TARGET: ForgeTarget = ForgeTarget.from_forge_config(_DEFAULT_FORGE)`, defined on the line
after `_DEFAULT_FORGE`. It is the exact value the resolver's call-time fallback returns, and it sits in the
lowest layer every former holder already imports. It replaces ten separate module-level computations of
that value: five public ones (`alan/teams/_pr_forge.py`, `alan/branch_protection_compat.py`,
`alan/engine_preflight.py`, `alan/merge_recovery.py`, `alan/secrets_store.py`) and five private copies
(`alan/collision.py`, `alan/delivery.py` — behind the delivery read — `alan/teams/_pr_merge_checks.py`,
`alan/teams/dependency_sweep.py`, `alan/teams/pr_merge.py`). Every consumer in the backend, the tests and
the two operator scripts (`scripts/alan_copilot_resolve.py`, `scripts/alan_forge_contract_check.py`)
imports it from `alan.engine_config` under that name: there is no `__all__` facade and no same-name alias
re-export. Parameter defaults still default, now to the shared value.

The one exception is the merge team. `alan/teams/pr_merge.py` keeps `_DEFAULT_FORGE_TARGET: ForgeTarget =
DEFAULT_FORGE_TARGET`, a module-level binding of its old private name to the shared object — a reference,
not a second computation. It exists because `tests/test_teams_pr_merge.py` reads it as
`pr_merge_mod._DEFAULT_FORGE_TARGET` and sits at its size pin, so renaming those reads would grow the file
past the pin.

**What is enforced.** All in `tests/test_engine_config_forge.py`:

- `test_default_forge_target_pinned_by_value` compares the constant to a literal `ForgeTarget` (repository,
  credential env name, authoritative checks), so a change to the default engine's forge facts fails by
  value, not by name. `test_default_forge_target_matches_resolver_fallback` ties it to
  `forge_target_for_workspace(None, None)`, and `test_pr_merge_private_name_is_the_shared_constant` pins
  the `is` identity of the merge team's binding.
- `test_no_module_level_default_resolver_call_outside_engine_config` is an AST scan over `alan/**/*.py`
  and `scripts/*.py`, minus `alan/engine_config.py`. It flags a `forge_target_for_workspace` call — a bare
  name or a module attribute — whose `engine_key` and `snapshot_config` are both absent or the literal
  `None`, bound by position or by keyword, when the call runs at import time: a module statement, a class
  body, a parameter default or a decorator argument. A call inside a function or lambda body does not run
  at import time and is not flagged. A parametrized source table exercises the predicate on positive and
  negative sources, in the style of `tests/test_forge_patch_sites.py`.

**Ruling: the merge serialization lock stays in the merge team.** `_MERGE_LOCK` (`alan/teams/pr_merge.py`)
is not moved into the forge adapter. Invariant A8 (`pr_iteration_pipeline.md`, §A8) makes the lock cover the
BEHIND recheck, the merge call and the audit rows as one critical section; a lock taken inside the
adapter's `merge_pr` would cover the merge call alone and so narrow A8.

**Ruling: the workspace-to-target resolution module stays.** `alan/teams/_pr_forge.py` keeps
`forge_target_for_ws` and `governing_config_for_ws` because they are real resolution helpers; only the
constant left it.

**The sibling-overlap diff read now uses the workspace's own target.** `Dispatcher._audit_sibling_overlap`
called `_pr_branch.fetch_diff_files(handover.pr_number)` with no target, so a non-default engine's
terminal merge had its changed files read from the default engine's repository. It now passes
`forge_target=_base.forge_target_for_ws(ws)`, the dispatcher's existing workspace-to-target resolver. For
the default engine the read names the same repository as before.

**The remediation retry counter is now keyed by the blocked attempt's target.**
`Dispatcher._maybe_retry_pr_merge` loaded and bumped `pr_<n>_remediation_retries.json` through
`_pr_state_paths` with no target, so every engine's counter lived in the default engine's state directory.
It now takes a required fourth parameter, `forge_target`, which `_pr_merge_conflict_or_retry` fills with
`MergeGateBlocked.forge_target` — the non-optional target the merge-gate attempt itself used — and
`load_remediation_retry_count` and `bump_remediation_retry_count` both receive it.

**Named behaviour change.** For a non-default engine the retry counter now lives under
`state/<owner>/<repo>/` and starts fresh. Counts already written into the default engine's state
directory stay where they are, orphaned: never read again for that engine and never cleaned up. Rollout
effect: a second engine's pull request that is mid-remediation at the deploy that lands this change gets up to
`REVISIT_CAP` extra automatic retries, once. The default engine is unchanged: the same namespaced counter
file is used, and the legacy unscoped file is still read as a fallback for the default forge.
`tests/test_dispatcher_forge_target_routing.py` proves both halves against the real state paths under a
temporary `ALAN_STATE_ROOT`, and `tests/test_dispatcher_manifest_overlap.py` proves that the diff read is
called with the workspace's target for a registered non-default engine and with the default target
otherwise.

**Identity checks keep their meaning.** `_dispatcher_selfheal._probe_checkout_for` (the "is this workspace
alan itself" check) and `_pr_state_paths._is_default_forge` (the check that confines the legacy state
layout to alan) still compare `repo.slug` only, now against `engine_config.DEFAULT_FORGE_TARGET`; they
are not tightened to whole-value equality.

**Deliberately untouched.** The deploy restart probe still passes the default constant to its merge-commit
read; only the way it refers to the constant changed. The two Copilot thread-reply and thread-resolve
sites (`alan/teams/_pr_threads.py`, `alan/teams/_copilot_resolution.py`) still pass the default constant,
where the target never reaches the command line. `alan/branch_protection_compat.py`, one of the three
owner-walled modules that still assemble `gh` command lines, changes only its local constant and its
import: its command lines are byte-identical and `RECORDED_ARGV_MODULES` still names the same three
modules. `RECORDED_PATCH_SITES` stays at `(1144, 112)`, because the new tests patch no forge-family
module.

## X2.7 PR17: the state and credential layer requires the forge target

Every forge-aware function in the per-PR state layer and the credential layer took its forge target as
optional and fell back to alan's own repository when a caller left it out. A caller that forgot the
workspace's target then read or wrote alan's state, or resolved alan's credential, on behalf of another
engine, and neither type checker nor test suite noticed; PR16 fixed two such callers in the dispatcher.
This change removes the fallback from those functions, so leaving the target out is a type error. It moves no `gh` command line, adds no port operation, and adds no
import-linter layer.

**Which parameters became required.** 53 defaulted parameters lose their `= DEFAULT_FORGE_TARGET` default,
with no sentinel and no runtime fallback in their place:

- `alan/teams/_pr_state_paths.py`: all 45 — `_engine_state_dir` and the path, load, save, bump and release
  functions for acked comments, gate reruns, reconcile attempts, remediation retries, acked, replied and
  resolved thread markers, the ack budget, the system-head marker, fix cycles and capability waivers.
- `alan/merge_recovery.py`: 4 — `classify_block` and `needs_sonar_annotations` take a required positional
  `forge_target`; `reconcile_attempts_exhausted` takes it required and keyword-only;
  `attempt_mechanical_recovery` takes it required and keyword-only, after its still-defaulted
  `annotations`, because Python forbids a required positional after a defaulted one. The merge gate's
  production call now passes it by keyword.
- `alan/secrets_store.py`: 2 — `transport_credential` and `transport_env`.
- `alan/loop_guard.py`: 1 — `fix_cycle_ceiling_reached`.
- `alan/engine_preflight.py`: 1 — `load_last_preflight`.

**This supersedes a PR16 statement.** PR16 said parameter defaults still default, now to the shared value.
That no longer holds for these five areas; it still holds for the forge-call family and the slot pool
below.

**Unchanged.** `_pr_state_paths._is_default_forge` — the check that confines the legacy unscoped state
layout to alan — stays and keeps comparing `repo.slug` against `engine_config.DEFAULT_FORGE_TARGET`; it was
never a default. The legacy fallback in `_read_json_with_legacy_fallback` is unchanged, so every state path
is byte-identical for every engine, the target that reaches each credential lookup is the same, and no audit
row changes. `tests/test_pr_state_paths.py` keeps its default-engine path assertion, now as
`test_engine_state_dir_explicit_default_forge_is_namespaced_alan`: passing the shared constant explicitly
yields `state/DejvMatikar/alan`, the path the omitted default used to produce.

**The restart probe was the one production caller relying on a default.**
`alan/teams/_deploy_restart_probe._fetch_commit` resolved its git-fetch credentials with
`secrets_store.transport_env()` and no target. It now passes `forge_target=DEFAULT_FORGE_TARGET`
explicitly, which is the value the omitted default resolved to and the constant its merge-commit read on
the same probe already passed; the probe still verifies alan's own deploys only. The old test replaced
`transport_env` with an argument-ignoring `lambda *a, **k: {}`, which is why nothing had noticed. The
shared `_stub_merge_and_fetch` helper in `tests/test_teams_deploy_restart_probe.py` now installs a fake that
records its arguments and returns them, and
`test_fetch_resolves_transport_env_for_the_default_forge_target` asserts the probe called
`transport_env(forge_target=DEFAULT_FORGE_TARGET)`. It adds no textual patch of a forge-family module.

**Deferred: the slot pool.** `alan/slot_pool.py` still defaults to the shared constant in two places, and
this change does not touch them. The constructor's `_resolve_forge_target_for_pool` (`alan/slot_pool.py`)
turns the unset marker `_FORGE_TARGET_UNSET` into `DEFAULT_FORGE_TARGET`, and twelve private helpers carry a `forge_target: ForgeTarget | None = DEFAULT_FORGE_TARGET` default:
`_fetch_base`, `_fetch_existing_branch`, `_prepare_new_branch`, `_fetch_for_resume`, `_fallback_prepare`,
`_prepare_resume`, `_refresh_base_best_effort`, `_prepare_existing_branch`, `_warmup_command_env`,
`_run_warmup_command`, `_run_warmup` and `_init_slot`. The infra route that builds the default pool
(`alan/routes/infra.py`) and the deploy doc are untouched for the same reason. A later slice takes it.

**Deferred: the forge-call family and the runner move.** The `forge_target` keyword-only defaults in the
branch helpers, readiness, the flake ledger, push, the merge team, the merge checks and PR feedback stay,
along with the doc passages that quote them (`merge_recovery.md`'s flake-ledger signature and
`pr_iteration_pipeline.md`'s keyword-only default passages). Moving the runner out of the branch helpers and
removing the update-branch wrapper is a separate later slice.

**What is not added.** `RECORDED_PATCH_SITES` stays at `(1144, 112)`, and there is no new structural gate
against reintroducing a default: the type checkers are the guard for real callers. Neither the size
baseline nor the forge-argv record is edited.

**Residual.** The type checkers cover direct calls in `alan/` (pyright) and in `alan/` plus `tests/` (mypy).
A test that patches one of these functions with a catch-all `*a, **k` fake, or with a fake whose target
parameter defaults to `None`, still hides a real caller that omits the target: the fake accepts the call the
real function would now reject. Indirect calls — a thread's argument tuple, a `functools.partial`, a
callable passed as an object — are likewise visible only to the test suite; this change swept the known
ones by hand.

## X2.7 PR18: the branch helpers, flake ledger, freshness check and Sonar helpers require the forge target

Four modules of the forge-call layer took their forge target as optional and fell back to alan's own
repository when a caller left it out: the PR branch helpers, the flake ledger, the measured-baseline
freshness check and the SonarCloud helpers. A caller that forgot the workspace's target then read or wrote
alan's PRs, checks or state on behalf of another engine, and neither type checker nor test suite noticed.
This change is the first half of the forge-call family's deletion pass (slice 6c-1): it removes the
fallback from these four modules, so leaving the target out is a type error in the checked code and a
`TypeError` at runtime everywhere else. It is a pure guard. It moves no `gh` command line, adds no port
operation, adds no import-linter layer and adds no structural gate against a default returning.

**Which parameters became required.** 36 defaulted parameters lose their `= DEFAULT_FORGE_TARGET` default,
with no sentinel and no runtime fallback in their place. Each keeps its name, kind, annotation and
position:

- `alan/teams/_pr_branch.py`: 15 — `verifier_gh_env`, `resolve_existing_branch`,
  `update_pr_branch_outcome`, `update_pr_branch`, `pr_is_open`, `_fetch_diff_files`, `_fetch_pr_head_sha`,
  `fetch_flake_guard_head_sha`, `fetch_failing_check_details`, `rerun_external_gate`,
  `rerun_flaky_checks`, `fetch_failing_job_ids`, `fetch_failed_log`, `fetch_check_annotations` and
  `fetch_failing_e2e_tests`.
- `alan/teams/_pr_flake_ledger.py`: 18 — `gate_reruns_exhausted`, `flake_failures_path`,
  `_load_flake_failures`, `_save_flake_failures`, `failing_e2e_spec_names`, `failing_e2e_specs`,
  `record_flake_occurrence`, `gate_survived_checks_path`, `load_gate_survived_checks`,
  `record_flake_survival`, `survived_at_head_checks`, `evidenced_actionable_failing_checks`,
  `fetch_pr_diff_patch`, `diff_attributable_e2e_checks`, `_has_open_flake_proposal`,
  `_ensure_flake_label`, `_open_flake_issue` and `_maybe_open_flake_proposal`. The module's import of
  `DEFAULT_FORGE_TARGET` goes with the last default.
- `alan/teams/_pr_freshness.py`: 1 — `is_measured_baseline_stale`.
- `alan/teams/_pr_sonar.py`: 2 — `list_sonarcloud_annotations` and `retrigger_sonar_analysis`.

Six of the 36 are positional-or-keyword and stay positional, because production callers — the ledger
itself, `alan/forge_gh.py`, `alan/teams/_pr_post.py` and `alan/teams/pr_merge.py` — pass them by position:
`verifier_gh_env` (`forge_target`) and the ledger's `flake_failures_path`, `_load_flake_failures`,
`_save_flake_failures`, `gate_survived_checks_path` and `load_gate_survived_checks` (`forge`). The other 30
stay keyword-only. Making the six keyword-only would have forced edits to 6c-2 call sites for no gain.

**What still defaults, and why.** Two leftovers and one non-parameter are deliberate:

- `_pr_branch.credential_recovery_recipe` takes `forge_target: ForgeTarget | None = None`. Its default is
  "no target", which builds the generic recipe and never falls back to alan, so it was never one of the
  defaults that leak.
- `_pr_branch.list_open_prs` takes no target and passes `DEFAULT_FORGE_TARGET` explicitly to the open-PR
  title listing. It only runs for alan's own scope, so `_pr_branch.py` keeps its import of the constant.
- The Sonar hotspot helpers — `sonar_hotspots_pr_url`, `sonar_ack_hotspots` and `_fetch_hotspot_keys` —
  take no forge target at all; they talk to SonarCloud, not the forge. They are unchanged.

**Production: no caller omitted the target.** Every production caller of the 36 parameters already passed
one, so no production call site changed. pyright over `alan/` and mypy over `alan/` and `tests/` report no
missing target in `alan/`. For every engine the state paths, `gh` command lines, credentials and audit rows
are byte-identical to before. The evidence that the guard holds is that the parameters have no default and
that the backend gates pass.

**Tests.** Every test call that left the target out now passes it, as `DEFAULT_FORGE_TARGET` positionally
for the six positional parameters and `forge_target=DEFAULT_FORGE_TARGET` for the rest. Tests of real
default-engine behaviour keep asserting the same thing with the default passed explicitly: the legacy
fallback reads for the flake-failure ledger and the gate-survivor record, the namespaced ledger path for
alan, per-engine isolation (the default engine's files are not written when another engine's are) and the
verifier-credential test showing that the default's repository and credential name reach the secret
lookup. Two tests existed only to prove that an omitted target defaulted, and are deleted:
`test_forge_target_defaults_to_the_default_forge_target` (`tests/test_pr_freshness.py`) and
`test_fetch_pr_diff_patch_defaults_to_the_default_forge_target` (`tests/test_pr_flake_ledger_forge.py`).
Each duplicated the explicit-target test next to it. The string-literal source fixtures in
`tests/test_forge_seam_merge_family.py` are text scanned by the seam gate, never executed, and are left
alone. `RECORDED_PATCH_SITES` stays at `(1144, 112)`, no test file is added and no size baseline is edited.

**Still defaulting in 6c-2: direct forwarders.** These functions keep their own default and pass alan's
default straight into a now-required parameter, so their default still reaches the four modules:

- `_pr_post.dismiss_stale_verifier_reviews` into `_pr_branch._fetch_pr_head_sha`.
- `implementation_fix._maybe_fetch_sonar` into `_pr_sonar.list_sonarcloud_annotations`, and
  `implementation_fix._maybe_fetch_gate_failures` into `_pr_branch.fetch_failing_check_details` — the fix
  team's Sonar and gate-failure fetches.
- `_verification_gates._resolve_actionable_gate_checks` into
  `_pr_flake_ledger.evidenced_actionable_failing_checks` — the verification gates' actionable-check
  resolution.
- `dependency_sweep._detect_upstream_block`, whose `ctx` defaults to `_DEFAULT_SWEEP_CTX` (a
  `SweepContext` built on `DEFAULT_FORGE_TARGET`) and passes `ctx.forge` into
  `_pr_branch.fetch_failing_job_ids`.

**Still defaulting in 6c-2: indirect forwarders.** `pr_merge._execute_merge` defaults its `forge_target` to
the merge team's `_DEFAULT_FORGE_TARGET` and hands it to `_handle_merge_failure`, then
`_merge_failure_outcome`, then `_escalation_diagnostics`, whose `_diagnostics_rerun_lines` and
`_diagnostics_e2e_lines` reach `_pr_branch.fetch_flake_guard_head_sha`,
`_pr_flake_ledger.load_gate_survived_checks` and `_pr_branch.fetch_failing_e2e_tests`. The dependency
sweep's `_to_proposals` and `_run_sweep` take the same default context. Other 6c-2 defaults — the merge
team's merge path and the rest of the merge checks, readiness, reconcile, push, PR post, body, feedback,
closing and threads, the verification review post, the rest of the dependency sweep, delivery and
branch-protection compatibility — reach these parameters the same way until 6c-2 lands. This slice removes
the default at the four modules; 6c-2 removes the defaults that feed them.

**This supersedes earlier statements, for these four modules only.** Three statements no longer hold for
the branch helpers, the flake ledger, the freshness check and the Sonar helpers:

- PR16's "Parameter defaults still default, now to the shared value" (in the "One constant" paragraph).
- PR17's "it still holds for the forge-call family and the slot pool below" sentence.
- PR17's "Deferred: the forge-call family and the runner move" paragraph, which listed the branch helpers
  and the flake ledger among the keyword-only defaults that stay.

The PR16 and PR17 text is not edited. Both statements still hold for the rest of the forge-call family
that this change does not touch, and the slot pool stays deferred as PR17 recorded it.

**`pr_iteration_pipeline.md` D15 no longer holds for the branch helpers and the Sonar helpers.** D15's
signature passage, which quotes those helpers with a defaulted keyword-only `forge_target`, and its
rationale that the default engine's behaviour is unchanged because of the defaults, are both out of date
for `_pr_branch` and `_pr_sonar`. D15's sentence about the `implementation_fix` wrappers still holds until
6c-2. D15's own text is left for 6c-2, which rewrites it once the rest of the family is required.
`merge_recovery.md`'s one flake-ledger classifier signature that quoted the default —
`evidenced_actionable_failing_checks` — now reads `*, forge_target`; the target-free shorthand around it
stays.

**Accepted residuals.**

- Silent fakes. A test that patches one of these functions with a catch-all `*_a, **_kw` lambda still
  accepts a call that omits the target, so it hides a real caller that does. The type checkers cover direct
  calls in `alan/` (pyright) and in `alan/` plus `tests/` (mypy); indirect calls are visible only to the
  test suite.
- Inconsistent naming. The ledger's five positional parameters are named `forge` while every other
  parameter in the four modules is `forge_target`. Renaming them would touch 6c-2 call sites, so it is a
  follow-up for 6c-2.

## X2.7 PR19: the verification and sweep family requires the forge target

PR17 and PR18 made the state, credential, branch-helper, flake-ledger, freshness and Sonar layers require
the forge target and left the callers that feed them for 6c-2. PR19 is 6c-2's second half: it removes the
default from the rest of the verification, fix-team and sweep family, everywhere except the merge team.
It is a pure guard. A caller that leaves the target out no longer silently acts on alan's own repository
on behalf of another engine; it is a type error under both pyright and mypy. Nothing changes at runtime for
any caller that passes a target.

**Which parameters became required.** Thirty-six forge-target parameters lose `= DEFAULT_FORGE_TARGET`,
in seventeen areas:

- `alan/teams/_pr_ready.py`: 8 — `_gh_pr_view_readiness`, `_resolve_readiness`, `_fetch_pr_base_sha`,
  `_gather_quarantine_inputs`, `classify_flake_quarantine`, `check_pr_merge_readiness`,
  `readiness_or_unavailable` and `check_pr_verifier_blockers`.
- `alan/teams/_pr_push.py`: 5 — `push_branch`, `_try_push_with_lease`, `_fetch_for_reconcile`,
  `_repush_reconciled` and `push_fix_branch_with_reconcile`.
- `alan/teams/implementation_fix.py`: 4 — the fix team's pre-prompt fetches `_maybe_fetch_threads`,
  `_maybe_fetch_sonar`, `_maybe_fetch_gate_failures` and `_maybe_fetch_current_body`.
- `alan/teams/_pr_body.py`: 2 — `fetch_pr_body` and `amend_pr_body`.
- `alan/teams/_pr_merge_checks.py`: 2 — `resolve_checks_kind` and `confirm_ready_kind`.
- `alan/teams/_pr_post.py`: 2 — `dismiss_stale_verifier_reviews` and `post_pr_review_with_inline_comments`.
- `alan/teams/_verification_review_post.py`: 2 — `_review_post_failed_body` and
  `_emit_review_post_failed`.
- `alan/branch_protection_compat.py`: 2 — `compare_branch_protection` and `apply_branch_protection`. Only the
  two defaults and the import they left unused are removed; its `gh` command lines are byte-identical and it
  stays in the recorded list of modules that build `gh` command lines.
- One each: `_pr_closing.gh_pr_closing_issue_numbers`, `_pr_reconcile.reconcile_branch`,
  `_verification_gates._resolve_actionable_gate_checks`, `_pr_merge_no_signal.timeout_ctx`,
  `_pr_feedback.fetch_feedback`, `_pr_merge_conflict.gh_pr_state_and_merge_status`,
  `_pr_threads.list_unresolved_threads`, `delivery.fetch_pr_delivery` and
  `dependency_sweep.load_dependency_sweep_status`.

The dependency sweep's ten `ctx: SweepContext` parameters also lose their default: `_fetch_open_proposals`,
`_fetch_pr_files`, `_detect_upstream_block`, `_to_proposals`, `_has_open_security_issue`,
`_ensure_security_label`, `_open_gh_issue`, `_handle_vuln`, `_run_vuln_scan` and `_run_sweep`. The
module-level `_DEFAULT_SWEEP_CTX` (a `SweepContext` built on `DEFAULT_FORGE_TARGET` with the vulnerability
scan on) is deleted, so nothing supplies alan's target to the sweep silently. Every parameter keeps its name,
annotation and position. A parameter that was keyword-only stays keyword-only, so it is now a required
keyword-only parameter.

**Positional parameters.** Four of the 36 are positional-or-keyword and stay positional:
`compare_branch_protection` and `apply_branch_protection` (`forge`), `load_dependency_sweep_status`
(`forge`) and `fetch_pr_delivery` (`forge_target`). Five production callers pass them by position —
`alan/recurring_status.py`, `alan/routes/engines.py`, `alan/engine_preflight.py`, `alan/repo_scaffold.py` and
`alan/routes/workspaces.py` — so making them keyword-only would have forced edits to those callers. That
move is deferred alongside the rename below. The other 32 stay keyword-only.

**What still defaults, and why.**

- The merge team's ten module-private helpers in `alan/teams/pr_merge.py` still default to its private
  `_DEFAULT_FORGE_TARGET` binding: `_poll_until_done`, `_evaluate_checks`, `_outcome_from_pr_state`,
  `_conflict_outcome`, `_outcome_from_checks`, `_merge_or_dry_run`, `_handle_behind_recheck`,
  `_execute_merge`, `_gh_pr_checks` and `_pr_url`. The binding and the identity test that pins it are
  unchanged. This is out of scope for PR19 and deferred to a later slice.
- The slot pool keeps the defaults PR17 recorded ("Deferred: the slot pool").
- The `None` "no target" defaults are a different kind and stay. `_pr_branch.credential_recovery_recipe` and
  the `forge_target` field and parameter in `_pr_handover.py` default to "no target", which builds the
  generic result and never falls back to alan, so they were never among the defaults that leak.
- Deliberate hardcoded uses of `DEFAULT_FORGE_TARGET` are unchanged: the Copilot thread reply
  (`_copilot_resolution`) and resolve (`_pr_threads.resolve_review_thread`) calls, the deploy restart probe,
  collision detection, the open-PR title listing and triage's credential-name read.

**Indirect chains still open through the merge team.** The merge team's private default still reaches the
parameters PR19 made required. `_poll_until_done` reaches the merge-conflict state read through
`_poll_once`, and `_evaluate_checks`, which it also reaches, reaches the no-signal timeout context, PR
feedback and the merge checks. `_handle_behind_recheck` reaches PR readiness, and `_execute_merge` reaches
it again through `_escalation_diagnostics` and `_diagnostics_readiness_lines`. Each of those helpers passes
on the default it was handed, so PR19 does not claim these chains are closed: it removes the default at the
parameters, and the later slice removes the defaults that feed them.

**Production: no caller omitted the target.** Every production caller of the 36 parameters and of the ten
sweep `ctx` parameters already passed one, so no production call site changed. pyright over `alan/` and mypy
over `alan/` and `tests/` report no missing target in `alan/`. For every call that passes a target the `gh`
command lines, state paths, credentials and audit rows are byte-identical to before. The evidence that the
guard holds is that the parameters have no default and that the backend gates pass.

**Tests.**

- Every test call that left the target out now passes alan's target, as `forge_target=DEFAULT_FORGE_TARGET`
  (or the file's existing alias) for the keyword-only parameters and positionally for the four positional
  ones. Tests of real default-engine behaviour keep asserting the same thing with the default passed
  explicitly.
- Both dependency-sweep test files define a module constant `_ALAN_SWEEP_CTX`, a `SweepContext` on alan's
  target with the vulnerability scan on, and pass it wherever they relied on the deleted default context, so
  the scan-path tests still exercise the scan. The issue-creation test
  (`test_open_gh_issue_creates_with_repo_and_label`) passes that context in its call and keeps its
  assertion on the target unchanged.
- The test that the review post refuses a positional verifier flag now passes the target and matches the
  positional-argument `TypeError`. It could previously pass merely because the target was missing.
- `test_apply_uses_default_forge_target_default_argument` existed only to prove that
  `apply_branch_protection` had a default, and is deleted. Apply's no-credential path stays covered by
  `test_apply_no_credential_defers_all_rows_unknown_and_no_subprocess`.
- The unresolved-threads identity test and the two review-post-failure credential-name tests pass alan's
  target explicitly. The two test fakes of the fix team's reconcile-push no longer default the target.
- `RECORDED_PATCH_SITES` stays at `(1144, 112)`, no test containing a counted patch site is deleted or moved,
  and no size baseline is edited.

**This supersedes earlier statements, for the areas PR19 covers.**

- PR18's "Still defaulting in 6c-2: direct forwarders" and "Still defaulting in 6c-2: indirect forwarders"
  lists no longer hold for the functions above. What remains of the indirect list is the merge team's
  private defaults, recorded under "Indirect chains still open" in this section.
- PR16's "Parameter defaults still default, now to the shared value" and PR17's "it still holds for the
  forge-call family" no longer hold for those functions.

The PR16, PR17 and PR18 text is not edited. Their statements still hold for the merge team's helpers and the
slot pool, which stay deferred. `pr_iteration_pipeline.md` D15 and `dep_sweep.md` are corrected to match.

**Deferred.**

- The merge team's ten private defaults and its `_DEFAULT_FORGE_TARGET` binding, to a later slice.
- Renaming the remaining `forge` parameters to `forge_target`: the flake ledger's five, the sweep status
  read and branch-protection compatibility. This re-records PR18's "follow-up for 6c-2" as not done here.

**Accepted residual.** A test that patches one of these functions with a catch-all `*_a, **_kw` lambda or a
`MagicMock` still accepts a call that omits the target, so it can hide an indirect call that leaves the
target out. The type checkers cover direct calls in `alan/` (pyright) and in `alan/` plus `tests/` (mypy);
indirect calls are visible only to the test suite.

## X2.7 PR20: the merge team requires the forge target

PR19 removed the default from the verification, fix-team and sweep family and left the merge team's ten
private helpers for a later slice. PR20 is that slice, 6c-3. It completes the default removal for the
forge-call family. It is a pure guard: a caller that leaves the target out is now a type error under both
pyright and mypy and a `TypeError` at runtime. Nothing changes at runtime for any caller that passes a
target.

**Which parameters became required.** Ten `forge_target` parameters in `alan/teams/pr_merge.py` lose
`= _DEFAULT_FORGE_TARGET`: `_poll_until_done`, `_evaluate_checks`, `_outcome_from_pr_state`,
`_conflict_outcome`, `_outcome_from_checks`, `_merge_or_dry_run`, `_handle_behind_recheck`, `_execute_merge`,
`_gh_pr_checks` and `_pr_url`. Every parameter keeps its name, annotation and keyword-only kind, and no
parameter changes position. `_execute_merge`'s `expected_head_sha` stays positional with its `""` default;
only the forge-target parameters changed.

**The alias is removed.** `_DEFAULT_FORGE_TARGET` is deleted from `alan/teams/pr_merge.py` and the import is
narrowed to `from alan.engine_config import ForgeTarget`, so the merge team no longer names the shared
constant under any name. `test_pr_merge_private_name_is_the_shared_constant`, which pinned the alias's `is`
identity to `DEFAULT_FORGE_TARGET`, is deleted with the now-unused `pr_merge` import in
`tests/test_engine_config_forge.py`. `test_default_forge_target_pinned_by_value`,
`test_default_forge_target_matches_resolver_fallback` and the module-level resolver-call AST scan
(`test_no_module_level_default_resolver_call_outside_engine_config`) survive.

**Production: no caller omitted the target.** Every chain in the merge team starts from the workspace's
resolved target and every internal call already passed it explicitly, so the production diff is the ten
signatures, the alias line and the import, and no call site changed. For every engine the `gh` command
lines, state paths, credentials and audit rows are byte-identical to before.

**Tests.**

- Every test call that left the target out now passes alan's target, using the file's existing name:
  `forge_target=DEFAULT_FORGE_TARGET`, or `forge_target=_TARGET` in `tests/test_pr_merge_forge_port.py`.
  `tests/test_teams_pr_merge.py` and `tests/test_pr_merge_feedback_rate_limit.py` gain a direct import of
  `DEFAULT_FORGE_TARGET`; no wrapper, fixture or test-local alias is added. Calls that no longer fit in one
  line are wrapped at 100 columns.
- The two check-parsing tests in `tests/test_teams_pr_merge.py` are deleted:
  `test_gh_pr_checks_falls_back_to_pending_on_nonzero` and
  `test_gh_pr_checks_parses_stdout_even_on_nonzero_rc`. The pending-placeholder fallback is still covered
  with an explicit target by `test_gh_pr_checks_unknown_failure_without_json_keeps_pending_fallback` in
  `tests/test_teams_pr_merge_forge.py` and by `test_checks_unclassified_failure_without_rows_keeps_the_pending_fallback`
  in `tests/test_pr_merge_forge_port.py`. Trusting rows despite a non-zero exit is still covered by
  `test_gh_pr_checks_rc8_with_json_still_trusted` in `tests/test_teams_pr_merge_forge.py` and
  `test_checks_nonzero_exit_with_rows_is_still_trusted` in `tests/test_pr_merge_forge_port.py`.
  `test_gh_pr_checks_normalises_non_string_fields` is unique and is kept, passing the target.
- The file's reads of `pr_merge_mod._DEFAULT_FORGE_TARGET` switch to the directly imported
  `DEFAULT_FORGE_TARGET`. Together with the two deleted tests this leaves `tests/test_teams_pr_merge.py`
  under its size pin.
- `RECORDED_PATCH_SITES` stays at `(1144, 112)`, because no forge-family patch site is added or removed, and
  no size baseline is edited.

**This supersedes earlier statements.** The earlier text is not edited.

1. PR16's paragraph "The one exception is the merge team", which explained why the merge team kept its
   alias.
2. Only the `test_pr_merge_private_name_is_the_shared_constant` clause of PR16's "What is enforced". The
   other two tests in that bullet, and the AST-scan bullet, still hold.
3. PR19 "What still defaults, and why.", first bullet: the merge team's ten helpers no longer default.
4. PR19's whole "Indirect chains still open through the merge team." paragraph. The chains it named no longer
   start from a default.
5. PR19 "Deferred.", first bullet.
6. PR19's supersession-block sentence "What remains of the indirect list is the merge team's private
   defaults, recorded under "Indirect chains still open" in this section." With it, PR18's "Still
   defaulting in 6c-2: indirect forwarders" sentence about `pr_merge._execute_merge` no longer holds.
7. PR19's sentence "Their statements still hold for the merge team's helpers and the slot pool, which stay
   deferred." From now on, the PR16, PR17 and PR18 statements that PR19 carried forward hold only for the
   slot pool.

`pr_iteration_pipeline.md` D15 is corrected in place to name only the slot pool and to cite this section.

**What remains.** The slot pool (`alan/slot_pool.py`) holds the only forge-target parameter defaults to
alan's target left in the backend. These are not parameter defaults of that kind and remain: the resolver's
deliberate call-time fallback to the default engine, the `None` "no target" defaults, and deliberate
call-time uses of `DEFAULT_FORGE_TARGET`, such as the Copilot thread reply and resolve calls, the deploy
restart probe, collision detection, the open-PR title listing and triage's credential-name read.

**Deferred.**

- The slot pool's defaults and its constructor's fallback.
- Renaming the remaining `forge` parameters to `forge_target` (PR19's second "Deferred" bullet) still stands.
- Moving the merge team's tests onto the fake forge is a later X2.7 slice.

**Accepted residual.** A test that patches one of these helpers with a catch-all stub still accepts a call
that omits the target; the `_evaluate_checks` monkeypatch in `tests/test_teams_pr_merge_conflict.py` is one.
The type checkers see only direct calls, and no production code reaches these helpers indirectly.

## X2.7 PR21: the update-branch operation is deleted

Slice 6d-1 of the `_pr_branch.py` deletion pass. The port had two branch-update operations:
`request_branch_update`, which makes one attempt, and `update_branch`, the yes/no operation whose adapter
body called `_pr_branch.update_pr_branch`, which called `update_pr_branch_outcome`, which called
`request_branch_update` back through the port. That port to helper to port round trip was the last one of its
kind, and this change removes it.

**What was deleted.** `update_branch` on the port, on `GhForge` and on the test fake (including the fake's
operation-name table and the fake-forge test list), and `_pr_branch.update_pr_branch`. No replacement yes/no
helper exists under any name. The operation count on the port and on the adapter goes from forty-nine to
forty-eight, and the error contract from twenty-eight of forty-nine operations to twenty-seven of forty-eight.

**The callers.** `pr_merge._behind_during_merge_outcome` and `merge_recovery._recover_behind_plus_transient`
now call `_pr_branch.update_pr_branch_outcome` and convert it once to a local `bool` with
`outcome is _pr_branch.BranchUpdateOutcome.UPDATED`. Only `UPDATED` counts as success: `BranchUpdateOutcome` is
a `str` `Enum`, so a truthiness test would count `CONFLICT` and `FAILED` as success. Every downstream
signature and audit field is unchanged. Each attempt is still one `gh pr update-branch {n} --repo {slug}`
under the ambient identity, and the commands, credentials, state paths and audit rows are byte-identical:
`pr_update_branch_ok` and `pr_update_branch_failed` from the outcome helper, and the callers' own
`merge_deferred_behind` and `merge_recovery_attempt`. `merge_recovery.py` no longer imports `active_forge`,
because its only use went with the deleted call.

**Why the roadmap's dry-run exercise does not apply.** The merge team's call site sits after
`_merge_or_dry_run`'s dry-run return, and the recovery rung is not dry-run gated, so exercising either live
would perform a real branch update on a real PR. The tests that stand in for it are:

- `tests/test_teams_pr_merge_forge.py`: `test_behind_during_merge_update_succeeds_records_ok_row_for_task`,
  `test_behind_during_merge_update_fails_records_failed_row_for_task` and
  `test_behind_during_merge_update_conflict_records_failed_row_for_task`, entered through the team's run with
  `ALAN_PR_MERGE_DRY_RUN` unset. Each asserts exactly one `request_branch_update` for PR 42 against alan's
  forge target, the `pr_update_branch_*` row carrying the workspace's task id, and the
  `merge_deferred_behind` row's `update_ok`.
- `tests/test_dispatcher_merge_gate_routing.py`: `test_pr_merge_gate_auto_updates_branch_when_behind_only`,
  `test_pr_merge_gate_falls_through_when_auto_update_fails` and the new
  `test_pr_merge_gate_falls_through_when_auto_update_conflicts`, entered through the dispatcher's handling of
  a `pr_merge` handover the way the other merge-gate routing tests are. Each asserts one
  `request_branch_update` for PR 99 against alan's forge target and the matching `pr_update_branch_*` row
  with the workspace's task id. `test_pr_merge_gate_skips_auto_update_when_other_blockers` now stubs
  `request_branch_update` with a stub that raises and asserts no call.
- `tests/test_pr_branch_no_recursion.py`: the three tests are re-pointed at `update_pr_branch_outcome`
  through the real adapter with only the process runner doubled. Each still asserts one process call and one
  audit row, now with the `UPDATED`, `CONFLICT` and `FAILED` outcomes.

**Tests.**

- The four `test_update_pr_branch_*` tests in `tests/test_pr_push.py`, the wrapper's own test in
  `tests/test_pr_branch_forge.py` and the two wrapper-delegation tests in `tests/test_forge_gh_adapter.py`
  are deleted. What they checked is covered by the outcome helper's tests in `tests/test_pr_branch_forge.py`
  and by the pinned adapter argv test.
- `test_update_pr_branch_outcome_issues_only_the_update_attempt` now asserts the fake saw exactly one call,
  `request_branch_update`, instead of naming the deleted operation.
- Every test that stubbed the deleted operation stubs the fake's `request_branch_update` with an
  `ApiResult` (success, failure, or a conflict-shaped failure), and the merge-recovery test the gh-read
  baseline lists by name, `test_attempt_behind_backend_red_only_does_not_call_update_pr_branch`, keeps its name
  and now stubs `request_branch_update` with a stub that raises. No baseline is edited.
- `tests/test_forge_gh_transport_ops.py`: the operation-count pin moves from 49 to 48 on both owners.
- `RECORDED_PATCH_SITES` moves from `(1144, 112)` to `(1136, 111)`, measured at this change's head by
  `count_family_patch_sites()`. No counted patch site is added. Seven counted sites go with the deleted
  wrapper tests, and the two BEHIND-only readiness patches in `tests/test_dispatcher_merge_gate_routing.py`
  collapse into one module-level helper, `_readiness_behind_only`, that the three routing tests share, so the
  figure is one below what counting only the seven deleted sites would give (1137). The merge-family seam
  gate's forbidden-name set is unchanged, and `tests/size_baseline.py` is not edited.

**Next slice (6d-2).** It moves the process runner and the reviewer-credential resolver out of the branch
helpers.

**Done-criterion flag.** The item's criterion "process-runner calls outside the adapter reaches zero" will
not be met by that move. The `run_subprocess(` callers outside `alan/forge_gh.py` are git commands
(`repo_scaffold`, `explore_chat_repo`, `_pr_push`, `_pr_closing`, `_pr_reconcile`,
`_deploy_restart_probe`, `_copilot_resolution`), curl calls (`_pr_sonar`), the scenario runner's scenario
commands (`_scenario_runner`), and one leftover `gh pr create` in `implementation.py`. Moving the runner
relocates those calls; it does not remove them. The criterion needs a ruling when the item closes.

**This supersedes earlier statements.** The earlier text is not edited.

1. "What PR3 migrated" rows 1 and 7, whose port method `update_branch` no longer exists: both callers now
   reach `update_pr_branch_outcome` directly.
2. PR2's "Claimed, and not claimed" sentence calling `update_branch` a delegating façade whose `GhForge` body
   calls back into `_pr_branch.update_pr_branch`.
3. PR3's numbered item 5, "Only `update_branch` still does since X2.7 PR14": no façade calls back into
   `_pr_branch` any more.
4. PR14's "Termination" paragraph. `update_branch` and `update_pr_branch` are gone, so there is no wrapper path
   left to recurse through; the outcome helper calls `request_branch_update`, which does not call back.
   `tests/test_pr_branch_no_recursion.py` keeps the one-call-one-row proof for the outcome helper.
5. PR14's and PR15's sentences that rerouting the merge team off the `update_branch` wrapper waits for the
   item's final deletion pass, insofar as they concern `update_branch`. Moving the process runner still
   waits, and is slice 6d-2.

## X2.7 PR22: the process runner and the reviewer-credential resolver leave the branch helpers

Slice 6d-2 of the `_pr_branch.py` deletion pass, and the prerequisite for deleting the branch helpers: after it,
`alan/forge_gh.py` no longer imports `alan.teams._pr_branch`. Runtime behaviour does not change. Every command
line, working directory, timeout, environment, credential choice and audit row is what it was.

**What moved.**

- `run_subprocess(cmd, *, cwd, timeout, env=None)` is now `alan/process_runner.py::run_subprocess`, with its
  name, signature and body unchanged: it merges `env` over `os.environ` when `env` is not `None` (and passes
  `env=None` through otherwise) and calls `subprocess.run` with `capture_output=True`, `text=True` and the
  timeout.
- `verifier_gh_env(forge_target)` is now `alan/secrets_store.py::verifier_gh_env`, defined directly after
  `transport_env` and beside `transport_credential`, the transport identity's resolvers. It resolves the
  reviewer credential (`resolve_secret(slug, REVIEWER_PURPOSE, env_fallback=credential_env_name)`), strips it,
  and returns `{"GH_TOKEN": token}` for a non-empty token and `{}` for an unset or blank one. Its body uses
  only names local to the secrets store, so the move adds no import edge.
- `_pr_branch.py` neither defines nor re-exports either name: no alias, no export-list entry. The absence is
  asserted, by name, in `tests/test_forge_gh_check_run_ops.py::
  test_the_removed_and_moved_helpers_are_gone_from_the_pr_branch_module`, which 6d-1 introduced for the
  helpers it deleted. A bridge alias would have made a production-first split possible, and it would have been a
  gate downgrade, so the change is one pull request.

**Where the runner sits.** `process_runner` is the last entry of the `[tool.importlinter]` "Layered
architecture" list, after `block_correction`, with no `ignore_imports` entry added. It is a public leaf: it
imports only `os`, `subprocess` and `collections.abc`. Being last in an exhaustive layers contract is what makes
`lint-imports` enforce that leafness, since any `alan` import from it would be an upward edge; the public name
keeps it off the private-module allowlist. `tests/test_transport_canary.py::
test_process_runner_imports_no_application_module` states the same rule at the source level, and
`test_no_long_lived_git_subprocess_or_fsmonitor` now scans the module too. Two tests that pin a layer's
position counted from the bottom of the list, `test_base_ref_is_registered_below_its_consumers_in_layers` and
`test_engine_config_validation_is_thirteenth_to_bottom_import_layer` (now `..._fourteenth_...`), moved by one
place and by one name because the list grew at the bottom.

**The callers.** Forty-two of the forty-three `run_subprocess(` calls in `alan/`, and the one in
`scripts/alan_forge_contract_check.py`, look `process_runner.run_subprocess` up as an attribute of the module at
call time, the way `_pr_branch.run_subprocess` was looked up: `alan/forge_gh.py` (23), `alan/teams/_pr_push.py`
(7), `alan/repo_scaffold.py` (3), `alan/teams/_pr_closing.py` (2), `alan/teams/_pr_sonar.py` (2) and one each in
`alan/teams/_deploy_restart_probe.py`, `alan/teams/implementation.py`, `alan/teams/_pr_reconcile.py`,
`alan/teams/_scenario_runner.py` and `alan/teams/_copilot_resolution.py`. A single module attribute keeps one
patch target for the whole suite. The one named exception is `alan/explore_chat_repo.py`, which keeps a by-name
import (`from alan.process_runner import run_subprocess`) so its own tests, which patch the name on that
module, need no change. Both production calls to the resolver, `forge_gh._verifier_env` and
`_pr_post.verifier_identity_available`, look `secrets_store.verifier_gh_env` up at call time. Every other name
these modules take from `_pr_branch` (`classify_credential_failure` in `_pr_push`, the check-run helpers in
`_pr_sonar`, the by-name block in `implementation.py`) is untouched.

**What the adapter still reaches in `teams`.** `alan/forge_gh.py` no longer imports `alan.teams._pr_branch`. It
still imports `GH_TIMEOUT_S` from `alan.teams._pr_forge` and `safe_json` from `alan.teams._pr_json`, and
importing either loads the `alan.teams` package at runtime. Those two are the adapter's only remaining edges
into `teams`. Moving the constant or the JSON helper, or deleting their modules, is not part of this slice.
`tests/test_transport_canary.py::test_forge_gh_does_not_import_the_pr_branch_module` walks every `Import` and
`ImportFrom` in the adapter, `from alan.teams import _pr_branch` included, and asserts none resolves to
`alan.teams._pr_branch`; the existing "does not from-import its parent" rows cannot see that form.

**Replacement reason for the Layering section.** `alan.forge_gh` stays above `teams` in the layers list only
because of `GH_TIMEOUT_S` and `safe_json`. It no longer delegates into `_pr_branch` (its transport helpers and
`verifier_gh_env`), which is the reason the Layering section gives. "Operations deliberately not on the port"
is not superseded: `verifier_identity` (formerly `verifier_login`) and `fetch_failing_e2e_tests` still live in `_pr_branch.py` for the reasons
given there, and the `verifier_env` bullet's `verifier_gh_env` now lives in `secrets_store`.

**The canary and the guard.**

- Three rows of `_CANARY_MODULES` that protect the runner lookup name the runner's new module: `forge_gh`,
  `_pr_push` and `_deploy_restart_probe` must not from-import `alan.process_runner`. The `_pr_push` row for
  `alan.teams._pr_branch` stays, because `_pr_push` still looks `classify_credential_failure` up on it, and the
  other branch-helper rows (`_pr_threads`, `_pr_post`, `_pr_ready`, `_clause_audit`) stay. Two new rows forbid
  `forge_gh` and `_pr_post` from from-importing `alan.secrets_store`, so the resolver is looked up as an
  attribute of the module and a test can patch it there.
- The four forge-contract patch sites in `tests/test_transport_canary.py` and the seven in
  `tests/test_forge_contract_canary.py` patch `process_runner`; the script's call is one of the forty-three
  runner calls above.
- `tests/gh_guard.py`'s stub message and read-ratchet remedy name `alan.process_runner.run_subprocess`, and
  `tests/test_gh_read_ratchet.py` asserts `alan.process_runner`. The name-based transport entry-point list
  (`tests/forge_argv.py`) matches on the call name `run_subprocess`, so it finds the runner in its new home
  without an edit.

**Tests.** Every test that patched either function on the branch-helpers module patches the new home: the
counted `monkeypatch.setattr` sites, the ones that go through the `pr_branch_mod` alias
(`tests/test_teams_implementation_body.py`, `tests/test_dispatcher_scenario_rebind.py`,
`tests/test_scenario_rebind_e2e.py`), the adapter stand-in in `tests/test_forge_gh_check_run_ops.py` (now
`monkeypatch.setattr(forge_gh, "process_runner", SimpleNamespace(run_subprocess=double))`, its test renamed
`test_no_operation_or_json_helper_reaches_process_runner_except_through_run_subprocess`), and the direct calls in
`tests/test_pr_push.py` and `tests/test_teams_implementation.py`. The resolver's seven tests stay in
`tests/test_pr_push.py` and call `secrets_store.verifier_gh_env`. No test function leaves the suite, no new
patch of a forge-family module is added, `tests/test_teams_pr_merge.py` stays at its size pin (the new import
joins an existing `from alan import` line), and `tests/size_baseline.py` is not edited.

**The patch-site figure drops because of a relocation, not a migration.** `RECORDED_PATCH_SITES` moves from
`(1136, 111)` to `(794, 74)`, measured at this change's head by `count_family_patch_sites()`. The 342 sites that
leave the count are the 333 runner sites and 9 resolver sites that patched `_pr_branch`, a family module, and now
patch `process_runner` or `secrets_store`, which are not. The definition is unchanged: `FAMILY_MODULES` stays at
eleven. No test moved onto the fake forge, so the drop says nothing about how many tests still stand on a
process double instead of the port.

**Done-criterion flag.** The item's criterion "forge-family patch sites ≤ 40" is not met by this figure, and
the drop to 794 must not be read as progress toward it. It needs a ruling when the item closes, alongside the
"process-runner calls outside the adapter reaches zero" flag raised under X2.7 PR21, which this change does
not affect: the non-adapter callers moved from one module to another and are still git, curl and scenario
commands, which belong to a version-control port, not to the forge port.

**This supersedes earlier statements.** The earlier text is not edited.

1. The Layering section's reason for placing `alan.forge_gh` above `teams`, that it delegates into
   `alan.teams._pr_branch` (its transport helpers and `verifier_gh_env`): the only reason now is
   `GH_TIMEOUT_S` and `safe_json`. The rest of that section stands.
2. Every earlier statement that the adapter, or a test, reads the runner as `_pr_branch.run_subprocess`, or the
   reviewer credential as `_pr_branch.verifier_gh_env`, "at call time" or through "the module attribute", among
   them the method table's remark on the `gh pr` argv methods, PR14's and PR15's descriptions of the runner
   patch target and PR15's canary and test-fixture notes. The lookup is still at call time, and it is now
   `process_runner.run_subprocess` and `secrets_store.verifier_gh_env`.
3. PR21's "Next slice (6d-2)" note, which announced this move, and PR14's and PR15's sentences that moving
   the process runner waits for the final deletion pass: it is done.

## X2.7 PR23: the slot pool requires the forge target

Slice 6d-3 of the deletion pass, and the slot pool's turn. `alan/slot_pool.py` was the last place in the
backend where a forge target silently fell back to alan's own repository when a caller left it out, in two
forms: twelve private helpers took `forge_target: ForgeTarget | None = DEFAULT_FORGE_TARGET`, and the pool's
constructor turned an omitted target into alan's through an unset marker. This change removes both. Leaving
the target out of one of the twelve helpers is now a type error under pyright and mypy and a `TypeError` at
runtime. Omitting it from the constructor now means "no target", so a `SlotPool` built directly from a remote
clone source without a target injects no credential, where it used to inject alan's. That is the one
behavioural difference, and it is confined to direct construction: every pool the service builds passes its
target explicitly, so nothing changes at runtime for any production pool.

**Which parameters became required.** Twelve parameters in `alan/slot_pool.py` lose `= DEFAULT_FORGE_TARGET`:
`_fetch_base`, `_fetch_existing_branch`, `_prepare_new_branch`, `_fetch_for_resume`, `_fallback_prepare`,
`_prepare_resume`, `_refresh_base_best_effort`, `_prepare_existing_branch`, `_warmup_command_env`,
`_run_warmup_command`, `_run_warmup` and `_init_slot`. Every parameter keeps its name, its
`ForgeTarget | None` annotation and its position. `None` is still an accepted value and still means no
credential is injected. Every production call already passed the target positionally, so no production call
to these helpers changed.

**The constructor fallback is removed.** `_UnsetForgeTarget`, `_FORGE_TARGET_UNSET` and the
`DEFAULT_FORGE_TARGET` import are deleted from `alan/slot_pool.py`, and `_resolve_forge_target_for_pool`
narrows to `forge_target if is_remote_url(repo_root) else None`. `SlotPool.__init__` declares
`forge_target: ForgeTarget | None = None`, so omitting the target means "no target", with no credential
injected, for a remote clone source as well. The local-source rule is unchanged: any target given with a local
(non-URL) clone source resolves to "no target". The constructor keeps its optional target and the "no target"
value is not respelled.

**Production.** `_create_slot_pool` in `alan/routes/infra.py` builds the service's default pool from its clone
source, and it was the only caller that relied on the fallback. It now passes
`forge_target=engine_config_mod.DEFAULT_FORGE_TARGET` explicitly, through the module's existing import.
`_create_slot_pool_for_engine` already passed a real target or `None`. The default pool's clone, fetch and
warmup commands and its credential environment are unchanged, for a remote and for a local clone source alike:
the default pool carries alan's target when built from a remote source, as it did before, and resolves to no
target when built from a local one. `alan/routes/infra.py` is a drift-gated path, and this section is the
accompanying documentation change.

**Tests.**

- Every test call to one of the twelve helpers now passes a target. The 41 calls in `tests/test_slot_pool.py`
  that left it out pass `DEFAULT_FORGE_TARGET`, already imported by the file, as the trailing positional
  argument, so the tests that assert on injected credentials, such as the clone test that asserts the
  credential-reset argv, still exercise the same path. Calls that no longer fit in one line are wrapped at 100
  columns. The direct calls in the other test files already passed a target.
- Test fakes of the twelve helpers no longer default the target, and no longer default the cache environment
  either where the fake defaulted both. That covers `tests/test_slot_pool.py`,
  `tests/test_slot_pool_base_refresh.py` and `tests/test_slot_pool_properties.py`, and the recording stand-in
  for `_init_slot`. The never-called `patched_prepare_resume` and the catch-all `*_a, **_kw` stubs are left
  alone.
- `test_slot_pool_uses_default_forge_target_for_remote_repo_root` is flipped and renamed
  `test_slot_pool_omitted_forge_target_injects_no_credential_for_remote_repo_root`. It builds a pool from a
  remote clone source with no target, asserts the pool's target is `None`, and asserts that
  `_clone_env_and_argv` with that target returns the plain `git clone` argv and an environment without the
  askpass token. The explicit-`None`, explicit-target and local-source constructor tests are unchanged.
- Two tests in `tests/test_server.py` build the default pool through the real `_create_slot_pool`.
  `test_create_slot_pool_default_pool_carries_alan_forge_target_for_remote_source` patches the clone source to
  alan's remote URL and asserts the pool's target equals `DEFAULT_FORGE_TARGET`, which is equality and not mere
  non-emptiness. `test_create_slot_pool_default_pool_resolves_no_forge_target_for_local_source` asserts `None`
  for a local source.
- `RECORDED_PATCH_SITES` stays at `(794, 74)`, because the slot pool is not a family module.
  `tests/test_slot_pool.py` stays at or under its size pin, and `tests/size_baseline.py` is not edited.

**This supersedes earlier statements.** The earlier text is not edited.

1. PR16's "Parameter defaults still default, now to the shared value" (in the "One constant" paragraph). It no
   longer holds anywhere in the application code.
2. PR17's "it still holds for the forge-call family and the slot pool below", the "and the slot pool below"
   part.
3. PR17's whole "Deferred: the slot pool." paragraph, including its note that "The infra route that builds the default pool (`alan/routes/infra.py`) and the deploy doc are untouched for the same reason."
4. PR18's "the slot pool stays deferred as PR17 recorded it".
5. PR19's "The slot pool keeps the defaults PR17 recorded ("Deferred: the slot pool")." in "What still defaults, and why."
6. PR19's "Their statements still hold for the merge team's helpers and the slot pool, which stay deferred."
7. PR20's numbered item 7, which carried the PR16, PR17 and PR18 statements forward "only for the slot pool".
8. The first sentence of PR20's "What remains": "The slot pool (`alan/slot_pool.py`) holds the only forge-target parameter defaults to alan's target left in the backend."
9. The first bullet of PR20's "Deferred": "The slot pool's defaults and its constructor's fallback."

`pr_iteration_pipeline.md` D15, `deploy.md` and `slot_pool.md` are corrected in place. D15's first paragraph no
longer names the slot pool as an exception, `deploy.md`'s passage on the constructor's unset marker now says
that omitting the target means no credential and that `_create_slot_pool` passes alan's target, and
`slot_pool.md`'s call shapes for the two warmup helpers include the target.

**What remains.** No function parameter in `alan/` defaults to alan's forge target. These are not defaults of
that kind and remain:

- The deliberate call-time uses of `DEFAULT_FORGE_TARGET`: the Copilot thread reply and resolve calls, the
  deploy restart probe, collision detection, the open-PR title listing, triage's credential-name read, and now
  `_create_slot_pool`. The two `repo.slug` comparisons against it, in `_pr_state_paths._is_default_forge` and
  in the dispatcher's self-heal check, were never defaults.
- The resolver's deliberate call-time fallback to the default engine.
- The `None` "no target" defaults, which build the generic result and never fall back to alan, now including
  `SlotPool.__init__`'s `forge_target`.
- The pool's `warmup_commands` and `cache_env` defaults, which resolve to the default engine's
  `_ENGINE_DEFAULT` values. They are engine-config defaults, not forge targets.

PR20's "Accepted residual" carries forward to the slot pool: a fake that swallows its arguments still accepts a
call that omits the target, and the type checkers see only direct calls.

**Still deferred.**

- Renaming the remaining `forge` parameters to `forge_target` (PR19's second "Deferred" bullet).
- Moving the merge team's tests onto the fake forge.
- Deleting the workspace-to-target resolution module, `alan/teams/_pr_forge.py`.
- The PR21 done-criterion ruling on "process-runner calls outside the adapter reaches zero".
- The PR22 done-criterion ruling on "forge-family patch sites ≤ 40".

## X2.7 PR24: the investigator's replay allowlist is stored as plain strings

Slice 6e(c) of the deletion pass, the first of three serial slices that each take one module off the
recorded-argv list. The root-cause investigator checks each "replay command" citation a model makes against
six read-only command prefixes: `grep`, `cat`, `git log`, `gh pr view`, `gh issue view` and `gh run view`.
`_REPLAY_ALLOWLIST` in `alan/investigator.py` held them as tuples of words, and the recorded-argv detector
counted the module because three of those tuples start with the literal `"gh"`. This change stores all six
entries as plain strings in a `frozenset[str]` and splits each into words inside `_replay_command_resolves`,
at the moment a citation is checked. Nothing is split at module load. Matching is unchanged: it is a prefix
match on whole arguments, so the citation's first N arguments must equal the entry's N words exactly.
`("gh", "pr", "viewer")`, `("gh", "pr")` and the single argument `("gh pr view",)` are all still rejected.
Which citations resolve is unchanged, and so are the model prompt, the allowed-tools list, the citation
schema and the resolution of audit-row and log-line citations.

**Why the change is legitimate.** At this change's base commit (`91f276f3`, 2026-09-26) the entries are data.
`_replay_command_resolves` is their only reader, and it only compares them against the argv the model cites.
Nothing at this commit executes a cited argv, so no `gh` command is ever run from an entry; a tuple that starts
with `"gh"` here is a string to compare against, not a command line the module builds. The detector was
counting a data shape, not a caller of the forge.

**Ruling (c).** Owner ruling, 2026-09-25: "(c) investigator.py: reshape the _REPLAY_ALLOWLIST literals to strings
split at use; no gate change; the PR says why (non-executed data)." This section and the PR description carry
the "why", as the ruling requires.

**The gate is unchanged.** `is_gh_argv`, `literal_prefix`, `ADAPTER_MODULES` and
`tests/test_forge_argv_enumeration.py` are not edited, and no exemption for data tuples is added. The detection
rule still flags every list or tuple literal under `alan/` whose first element is the literal `"gh"`. The
investigator drops out because a set of strings is an `ast.Set` with no element equal to `"gh"`, not because
the rule learned to tell data from commands.

**What is enforced.** `alan/investigator.py` leaves `tests/forge_argv.py`'s `RECORDED_ARGV_MODULES` (3 → 2;
`alan/branch_protection_compat.py` and `alan/teams/implementation.py` remain). It does **not** join
`MIGRATED_MODULES`, which stays at twenty-three: the investigator never called the forge, so there was nothing
to migrate. It leaves the recorded set the way `alan/repo_scaffold.py` did in X2.7 PR9 and the closing-link and
restart-probe modules did in PR11, off the recorded set without joining the migrated one.

**Why the roadmap's dry-run exercise does not apply.** The roadmap asks every caller PR to be exercised under
the merge-lane dry-run switch. The investigator is not a forge caller: it makes no forge call, before or after
this change, so there is no call site to exercise and no dry-run gate to pass through. The tests that stand in
for it are ten new one-scenario functions in `tests/test_investigator.py` that call `_replay_command_resolves`
directly:

- Accepted: `grep`, `cat`, `gh pr view`, `gh issue view` and `gh run view`, each with a citation starting with
  the prefix. The existing `git log` test, `test_replay_command_on_allowlist_resolves`, covers the sixth.
- Rejected: `gh pr merge`, `git push`, `("gh", "pr")` (shorter than any `gh` entry), `("gh", "pr", "viewer")`
  (the last argument only starts with an allowed word) and the single argument `("gh pr view",)`.

No existing test function is modified or removed.

**Unchanged.** The forge port's operation count stays at forty-eight, `RECORDED_PATCH_SITES` stays at
`(794, 74)`, and `tests/size_baseline.py` is not edited. `docs/architecture/investigator.md` is not changed.

**This supersedes earlier statements.** The earlier text is not edited.

1. PR11's "Left for later" statement that `alan/investigator.py` "waits on an owner ruling", including how the
   gate should classify a citation allowlist of `gh`-prefixed tuples.
2. PR12's "Left for later" statement that `alan/investigator.py` "waits on an owner ruling".
3. PR13's "Left for later" statement that `alan/investigator.py` "wait[s] on an owner ruling".

**Still deferred.**

- `alan/teams/implementation.py`, whose PR creation moves onto an ambient-identity forge operation (slice
  6e(b)).
- `alan/branch_protection_compat.py` (slice 6e(a)), which waits on the owner.
- Both are the two modules still in `RECORDED_ARGV_MODULES`. X2.7 is done when that set is empty.

## X2.7 PR25: the implementation team opens its pull request through the forge port

Slice 6e(b) of the deletion pass. The implementation team's `_open_pr` built its own `gh pr create` command
line and ran it through the process runner. This change moves that command behind the port as a new operation,
`create_pr_from_checkout`, and the module drops off the recorded-argv list. Behaviour is exactly what it was:
the same command line, run in the same place, as the same identity (on the owner's install and an
unconfigured install; on a copy see "Identity on a copy" above).

**Ruling (b).** Owner ruling, 2026-09-25: "(b) teams/implementation.py create_pr: add an ambient-identity variant op
that reproduces today's argv byte-for-byte (`gh pr create --title --body --base main --head`, no --repo, ambient
auth); no author change."

**What changed.** `ForgePort`, `GhForge` and the test fake gain `create_pr_from_checkout(title, body,
head_branch, *, forge_target, workdir, timeout) -> ApiResult`, directly after `create_pr`. The adapter's body is
one `_forge_run` call for exactly
`gh pr create --title {title} --body {body} --base main --head {head_branch}` with `cwd=str(workdir)` and the
given `timeout`, and `_api_result` over the result. There is no `try`, so a `FileNotFoundError` or
`subprocess.TimeoutExpired` from the runner reaches the caller. `--base main` is fixed inside the adapter and
callers cannot set it. `_open_pr` now reaches the operation through `active_forge()`, passing the workspace's
forge target (`_pr_forge.forge_target_for_ws(workspace)`), the worktree path and `GH_TIMEOUT_S`. The helper
keeps its name and parameters. Everything else stays in the caller, unchanged: the audit rows
(`pr_create_failed` twice, `pr_create_ok` once), the conversion of a transport failure into
`ForgeTransientError`, `raise_for_infra` on a non-zero exit, and `_extract_pr_number`.

**Why a separate operation.** `create_pr` runs the same `gh pr create` family, but as a different identity: it
resolves the transport credential and passes it as the environment, it names the repository with `--repo`, and it
has no `--base`. The implementation team's command runs as the machine's ambient `gh` login, has no `--repo`, and
passes `--base main`. Reusing `create_pr` would change who authors the PR, so the new operation is its
ambient-identity sibling, the way `pr_open_state` is `pr_state`'s ("X2.7 PR14: PR view reads and branch update"
above): the same command as a different identity, so neither operation is reused for the other. The scaffold's
`create_pr` is not changed in any way.

**No author change (owner's install and unconfigured install).** The operation resolves no credential and passes no
environment. It calls neither `_transport_env` nor `_verifier_env` and never touches `secrets_store`, and the
process-runner call carries no `env` keyword at all, so the PR is created by the machine's ambient `gh` login,
as before. "Not a copy" is decided before any lookup, so that stays true.

**Accepted consequence.** On the owner's install, non-default engines still open their PR as the ambient `gh`
login, in whichever repository the checkout's remote points to. `forge_target` does not reach the argv, so a
reader must not assume the target names the repository: on the owner's install the workspace's target reaches
the call and stops there. This was recorded as accepted under ruling (b) and is not reopened here.

**Lifted for copies only.** On a copy the same call carries the transport credential of `forge_target` through
`_forge_run`, and its command line is otherwise unchanged: no `--repo`, fixed `--base main`, the same working
directory. `tests/test_forge_gh_copy_identity.py::test_copy_create_pr_from_checkout_carries_the_transport_credential_with_unchanged_argv`
pins it. The two owner-install tests below run with the session's profile source defaulted to the owner's
install and pass unchanged.

**Why the roadmap's dry-run exercise does not apply.** The call sits after `_push_changes`' `dry_run_enabled()`
return (`implementation.py`, the `ALAN_IMPL_DRY_RUN` branch), and the merge-lane dry-run switch never gates this
team, so exercising it live would open a real PR. The tests that stand in for a live exercise are:

- `tests/test_forge_gh_caller_ops.py::test_create_pr_from_checkout_runs_the_ambient_argv_without_an_environment`:
  the exact command line, and the runner receiving only the working directory and the timeout, with no
  environment, while a transport credential is resolvable.
- `tests/test_forge_gh_caller_ops.py::test_create_pr_from_checkout_ignores_the_forge_target`: two different
  forge targets give the identical command line, with no `--repo`.
- `tests/test_teams_implementation_forge.py::test_open_pr_runs_one_ambient_gh_pr_create_through_the_forge_port`:
  drives `_open_pr` through the real adapter with the transport-credential and reviewer-credential resolvers
  made to fail if called, and asserts exactly one runner call whose command starts with `gh` and equals the old
  command line, run in the worktree with no environment, and exactly one recorded call of the new operation
  carrying the workspace's forge target. The `git` runner calls the helper makes first are expected. It only
  exercises observable behaviour, so it also passes on the base commit.

**What is enforced.**

- `tests/forge_argv.py`: `RECORDED_ARGV_MODULES` goes from 2 to 1 (only `alan/branch_protection_compat.py`
  remains) and `MIGRATED_MODULES` from 23 to 24, with `alan/teams/implementation.py` joining it.
- The operation count on the port and on the adapter goes from forty-eight to forty-nine, and the error contract
  becomes twenty-eight of forty-nine operations. That equals the figure before PR21 only by coincidence:
  `update_branch` is gone and `create_pr_from_checkout` is added.
- The fake forwards `create_pr_from_checkout` to the real adapter by default, and `create_pr` stays strict: an
  unstubbed call to it still fails.
- `_FORBIDDEN_PARAMETER_NAMES` in `tests/test_forge_gh_transport_ops.py` is unchanged. Only its operation-count
  pin moves, from 48 to 49.

**Unchanged.** `RECORDED_PATCH_SITES` stays at `(794, 74)`, and `tests/size_baseline.py` is not edited.
`alan/teams/implementation.py` shrinks slightly and `tests/test_teams_implementation.py` is not edited.

**This supersedes earlier statements.** The earlier text is not edited.

1. PR21's "Done-criterion flag", only the clause "and one leftover `gh pr create` in `implementation.py`". The
   flag itself stands: the `git`, `curl` and scenario-runner `run_subprocess(` calls outside the adapter remain,
   so "process-runner calls outside the adapter reaches zero" is not claimed.

**Still deferred.**

- `alan/branch_protection_compat.py` (slice 6e(a)), which waits on the owner. It is the last module in
  `RECORDED_ARGV_MODULES`. X2.7 is done when that set is empty.

## The higher-layer forge ports that already exist

`alan/unblock_sweep.py` already defines three narrow `Protocol`s — `LockFor` (:68), `ReadReadiness`
(:72), and `IssueClient` (:78, backed by `alan/teams/unblock_issue.py`) — at the `unblock_sweep`
layers-list slot (142), well above `teams` (197). Because they sit above `teams`, they may name
`teams`-owned types directly: `ReadReadiness.__call__` returns `alan.teams._pr_ready.PrReadiness`
without difficulty, since nothing stops a higher layer from referencing a lower one. Each of those
three ports is already shaped per-call on a `ForgeTarget`, the same calling convention `ForgePort`
uses. `ForgePort` is the transport-level port *beneath* them: it is what `_pr_ready.PrReadiness`
would eventually be built from, not a competitor to it. `ForgePort` is deliberately not an
extension of `IssueClient`: `IssueClient` names issue-domain operations (`ensure_shape_issue`,
`comment`) over the unblocker's own workflow concerns, sitting above `teams` and free to reference
whatever it likes from below; `ForgePort` sits below `teams` and could not express those operations
even if it wanted to, since GitHub Issues and the merge family's PR/check/review surface are
different domains that happen to share a forge. Whether `LockFor`/`ReadReadiness`/`IssueClient` are
later re-expressed in terms of `ForgePort` calls is X2.7's call, not this PR's.

## `_pr_forge.py` is a different thing with a similar name

`alan/teams/_pr_forge.py` (`forge_target_for_ws`, `governing_config_for_ws`, `role_model_effort`,
`GH_TIMEOUT_S`) is workspace-to-forge-target *resolution* logic living inside `alan.teams` —
it answers "which `ForgeTarget` and `EngineConfig` govern this workspace," not "how do I talk to the
forge." It shares the word "forge" with this port and nothing else: it is not a transport seam, it
performs no `gh` calls, and it stays exactly where it is. It no longer defines the default target:
`DEFAULT_FORGE_TARGET` lives in `alan/engine_config.py` (see "X2.7 PR16" above). `alan/forge_gh.py`
imports `GH_TIMEOUT_S` from it purely as a shared constant, the same way any other `teams` caller
does.

## Operations deliberately not on the port

- **`fetch_failing_e2e_tests`** (`_pr_branch.py`) returns `tuple[alan.e2e_failures.FailingE2ETest, ...] | None` — `alan.e2e_failures` sits at layer 209, above `teams` and far above the port's slot beneath `config_files`. The port cannot name that return type from below. It stays a caller-side composition of two port operations, `status_check_rollup` (through `fetch_failing_job_ids`) and `transport_check_run_annotations` (through `fetch_check_run_annotations`).
- **The check-run listing is on the port** (reversed in X2.7 PR15). It was listed here because `_iter_check_runs` takes an `on_fail: Callable[[int, str], None]` audit callback from its caller and every family caller supplies a different one (`_audit_sonar_failure`, `_audit_gate_detail_failure`, `_audit_quarantine_query_failure`, `_audit_gate_rerun`/`_audit_gate_query_failure`). The callback never had to cross the port: the operation `commit_check_runs_page` returns a typed `ApiResult` and takes no callback, and the callback stays with the caller, which reports a raised fault or a non-zero exit through it and parses the payload itself. What the earlier reasoning ruled out — baking one audit shape into the port or accepting an arbitrary callback — is still ruled out; it was never the only shape available.
- **`verifier_env`** — no single function of this name exists; the two verifier-identity helpers are `verifier_gh_env` (used internally by `reviews`/`dismiss_review` and, behind `as_verifier`, by `unresolved_threads`/`resolve_thread`/`post_review`; never exposed as a port method or a port parameter, because every caller wants it fused with the operation it authorizes) and the transport credential, which the adapter's private `_transport_env` derives through the public `secrets_store.transport_credential` (X2.7 PR10), fused to every transport-identity operation and never exposed as a port method or a port parameter (the `_pr_branch._transport_gh_env` copy was deleted in X2.7 PR15).
- **`_pr_ready`'s two residual `_pr_branch` reaches** — `verifier_identity()` (an environment-and-profile read that performs no forge call, see below) and `_iter_check_runs` (which since X2.7 PR15 reaches the forge through `commit_check_runs_page`; `_pr_ready` still calls it, caller-only, with a `forge_target` in place of the owner and repository names). `_pr_ready` builds no argv and calls none of the three transport entry points; `verifier_identity()` is the one classified not-a-port-operation, so the readiness surface counts as finished.
- **`verifier_identity`** (`_pr_branch.py`) takes no arguments at all and returns `VerifierIdentity | None`. It resolves the verifier's login from `ALAN_VERIFIER_LOGIN` in the environment (stripped; blank counts as unset), then from the default profile's `verifier_login` (`default_profile.resolved().profile.verifier_login`, the process-cached profile, never a file read at call time), then from nothing, and it has no built-in login. It performs no forge call, so it is not a forge operation; it is a plain environment-and-profile read that happens to live in `_pr_branch.py`, which replaced the former `verifier_login() -> str` and its hardcoded default. The result is the one place the port's callers learn which account is the verifier: `VerifierIdentity.matches(author)` is true only for a non-empty author exactly equal to the login, `is_verifier(identity, author)` is false for `None`, and "not configured" is `None`, never an identity holding an empty string. With nothing configured every caller fails closed, including for an author that is missing or empty: `_pr_post` fetches no review stream and dismisses nothing, `_pr_waiver_dismissal` targets no review, `_pr_ready`'s verifier lane keeps `CHANGES_REQUESTED`, `_verifier_thread_resolution` holds as `verifier_login_unconfigured` before it calls `viewer_login`, `_pr_feedback` exempts only the repository owner from the permission lookup, and `unblock_recipes` counts every `CHANGES_REQUESTED` reviewer as another author (`pr_iteration_pipeline.md`, D28 "Verifier identity", lists each consumer). The operations that act *as* the verifier (`reviews`, `dismiss_review`, `post_review`, `unresolved_threads` and `resolve_thread` with `as_verifier=True`, `viewer_login`) are untouched: they authenticate with the reviewer credential and neither read nor assume a login. The login is also not a port parameter, for the reason the credential is not: the callers hold the identity and compare it, the adapter only authenticates.

## The canary's empty-set row is a real measurement, not a floor

`tests/test_transport_canary.py`'s `_network_functions` helper walks every `ast.FunctionDef` in a
module, module-level and class-method alike, building a qualified name (`GhForge.read_pr`, for
example) for each, and flags any whose source contains a `"clone"`/`"fetch"`/`"push"` string
literal. It scans `GhForge`'s methods by qualified name the same way it scans `slot_pool.py`'s
top-level functions, so `_EXPECTED_FORGE_GH_NETWORK_FUNCTIONS = frozenset()` reflects the methods
having been audited and found clean, not an artifact of them living inside a class the scanner
couldn't see. It is substantively true: none of `GhForge`'s eighteen methods builds a
`git clone`/`fetch`/`push` argv — the three `gh pr` methods and `pr_patch` build `gh pr view`/`checks`/`merge`/`diff`
command lines, `open_prs_by_author` builds a `gh pr list` command line, the two review methods, the four query-shaped ones and `compare_base` build `gh api` command lines,
`search_open_issues`, `create_label` and `create_issue` build `gh issue list`/`label create`/`issue create` command lines,
and the three delegating methods build no argv of their own at all. The canary's audited set is
unchanged by PR3 and by X2.7 PR1: no row was removed, no test id changed, no assertion weakened and
no exemption added.

## The rejected `alan/teams/forge.py` option

An earlier shape considered putting the port inside `alan/teams/` (as `alan/teams/forge.py`),
reasoning that every existing forge-facing type it might want to return (`BranchUpdateOutcome`,
`e2e_failures.FailingE2ETest`) already lives at or above that layer. That shape was rejected: X2.3's
purpose is to make the merge family's forge calls fakeable *underneath* `teams`, and a port that
lives inside `teams` cannot be imported by anything below `teams` — it would be exactly as
unsubstitutable from outside the family as today's direct `gh` calls are, defeating the item's whole
premise. Sitting below `teams` is the sole cause both of the line-count pressure documented in the
PR description (the argv methods must rebuild what `gh_checked` and the `_forge_errors` classifiers
already do above this layer) and of every return type here being a primitive or a small frozen
dataclass rather than the richer domain type each existing caller already produces.

## P1.14: the stranger-thread settle operations

`resolve_thread_checked`, `unresolve_thread` and `repo_permission` serve
`alan/teams/_pr_feedback.py::fetch_feedback`, which stops a stranger's single-comment review thread from
blocking the merge gate (`docs/architecture/pr_iteration_pipeline.md`, D28). The port and the adapter go from
forty-nine to fifty-two operations each, and `tests/test_forge_gh_transport_ops.py`'s operation-count pin moves
from 49 to 52; none of the three accepts an environment, credential or transport-shaped parameter.

**Strict reviewer identity.** The existing `resolve_thread(as_verifier=True)` resolves `secrets_store.verifier_gh_env`
through `_verifier_env`, which returns `None` when the credential is absent, so on the owner's install `gh` then runs
under the ambient token (on a copy the seam falls back to the transport credential, and these two strict
mutations still refuse). The new mutations must never resolve as anyone but the reviewer, so they are separate operations that
enforce the rule inside the adapter: an empty reviewer env yields the failed `_NO_VERIFIER_CREDENTIAL` result and
`run_subprocess` is never called. `resolve_thread`, `_RESOLVE_THREAD_MUTATION` and `_verifier_env` are unchanged, and
`resolve_review_thread` and `resolve_thread_as` keep their callers.

**Read-back.** Both mutations select `thread{isResolved,comments{totalCount}}`, so the caller confirms the new state and
the thread's whole comment count from the mutation response itself. The feedback query's thread selection also gains
`comments(first:1){totalCount,...}`.

**Test fake.** `FakeForge` records all three through `_call` and lists them in `_FORGE_METHOD_NAMES`, but they stay out of
`_ADAPTER_BACKED_BY_DEFAULT`: an unstubbed call fails as `unstubbed forge call`, so no test can reach a real `gh`
mutation. `tests/test_forge_fake.py` covers them with the strict unstubbed-call and recorded-argument tests, and
`tests/test_forge_gh_repo_permission.py` covers the adapter: argv, URL quoting, ambient environment, the four
`repo_permission` outcomes, the exact mutation strings, the reviewer-only environment and the no-credential result.
