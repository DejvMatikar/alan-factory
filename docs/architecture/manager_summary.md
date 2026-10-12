# Manager Summary

Automatically produces a short, plain-language paragraph aimed at a manager the moment a workspace stops on its own. The intent is that opening a completed or failed workspace immediately shows what happened and what to do next.

## Trigger conditions

`_schedule_manager_summary` is called by the dispatcher at every **autonomous stop** — transitions into a stopped state that were not initiated by the user:

| Code path | Status set | Synthesis available? |
|-----------|-----------|---------------------|
| `_handle_decision` (await_human / escalate) | `AWAITING_INPUT` / `ESCALATED` | yes (`handover.full_synthesis`) |
| `_advance_workspace_dag` terminal | `COMPLETED` / `FAILED` | yes (handover synthesis if present) |
| `_fail_workspace` (team crash, no tasks) | `AWAITING_INPUT` | no |
| `_surface_routing_error` | `AWAITING_INPUT` | no |
| `_maybe_surface_stall` (DAG stall) | `AWAITING_INPUT` | no |
| `_observe_task_failure` (DAG cascade terminal) | `FAILED` | no |

**Not triggered** by:
- User-initiated stop (`interrupt_task` → `_pause_job`) — the workspace is paused, not stopped autonomously.
- Mid-flight team transitions (the router enqueues the next team; work is still in progress).
- Usage-limit pauses (workspace is not marked as a terminal stop).

If a workspace resumes and stops again, `_schedule_manager_summary` is called again; the new sidecar overwrites the old one.

## Daemon-thread design

Generation is non-blocking. `trigger_manager_summary` starts a daemon thread that calls `_do_generate`. The main dispatcher loop is never blocked waiting for Haiku to respond.

`_do_generate` calls `run_agent` with the Haiku model (`model="haiku"`), `deny_all_tools=True`, and `stream_path=_stream_path(ws_path)`, constructs a user prompt from the task title, status label, optional handover synthesis (truncated to 2000 chars), and optional `stop_reason` for non-synthesis paths. On success it writes the sidecar and calls `on_ready`. On any exception it writes `{"ok": false, "text": null}` and calls `on_ready`.

This is a pure-text task — every input is already in the prompt — so `deny_all_tools=True` makes `run_agent` emit `--disallowedTools '*'`, removing all tools from the model's context. Without it, Haiku could start tool-calling (Read/Glob/…) and exhaust the small `max_turns` budget, returning `error_max_turns` (exit 1) and silently producing no summary. The system prompt also instructs "do not use any tools" as a belt-and-suspenders, and the prompt is not passed to `--append-system-prompt` unmodified: `_build_cmd` appends the shared `NO_TOOLS_BLOCK` (`alan/claude_cli_grants.py`, see `docs/architecture/claude_cli_streaming.md`, "Append composition under `deny_all_tools`"), so the invocation is told outright that it has no tools and must not call, describe or simulate one. The headless-execution and project-doc blocks stay suppressed. The summary is a short sidecar text, so the block's conditional absent-evidence clause does not apply and it forbids mentioning missing evidence or missing tools — no "evidence absent" sentence may appear in the operator-facing paragraph. The redundancy with the prompt's own "do not use any tools" is deliberate.

`on_ready` publishes a `manager_summary_ready` `TeamEvent` via `PubSub` so connected SSE clients can refresh the panel without polling.

## Sidecar format

Written to `{ws.path}/manager_summary.json`:

```json
{"ok": true,  "text": "The feature was added and the PR merged successfully. …"}
{"ok": false, "text": null}
```

`ok: false` is the graceful-degradation state — generation failed or timed out. The frontend still renders the workspace normally; the manager-summary panel shows "summary unavailable" rather than a loading spinner or broken state.

`read_manager_summary(ws_path)` returns `None` when the file is absent or contains invalid JSON — both treated as "not yet available."

## Usage capture (since 2026-07-03)

`_do_generate`'s Haiku call now runs through `run_agent`'s streaming capture
seam (see `docs/architecture/claude_cli_streaming.md`) instead of the plain
one-shot JSON path, so its token/cost activity is persisted alongside the
sidecar:

- `_stream_path(ws_path)` returns `ws_path / "manager_summary_stream.jsonl"`
  — the same workspace directory (`ws_path`) the `manager_summary.json`
  sidecar already lives in two lines below, so the stream file resolves
  through the same `ALAN_WORKSPACES_ROOT`-derived container root as every
  other workspace sidecar and never touches the live user home directory
  when that override is set.
- `read_manager_summary_usage(ws_path) -> RunSummary | None` reads that file
  back via the shared `alan.run_summary.derive_run_summary` reader (frozen
  output shape/aggregation — see `docs/architecture/run_summary.md`),
  returning `None` when no generation has run yet (or the file is empty /
  unparseable).
- This changes *how* the Haiku call executes (one streaming subprocess
  instead of one JSON subprocess) but not *what* it returns: `_do_generate`'s
  success/failure contract, the sidecar format, and the daemon-thread
  non-blocking design below are all unchanged.

## Idempotency: last-write-wins per stop (since 2026-07-03)

`_do_generate` unlinks `_stream_path(ws_path)` (`unlink(missing_ok=True)` —
guarded against the common case of no prior generation) immediately before
calling `run_agent`. This makes each generation attempt start from an empty
stream file instead of appending onto whatever a previous attempt left
behind, so:

- If generation is triggered more than once for the same stop (e.g. a
  duplicate `trigger_manager_summary` call, or a resume-then-stop-again
  cycle — see the trigger table above, where the `manager_summary.json`
  sidecar is likewise last-write-wins), `read_manager_summary_usage` only
  ever reflects the *most recent* generation's spend, never a sum across
  every generation the workspace has ever run. This is what makes the spend
  safe to fold into a total exactly once — see below.
- The daemon-thread design is unchanged: the reset happens synchronously at
  the top of `_do_generate` (already running on the background thread), so
  it adds no retries, parallelism, or blocking wait to the single-subprocess
  path.

## Surfacing (since 2026-07-03)

`ManagerSummary.run_summary: RunSummary | None` (`alan/routes/workspaces.py`)
carries the result of `read_manager_summary_usage(ws.path)` alongside the
existing `ok`/`text` fields on `GET /api/workspaces/{task_id}`'s
`manager_summary`. Its presence tracks the *sidecar*, not the stream: when
`manager_summary.json` is missing, `_fetch_manager_summary` returns `None`
outright and the response's `manager_summary` field is `null` (no
`run_summary` at all — generation has not produced anything for this
workspace yet). When the sidecar exists but fails
`ManagerSummary.model_validate` (degrading to `ok: false`), the object is
still returned and `run_summary` is populated independently from the stream
file if one exists. Read failures (empty/unparseable stream, or any other
exception) degrade to `run_summary: None` — the workspace detail response
never errors or blocks on this.

The frontend is intended to render `manager_summary.run_summary` (model,
tokens, cost) next to the "what happened" paragraph via the shared pricing
display (see `docs/architecture/run_summary.md`); that rendering is the
scope of a sibling subtask and is not part of this backend change.

## Total fold-in (since 2026-07-03)

`GET /api/workspaces`'s `WorkspaceSummary.total_cost_usd` is otherwise the
sum of each invocation's per-task `NodeUsage.total_cost_usd` figures
(`_accumulate_cost`) — it never included the manager-summary Haiku spend,
under-reporting a workspace's true cost. `_fold_manager_cost(base, ws_path)`
(`alan/routes/workspaces.py`) adds `read_manager_summary_usage(ws_path)`'s
`total_cost_usd` on top of that base figure and is wired into
`_compute_invocation_data`, so every row in the list response folds it in.

This is double-count-proof by construction rather than by bookkeeping: there
is no persisted rollup of manager-summary spend anywhere — `_fold_manager_cost`
re-reads the (last-write-wins, per the idempotency section above) stream file
on every call, so a workspace's fold-in figure is always derived fresh from
exactly one generation's usage, never accumulated across repeated calls or
requests. A workspace with no manager summary yet (`read_manager_summary_usage`
returns `None`, or the stream is unreadable — any exception is swallowed and
logged) folds in nothing and its total is unchanged.

## Graceful degradation

The frontend calls `GET /api/workspaces/{task_id}` which includes `manager_summary` from the sidecar in the response. If the sidecar is absent or `ok: false`, the panel renders a fallback message and the rest of the page is unaffected. The detail view is never blocked on generation. The same holds for `run_summary` and for the list endpoint's cost fold-in: missing, zero, or unreadable usage data degrades to `None` / the unmodified base cost — it never raises, and the workspace stop itself always completes regardless of what this reads.
