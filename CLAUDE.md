# CLAUDE.md

alan is an autonomous multi-agent engineering system: a single-process FastAPI app (port 8585, React UI in `alan/web/`) whose dispatcher runs headless `claude` workers that plan, implement, verify, review, and squash-merge PRs against your copy of this repository. alan's own workers read this file, so keep every command and path in it accurate.

## Commands

```bash
uv sync --locked --extra dev
uv run python -m alan --port 8585         # serves /api/* and (in prod) the built UI
cd alan/web && npm ci && npm run dev      # frontend dev; Vite on 5173 proxies /api → 8585

# One file (addopts force -n 6 + coverage, so a lone file fails without these flags):
uv run pytest tests/test_claude_md_pip_audit_gate.py --no-cov -n 0

# Backend gates (all must pass before pushing)
uv run pytest                             # full suite ONCE, last (2x max); > x.log, grep it
uv run ruff check . && uv run mypy --num-workers=4 alan/ tests/
uv run pyright                            # strict; catches hallucinated APIs
uv run lizard --CCN 7 --length 60 --languages python --exclude "alan/web/*" alan/
uv run xenon --max-modules A --max-average A alan/
uv run lint-imports
uv run bandit -c pyproject.toml -r alan/
uv run vulture --min-confidence 80 alan/ tests/
uv run python -m alan.pip_audit_gate --check  # gates only pyproject.toml/uv.lock PRs

# Frontend gates
cd alan/web && npm run typecheck && npm run lint && npm run test:coverage
cd alan/web && npm run lint:knip && npm run lint:deps && npm run lint:dup
cd alan/web && npm run e2e                # Playwright; spawns FastAPI with stub claude
```

## Architecture

Concept docs live in `docs/architecture/` — its [README.md](docs/architecture/README.md) is the complete one-line index. Read the relevant doc BEFORE changing a subsystem: the invariants live there, not in code comments (there are none). New subsystem detail belongs in `docs/architecture/`, not in this file.

## Gates & conventions (CI blocks PRs on failure)

- Coverage is total: backend 100% branch, frontend 100/100/100/100 + jscpd threshold 1 (duplication blocks). Code without tests is a guaranteed CI failure.
- The layered import architecture is CI-enforced: a new backend module must be deliberately placed in the `[tool.importlinter]` layers list in `pyproject.toml`. The contract is exhaustive — a module missing from the list fails `lint-imports`, and all route siblings are covered by the independence contract.
- Complexity: lizard CCN < 8 / length ≤ 60 + xenon A (backend); ESLint `complexity ≤ 8`, `no-console` (frontend).
- Size ratchets (`tests/test_size_ratchets.py` + `tests/size_baseline.py`): test files ≤1000 / backend modules ≤800 non-blank lines. Files already over cap at baseline are pinned shrink-only in `tests/size_baseline.py`; under-cap files are never pinned and grow freely to the cap. New content for a capped file goes in a **new sibling file/module**, never a raised cap or pin. A baseline raise is a human call, made by hand-editing `tests/size_baseline.py` — there is no regeneration mechanism. `TEST_FILE_CAP` also governs the frontend `.test.ts`/`.test.tsx` population.
- The mobile-audit baseline (`alan/web/e2e/mobile-audit/baseline.json`) is tightening-only, enforced by a pinned high-water literal (`baseline-high-water.ts`) that the audit loads through `load-baseline.ts`; a raise requires hand-editing the pin alongside the baseline and is a human call — there is no regeneration mechanism. The desktop-audit baseline (`alan/web/e2e/desktop-audit/`) is the same tightening-only, hand-edit-only, no-regeneration ratchet — see `docs/architecture/mobile_ui_spec.md`'s desktop-audit section.
- Generated API types: `docs/architecture/api_types.md`.
- SonarCloud is optional and advisory: a copy may skip it in setup, the required `gate` check passes without it, and when connected its findings never block a merge — see `docs/architecture/sonarcloud.md`.
- Drift gate: a PR touching `alan/server.py`, `alan/routes/infra.py`, `alan/routes/_deps.py`, `alan/claude_cli.py`, `alan/__main__.py`, `deploy/ai.alan.plist`, `pyproject.toml`, `scripts/deploy_restart_decision.py`, `alan/served_sha.py`, `alan/deploy_refresh.py`, or `scripts/deploy_refresh.py` must also update `CLAUDE.md`, `docs/AGENTIC_CODING_GUIDE.md`, or `docs/architecture/**`. `alan/drift_gate.py` is authoritative for the list. Bypass label: `drift-ok`. Local check: `uv run python -m alan.drift_gate --check`.
- Fleet workers' real permission boundary is Claude Code's built-in Bash safety analyzer (auto-approves read-only Bash commands even when not pre-approved; denies mutating Bash commands headless — no interactive channel to approve them) plus alan's own hard block on `Write`/`Edit`/`NotebookEdit` outside `acceptEdits` mode (only `implementation`/`implementation_fix` switch into it) — `BASE_ALLOWED_TOOLS` (`alan/teams/_base.py`) is pre-approval, not the boundary. A new **mutating** Bash gate command must stay reachable from `BASE_ALLOWED_TOOLS`, or auto mode denies it headless; read-only Bash commands are auto-approved only inside the session's working directories — a role inspecting paths outside its checkout needs `WorkerOverrides.add_dirs` (`--add-dir`) plus explicit pre-approvals (`docs/architecture/claude_cli_streaming.md`). Full boundary table: `docs/audits/auto-mode-boundary-2026-07/README.md`. Roles that fetch external sources go through `python3 -m alan.source_fetch` — see `docs/architecture/source_fetch.md`.
- `ALAN_*` env vars are for **state isolation only** — redirecting a persistent store's runtime write path away from `~/.alan/` — never user-facing configuration. A new persistent store registers its redirect env var in every state-isolation surface it touches (e2e `serverEnv` in `alan/web/playwright.config.ts`, CI workflow env, `deploy/ai.alan.plist`, `tests/conftest.py`, `_worker_env`) in the same PR — a missed surface leaks test state into the real `~/.alan/`. Every `ALAN_*` var read by `alan/` Python source is classified as isolation or named excepted-config in `tests/env_var_classification.py`; `tests/test_env_var_classification.py` fails any new unclassified var — that is the enforcement mechanism, not a suggestion.
- Tests never spawn `gh` (`tests/gh_guard.py`: writes refused, reads stubbed rc 86) — mock the seam; `tests/gh_read_baseline.py` is a shrink-only legacy list (`docs/architecture/ci_isolation.md`).
- Squash-merge: the PR title becomes the `main` commit subject and the PR body carries the "why" — the no-comments rule depends on it.
- Structural gate on comments in test code (the Don'ts rule below is enforced, not merely documented): `tests/test_no_comments_in_test_code.py` walks every `tests/**/*.py`, and the `test-comments/no-prose-comments` ESLint rule (`alan/web/eslint.config.js`) covers `alan/web/**/*.test.{ts,tsx}`, `alan/web/src/test/`, and `alan/web/e2e/`. Both allowlist machine-read directives only (`tests/no_comments_policy.py:MACHINE_READ_COMMENT`, `eslint.config.js:MACHINE_READ_COMMENT`) — widening either to land a PR is a gate downgrade, not a fix.
- Structural gate on unused private identity re-export facades (`tests/test_reexport_facade.py`): a `from x import y as y` alias in `alan/` is flagged when it has no load reference elsewhere in the module, the module basename or the name is `_`-prefixed, and the name is absent from the module's own `__all__`. `REEXPORT_FACADE_ALLOWLIST` (`tests/reexport_facade.py`) ships empty and is a tightening-only ratchet — fix a real hit by deleting the re-export or declaring it in `__all__` if it's a genuine deliberate facade, never by adding an allowlist entry.
- `alan/teams/_pr_freshness.py`'s `MEASURED_BASELINE_PATHS` is the single authoritative list of tightening-only ratchet files, consumed by the merge gate's staleness check **and** by `alan/teams/_ratchet_direction.py`'s advisory loosening detector — see `docs/architecture/ratchet_direction.md`.
- Structural gate on hardcoded default-repository GitHub links (`alan/web/src/github-links.drift.test.ts`, mechanism detail in its own assertion messages): widening the exemption set, or falling back to a hardcoded default repo instead of threading a real `repo: RepoSlug | null` through, is a gate downgrade, not a fix.

## Running your copy

- `scripts/setup_macos.sh` (or `./setup.sh` from a fresh checkout) installs the launchd service `ai.alan`, which serves `http://127.0.0.1:8585` from your copy's checkout. Setup runbook: `docs/architecture/setup.md`.
- The self-updater `ai.alan.update` applies merged `main` to the checkout once GitHub-hosted CI has proved that exact commit green, and restarts the service when the change needs it: `docs/architecture/self_update.md`. There is no deploy pipeline beyond it.
- Secrets go in `~/.alan/secrets.env` (chmod 600), never in the git-tracked plist.
- Runtime state lives under `~/.alan/`. Tests, dev sessions, and worker subprocesses must NEVER write that tree — always run with `ALAN_*_ROOT` overrides (`docs/architecture/ci_isolation.md`).
- Team workers spawned in journal mode start in their own process session (`ALAN_DETACHED_WORKERS`, auto/on/off) and survive a service restart, re-attached by the restarted service; the dial is excepted-config, pinned off in `_worker_env` and in the e2e server environment — see `docs/architecture/worker_records.md`.

## Don'ts

- No comments / no docstrings in application code (`alan/`); names carry the meaning, "why" lives in the PR body. One exception: a short invariant comment is allowed at concurrency seams — lock/lease ordering, inside-vs-outside-lock contracts, timestamp-domain choices — where a name cannot carry a cross-function invariant. Module docstrings ARE allowed in `scripts/`.
- **The same rule binds test code, with no concurrency-seam exception**: zero comments and zero docstrings under `tests/`, in `alan/web/**/*.test.{ts,tsx}`, `alan/web/src/test/`, and `alan/web/e2e/`. The test's *name* is its documentation — see `docs/AGENTIC_CODING_GUIDE.md` §3 for the worked example and the full teaching. This is not a new-code-only policy and not a style preference: do not add a comment to an existing test file, do not restore one you find absent, and do not reintroduce them while editing nearby lines. The only survivors are the directives named in `MACHINE_READ_COMMENT` for both gates; when one trails a prose comment (`# why  # noqa: X`), the prose goes and the directive stays.
- `claude` CLI: use `--append-system-prompt`, never `--system-prompt` (preserves the default preamble); never `--dangerously-skip-permissions` — unattended edits need `permission_mode="acceptEdits"`, since `auto` + `--allowedTools Edit` waits forever for approval in headless `-p` mode.
- Don't paste file contents into prompts; use `@path/to/file` references.
- Don't add features, abstractions, or quality gates the current PR's scope doesn't need.
- Don't downgrade gates to land a PR — refactor the code or split the change.
- Don't `pip install` / `npm install` in CI; always `uv sync --locked` / `npm ci`.
