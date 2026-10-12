# Driver loops (supervised headless runner)

**Status:** the registry ships **empty on purpose** — `config/driver_loops.d/` contains no loop entries. The runner, its scheduling and run-recording, the control surface (pause/resume/run-now routes, the fleet-level Loops panel on `/health`; `alan/routes/driver_loops.py`; see `docs/architecture/health.md`'s Driver loops section), and the triage alarm all remain shipped and exercised — against synthetic test fixtures, since there is nothing left to register against. `/refocus-driver`, a second engine's roadmap driver, and `/issue-driver` run today by hand as interactive `/loop` sessions, with no signal showing whether they are alive. Registering any of them as a live headless loop waits on **two** separate things: #1440 (the `git diff/log/show --output` write gap that gates enabling any headless loop) **and** re-pointing `ISSUE_DRIVER_NAMES` (which now holds only `alan-issue-driver`) at the current live driver names — see "Reference template for registering the live drivers" below for the reviewed permission material that a future registration will need.

This module lets alan take over: each wake is a fresh headless `claude -p` run, recorded, scheduled, and alarmed on — independent of the fleet's dispatcher and workers.

## Registry (`config/driver_loops.d/*.yaml`, `alan/driver_loop_registry.py`)

File-authoritative, tracked configuration — the reviewed permission boundary for every loop. `DriverLoopEntry` fields: `name`, `command` (the slash command), `args`, `cwd`, `dedicated_clone`, `interval_seconds` XOR `self_paced` (exactly one), `enabled`, `timeout_seconds`, `model`, `add_dirs`, `allowed_tools`, `own_state_write`, `web_fetch_domains`.

`validate_entry` is a positive family allow-list with named rejections (`DriverLoopValidationError.reason`):

- `invalid_name` / `name_collides_with_reserved_subdir` — name must be lowercase-hyphenated and must not collide with an existing `alan.roots.STATE_ROOT_SUBDIRS` value.
- `schedule_ambiguous` / `schedule_interval_not_positive` — exactly one of `interval_seconds`/`self_paced` must be set; a fixed interval must be positive. There is no `permission-mode` field — every loop is deny-by-default (below).
- `grants_on_non_issue_driver` — `allowed_tools`, `add_dirs`, `web_fetch_domains`, and `own_state_write` must all be empty/false unless `name` is in `ISSUE_DRIVER_NAMES`, which holds only `alan-issue-driver`. The second grant-bearing name was retired from the allowlist along with its registration, so a driver of any other name that carries a grant is quarantined (`tests/test_driver_loop_registry.py` pins the set and the quarantine).
- `raw_gh_api_forbidden` — a tool string containing `gh api`, `gh pr merge`, `gh repo delete`, or `gh auth` is rejected outright, before the family check even runs.
- `tool_not_in_allow_list` — every other `allowed_tools` entry must match one of a fixed set of exact strings: `Read`/`Grep`/`Glob`, a read-only `git` command (`log`, `diff`, `status`, `show`, `branch --list`, `branch --show-current`, `fetch`, `merge-base`, `rev-parse`), a `gh issue` command, `gh label create`/`gh label list`, a read-only `gh pr`/`gh run` command (`gh pr list`, `gh pr view`, `gh pr checks`, `gh run list`, `gh run view`), the fixed local-API tool (`Bash(curl http://127.0.0.1:8585/api/*)`), or `WebFetch(domain:<name>)`. Matching is exact-string, not prefix: a broader grant such as `Bash(gh pr:*)`, `Bash(gh run:*)`, `Bash(gh label:*)`, or `Bash(git merge:*)`, and any write subcommand (`gh pr edit|comment|review|close`, `gh run rerun|cancel`, `gh label edit|delete`) all fall through to this same rejection.
- `web_fetch_domain_invalid` — `web_fetch_domains` entries must be bare domain names, never a scheme or a wildcard.
- `cwd_not_absolute` / `cwd_inside_alan_home` / `add_dir_inside_alan_home` — `cwd` and every `add_dirs` entry must be an absolute path and must never equal or sit inside `~/.alan` (which covers `~/.alan/state` and `~/.alan/secrets*` as a corollary — a loop's working directory or an operator-declared extra directory is never inside alan's own runtime-state tree). A loop's own write scope is never declared this way: `own_state_write: true` grants exactly `driver_loop_state_root() / name` (see below) as an `--add-dir` value and as a pair of path-scoped `Write(//<absolute state_dir>/**)`/`Edit(//<absolute state_dir>/**)` allowedTools entries, entirely derived by `driver_loop_cmd.py` — never a literal path an operator writes into the registry.

A YAML file that fails to parse, fails schema validation, or fails `validate_entry` is quarantined on its own (`QuarantinedDriverLoop`) rather than blocking the other files, and is surfaced as a `config_quarantine` health finding with `repo: None` — see "Alarm" below for exactly how (`alan/teams/_triage_driver_loops.py::driver_loop_config_quarantine_surfaces`), the same finding shape `alan/config_files.py` already uses for engine configs.

No shipped entry carries any grants today, since none are shipped. The permission boundary the two now-retired grant-bearing registrations (`alan-issue-driver` and a second engine's own issue driver) once encoded is preserved verbatim as reference material for the future registration of the live drivers — see "Reference template for registering the live drivers" below.

## Command construction (`alan/driver_loop_cmd.py`)

`build_argv(entry)` renders the exact flag line below — the fleet workers' command builder (`alan/claude_cli.py`'s `_build_cmd`) is never touched or reused, since loops need their own deny-by-default construction:

```
claude -p "/<command> <args>" --permission-mode dontAsk --tools <code-computed tool families> --setting-sources user --strict-mcp-config --output-format stream-json --verbose [--allowedTools ...] --disallowedTools <code-owned list> [--add-dir ...] [--model <model>]
```

`--setting-sources user` stays on every run because a registered driver's command exists only as `~/.claude/commands/<name>.md` — per the CLI's own settings-source contract, that discovery is gated on the `user` source specifically (alongside `~/.claude/settings.json`), and there is no flag that loads user commands without also loading user settings. That means `~/.claude/settings.json`'s own `permissions.allow` rules are still *read*; `--tools <families>` is what stops them from *acting*: it is a hard tool-availability gate (the same mechanism `--restricted` uses to strip Bash/WebFetch by default) computed strictly from the registry — `Read`/`Grep`/`Glob` always, plus `Bash` iff the entry grants any `Bash(...)` tool, plus `Write`/`Edit` iff `own_state_write`, plus `WebFetch` iff `web_fetch_domains` is non-empty — so a tool the entry never asks for is absent from the model's tool palette entirely, regardless of what an operator's own personal settings happen to allow. The rest of this paragraph illustrates the mechanism using the two now-retired registrations preserved as the reference template below, since no entry ships today: for a zero-grant entry like the retired `babysit` and second-engine roadmap-driver registrations, this closed the gap completely — nothing beyond read-only Read/Grep/Glob was ever available, full stop. For a grant-bearing entry like the two retired issue-driver registrations, `Bash`/`Write`/`Edit` stayed available since the registry legitimately granted them; within that already-granted family, an operator's own untracked `permissions.allow` rule for the *same* tool (e.g. a personal bare `Write` or `Bash(mkdir:*)` rule, confirmed live in a personal `~/.claude/settings.json`) could still widen what's approved — a residual inherent to needing the `user` setting source for command resolution at all, not eliminated by `--tools`. That residual is **not** bounded to the loop's own dedicated, isolated clone: it reaches every directory in the session — the clone itself, the loop's own state directory, and, for the retired second engine's issue-driver registration, this repository's `docs/` folder granted via `add_dirs`. Closing that residual too would mean moving a registered driver's command out of `~/.claude/commands/` into a project- or `--add-dir`-scoped location so `user` could be dropped from `--setting-sources` entirely; that is a provisioning/architecture change outside this PR (see "Out of scope" below).

`--disallowedTools` is a fixed, code-owned constant (`Bash(gh api:*)`, `Bash(gh pr merge:*)`, `Bash(gh repo delete:*)`, `Bash(gh auth:*)`, `Bash(rm -rf:*)`, `NotebookEdit`, `WebSearch`) present on every run regardless of the registry — this is what makes "never raw `gh api`" structural rather than a registry promise. `own_state_write` adds `Write(//<absolute state_dir>/**)`/`Edit(//<absolute state_dir>/**)` to `--allowedTools` and `<state_dir>` (a plain, single-slash path) to `--add-dir`; `web_fetch_domains` become `WebFetch(domain:<d>)` entries. The doubled leading slash on the `Write`/`Edit` rules is deliberate, not a typo: for a rule passed as a command-line flag (as opposed to one written into a settings file), the CLI anchors a single leading `/` at the session's working directory rather than at the filesystem root, so a rule written as `Write($HOME/.alan/<name>/**)` resolved *inside the loop's own clone* (`<clone>$HOME/.alan/<name>/**`) and never matched a real write — the defect this PR fixes. A rule of the form `//` plus the absolute path anchors at the filesystem root instead, confirmed by a probe run under `dontAsk`. `--add-dir` values are never given this treatment — `//` is permission-rule syntax only, and a directory argument is already resolved by the CLI as an ordinary filesystem path. `build_env()` is an allow-list (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TERM`, `USER`, `SHELL`), so `ALAN_VERIFIER_GH_TOKEN`, every `engine_config.registered_secret_env_names()` variable, and `control_plane.CONTROL_TOKEN_ENV` are absent by construction — never present-then-stripped. `render_for_doc(entry)` is `shlex.join(build_argv(entry))`, used both for operator-facing shakedown and pinned below by `tests/test_driver_loop_doc_parity.py`.

`driver_loop_runs.spawn_run` invokes this with `stdin=DEVNULL`, stdout/stderr to per-run log files, `start_new_session=True` (detached — survives a deploy restart of the whole service). Registry `cwd` and `add_dirs` are validated with `Path.expanduser()` (so `~/...` is accepted in the YAML) but neither `claude` nor `git` nor `subprocess.Popen` expand `~` themselves — `build_argv`'s `--add-dir` values, `_fast_forward`'s `git -C`, and `spawn_run`'s `Popen(cwd=...)` all expand the path a second time at the point of use, so a shipped `cwd: "~/repos/alan"` resolves correctly instead of failing outright.

### Reference template for registering the live drivers

Reviewed at #1414/#1433. Historical reference only: this section is never loadable as a live registration, and it can never satisfy the documentation/registry agreement check (`tests/test_driver_loop_doc_parity.py`) by standing in for a real entry. Two independent shape reasons back that: this is a `###` heading, never `####`, and its illustrative command fragment below sits in a fence tagged `text` rather than left untagged — `_ENTRY_BLOCK_RE` (which matches only a `#### <name>` heading immediately followed by an untagged fence) cannot match either shape. **This heading level and fence tag are load-bearing and enforced by `tests/test_driver_loop_doc_parity.py` — do not "tidy" this section into a `####` heading or a bare fence.** A future registration PR re-renders its own command line via `render_for_doc` rather than copying anything from this section verbatim.

The two grant-bearing registrations retired by this change — `alan-issue-driver` and a second engine's own issue driver — carried the following reviewed permission material, preserved here for the live drivers' eventual registration:

- **`allowed_tools`** — the full allow-list both entries carried (not all read-only: `gh issue comment`/`close`/`edit`/`create` and `gh label create` mutate GitHub state, and `git fetch` mutates the local clone's remote-tracking refs):
  - `Bash(git log:*)`
  - `Bash(git diff:*)`
  - `Bash(git status:*)`
  - `Bash(git show:*)`
  - `Bash(git branch --list:*)`
  - `Bash(git branch --show-current:*)`
  - `Bash(git fetch:*)`
  - `Bash(git merge-base:*)`
  - `Bash(git rev-parse:*)`
  - `Bash(gh issue view:*)`
  - `Bash(gh issue list:*)`
  - `Bash(gh issue comment:*)`
  - `Bash(gh issue close:*)`
  - `Bash(gh issue edit:*)`
  - `Bash(gh issue create:*)`
  - `Bash(gh label create:*)`
  - `Bash(gh label list:*)`
  - `Bash(gh pr list:*)`
  - `Bash(gh pr view:*)`
  - `Bash(gh pr checks:*)`
  - `Bash(gh run list:*)`
  - `Bash(gh run view:*)`
  - `Bash(curl http://127.0.0.1:8585/api/*)`
- **`args: headless`** — both ran their slash command with this argument.
- **`dedicated_clone: true` plus `own_state_write: true`** — this posture is what makes `driver_loop_cmd.py` emit the `//`-anchored `Write`/`Edit` state-dir rules and the matching `--add-dir` state-directory path, scoping every write grant to the loop's own `~/.alan/<name>/` directory:

  ```text
  Write(//$HOME/.alan/<name>/**)
  Edit(//$HOME/.alan/<name>/**)
  ```

- **One extra read directory** — the second engine's own issue driver alone granted a read-only path into this repository's `docs/` tree, read-only as far as the registry is concerned since the only write rules it emitted were scoped to its own state directory; it needed the directory to read a roadmap document in `docs/` for its roadmap-ownership check.

`babysit` and the second engine's roadmap driver carried zero grants and are not reproduced here — they carry no reviewed permission material worth preserving as a template.

Both retired issue drivers ran against `dedicated_clone: true` clones the owner provisioned separately (out of scope here) — see "Fast-forward" below for why a dedicated clone matters for a loop that writes anything.

## Wake clock and precedence (`alan/driver_loop_schedule.py`)

`decide(ScheduleContext) -> Decision` is pure, with fixed precedence: **not supervised → disabled → running → PAUSE → usage pause → live interactive lock → fast-forward hold → back-off → idle → spawn.** `queue.paused_until()` is the only `Queue` method this module family ever calls — no new table.

- **Fixed interval** counts from the previous run's own `started_at`, floored so a wake that falls due mid-run fires no sooner than `MID_RUN_WAKE_FLOOR_S` (180s) after that run's `ended_at`.
- **Self-paced** loops print `ALAN_LOOP_NEXT_WAKE=<seconds>` as the last non-empty line of their final result text; the value is clamped to `[180, 3600]` and defaults to `1800` when the marker is absent or unparseable (`driver_loop_runs.parse_next_wake_marker`). The next wake is the prior run's `ended_at` plus that value.
- **Back-off** is `min(BACKOFF_BASE_S * 2**(k-1), BACKOFF_CAP_S)` (300s base, 14400s / 4h cap) where `k` is the count of trailing `failed`/`timed_out` runs, walking back from the most recent run and skipping (neither counting nor breaking the streak) any `held_usage_pause`/`unknown_after_restart` run in between.
- Exactly one run per loop at a time, enforced by the `running` field of `ScheduleContext` (an unfinished run record for that loop) — there is no global concurrency cap across loops.

### Fast-forward (`alan/driver_loop_runner.py::_fast_forward`)

Only `dedicated_clone` entries are fast-forwarded (`git fetch origin main`, then `git merge --ff-only origin/main`, each capped at 60s); for every other entry `_fast_forward` returns `None` immediately without touching git. The attempt is gated behind a **cheap** decision computed with `fast_forward_error` forced to `None` — `_tick_one` only calls `_fast_forward` when that cheap decision is `spawn` or a run-now request is pending (checked via `driver_loop_status.run_now_pending`, which peeks the flag without consuming it). A loop that simply isn't due yet never runs git, so a slow remote can't delay another loop's due wake on the same runner thread.

On failure, the git error is persisted per loop via `driver_loop_status.record_fast_forward_failure(name, detail)` (a plain text file at `state_root()/driver_loops/<name>/fast_forward_error.txt`) and logged; on success it's cleared via `clear_fast_forward_failure`. `driver_loop_status._status_for` — and therefore `snapshot()` and the triage alarm below — reads the persisted failure via `last_fast_forward_failure` on every call, independent of the runner's own tick cadence, so a failure recorded by one tick stays visible between ticks (to a manual status check or a triage sweep) instead of disappearing the instant the tick that recorded it ends. The reported hold reason is `fast_forward_failed` with the git error as `Decision.detail`, never a misleading `overdue`.

## Runs, records, and restart adoption (`alan/driver_loop_runs.py`)

One JSON record per run at `state_root()/driver_loops/<name>/runs/<run_id>.json`, written atomically (mkstemp + `os.replace`) **before** `Popen`, with `pid`/`pgid`/`lstart`/`cmdline` stamped immediately after — the same write-before-spawn/stamp-after-spawn discipline as `alan/worker_records.py`, reused via `worker_records.ps_identity`/`is_pid_alive_or_ambiguous`/`terminate_group`/`guard_no_group_signal` (never `WorkerRecord`, `probe`, or `load_all` — loop runs never enter team-worker records and never count as fleet workers). Logs go to `logs_root()/driver_loops/<name>/<run_id>.{stdout,stderr}.log`. Only the last 20 records and logs per loop are kept (`prune_old_runs`).

Because the runner never retains a live `Popen` handle across a service restart, every run is resolved the same way whether or not a restart actually happened: `reconcile_after_restart(loop_name)` re-probes the record's `pid` via `ps_identity`/`is_pid_alive_or_ambiguous`. An unstamped (`pid is None`) or ambiguous/live run **holds its loop** until that run's own `deadline_at` — never spawning a second run for the same loop — and once the deadline passes, a still-alive process is killed via `terminate_group` (guarded by `guard_no_group_signal`) before being finalized. In every case, the outcome is derived from the run's own stdout log's final `result` stream-json event, and the exit code is recorded as **unavailable** (`exit_code_unavailable: true`) — a restart-straddling run never fabricates a real exit code. A run reaped within the same process lifetime (the common case) instead gets its real exit code from `Popen.wait()`/`.poll()`.

## Outcomes

Fixed precedence, matching `derive_outcome`: **unknown-after-restart** (log unresolved) → **timed out** → **held by usage pause** (the run's own final result names a usage-limit message) → **failed** (non-zero / error result) → **ran with denials** (`result.permission_denials > 0`) → **succeeded**. The denial count itself is always recorded (`RunRecord.denial_count`), independent of which outcome it produced.

## Holds

- **Usage/rate-limit pause** — `queue.paused_until()` in the future.
- **PAUSE file** — `driver_loop_pause.pause(name)`/`resume(name)`, refusing outside production (`not_supervised`) and allowed even for a disabled entry, so a hand-run interactive `/loop` session honours the same file. Lives at `driver_loop_state_root()/driver_loops/pause/<name>.pause` — `driver_loop_state_root()` (`ALAN_DRIVER_LOOP_STATE_ROOT`, subdir `.`) defaults to `~/.alan` itself in production and is redirected in every test/CI/e2e isolation surface.
- **Back-off** — see above.
- **Live interactive lock** — an interactive `/loop` session leaves an undocumented `.claude/scheduled_tasks.lock` in its working directory; its mere presence holds the loop and also raises a `driver_loop_attention` finding (below) so the fleet doesn't silently race a human's own session.
- **Fast-forward failure** — see "Fast-forward" above; the loop holds with reason `fast_forward_failed` and the git error as detail until the next successful fast-forward clears it.

PAUSE and usage-pause holds never count as overdue for alarm purposes — see below.

## Alarm (`alan/teams/_triage_driver_loops.py`, wired into `alan/teams/triage.py::TriageTeam._augmented_surfaces`)

Two self-clearing kinds, computed fresh every ~30-minute triage sweep from `driver_loop_status.snapshot()` — a finding is emitted only for the current sweep's condition, so it clears the moment the condition no longer holds:

- `driver_loop_unhealthy` (**high**) — the loop is overdue (due but not yet spawned), backing off, its dedicated clone fails to fast-forward (`fast_forward_failed`), or its last completed run failed/timed out.
- `driver_loop_attention` (**medium**) — the last completed run succeeded but reported denials (`ran_with_denials`), or the loop is currently held by a live interactive lock.

The `failing`/`ran_with_denials` reasons are suppressed while the loop is held as `not_supervised`, `disabled`, or `paused`, so pausing or disabling a loop over a failed last run silences the alarm instead of leaving it firing forever with no new run ever able to clear it.

An enabled, unpaused, non-usage-held loop that isn't in either state raises nothing.

A YAML file under `config/driver_loops.d/` that fails to parse, fails schema validation, or fails `validate_entry` is quarantined (`QuarantinedDriverLoop`) rather than blocking the other files, and is surfaced by this same module (`driver_loop_config_quarantine_surfaces`, wired into `_augmented_surfaces` right alongside the existing engine-config quarantine surfaces) as a `config_quarantine` health finding with `repo: None` — the same shape `alan/config_files.py` already uses for engine configs.

## Production-only gating

The runner (`alan/driver_loop_runner.py`) is a standalone daemon thread, started from `alan/routes/infra.py`'s lifespan via one call (`driver_loop_runner.start(queue=queue, on_change=...)`), gated solely on `alan.detached_mode.resolve_detached_mode()` — the same dial that gates detached fleet workers. Stage, dev, CI, e2e, and pytest all resolve this dial off (`ALAN_SLOT_POOL_ROOT` empty), so no test or dev run ever starts a real `claude`. `driver_loop_pause.pause`/`resume` independently refuse (`DriverLoopPauseError("not_supervised")`) whenever the dial is off, so a stray PAUSE-file write can't happen outside production either. `stop()` only stops the scheduling thread — it never signals a live, detached run. `alan/web/e2e/fixtures/server-env.ts` pins `ALAN_DETACHED_WORKERS: "off"` (already the e2e default) and adds `ALAN_DRIVER_LOOP_STATE_ROOT` as an isolation root alongside it.

The runner publishes `driver_loop_changed` on every spawn, joining `alan/routes/events.py`'s `_HEALTH_KINDS` — it routes only to the health SSE surface, never to `workspace_list`/`inbox`.

## Layering

`alan/driver_loop_{runner,status,schedule,runs,pause,cmd,registry}.py` sit as one contiguous block in `pyproject.toml`'s `[tool.importlinter]` layers list, immediately below `project` and immediately above `queue` — in that top-to-bottom order (`runner` importing everything below it; `registry` importing nothing from its siblings). This is what lets `runs.py` reuse `alan.claude_cli_journal`'s `is_error_result`/`last_result_event`/`result_detail` and `alan.usage_limit.is_usage_limit_message` (both sit below `queue`) without duplicating stream-json parsing, while staying reachable downward from `alan.teams` (`_triage_driver_loops.py`) and from `alan.routes.infra`/`alan.routes.events` at the top of the stack.

## Rollout check (not a merge gate)

After this PR merges with every loop still `enabled: false`, the runner spawns nothing — confirmed by the "inert on merge" test coverage in this PR. Enabling a loop, granting the two issue drivers' clones, and confirming a live run of each completes its intended writes with **zero permission denials** is the owner's post-merge rollout check against the real production instance — it is explicitly not a CI gate, since it requires real GitHub credentials and a live `claude` session that no test environment has. The check must confirm that a run can *create a new file* in its state directory, not only edit an existing one — an on-disk probe run under `dontAsk` before this fix tested only editing, and a loop's first-ever run has no journal yet to edit. Read the run's recorded permission denials (`RunRecord.denial_count`, the per-run stdout log) as part of the check, not just its overall outcome.

## Out of scope here

Editing the `.claude/commands/*.md` slash-command files; provisioning the two dedicated clones; loop-side throttling of GitHub API use; raising any cap, pin, or baseline; the `git diff/log/show --output` write gap that gates enabling any loop (tracked in #1440, its own issue); the CI backend path filter not covering `docs/architecture/driver_loops.md`, which lets a docs-only change bypass the doc-parity test (tracked in #1441).
