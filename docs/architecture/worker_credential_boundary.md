# Worker credential boundary

Every Claude session alan starts is refused when it tries to read the install's credential files: fleet workers of every role, the incident investigator, and the build and explore chats. This is enforced in the argument builders in `alan/claude_cli_grants.py` (`disallowed_tools_args`, `grant_args`), reached from `_build_cmd` and `_chat_cmd` in `alan/claude_cli.py`, so it holds whatever the stored engine record says. It is roadmap item P1.12; the decision to change production for it is PD17, and it answers a credential leak found in the public-release review. The empirical permission boundary this builds on is `docs/audits/auto-mode-boundary-2026-07/README.md`; the argument-list plumbing is `docs/architecture/claude_cli_streaming.md`.

## Claude Code version this relies on

Claude Code **v2.1.207** — the version the boundary audit observed. The deny semantics used here are: a `--disallowedTools` rule beats any `--allowedTools` pre-approval, `Read(//<absolute path>)` and `Read(//<absolute path>/**)` cover the built-in file tools (`Read`, `Grep`, `Glob`), and a `Bash(<prefix>:*)` deny matches a command that starts with that prefix. The post-merge canary below re-records the version it observed in the release driver's run notes; when it differs from the line above, update this line in the same change that records the result.

## Deny rules

Every invocation that has tools carries the same code-owned rule set, in every permission mode, passed as one `--disallowedTools` flag followed by each rule as its own argv element (never a joined string). Outside `acceptEdits` the joined write-tools token (`Write Edit NotebookEdit`) stays first; under `acceptEdits` (the two implementation roles) it is absent and the credential rules are the whole deny surface. An invocation with no tools keeps `--disallowedTools *` exactly, with no credential rules.

The protected locations are derived at argument-build time from the resolved state roots and the gh config location — never hard-coded, and no new `ALAN_*` variable — so a redirected root moves the rules with it. Each path is emitted in its configured absolute form and in its `resolve()` form (collapsed when they are equal), because a symlinked root would otherwise dodge a rule written against the other spelling. `Read(//…)` is the absolute-from-filesystem-root form; a single-slash rule is project-relative and matches nothing here.

| Rule | Covers |
|---|---|
| `Read(//<alan-home>/secrets.env)` | the secrets env file — `<alan-home>` is the parent of the state root |
| `Read(//<state-root>/control_token)`, `Read(//<state-root>/control_token_meta.json)` | the control token and its metadata |
| `Read(//<secrets-store>/**)` | the secrets store directory (`roots.secrets_store_root()`) |
| `Read(//<engines-root>/**)` | the per-engine records directory (`roots.engines_root()`) |
| `Read(//<gh-config-dir>/**)` | the gh config directory: `GH_CONFIG_DIR`, else `XDG_CONFIG_HOME/gh`, else `<home>/.config/gh` |
| `Read(//<home>/.claude/.credentials.json)`, plus `Read(//<CLAUDE_CONFIG_DIR>/.credentials.json)` when that variable is set | the Linux Claude credential file (macOS keeps the credential in the login Keychain, covered by the `security` deny) |
| `Bash(security:*)` | Keychain reads through the `security` CLI |
| `Bash(git credential:*)` | credential-helper reads |
| `Bash(gh auth:*)` | `gh auth token` and `gh auth status` |
| `Bash(git diff --no-index:*)`, `Bash(git blame --contents:*)` | git's two ways to print an arbitrary file's contents outside a repository |

A parity test in the private repository keeps this set equal to the state-backup tooling's secret markers, so a new secret marker that tooling learns about fails the parity test until a rule covers it. Roles that hold `--add-dir` grants (the maintenance team's state tree, the investigator's workspace, brief refinement's source directory) still carry these rules; a directory grant does not lift a deny.

## Narrowing rule

For every role except `implementation` and `implementation_fix`, `grant_args` filters exactly three grants out of the list it is given: `Bash(uv run:*)`, `Bash(npx:*)` and `Bash(npm --prefix:*)`. It is a literal three-entry filter, not an allowlist — every other stored grant passes through untouched, including `Bash(npm run:*)` (package.json scripts only) and the read-only staples. Stored engine records are never rewritten.

The exemption signal is `permission_mode == "acceptEdits"`: only the two implementation roles set it (`alan/teams/implementation.py`, `alan/teams/implementation_fix.py`), `grant_args` defaults to `"auto"` so an unknown caller fails closed, and `tests/test_worker_credential_boundary.py::test_accept_edits_literal_is_confined_to_the_implementation_roles` pins the literal to those two modules plus `claude_cli_grants.py`. Implementation roles keep their grants because they run their own gates; they carry the full deny set.

The three removed grants are replaced, at each non-implementation call site, by `with_gate_reachability(tools, cfg.gate_commands)` in `alan/claude_cli_grants.py`, which only appends (after the stored tools, without duplicates):

- for each gate command `uv run [--no-active] <tool> …`: `Bash(uv run <tool>:*)` and `Bash(uv run --no-active <tool>:*)`;
- for each gate command `npm --prefix <dir> run <script> …`: `Bash(npm --prefix <dir> run:*)`, which can only run package.json scripts;
- for every other gate (`bash scripts/gate.sh`, `dotnet build`): the full command as a prefix, only when every word is made of path-safe characters and at least one word is an argument rather than a bare interpreter;
- a fixed set for commands the role prompts tell workers to run (`PROMPT_INSTRUCTED_GRANTS`): `uv run` and `uv run --no-active` for `lizard`, `xenon`, `vulture` and `pytest` (the architecture prompts, and targeted runs such as `uv run --no-active pytest tests/x.py --no-cov -n 0`), `Bash(npm --prefix alan/web run:*)` (the changed-E2E runner, the frontend coverage run, the filtered `npm --prefix alan/web run test -- <path>` run and the screen-capture step) and `Bash(npm --prefix alan/web ci:*)` (the clause auditor's warm-up).

**Interpreter exclusion.** A tool that is itself an interpreter or launcher (`python`, `python3`, `node`, `bash`, `sh`, `env`, `npx`, `uv`, …) after `uv run` never gets a per-tool grant — `Bash(uv run python:*)` would be the general shell again. Its gate is granted as the exact full-command prefix, and a bare interpreter with no argument is granted nothing.

**Warmup commands take no part.** The host runs them (`alan/slot_pool.py`), not a role, so `uv sync` and `npm ci` never feed a grant. A structural test (`test_every_non_implementation_worker_overrides_call_site_wraps_gate_reachability`) requires every `WorkerOverrides(...)` call in `alan/teams/` other than the implementation roles, the shared cwd clone and the default fallback in `_base.py` to wrap its tools in `with_gate_reachability`, so a new call site cannot silently lose its gates.

## Reach table

Derived from each role's argument list on the default engine (`tests/test_worker_credential_reachability.py` builds these for four engine shapes: the default engine, an iOS-preset engine whose gate is `bash scripts/gate.sh`, an engine with no gate commands, and a copy's default profile). Every row carries the **full deny set** above; only the grants differ. "Staples" is the read-only set every role shares: `Read`, `Grep`, `Glob`, `Bash(git log|diff|status|show|blame|ls-files|rev-parse|merge-base|rev-list|ls-tree|cat-file|describe|diff-tree:*)`, `Bash(ls|cat|pwd|head|tail|wc|diff|nl:*)` and `Bash(npm run:*)`. "Derived" is the reachability grants above.

| Team | Roles | Mode | Grants |
|---|---|---|---|
| `architecture` | `structure_mapper`, `complexity_analyst`, `debt_auditor`, `change_planner`, `devil_advocate`, `judge` | `auto` | staples + derived |
| `brief_refine` | `brief_refiner` | `auto` | staples + derived; `--add-dir` the workspace when a source file exists |
| `decomposer` | `decomposer` | `auto` | staples + derived |
| `implementation_plan` | `architect`, `backend_analyst`, `frontend_analyst`, `qa_planner`, `devil_advocate`, `synthesizer` | `auto` | staples + derived |
| `verification` | `diff_reviewer`, `scenario_runner`, `judge`, `clause_auditor` | `auto` | staples + derived; `cwd` is the grounded checkout |
| `maintenance` | `code_health_analyst`, `ci_dependency_auditor`, `log_repo_scanner`, `security_scanner` | `auto` | staples + derived + `Bash(gh run list:*)`, `Bash(ps:*)`, `Bash(stat:*)`, `Bash(find:*)`; `--add-dir` the alan home |
| `research` | orchestrator, verifier, synthesizer | `auto` | staples + derived |
| `research` | workers | `auto` | staples + derived + `WebSearch`, `WebFetch`, `Bash(python3 -m alan.source_fetch:*)` |
| `brainstorming` | orchestrator, synthesizer; workers as `research` | `auto` | as `research` |
| `opportunity_scout` | orchestrator, verifier, synthesizer; workers as `research` | `auto` | as `research` |
| `ui_review` | orchestrator, synthesizer, workers | `auto` | staples + derived |
| `implementation` | implementer | `acceptEdits` | the stored grants unchanged: staples + `Bash(uv run:*)`, `Bash(npx:*)`, `Bash(npm --prefix:*)` + `Edit`, `Write`, `Bash(git:*)`, `Bash(uv:*)`, `Bash(npm:*)`, `Bash(node:*)`, `Bash(python:*)`, `Bash(python3:*)`, `Bash(pytest:*)`, `Bash(mkdir|mv|cp|rm:*)` |
| `implementation_fix` | implementer | `acceptEdits` | as `implementation` |
| `deploy_verify`, `dependency_sweep`, `pr_merge` | none | — | no Claude session: the host probes, sweeps and merges |
| investigator | investigator | `auto` | `Read`, `Grep`, `Glob`, `Bash(grep|cat|git log|gh pr view|gh issue view|gh run view:*)`; `--add-dir` the workspace |
| build chat, explore chat | chat turn | `auto` | `Read`, `Grep`, `Glob`, `WebSearch`, `WebFetch`, `Bash(git log|git diff|ls|cat|gh pr list|gh pr view|gh issue view:*)`; the write-tools token first, then the credential rules |

Which rule covers which path is the rule-set table above; the table is the same for every row, because the deny set does not vary by role. The two things that do vary by role are whether a Bash grant can reach a file the Read rules cover (see the risks), and whether the role can run code at all: only the two implementation rows can, through `Bash(uv run:*)`, `Bash(uv:*)`, `Bash(python3:*)` and friends.

## The canary

The backend suite proves the rule coverage without a real CLI. `alan/web/e2e/fixtures/claude` (the stub CLI) collects the values of `--disallowedTools` until the next `--` flag, and when the prompt on stdin starts with `READ_PROBE: <path>` it answers `DENIED: <rule>` when a `Read(//…)` rule (or `*`) covers the path and `ALLOWED` otherwise. `tests/test_worker_credential_canary.py` runs the stub with the argument list a real worker would get and probes synthetic paths under redirected roots: the secrets store, the control token, the gh config directory, the engines directory and the Claude credential file are refused; an uncovered path and a `secrets-other` sibling are allowed. It never reads the real state tree, and the guard that stops tests from spawning `claude` (`tests/conftest.py::_forbid_real_claude_spawn`) is unchanged — the test spawns `bash <stub>`, and a second test asserts a direct `claude` spawn is still refused.

**Real-CLI canary — a post-merge step the release driver runs, not part of this change.** The stub proves the argument list, not the CLI's interpretation of it. On both macOS and Linux, with `ALAN_*` roots redirected to a throwaway tree holding a synthetic canary file (never the real state tree), start one worker session with the production argument list and record `claude --version` and each verdict:

1. the `Read` tool on the canary — must be refused;
2. Bash `cat <canary>` — the reach of a Read deny over Bash `cat`, `head`, `tail`, `grep`, `diff` and `nl` is **unverified until this step runs**; record whether it is refused or read;
3. `git diff --no-index /dev/null <canary>` — must be refused by the Bash deny;
4. a gate that must still run, for example `uv run --no-active pytest --version` — must succeed.

A step 2 that reads through does not fail the release, but it moves the affected roles from "covered" to a named remaining risk below and is the input to the OS-sandbox follow-up.

## Remaining risks

- **Script execution by the implementation roles.** They keep `Bash(uv run:*)`, `Bash(uv:*)`, `Bash(python3:*)` and `Bash(node:*)` so they can run their own gates; a script they write and run is not constrained by a Read deny on the built-in tools. They have the deny rules and nothing more.
- **Gate commands are code execution.** `uv run pytest`, `ruff`, `mypy` and the frontend scripts execute repository code and plugins; narrowing removes the general shell, not the ability to run a test that reads a file.
- **`find -exec`.** The maintenance roles hold `Bash(find:*)`; `find <root> -exec cat {} +` is a Bash read that a Read rule may not intercept.
- **Recursive grep over a parent directory.** `Grep` (and Bash `grep -r`) over `<alan-home>` visits the secrets store beneath it; whether a Read deny on a child path filters a recursive walk is part of the canary's step 2 and is unverified.
- **`diff` and `nl`.** Both are in the staples and print file contents; like `cat`, they are covered only if the CLI applies Read denies to Bash.
- **Argument-pattern git denies catch only the leading form.** `Bash(git diff --no-index:*)` does not match `git diff -U0 --no-index …` or `git -c core.x=y diff --no-index …`, and `Bash(git blame --contents:*)` has the same limit.
- **A secrets-file location moved by the deploy-time override.** `deploy/run_alan.sh` reads `ALAN_SECRETS_FILE`; the Python side never does (an `ALAN_*` variable read there would be a new user-facing knob). The rule for `secrets.env` follows the state root's parent, so a secrets file moved by that override is not covered.
- **`security` and `gh auth` are prefix denies.** A wrapper script or `env security …` is not a prefix match; the Keychain is a prompt-free read for the same user, which is why the dedicated-user advice below is the real mitigation.

## Stored versus enforced

Stored engine records, `render_allowed_tools`, `render_base_tools` and the engine-configuration page still show the stored grants, including `Bash(uv run:*)`. What is enforced is the argument builder's override: the three grants are removed from the argument list of every non-implementation role whatever the record says, and the credential denies are added whatever the record says. The persisted per-node `*.config.json` `allowed_tools` is the pre-narrowing list plus the reachability additions; the flag values on the spawned command line are the ground truth. `tests/test_auto_mode_boundary_smoke.py` measures the stored grants, not the argument list. Workers already in flight when this change deployed, detached workers that a service restart re-attached, and nodes resumed with `--resume` keep the argument list they were started with — only a freshly started session carries the new rules.

## Dedicated macOS user

The deny rules are a permission layer inside one user's session; the durable boundary is running alan under a macOS user that owns nothing else: an account that holds no model credential, no `gh` login and no `secrets.env`.

## Follow-ups (not in this change)

- An OS-level Bash sandbox for the implementation roles.
- The driver loops' space-joined deny list in `alan/driver_loop_cmd.py`, which does not carry these rules.
- `Edit` and `Write` denies on the secret paths for the implementation roles.
- Boundary-smoke coverage of the enforced argument list rather than the stored grants.
