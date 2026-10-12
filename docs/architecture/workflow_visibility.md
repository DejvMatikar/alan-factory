# Workflow visibility

**Source:** `alan/workflow.py`, `alan/claude_cli.py` (`run_agent_streaming`), `alan/teams/_base.py` (`workflow_lifecycle`, `run_or_resume_node`, `MAX_TURNS`), `alan/teams/_node_launch.py` (node-config persistence and the launch recorder), `alan/node_launch_record.py` (the `launch` record shape).

Kestra-flavored declarative record of what each invocation ran. Visible end-to-end in the frontend DAG view (see [frontend.md](frontend.md)).

## On-disk shape

Per invocation:

```
~/.alan/workspaces/<task_id>/invocations/inv_NNN/
  workflow.yaml
  agents/<role>.stream.jsonl   # raw Claude stream events, one JSON object per line
  agents/<role>.result.json    # cached final `result_text` (+ role, model, completed_at)
  agents/<role>.config.json    # persisted node config: system_prompt, user_text, allowed_tools, max_turns, permission_mode, model, turns_source, derived_max_turns, and (since 2026-10-04) the nested `launch` record
```

The `.result.json` cache is written by `_run_fresh_node` / `resume_workflow_node` immediately after a node completes successfully. `_base.run_or_resume_node` consults the cache to skip already-succeeded nodes during multi-node resume — see [queue_and_dispatcher.md § Multi-node resume](queue_and_dispatcher.md#multi-node-resume-pr3). Cache writes are atomic (`.tmp` + rename); a missing/malformed cache is treated as "not cached" by `load_node_result`.

## The `launch` record inside `*.config.json` (since 2026-10-04)

`_prepare_node_context` still writes the flat config (the full augmented prompt, `user_text`, the allowed tools as
passed, `max_turns`, `permission_mode`, `model`, the turn-budget source) **before** the subprocess starts. The
launch itself is recorded afterwards: `_stream_node` passes `on_spec=_node_launch.launch_recorder(...)` to
`run_agent_streaming`, which calls it with the final `LaunchSpec` immediately before spawning, and the recorder
adds a nested `launch` object to the same file (`workflow.save_node_launch`, atomic, `0o600`). The key is
`NotRequired` in `NodeConfigDoc`, so every record written before this change still parses and simply has no
`launch` (the run view shows "not recorded for this run" rather than a guessed value).

`launch` (`alan/node_launch_record.py::LaunchRecord`) stores: `agent` (the catalogue id, e.g.
`team_role:verification/judge`), `model`, `effort` — the value the CLI actually received, resolved from the role
default when the team passed none — `max_turns`, `permission_mode`, `prompt_segments` (`label`/`text`/`shared`
for the role prompt, the `turn_budget` line and each appended tail block; **segments only**, never the joined
string), the effective `allowed_tools` (after narrowing) and `denied_tools`, `add_dirs`, `deny_all_tools`,
`session_mode` (`new`/`resumed`), `cwd`, the idle and absolute timeouts, `fleet`, `detached`, `priority_delta`
and `env` — environment variables by **name and category only**; no value, token or secret is ever stored.
`parse_launch` drops a malformed `launch` so a corrupt record can never hide the rest of the config. Malformed includes a missing field, an **unknown** field at the top level or inside a prompt segment or environment entry (so nothing beyond the documented shape — in particular no stray value — ever reaches the config API), a `session_mode` outside `new`/`resumed`/`none`, and an environment `category` outside the `launch_spec.EnvCategory` values. The key set is exact in both directions, so adding a field to `LaunchRecord` later makes every record written before that change parse as "not recorded for this run": such a change must either keep the new field optional in `parse_launch` or ship a migration of the stored configs.

Rules: attaching to a surviving worker (`_node_attach.attach`) never spawns, so it writes nothing; a resume goes
through `resume_workflow_node` → `_prepare_node_context` → `run_agent_streaming` again, so the config is rewritten
and `launch` is replaced with the resumed launch (`session_mode == "resumed"`); an `on_spec` failure is logged and
never aborts the node. `alan/teams/_base.py` is pinned shrink-only in `tests/size_baseline.py`, which is why
`_PreparedNodeContext`, `_persist_node_config` and the recorder moved into `_node_launch.py` (`_base` keeps a thin
`_prepare_node_context` wrapper because the existing tests call it by that name).

`create_invocation_dir` reserves the next `inv_NNN` slot with `O_EXCL` (same pattern as handover slot claim).

Path-segment validation: `inv_id` must match `^inv_\d{3}$`; node ids must match `^[a-z][a-z0-9_]{0,63}$` (path-traversal defense). Orchestrated teams additionally reject orchestrator-emitted roles that collide with reserved names (`orchestrator`, `synthesizer`) or duplicate each other.

## `WorkflowDoc` / `WorkflowTask`

Typed pydantic. `WorkflowDoc` carries `schema_version` (currently `"1.7"`; legacy `"1.0"`–`"1.6"` docs still load), top-level `status` (`pending` / `running` / `success` / `failed` / `interrupted`), `slot_index: int | None` (the slot-pool slot this invocation leased, so a resumed `implementation`/`implementation_fix`/`verification` job re-leases the same cwd that holds its `claude` session — see [slot_pool.md](slot_pool.md#sticky-slot-affinity-on-resume-preferred_idx--workflowdocslot_index); `None` outside the pool, 1.6+), `tasks: list[WorkflowTask]`.

The `"interrupted"` top-level status (schema 1.4) is written by the user-pause path when a task is stopped: `interrupt_workflow_on_disk(..., recover_finalized=True)` sets the doc status to `"interrupted"` instead of `"running"`, so the frontend InvocationCard correctly shows the invocation as stopped rather than running. The startup reconciler (which uses `recover_finalized=False`) still sets `"running"` so its resume flow is unaffected.

`WorkflowTask`:

- `id` — unique within the workflow.
- `type` — `io.alan.LLMStep` for real LLM steps, `"none"` for the no-LLM proxy node.
- `role`, `model`.
- `effort: str | None` — the Claude `--effort` the CLI receives for this node (1.3+). A team that passes none gets the model-derived default (`launch_spec.effort_for_model`: opus→`xhigh`, sonnet→`high`, haiku→none), and since 2026-10-04 `_base._start_node_locked` records that *resolved* value (`launch_spec.resolve_effort`) instead of leaving `None` — a node row never shows a blank effort for a model that has a default. Teams that need another effort — currently `implementation`/`implementation_fix` (`sonnet`@`xhigh`) — pass it explicitly. Persisted so the value survives resume. `WorkflowGraph.start_node` itself still stores exactly what it is given (`tests/test_workflow.py` pins that), so the fix lives at the team-node call site.
- `depends_on: list[str]` — edges in the DAG.
- `status` — `pending` / `running` / `success` / `failed` / `interrupted` (the `interrupted` value is set by the reconciler for nodes whose `claude_session_id` survived a restart; `resume_node` flips it back to `running`). A `running` node with no `claude_session_id` never becomes `interrupted` — see the `interrupt_workflow_on_disk` entry below for where it goes instead.
- `started_at`, `completed_at`.
- `stream_path` — path to the raw `.stream.jsonl` for this node.
- `dispatched` — bool.
- `error: str | None` — populated on failure (Phase 4).
- `claude_session_id: str | None` — the claude session UUID for this node (1.1+). Persisted as soon as `claude` emits its first `system/init` event, so a restart mid-stream still has a recoverable session.
- `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens: int | None`, `total_cost_usd: float | None`, `usage_raw: dict | None` — Claude usage block captured from the terminal `type=result` event (1.2+). All default to `None`; last-write-wins on resume (no accumulation across interrupted runs). See **Usage capture** below.

## Usage capture (schema 1.2+)

`run_agent_streaming` returns `AgentStreamingResult` (not a bare `str`). The `.usage` field is a dict extracted from the terminal `type=result` event's nested `usage` block, plus the top-level `total_cost_usd` key that the Claude CLI emits alongside it. `_base._node_usage_from_dict` maps this dict to a `NodeUsage` frozen dataclass; `WorkflowGraph.complete_node(usage=…)` writes all six fields atomically into the task row in the same save pass that marks the node `success`.

The frontend `WorkflowDAG` node card renders a usage row (`↑<in> ↓<out> $<cost>`) when `input_tokens` or `output_tokens` is non-null, using `formatUsageRow` from `streamSummary.ts`. The `summarizeResult` summary line also shows `↑<in> ↓<out>` via `formatUsageSuffix`.

## Active intervals feeding `alan.fleet_timing`

`WorkflowRowSummary` (the small frozen `(team, status, cost)` record `load_workflow_summary_cached` returns, next to `load_workflow_cached` — see `docs/architecture/workspace_index.md`'s "Refresh cycle" for how the workspace index consumes it) carries an `active_intervals: tuple[ActiveInterval, ...]` field (`ActiveInterval = tuple[datetime, datetime | None]`, `alan/workflow.py`) alongside `team`/`status`/`cost`. Each element is one non-sentinel node's raw `[started_at, completed_at)` window, derived by `_doc_active_intervals`/`_node_active_interval` (`alan/workflow.py`) at the exact point `_doc_row_summary` also computes `cost` — so it rides the same mtime+size doc cache as everything else in `WorkflowRowSummary`; no new cache, no new disk read.

A node contributes no interval when its `model == SENTINEL_MODEL` (`"none"` — the deploy_verify/pr_merge/triage/dependency_sweep no-LLM proxy nodes) or when it never started (`started_at is None`, i.e. still `pending`). A node with both `started_at` and `completed_at` set contributes its own closed interval verbatim. A node with `started_at` but no `completed_at` is either clamped to the *doc's* own `completed_at` (set by `finalize()`) when the doc itself is terminal — the node crashed or was interrupted without reaching its own `complete_node`/`fail_node` call — or left genuinely open (`(started_at, None)`) when the doc is still `running`/`pending`.

`alan/fleet_timing.py` is the sole consumer of `active_intervals`: it merges them (within one doc, and across every invocation of a workspace) into the union active-fleet-time figure surfaced on `GET /api/workspaces`. See `docs/architecture/workspace_index.md`'s "Active fleet time and lead time" section for the full merge algorithm and the `WorkspaceRow` shape it feeds, and `docs/architecture/deploy_verify.md` for where the union gets settled into `Meta.settled_active_seconds` at delivery confirmation.

## `WorkflowGraph`

Mutation API: `start_node`, `complete_node`, `fail_node(node_id, *, error=…)`, `set_session_id(node_id, sid)`, `set_dispatched(node_id, roles)`, `mark_interrupted(node_id, *, error=…)`, `resume_node(node_id)`, `finalize`. `mark_interrupted` flips a `running` node to `interrupted` (preserves `claude_session_id`); `resume_node` flips an `interrupted` node back to `running` and clears `completed_at` + `error`. `set_dispatched` records the worker roles an orchestrator emitted — used by `OrchestratedTeam._phase_orchestrate` after `run_or_resume_node` so the value is available on subsequent resumes. The reconciler uses `mark_interrupted` indirectly via `interrupt_workflow_on_disk(..., resumable=True)`.

Save is whole-file atomic (`.tmp` + `os.replace`). Re-saved after every transition so a reader (or a crash) always sees a consistent snapshot. `WorkflowGraph.from_doc` flips top-level `pending → running` when a pre-seeded graph is loaded.

`start_node` on a node already present in a pre-seeded pending workflow flips its status `pending → running` instead of raising the old "duplicate node id" error.

Module-level helper `reset_failed_nodes_for_rerun(workspace, inv_id)` is used by the dispatcher's `_fail_genuine` to prepare a failed invocation for re-entry on the recoverable-failure path. It loads the workflow, resets every `failed` or `running` node to `pending` (clearing `error`, `started_at`, `completed_at`, `claude_session_id`), and saves atomically. Success nodes are left untouched so `run_or_resume_node` can return them from cache. Catches `(WorkflowError, OSError)` so a disk error never propagates into the worker thread. A no-op when no nodes need resetting (no write occurs).

`StaticPipelineTeam._parse_or_invalidate(workspace, inv_id, parse_fn, synthesis)` — post-success cache invalidation seam used by `implementation_plan`, `architecture`, and `decomposer`. A pipeline node is finalized as `success` and its result cached by `run_pipeline`, *then* the team calls a team-specific parse function (decisions YAML / decomposition) on that output. If the parse raises `StaticPipelineParseError`, the helper:
1. Calls `invalidate_node_result(workspace, inv_id, self.roles[-1].name)` first — this is best-effort and runs even if the steps below fail.
2. Loads the graph fresh with `load_graph` and calls `fail_node` on `roles[-1]`, then saves — transitions the node from `success` → `failed` so `reset_failed_nodes_for_rerun` can reset it to `pending` on the next retry.
3. Re-raises the `StaticPipelineParseError` so the dispatcher sees a job failure.
If `load_graph` itself raises `WorkflowError` (e.g., corrupt or missing workflow file), the exception is swallowed — step 2 (the `fail_node` transition) never runs, so the node's on-disk status stays `success` while its cache file is gone. `_cached_result_for` reads `load_node_result_doc`, not the older `load_node_result`: on a `success` node whose result document cannot be read back it **raises `WorkflowError`** naming the node and invocation, rather than returning `None`/`""` and letting `run_or_resume_node` silently fall through to a fresh re-run. This is a correction to a previous version of this doc, which described `_cached_result_for` as returning `None` here — the code at the time actually returned `""` (via `load_node_result(...) or ""`), and at these two sites (`clause_auditor_retry`, `judge_retry`) that silent empty string degraded every audited clause to `UNVERIFIABLE` or stripped the judge verdict entirely: a quiet wrong answer, not a crash. The step-1 cache deletion is still what closes the infinite-Resume loop — a cached bad output can never be replayed — but it now does so by making the next resume fail loudly instead of quietly re-running or quietly returning empty output. `_parse_or_invalidate`'s own mainline path (where step 2 succeeds) is unaffected: `fail_node` flips the node to `failed` before any resume can reach `_cached_result_for`'s success branch at all, so the status check short-circuits to `None` first and the raise is never reached there.

`interrupt_workflow_on_disk(workspace, inv_id, *, error, resumable=False)` now lives in `alan/workflow_interrupt.py`, not `alan/workflow.py` — it sits in the import-linter layers list immediately above `workflow` and below `fleet_timing`, since it needs `WorkflowDoc`/`WorkflowTask`/`load_workflow`/`save_workflow`/`workflow_path` downward from `workflow` and `Workspace` downward from `workspace`; `reconciler`, `dispatcher`, and `_dispatcher_usage_limit` all import it from there. It is used by the [reconciler](queue_and_dispatcher.md#restart-resilience) to load a workflow without flipping its top-level status to `running`, then mark its `running` nodes terminal. When `resumable=False`, every running node flips `failed` and the workflow itself flips `failed`. When `resumable=True`, running nodes that already have a captured `claude_session_id` flip to `interrupted` (workflow stays `running`); a running node with no session id is **deferred**, not flipped immediately — it is settled only after every node in the doc has been classified, against whether the doc has any `interrupted` node at all. With at least one sibling `interrupted`, the deferred node goes to `pending` instead of `failed`: `error` is set to the same interrupt marker, and `started_at`/`completed_at`/`claude_session_id` are cleared, mirroring `reset_failed_nodes_for_rerun`'s reset shape. Re-entry then goes through `start_node`'s existing `pending → running` affordance instead of the fresh-node path that raises `duplicate node id` on anything but `pending`. With no interrupted sibling — the lone-killed-step case — the deferred node still flips `failed` exactly as before; a run's eligibility for recovery is not widened by this, only which specific node inside an already-recoverable stage gets to resume alongside its sibling. This is what makes a parallel stage (e.g. `verification`'s `("diff_reviewer", "scenario_runner")`, `implementation_plan`'s `("frontend_analyst", "qa_planner")`) recover as a whole when only some of its nodes had reported a session id before the process died, rather than the surviving node's `interrupted` status routing to resume while the sessionless node's stale `failed` status crashes re-entry. The settled node still renders with the neutral `pending` tint in the frontend — the "this was attempted once" signal is an accepted display cost, not something a new state or field exists to preserve. Returns `None` if the file is missing/malformed; a no-op (no write) if there was nothing to interrupt.

Genuine node failures — a node that ran and actually errored — are written by `_finalize_failed`/`fail_node` in `alan/teams/_base.py`, a wholly different call path that never goes through `_transition_running`/`interrupt_workflow_on_disk` at all. The two classes of `failed` node are therefore distinguishable by which write path produced them, never by inspecting the `error` string — the pause paths (`Dispatcher._pause_job`, `_handle_usage_limit`) write arbitrary exception text into the same field, so any textual rule would misclassify a real fault as a retryable interruption.

## Streaming primitive

`claude_cli.run_agent_streaming(system_prompt, user_text, *, model, effort=None, on_event, stream_path, max_turns, cwd=None, allowed_tools=None, permission_mode="auto", session_id=None, on_session_id=None, resume_session_id=None)`:

- Spawns `claude -p` via `subprocess.Popen` with `--output-format stream-json --verbose --max-turns <n> --append-system-prompt …`.
- When `session_id` is set, appends `--session-id <uuid>` to the command (caller pre-generates `uuid.uuid4()`).
- When `resume_session_id` is set, the command instead uses `--resume <session_id>` (takes precedence over `--session-id`). If `claude` reports `"No conversation found with session ID …"`, the wrapper raises `ResumeSessionLostError` (a `ClaudeCliError` subclass) so callers can distinguish "session lost" from generic CLI errors. `_base.resume_workflow_node` is the entry point for resuming an `interrupted` node — it reads the session id from `WorkflowTask.claude_session_id` and threads it through.
- Pumps stdout line-by-line as the subprocess emits each JSON event.
- Appends each raw line to `stream_path` (flushed per line so readers see incremental progress).
- Calls `on_event(parsed)` immediately per event (not buffered until exit).
- When `on_session_id` is set, fires it **once** the first time a `{"type":"system","subtype":"init","session_id":"…"}` line arrives — synchronously, before the next event is parsed. `_base._run_fresh_node` wires this callback to `WorkflowGraph.set_session_id(node_id, sid)` + `save_workflow`, so the session_id is durable on disk before any restart can kill the subprocess. Exceptions raised by the callback are logged and swallowed; they never abort the in-flight agent.
- Final result extracted from the terminal `type=result` line.
- Optional `cwd` (used by ImplementationTeam to run inside a worktree).
- Optional `allowed_tools` (when set, `_build_cmd` appends `--allowedTools "<space-joined>"`; when `None`, the worker inherits the operator's `~/.claude/settings.json` allowlist).
- `permission_mode` defaults to `auto`; ImplementationTeam overrides to `acceptEdits` (auto + `Edit` allowlist requires per-action approval that can never arrive in headless `-p`).

Timeout: `ALAN_CLAUDE_TIMEOUT_SECONDS` (env, default 120; blank/non-numeric/non-positive → 120). Applied to both `run_agent` (one-shot) and `run_agent_streaming`. On timeout the subprocess is killed; on nonzero exit, stderr is captured and surfaced.

## Turn budget

`alan.teams._base.MAX_TURNS = 150` — default ceiling applied to every role and team that doesn't override it. It is a runaway guard, not a tuning knob. (Temporary exceptions: `implementation` overrides to 500 and `implementation_fix` to 300 via module-local `_MAX_TURNS` constants — see `docs/architecture/implementation_pipeline.md`.)

`_max_turns_for_run(opts)` returns `opts.max_turns` (if explicitly overridden via `WorkerOverrides`) or `MAX_TURNS`. Every standard workflow-node call passes it through `_base.run_or_resume_node`; the drift-gate backstop `implementation._backstop_resume_attempt` (`alan/teams/implementation.py:651-670`) is a specialized direct path that calls `run_agent_streaming` itself and supplies its own `_BACKSTOP_MAX_TURNS`, bypassing `run_or_resume_node` entirely.

## Phases

### Phase 1 — every team writes workflow.yaml

`workflow_lifecycle(workspace, *, team_name, inv_id)` (context manager in `_base.py`):

- Loads the pre-seeded `inv_NNN` graph (sets top-level status to `running`).
- On clean exit → finalizes top-level `status="success"`.
- On exception → captures `str(exc)`, marks any still-`running` node `failed` via `fail_node(..., error=…)`, finalizes top-level `status="failed"`, saves, re-raises.

`_run_fresh_node(...)`: `start_node` → save → emit `node_started` → `save_node_config(…)` → `run_agent_streaming(stream_path=…)` → `complete_node` → save → emit `node_completed`. Config is written before `run_agent_streaming` so it is present even when the agent fails. `_run_fresh_node` is reachable only from `run_or_resume_node`'s fresh arm — it is not a public name on `alan.teams._base`, so a new retry step spawned by a future author inherits resume-safety without needing to know this entry point exists.

### Phase 3 — pre-allocated pending workflow.yaml

The dispatch endpoint and the dispatcher's router both go through `alan.dispatcher.enqueue_with_pending_workflow(queue, pubsub, workspace, team, prior, parent_handover_id=None)`.

That helper:

1. Calls `team.plan_workflow(workspace, prior) -> list[PendingTaskSpec]` — a new method every Team implements:
   - `SingleAgentTeam` → one pending node.
   - `StaticPipelineTeam` / `ImplementationPlanTeam` → the role chain (`ImplementationPlanTeam` returns its 6-node chain).
   - `ResearchTeam` → just the orchestrator (workers are added dynamically at runtime).
   - `PrincipalProxyTeam` → one no-LLM node.
2. Calls `workflow.seed_pending_workflow(workspace, team, tasks)` to claim `inv_NNN` with `O_EXCL` and write a workflow.yaml whose `status="pending"` and every `WorkflowTask.status="pending"`.

The job row carries `triggered_by_invocation_id=inv_id`. The dispatcher reads that in `_resolve_inv_id` and passes the pre-allocated `inv_id` to `team.run(workspace, prior, emit, *, inv_id)` — a Protocol change so every Team now takes the keyword arg. A defensive fallback in `_resolve_inv_id` seeds on the fly if a row is missing the column (legacy enqueues).

Dispatch endpoint publishes a new SSE event `workflow_created` (payload `{inv_id, team, job_id}`) and returns `{job_id, invocation_id}`. The answer endpoint goes through the same helper so resolving a blocking question pre-creates the resolver team's pending workflow too.

Frontend `useLiveWorkspace` includes `workflow_created` in `WORKSPACE_LIFECYCLE_KINDS` so the Workspace page refetches `listInvocations` and a pending `InvocationCard` appears in the Timeline immediately on click (no longer waiting for the first `node_started`). Pending invocations hide the inv-level duration; the per-node `NodeStatusBadge` renders the `pending` style.

Tests pass through `tests/_helpers.seed_and_run(team, ws, prior, emit)` which mirrors the production seed-then-run flow.

### Phase 4 — per-node error capture

`WorkflowTask.error: str | None`. `WorkflowGraph.fail_node(node_id, *, error=…)` writes it. `workflow_lifecycle` uses `try/except/else` (not just `try/finally`) so it can capture `str(exc)` and pass it to `_mark_running_nodes_failed → fail_node(error=…)` before re-raising. `ResearchTeam`'s own try/except path does the same: when a worker blows up, the orchestrator's `success` status is preserved and only the running node flips to `failed` with the error string.

Frontend `WorkflowDAG` renders the error inline (small red text) AND as a `title=` tooltip on failed nodes; `StreamPanel` accepts an optional `nodeError` prop and pins a red footer "node failed: <message>" at the bottom, fed by `selected.status === "failed" ? selected.error : null` from `InvocationCard`.

### Phase 8a — visibility itself

Every workflow event published through `PubSub` carries the `inv_id` so frontend parsers (`parseTaskFromEvent`, `parseStreamPayload`) can route it to the right invocation:

- `stream` → `TeamEvent("stream", {"inv_id": str, "role": str, "event": str})` where `event` is the JSON-encoded raw Claude stream event (the `ResearchTeam` `json.dumps`es it before publishing).
- `node_started` / `node_completed` → `TeamEvent(kind, {"inv_id": str, "role": str, "node": str})` where `node` is `model_dump_json()` of the `WorkflowTask` snapshot.

Emitted by `emit_node` / `stream_emitter` in `alan/teams/_base.py`. The workspace SSE stream gets live updates through this fanout.

## Read-side API

- `GET /api/workspaces/{task_id}/invocations` — list `inv_NNN` ids.
- `GET /api/workspaces/{task_id}/invocations/{inv_id}` — parsed `WorkflowDoc`.
- `GET /api/workspaces/{task_id}/invocations/{inv_id}/agents/{role}/stream` — parsed list of raw Claude stream events; blank and non-JSON lines are skipped, non-object JSON lines filtered out.
- `GET /api/workspaces/{task_id}/invocations/{inv_id}/agents/{node_id}/result` — parsed `NodeResultDoc` (role, model, result_text, completed_at); 404 when the `.result.json` is absent.
- `GET /api/workspaces/{task_id}/invocations/{inv_id}/agents/{node_id}/config` — parsed `NodeConfigDoc` (system_prompt, user_text, allowed_tools, max_turns, permission_mode, model, turns_source, derived_max_turns and, when recorded, the nested `launch` record described above); 404 when the `.config.json` is absent.

The `workflow` slot sits in the import-linter layer stack between `handover` and `workspace`. `workflow_interrupt` sits directly above `workflow`, and `fleet_timing` (`alan/fleet_timing.py`) sits directly above that, between `handover` and `workflow_interrupt` — `fleet_timing` is the sole consumer of `WorkflowRowSummary.active_intervals` and imports nothing else from `alan.workflow`.

## Dispatch-instructions read-back

`alan/dispatch_instructions.py` answers "did my dispatch instructions reach this run, and what were they?" purely by reading state the write path already records — no new store, column, or index. `resolve_dispatch_instructions(queue, workspace)` builds the map two steps:

1. `_jobs_by_invocation` walks `Queue.list_jobs_for_workspace(workspace.task_id)` (id-ascending, the existing method) and folds every row with a non-`None` `Job.triggered_by_invocation_id` into a `dict[inv_id, Job]`, last write wins. `triggered_by_invocation_id` is not "the invocation that triggered this job" despite the name — `enqueue_with_pending_workflow` (`alan/_dispatcher_base.py`) and every direct `queue.enqueue` call for a Phase-3 dispatch set it to the job's *own* pre-allocated `inv_id` at enqueue time, alongside `instructions`, so the two columns are written atomically together. The fold exists to stay correct if a future path ever lets two job rows reference the same invocation; under today's `O_EXCL` invocation-dir allocation that never happens.
2. For every invocation id `workflow.list_invocations(workspace)` finds on disk (the same listing `GET /{task_id}/invocations` uses), the resolver looks up the joined job and classifies it into one of four statuses, never touching prompt composition (`alan/teams/_base.py::format_dispatch_instructions`) or dispatch itself:
   - `attached` — `Job.instructions` is non-empty and `Job.team` is not in the pinned `_NON_CONSUMING_TEAMS` set, so the team's `run()` spliced it into the worker prompt. `text` carries the exact stored string.
   - `none` — a job row exists but `Job.instructions` is falsy (covers both "nothing was ever attached" and blank input normalised away by `normalize_instructions`, `alan/app/workspace_creation.py`, before it ever reaches `queue.enqueue`).
   - `not_delivered` — `Job.instructions` is non-empty but `Job.team` is in `_NON_CONSUMING_TEAMS` (`pr_merge`, `triage`, `dependency_sweep`, `deploy_verify` — teams whose `run()` never splices `instructions` into an LLM prompt, either because they have no LLM step or, for `deploy_verify`, because the field is repurposed as a JSON `GateRecord` for audit rather than prompt text). `text` still carries the recorded string so a caller can show what was typed even though it never reached a worker. Growing this set is a behavioural claim about a team's `run()`, so it is pinned and canary-tested rather than derived.
   - `unknown` — the invocation exists on disk but no surviving job row references it (e.g. queue rows purged independently of the workflow directory).

`GET /api/workspaces/{task_id}/dispatch-instructions` (`alan/routes/dispatch_instructions.py`) exposes this as a whole-workspace map (`inv_id -> {status, text}`); there is deliberately no `inv_id` query parameter or path segment, because a literal `.../invocations/instructions` path would be swallowed by the pre-existing parameterised `GET /{task_id}/invocations/{inv_id}` route in `alan/routes/workspaces.py` (Starlette matches routes in registration order, and `{inv_id}` matches any literal segment). An unknown or unreadable `task_id` returns `200 {}` rather than 404, so a caller can degrade instead of blanking — the route catches `workspace.WorkspaceError` itself rather than using the shared `read_workspace_or_404` helper other invocation routes use.
