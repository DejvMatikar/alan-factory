# Drift gate (`alan/drift_gate.py`)

`alan/drift_gate.py` is the single source of truth for the CI drift gate. It mirrors the `drift-gate` job in `.github/workflows/tests.yml` so the check can be run locally and by autonomous workers before a PR is opened.

## Constants

`LOAD_BEARING_FILES` -- `frozenset[str]` of repository-root-relative paths that require a companion documentation update when changed. Mirrors the `loadbearing` filter in the CI workflow exactly:

- `alan/server.py`
- `alan/routes/infra.py`
- `alan/routes/_deps.py`
- `alan/claude_cli.py`
- `alan/__main__.py`
- `deploy/ai.alan.plist`
- `.github/workflows/ci-deploy.yml`
- `pyproject.toml`
- `scripts/deploy_restart_decision.py`
- `alan/served_sha.py`
- `alan/deploy_refresh.py`
- `scripts/deploy_refresh.py`

`_DOC_PATTERNS` -- tuple of `fnmatch` patterns for allowed documentation targets (mirrors the `docs` filter):

- `docs/architecture/**`
- `CLAUDE.md`
- `docs/AGENTIC_CODING_GUIDE.md`

## Public API

`changed_paths(base_ref: str | None = None) -> list[str]` -- runs `git diff --name-only {base_ref}...HEAD` (three-dot merge-base diff, matching dorny/paths-filter behaviour) and returns the changed file paths. An explicit `base_ref` keeps its exact prior behaviour unchanged: `_SAFE_BASE_REF` validation, then the diff, raising `ValueError` on an unsafe ref or `RuntimeError` on a non-zero git exit code (with the exit code and stderr in the message). `base_ref=None` (the new default) instead resolves the base **at the point of use** via `alan.base_ref.resolve(Path.cwd())` -- a fresh `git fetch origin main` in the current working directory, never a cached or possibly-stale local ref (see `docs/architecture/e2e_testing.md` for the wider staleness problem this closes across four call sites, of which the CLI entry point below is one). When that resolution itself fails (fetch failed, timed out, or `git` was unavailable), `changed_paths` raises `BaseUnresolvedError` -- distinct from `RuntimeError`/`ValueError`, since it means "the base could not be established," not "the diff failed" or "the ref was malformed."

`drift_gate_violated(paths: list[str]) -> bool` -- returns `True` when at least one load-bearing file is in `paths` and no documentation target is. Returns `False` for paths that touch no load-bearing files (unaffected builds).

`doc_target_hint() -> str` -- returns a human-readable comma-separated list of allowed documentation targets suitable for embedding in prompt text (e.g. `` a path under `docs/architecture/`, `CLAUDE.md`, or `docs/AGENTIC_CODING_GUIDE.md` ``). Derives its output directly from `_DOC_PATTERNS` so prompt text stays in sync with the CI gate without duplicating the list.

## CLI entry point

```
python -m alan.drift_gate [--check] [--base-ref REF]
```

`--base-ref` now defaults to unset (`None`), so the CLI resolves its base fresh on every invocation rather than trusting whatever `origin/main` happened to be cached in the caller's checkout; passing `--base-ref` explicitly still bypasses resolution entirely, exactly as before. Exits 0 when no doc update is required; exits 1 with an actionable error message when `drift_gate_violated` is true and `--check` is passed. On `BaseUnresolvedError` the CLI prints `DRIFT GATE: base unresolved — check skipped` to stderr and exits **0** unconditionally (even with `--check`) -- a transient network or forge fault must never red a worker's gate turn over a check that is advisory at the point workers run it. This is safe because the CLI is a local backstop, not the authority: CI's actual drift gate is the `dorny/paths-filter`-driven `drift-gate` job in `.github/workflows/tests.yml`, which computes its own changed-paths set independently and is unaffected by whether this entry point could resolve a base. Does not modify any file, CI configuration, or apply the `drift-ok` bypass -- that remains a human decision.

`alan/teams/implementation.py`'s `_drift_gate_check` (the in-worker backstop that decides whether to force a documentation-update resume before a worker's session ends) follows the identical two-step shape: `alan.base_ref.resolve(worktree)` first, then `changed_paths(base)` with the resolved ref, swallowing both an unresolved base and a `RuntimeError` from the diff itself as "no violation detected" (never a hard failure) -- a transient fetch or diff fault must degrade to "didn't check" here too, not to a false backstop trigger. This check fetches unauthenticated (no `transport_env` credential is threaded through), which is unchanged from the inline `subprocess.run` it replaces; the practical difference this build introduces is that a private non-default engine now logs a warning and skips the backstop instead of silently comparing against a stale cached ref.
