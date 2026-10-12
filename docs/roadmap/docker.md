# Release 1.1 Roadmap: alan in Docker on Linux, Windows and macOS

**STATUS: PLANNED, NOT COMMITTED.** This file ships inside release 1 as a description of possible future work. No date is set, and it may never be delivered. If the maintainer takes it up, delivery happens in this public repository: every item below lands here as a pull request that passes `gate`. Until an item is merged, nothing in it is promised.

**One goal.** Anyone on Linux, Windows (Docker Desktop with WSL 2) or macOS can copy this template, start alan with Docker, and keep developing it through alan's own build and explore chats. It is the same full mode as release 1:
- a verifier bot reviews every PR;
- GitHub-hosted CI gates every merge;
- alan updates itself from the copy's own `main`.

Release 1 stays exactly as it is: Apple-Silicon macOS, run by launchd. If this roadmap is taken up, release 1.1 adds Docker beside it, and macOS remains the reference platform.

> **Provenance.**
> - **Analysis:** verified against the release 1 tree on 2026-10-06 by three independent code sweeps (launchd and self-update; setup, credentials and state; the existing Linux evidence). An earlier portability audit reached the same conclusion.
> - **Review:** four devil's-advocate reviews of the first draft (technical accuracy, security and public exposure, delivery and sequencing, a new-user walkthrough on each platform). They raised 48 findings, 10 of them blockers (several overlapping), all incorporated; see "Review record" at the end.
> - **Paths:** cited without line numbers, so they survive edits. Every item re-verifies its key components at HEAD before scoping.

## How this roadmap is delivered

1. **Release 1 ships this file** with status PLANNED.
2. **A dedicated driving install** (D0.3) is a release-1 install of *this repository itself*, run by the maintainer under its own operating-system user or on its own host. Its default engine is this repository, so the code that develops alan here is alan's own published code.
   - It holds no other engine, no private checkout and no credential that reaches any other repository.
   - The maintainer's other installs never bind this repository.
3. **The driving install runs this roadmap at posture `propose`.** Its fleet opens a PR here for each item; the PR passes `gate` and the verifier bot's review, and **the maintainer approves every merge**. The install then self-updates to its own merge, so every merged change is exercised by the very install that wrote it.
4. **The public-write guard (D0.2) is in place before the first fleet push.** Every push, PR, comment and merge is scanned first.
5. **Copies made from the template after an item merges get that item.** Existing copies don't update from this repository; the template has no upstream update channel yet (release 2).
6. **The Docker path is announced only at D13.** Until then the tutorials label it a preview.

## Binding rules for every item

- **macOS behaviour must not change.** An item that touches a shared path proves the launchd path is unchanged, using the existing exact-argument tests or a new characterization test.
- **Gates are never relaxed:** 100% coverage, complexity, per-file size caps, import layers, the drift gate, and no comments. Oversized work splits into more items.
- **No new user-facing `ALAN_*` variables.** Configuration goes through:
  - the default-engine profile;
  - persisted engine records;
  - marker files (the image's container marker, D3);
  - setup's state in the data volume;
  - the compose file.
- **alan never handles Claude credentials.** The user's own image build installs the unmodified Claude Code CLI, and the user signs in to it themselves.
- **No published container image.** Users build the image locally, and nothing this repository publishes contains the Claude Code CLI.
- **Networking:**
  - The compose file publishes alan's port on the host's `127.0.0.1` only.
  - Inside the container, alan listens on the container's interface. So in container mode, every mutating API route requires the control token (D7).
- **This repository is public.** No file, PR, review, comment, branch name or commit (message, trailers, author or committer) carries a host, a home path, a machine name, a private repository's name, a real name or a personal email. Public GitHub logins are exempt. D0.2 enforces this for the fleet.
- **Every DoD clause names who executes it:**
  - `gate`, on Linux;
  - the worker's local gate run in the driving install, recorded in the PR body;
  - the maintainer, in a rehearsal.

  Fleet workers get no `docker` grant, so container DoDs are proven by `gate` jobs or by rehearsals.

## Decision log

| ID | Decision | Date | Source |
|---|---|---|---|
| DD1 | **Docker is the candidate for release 1.1.** Release 1 is not delayed for it, and no date is set. | 2026-10-06 | maintainer |
| DD2 | **This roadmap ships inside release 1 and is delivered in this public repository** by a dedicated driving install, at posture `propose` (the maintainer approves every merge). | 2026-10-06 | maintainer; review |
| DD3 | **Windows is fully supported, through Docker Desktop on WSL 2.**<br>• Every Docker release is rehearsed on a real Windows host before it is announced (D12).<br>• GitHub-hosted Windows runners can't run Linux containers, so CI proves only the Windows checkout (D9). The rest is proven by rehearsal. | 2026-10-06 | maintainer |
| DD4 | **The image holds the toolchain plus a bootstrap. The live checkout lives in a volume.**<br>• At build time the image copies only `setup.template` and `deploy/docker/` from the launcher, into a read-only bootstrap directory. That directory includes `deploy/docker/bootstrap.sh`.<br>• The bootstrap signs in to gh, creates or adopts the user's copy, clones it into the `alan-src` volume, and hands over to the copy's own `scripts/setup_docker.sh`. It runs only until the copy exists, and it stays small so that setup edits don't force image rebuilds.<br>• Self-update then works on the copy exactly as on macOS.<br>• The launcher is a clone of this template. After setup it points at the user's copy, so image rebuilds use the copy's own `Dockerfile`. | 2026-10-06 | proposed (veto: maintainer) |
| DD5 | **The image contents:**<br>• Debian bookworm, pinned by digest;<br>• Python 3.11 with uv, Node 22, git, gh, curl, procps, tmux, tini, supervisord, pre-commit and gitleaks, plus the OS dependencies of Playwright's Chromium;<br>• every tool in `/usr/local/bin` or `/usr/bin`, because Debian's `/etc/profile` resets `PATH` for the `bash -lc` that `alan/web/playwright.config.ts` uses;<br>• the Claude Code CLI at the version pinned in `deploy/docker/toolchain.lock`, installed during the user's own build, with its auto-updater off image-wide;<br>• a non-root user (uid 10001, since the suite fails as root) and `LANG=C.UTF-8`;<br>• built for amd64 and arm64. | 2026-10-06 | proposed (veto: maintainer) |
| DD6 | **Three named volumes, never bind mounts:**<br>• `alan-home`: the whole `$HOME` of uid 10001, holding `~/.claude`, `~/.claude.json`, `~/.config/gh` and `~/.gitconfig`;<br>• `alan-data`: `~/.alan`;<br>• `alan-src`: `~/src`, holding the checkout `~/src/alan` and its sibling operator clones (`alan/operator_repos.py`).<br>Compose fixes the project and volume names, so the install doesn't depend on the launcher folder's name. Bind mounts from macOS or Windows break the web `dist` symlink swap, `flock`, the tmux socket, 0600 secret files and same-tick timestamp detection. | 2026-10-06 | proposed (veto: maintainer) |
| DD7 | **PID 1 is tini, which runs supervisord.**<br>• Workers are spawned in their own session, so they survive a restart of the service alone.<br>• A `docker restart`, host reboot or Docker Desktop restart still ends them; the service then falls back to resume, as documented in `docs/architecture/worker_records.md`.<br>• The updater picks its backend from a CLI argument, since it may not read the environment. In-service code picks it from the image's container marker. | 2026-10-06 | proposed (veto: maintainer) |
| DD8 | **Claude sign-in in a container is file-based:** `docker compose exec -it alan claude`, then `/login`, which persists in `alan-home`; or an `apiKeyHelper`.<br>• An API key in an environment variable is a caveat: the operator terminal and driver loops start with a stripped environment, so it never reaches them. | 2026-10-06 | proposed (veto: maintainer) |
| DD9 | **macOS stays the reference platform.**<br>• The launchd service, the launchd updater and `scripts/setup_macos.sh` keep working byte for byte.<br>• Docker on a Mac is supported, but `./setup.sh` remains the recommended Mac path. The two are alternatives, so uninstall one before switching.<br>• Release 1's "Apple Silicon only" applies to the launchd path; the Docker path supports amd64 and arm64 hosts. | 2026-10-06 | maintainer |
| DD10 | **Fleet PR branches are public the moment they are pushed.**<br>• D0.2's pre-push scan is their review.<br>• PR branches may be force-pushed with lease; `main` never is.<br>• Changes to this file's status ride fleet PRs. The driver never pushes here directly. | 2026-10-06 | review |

**Open questions:**
- **OQ1. Who provides the Windows host for D12, and how is it kept available for every Docker release?** Apple-Silicon Macs can't host Docker Desktop on WSL 2, so it must be a Windows PC or a cloud VM with nested virtualisation.
- **OQ2. Is the supervisor supervisord or s6-overlay?** DD7 proposes supervisord, because `supervisorctl` maps one-to-one onto the `launchctl` verbs the updater uses. D2 may overturn this with evidence.
- **OQ3. Does D0.2 ship in release 1, or does the maintainer land it here by hand as the first PR?** It must exist before the first fleet push (rule 4 above). Shipping it in release 1 is recommended.

## Where alan is tied to macOS today

**Already portable (proven, not assumed):**
- **The full gate suite passed in a Linux container.** Release 1's snapshot dry run ran it on Ubuntu 24.04, arm64, as a non-root user:
  - the whole backend suite at 100% branch coverage;
  - ruff, mypy, pyright, lizard, xenon, import-linter, bandit and vulture;
  - the frontend typecheck, lint and unit suites.
  - Four Linux-only defects it found (timestamp granularity and one PTY test) are fixed.
- **Release 1's `gate` runs on `ubuntu-latest`,** including the browser end-to-end suite. Only the two font-sensitive layout audits run on hosted macOS. A copy's CI never depends on the user's own operating system.
- **The backend has no platform branches:** no `sys.platform` checks, no Keychain calls, and no `~/Library` or Homebrew paths.
- **Already portable as is:**
  - the bind host (`alan/bind.py`, `ALAN_HOST`). A non-loopback host is refused at startup unconditionally today. The container recipe adds a non-loopback opt-in together with the planned container-mode authentication item (D7), not before. It is never a way to expose alan on a LAN;
  - the Host-header allowlist (`alan/server.py`), which checks the hostname only, so a Docker port mapping passes;
  - the `ps -o lstart` worker identity (procps prints the same format);
  - worker priority (`os.setpriority`) and worker detachment (`start_new_session`);
  - SQLite (rollback journal, no WAL);
  - the secret and control-token stores (0600 files);
  - `deploy/run_alan.sh`;
  - the web slot swap (`scripts/swap_web_dist.py`, a relative symlink renamed in place).
- **Most of setup is reusable.**
  - `scripts/setup_macos.sh` is itself the step engine: `setup.sh` sources it and overrides `SETUP_STEPS` and its hooks.
  - Its GitHub binding, hardening, branch protection, verifier, SonarCloud, trigger, latch and report steps are portable, apart from the service restarts they call.
  - Almost all of the `alan/setup_*.py` helpers are portable.

**The real couplings:**

| Coupling | Where | Size | Item |
|---|---|---|---|
| **Self-update calls `launchctl` from three places with no shared seam.** Without `launchctl`, the classify step fails, so every apply records `service_probe_failed`. | `alan/self_update/__init__.py`, `alan/self_update/_ops.py`, `alan/teams/_deploy_restart_probe.py` | M | D1, D2 |
| **The soak's crash-loop detection reads launchd's `runs` counter.** This is the only launchd *logic*; the rest is plumbing. | `alan/self_update/_soak.py` | M | D2 |
| **Self-update can't apply image-level changes.** A change to the image's own inputs would restart the old image and report success. | `alan/deploy_restart.py` | S–M | D2 |
| **The launchd service definition.** | `deploy/ai.alan.plist`, `deploy/ai.alan.update.plist`, `deploy/render_plist.sh`, `deploy/render_update_plist.sh`, `deploy/update_runner.sh` | S | D3 |
| **Worker survival needs PID 1 to outlive the service.** Two Linux behaviours are untested: start-time stability across VM clock steps, and command-line stability for Node processes. | `alan/worker_records.py` | S–M | D3, D5 |
| **Credential storage differs.** On macOS the Claude and gh tokens sit in the Keychain; on Linux they are files. The deny rules name the Linux paths but have been verified only on macOS. | `alan/claude_cli_grants.py`, `docs/architecture/worker_credential_boundary.md` | S–M | D6 |
| **Mutating API routes assume a loopback-only listener.** Not every one requires the control token. | `alan/routes/`, `alan/routes/_deps.py` (`require_control_token`) | M | D7 |
| **The macOS-only part of setup:** the arm64 and GUI-session checks, Homebrew, the `sysctl` and `lsof` probes, the plist render and load, launchd restarts, the launchd Claude check, the updater agent and uninstall. | `scripts/setup_macos.sh`, `alan/setup_claude_check.py` | M | D8 |
| **Commands and paths shown to the user assume a host install.**<br>`SETUP_FIX` and `RESUME_COMMAND` are examples. So is the control-token location the UI shows. | `alan/engine_preflight.py`, `alan/setup_sonar.py`, `alan/web/src/components/ControlTokenSection.tsx` | S | D4 |
| **Small fixes:**<br>• there is no `.gitattributes`;<br>• CPU count and load ignore cgroup limits;<br>• the iOS preset is offered on Linux;<br>• the stat-cache keys lack the inode;<br>• some hint texts assume macOS. | `alan/fleet_load.py`, `alan/engine_presets.py`, `alan/engine_stack_detect.py`, `alan/store.py`, `alan/worker_liveness.py` | S each | D3, D4 |

## Phase overview

| ID | Item | Lane | Size | Status |
|---|---|---|---|---|
| D0.1 | Charter: this file ships in release 1 | maintainer | S | in-flight |
| D0.2 | Public-write guard | maintainer (OQ3) | M | pending |
| D0.3 | Dedicated driving install and engine binding | maintainer | S | pending |
| D0.4 | Driver skill | maintainer | S | pending |
| D1 | Service-manager seam with the launchd backend | fleet | M | pending |
| D2 | Supervisor backend, neutral soak, supervised updater, image-rebuild block | fleet | M | pending |
| D3 | Container image, compose file, entrypoint and build job | fleet | L (may split) | pending |
| D4 | Portability fixes and container-aware texts | fleet | M | pending |
| D5 | Worker identity on Linux | fleet | S–M | pending |
| D6 | Linux credential boundary | fleet | S–M | pending |
| D7 | Container-mode API authentication | fleet | M | pending |
| D8 | Setup inside the container | fleet | M | pending |
| D9 | Docker CI: boot, stubbed workspace, self-update round trip, Windows checkout | fleet | M | pending |
| D10 | Docker documentation and tutorials (preview) | fleet | S–M | pending |
| D11 | Linux rehearsal | maintainer | M | pending |
| D12 | Windows rehearsal | maintainer | M | pending |
| D13 | Release 1.1 | maintainer | S | pending |

**Status vocabulary:** `pending`, `in-flight`, `done`, `done (partial)`, `deferred`.

**Lanes:**
- **fleet:** a workspace in the driving install, dispatched through the build-chat front door. Its PR lands here.
- **maintainer:** only the maintainer can do it. The driver writes a runbook as soon as the item's dependencies are met.

### Execution order

**Main order:**

`D0.1 → D0.2 → D0.3 → D0.4 → D1 → D2 → D3 → D8 → D9 → D10 → D11 → D12 → D13`

**Beside items** fill a free fleet seat whenever their dependencies are met and no hot file blocks them:
- D4, D5 (before D9);
- D6, D7 (before D11).

**Fleet lane:** at most two working workspaces.

### Hot files

`pyproject.toml`, `tests/size_baseline.py` and `tests/conftest.py` block outright: a second live workspace may not name them. Every other file below blocks only when two live workspaces name the same one:
- `CLAUDE.md`
- `alan/self_update/` (the whole package)
- `deploy/` (the whole directory)
- `scripts/setup_macos.sh`
- `.github/workflows/tests.yml`
- `alan/drift_gate.py`
- `alan/claude_cli_grants.py`
- `alan/web/src/api/schema.ts`

---

## Phase D0: before the first fleet push

### D0.1 Charter

- **Scope:** this file, shipped in release 1 with status PLANNED, plus the README's one-line pointer to it.
- **DoD:** both are in the release 1 tree and pass the release's owner-pattern scan.

### D0.2 Public-write guard

- **Why:** the fleet writes into a public repository. A pushed branch, a commit's author line or a PR body is public within minutes, and refusing a merge afterwards publishes nothing back. Worker transcripts routinely contain local paths.
- **Scope:**
  - **Whether a repository is public** is read from the forge API, never set by hand. A failed read counts as public.
  - **Before each `git push`** in the host push paths (`alan/teams/_pr_push.py`, `alan/teams/_pr_reconcile.py`), every commit being pushed is scanned. For each commit that covers:
    - the message and trailers;
    - the full content of every added or modified file, with binary files scanned as bytes;
    - the author's and committer's name and email;
    - the branch name.

    A pattern hit, or an email that isn't a `users.noreply.github.com` address, refuses the push.
  - **Commit identity:** on a public engine, worker commits use the account's noreply identity, set in the slot's local git config.
  - **Before each forge write** (PR title and body, review, comment, issue) and **before each merge** (the PR's full diff, under the same content rule), the same scan runs.
  - **The pattern list** is per install and covers hosts, home paths, machine names, real names, personal emails, and private repository names and references.
    - It lives outside any deploy-managed checkout.
    - On a public engine, a missing or empty list refuses dispatch. A fresh copy has no list and no public engine, so the guard stays inert there.
  - **Strangers' content never reaches a prompt on a public engine.** A thread, review or comment whose author lacks write permission is excluded, including from `implementation_fix`'s thread block (`alan/teams/_pr_threads.py`, `alan/teams/implementation_fix.py`), and a stranger's thread of any length is settled (`alan/teams/_pr_feedback.py`).
  - **A finding names the pattern label and the path,** never the matched text.
- **DoD** (tests, `gate`):
  - A planted hit in each of these refuses the action and hides the matched text: a commit message, a trailer, an added line, a modified tracked binary file, an author email, a branch name, a PR body, a review and a merge diff.
  - A secret added in one commit and removed in a later one is refused at the first push.
  - A stranger's thread never reaches a prompt.
  - A public engine with no pattern list refuses dispatch.
  - A clean push passes.
- **Depends on:** D0.1. **Delivery:** see OQ3.

### D0.3 Dedicated driving install and engine binding

- **Scope (maintainer runbook):**
  - **The install:** `./setup.sh` from a clone of this repository, run by its owner under a dedicated OS user or on a dedicated host.
    - Its global git identity is the account's noreply address.
    - It holds this repository's engine only.
    - First verify at HEAD that setup accepts this repository itself when its owner runs it (the ownership guard compares `origin`'s owner with the signed-in user). If it doesn't, an item adds that path first.
  - **The engine record:**
    - posture `propose`;
    - `authoritative_checks: [gate]`, with `gate` pinned to the GitHub Actions app and admins enforced;
    - `ci.main_ci_workflow: tests.yml`, and no deploy workflow;
    - sweeps off;
    - the unblocker's issue filing and the flake ledger's issue proposals off, because this repository has Issues turned off. Verify at HEAD that switches exist; if not, D0.2 adds them.
  - **Repository settings:**
    - the verifier bot as a collaborator with push access;
    - a transport token scoped to this repository;
    - interaction limits set to collaborators only, renewed whenever fewer than 30 days remain;
    - on the account, "Keep my email addresses private" and "Block command line pushes that expose my email" switched on. These decide the author of a squash merge, so preflight reads both back.
- **DoD:**
  - Preflight passes.
  - A no-op fleet PR passes D0.2's guard and `gate`, and merges after the maintainer's approval. That PR adds a one-line pointer to this file's binding rules in `CLAUDE.md`.
- **Depends on:** D0.2.

### D0.4 Driver skill

- **Scope:** a driver loop for this file, in the shape of release 1's driver.
  - It tracks blockers as issues in the maintainer's own tracker, never here (Issues are off).
  - Its status changes to this file ride fleet PRs (DD10).
- **DoD:** one dry tick scopes D1 without dispatching it.
- **Depends on:** D0.3.

## Phase D1–D10: the fleet items

### D1 Service-manager seam with the launchd backend

- **Why:** `launchctl` is reached from three places that share nothing:
  - the updater's own funnel in `alan/self_update/__init__.py`;
  - a second funnel, with the status, teardown, bootstrap, kickstart and restart helpers, in `alan/self_update/_ops.py`;
  - `alan/teams/_deploy_restart_probe.py`.
- **Scope:**
  - **The seam module:**
    - stdlib-only, placed below `self_update` in the import-linter layers;
    - exposes `status` (loaded, pid, restart count), `stop`, `start`, `restart` and `render`.
  - **The launchd backend** is today's calls, moved, not changed. All three call sites use it.
  - **The updater's backend argument** is added to `_cli._VALUE_FLAGS`, `_extract._ALLOWED_ARGV_FLAGS` and `_run._apply_argv`. `--plist-path` keeps working and implies launchd.
  - **In-service code** (`_deploy_restart_probe`) picks the backend from the image's container marker; with no marker it uses launchd.
  - `tests/test_self_update_structure.py`'s `EXPECTED_CLOSURE` learns the module.
- **Key components:**
  - `alan/self_update/`: `__init__.py`, `_ops.py`, `_soak.py`, `_rollback.py`, `_cli.py`, `_extract.py`, `_run.py`, `_leftover.py`;
  - `alan/teams/_deploy_restart_probe.py`;
  - `pyproject.toml` (layers);
  - `tests/test_self_update_structure.py`;
  - `docs/architecture/self_update.md`.
- **DoD** (`gate`):
  - Every existing test that asserts a `launchctl` argument list passes with its expected values unedited.
  - A test proves no module under `alan/` outside the launchd backend contains a `launchctl` literal.
  - The structure test still forbids environment reads in the updater.
- **Depends on:** D0.4.

### D2 Supervisor backend, neutral soak, supervised updater, image-rebuild block

- **Scope:**
  - **A `supervisor` backend** drives `supervisorctl`.
    - It covers status (state, pid, uptime), stop, start and restart.
    - Its `render` is `supervisorctl reread && supervisorctl update` when the target changes `deploy/docker/supervisord.conf`.
  - **The soak's crash-loop signal becomes backend-neutral.**
    - It counts the restarts the seam reports: launchd's `runs`, or the supervisor's observed pid changes.
    - "Missing on two polls" means not running on two polls.
  - **The updater runs in the container** through `deploy/docker/update_loop.sh`.
    - supervisord has no timer, so the script loops: it runs once at start, then every 900 s.
    - The structure test's single-runner pin becomes a two-runner allowlist.
  - **The image-rebuild block.**
    - The image records a hash of its inputs: the `Dockerfile`, `compose.yaml`, and every file the `Dockerfile` copies into the image. Those are `setup.template` and `deploy/docker/`, which includes `toolchain.lock` and `bootstrap.sh`.
    - A test derives the `COPY` sources from the `Dockerfile` and fails when the hashed set differs from them.
    - The updater applies a target only when the target's inputs hash to the same value.
    - Otherwise it blocks with `image_rebuild_required` and a health finding naming the rebuild commands (`git pull`, then `docker compose build` and `docker compose up -d`, in the launcher). The next check after a rebuild applies the target.
    - On macOS, `alan/deploy_restart.py` classifies these paths as safe.
- **DoD** (`gate`):
  - Supervisor-backend tests mirror each launchd verdict: a restart-free apply, a restart apply, rollback at each failure point, a crash loop caught by two restarts within the soak window, and a service missing on two polls.
  - A change to any hashed input blocks with `image_rebuild_required`, and the hashed set equals the `Dockerfile`'s `COPY` sources (both tested).
  - The launchd tests are unchanged.
- **Depends on:** D1.

### D3 Container image, compose file, entrypoint and build job

- **Scope:**
  - **`Dockerfile`** per DD4 and DD5:
    - the base image pinned by digest;
    - no `curl … | sh`;
    - every download checked against a checksum, or taken from a signed apt repository with a pinned key fingerprint;
    - the Claude Code CLI version, the uv version, the Node major and the Playwright version taken from `deploy/docker/toolchain.lock`;
    - the CLI's auto-updater disabled through its managed settings (verified against the pinned CLI);
    - every volume mount point pre-created and owned by uid 10001;
    - the container marker file;
    - a `HEALTHCHECK` on `/api/health`.
  - **`compose.yaml`:**
    - Identity and access: top-level `name: alan`, explicitly named volumes (DD6), `127.0.0.1:8585:8585`, and a dedicated network with no other services.
    - Reliability: `restart: unless-stopped`, `shm_size: 1gb`, a `nofile` limit of at least 4096 (because `deploy/run_alan.sh` raises the soft limit), `pids_limit`, and a stop grace period long enough for a clean shutdown.
    - Hardening: `user: "10001:10001"`, `cap_drop: [ALL]` and `no-new-privileges`. It never sets `privileged`, never mounts the Docker socket, and never sets `seccomp=unconfined` or adds `SYS_ADMIN`. Chromium's sandbox uses a working seccomp configuration that D3 records.
    - `ALAN_DISPATCHER_CONCURRENCY`, an existing excepted-config variable, set to a VM-safe value (provisional until D11 measures it).
  - **`deploy/docker/entrypoint.sh`** has the subcommands `serve` (the default), `setup` and `shell`.
    - **On every start:** git's `safe.directory`, and `gh auth setup-git` whenever `gh auth status` succeeds.
      - Before the first sign-in, that call is skipped, never fatal. The bootstrap runs it right after the device-flow sign-in.
    - **`serve`:**
      - holds an exclusive `flock` on `~/.alan/serve.lock` for its lifetime;
      - with no copy recorded in `~/.alan/setup.copy`, runs supervisord with the `alan` program stopped and logs the setup command;
      - otherwise, when `HEAD` differs from the last built commit, runs `uv sync --locked`, `npm ci`, the Playwright install (`npx playwright install chromium` into `PLAYWRIGHT_BROWSERS_PATH` in `alan-data`, no `--with-deps`) and the web build into a slot; then starts supervisord.
    - **`setup` and `serve` refuse in a second container** while the lock is held, naming `docker compose exec`.
  - **`deploy/docker/supervisord.conf`:**
    - The `alan` program runs `deploy/run_alan.sh` unchanged, with the environment `deploy/ai.alan.plist` sets today plus `ALAN_HOST=0.0.0.0`. A non-loopback bind is refused unconditionally today, so the recipe adds a non-loopback opt-in together with the planned container-mode authentication item (D7), not before, and never as a way to expose alan on a LAN. That environment is `PATH`, `VIRTUAL_ENV`, the slot-pool root, the slot repository URL (derived from `origin`) and the auto-archive days.
    - Its stdout and stderr go to `~/.alan/logs/alan.log` and `alan.error.log`; the maintenance role reads the latter. Its stderr is also copied to the container's output for `docker compose logs`.
    - It also runs D2's updater program.
  - **`.gitattributes`:** `eol=lf` for `*.sh`, `Dockerfile`, `compose.yaml` and `deploy/docker/*`.
  - **A `docker-build` job in `.github/workflows/tests.yml`:**
    - builds the image on `ubuntu-latest` (amd64) and `ubuntu-24.04-arm` (arm64);
    - runs on `pull_request` and `workflow_dispatch`, and is skipped on `push`, so a registry or mirror outage never blocks self-update, which reads the push-event `gate`;
    - `gate` needs it on pull requests and accepts its skip on push events only;
    - pinned to `permissions: contents: read`, with no secrets and no `pull_request_target`.
  - **Gate wiring:** the new files join `alan/drift_gate.py`'s load-bearing list, the `tests.yml` drift-gate filter, the drift-gate list in `CLAUDE.md`, and the scanned-assets test. A first section of `docs/architecture/docker.md` lands with them.
- **Key components:**
  - `deploy/run_alan.sh`;
  - `deploy/ai.alan.plist` (read only);
  - `deploy/render_plist.sh` (the `origin` normalisation to reuse);
  - `alan/drift_gate.py`, `tests/test_drift_gate.py`, `.github/workflows/tests.yml`, `CLAUDE.md`;
  - `tests/test_deploy_asset_home_literals.py`;
  - `tests/_setup_stubs.py` (the stub-binary pattern);
  - `alan/operator_repos.py`.
- **DoD** (`gate`):
  - **Entrypoint tests** (stub `git`, `uv`, `npm`, `gh` and `supervisord`) cover:
    - a first `serve` with no copy;
    - a serve that builds;
    - a re-serve that skips the build;
    - the second-container refusal;
    - `setup-git` on every signed-in start;
    - a first serve with gh not yet signed in, which still starts supervisord and exits zero.
  - **A compose test** pins:
    - the port binding, the project and volume names, and the absence of any bind mount or Docker socket mount (in both syntaxes);
    - `user`, `cap_drop`, `no-new-privileges`, `restart`, `shm_size` and `nofile`.
  - **A Dockerfile test** pins the digest, the absence of a pipe-to-shell, and that every version comes from the lock.
  - **A test proves `operator_root()` resolves inside a declared volume.**
  - **The `docker-build` job** passes on both architectures, and the workflow test pins its triggers and permissions.
- **Depends on:** D2. May split into an image-and-compose PR and an entrypoint PR.

### D4 Portability fixes and container-aware texts

- **Scope:**
  - **`alan/fleet_load.py`:** under a cgroup v2 quota, both the core count and the load come from the cgroup (`cpu.max`, and `cpu.pressure` or the `cpu.stat` usage delta); `getloadavg` is used only without a quota. No new environment variable.
  - **The iOS preset and iOS stack detection are offered only on macOS:** `alan/engine_presets.py`, `alan/engine_stack_detect.py` and `alan/routes/engine_presets.py`.
  - **`alan/store.py`:** `stat_cache_hit`, `stat_cache_put` and `stat_signature` gain the inode (the key already uses `st_mtime_ns`). So do the directory fingerprints in `alan/workspace_index.py` and `alan/handover.py`.
  - **Container-aware texts:** when the container marker exists, these name `docker compose exec …` commands:
    - `SETUP_FIX` (`alan/engine_preflight.py`) and `RESUME_COMMAND` (`alan/setup_sonar.py`);
    - setup's resume, token and log lines;
    - `ControlTokenSection.tsx`;
    - the Xcode and native-capture hints.

    The UI learns container mode from an API field, so `alan/web/src/api/schema.ts` is regenerated.
- **DoD** (`gate`): one test per fix, one test per text in each mode, and macOS behaviour unchanged.
- **Depends on:** D3 (the marker).

### D5 Worker identity on Linux

- **Why:** a restarted service re-attaches each worker by pid, `ps` start time and command line (`alan/worker_records.py`). Neither the clock-step case nor the command-line case is tested.
- **Scope:**
  - Extend `tests/test_detached_worker_survival.py`, which already runs on `ubuntu-latest`, with a clock-step case and a command-line-rewrite case.
  - If either is real, harden `probe()` without changing macOS behaviour, for example by comparing `/proc/<pid>/stat`'s start time in ticks on Linux.
- **DoD** (`gate`, plus the macOS local gate run recorded in the PR): a simulated clock step never turns a live worker dead.
- **Depends on:** D0.4.

### D6 Linux credential boundary

- **Why:** on macOS the Claude and gh tokens sit in the Keychain; in the container they are files in `alan-home`. The deny rules in `alan/claude_cli_grants.py` name the Linux paths, but they have been verified only on macOS.
- **Scope:**
  - **Canary coverage** for the Linux paths, in `tests/test_worker_credential_canary.py`. The canary files are `~/.claude/.credentials.json`, `~/.claude.json`, `~/.config/gh/hosts.yml` and the file an `apiKeyHelper` reads, plus process environments. Add deny rules where the coverage finds a gap.
  - **In container mode, the service never keeps secrets in its exec-time environment,** or it marks itself non-dumpable.
  - **After setup, gh's login in `alan-home` holds only the repository-scoped token.** Steps that need account scope use a throwaway gh config.
  - **The security tutorial gains a Docker section:**
    - the container isolates the host from alan, not alan's credentials from its own workers;
    - anyone who can run Docker on the host can read every volume;
    - the `docker` group is part of alan's trust boundary;
    - never expose the Docker daemon over unauthenticated TCP.
- **DoD:**
  - (`gate`) Every non-implementation role is refused each canary through `Read` and through the read-only Bash staples.
  - (`gate`) For the implementation roles, script execution stays the named remaining risk it is on macOS (`docs/architecture/worker_credential_boundary.md`), and the README's safety notice names it for Docker too.
  - (maintainer, D11) The real-CLI canary in a Linux container, recording `claude --version`.
- **Depends on:** D3. **Gates D11.**

### D7 Container-mode API authentication

- **Why:** inside the container alan listens on the container's interface, which local users and other containers can reach without going through the host's `127.0.0.1` publish. The Host-header allowlist stops DNS rebinding, but a non-browser client can send any Host header.
- **Scope:**
  - When the container marker exists, every mutating route requires the control token (`require_control_token`, `alan/routes/_deps.py`).
  - The UI's control-token flow covers every such call.
  - Setup prints how to read the token (`docker compose exec`).
- **DoD** (`gate`):
  - A test sends each mutating route a request with `Host: localhost` and no token in container mode, and gets 401.
  - With no marker, every route behaves as today.
  - The frontend and e2e suites pass in both modes.
- **Depends on:** D3.

### D8 Setup inside the container

- **Scope:**
  - **The quickstart:** `docker compose build`, then `docker compose up -d`, then `docker compose exec -it alan setup`. These are the same commands on every OS.
    - Setup runs inside the serving container, so it starts and restarts the `alan` program through that container's supervisord.
    - No second container ever serves.
  - **First run uses `deploy/docker/bootstrap.sh`,** the only setup code in the image. `docker compose exec -it alan setup` runs it while no copy is recorded, and runs the copy's `scripts/setup_docker.sh` (with `--from` and `--only`) afterwards. In order, the bootstrap:
    1. signs in to gh with the device flow, then runs `gh auth setup-git`;
    2. creates the copy with `gh repo create --template`, as `./setup.sh` does, or adopts one made with "Use this template" (`--copy <owner>/<repo>`);
    3. clones it into `~/src/alan` and records `~/.alan/setup.copy`;
    4. hands over to the copy's own `scripts/setup_docker.sh --from preflight`.

    A preflight refusal after the hand-over leaves the copy and its clone in place, and a re-run resumes.
  - **`scripts/setup_docker.sh`** sources `scripts/setup_macos.sh` (the step engine). It overrides:
    - these functions: `agent_loaded`, `guard_existing_install`, `detect_service_state`, `render_service`, `load_service`, `restart_service`, `reload_service`;
    - these steps:
      - **preflight:** inside a container; memory is the cgroup's `memory.max`, else `MemTotal`; then CPUs, port and disk, against the minimums D11 measures;
      - **install:** checks tool versions only;
      - **build:** checks the entrypoint's build marker and runs `pre-commit install`;
      - **start;**
      - **Claude check:** `claude auth status` as a transient supervisord program with the service's environment, reusing `alan/setup_claude_check.py`'s parsing;
      - **updater;**
      - **uninstall:** prints the `docker compose down` and volume commands, plus the manual list.
  - **Unchanged steps:** every GitHub, verifier, SonarCloud, trigger, latch and report step runs unchanged, through the overridden restart functions.
  - **The report** prints:
    - the Claude sign-in command (DD8; setup never signs in to Claude);
    - the control-token command;
    - the launcher's `git remote set-url` line pointing it at the user's copy.
- **DoD** (`gate`), as stub tests:
  - a `setup` from the image alone, on empty volumes with gh not yet signed in, ends with the copy in `alan-src`, `setup.copy` written and the hand-over made;
  - the bootstrap is the only setup code the image copies;
  - a full re-run reports "already" for every step;
  - each preflight refusal;
  - uninstall.
- **Depends on:** D3.

### D9 Docker CI: boot, stubbed workspace, self-update round trip, Windows checkout

- **Scope:** in `.github/workflows/tests.yml`, under the same triggers and permissions as `docker-build`:
  - **`docker-smoke`** on `ubuntu-latest` and `ubuntu-24.04-arm`:
    1. Boot the image with the stub `claude` and `gh` from `alan/web/e2e/fixtures/`.
    2. Wait for `/api/health` to report `served_sha` equal to HEAD.
    3. Run one stubbed workspace to a dry-run merge.
    4. Run a self-update round trip to a second commit while a detached stub worker is alive.
       - It uses a local bare remote behind `url.<base>.insteadOf`.
       - The stub `gh` answers the updater's workflow-run and check-run reads.
       - Assert that `served_sha` advanced and the worker survived.
  - **`windows-checkout`** on `windows-latest`: check out with `core.autocrlf=true`, and assert that every file `.gitattributes` names is LF.
  - `gate` needs both on pull requests. The workflow tests pin them.
- **DoD** (`gate`):
  - Both jobs pass on a PR.
  - `gate` fails when either fails (tested).
- **Depends on:** D4, D5, D8.

### D10 Docker documentation and tutorials (preview)

- **Scope:**
  - **`docs/architecture/docker.md`** completed, with its index line.
  - **`docs/tutorials/docker.md`**, labelled "preview, not yet rehearsed" until D13, with:
    - **Windows:** Docker Desktop with WSL 2; run every command in PowerShell or Windows Terminal, not Git Bash; `memory=` and `swap=` in `.wslconfig`; the disk-image location and how to reclaim space; Docker Desktop's licence terms; "start when you sign in"; a sleeping PC pauses the fleet.
    - **Linux:** install Docker from Docker's own apt repository; `systemctl enable docker`; the `docker` group is part of the trust boundary; remote access through `ssh -L 8585:127.0.0.1:8585 <host>`, never a wider port binding.
    - **macOS:** Docker or `./setup.sh`, never both (DD9).
    - **Everyone:**
      - keep the launcher folder;
      - `down` versus `down -v`;
      - backup, restore and move commands for bash and PowerShell;
      - logs (`docker compose logs`, and `~/.alan/logs` through `exec`);
      - the rebuild after `image_rebuild_required`.
  - **Sizing:** VM sizing labelled provisional, as the macOS setup minimums; D13 replaces it with D11's and D12's measurements.
  - **Tutorial updates:** the Claude sign-in tutorial gains its container section (DD8), and the security tutorial gains D6's Docker section.
- **DoD** (`gate`): every tutorial command matches the setup flags (the existing grep test, extended to the Docker tutorial).
- **Depends on:** D8.

## Phase D11–D13: rehearsal and release

### D11 Linux rehearsal

- **Maintainer.** On a clean amd64 Linux host, following `docs/tutorials/docker.md` word for word:
  1. Template, launcher clone, build, `up`, then setup; every requirement passes.
  2. A build chat proposes and dispatches.
  3. One fleet PR merges on a **private** copy. The instance self-updates, including a restart-requiring change, with the container recreated since setup and a live worker surviving the service restart.
  4. Force a bad SHA and watch the rollback. Merge an image-input change and watch `image_rebuild_required`, then the rebuild.
  5. `docker compose down`, then `up`, keeps all state. So does a launcher re-cloned under another folder name.
  6. Reboot the host and run no command: alan serves again, and the queue resumes.
  7. The real-CLI credential canary (D6), recording `claude --version`.
  8. Uninstall.
- **Record** peak VM memory, CPU and the volume sizes after a full day at default concurrency.
- **Depends on:** D4, D5, D6, D7, D9, D10.

### D12 Windows rehearsal

- **Maintainer.** D11's steps on Windows 11 with Docker Desktop (WSL 2), on a 16 GB host, plus:
  - a launcher clone made with Git for Windows' default `autocrlf`;
  - a host sleep and resume during a live worker (D5's clock-step case);
  - a Docker Desktop restart, which must end in resume, not loss.
- Repeated before every Docker release (DD3).
- **Depends on:** D11 and OQ1.

### D13 Release 1.1

- **Maintainer.**
  - The README gains the Docker quickstart, the platform matrix marking Linux and Windows supported, the measured sizing, and what the Docker CI jobs cost a copy.
  - The tutorials drop the preview label.
  - Tag `r1.1` with release notes.
  - Set this file's status to DONE.
- **Depends on:** D11, D12.

## Out of scope for release 1.1

- A published container image, Kubernetes, or Helm.
- A native Linux service without Docker (a systemd unit).
- Windows without WSL 2, and native Intel Macs.
- A stubbed demo mode.
- The iOS preset off macOS.
- The UI "update now" button. It is a separate item, not part of this roadmap.
- An upstream update channel into existing copies (release 2).

## Review record

On 2026-10-06, four devil's-advocate reviews checked the first draft of this file. Their 48 findings (10 rated blocker, several overlapping) are all incorporated above. The main changes:
- **Technical accuracy:**
  - the first-run bootstrap (DD4, D8);
  - state outside the volumes (DD6);
  - operator clones (DD6, D3);
  - the updater's argument and runner plumbing (D1, D2);
  - image-input drift (D2, D3);
  - arm64 coverage (D3, D9);
  - cgroup load (D4).
- **Security and public exposure:**
  - the dedicated driving install and posture `propose` (DD2, D0.3);
  - the pre-push scan and noreply identity (D0.2);
  - strangers' content kept out of prompts (D0.2);
  - container-mode API authentication (D7);
  - the supply chain and compose hardening (D3);
  - the credential-boundary DoD and the trust-boundary text (D6).
- **Delivery and sequencing:**
  - posture and engine fields (D0.3);
  - Issues off and the driver (D0.3, D0.4);
  - the dependency cycle and forward references (execution order, D3, D10);
  - who executes each DoD (binding rules);
  - the drift-gate wiring (D3);
  - Docker jobs skipped on push (D3, D9).
- **New-user walkthrough:**
  - setup inside the serving container and the serve lock (D3, D8);
  - `gh auth setup-git` (D3);
  - container-aware texts (D4);
  - VM sizing (D8, D10, D11);
  - the restart policy and fixed volume names (D3);
  - the platform guides (D10).

A follow-up automated review of the revised draft added three more, also incorporated:
- modified binary files in the public-write guard's scan (D0.2);
- `gh auth setup-git` before the first sign-in (D3);
- the image hash covering every file the image copies, with a smaller bootstrap (DD4, D2, D8).
