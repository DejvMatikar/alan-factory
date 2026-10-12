# Operator terminal

The operator terminal gives whoever runs an install a real, interactive Claude Code session inside the web UI, running with that person's own authority, so a stuck fleet can be diagnosed and unblocked without opening a terminal on the host. This document covers the backend and the WebSocket bridge; the Operator page, the top-bar dot and the stuck-workspace link are frontend work that consumes the contract below.

## Shape

There is **exactly one session** at a time. It is a `claude` process hosted by a **tmux server on a private socket**, started in an **operator checkout** whose location depends on the scope (see "Scope decides where the session starts"). The browser never talks to tmux directly: a WebSocket handler spawns a short-lived `tmux attach-session` client on a pty and pumps bytes between the two. Closing the page kills only that attach client; the session lives on inside tmux.

| Module | Role |
| --- | --- |
| `alan/operator_prompt.py` | `Scope` (`fleet`/`repo`/`workspace`), target validation (a server-side format check), the workspace analysis prompt |
| `alan/operator_ticket.py` | single-use, 30 s, in-memory attach tickets |
| `alan/operator_checkout.py` | git plumbing for operator checkouts: the git runner, credential-embedding refusal, clone, fast-forward update, redaction, the install's origin and legacy checkout path |
| `alan/operator_repos.py` | the operator repos root and per-repository checkout paths, `ensure_repo`/`ensure_all`, the legacy-checkout migration, the warning cap |
| `alan/operator_launch.py` | one `LaunchPlan` per scope: working directory, `claude` argv (prompt, extra folders, credential deny-rules) and warnings |
| `alan/operator_tmux.py` | private socket + conf, the tmux runner seam, `has-session`/`new-session`/`capture-pane`/`kill-session` |
| `alan/operator_pty.py` | the pty attach client (`PtyClient`, `PtySpawnError`), resize, async output pump |
| `alan/operator_session.py` | the one-session state machine: availability, target resolution, start/replace, status, End and transcript archive |
| `alan/routes/operator_terminal.py` | `/api/operator/*` HTTP routes and the WebSocket |

Layering (`[tool.importlinter]` in `pyproject.toml`): `operator_session` sits directly above `workspace` (it records interventions and writes into workspace directories); `operator_launch` and `operator_repos` sit directly below `workspace`, in that order (launch plans call into the repos module, which reads the engine registry); the other five sit directly above `driver_loop_cmd` (they borrow its env allow-list); the route joins the routes independence contract.

## Starting a session

`operator_session.start(scope, target, replace=…)` runs, in order, under one process-wide lock:

1. **Validate and resolve.** `fleet` takes no target; `repo` takes `owner/name`; `workspace` takes a task id (`[a-z0-9][a-z0-9-]{0,63}`). Anything else is refused before any side effect (`422`). A `repo` target must be a **registered, non-hidden** engine (`engine_config.lookup_by_slug`; the match is exact), otherwise `404`. A `workspace` target must exist (`404`), and its repository is resolved through `lookup_by_slug(engine_key)`, or `get_registered()` when the workspace has no engine key: a hidden engine is allowed, an unregistered one is `404`. The resolution never falls back to `canonical_scope_key`, which would silently substitute the default engine.
2. **Refuse a second session.** If a session is live and `replace` is false, `409`. Only an explicit `replace: true` continues.
3. **Availability gate.** Consulted **only here**, never for attach or End (a live session stays attachable and endable even if a prerequisite later disappears). The named causes are `detached_mode_off`, `tmux_missing`, `claude_missing`, `git_missing`, all checked against the same allow-listed `PATH` the session will get, and `checkout_clone_failed` (below). `GET /api/operator/status` reports the same reason (with a human sentence) when no session is live, so the page can say exactly why it is unavailable instead of showing a broken terminal.
4. **Finalize the replaced session**, if any (transcript archive for workspace scope, then `kill-session`). The checkout updates and the legacy-checkout migration below therefore never run under a live session. **A replace is not atomic:** once this step has run the old session is gone, so a later failure (`503` checkout clone failure, `500` spawn or record failure) leaves **no** session live. The old session's workspace transcript is already archived, and nothing about the old session is lost that End would not also lose. Only a failure *before* this step (`409`, `404`, `422`, `503` unavailable) leaves the old session running.
5. **Ensure the checkouts and build the launch plan** (the next two sections). A failed install checkout (global and workspace scope) or a failed first clone of the started repository is `OperatorUnavailable` with reason `checkout_clone_failed` (`503`).
6. **Spawn**: `tmux new-session -d -s alan-operator -c <cwd> … <claude argv>` on the private socket, where `<cwd>` and the argv come from the launch plan. tmux refusing, missing, or timing out is `OperatorStartFailed` (`500`), with tmux's stderr redacted.
7. **Record** `session.json` (scope, target, start time, update warning) at mode `0600`. If that write fails the just-started session is killed again and the stop is confirmed the same way End confirms it, so a session never exists without its record; if the stop cannot be confirmed the `500` says so, and the surviving session is still visible to `current()` (as an unscoped fleet session) so it can be attached and ended.
8. **Workspace scope only:** record `InterventionAction.OPERATOR_TERMINAL` on the workspace, **after** the spawn succeeded. A failure to record is logged and swallowed; a live session matters more than the marker.

The route publishes `operator_session_changed` on start and End, and also when a start that asked for `replace` fails (the old session may already be gone, so the dot must be refetched). `routes/events.py` classifies it in `_HEALTH_KINDS`, so it invalidates only the health surface (which the top-bar dot reads).

### Scope decides where the session starts, what it can reach and whether it gets a prompt

`operator_launch` builds one `LaunchPlan` per scope. The environment, the tmux invocation and the permission mode (no permission flags — the operator approves in the terminal exactly as in any Claude Code session) are identical for every scope. What varies is the working directory, the extra folders and the prompt. The `claude` argv is always `claude [<prompt>] [--add-dir <dir>...] --disallowedTools <deny-rules>...`, with the variadic flags after the prompt so they cannot swallow it.

| Scope | Working directory | `--add-dir` | Prompt |
| --- | --- | --- | --- |
| `fleet` (global) | the operator repos root, holding a checkout of every connected repository | the workspaces, logs and state roots | none: the session sits idle until the operator types |
| `repo` | that repository's operator checkout | none (no fleet state folders) | none |
| `workspace` | the operator checkout of the workspace's own repository | the workspace's folder (`<workspaces-root>/<task-id>`, which holds every subtask), the logs root, and `<install-checkout>/docs` when the workspace's repository is not the install's own | `operator_prompt.workspace_prompt`: pointers with absolute paths only, plus the safety rules |

Global and repo sessions inject **no prompt, no rules and no task**. The workspace prompt holds **pointers only** — the workspace folder, the logs root, the architecture docs under the install checkout, the workspace's task id and repository — and never any gathered data: the backend reads no workspace content to build it. Every pointer in it resolves inside the session's working directory or one of its extra folders. It carries the safety rules (the credential files are blocked, inspect before changing, act only on what the operator asks, never print secrets or the control token, no merges or history rewrites unless asked, leave the listed directories alone). The rules rely on the model following them; see accepted risks.

The previous rule "the scope changes the prompt and nothing else, and grants and removes no permission" is **retired**: the scope now decides the working directory and the extra folders Claude Code opens without asking, and (for global and repo) whether any prompt exists at all.

### Credential deny-rules (every scope)

Every plan ends with `--disallowedTools` followed by `claude_cli_grants.credential_deny_rules()` — the same deny-rules fleet workers get. They cover the install's control token, its metadata file and `secrets.env` (plus the secrets store, the engines directory, the `gh` config and Claude's own credential file, and a short list of credential-reading Bash commands), so the block is mechanical for Claude Code's file tools whatever the scope, not prompt wording. `disallowed_tools_args` is deliberately not used: its write-tool denial is for fleet roles, and operator sessions must be able to edit.

## The operator checkouts

`operator_repos` keeps one clone per repository under a dedicated root **beside** the install: `<install-parent>/<install-name>-operator-repos/<owner>/<repo>`. The install root is the directory holding the `alan` package. A path comes only from a registered `RepoIdentity` or from the install's own `remote.origin.url` (parsed to `owner/repo` and matched case-insensitively against the registry, so one repository never gets two folders); each of `owner` and `repo` must full-match `[A-Za-z0-9][A-Za-z0-9._-]{0,99}`. The root is never the install, never inside it, never its parent and never under `~/.alan`; the rule is checked on the literal path and on its symlink-resolved real path, for the root and for every repository checkout path, so a symlink cannot redirect a checkout into the install or `~/.alan`. An install that would violate this has no usable operator root and the session is unavailable (`checkout_clone_failed`). These are separate from the workers' slot checkouts.

- **First use:** create the parent folder and `git clone -- <url> <path>`. The install's own repository clones from the install's `remote.origin.url`; every other repository clones from `https://github.com/<owner>/<repo>.git`. An origin that embeds credentials (a password in any scheme, or a user-info token on `http`/`https`) or that does not parse is refused rather than cloned, because the URL would sit in the `git clone` argument list where local process inspection can read it; an SSH remote or a credential helper keeps the secret out of it. A failed fresh clone removes the partial directory it created (never one that existed beforehand) and raises `CheckoutCloneFailed`.
- **Later uses:** `git fetch --quiet` then `git merge --ff-only --quiet @{upstream}`. **Never a reset**, so local work the operator left in a checkout survives. Any failure returns a **warning**, never a blocker.
- **Global scope** ensures the install's repository first (a failure is unavailable), then every other registered, non-hidden repository. A clone failure of another repository and every update failure become warnings prefixed `owner/repo: `, redacted, capped at five entries plus `and N more`, and joined with `; ` into the unchanged `session.json` `update_warning`. **Repo scope** ensures only the started repository (a failed first clone is unavailable); **workspace scope** ensures the install's repository first (it supplies the docs folder) and then the workspace's own (a second warning from the install checkout is prefixed, the workspace repository's is not).
- **Legacy checkout:** installs that predate this layout kept the install's operator checkout at `<install-parent>/<install-name>-operator`. Inside `ensure_repo` for the install's own repository — so only ever after a replaced session was finalized — that folder is moved into the new layout with one `os.rename`, only when it has a `.git` directory and the new location is absent, so the operator's local work comes along. When both exist the legacy folder is left untouched and a warning says so; a failed rename is a warning too and a fresh clone follows. Nothing is ever deleted.
- Warnings are redacted: credentials embedded in URLs become `***@`, control characters and runs of whitespace collapse, and each text is capped at 300 characters.

Every git run, like every tmux run, executes with `driver_loop_cmd.build_env()` plus `TERM=xterm-256color`, with stdin closed and `GIT_TERMINAL_PROMPT=0` so a missing credential fails instead of waiting for a prompt.

## Environment

The tmux server, every tmux command, every attach client and every checkout git run receives **only** the keys `build_env()` allows (`PATH`, `HOME`, `LANG`, `LC_ALL`, `USER`, `SHELL`, minus the control token, the verifier token and every registered engine secret) plus `TERM` (and, for git, `GIT_TERMINAL_PROMPT=0`). The service's own environment is never inherited (the runners pass an explicit `env=`, they do not merge `os.environ`). No new `ALAN_*` variable exists; the state root is the existing `ALAN_STATE_ROOT` redirect, so tests and dev sessions never touch production state, and the operator repos root derives from the install location, which tests redirect.

## Survival

- The tmux server is a daemon on the private socket `state_root()/operator/tmux.sock` (directory `0700`), so it outlives the service. The conf (`operator_tmux.TMUX_CONF`, rewritten from the constant on every start) disables the tmux prefix and status line so every key reaches Claude Code, keeps 100 000 lines of scrollback, and sets `remain-on-exit on`.
- `remain-on-exit` is deliberate: when Claude Code exits, the pane stays as a dead pane. `status` reports `exited: true` for it, the operator can still read the final screen, and End can still capture it into the transcript. The session counts as live until End.
- **The service holds no session state in memory.** `current()` asks tmux (`has-session`) and reads `session.json`; a restarted service therefore finds the session again without any boot-time step. Reattachment happens when the page is opened and asks for a ticket, never at boot.
- A `session.json` with no tmux session (tmux killed, machine rebooted) reads as no session; the next start overwrites it. A live tmux session with a missing or unreadable record is reported as fleet scope with an unknown start time, so it can still be attached and ended and is never orphaned.
- `detached_mode_off` gates start because a session can only survive a service restart when the install runs detached workers (`ALAN_DETACHED_WORKERS`, see `worker_records.md`); the operator terminal deliberately follows the same dial rather than adding its own.

## API

| Method | Path | Guard | Notes |
| --- | --- | --- | --- |
| GET | `/api/operator/status` | none | `{session, unavailable}`; `session` is `{scope, target, started_at, update_warning, exited}` or `null`; `unavailable` is `{reason, detail}` or `null` (only ever set when no session is live) |
| POST | `/api/operator/session` | control token | body `{scope, target?, replace?}`; `200` session, `404` unknown workspace, a repo that is not connected or is hidden, or a workspace whose repository is not connected, `409` live session without `replace`, `422` invalid scope/target, `503` unavailable (including a failed checkout clone) with body `{detail: {reason, detail}}`, `500` tmux failure |
| DELETE | `/api/operator/session` | control token | End: archive (workspace scope), kill, remove record; `204`, `404` if nothing is live, `500` if the stop cannot be confirmed after `kill-session` |
| POST | `/api/operator/attach-ticket` | control token | `{ticket, expires_in_s}`; `404` when no session is live |
| WS | `/api/operator/terminal?ticket=…&cols=…&rows=…` | ticket | see below |

All three mutations are listed in `tests/test_operator_mutation_token_inventory.py`, which fails an unclassified new non-GET operator route.

### The WebSocket

The ticket is **popped from the in-memory store before the connection is accepted**, under a lock, so it is single-use even under a race (sixteen concurrent consumers produce one winner). A missing, unknown, reused or expired ticket is refused **before `accept()`** with close code `4401` (a real server answers the handshake with HTTP 403; the close code is what an ASGI test client sees). Tickets live 30 seconds and are purged on the next issue.

After `accept()`:

| Close code | Meaning |
| --- | --- |
| `4404` | ticket was valid but no session is live (sent after accept so a browser can read the code) |
| `4001` | superseded: a newer attachment took over |
| `4002` | ended: the attach client's output reached end-of-file because the session was killed |
| `1011` | the attach client could not be started |
| `1008` | `cols`/`rows` outside `1..1000` (refused at the handshake) |

Frames: server → browser is **binary** terminal output; browser → server is **binary** keystrokes/paste, and **text** JSON control messages. The only control message is `{"type": "resize", "cols": N, "rows": M}`; anything else, or an out-of-range value, is ignored. Resize sets the pty window size and signals the attach client with `SIGWINCH`, which tmux answers by redrawing (tmux runs with `window-size latest`).

**One browser attachment at a time.** A new attachment first takes over the in-process registry, which wakes the previous handler to close with `4001`, and then spawns its `tmux attach-session -d` client (`-d` also detaches any other tmux client, so the guarantee holds at the tmux level too). Supersede is decided by the registry flag, not by which task finished first, so the old view always reads `4001` even when tmux detaches it a moment earlier. A new attachment never creates a second session.

The attach client is closed **synchronously** when the handler leaves, including when the handler is cancelled: an `await` in cleanup can be skipped under level-triggered cancellation and would leak the child process.

## End and the transcript

`operator_session.end()` (and the replace path) finalizes: for **workspace scope only**, `tmux capture-pane -p -J -S -` (the full scrollback, wrapped lines joined) is written to `<workspace>/operator_transcript-<YYYYMMDDTHHMMSSZ>.txt` at mode `0600` (`mkstemp` + `fchmod` before any byte is written), then `kill-session`, then the record is removed. Capture happens **before** the kill. The record is removed only once a follow-up `has-session` positively answers that no such session exists; a tmux that times out or cannot be run proves nothing, so End (and a replace) fails with `500` and keeps the record until a later End can confirm the stop. The name never replaces an earlier transcript: when one already exists for the same second, `-2`, `-3`, … is appended (finalizes run under the session lock, so the existence check cannot race another archive). A missing workspace, a failed capture or an unwritable file is logged and does not stop the End. Global and repo scope archive nothing and rely on Claude Code's own session log.

## Security model

- **Control token.** Start, End and ticket issue all require `X-Alan-Control-Token` (`require_control_token`). The WebSocket itself carries no token: it carries a ticket, which can only be obtained with the token.
- **Tickets.** 32 bytes of `secrets.token_urlsafe`, 30 s TTL on the monotonic clock seam, held in memory only (a restart invalidates all of them), consumed before accept. A ticket in a URL can reach access logs; it is worth nothing after one use or 30 seconds.
- **Host and Origin.** `HostAllowlistGuard` already refused any foreign `Host`. For **websocket scopes only** it now also requires **exactly one** `Origin` header whose hostname is in the same allow-list (loopback plus the configured public origin's hostname); the Origin must be a complete serialized origin (`http` or `https`, a host name or bracketed IPv6 literal, and an optional numeric port in `1..65535`, with no user-info, path, query, fragment or surrounding whitespace), so `http://localhost:bad`, `http://localhost:99999`, `//localhost` and `http://localhost/x` are malformed. A missing, `null`, malformed, duplicated or foreign Origin is refused with `403 {"detail": "origin not allowed"}` before the app runs. That websocket rule is specific to the terminal socket; HTTP scopes are governed by the separate global write rule, under which any method other than `GET`, `HEAD` or `OPTIONS` (the terminal's Start, End and ticket-issue endpoints included) is refused with the same fixed `403` when its `Origin`, or in the absence of `Origin` its `Sec-Fetch-Site`, is cross-site; see `prep_chat.md`'s "Host allowlist". This is what stops a page on another origin from opening the terminal socket with a ticket it should not hold.
- **Credential deny-rules.** Every operator session, whatever the scope, is started with the fleet's credential deny-rules as `--disallowedTools` (see "Credential deny-rules"), so the control token, its metadata and the secrets file are blocked for Claude Code's file tools by configuration, not by prompt wording.
- **Isolation.** Private socket in a `0700` directory under the state root; session record and transcript at `0600`; explicit allow-listed environment for everything spawned; target strings validated against strict patterns, and repo targets additionally against the engine registry, before they reach a prompt, a path or a tmux argument (the whole `claude` argv is passed as a single `shlex.join`-quoted argument); checkout paths built only from registered identities or the install's parsed origin and a strict component pattern.
- **No shipped identity.** Nothing in the code names an owner path, account or machine: every path derives from the install location or the state root.

## Testing seams

`tests/conftest.py` installs two autouse **refusing fakes**, `operator_tmux.run_tmux` and `operator_checkout.run_git`, and the session-wide subprocess guard now also refuses any `tmux` exec next to `claude`. An autouse fixture also redirects `operator_checkout.install_root` into the test's tmp directory, so the operator repos root and the legacy checkout path can never resolve next to the real install (`tests/test_conftest_safeguards.py` pins this). A test that needs tmux behaviour uses the `fake_tmux` fixture (a scripted stand-in that speaks `has-session`/`list-panes`/`capture-pane`/`kill-session`/`new-session`), a test that needs git uses the `fake_git` fixture (a scripted stand-in that answers `config`, creates the checkout on `clone`, answers `fetch`/`merge`, and can be told to fail a verb for a repository), and `operator_state` (isolated state root, detached mode on, both fakes installed; it returns the operator repos root). `register_test_engine` registers a connected repository. No test spawns real tmux, claude or git; the pty tests spawn only the Python interpreter.

## Accepted risks

- **The session holds the operator's full authority.** The control token plus network reach to the service equals a shell on the host. The scope's working directory, the deny-rules and the Origin guard narrow accidents, not a token holder; a global session's extra folders and the cwd decide only what Claude Code opens without asking, not what the host user can reach.
- **Global and repo sessions have no prompt rules at all.** They are started idle, with no rules, no task and no pointers, so their only mechanical protection is the credential deny-rules. The workspace prompt's safety rules depend on the model following them; nothing enforces them mechanically, and permissions are Claude Code's own.
- **The deny-rules are not an OS sandbox.** They block Claude Code's file tools and a fixed list of credential-reading Bash commands for the control token, its metadata and the secrets file; they do not stop a different Bash command the operator approves from reading those files, and they protect nothing that is not on the list.
- **Operator checkouts are working copies the operator may edit.** They are fast-forwarded and never reset, so a diverged or dirty checkout simply fails to update and shows a warning; a global start touches every connected repository, so one slow remote makes the whole start slow while the process-wide lock is held.
- **A crash before End loses the archived transcript.** The transcript is captured only at End. If the host or tmux dies first, only Claude Code's own session log remains.
- **A ticket can appear in access logs** for the seconds it is valid.
- **One process-wide lock** serialises start and End, including a first-use clone that may take minutes; a second start during that time waits.
