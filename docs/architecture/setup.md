# Setup: `./setup.sh` and `scripts/setup_macos.sh`

The scripted path from a clean Apple-Silicon Mac to a running, profile-bound copy of alan whose GitHub repository is bound and hardened, and that can become ready to dispatch (roadmap items P1.9a, P1.9b and P1.9c). P1.9a boots the copy; P1.9b, the four steps after `start`, gives it a transport credential scoped to its one repository, hardens the repository settings that fit its visibility, applies branch protection through alan's own protect action and confirms the result with alan's preflight. P1.9c adds seven steps after `confirm`: `sonar` optionally connects SonarCloud to the repository (PD28: a copy is ready and dispatches without it), `verifier` stores the verifier bot's credential and records its login, `trigger` starts one CI run, `claude` checks that Claude Code works under launchd, `updater` installs the self-updater agent, `latch` waits for the CI run's `gate` job and, when SonarCloud is connected, for SonarCloud to report and records that it did, and `report` prints the final report. The copy stays "not ready to dispatch" until that report says it is complete. Both entry points are Bash 3.2 (the macOS system shell): one function per step, every step probes before it acts and is idempotent, nothing is traced, and no secret is ever an argument or an output.

## Entry points and steps

| Entry | What it is |
|---|---|
| `scripts/setup_macos.sh` | The step engine. Runs inside the user's own clone. Also sourced by `./setup.sh`. |
| `./setup.sh` (repo root) | The bootstrap entry. Sources the engine, inserts the `copy` step after `install`, and owns template resolution, the clone retry and the handover. |

Steps run in this order (`SETUP_STEPS`, a plain string list; each name `x` has a function `step_x`):

1. `preflight`: read-only probes (below), then the Homebrew-ownership check (below).
2. `install`: Homebrew, the seven formulae (`git gh uv node@22 gitleaks pre-commit tmux`), the Claude Code CLI, a `gh` sign-in. The same ownership check runs after Homebrew is located and before any formula is installed. `tmux` is the operator terminal's system prerequisite (`docs/architecture/operator_terminal.md`): installing it here means a fresh copy has a working terminal with no manual step. Each formula is probed with `brew list --versions` first and reported `[already]` on a re-run. The operator checkout is not a setup step: alan clones it itself the first time a session starts (`docs/architecture/deploy.md`, "Operator terminal").
3. `copy` (bootstrap entry only): create the user's private copy of the template and clone it, then hand over.
4. `build`: `uv sync --locked --extra dev`, `npm ci && npm run build` in `alan/web`, `pre-commit install`. No Playwright browser is downloaded.
5. `profile`: write the default-engine profile through `alan.default_profile_writer`.
6. `start`: secrets file, log directory, render and load the service, wait for the control token, require a healthy service serving the clone's HEAD.
7. `credential`: store the transport credential for the repository (below).
8. `harden`: the repository settings that fit its visibility (below).
9. `protect`: branch protection through alan's protect action, skipped when it already matches.
10. `confirm`: alan's preflight; fails only on the findings this item owns.
11. `sonar`: optional. Find the one SonarCloud project bound to the repository and write its settings into the profile and the repository, or skip it (below).
12. `verifier`: check the verifier bot's token, store it as the `reviewer` credential, record its login, restart the service (below).
13. `trigger`: dispatch one `tests.yml` run on `main` and record it (below).
14. `claude`: check that Claude Code works under launchd through one transient agent (below).
15. `updater`: render and load the self-updater agent `ai.alan.update` (below).
16. `latch`: wait for the recorded run's `gate` job and, when SonarCloud is connected, for SonarCloud to report on its commit, then write the latch (below).
17. `report`: the final report (below).

The full order, in `SETUP_STEPS` of the engine, is `preflight install build profile start credential harden protect confirm sonar verifier trigger claude updater latch report`; `./setup.sh` inserts `copy` after `install`. The manual inputs all come before the long unattended wait: the SonarCloud tokens (`sonar`, only when SonarCloud is connected), then the verifier bot's token (`verifier`), then the CI run starts (`trigger`), Claude is checked, the updater installs, and only then does `latch` wait on CI (and on SonarCloud, when it is connected). The steps `credential`, `harden`, `protect`, `confirm`, `sonar`, `verifier`, `trigger`, `latch` and `report` talk to the running service, so they need it up (`start` first); each names the missing precondition when it refuses. `claude` talks to `launchctl` and the `claude` executable only, and `updater` to `launchctl` only.

Flags: `--from <step>`, `--only <step>` (mutually exclusive), `--owner <org>`, `--use-gh-token`, `--accept-sonar-user-token`, `--no-sonar`, `--check-claude`, `--uninstall`, and `--template <owner/repo>` on `./setup.sh` only. `./setup.sh` forwards `--owner`, `--use-gh-token`, `--accept-sonar-user-token` and `--no-sonar` to the handover. `--accept-sonar-user-token` and `--no-sonar` take no value and only change the `sonar` step (below); giving both is not an error. `--check-claude` takes no value, is accepted by `scripts/setup_macos.sh` only and runs the Claude check alone (below); `./setup.sh` rejects it (exit 2) and names `scripts/setup_macos.sh --check-claude`, and it exits 2 together with `--from`, `--only` or `--uninstall`. An unknown flag, a step name outside `SETUP_STEPS`, a flag missing its value, a value that looks like a flag, an invalid `--owner`, `--from`/`--only` together, or `--uninstall` with `--from`/`--only` exits 2 before any tool runs. `HOME` unset exits 1 before any tool runs.

Later setup work extends setup only by appending a step name to `SETUP_STEPS` and defining its `step_<name>`; the guard, the flag parser, the state file and the closing line are shared. Before any step runs, setup prints the checklist of manual steps and an honest time estimate (`print_checklist`); its second item is creating the fine-grained token for the repository.

## Invariants

### The existing-install guard

`guard_existing_install` runs first in every mode: a plain run, `--from`, `--only`, `--check-claude` and `--uninstall`, and before any mutating call. It refuses (exit 1, `setup: refused at preflight: existing-install: ...`) when either:

- `~/.alan/queue.db` exists and `~/.alan/setup.state` does not. The queue database is created by the first boot of any alan, so this Mac already runs one that setup did not create. This is why the owner's Mac (a live queue, no setup state) refuses in every mode, including `--uninstall`.
- `ai.alan` (or `ai.alan.update`) is loaded and its `deploy/run_alan.sh` (`deploy/update_runner.sh`) path, read from `launchctl print`, is not this checkout. A loaded agent whose path cannot be read, or points at a directory that does not exist, counts as foreign: the guard fails closed.

Both sides of the comparison are canonicalised with `pwd -P`, because `deploy/render_plist.sh` writes a logical `pwd` into the plist and a symlinked checkout path would otherwise refuse the user's own install. The bootstrap entry also accepts the copy destination (`$HOME/<template repo name>`) as "this checkout", so a second `./setup.sh` run is not refused for a service that runs from the copy it created.

The guard prints no path and no identity, only the probe name and a fixed message.

### The profile is written before the first boot

`step_profile` writes `~/.alan/engines/default_profile.yaml` and `state_mark profile` records it in `~/.alan/setup.state` before `step_start` exists to run. `step_start` refuses (`profile-before-boot`, before any `launchctl` write) unless both the state file and the installed profile exist, so the state file always predates `queue.db` and a run that fails after the load resumes on re-run without tripping the existing-install guard. Booting first would create `queue.db` while the service still resolves no installed profile (a copy without one is unconfigured and dispatches nothing, `alan/default_profile.py`) and would break the guard's own premise that the state file predates the queue, so this ordering is structural, not conventional.

The profile step also refuses a template clone: the origin owner must equal the signed-in GitHub user (`gh api user --jq .login`) or `--owner`, case-insensitively. A non-GitHub origin (one that fails the origin grammar below) or a failing `gh` refuses too (fail closed), and no profile is written. This refusal belongs to the profile step: it does not run under `--only start`.

### One origin grammar

Setup, the renderer and the self-updater agree on which origins name a GitHub repository, because the same origin feeds the ownership check, the profile, the bind steps, the launch configuration's slot clone source and the updater's fetch. In `scripts/setup_macos.sh` the grammar is the `ALNUM_CHARS`, `NAME_CHARS`, `OWNER_RE`, `REPO_RE`, `HTTPS_ORIGIN_RE`, `SSH_ORIGIN_RE` and `SCP_ORIGIN_RE` lines and the `parse_origin` function, all byte-identical to `deploy/render_plist.sh`; `alan/self_update/_origin.py` restates it in Python. `load_origin` fills `ORIGIN_OWNER` and `ORIGIN_REPO` from `parse_origin` and leaves both empty for anything the grammar refuses, which is what the profile step's template-clone refusal, the bind steps' `origin is not a GitHub repository, so there is no repository to bind` refusal and `slot_source_matches` all key on. The host must be the lowercase `github.com`; an uppercase host, an explicit port, a trailing `/` or any surrounding whitespace is unusable, an owner is 1 to 39 characters and a repository 1 to 100 after one `.git` is stripped, and neither is `.` or `..` (the full statement is in `deploy.md`, "Launch configuration: template + render"). So there is no origin on which setup proceeds with a repository the renderer would not also use, and none on which the renderer would substitute a different one. `tests/test_deploy_render_origin_parity.py` runs one table through all three implementations and compares the shell lines and function text between the two scripts.

### Setup never signs in to Claude

Setup runs `claude --version` and, in the `claude` step, the read-only status command inside a transient launchd agent (below), and nothing else with `claude`: never `login`, never `setup-token`. It never calls `security`. alan does not handle Claude credentials; the user signs in themselves (`claude` once, in a terminal). No output prints a secret value, a token prefix, a personal email, a tailnet host or a machine name. Bash never reads the control token: setup waits for the file to exist (`[ -s ]`) and prints only its path. `alan.setup_transport` reads it only to write a mode-600 header file in a per-run directory (`mktemp -d`, mode 700) that setup removes on exit, and Bash hands that file to `curl -H @file` for setup's own local API calls. The secrets file (`~/.alan/secrets.env`) is created empty at mode 600 and Bash never reads or prints it. That promise is narrowed, not dropped, by the verifier step: the one writer of its contents is `alan.setup_verifier`, which rewrites the single `ALAN_VERIFIER_LOGIN=` line and no other (see "The verifier bot"), and no output prints any line of the file. The `trigger`, `claude`, `latch` and `report` steps never touch it. The only `eval` is `eval "$(brew shellenv)"`.

### Setup handles four credentials

P1.9a's setup handled no credential; P1.9b changed that rule to one, the GitHub transport token, P1.9c's `verifier` step added a second, the verifier bot's token, and its optional `sonar` step adds two more when SonarCloud is connected, a SonarCloud admin token and a SonarCloud analysis token. Setup handles exactly these four, and only through a mode-600 file or stdin: the transport token is typed (or, with `--use-gh-token`, read from `gh auth token`) inside `alan.setup_transport`, the bot's token is typed inside `alan.setup_verifier`, each is verified there, written to a mode-600 body file in the per-run directory and given to `curl` as its stdin (`--data-binary @-`). The two SonarCloud tokens are typed inside `alan.setup_sonar` and never reach a file at all: each goes to `curl` as a one-line configuration on its standard input (`--config -`), and the analysis token alone also goes to `gh secret set` on its standard input. None is ever a shell variable, an argument or an output, and none is the `gh` session token or the control token. All three helpers read a piped stdin one byte at a time from file descriptor 0 (`setup_transport.read_fd0_line`), never through a buffered reader, so one piped stdin can carry several tokens in the order the steps ask for them (transport; SonarCloud admin, then analysis; verifier bot) and each helper consumes only its own lines; on a terminal each token is read without echo. The steps after `verifier` handle no credential at all: `trigger`, `latch` and `report` use the signed-in `gh` session and alan's local API, and the Claude check reads no credential (below). alan never handles a Claude credential. The static banned-name rule over the two scripts is unchanged (`GH_TOKEN`, `SONAR_TOKEN`, `ANTHROPIC`, `API_KEY`, `PASSWORD` and `VERIFIER_GH` still fail it, which is why the flag's variable is `USE_GH_SESSION`), and so is the token-prefix scan. Tests plant fake token values and check that they appear in no output, no recorded argument and no stub log. The reply body of a failed secret write is never printed, because a validation error can echo its input.

### The closing line (PD5)

A run that includes `report` ends with the report's own verdict (below) and prints no closing line. A run that includes `start` but not `report` ends with: alan is up on `http://127.0.0.1:8585` and serving this checkout's commit, "but it is not ready to dispatch work yet", followed by the pending items named descriptively: SonarCloud (until `latch` is recorded; the line before it says SonarCloud is optional and never blocks dispatch, and skipping it still records `latch` once the CI wait is done), the verifier bot (until `verifier`), the Claude check (until `claude`), the updater agent (until `updater`) and always the final report; until the binding is complete the line also names binding and hardening the GitHub repository (P1.9b) first. The wording matches the readiness text in `alan/engine_preflight.py` (`SETUP_FIX = "./setup.sh"`). Setup never says it is complete except in the verdict of the `report` step; a run that did not include `start` says only which steps it ran.

## State and idempotence

`~/.alan/setup.state` holds one completed step name per line, in completion order. It is written only when its contents change (a temporary file, then `mv`), so a re-run leaves its mtime untouched.

Marks are hints; probes are authoritative. Every selected step always probes and reports `[already]` (mutating steps with nothing to do) or `[ok]` (read-only checks); `[done]` means the step acted. A second run makes no mutating tool call: no `brew`, `launchctl` or `gh api` write and no `curl` POST that stores a secret or protects the branch. The exceptions are re-attempting a repository setting whose read-back could not confirm it and finishing a restart the restart marker records (below). This is what makes re-runs, uninstall-then-reinstall and resume-after-a-failed-load correct: the marks never skip a probe, and the file only proves setup has run here before. A step that recorded a failure is not marked (below).

## The profile writer

`ALAN_ENGINES_ROOT=<dir> python -m alan.default_profile_writer --owner <o> --repo <r>` (`alan/default_profile_writer.py`, in the import layers directly above `alan.default_profile`) builds the profile through `DefaultProfile`, renders sorted YAML, validates the rendered text again, and writes it atomically (`write_text_atomic`). It never touches `resolved()` or the owner file. The CLI accepts no path argument: it resolves the engines directory through `roots.engines_root()`, and the setup script pins `ALAN_ENGINES_ROOT` to `~/.alan/engines` for that one call, so a stray value in the user's shell cannot redirect the write.

The profile: posture `propose`; `authoritative_checks: [gate]`; `authoritative_check_apps: {gate: 15368}` (the integer app id); `enforce_admins: true`; requirements `reviewer_credential` only (`code_quality` is no longer a default requirement, PD28); both sweeps off; `main_ci_workflow: tests.yml`; no deploy workflow; no `code_quality` block (P1.9c writes it after SonarCloud reports).

| Installed profile | Outcome | stdout | Effect |
|---|---|---|---|
| absent | `written` | `written` | writes it, creating the directory |
| same repo (owner and repo, case-insensitive), equal ignoring `code_quality` and an extra `code_quality` requirement entry | `already` | `already` | nothing written |
| same repo, other fields drifted | `kept` | `kept` | nothing written; stderr names the fields |
| different repo | refuse | none | exit 1, file byte-identical |
| unreadable, invalid YAML or fails validation | refuse | none | exit 1, file byte-identical |

`code_quality` is ignored in the equality check because the `sonar` step adds that block to the profile setup wrote: byte equality would clobber it, and repo identity alone would hide real drift. An older profile that also lists `code_quality` among its requirements still reports `already`: the `requirements` comparison drops only that one entry (`_comparable`), so every other difference, a missing `reviewer_credential` included, is still `kept`. `write_code_quality` never adds the requirement; a profile that lists it keeps it and is checked exactly as before (not ready until SonarCloud reports for its project). Invalid owner or repo input exits 1 without creating a file and without echoing the input.

`write_code_quality` (same module, called by `alan.setup_sonar`, with no command-line entry of its own) adds or replaces exactly the `code_quality` block, and only on an installed profile for the same repository (owner and repo, case-insensitive). It re-renders the parsed profile with the new block, validates the rendered text, and writes it atomically, keeping every other field as it was (drifted fields included). A missing profile, one for another repository, and one that is unreadable, invalid or fails validation are refused (`ProfileWriterError`) with the file left byte-identical, and an empty organization or project key is refused before anything is written. A block equal to the wanted one reports `already` and writes nothing. It calls a `before_write` hook after every check has passed and immediately before the write, and never when nothing changes: that is where setup creates the restart marker, so a marker exists whenever the profile may have changed and never when it did not.

## Start sequence

1. Refuse unless the state file and profile exist and the virtualenv is built.
2. Secrets file: created empty at mode 600 when absent; an existing file keeps its contents and is tightened to 600; an already-600 file is not touched. Log directory: created before the load (the plist's log paths need it).
3. Decide: loaded from this checkout, health `ok` and `served_sha == HEAD` means `current`, and setup skips the render and every `launchctl` write. Loaded from this checkout but not serving HEAD (or not answering) means `stale`. Not loaded means `absent`. Loaded from elsewhere is refused.
4. Otherwise snapshot the plist and run `deploy/render_plist.sh` unchanged, then `plutil -lint` and read `ALAN_SLOT_REPO_ROOT` back. The renderer has no default clone source: when the checkout's origin does not satisfy the origin grammar ("One origin grammar", below) it exits 78 and writes nothing, so setup restores (or removes) the plist and refuses at `renderer` (`deploy/render_plist.sh failed; the previous service file was restored`) before any `launchctl` write. The renderer's own one-line message, which names the problem and the fix (`git remote set-url origin https://github.com/<owner>/<repo>.git`), is printed just above that line; neither line echoes the origin, which may carry a credential. The read-back stays as a second layer: a read-back that differs, case-insensitively, from `https://github.com/<owner>/<repo>.git` for the origin setup parsed is still a failure that restores the plist and exits non-zero before any `launchctl` write, so launchd never auto-loads a wrong-repo plist at the next login.
5. `absent`: `launchctl enable`, `bootstrap`, then `kickstart -p` — the same locked-screen-safe sequence as the auto-deploy restart (`docs/architecture/deploy.md`, "Why the restart step kickstarts as well as bootstraps"). `stale`: exactly one `kickstart -k`.
6. Wait for `~/.alan/state/control_token` (bounded attempts); print its path, never its value.
7. Poll `/api/health` (bounded attempts) with `curl` and the venv Python's stdlib `json`. `HealthResponse.ok` is unconditionally true, so `served_sha == HEAD` is the real gate. A mismatch or a timeout exits non-zero and points at `~/.alan/logs/alan.log` and `alan.error.log`; there is no second restart.
8. Only then is `start` marked.

Every wait is bounded by an attempt count (`TOKEN_ATTEMPTS`, `HEALTH_ATTEMPTS`, `UNLOAD_ATTEMPTS`, `CLONE_ATTEMPTS`) with a `sleep` between attempts. `SETUP_TOKEN_ATTEMPTS`, `SETUP_HEALTH_ATTEMPTS` and `SETUP_UNLOAD_ATTEMPTS` override the first three when set to a positive integer (no leading zero); any other value keeps the default. The later waits (`CLAUDE_ATTEMPTS`, `RUN_ATTEMPTS`, `LATCH_ATTEMPTS`) are overridden the same way by `SETUP_CLAUDE_ATTEMPTS`, `SETUP_RUN_ATTEMPTS` and `SETUP_LATCH_ATTEMPTS`. The override exists for the test harness (`tests/_setup_support.py` runs with a budget of 3, so a timeout test does not poll 90 times); exactly one test family runs without it and pins the default budget. The harness writes each stub tool, the stub virtualenv Python and the stub installers' binaries once per test process and symlinks them into every sandbox: on macOS the first exec of a newly written executable costs ~0.7s (a security assessment that serializes across xdist workers), which made the setup tests the largest share of the backend CI job.

## The copy step (bootstrap entry)

The template comes from `--template <owner/repo>`, else from a one-line `setup.template` file next to `setup.sh` (added by the release overlay, not present on the private main), else setup refuses before any mutating call. The copy is private, named after the template repository, owned by `--owner` else the signed-in GitHub user, and cloned to `$HOME/<template repo name>`. `gh repo create --template` returns before GitHub has filled the new repository, so a clone made straight away can have no HEAD: `clone_with_retry` removes such a clone (only if it is a headless clone of the copy) and tries again up to `CLONE_ATTEMPTS`, then exits non-zero. A `gh repo clone` that itself fails is reported as such (separately from a clone with no HEAD), retried the same way, and any partial directory without `.git` that the failed attempt left behind is removed, so the next run does not trip over it. A destination that exists and is not a clone of the copy is refused and never deleted. Setup then `exec`s `scripts/setup_macos.sh --from build` in the new clone (plus `--owner` when given); `--only copy` stops after the copy. When the copy would have the template's own name (the signed-in user, or `--owner`, owns the template), setup refuses at `copy-is-template` before any GitHub write: otherwise `gh repo view` would find the template and setup would bind it as the user's copy.

**A public fork as the copy.** The README's main path is a public fork cloned with `git clone` to `$HOME/alan-factory` before `./setup.sh` runs. The copy step then finds the clone (its origin is `<login>/alan-factory` with a HEAD), reports `[already]` and makes no GitHub write, and the handover runs that same checkout's setup script. A fork differs from a template copy in three ways setup handles: it is public, so the public-only hardening applies and branch protection works on GitHub Free; it starts with Issues off, which `harden` turns on; and it starts with its workflows off, which only the browser can change, so `harden` checks `tests.yml` and fails with the Actions-tab instruction (below). The weekly `pip-audit` workflow stays off in a fork until it is enabled in the Actions tab; setup does not need it.

## The transport credential

The `credential` step stores the token alan uses to clone, fetch and push the repository and to make credentialed forge calls. It goes through the service's own API, `POST /api/secrets`, as the `transport` credential of `<owner>/<repo>:transport`, so the service stays the only writer of its secret store and still emits its `secret_written` event. The engine name is read from `GET /api/engines/<owner>/<repo>` (the engine's `forge.repo`, in the case the service registered it: secret keys are case-exact).

`slot_pool` (slot clones and fetches), `forge_gh` and `repo_scaffold` resolve the record from the secret store on every call, so a stored credential is used from the next call on, with no restart.

**Default: a fine-grained token for this one repository.** Setup prints the permissions (`PERMISSIONS_TEXT` in `alan/setup_transport.py`) before it asks:

```
    Administration: Read and write
    Contents: Read and write
    Pull requests: Read and write
    Issues: Read and write
    Actions: Read and write
    Workflows: Read and write
    Commit statuses: Read-only
    Metadata: Read-only (GitHub adds it by itself)
```

with "Repository access: Only select repositories" and this repository only. Why Administration write: alan applies branch protection and the squash-merge and branch-deletion settings through GitHub's administration API, and no other permission allows that; without it the `protect` step fails.

`python -m alan.setup_transport transport-body` reads the token from stdin without echoing it (`getpass` on a terminal, the first line of piped input otherwise) and refuses empty input or end-of-file, pointing at `--use-gh-token` or a terminal. It makes one authenticated identity request (`gh api -i user`, the token in that one child's environment, never in an argument) and decides the token type from GitHub's answer, never from the token's prefix: a fine-grained token's answer has no `X-OAuth-Scopes` header, a classic token's has one (`github_repos.probe_token_identity`). A token that is not fine-grained, or whose account is not the signed-in `gh` user (Bash reads that login with `gh api user --jq .login` and passes it as `--signed-in-user`), is refused; the refusal names the reason, stores nothing and prints nothing derived from the token (not even the other account's login). Only then does it write the mode-600 body file for `curl`.

Two limits are accepted. Detecting "fine-grained" by a missing header cannot tell a GitHub App user token from a fine-grained one (both lack the header); the `confirm` step's preflight (`author_token_valid`, `repo_clonable`, the protection rows) is the backstop. And setup checks neither that the token is scoped to this repository nor which permissions it holds, because GitHub's identity answer does not say; a token that lacks one fails at `protect` or `confirm` with the reason.

**`--use-gh-token`** stores the signed-in `gh` session's own all-repository token instead: `gh auth token` is piped straight into `alan.setup_transport`'s stdin, so the token is never a shell variable or an argument. That run prints `RISK_TEXT` first: the token reaches every repository the account can reach, it stays in alan's secret store under `~/.alan` (mode 600), and every slot clone, fetch and push uses it. It skips the two checks above, because the token is the session's own.

**A re-run** with a transport credential already stored reports `[already]`, asks for nothing and overwrites nothing; no flag overwrites. To replace a wrong or revoked token: revoke it on GitHub, delete the record with `DELETE /api/secrets` (JSON body `{"engine": "<owner>/<repo>", "purpose": "transport"}`, authenticated by the control token, which you put in a private `-H @file` header file so it stays off the command line), then run `scripts/setup_macos.sh --only credential`.

The step needs curl 7.55 or later (`-H @file`); macOS ships a newer one.

## Bind and harden the repository

Each of `credential`, `harden`, `protect` and `confirm` starts with the same preparation (`bind_prepare`): the Homebrew environment, the virtualenv, an origin that names a GitHub repository, the private control-token header file, and the engine lookup. The repository they act on is the one the service is bound to (the engine's `forge.repo`), so they can never touch a template clone the profile step refused. A service that does not answer, a control token it does not accept (401 or 403), an engine it does not know or one without a forge refuses the run, naming what to do first. Every JSON answer is read by `python -m alan.setup_verdicts` (`alan/setup_verdicts.py`, standard library only), never by Bash.

### Visibility-dependent hardening

`harden` reads the repository (`gh api repos/<owner>/<repo>`, with the signed-in `gh` session, which holds the admin right) and, on a public copy only, its interaction limits, then decides each setting from what it read:

| Setting | Public copy | Private or internal copy |
|---|---|---|
| Secret scanning | required: written (`security_and_analysis`), read back | `[unavailable]`: the repository is not public; never attempted |
| Push protection | required: written, read back | `[unavailable]`: the repository is not public; never attempted |
| Collaborator-only interaction limits | required: `PUT .../interaction-limits` with `collaborators_only` and expiry `six_months`, read back | `[unavailable]`: the repository is not public; never attempted |
| Pull-request creation policy | `pull_request_creation_policy: collaborators_only`, read back | the same: attempted on every copy |
| Issues | `has_issues: true`, read back (a fork starts with Issues off; alan files its own issues) | the same: attempted on every copy |

A setting GitHub does not offer for the repository (its `security_and_analysis` block, or the feature inside it, is absent) is reported `[unavailable]` with "GitHub does not offer <setting> for this repository", not as a failure. Unavailability is decided by reading the visibility and the settings, never by attempting a write. The visibility rule is decision PD26: secret scanning, push protection and collaborator-only interaction limits apply only to a public copy, and a private or internal copy reports them unavailable. Interaction limits expire (`six_months` is the longest GitHub allows), so they are renewed only when they are missing or are not `collaborators_only` (whatever their origin); a run that finds them held writes nothing.

After the settings, `harden` reads `repos/<owner>/<repo>/actions/workflows/tests.yml` and requires its `state` to be `active`. A fork's workflows are not registered until someone clicks "I understand my workflows, go ahead and enable them" in its Actions tab, so the read answers 404; GitHub has no API for that click. Setup then prints the Actions-tab link and the button's label, records `harden` as failed ("GitHub Actions workflows are off") and runs the remaining steps, which ends with the resume command. Without this check the failure would surface later, at `trigger`, as an unreadable run list.

The pull-request creation policy is PD11's case: the write is undocumented, so it is called and read back. When the read-back does not confirm `collaborators_only`, setup prints the browser instruction (the repository's settings page), records the step as failed with the policy named as unverified, runs the remaining steps and ends non-zero with the resume command. That holds on every copy. P3.7 confirms GitHub's behaviour for this API. Every other required setting whose read-back fails fails the step too.

If GitHub never returns `pull_request_creation_policy` in the repository answer, a permanent failure of this one row is the expected result, not a fault of the run: every real run ends non-zero naming the policy as unverified, and each re-run of `harden` re-attempts the write and fails the same way. The steps after `harden` have already run by then. Set the policy in the browser as instructed, then run `scripts/setup_macos.sh --from confirm` to re-check the binding without writing the policy again; the resume command the failed run prints (`--from harden`) re-attempts the write.

### Protect and confirm

`protect` first probes: `POST /api/engines/<owner>/<repo>/preflight` (which reads GitHub and writes only alan's local preflight record) and the `protection-probe` verdict, which says `match` only when all five compatibility-matrix rows pass and no `branch_protection_readable` finding is present. On a match it reports `[already]` and calls nothing that writes. Otherwise it calls `POST .../protect`, which requires `gate` from the GitHub Actions app (id 15368, from the profile) with admins enforced and also sets squash merge and branch deletion. Success is judged from the action's reported outcome (`protection_deferred` empty and `protection_applied_keys` non-empty, the `protect-outcome` verdict), never from the HTTP status: a 200 with deferred settings fails, and each deferred finding is printed as a `[problem]` line. The step refuses, without probing, when no transport credential is stored.

`confirm` runs the preflight again and fails only when a finding this item owns is not `pass`: `author_token_valid`, `repo_clonable`, and the five matrix rows (or the `branch_protection_readable` finding that replaces them). A missing owned finding counts as not confirmed. The reviewer and code-quality findings (`reviewer_distinct_from_author`, `dispatch_readiness`) are printed as `[pending]`: the final report checks them. Any other finding that is not `pass` is printed as `[info]`. `OWNED_CHECKS` in `alan/setup_verdicts.py` is test-asserted to contain every `COMPATIBILITY_MATRIX` key, so a new matrix row cannot silently narrow the gate.

An unprotected private copy (a plan without branch protection for private repositories, such as GitHub Free) is a named failure of `protect` and `confirm`, not an unavailable setting, because alan's merge gate depends on branch protection; alan#1894 decides whether copies default to public or private. The README's main path, a public fork, avoids this.

### Failures that do not stop the run

A failure the run can survive is recorded with `record_failure`: it prints a `[failed]` line, the step is not marked in the state file, later steps still run, and the run ends with exit 1 and the last line `Resume with: scripts/setup_macos.sh --from <first failed step>`, preceded by a summary naming every failed step. A re-run's probes then re-attempt only what is not confirmed. Refusals stay fatal and stop at once (`setup: refused at <step>: <probe>: ...`, exit 1): a refused or unreadable token, a control token the service does not accept, a missing engine or forge, a service that does not answer, an answer that cannot be read where a decision needs it.

## The verifier bot

The verifier bot is a second GitHub account, separate from the user's own, that reviews every pull request (PD2: it is mandatory, there is no single-account mode). The `verifier` step needs the running service and a stored transport credential's engine, like the four binding steps (`bind_prepare`).

**Checks.** `python -m alan.setup_verifier verify` prints the instructions (`INSTRUCTIONS_TEXT`: invite the bot with the Write role, accept the invitation as the bot, create a classic token with the `repo` scope and an expiry), reads the token (see "Setup handles two credentials") and validates it through `alan.github_repos`, in this order, refusing with a fixed message at the first failure:

1. Identity: one `gh api -i user` call with the token in that one child's environment. A rejected token is refused. The token type comes from GitHub's answer, never from the token's prefix: an answer without `X-OAuth-Scopes` is a fine-grained token and is refused, because a fine-grained token cannot reach a repository owned by another account.
2. Distinct from the author: the bot's login must differ, case-insensitively, from the signed-in `gh` user (Bash reads it with `gh api user --jq .login` and passes `--signed-in-user`). An unknown signed-in user refuses (fail closed). The login must also be a plain GitHub login (`[A-Za-z0-9][A-Za-z0-9_-]*`) before it can be written to `secrets.env`.
3. Write access: `gh api repos/<owner>/<repo>` with the bot's own token must show `push`, `maintain` or `admin` permission. When it does not (a private repository the bot cannot see answers 404), `gh api user/repository_invitations` decides the message: a pending invitation to this repository says the invitation has not been accepted yet, anything else says the bot has no write access.

No refusal names either account or echoes any part of the token. The expiry comes from the `github-authentication-token-expiration` header of the identity answer and is stored as the UTC calendar date (`2099-01-01 01:30:00 +0300` is `2098-12-31`); a header setup cannot read refuses. When GitHub reports no expiry setup stores none and prints that no expiry reminder will fire (with an expiry, `alan/engine_preflight.py` warns during the last week and refuses the credential once it has expired).

**Stores.** After the checks Bash creates the restart marker (below), has `python -m alan.setup_verifier login-line` record the login, and then posts the body file to `POST /api/secrets` (the same `bind_post_secret` the transport credential uses) as the `reviewer` credential of `<owner>/<repo>:reviewer`, with `expires_at` when there is one. The login line goes first on purpose: a run interrupted after the record but before the line would leave a stored credential while the service still has no verifier login configured and so recognises no account as the bot (the fail-closed state below); in the other order every interrupted state asks for the token again.

**The login line.** `login-line` (given the path of `~/.alan/secrets.env` as an argument, never its contents) leaves exactly one `ALAN_VERIFIER_LOGIN=` line: the first is replaced in place, later duplicates are dropped, an absent line is appended. Every other byte is kept, line endings included (a newline is added only when the last line had none), and a commented or indented look-alike is not touched. The file is written through a temporary file and a rename with mode 600, and only when it changes; a run that changes nothing leaves its mtime alone. A symbolic link is refused rather than written through. Bash never reads the file; it only hands the path to the helper, and no line of it is printed. `ALAN_VERIFIER_LOGIN` is the existing excepted-config variable (`tests/env_var_classification.py`), so setup adds no `ALAN_*` variable.

**Where the service reads the login.** The service resolves the verifier's login as `ALAN_VERIFIER_LOGIN` first, then the default profile's `verifier_login` field, then nothing (`_pr_branch.verifier_identity()`, `deploy.md`, "Verifier bot token bootstrap"). Setup writes only the environment line: the profile `alan.default_profile_writer` builds carries no `verifier_login`, so on a copy this line is the whole source of the bot's identity. There is no built-in login. A copy whose `verifier` step has not completed therefore has the verifier *not configured*, which is a distinct state and not an empty login: no review counts as a verifier verdict, no review is dismissed as a stale verifier review, no thread is resolved as the verifier's and no account is exempt from the stranger-thread filter as the verifier, including a review or thread with no author ([pr_iteration_pipeline.md](pr_iteration_pipeline.md), D28; [forge_port.md](forge_port.md)). Nothing here needs a setup change for the owner's install, which names its bot in its checked-in profile instead ([engine_config.md](engine_config.md), "Profile schema").

**Failures and re-runs.** A refused token is recorded with `record_failure`: a `[failed]` line with the cause, the step is not marked, the later steps still run, and the run ends with exit 1 and `Resume with: scripts/setup_macos.sh --from verifier`. A missing token (empty input or end of input) fails the same way. A service that does not answer, a control token it does not accept, or a store that rejects the credential refuses the run as in the credential step. A re-run reports `[already]`, asks for nothing, writes nothing and restarts nothing when the reviewer credential is stored and exactly one non-empty login line is recorded; a stored credential without a login line is asked for again. No flag replaces a stored credential: delete the record (`DELETE /api/secrets`, body `{"engine": "<owner>/<repo>", "purpose": "reviewer"}`, authenticated like the transport record) and run `scripts/setup_macos.sh --only verifier`.

## SonarCloud

`sonar` needs the running service and the engine, like the binding steps (`bind_prepare`). SonarCloud is advisory and optional: nothing here makes it a required or authoritative check (`docs/architecture/sonarcloud.md`, PD15), and a copy is ready and dispatches without it (PD28).

**Skipping.** The first prompt is the admin token's, and it says SonarCloud is optional and to press Enter to skip. An empty, whitespace-only or end-of-input answer raises `SonarSkipped` in `alan.setup_sonar`, which exits 3 without printing a reason; `sonar_configure` reads that code with `elif [ $? = 3 ]` and reports `[skipped]`, not `[failed]`. The step then completes and is marked. A skip reads exactly one line of standard input, so the line order for the steps after it is unchanged and no extra question is asked. It records no project in the profile, sets no repository variable and no secret, calls SonarCloud and `gh` for nothing, creates no restart marker and leaves `~/.alan/setup.ci_run` alone. `--no-sonar` skips the same way without prompting at all, and leaves standard input to the later steps. An empty analysis token after a valid admin token is still refused, because by then the user has chosen to connect. To connect later, run `scripts/setup_macos.sh --only sonar`.

**Probe.** `python -m alan.setup_sonar configured` (exit 0 or 1) is true only when the installed profile for this repository has a `code_quality` block, the repository variables `SONAR_ORGANIZATION` and `SONAR_PROJECT_KEY` (`gh variable list`) equal that block's organization and project key, and the analysis-token secret exists (`gh secret list`, names only). Then the step reports `[already]` ("already configured"), asks for nothing, calls SonarCloud never and writes nothing; with `--no-sonar` it says SonarCloud stays connected and changes nothing. Anything else, including a `gh` that cannot answer, runs the configuration below. To replace the analysis token, delete the secret in the repository's Actions settings and run `scripts/setup_macos.sh --only sonar`.

**Tokens.** `python -m alan.setup_sonar configure` prints the instructions (`INSTRUCTIONS_TEXT`: import the repository into SonarCloud once, create an admin token, create an analysis token as a different SonarCloud user) and reads two tokens from standard input, admin first (the admin prompt is the skip point above). A token that holds characters a SonarCloud token never has is refused unechoed, because it goes into a `curl` configuration line. The admin token is used for reads only: to find the project and to read one setting. It is never stored, written to GitHub, put in a record or shown. Every SonarCloud call is `curl --config -` with the token as `user = "<token>:"` on standard input, so the token is in no argument, and the answer's status decides: 401 or 403 means the token was rejected, 404 means absent, anything else that is not 200 refuses with the status and the fixed API path.

**Discovery.** The admin token's own user comes first (`/api/users/current`; a token that belongs to no user is refused, because the analysis token could not be compared with it). Then for each organization the user belongs to (`/api/organizations/search`) setup lists the projects whose name or key contains the repository name (`/api/projects/search`) and asks each for its GitHub binding (`/api/alm_settings/get_binding`); a project counts when its bound repository equals this one, case-insensitively. Exactly one must count. The user never types the organization or the project key: both come from that answer and go unchanged into the three writes. Whether SonarCloud answers these calls the way the stub harness models them is only proven by the P3.7 rehearsal; when it does not, the step fails with a browser instruction and setup stays incomplete rather than reporting a false result.

| Discovery | Writes | Browser instruction | Step |
|---|---|---|---|
| admin token rejected (401, 403) | nothing | create a new admin token as a user who administers the organization | failed, exit 1 |
| no project bound to the repository | nothing | import the repository (Analyze new project) | failed, exit 1 |
| several projects bound | nothing | keep exactly one, delete the rest | failed, exit 1 |
| exactly one project | below | none | done |

**Automatic Analysis.** After discovery setup reads the project's `sonar.autoscan.enabled` setting (`/api/settings/values`). Only the value `false` confirms it is off, and then nothing is printed. Anything else (on, unreported, a refused or failed read) prints a browser instruction (Administration, Analysis Method, turn Automatic Analysis off, or the CI analysis is rejected) and the step carries on: it is not failed on that ground alone, because setup does not change SonarCloud.

**The analysis token.** It is checked before anything is written. A token byte-identical to the admin token is always refused, even with `--accept-sonar-user-token`. Otherwise its own user is read the same way; SonarCloud rejecting it, or a token that belongs to no user, refuses. A token from a different user than the admin token's (compared case-insensitively) is accepted. A token from the admin token's own user is refused with a message naming `--accept-sonar-user-token`, unless the flag is given, in which case `RISK_TEXT` is printed (that token can do everything that user can, every workflow run in the repository can use it, and it stays as a repository secret) and it is accepted.

**Writes, in this order,** all with the same organization and project key: the installed profile's `code_quality` block (`write_code_quality`, above: a profile for another repository, or none, is refused here, before any GitHub write), the repository variable `SONAR_ORGANIZATION`, the repository variable `SONAR_PROJECT_KEY` (`gh variable set <name> --repo <owner>/<repo> --body <value>`; these are not secrets, so the value is an argument), and the repository secret named by `default_profile.SONAR_TOKEN_ENV` (`gh secret set <name> --repo <owner>/<repo>` with the token on standard input, so no token byte is in any argument). `alan.setup_sonar` runs `gh` itself, through the `GH_EXECUTABLE` constant, rather than through the forge adapter (`alan/forge_gh.py`), which has no repository-variable or secret operation; the signed-in `gh` session does the writes, as in the harden step, because alan's transport token has no permission for Actions variables or secrets. These are the names the CI overlay reads (`release/overlay/.github/workflows/tests.yml`: `vars.SONAR_ORGANIZATION`, `vars.SONAR_PROJECT_KEY`, `secrets.SONAR_TOKEN`). A refused write fails the step with a fixed message naming what GitHub refused; a re-run repeats only what the probe finds missing, and the profile write reports `already`.

**A fresh run after connecting.** A successful `configure` deletes `~/.alan/setup.ci_run`. A CI run recorded before SonarCloud was connected ran with the analysis skipped, so `trigger` must dispatch a new one for `latch` to have an analysis to wait for. A skip, an `[already]` and a refused configuration leave the record alone.

**Restart.** The service reads `code_quality` from the profile at process start, so a changed profile needs a restart. Bash passes the marker path (`~/.alan/setup.restart_pending`) to the helper, which creates it through the profile writer's `before_write` hook, before the profile changes. `sonar_reload` then defers: when `verifier` is still selected it does nothing and the verifier's `reload_service` restarts the service once for both changes; otherwise (`--only sonar`) it calls `reload_service` itself. A run that wrote nothing has no marker and restarts nothing.

**Failures and re-runs.** A refused discovery, token or write is recorded with `record_failure`: a `[failed]` line with the cause (the last line the helper printed to standard error), the step not marked, the later steps still run, and the run ends with exit 1 and `Resume with: scripts/setup_macos.sh --from sonar`. A missing analysis token (empty input or end of input) fails the same way; a missing admin token is a skip, not a failure. A service that does not answer, a control token it does not accept, or a missing engine refuses the run as in the binding steps.

## The CI trigger and the latch

`trigger` and `latch` prove, once, that the copy's own CI and SonarCloud work together, and record that proof for alan's readiness check. Both talk to GitHub through the signed-in `gh` session and to the service through the local API; neither reads or writes `secrets.env` or handles a credential.

**`trigger`.** It records the run it started in `~/.alan/setup.ci_run` (one line: the run number, its creation time and its address). A re-run reads that run first (`gh run view`) and decides with the `run-status` verdict:

| Recorded run | Effect |
|---|---|
| in progress (any status other than completed) | `[already]`, no second run |
| completed with its `gate` job successful | `[already]`, no second run |
| completed and red | exactly one fresh run |
| GitHub will not show it | the step fails; no run is started |
| none recorded | one run |

A new run is `gh workflow run tests.yml --repo <owner>/<repo> --ref main`. Because `gh workflow run` names no run, setup lists the `workflow_dispatch` runs of `tests.yml` on `main` first (`gh run list`), remembers the newest number, dispatches once, and polls the list (`RUN_ATTEMPTS`, `SETUP_RUN_ATTEMPTS`) for a run with a higher number; it records that one. A list that cannot be read before the dispatch fails the step without dispatching, so a failed read can never make an old run look new. A dispatch GitHub refuses, or a run that does not appear within the wait, fails the step with a resume line; the second case warns that a re-run may start another run.

**`latch`.** `python -m alan.setup_latch standing` (always exit 0) prints where SonarCloud stands, and the step reads that one word. A latch that already matches the profile's `code_quality` block (`latched`) is `[already]`, with no read of GitHub. Otherwise the step always waits on the recorded run, at most `LATCH_ATTEMPTS` polls (`SETUP_LATCH_ATTEMPTS`) of `LATCH_POLL_SECONDS`, 120 polls of 15 seconds, thirty minutes, for its `gate` job: a red run or one that never finishes leaves the step, and setup, incomplete whether or not SonarCloud is connected. It waits for SonarCloud and writes the latch only when a SonarCloud project is recorded in the profile (`not-latched` on a profile that requires it, `unreported` on one that does not), and only when both hold:

1. the run is completed and its `gate` job succeeded (`run-status`), and
2. SonarCloud's own app has posted, on the run's head commit and after the run was created, either a completed check run (`app.slug` is `sonarqubecloud`, under any name) or a commit status (creator `sonarqubecloud[bot]`, or `sonarcloud[bot]`, in state success, failure or error) whose time is later than the run's creation time (`sonar-evidence`; both times come from GitHub, so the local clock is never compared). A Sonar-named check posted by `github-actions` never counts, and neither does anything created before the trigger, whatever it says.

Then `python -m alan.setup_latch write` (`alan/setup_latch.py`, in the import layers directly above `alan.engine_preflight`) records the installed profile's `code_quality` organization and project key through `engine_preflight.write_code_quality_latch` under the engines root. The service reads the latch on every readiness evaluation, so writing it needs no restart. A latch for another organization or project is not reused. Reading the check runs and statuses is done by Bash through `gh api` and judged by the verdict, so no timestamp is compared in shell.

`standing` is one of `latched`, `not-connected` (the profile does not require `code_quality` and names no project), `unreported` (it names a project, no latch matches, and the profile does not require it), `no-project` (it requires `code_quality` and names no project) and `not-latched` (it requires it, names a project, and no latch matches). A profile that cannot be read counts as requiring it. `state` and its exit codes are unchanged. With no project the run's `gate` is the whole wait and the step reports `[ok]` with "SonarCloud: not connected (optional)". A connected SonarCloud that posts nothing within the wait is a warning, not a failure, on a profile that does not require it: the step prints `[warning] SonarCloud has not reported: ...`, writes no latch and is marked, so the run can still end complete with exit 0. A profile that lists `code_quality` keeps today's failure.

Failures, all recorded with `record_failure` and ending the run with exit 1 and a resume line:

| Outcome | Recorded against | Resume |
|---|---|---|
| the run is red | `trigger` and `latch` | `--from trigger` (starts one fresh run) |
| no run is recorded | `trigger` and `latch` | `--from trigger` |
| the run did not finish within the wait | `latch` | `--from latch` (waits again on the same run) |
| SonarCloud posted nothing that counts within the wait, on a profile that requires `code_quality` | `latch` | `--from latch` |
| the profile requires `code_quality` and has no `code_quality` block | `latch` | `--from latch` |

A red run's `[failed]` line names the first failing job and the run's address. When the repository is private (`gh api repos/<owner>/<repo>`, the same visibility verdict `harden` uses) and a failed job's name contains `sonar`, it also says that SonarCloud's Free plan does not analyse private projects, which may be why. An unreadable visibility never claims it. Whether SonarCloud reports a branch analysis on `main` at all is only proven by the P3.7 rehearsal: when it does not, `latch` times out and nothing is latched: setup stays incomplete on a profile that requires `code_quality` and only warns on one that does not; it never reports a false complete.

## The Claude check

`claude` (and `scripts/setup_macos.sh --check-claude`, which runs this one step alone) checks that Claude Code works as launchd runs it, because that is how the service and the workers run it. The Claude-check rule:

- `alan.setup_claude_check plist` writes one launch file, `ai.alan.claude-check`, and its log into the per-run directory (`BIND_DIR`, the same mode-700 `mktemp -d` directory that holds the private files), and Bash loads it into the GUI domain (`launchctl bootstrap gui/<uid>`); `RunAtLoad` starts it. It runs `claude auth status` once through `/bin/sh` and writes the exit code and the output to the log. The step reads the log through `alan.setup_claude_check verdict` (bounded by `CLAUDE_ATTEMPTS`, `SETUP_CLAUDE_ATTEMPTS`).
- It boots the agent out and deletes its file on pass, on fail and on timeout, inside the single `on_exit` trap that already removes the per-run directory (`claude_cleanup`, then `bind_cleanup`), so an interrupted run cleans up too. A pre-run sweep (`claude_sweep`, whenever the run includes `claude`, and for `--check-claude`) and the `--uninstall` sweep boot out a leftover agent and delete a leftover launch file.
- It prints only `pass` or `fail` and a method label from a fixed set (`METHOD_LABELS` in `alan/setup_claude_check.py`: the sign-in kinds Claude Code reports), or `method not reported`; a label is chosen by a lookup, never copied from the output. It never echoes any `claude` output: an e-mail address, an organization name or a key-like string in the output appears nowhere.
- It passes on exit 0 with a `loggedIn` that is not `false`; anything else fails, and so does a check that does not finish within the wait. A failure tells the user to sign in themselves (`claude` once in a terminal) and to run `scripts/setup_macos.sh --check-claude`; setup never signs in for them, and alan never handles a Claude credential.
- `--check-claude` alone loads one transient agent too, after the existing-install guard and before any state is touched. A failure ends with exit 1 and `Resume with: scripts/setup_macos.sh --check-claude`. It does not mark the state file; the full step does, and the final report reads that mark rather than starting a second agent.

## The final report

`report` runs last. It re-runs the preflight, reads `GET /api/health`, and prints one line per mandatory item, `[ok]` or `[failed]`:

| Item | Passes when | Failure recorded against |
|---|---|---|
| owned P1.9b findings | the preflight's owned findings all pass (`confirm` verdict) | `confirm` |
| SonarCloud latched | `python -m alan.setup_latch standing` says `latched`, or `not-connected` on a profile that does not require it (`[ok]` "SonarCloud: not connected (optional)"); `unreported` on such a profile is a `[warning]` and passes | `latch` for `not-latched`; `sonar` for `no-project`, a profile that requires `code_quality` and names none (it never shows as "not connected") |
| verifier stored and distinct | the reviewer credential and exactly one login line are recorded, and the fresh preflight's `reviewer_distinct_from_author` is `pass` | `verifier` |
| Claude check | the `claude` step is recorded in the state file | `claude` |
| updater loaded | `ai.alan.update` is loaded from this checkout | `updater` |
| restart | `setup.restart_pending` does not exist | `verifier` |
| dispatch readiness | `/api/health` `dispatch_readiness` has no entry whose state is anything but `warning`, for any engine | the step that owns the requirement: `reviewer_credential` `verifier`, `code_quality` `latch`, `default_engine` `profile`, any other `confirm` |

`dispatch_readiness` is read literally (`readiness` verdict): every `not_ready` or `unknown` entry, for any engine, fails and is named with its engine and requirement; a `warning` (such as the reviewer credential's expiry warning) is printed as `[info]` and passes; an entry setup cannot read counts as blocking, and a health answer without the list, or no answer, refuses the report. The restart item exists because the service reads the profile's `code_quality` block and the login at process start: a completed report means the service was restarted after the last change to either.

The report says `setup is complete: ...` and exits 0 only when every item holds and no earlier step of the same run recorded a failure. Otherwise `report` records itself as failed beside the steps its failing items name, the run ends with `setup: incomplete: <steps in SETUP_STEPS order> did not complete` and `Resume with: scripts/setup_macos.sh --from <the earliest failed step in SETUP_STEPS order>`, not in the order the failures were recorded. The report never claims more than the probes show: a marked step is a hint, and only the items above are checked.

## The restart marker

The service reads `ALAN_VERIFIER_LOGIN` from its launcher environment, and the profile's `code_quality` block from the installed profile, at process start, so a changed login line or a changed block needs a restart. `~/.alan/setup.restart_pending` records that a restart is owed: `restart_mark` creates it before the first mutation that needs one (before the login line and the reviewer record; the `sonar` helper creates the same file just before the profile changes), and `reload_service` clears it only after it has done all three of: exactly one `launchctl kickstart -k ai.alan`, a bounded wait for `/api/health` to answer `served_sha == HEAD` (the same wait as the start step, which refuses on a timeout and leaves the marker), and a re-run of the preflight (`POST /api/engines/<owner>/<repo>/preflight`), so the saved preflight carries the reviewer credential's fingerprint. A run that finds the marker restarts even when it changes nothing itself, which is how an interrupted run finishes; a run without the marker never restarts. The marker is a durable file, not shell state, because the restart must survive the run that owed it. The `sonar` step does not restart when `verifier` is still selected, so one restart covers both; later setup work that changes what the service reads at start reuses the same marker and `reload_service`.

## The updater agent

`updater` renders `deploy/render_update_plist.sh` unchanged (it writes `~/Library/LaunchAgents/ai.alan.update.plist` from `$HOME`), checks the result with `plutil -lint`, then runs `launchctl enable` and `launchctl bootstrap gui/<uid>` for `ai.alan.update`; the agent's `RunAtLoad` starts it, so there is no `kickstart`. When `launchctl print` already shows the agent loaded from this checkout (the same canonical-path comparison as the existing-install guard) the step reports `[already]` and makes no render and no `launchctl` write. A renderer, lint or bootstrap failure is recorded with `record_failure` (`[failed]`, step not marked, later steps run, `Resume with: scripts/setup_macos.sh --from updater`). `deploy/update_runner.sh` still gates on the installed profile in pure shell. `--uninstall` boots out and removes the agent as before.

## Uninstall

`--uninstall` (after the existing-install guard) boots out and removes `ai.alan` and `ai.alan.update` when present. An absent agent is reported `[already]`, not an error. It keeps `~/.alan` (including the setup state, so a later setup run is not refused as a foreign install, the stored transport and reviewer credentials under `~/.alan/secrets` and the `ALAN_VERIFIER_LOGIN=` line in `secrets.env`) and the clone, and prints the manual cleanup list: the SonarCloud project, the two SonarCloud tokens to revoke (the analysis token's copy and the two SonarCloud variables stay in the repository's Actions settings until deleted), the GitHub apps, the verifier bot account with its token, and the transport token to revoke on GitHub (alan's copy stays until deleted). A bootout that leaves the agent loaded after `UNLOAD_ATTEMPTS` checks exits non-zero. A launch-agent file whose agent is not loaded is deleted only when its `ProgramArguments` runner path names this checkout (`plist_is_here`); one that names another checkout, or none setup can determine, is refused (`foreign-agent-file`, exit 1) and left in place.

## Recorded values

| Value | Setting |
|---|---|
| Minimum RAM | `MIN_RAM_GB=16` |
| Minimum free disk | `MIN_DISK_GB=20` |
| Copy destination | `$HOME/<template repo name>` |
| Copy visibility | private |
| Transport secret | `<owner>/<repo>:transport` (`secrets_store.TRANSPORT_PURPOSE`) |
| Reviewer secret | `<owner>/<repo>:reviewer` (`secrets_store.REVIEWER_PURPOSE`), `expires_at` the token's UTC expiry date or absent |
| Verifier login | one `ALAN_VERIFIER_LOGIN=` line in `~/.alan/secrets.env` |
| Restart marker | `~/.alan/setup.restart_pending` |
| CI run record | `~/.alan/setup.ci_run` (run number, creation time, address) |
| CI workflow | `tests.yml` on `main`, dispatched with `gh workflow run` |
| SonarCloud latch | `~/.alan/engines/code_quality.latch` (`engine_preflight.write_code_quality_latch`) |
| Claude-check agent | `ai.alan.claude-check`, plist and log in the per-run directory, removed on every exit |
| CI wait | `LATCH_ATTEMPTS=120` polls of `LATCH_POLL_SECONDS=15` (thirty minutes) |
| SonarCloud variables | repository variables `SONAR_ORGANIZATION`, `SONAR_PROJECT_KEY`, equal to the profile's `code_quality` block |
| SonarCloud analysis token | repository secret `SONAR_TOKEN` (`default_profile.SONAR_TOKEN_ENV`), never in alan's secret store |
| Interaction limit | `collaborators_only`, expiry `six_months` |
| Local API timeout | `API_TIMEOUT_SECONDS=60` |
| Copy owner | `--owner`, else the signed-in GitHub user |
| Exit 1 | refused or failed |
| Exit 2 | usage error |
| Exit 3 | Homebrew was just installed and its prefix does not resolve; the message tells the user to add the `brew shellenv` line the Homebrew installer printed to their shell profile, open a new terminal and re-run, or re-run with `HOMEBREW_PREFIX` set |

The Homebrew prefix is never hard-coded: `brew shellenv` and the `node@22` keg path (`<prefix>/opt/node@22/bin`, prepended to PATH, never `brew link`ed) both come from `brew --prefix`; a missing `brew` is located through `HOMEBREW_PREFIX` when set, else installed and looked up again. `build` and `profile` first call `setup_path`, which re-establishes that same environment read-only when `install` did not run in this process, so `--from build` or `--only build` in a fresh terminal still finds the keg-only `npm`. These values are provisional and adjustable in the constants block at the top of `scripts/setup_macos.sh`.

**Unwritable Homebrew prefix.** A dedicated macOS user often sits beside another account that owns Homebrew. `refuse_unwritable_homebrew` fires when `brew` was located, `brew --prefix` names an existing directory, `[ -w ]` on it is false for the current user, and at least one formula of `BREW_FORMULAE` is missing. It is read-only: only `brew --prefix` and `brew list --versions` run, `brew shellenv` does not, and `BREW_PREFIX` is never assigned, so a full run still loads the Homebrew environment exactly once. It is called last in `preflight`, and in `install` between `ensure_homebrew` and `install_formulae`, so `--from install` and `--only install` refuse at `install` before any `brew install`, `brew link` or Homebrew installer runs. The output is one `refused at <step>: homebrew:` line (exit 1) that names the prefix as Homebrew reports it, lists the missing formulae in `BREW_FORMULAE` order and ends with a copy-pasteable `brew install <missing formulae>` to run from the account that owns Homebrew, then re-run setup. When every formula is present, setup proceeds without writing to the prefix and `install` reports each formula and Homebrew as `[already]`. A prefix that does not exist yet is never refused, and a Mac with no Homebrew on PATH makes no Homebrew call in `preflight` and installs Homebrew in `install` as before. Setup never looks up the owning account and never changes the prefix.

## Known limitations

- Setup cannot detect another account's Homebrew that is not on the current user's PATH. The official Homebrew installer then runs and offers to take over that prefix. The security tutorial tells the dedicated user to put that Homebrew on its PATH before running setup.
- Writability is the only signal of the unwritable-prefix refusal. A prefix the user can write, for example a group-writable prefix for an admin user, is treated as the user's own, and a writable prefix whose `Cellar` is not writable still fails inside `brew install`.

- Resume from inside the copy with `scripts/setup_macos.sh --from <step>`. Re-running `./setup.sh` from a clone kept anywhere other than the copy destination clones a second copy, and the existing-install guard then refuses to load a second service beside the first.
- The build probe checks for `.venv/bin/python`, `alan/web/dist/index.html` and the pre-commit hook, not lockfile drift; the updater owns dependency refreshes.
- Port 8585 is probed with `lsof` as the current user, which can miss another user's listener.
- The plist template's PATH names a fixed Homebrew prefix; a non-default prefix is not rewritten (the template stays owner-shared and unchanged).
- The transport token's expiry is not tracked or renewed (a follow-up), and alan's ambient-credential forge calls have not moved onto the transport credential yet (a follow-up issue).

## Accepted risk

The official Homebrew and Claude Code installers are fetched over HTTPS and run unpinned. Pinning them would trade a supply-chain window for a stale installer that stops working; the same trust is already extended to `brew`-installed formulae.

## Precedence

This doc governs copies. A machine that already runs alan never gets a default-engine profile or an updater agent from setup (the guard refuses it).

## Roadmap mapping

P1.9d (PD28) makes SonarCloud optional: the `sonar` prompt and `--no-sonar` skip it, `code_quality` is no longer a default profile requirement, `latch` waits for `gate` alone without a project and `report` shows "not connected (optional)" as passing. P1.9b (bind and harden the user's GitHub repository) is the four steps `credential harden protect confirm`. P1.9c (SonarCloud, the verifier bot, the CI run, the Claude check, the updater agent, the latch and the final report) is the seven steps `sonar verifier trigger claude updater latch report`, delivered in three tasks: `verifier updater`, then `sonar`, then `trigger claude latch report` with the `--check-claude` flag. Decision-log rows: PD2 (the verifier bot is mandatory), PD5 (closing line), PD6, PD9, PD11 (a failed PR-policy read-back fails the run), PD16, PD19 (posture `propose`) and PD26 (the visibility rule).
