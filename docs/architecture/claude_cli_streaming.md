# claude_cli streaming subprocess

## One argv builder: `LaunchSpec` and `launch_spec.to_argv` (since 2026-10-04)

Every `claude` launch alan makes — the fleet team nodes, one-shot helpers, evals, build/explore chats, the
engine-config proposal, driver loops and operator terminals — describes itself as a frozen `LaunchSpec`
(`alan/launch_spec.py`) and gets its argv from `launch_spec.to_argv(spec)`. `launch_spec.py` is the only module
in `alan/` that contains the `claude` literal or `--append-system-prompt` (`tests/test_launch_spec.py` walks every
module's string constants to prove it). `LaunchSpec.layout` selects one of four argv shapes — `agent`
(`claude -p … --append-system-prompt …`, the old `_build_cmd`), `chat` (the old `_chat_cmd`, where the prompt flag
comes last and `--verbose` follows `--output-format`), `driver_loop` and `operator` — and each shape reproduces
the previous flag order byte for byte; `tests/test_launch_spec_argv_golden.py` keeps a frozen copy of the four
previous builders as the oracle and compares every flag combination (3,456 `_build_cmd` combinations — each with and
without a project-doc block — every chat combination, eight driver-loop entries, every operator scope).

A spec carries the inputs and the *effective* result of the grant helpers, never a second copy of the rules:
`requested_tools` (as passed), `allowed_tools` (after `claude_cli_grants.narrowed_tools` unless the mode is
`acceptEdits`; empty under `deny_all_tools`), `add_dirs`, `denied` (the exact tokens after `--disallowedTools`,
including the joined `Write Edit NotebookEdit` token — `launch_spec.flat_denied_tools` splits it for display), the
appended prompt as labelled `PromptSegment`s (`system_prompt`, `turn_budget`, then the tails `headless_execution`
/ `project_doc` or `no_tools`), the session (`new` / `resumed` / `none`) and a `ProcessSpec` (cwd, idle and absolute
timeouts, fleet priority delta, detached, and the environment policy by **name and category only** — the
categories are `fixed_override`, `sandbox_redirect`, `cache_redirect`, `secret_removed`, `fleet_ceiling` and
`allowlisted`; no value is ever stored). `claude_cli._worker_env_policy` is derived from the same constants
`_worker_env` uses and `tests/test_launch_spec_env_policy.py` checks it against the real environment.

`claude_cli` builds specs through three public functions that the launch path and the agent catalogue
(`docs/architecture/agent_catalogue.md`) share: `streaming_spec` (used by `run_agent_streaming`), `oneshot_spec`
(the JSON `run_agent` path) and `chat_launch_spec` (used by `run_chat_turn`). `streaming_spec` takes the
process-start flags `fleet` and `journal_stream` as one `ProcessMode`, and the process half of a spec is set
through `launch_spec.with_process` (`with_add_dirs` for the operator workspace folders). `_build_cmd`, `_chat_cmd` and
`_appended_system_prompt` stay as thin wrappers over the same spec code because the existing, size-pinned tests
call them directly; they are listed in `__all__` so pyright treats the private names as exported. The tails are
composed in `claude_cli._prompt_segments` from the module's own `_HEADLESS_EXECUTION_BLOCK` / `_project_doc_block`
names, which is why tests that monkeypatch those names keep working.

`run_agent`, `run_agent_streaming` and `run_chat_turn` gained an optional `agent: AgentKey | None` keyword
(`alan/agent_key.py` — `kind` is one of `team_role`, `chat`, `background_helper`, `operator_launched`, `name` is a
stable id such as `verification/judge`); `run_agent` and `run_agent_streaming` also gained
`on_spec: Callable[[LaunchSpec], None] | None`. The kwargs stay optional so existing callers and test fakes keep
working, but `tests/test_agent_catalogue_parity.py` parses `alan/` and fails when any production call omits
`agent=` or passes the constant `None`. `on_spec` is called with the final spec (process facts included)
immediately before the process is spawned; an exception from it is logged (`on_spec callback raised`) and never
aborts the run. Team nodes pass `agent_key.team_role(team, role)` and a recorder that stores the spec as the
`launch` key of the node's `*.config.json` (`docs/architecture/workflow_visibility.md`). `run_chat_turn` takes no
`on_spec`: a chat turn writes no run record, and its spec is built by `claude_cli.chat_launch_spec`, which the
catalogue's chat entries and the parity gate call directly.

The effort the CLI receives is resolved in `launch_spec.resolve_effort` (explicit value, else opus→`xhigh`,
sonnet→`high`, haiku→none); `claude_cli._effort_for_model` / `_resolve_effort` are aliases of it, and the team
node starter records the same resolved value on the workflow node instead of `None`.

## `--allowedTools` is discrete argv tokens, not a joined string (since 2026-07-14, PA7.2)

`_build_cmd` and `_chat_cmd` both pass each `allowed_tools` entry as its own argv element —
`launch_spec._grant_tokens` emits `["--allowedTools", *spec.allowed_tools]` for every layout — **not**
`" ".join(allowed_tools)`. The CLI re-splits a joined `--allowedTools` value on whitespace, so a
single-token join fragments any pre-approval entry with an internal space: `Bash(uv run:*)` (a real
`_BASE_TOOLS` entry, `engine_config.py:277`) became the two fragments `Bash(uv` and `run:*)`, and the
intended pre-approval silently never registered — a mutating gate command the entry was meant to
pre-approve could be denied under `auto` mode in headless execution
(`docs/audits/auto-mode-boundary-2026-07/README.md`, candidate `PA6.3-r2`). Confirmed via
`tests/test_claude_cli.py::test_build_cmd_allowed_tools_survive_as_discrete_argv_tokens` et al. and
`tests/test_claude_cli.py::test_chat_cmd_allowed_tools_survive_intact_with_internal_spaces` before the
fix landed (both proved the fragmentation, then were flipped to assert discrete-token survival).
`--disallowedTools` (`claude_cli_grants.WRITE_TOOLS`/`claude_cli_grants.disallowed_tools_args`) was unaffected by that
fix — the write tools never contain spaces, so that one token is still `" ".join(WRITE_TOOLS)`. The credential deny
rules that now follow it (P1.12) do contain spaces (`Bash(git credential:*)`, a `Read(//…)` path with a space in it), so
each is its own argv element after a single `--disallowedTools` flag: `["--disallowedTools", "Write Edit NotebookEdit",
<rule>, <rule>, …]`. Under `acceptEdits` the write-tools token is absent and the flag carries only the credential rules;
`deny_all_tools` keeps `["--disallowedTools", "*"]` exactly, with no credential rules. `_chat_cmd` builds its grant and
deny arguments through the same two functions (`grant_args`, `disallowed_tools_args`), so chats carry the same rules.
The rule set, the narrowing of `Bash(uv run:*)`, `Bash(npx:*)` and `Bash(npm --prefix:*)` for every role except the two
implementation roles, and the reach table live in `docs/architecture/worker_credential_boundary.md`.

## `--add-dir` — extending the worker's directory sandbox (since 2026-08-02)

Current Claude Code CLIs sandbox path-bearing Bash commands (`ls`, `cat`, `stat`, `find`, …) to
the session's working directories: outside those directories even a pre-approved read-only
command requires interactive approval, which headless execution fails closed
(the 2026-08-02 maintenance run demonstrated this — every `~/.alan` probe was denied with
"may only list files in the allowed working directories", making rows 4/10 of
`docs/audits/auto-mode-boundary-2026-07/README.md` stale for path-escaping commands).
`_build_cmd` therefore accepts `add_dirs: list[str] | None` and emits each entry as a discrete
token after `--add-dir` (skipped under `deny_all_tools`, like `allowed_tools`);
`run_agent_streaming` passes it through, and teams set it per role via
`WorkerOverrides.add_dirs` (`alan/teams/_base.py`, threaded by `_overrides_to_kwargs`).
The only consumer is the maintenance team, whose four read-only roles get the live alan state
tree (`MaintenanceTeam._role_overrides` → `_state_tree_root()`: the parent of `ALAN_STATE_ROOT`
when set, else `~/.alan`) so their live-state audits (worktree/workspace residue, secret-file
permission bits, error-log scans) can run headless. This widens read reach only — the
mutating-command deny path and the `Write`/`Edit`/`NotebookEdit` hard block are unchanged, the
credential deny rules (`docs/architecture/worker_credential_boundary.md`) still cover the secret
paths inside that tree (the security scanner receives the secret files' permission bits from the
host, `alan/teams/_maintenance_secrets.py`, instead of reading them), and
`_worker_env`'s `ALAN_*_ROOT` sandbox redirection still keeps the worker's own gate runs from
writing the live tree (`docs/architecture/ci_isolation.md`). `add_dirs` is not persisted into
the per-node `*.config.json` (only `allowed_tools`/`permission_mode` are) — a boundary audit
must read it from team source, not node configs.

Both grant-side and deny-side argv builders live in the sibling module `alan/claude_cli_grants.py`
(`grant_args` — `--allowedTools` then `--add-dir`, both suppressed under `deny_all_tools`, with the three
general-exec grants filtered out unless `permission_mode` is `acceptEdits`;
`disallowed_tools_args`, `credential_deny_rules` and `WRITE_TOOLS` — the deny surface), which `claude_cli` imports. They
were extracted there rather than grown in place because `alan/claude_cli.py` is pinned shrink-only
in `tests/size_baseline.py`. A boundary audit reads the permission argv from this module.
`MaintenanceTeam._state_tree_root()` resolves the granted tree as `roots.state_root().parent`,
so it goes through the `alan.roots` seam rather than reading `ALAN_STATE_ROOT` directly
(`tests/test_no_raw_root_resolution.py` enforces this).

## Fleet-only Bash/stall ceilings (since 2026-09-12)

The child's Bash tool shares one wall-clock ceiling — `BASH_MAX_TIMEOUT_MS`,
default 600 000 ms (10 min) inside the installed `claude` CLI — regardless of
how long the work actually takes. The secondary repository's full gate
(dependency sync, frontend build, backend suite) runs to roughly 13 minutes,
past that ceiling. Probing the installed CLI (`claude --version` → `2.1.269`)
by reading its bundled source directly and by running small-scale live
`claude -p` calls with overridden env values showed, per variable:

| Variable | Verdict on 2.1.269 |
|---|---|
| `BASH_DEFAULT_TIMEOUT_MS` | Honoured — read from env, no upper clamp beyond `> 0`. Confirmed live: overriding it to 3000 cut an 8s `sleep` short. |
| `BASH_MAX_TIMEOUT_MS` | Honoured — read from env, `Math.max(env_value, default)`, no upper clamp. Confirmed live the same way. |
| `BASH_MAX_OUTPUT_LENGTH` | Honoured, but hard-capped at **150 000** chars by the CLI itself (default 30 000) — a caller-supplied value above 150 000 is silently capped, not an error. |
| `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` | Honoured — the CLI reads this directly as the stall-detection ceiling when set, overriding a dynamic default formula. Its own description is subagent-framed, but the bundled source shows it governs the general stream-watchdog stall window, which is what backs the "waiting for backgrounded work" ceiling this change needs. |

This is a **full** delivery, not a half one: unlike the upstream reports that
some CLI versions ignore these variables (`anthropics/claude-code#26660`,
`#34138`), this installed version demonstrably honours all four. Nothing
here weakens a check or lengthens an unrelated wait to compensate for a
non-functional variable — the mechanism works as read.

The CLI's own bundled string table additionally confirms the failure mode
this change targets exists as a real feature: hitting a Bash call's timeout
can produce *"Command did not complete within its {N}s timeout and was
moved to the background (ID: …). … If it exits while you are still working
you will be notified"* — a promise a one-shot headless run can never collect
on. The headless-execution prompt block below is corrected to name this
exact case, rather than only the case where a tool refuses outright.

`alan/fleet_load.py::fleet_env` — reached only via `_worker_env(fleet=True)`,
whose sole call site is the team-worker spawn (`alan/teams/_base.py`) — now
also writes four fixed literals into the child's environment,
unconditionally overriding any inherited value: `FLEET_BASH_DEFAULT_TIMEOUT_MS`
and `FLEET_BASH_MAX_TIMEOUT_MS` (both 1 800 000 ms / 30 min — comfortably
above the ~13-minute gate with headroom for slower runs, without adopting an
open-ended ceiling), `FLEET_BASH_MAX_OUTPUT_LENGTH` (150 000 — the CLI's own
discovered hard cap, so the literal states the true effective value rather
than a number that gets silently rewritten downward), and
`FLEET_ASYNC_STALL_TIMEOUT_MS` (1 800 000 ms, matching the other two so a
long-running gate is never flagged as stalled while it is still producing
output). These are fixed literals precisely because they are read by the
*child* Claude Code process, not by `alan/claude_cli.py` itself — raising
`BASH_MAX_TIMEOUT_MS` alone would still leave the gate truncated by the
*host's* own idle-timeout pump, which is why `alan/teams/_base.py::_stream_node`
also now passes `ProcessConfig(timeout_seconds=FLEET_IDLE_TIMEOUT_SECONDS)`
(2100 s / 35 min) at the single fleet worker spawn — raising the child's
ceiling without raising the host's own `_DEFAULT_IDLE_TIMEOUT_SECONDS` (600 s,
unchanged, still governs every non-fleet caller) would have the host's pump
kill the child before the child's own raised ceiling ever mattered. No
`ALAN_*` env var was added: the project's env-var-prefix convention is
reserved for state-isolation redirects, these five values are fixed fleet
literals (mirroring `FLEET_NICE_DELTA`/`FLEET_PYTEST_WORKER_CAP` immediately
above them in the same module), and no measured second engine needs a
different number.

## Headless-execution prompt block (since 2026-07-02; relocated 2026-09-12)

`HEADLESS_EXECUTION_BLOCK` is a module-level constant in `alan/headless_prompt.py`,
appended to every worker's system prompt inside `claude_cli._appended_system_prompt`
(`f"{system_prompt}\n\n{_HEADLESS_EXECUTION_BLOCK}{_project_doc_block()}"`, then
passed to `--append-system-prompt`), imported into `alan/claude_cli.py` under its
old private aliases (`_HEADLESS_EXECUTION_BLOCK`, `_project_doc_block`) so the
composed-prompt call site and every existing test that references
`claude_cli._HEADLESS_EXECUTION_BLOCK` are unchanged. The relocation was forced,
not optional: `alan/claude_cli.py` sits at exactly its 1410-line shrink-only pin
(`tests/size_baseline.py`) with zero headroom, so the fix's other net-positive
change (the event-time usage-limit raise below) had no room unless this block
moved out first. `alan/headless_prompt.py` sits in the layers list immediately
after `fleet_load` and before `project_doc_context` — strictly below `claude_cli`
(its only importer) and strictly above `project_doc_context` (which it imports
downward from for `get_active_project_doc_path`). `_build_cmd` is the single
command-construction path shared by `_run_cli` (non-streaming, `run_agent`) and
`run_agent_streaming` (streaming/resume, used by every team's worker) — so
every headless `claude -p` worker invocation carries it.

The block tells the one-shot worker that no scheduler, monitor, background
job runner, or notification channel exists inside a headless `-p`
subprocess: it must run long gate/verification commands to completion
synchronously and read their exit code/output directly, never background a
command and then poll or wait for a completion notification, never sleep or
idle waiting on an external wakeup, and never burn turns on no-op filler
while idling for a mechanism that cannot fire. This closes a failure mode
where a worker finished its real work, then backgrounded a final check and
sat waiting for a notification a headless subprocess has no scheduler to
deliver, burning its remaining turns on filler and ending without
committing.

### Refusal-and-backgrounding bullet (since 2026-08-21; broadened 2026-09-12)

The four bullets above are a *general* prohibition, and a scan of stored
worker results across roughly ten weeks found thirteen occurrences of the
exact failure they exist to prevent: a worker asked to run a gate command,
had the tool refuse it and suggest backgrounding it instead, and complied —
launching the gate in the background and then spending its remaining turns
waiting for a completion notification a headless session can never deliver.
The general rule lost to a specific, actionable-looking tool refusal that
names background execution as the sanctioned alternative. A fifth bullet
originally spoke only to that refusal case. As of 2026-09-12 the trigger it
names was found to be false in practice: the installed CLI does not refuse a
gate command that exceeds its Bash ceiling — it silently backgrounds the
command itself and promises a notification on completion (see "Fleet-only
Bash/stall ceilings" above for the CLI's own message text), which a one-shot
headless run can never receive. The bullet now names both cases — a tool
that moves work to the background on its own and says it will notify the
worker, and a tool that refuses and suggests backgrounding instead — since
neither promise can be kept here: no notification and no wakeup can ever
reach a one-shot headless run. Either way the worker must not comply; it
must re-run the command in the foreground and read its actual exit code
before deciding what to do next. Like the rest of the block, this bullet
rides the `deny_all_tools=False` path only (see "Append composition under
`deny_all_tools`" above) and is asserted directly in
`tests/test_claude_cli.py::test_headless_block_forbids_complying_with_background_execution_advice`
and in `tests/test_headless_prompt.py`.

`_chat_cmd` (chat and consultant sessions — `run_chat_turn`) builds its
command independently of `_build_cmd`
and does **not** append this block; those sessions are interactive
conversations, not one-shot task workers, and the guidance about
backgrounded gate commands does not apply to them.

### Append composition under `deny_all_tools` (PA-29 suppression since 2026-07-12; no-tools block since 2026-09-21)

`_build_cmd` also appends `_project_doc_block()` (the "read this project
doc for context" `@`-reference, when a project doc is active — see
`docs/architecture/project_doc.md`) alongside `_HEADLESS_EXECUTION_BLOCK`.
Both instructions are unactionable — and worse, actively harmful — for a
caller that passed `deny_all_tools=True`: that caller has no tools at all
(`--disallowedTools '*'`, via `claude_cli_grants.disallowed_tools_args`), so "read this doc
first" and "run gates to completion / commit your work" both invite a tool
call the worker structurally cannot make. `_build_cmd` assembles the appended
system prompt through `_appended_system_prompt`, which branches on
`deny_all_tools`:

```python
def _appended_system_prompt(system_prompt: str, *, deny_all_tools: bool) -> str:
    if deny_all_tools:
        return f"{system_prompt}\n\n{NO_TOOLS_BLOCK}"
    return f"{system_prompt}\n\n{_HEADLESS_EXECUTION_BLOCK}{_project_doc_block()}"
```

When `deny_all_tools=True`, the headless-execution block and the project-doc
block stay suppressed (`_project_doc_block()` is not even evaluated), but the
caller's prompt is **not** passed to `--append-system-prompt` bare: the
shared `NO_TOOLS_BLOCK` constant, owned by `alan/claude_cli_grants.py` next to
`disallowed_tools_args` and imported by `alan/claude_cli.py`, is appended.
Passing the bare prompt was the defect: a tool-denied invocation was never
told it had no tools, so a role whose own prompt says "ground your verdict by
reading the files" announced it would check the repository, role-played the
tool call in prose (`**Tool Call: Read**` plus a JSON block), frequently
fabricated the tool result too, and stopped before its mandatory
`VERDICT:` line — the failure the weekly judge sweep scored as a wrong verdict
(`docs/architecture/eval_scaffolding.md`, Metric 6).

`NO_TOOLS_BLOCK` states that the invocation has no tools; that the model must
never call, attempt, describe or simulate a tool call and never write a
tool-call or tool-result block; that everything it needs is already in the
prompt; and that it must still emit every mandatory machine-readable line its
role requires. It then carries one strictly conditional clause: *only if* the
role's output contract asks for a judgement grounded in repository evidence,
state plainly that the evidence is absent from this prompt and proceed to give
the judgement; a role that reproduces text verbatim or writes a short summary
must not mention missing evidence or missing tools at all. One constant serves
all five callers rather than a per-caller parameter (which would touch
`run_agent`, `run_agent_streaming`, `_run_cli` and `_build_cmd`) because one
wording cannot drift and the conditional clause is what makes it read
correctly for each: the judge states the evidence is absent and still emits
its verdict; the compactor, `manager_summary`, `collision` and the planner
grader (`behavioral_eval`) produce no "evidence absent" sentence.

This is still a single chokepoint: every deny-all caller funnels through
`_build_cmd` via either `_run_cli` (`run_agent`, non-streaming) or
`run_agent_streaming` (both accept and thread through a `deny_all_tools`
parameter already), so no per-caller change was needed. The deny-all callers
are `alan/judge_eval.py`, `alan/behavioral_eval.py`, `alan/manager_summary.py`,
`alan/collision.py`, and `alan/compression.py`'s Haiku compactor (see
`docs/architecture/compression.md`; `alan/compactor_eval.py` imports that
compactor as `_call_compactor_llm` and is not a sixth site) — all five receive
`NO_TOOLS_BLOCK` and neither of the other two blocks. The suppression half is
pinned per caller as three separate assertions — the append *contains*
`NO_TOOLS_BLOCK`, *does not contain* `_HEADLESS_EXECUTION_BLOCK`, and *does not
contain* the project-doc block — in `tests/test_claude_cli.py`,
`tests/test_claude_cli_streaming.py`, `tests/test_project_doc_injection.py`,
`tests/test_compression_llm.py` and `tests/test_manager_summary.py`, with the
block's wording and once-only composition pinned in
`tests/test_no_tools_block.py`. Collapsing these into one whole-append equality
would discard the PA-29 suppression guarantee and is a gate downgrade.

Callers that do NOT pass `deny_all_tools=True` (every `permission_mode="auto"`
or `"acceptEdits"` team worker) are completely unaffected: the non-deny-all
branch reproduces the prior unconditional assembly byte-for-byte, and the
no-tools block never reaches a caller that has tools.

## Auxiliary one-shot capture seam (since 2026-07-03)

`run_agent(system_prompt, user_text, *, model=..., deny_all_tools=..., stream_path: Path | None = None)`
gained a `stream_path` parameter so one-shot auxiliary calls (e.g. the
collision check, the manager-summary) can have their run activity captured
without changing the behaviour of every other `run_agent` caller.

- `stream_path=None` (the default, and every existing caller) keeps the
  original `_run_cli` → `--output-format json` one-shot path byte-for-byte
  unchanged: a single `subprocess.run`, JSON parsed via `_parse_json`, result
  text extracted via `_extract_result_text`. This is the regression fence —
  no caller behaviour changes.
- `stream_path=<path>` routes through `run_agent_streaming(...,
  deny_all_tools=deny_all_tools, max_turns=3, stream_path=stream_path,
  proc_config=ProcessConfig(timeout_seconds=_cli_timeout_seconds()))` and
  returns `.result_text`, preserving `run_agent`'s bare-string return
  contract. Pinning `proc_config.timeout_seconds` to `_cli_timeout_seconds()`
  (the `ALAN_CLAUDE_TIMEOUT_SECONDS`-overridable, 120 s-default one-shot
  ceiling) keeps the streaming idle deadline at parity with the non-streaming
  path instead of falling back to the streaming path's 600 s
  `ALAN_IDLE_TIMEOUT_SECONDS` default. Still exactly one subprocess — no
  retries, no parallelism.

`run_agent_streaming` also gained `deny_all_tools: bool = False`, threaded
into its `_build_cmd(...)` call alongside the existing `allowed_tools`
argument (`claude_cli_grants.disallowed_tools_args` already handled `deny_all_tools` for the
non-streaming path — see `_run_cli`). This lets a streaming-routed
auxiliary call run with the same "no tools" posture the non-streaming path
uses today.

Because `run_agent_streaming`'s stream sink (`_open_stream_sink`) opens
`stream_path` in append (`"a"`) mode, repeated auxiliary runs that pass the
same `stream_path` accumulate into one file rather than overwriting prior
runs — the file is a per-container-directory run log, not a per-call
scratch file.

## TurnCeilingReached (since 2026-06-14)

`TurnCeilingReached(ClaudeCliError)` is raised when the Claude CLI exits with
`subtype == "error_max_turns"`. The predicate `_is_turn_ceiling(event) -> bool`
checks this subtype. Raises are inserted at three chokepoints, each **before**
the generic `ClaudeCliError`/`ServerErrorRetryable` branch:

| Chokepoint | Path | Notes |
|---|---|---|
| `_maybe_raise_transient` | streaming, rc≠0 | builds message via `_stream_error_parts` (includes `num_turns`, `stop_reason`); also appends `stderr=<snippet>` when stderr is non-empty |
| `_raise_for_error_event` | streaming, rc=0 with error result event | same rich parts; `_is_turn_ceiling` checked **before** `_is_server_error` to prevent misclassification when both markers match |
| `_extract_result_text` | non-streaming (`run_agent`) | message includes `_result_detail` |

`_is_server_error` is the seam a new text signal belongs on precisely because both `_maybe_raise_transient` (rc≠0) and `_raise_for_error_event` (rc=0 with an error result event) consult it — a signal added to either gate's own tuple instead would cover only one of the two exit shapes a failed run can take.

`_maybe_raise_transient(events, stderr="")` accepts an optional `stderr` string;
`_stream_subprocess` passes the collected stderr so crash/OOM text is included in
the exception message when it is present.

Because `TurnCeilingReached` extends `ClaudeCliError` (not `ServerErrorRetryable`),
existing `except ClaudeCliError` handlers still catch it. The implementation team
adds a specific `except TurnCeilingReached` handler that pushes committed WIP before
re-raising — see `docs/architecture/implementation_pipeline.md`.

`alan/claude_cli.py` — `_stream_subprocess` and helpers.

## Concurrent pump (since 2026-06-13)

The streaming read is handled by a **concurrent two-thread pump** to prevent
deadlocks from large stderr output and to enforce wall-clock timeouts without
blocking the main thread on a raw `proc.wait(timeout=…)`.

### Thread model

```
_spawn_streaming_cli()  →  proc (subprocess.Popen)
                              │
               ┌──────────────┼──────────────┐
          _stdout_reader    (main consumer)  _stderr_reader
          (daemon thread)                   (daemon thread)
               │                                 │
          stdout_q (queue.Queue[str|None])   stderr_lines (list[str])
               │                                 │
          shared  last_activity[0]  ←─────────────┘  (both threads update)
               │
          _consume_stdout_queue  (main thread)
```

- **`_stdout_reader`**: iterates `proc.stdout`, updates `last_activity[0]`,
  puts each line on `stdout_q`. Puts `None` sentinel on EOF.
- **`_stderr_reader`**: iterates `proc.stderr`, updates `last_activity[0]`,
  appends to `stderr_lines`. No queue — avoids sentinel-ordering race.
- **Consumer thread (main)**: `_consume_stdout_queue` calls
  `queue.get(timeout=min(idle_remaining, abs_remaining))` in a loop. All
  JSON parsing, event callbacks, sink tee, and session-id capture happen
  here so state is single-threaded.

### Deadline enforcement

Two independent ceilings, both environment-overridable:

| Variable | Default | Purpose |
|---|---|---|
| `ALAN_IDLE_TIMEOUT_SECONDS` | 600 s | Reset on any stdout OR stderr byte |
| `ALAN_ABS_TIMEOUT_SECONDS` | 14 400 s (4 h) | Hard wall-clock ceiling |

`_check_deadlines(start, last, idle_timeout, abs_timeout) -> float`:
- Raises `ClaudeCliTimeout` with "absolute ceiling" if `now - start >= abs_timeout`
- Raises `ClaudeCliTimeout` with "idle for Xs" if `idle_remaining <= 0`
- Returns `min(idle_remaining, abs_remaining)` as the effective `queue.get` timeout

This ensures the absolute ceiling wakes the queue early (not just at idle
expiry). When `queue.Empty` fires, the handler re-checks which ceiling was
hit and raises accordingly.

`ClaudeCliTimeout` inherits `ServerErrorRetryable → ClaudeCliError`, so
it routes through the dispatcher's existing server-error retry path.

### Single kill owner — pipe mode (deterministic pipe closure, since 2026-07-26)

`_stream_subprocess` selects between two consuming loops on a `journal_stream`
parameter: a code-level boolean, default off, threaded only through
`run_agent_streaming` (so only team workers can ever request it — chat turns,
compression, the investigator, and engine-config proposal have no parameter
to express it). This section describes the pipe-mode loop, unchanged since
2026-07-26; the reader-owned-closing invariant and `_abort_child` below scope
explicitly to pipe mode. The next section covers journal mode's analogous,
but structurally different, terminator.

Every exit path of `_stream_subprocess`'s pipe-mode body — normal completion,
non-zero exit, timeout/termination, and failure to open the optional run-log
sink — closes both of the child's output pipes by the time the call returns
or raises, provided the readers reach end-of-stream within
`_REAP_GRACE_SECONDS` of the kill (see "Honest limit" below for when that
proviso fails). This corrects a prior version of this doc, which claimed that
awaiting the reader threads on the normal path "left clean file handles for
garbage collection" — that was the false premise behind a real leak.
Awaiting a thread does not close what it was reading; only the input pipe
(`stdin`) was ever explicitly closed, and both output pipes (`stdout`,
`stderr`) survived until GC finalized them, raising unclosed-file warnings
in the one scheduled job that spawns a real subprocess instead of mocking
`subprocess.Popen`.

**Reader-owned closing.** `_stdout_reader` and `_stderr_reader` each take
`src: IO[str]` and wrap it in `contextlib.closing(src)`; the stream closes
when the reader's own `for line in src:` loop exits — via EOF on the normal
path, or once the killed child's pipe reaches EOF on the abort path.
`_stdout_reader` puts its `None` sentinel only *after* the `with
contextlib.closing(...)` block exits, so by the time the main thread
observes the sentinel, stdout is already closed. A stream is never closed
from a thread other than the one whose reader may still be blocked on
it — doing so would race that reader, trading unclosed-file warnings for
unhandled-thread-exception warnings, the same failure with a new cause.

**`_abort_child` is the single terminator of the pipe-mode pump region.** On
any exception raised while `_consume_stdout_queue` is running (idle timeout,
absolute-ceiling timeout, a malformed stream line, an event-callback
exception — anything `_pump_concurrent`'s `except BaseException` catches),
`_abort_child(proc, readers)` runs `_kill_and_reap(proc)` first — the kill
unblocks both reader threads, since a blocked read is exactly what SIGKILL
resolves — then joins each reader thread bounded by `_REAP_GRACE_SECONDS`,
then re-raises. Descriptors close (via the readers' own
`contextlib.closing`) *before* the exception reaches the caller, not
asynchronously afterwards. If this terminating responsibility ever moves to
a different function, this invariant must be rewritten to match — it must
not silently drift out of sync with the code again.

**The two reader-less bail-outs.** Before `_pump_concurrent` starts, no
reader thread owns either pipe yet, so closing them from the calling thread
is race-free: `_close_child_pipes(proc)` is called directly (no thread
hand-off) in `_spawn_streaming_cli` when the child has no usable `stdin`,
and in `_stream_subprocess` when `open_stream_sink` (`alan/claude_cli_journal.py`)
fails to open the optional run-log sink. `_close_child_pipes` is
None-tolerant (skips `proc.stdout`/`proc.stderr` when either is `None`) so
this same helper is safe to call from journal mode too, where the child has
no pipes at all — see below.

**Honest limit.** The guarantee holds provided the readers reach
end-of-stream within `_REAP_GRACE_SECONDS` of the kill. A grandchild process
that inherits either pipe (e.g. the child forks and hands its stdout/stderr
fd to a longer-lived descendant) keeps that pipe's write end open
regardless of what happens to the immediate child — no amount of design at
this layer can close a descriptor another process still holds open. In that
case `_abort_child`'s `reader.join(timeout=_REAP_GRACE_SECONDS)` times out
without the corresponding pipe ever closing; the call still returns/raises
within the bounded grace, it just cannot prove closure. A related
normal-path limit: if the immediate child itself keeps stderr open past
`_REAP_GRACE_SECONDS` without exiting, the stderr reader's join at the end
of `_pump_concurrent` also times out — that pipe closes only once the
reader eventually reaches EOF, later than the grace bound promises.

### Journal mode's terminator (since 2026-08-14)

Journal mode (`journal_stream=True`) writes the child's stdout straight into
the on-disk stream journal (`workflow.stream_path`, opened append) and its
stderr into a sibling `.err` file in the same per-invocation agents
directory (`alan/claude_cli_journal.py`'s `journal_err_path`), and tails
those files instead of reading pipes. The tailer never replays a prior run:
`open_journal_handles` measures the size of *both* the journal and the
`.err` sibling before the child is spawned, returning an `offset` and an
`err_offset`. `pump_journal` seeks the journal to `offset` before its first
read, so a resumed run's journal never re-surfaces its predecessor's
events, and `JournalSpec.err_offset` carries the same guarantee for stderr
— `read_err_file` seeks to it before reading, so a retried node's stderr
classification is computed over only the current attempt's error text, not
the concatenation of every attempt that has ever written to that `.err`
file. It has no
reader threads to join: `open_journal_handles` opens both files before the
child is spawned, and `_stream_subprocess_journal` closes the parent's two
handle copies immediately after `Popen` returns (via `run_journal_pump`'s
`try/finally`), so the only descriptors outstanding once spawn completes are
the child's own. There is nothing analogous to `_abort_child` to coordinate.

`claude_cli.py`'s `_kill_and_reap` (unchanged from pipe mode) is injected
into `alan.claude_cli_journal.pump_journal` as the `kill` callable:
`claude_cli` owns *how* to kill — SIGKILL plus a bounded reap, identical to
the pipe-mode path — and the pump owns *when*, deciding via `deadline_state`
whenever growth of neither the journal nor the `.err` file has been observed
within the idle window, or the absolute ceiling has elapsed.

**The re-read rule and its bound.** After `pump_journal` returns — whether
via normal exit (the child's own `proc.poll()` resolved) or via a deadline
kill — `run_journal_pump` runs exactly one unconditional post-exit pass:
`_wait_with_grace(proc, kill)`, then `reread_tail` (one bounded read of the
journal starting at the offset `pump_journal` had reached), then
`read_err_file` (one bounded read of the sibling `.err` file starting at
`spec.err_offset`).
`_wait_with_grace` first calls `proc.wait(timeout=_WAIT_GRACE_SECONDS)`; if
the process is still alive when that grace window elapses — the injected
`kill`'s own bounded reap (`_REAP_GRACE_SECONDS`) didn't finish it — it
invokes the same `kill` callable a second time and then waits unbounded,
so a stuck reap cannot hang the post-exit pass indefinitely on the first
bounded wait alone. `read_err_file` reads from `spec.err_offset` rather
than from byte 0, so this pass — like the journal re-read beside it — never
re-ingests a prior attempt's error text. Kill → wait → re-read → read err →
classify is a single consistent snapshot; `_stream_subprocess_journal`
never re-reads a second time. Because the re-read is unconditional rather
than gated on the deadline branch specifically, an external
`ProcRegistry.terminate` cancel — which signals the child directly, outside
`pump_journal`'s own kill path — gets the same treatment: the child's
`poll()` resolves non-`None` on the next loop iteration, `pump_journal`
returns exactly as it would on a normal exit, and the same unconditional
re-read still runs.

**Completion beats the kill.** `_rc_after_pump` inspects
`last_result_event(events)` *after* that re-read: if a terminal result event
landed inside the deadline-kill race window, the worker is judged by that
event — a success result maps to rc 0, an error result to rc 1, and either
way execution falls through to the same `_classify_stream_exit` gates pipe
mode uses — rather than by the kill signal. Only when the re-read still
finds no result event does `_rc_after_pump` raise `ClaudeCliTimeout`.

**The honest limit.** A file has no end-of-file marker, so tailing it can
never *prove* the child has stopped writing — unlike a pipe, which signals
EOF when its write end closes. The single bounded re-read after `proc.wait()`
is the entire mechanism preventing an unbounded tail; the cost is that any
bytes the child flushes after that read are lost. This is the same trade
`_REAP_GRACE_SECONDS` already makes on the pipe-mode kill path, applied here
to a read instead of a wait.

**Deliberately deferred, named — belongs to the flip PR:**
`alan/teams/_base.py`'s `run_or_resume_node`/`WorkerOverrides` wiring (this
change only threads `journal_stream` through `run_agent_streaming`, not any
team); `alan/teams/implementation.py:633`'s drift-backstop resume, which
calls `run_agent_streaming` without a `stream_path` and so cannot select
journal mode regardless; `alan/retention.py:137`'s `*.stream.jsonl` glob,
which does not yet sweep the new `.stream.err` sibling; and the workspace
stream HTTP endpoint as a second consumer of raw (rather than
tee-filtered) journal content.

### `run_chat_turn` vs `run_agent_streaming` idle timeout

- `run_chat_turn` passes `timeout_seconds=_chat_turn_timeout_seconds()` (env
  `ALAN_CHAT_TURN_TIMEOUT_SECONDS`, default 600 s) as the idle deadline.
- `run_agent_streaming` passes `timeout_seconds=_proc.timeout_seconds`; when
  `None` (the common case), `_stream_subprocess` falls back to
  `_idle_timeout_seconds()` (`ALAN_IDLE_TIMEOUT_SECONDS`, default 600 s).

The absolute ceiling (`ALAN_ABS_TIMEOUT_SECONDS`) is always read from the
environment regardless of the idle setting.

### Transient vs terminal classification (three-gate model)

`_stream_subprocess` applies three classification gates in sequence when `rc != 0`:

**Gate 1 — `_maybe_raise_transient(events)`** (existing, unchanged)
Inspects the last `type=="result"` event only. Raises:
- `UsageLimitError` — if the result or a prior `rate_limit_event` indicates a usage/session limit.
- `ServerErrorRetryable` — if `api_status_code >= 500`, `api_error_type` is in
  `_SERVER_ERROR_API_TYPES`, or result text contains a known server-error phrase.

`_SERVER_ERROR_TEXT_SIGNALS` now carries **four** members, not three:
`"overloaded"`, `"internal server error"`, `"service unavailable"`, and
(since 2026-08-21) `"the response stopped arriving"`. The fourth is not a
5xx at all — it is a **transport** condition, the model connection dropping
mid-stream, which carries no HTTP response and therefore no status code to
check under Tier 1. It is deliberately filed under a predicate named
`_is_server_error`, whose raised message still reads `claude CLI stream-json
server error: …`; that naming mismatch is known and accepted, not an
oversight to "correct" by moving the entry elsewhere — the whole point of
this seam is that it is consulted from both exit shapes a failed run can
take (see the chokepoint table above). The dispatcher's response to this
signal (`alan/_dispatcher_usage_limit.py`'s `_handle_server_error`) is a
**bounded automatic re-run from zero** — a fresh `claude` invocation, never
a `claude --resume` — so a long run that retries on this signal starts over
from scratch. That is far better than losing the run outright, but it is a
re-run, not a resumption, and must not be described as "recovery."

**Gate 2 — `_maybe_raise_transient_fallback(events, rc, stderr)`** (added 2026-06-14)
Handles failures that Gate 1 misses because no result event was emitted or
because the signal is in stderr. Applied only after Gate 1 returns without raising.

```
_STDERR_TERMINAL_SIGNALS  — authentication, invalid api key, permission denied,
                             not found on path, no conversation found
_STDERR_TRANSIENT_SIGNALS — connection, network, timeout, ssl, socket,
                             econnreset, econnrefused, etimedout
```

Terminal-signal denylist always wins: if stderr (lowercased) matches any
`_STDERR_TERMINAL_SIGNALS` entry, Gate 2 returns immediately (stays terminal).

Case 1 — **crash-before-result**: `rc != 0` and no `type=="result"` event
emitted. If stderr is not terminal → raises `ServerErrorRetryable`. This is the
DAG-cascade case: a killed/OOM worker was silently terminal before; now it
retries (bounded by `server_error_retry_count`).

Case 2 — **network error with non-error result**: `rc != 0`, a success result
event is present (not `_is_error_result`), and `_is_stderr_network_transient`
detects a transient signal in stderr → raises `ServerErrorRetryable`. Handles
the rare case where the CLI emits a result then crashes while tearing down.

**`ResumeSessionLostError` interop**: "no conversation found" is in
`_STDERR_TERMINAL_SIGNALS`, so a session-lost crash still reaches `_raise_nonzero`
and the `_SESSION_NOT_FOUND_RE` regex in `run_agent_streaming` fires correctly.

**Bounded retries**: Gate 2 raises `ServerErrorRetryable`, which routes through
the dispatcher's existing `server_error_retry_count` budget (max 3 retries +
exponential backoff). No unbounded loops.

**Gate 3 — `_maybe_raise_success_contradiction(events, rc)`** (added 2026-07-04)
Handles the "success-contradiction" shape that Gates 1–2 both fall through on:
`rc != 0`, a final `type=="result"` event with `is_error` truthy, but whose
`subtype` is literally `"success"` — i.e. the CLI's own result event claims
success while the process nonetheless exited non-zero. This shape is produced
when an authentication (401) or connectivity error surfaces on a *later* turn,
after the CLI had already done real work on earlier turns; the process then
exits non-zero but the last-seen result event was captured before the fatal
error updated the subtype. Applied only after Gates 1–2 return without
raising, and only when a result event is present — `_is_success_contradiction`
requires both `is_error=True` and `subtype == "success"`, so this gate never
fires for the ordinary crash-before-result / non-error-result shapes Gate 2
already owns, nor for a malformed or missing-subtype error result that merely
happens to fall outside the `error_`-prefixed set (tightened 2026-07-04 per
review — the original `not subtype.startswith("error_")` check was broader
than the literal-"success" contradiction the design describes and could have
reclassified an unrelated malformed-subtype error as retryable).

```
_RESULT_AUTH_TRANSIENT_SIGNALS — 401, authentication, invalid api key,
                                  invalid credentials, unauthorized
```

The gate inspects the **result event's own text** (`_result_detail`), not
stderr — the observed `ConnectionRefused` incidents live in the result text,
which the stderr-only `_is_stderr_network_transient` matcher never sees.
Classification: `_is_result_auth_transient(detail)` (new tuple above) OR
`_is_result_network_transient(detail)` (reuses the existing
`_STDERR_TRANSIENT_SIGNALS` tuple, matched against the result text instead of
stderr) → raises `ServerErrorRetryable` with a distinct marker string,
`"claude CLI success-contradiction transient (rc=<rc>): <detail>"`. Any other
(non-transient) success-contradiction detail falls through unchanged to
`_describe_stream_error` / `_raise_rich_stream_error`, which raises the
existing terminal `ClaudeCliError` with the `"claude CLI: <subtype>;"` message
shape. Gate 3 does not re-check `_is_server_error(last_result)`: Gate 1 already
evaluates that same `last_result` for a 5xx shape before Gate 3 ever runs, so
a server-error leg here could never be reached — it was dead code, removed
2026-07-04.

The distinct marker is deliberate: `str(exc)` is what the dispatcher stamps
into `job_failure_audit.jsonl`'s `error` field (see `alan/dispatcher.py`,
untouched by this change — no structured audit field was added). On a
successful retry the marker never reaches that file at all; on retry
exhaustion the terminal row reads `"claude CLI success-contradiction
transient (rc=...): ..."` instead of the old `"claude CLI: success;"`
signature, so failure-triage analyses that grep for the old signature no
longer miscount a recoverable transient blip as a terminal failure. Real
observed shapes (`~/.alan/logs/job_failure_audit.jsonl`, `failure-triage-sweep`
task, 7x 401 + 1x `ConnectionRefused`) drove the two signal sets above.

**Narrow auth inversion, preserved**: `_maybe_raise_success_contradiction` only
fires when `_last_result_event(events)` returns a non-`None` event — i.e. the
CLI already emitted a result before the fatal error surfaced. An authentication
error at process *startup*, with no result event ever produced, still falls
through Gate 2's `last_result is None` branch and Gate 3 no-ops (`last_result
is None` short-circuits before the contradiction check), so it still reaches
`_raise_nonzero` and stays terminal. `_STDERR_TERMINAL_SIGNALS`, `_is_server_error`,
and `_is_error_result` are unmodified — this reclassification is additive and
scoped strictly to the contradiction shape.

### Event-time usage-limit raise (mid-stream rejection, since 2026-09-12)

A rejected model rate limit was already parsed: `alan/usage_limit.py`'s
`reset_epoch_from_rate_limit_event` reads a `rate_limit_event`'s
`rate_limit_info.status == "rejected"` and its provider-supplied `resetsAt`,
buffered by `SAFETY_BUFFER_SECONDS`. Before this change that read was reached
only from `_maybe_raise_usage_limit_from_events`, called solely while
classifying a *final* `result` event that is itself an error
(`is_error_result(result_event)`). A rejection that arrives mid-stream,
followed by an internal retry that eventually succeeds, produced no error
result at all — the step completed normally and the wait was silently lost,
even though a slot and a lane seat stayed held the whole time the rejection
was in effect.

The fix acts on the rejection at event time, on the live stream, rather than
waiting for a terminal result. `_StreamPumpState.handle` gained
`_maybe_raise_rejected_rate_limit(parsed)`, called as its last statement —
after `_tee_to_sink` (the run-log line for this event is written first) and
after `on_event` (so the UI/SSE stream sees the event before the abort) —
which calls the same `reset_epoch_from_rate_limit_event` and raises
`UsageLimitError("rate limit rejected mid-stream", epoch)` on a non-`None`
result. Because `handle` is called for every line as it arrives, a rejected
event is now always caught the instant it is read — the terminal-result-path
scan it used to require (`_maybe_raise_usage_limit_from_events`, which
searched the already-collected `events` list backwards for a rejected event
whenever the *final* result was itself an error) became dead code the moment
this landed: every event that reaches that list has already passed through
`handle`'s new check without finding a rejection, by construction, so the
scan could never again find one. `_maybe_raise_usage_limit_from_events` and
its helper `_find_rejected_rate_limit_epoch` were deleted rather than kept
alongside the new check — `_extract_stream_result` and `_maybe_raise_transient`
lost their calls into it accordingly. (`_maybe_raise_transient`'s and
`_extract_result_text`'s own independent text-based detection,
`is_usage_limit_message(detail)`, is a different signal — parsed straight
from the result text, with no dependency on a prior `rate_limit_event` — and
is untouched.)

`handle` is the one line-level seam shared by both the pipe-mode consumer
loop (`_consume_stdout_queue`) and journal mode's tailer (`pump_journal`,
`reread_tail`) — production team workers take the journal pump, not the
concurrent pump, which is why the seam had to be here rather than in either
pump's own loop. The raise propagates as a plain `UsageLimitError` (never
`ServerErrorRetryable`): that class's bounded exponential backoff (three
attempts, 120–600 s) cannot express a wait that may be hours or days out and
would convert a scheduled wait into a failed job. It reaches
`alan/dispatcher.py`'s existing `except UsageLimitError` clause in `_call_team`
by a second call stack — the same handler the terminal-result path already
reached, before this change, via the now-deleted scan — so `_handle_usage_limit`
(`alan/_dispatcher_usage_limit.py`) needs no change: it pauses the queue
globally on the provider's own reset time, interrupts the workflow with
`resumable=team.supports_resume`, and requeues via `requeue_for_resume`
(clearing `eligible_at` to `NULL`, so the wait is carried entirely by the
global pause, not a second future-timestamp mechanism) or `requeue_pending`,
then publishes `queue_paused`. No per-engine pause exists or was added: the
subscription is account-scoped, so one global pause across both engines is
correct.

Because the raise now happens *inside* the reading loop rather than only
after it, `alan/claude_cli_journal.py::pump_journal`'s per-iteration
`_pump_iteration` call is now wrapped in `try: ... except BaseException:
kill(proc); raise`, reusing the already-passed `kill` parameter. Before this
change `pump_journal` had no guard at all: a raise from inside
`consumer.handle` (this new usage-limit raise, or any other exception) would
propagate straight out of `pump_journal`, skipping `run_journal_pump`'s
subsequent `_wait_with_grace(proc, kill)` — abandoning a `start_new_session=True`
detached child that would otherwise keep running (and keep hitting the same
rejection) with nothing left to reap it. `reread_tail`'s own call site
(`run_journal_pump`, after `_wait_with_grace` has already reaped the child)
is deliberately left unguarded — the child is already gone by then, so there
is nothing left for a second kill to do, and a consumer exception raised
during that final re-read pass propagates out of `run_journal_pump`
unguarded exactly as it always has (`tests/test_claude_cli_journal.py::test_reread_tail_call_site_is_unguarded_and_propagates_consumer_errors`
asserts this explicitly). The pipe-mode consumer loop needed no equivalent
change: `_pump_concurrent` already wraps its whole `_consume_stdout_queue`
call in `except BaseException: _abort_child(proc, readers); raise`.

### Failure logging

When the `claude` subprocess fails, `alan.claude_cli` logs both its stdout and stderr at WARNING, truncated to 2000 chars each; the dispatcher's `job failed: … error=<reason>` WARNING line carries the real CLI error text (post-classification), and every terminal job failure appends a structured row to `~/.alan/logs/job_failure_audit.jsonl` (see `queue_and_dispatcher.md`).

### Removed code

- `_pump_stdout`: had a dead `if stdout is None: return` guard that broke
  100% branch coverage.
- `_await_completion`: old wall-clock `proc.wait(timeout=N)` path replaced by
  the idle-deadline pump above.

### Test helpers

- `_BlockingIter` (in test files): `__next__` blocks on `threading.Event.wait()`
  until `proc.kill()` → `release()` unblocks it. Makes stall tests deterministic.
  Also defines a no-op `close()` — required since `_stdout_reader`/`_stderr_reader`
  now wrap their `src` argument in `contextlib.closing`, and every Popen stream
  double (`_FakePopen` et al.) uses `io.StringIO`, not a bare `iter([...])`, for
  the same reason: a list iterator has no `close` attribute at all.
- `ALAN_IDLE_TIMEOUT_SECONDS=0.05` in tests triggers idle timeout in ~50 ms.
- `ALAN_ABS_TIMEOUT_SECONDS=0.05` with `ALAN_IDLE_TIMEOUT_SECONDS=9999`
  triggers absolute ceiling in ~50 ms.
- `tests/test_claude_cli_streaming.py`'s real-subprocess pipe-closure tests
  redirect `subprocess.Popen` to spawn an actual short-lived `python -c` child
  (never a mock) and assert `proc.stdout.closed` / `proc.stderr.closed` via the
  `on_proc_started` hook — the one exception is the missing-stdin bail-out,
  where that hook never fires (`_spawn_streaming_cli` raises before
  `_stream_subprocess` reaches the callback), so that test captures the proc
  from its own `Popen` wrapper instead.
