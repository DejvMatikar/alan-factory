# GitHub token repo picker — `alan/github_repos.py` + `POST /api/github/repos`

Before any engine or project exists, a project-setup flow lets a user paste a raw GitHub
token and see which repositories it can reach and what it can do with each one.
`probe_repos(token)` runs exactly two read-only `gh api` calls — `user` (identity/validity)
and a paginated `user/repos` listing — and turns the listing's per-repo `permissions`
object (`pull`/`push`/`admin`) directly into the capability matrix the picker renders. It
is the **second consumer** of `branch_protection_compat.py`'s subprocess primitive
(`_invoke_gh_api`, `_ADMIN_SCOPE_MESSAGE`, `_GhApiCallError`), imported cross-module with
the same `# pyright: ignore[reportPrivateUsage]` convention `engine_preflight.py` already
uses for `engine_config._validate_before_register` — no new subprocess wrapper, no HTTP
client.

## Zero persistence, never raises

`probe_repos` never references `secrets_store` and never writes anything — the pasted
token lives only in the request body and this function's stack frame. Every failure mode
(invalid token, rate limit, malformed response, truncated listing) becomes a typed
`Finding` (`alan.branch_protection_compat.Finding`) in a normal `200` response body,
mirroring `engine_preflight.py`'s never-raises contract; only `require_control_token` on
the route itself can produce a 401/403/500. This makes "no orphan credential" a
test-level invariant — there is no code path that could write one.

## Two read-only calls, bounded pagination

1. `_check_token_valid` calls `gh api user`; a non-zero exit or `_GhApiCallError` (OSError,
   timeout) short-circuits with a single `Finding(check="token_valid", status="fail")` and
   `required_permissions` still populated — never a generic 500.
2. `_list_repos` walks `user/repos?per_page=100&page=N&affiliation=owner,collaborator,organization_member`
   (a query-string-in-path `gh api` call, never `-f`/`--paginate`) for up to 10 pages
   (1000-repo cap). A short page (fewer than 100 rows) stops the loop; exhausting all 10
   pages instead emits `Finding(check="repo_list_truncated", status="unknown")` — bounded
   pagination surfaced as data, not silently dropped rows. A non-zero exit, malformed JSON,
   or a non-array JSON response raises `_GhApiCallError` internally, caught by
   `probe_repos` and classified by `_list_error_finding`: `"403"`/`"429"` in the message
   becomes `check="rate_limited"`, anything else becomes `check="repo_list_failed"` — both
   named, neither a generic error.

## Per-repo vs token-wide findings

`RepoCapability` (`slug`, `read`, `write`, `admin`, `finding: Finding | None`) has its own
`finding` slot because `Finding` carries no repo dimension — a per-repo failure (today,
only "not admin" — `_ADMIN_SCOPE_MESSAGE`, the same "cannot read branch protection —
token lacks admin scope" text `branch_protection_compat.py` already uses) must attach to
the row it describes, not the top-level `GithubReposResult.findings`, which is reserved
for token-wide failures (invalid token, rate limit, truncation). A listing row with an
empty/missing `full_name` is skipped entirely — an unactionable, unselectable entry, not a
`RepoCapability` with `slug=""`.

**Known boundary — write vs branch-protection-admin split.** `write` comes straight from
the listing row's `push` bit; `admin` from its `admin` bit. A fine-grained GitHub token can
hold `contents:write` (so `write=True`, real push access) while lacking
`administration:read` (so `admin=False`, and the picker sees the same "cannot read branch
protection" finding a classic token without admin scope would produce). This module does
not — and is not meant to — distinguish "no admin scope" from "fine-grained token missing
the PR-write scope specifically": true pull-request-write verification (can this token
actually open a PR against this repo?) is deferred to the first real dispatch/preflight,
which is the only place a non-mutating check for that specific capability is possible.
`REQUIRED_PERMISSIONS` states this boundary as structured data — both a `classic` (`repo`)
and three `fine-grained` (`contents:write`, `pull_requests:write`,
`administration:read`) entries, each carrying its `token_type`, so the downstream wizard
can render token-creation guidance matching whichever token type the user is creating.

## Route and layering

`POST /api/github/repos` (`alan/routes/github.py`, `github_router`, prefix `/api/github`)
is control-token guarded (`Depends(require_control_token)`, the same dependency
`routes/secrets.py` and `routes/engines.py` use) and offloads `probe_repos` via
`run_in_threadpool`, following the same convention as every other secret-adjacent route.
Registered in `server.py` alongside `secrets_router`/`engines_router` and listed in the
"Routes siblings are independent" import-linter contract. `alan.github_repos` sits in
`[tool.importlinter]` layers directly above `branch_protection_compat` (imports its
private subprocess primitive) and below `routes` (which imports `probe_repos` /
`GithubReposResult`) — the same sandwich position `engine_preflight` occupies relative to
`branch_protection_compat`, just one layer shallower since `github_repos` has no
dependency on `engine_config`/`secrets_store` at all.
