# Chat-driven workspace creation

> **Fully removed (2026-06-09):** The frontend entry point (`WorkspaceCreate.tsx`, `useChatSession.ts`, `TaskProposalModal.tsx`, `WorkspaceProposalPreview.tsx`, `ResumeChatPanel.tsx`) and the home-screen "Create new task" CTA were removed on 2026-06-02. The backend (`/api/chat/*` routes, `alan/routes/chat.py`, legacy helpers in `alan/chat.py`, `reconcile_chat_sessions_on_startup` in `alan/reconciler.py`, and legacy deps in `alan/routes/_deps.py`) was fully removed on 2026-06-09. Build Chat (`/build`) is now the creation front door.
>
> **Repository binding does not apply here:** the `engine_key` repository-binding invariant added to `alan._chat_core.ChatSession` (required at create time on build-chat/explore-chat, backfilled on legacy records — see `build_chat.md`'s "Repository binding" section) has no bearing on this doc's `ChatSession`, which belonged to the already-deleted `alan/chat.py` module and never carried the field.

**Source:** `alan/chat.py`.

Replaces the legacy `/api/improve-prompt` rewrite + form template flow. The legacy endpoint + `claude_cli.improve_prompt` + `claude_cli.SYSTEM_PROMPT` + `TASK_DESCRIPTION_TEMPLATE` are deleted. `claude_cli.format_user_text` stays (still used by `implementation_plan` and the orchestrated/static team pipelines).

## Session persistence

Per-session transcripts under `~/.alan/chat-sessions/<uuid>/{session.json, stream.jsonl}`.

`ChatSession` also carries an optional `in_flight_turn: {started_at, user_text}` marker (set in `send_chat_message` via `append_user_message_and_mark_started`, cleared in `_run_chat_turn_background` via `append_assistant_and_clear_in_flight` or `_clear_in_flight_best_effort` — always before the terminal `assistant_done`/`error` SSE event is published). Both transitions are single atomic writes so a partial-failure can never leave a half-written transcript with no marker (or vice versa).

`reconciler.reconcile_chat_sessions_on_startup` (called from `lifespan` *unconditionally* — outside the `ALAN_DISABLE_DISPATCHER` gate so chat survives even when the job dispatcher is off) appends a synthetic "alan was restarted while I was preparing a reply" assistant reply for any session with `in_flight_turn != None`, in a single atomic write. The frontend uses the `in_flight_turn` field returned by `GET /sessions/{id}` to set `isAssistantThinking` (and `queue.thinkingRef.current`) on initial load, so a refresh mid-turn shows the spinner and routes any new user message through the queue path.

A `GET /api/chat/sessions` listing endpoint (`chat.list_sessions`, capped at 25, sorted by `created_at` desc, corrupt dirs skipped) plus `ResumeChatPanel` on the home page lets users recover sessions whose URL/tab they closed.

UUID v4 is generated server-side at `create_session()` and passed unchanged to Claude as `--session-id` (first turn) / `--resume` (subsequent turns) via:

```python
claude_cli.run_chat_turn(
    *, user_text, session_id, resume, model, max_turns,
    allowed_tools, on_event, stream_path, cwd
) -> ChatTurnResult
```

The CLI runs with a read-only allowlist: `Read Grep Glob WebSearch WebFetch Bash(git log:*) Bash(git diff:*) Bash(ls:*) Bash(cat:*) Bash(gh pr list:*) Bash(gh pr view:*) Bash(gh issue view:*)`. `gh pr list/view` are read-only and required by the scope-audit gate's `in_flight_pr` check; `gh issue view` (added later, alongside the identical grant on `_WORKSPACE_CHAT_ALLOWED_TOOLS`) lets a session read a private GitHub issue in its own bound repository, in the same turn, by issue number or by a pasted URL — no `gh issue list`/`create`/`comment`/`edit` is granted, so the chat can read but never enumerate, file, or mutate issues; `CHAT_ISSUE_SCOPE_CLAUSE` has the assistant extract the trailing issue number itself and call `gh issue view <number>` when a pasted URL names this session's own bound repository, and decline when it names any other repository. The chat command also passes `--disallowedTools "Write Edit NotebookEdit"` so user-level `~/.claude/settings.json` `permissions.allow` cannot leak write tools into the headless chat subprocess (defense-in-depth for `_build_cmd` too: only `permission_mode="acceptEdits"` callers skip the deny list).

**Known gap (partly closed by P1.12):** the deny list now also carries the credential rules of `docs/architecture/worker_credential_boundary.md` — each as its own argument, after the write-tools token — and `grant_args` drops `Bash(uv run:*)`, `Bash(npx:*)` and `Bash(npm --prefix:*)` from any custom tool list; the remaining gap is a bare `Bash` deny. The strict patterned `--allowedTools` for `Bash` (`Bash(git log:*)` etc.) is *not* defense-in-depth — a host `permissions.allow: ["Bash"]` (or `Bash(rm:*)` etc.) would re-enable arbitrary shell from the chat subprocess. Mitigations: (a) the `_CHAT_PROMPT_NO_IMPL` guardrail tells the model to refuse non-read Bash; (b) the CLI permission UI still prompts unless `skipAutoPermissionPrompt=true` is set. A Bash-pattern deny entry is deferred until we can verify CLI semantics for `--disallowedTools Bash` vs `--allowedTools "Bash(git log:*)"` interaction (whether the named-pattern allow wins over the bare-tool deny).

`_chat_cmd` passes `--append-system-prompt CHAT_SYSTEM_PROMPT` on **every** turn (first and resume), so updates to the guardrail land in existing sessions immediately rather than waiting for them to roll over.

## `CHAT_SYSTEM_PROMPT`

Multi-section prompt that bakes in:

- Layer-1 6-field task-description rubric (`## Goal`, `## Definition of Done`, `## Constraints`, `## Out-of-scope`, `## References`, `## Suggested action`). The first five are server-enforced; `## Suggested action` is encouraged in the chat draft for human review but optional in the extracted JSON description, since the action is already a typed field on the proposal.
- Hard-coded action catalog (`CHAT_ACTION_CATALOG`, validated against `VALID_ACTIONS`). The frontend maps each user-facing action to a starting team via `ACTION_TO_TEAM` (`alan/web/src/api.ts`) before calling `/api/workspaces/{id}/dispatch`.
- Specificity rubric + anti-patterns.
- No-implement guardrail (`_CHAT_PROMPT_NO_IMPL`): enumerates allowed read-only tools, forbids `Write` / `Edit` / `NotebookEdit`, and explicitly bans claiming to have implemented / edited / refactored / fixed anything, especially after a tool call is permission-rejected. Counters observed cases where the model emits blocked `tool_use` blocks then writes a fabricated "implementation complete" summary.
- Optional `<readiness state="ready|incomplete" missing="[…]" />` self-eval marker the UI consumes.
- **Scope-audit gate** (`_CHAT_PROMPT_SCOPE_AUDIT`): before flipping readiness to `ready`, the model performs up to 6 read-only tool calls probing for adjacent areas the user did not mention. Five checks: `sibling_code` (Glob siblings), `in_flight_pr` (`gh pr list --state open`), `prior_workspace` (`ls ~/.alan/workspaces/`), `dod_untestable` (model articulates a concrete test), `invariant_risk` (`Grep docs/architecture/`). Findings accumulate as a `scope_audit` array on the proposal and as inline `## Adjacent Scope` bullets in the description.
- `EXTRACT_TASK_YAML` sentinel contract: when the user sends exactly that string, the agent responds with a single YAML document whose top-level mapping is one of:
  - `status: ready` plus a `proposal:` mapping (`title`, `description`, `team`, `instructions`, `scope_audit`) — single-task path
  - `status: ready` plus a flat `tasks:` list (multi-task workspace path; discriminated by presence of `tasks:` key) — each subtask is a `TaskProposal` with a mandatory `id:` field; `dependencies:` lists `{from_id, to_id}` pairs; top-level `title:` and `description:` describe the workspace
  - `status: need_more_info` plus `questions:` and `missing_fields:` lists
  Free-form fields (`description`, `instructions`, `scope_audit[].summary`) use `|` block scalars so multi-line markdown, embedded quotes, apostrophes, and backslashes are passed through verbatim — the previous JSON contract dropped a parse the moment the model failed to escape one of those. YAML is a superset of JSON, so the parser also accepts payloads that happen to be valid JSON; the migration loosened the contract. Note: single-task uses a `proposal:` wrapper mapping; workspace uses flat top-level fields — the `"tasks" in obj_dict` check in `_dispatch_proposal_shape` is the canonical discriminator.
- `scope_audit` is required (default `[]`) and follows the shared `alan.scope_audit.ScopeGapFinding` shape: `{kind, summary, references, suggested_action}`. The server-side `_build_proposal_ready` enforces a bidirectional invariant: non-empty `scope_audit` ↔ `## Adjacent Scope` markdown header present in `description`.

## Routes

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/chat/sessions` | Create session (201), returns the UUID. |
| GET | `/api/chat/sessions/{id}` | Fetch one session. |
| DELETE | `/api/chat/sessions/{id}` | Delete one session (204). |
| POST | `/api/chat/sessions/{id}/messages` | 202 on accept; 409 when a turn is in flight per `chat.SessionLocks`. Kicks off a background thread that publishes `chat_event` per Claude stream line, then `assistant_done` with the final text + usage, or `error`. |
| GET | `/api/chat/sessions/{id}/events` | SSE wrapper over `PubSub.subscribe("chat:<id>", …)`. |
| POST | `/api/chat/sessions/{id}/propose` | Runs `run_chat_turn(user_text="EXTRACT_TASK_YAML")` synchronously, parses via `_extract_yaml_object` (`_strip_code_fence` then `yaml.safe_load`), returns a discriminated `ProposalReady \| ProposalNeedsInfo` union. |
| POST | `/api/chat/sessions/{id}/interrupt` | 204 on cancel; 409 if no turn in progress (or `SessionLocks` holds the slot but the subprocess hasn't been registered yet); 404 if the session doesn't exist. Sends SIGTERM to the live `claude` CLI subprocess via `SessionLocks.cancel()`. The background thread catches the resulting `ClaudeCliError`, releases the lock, and then publishes `error`. |

`SessionLocks` is keyed by `session_id` and stores the per-session `subprocess.Popen[str]` so a separate request can terminate it. `run_chat_turn` accepts an `on_proc_started` callback that the server wires to `SessionLocks.set_proc`; exceptions inside the callback are logged but not propagated so a registry hiccup can't leak the live subprocess.

`_run_chat_turn_background` splits into `_execute_chat_turn` (which holds the lock for the claude subprocess + persistence, then releases it in `finally`) and a thin outer wrapper that publishes `assistant_done`/`error` AFTER the lock is released. This ordering is required by the optimistic propose-prefetch in `useChatSession.applyEvent`: a frontend handler reacting to `assistant_done` can immediately POST `/messages` or `/propose` without losing the race against `locks.release(sid)`. `useChatSession.sendMessage` also awaits any in-flight `prefetchRef` before nulling it, so a user typing through the prefetch window doesn't fire a second POST while the prefetch still holds the backend lock.

## Server-side Layer-1 enforcement

`_missing_layer1_headers` regex-checks the description for the five content headers (`## Goal`, `## Definition of Done`, `## Constraints`, `## Out-of-scope`, `## References`) before persisting a ready proposal — 422 `{detail: "proposal_parse_failed", reason: "<message>"}` on mismatch. `## Suggested action` is intentionally not enforced here because `proposal.action` already carries it as a typed field validated against `VALID_ACTIONS`.

`_dispatch_proposal_shape` (`alan/routes/chat.py`) is the top-level discriminator gate: if `"tasks" in obj_dict` it delegates to `_build_workspace_proposal_ready`; otherwise to `_build_proposal_ready` (single-task path). `_build_workspace_proposal_ready` validates the `WorkspaceProposal` model (which enforces DAG acyclicity on `dependencies` via a Pydantic model validator), runs `_validate_subtask` on each `TaskProposal`, and normalises a single-task workspace with no dependencies to a plain `ProposalReady`. `_validate_subtask` enforces: mandatory `id`, non-`INTERNAL_ONLY_TEAMS` team, Layer-1 headers present, and `_count_references_files ≤ 5` (budget validator — rejects subtasks with more than 5 `## References` bullet entries since LoC is unknowable pre-implementation).

Every 422 from `/propose` returns the same structured shape (`{detail: <code>, reason: <message>}`) regardless of whether it was a YAML-extract failure (`yaml extract failed: …` / `top-level YAML value is not a mapping`), a Pydantic `ValidationError`, or one of the local `ValueError` raises (`unknown action`, `description missing headers`, `proposal missing`, scope_audit/header mismatch, kind/action enum confusion, …). `_proposal_parse_error` logs the failure reason at WARNING and the raw model output truncated to 2000 chars at DEBUG for every 422 path — the chat-creation `/propose` (`dispatch_proposal_parse_failed`). The WARNING line is operator-safe (reason only); the DEBUG line carries the raw payload for diagnosis when explicitly enabled. The frontend's `readErrorDetail` flattens the structured shape to `"<code>: <reason>"` so the UI surfaces the validation cause directly.

On `need_more_info` the server appends the formatted follow-up questions to the transcript as an `assistant` message AND publishes `assistant_done` so the chat UI updates the same way regardless of which path was taken.

## Timeouts

`ALAN_CHAT_TURN_TIMEOUT_SECONDS` (default 600) overrides the per-turn timeout for the streaming path. Separate from `ALAN_CLAUDE_TIMEOUT_SECONDS` (default 120) which governs the one-shot path used by `/api/plan` and `run_agent`.

## Frontend

- `WorkspaceCreate.tsx` — full-screen chat at `/workspaces/new`.
- `useChatSession.ts` — mirrors `useLiveWorkspace`; parses `<readiness …/>` out of assistant text and exposes `readinessState` / `missingFields`; surfaces tool-use events as `toolActivity` cards.
- `TaskProposalModal.tsx` — pre-filled review with editable title/description/team + `Cancel` / `Create` / `Create & Dispatch` buttons + slug preview. The team dropdown maps to a team before the dispatch call.
- The "Create Task" button is **always enabled**; its label and color reflect the agent's last readiness signal:
  - neutral `unknown` (grey)
  - amber `incomplete` w/ tooltip listing missing fields
  - green `ready`
- On click the agent self-gates: `ready` opens the modal; `need_more_info` posts the follow-up questions back into the chat.
- `WorkspaceList.tsx` shed the inline form; a "Create new task" CTA links to `/workspaces/new`.

## Multi-task accept path (PR #234 backend, PR #235 dispatcher DAG, PR #238 frontend wire)

`POST /api/workspaces` accepts an optional `tasks: list[TaskEntry]` and
`dependencies: list[TaskDependency]` alongside the existing `title`/`description`
fields. When `tasks` is non-empty the server persists a multi-task DAG workspace and
returns:

```json
{ "task_id": "<workspace-slug>", "root_task_ids": ["<task-a-id>", ...] }
```

`root_task_ids` lists the DAG roots — every task whose `depends_on` list is
empty — but **nothing is dispatched** by naming them. A create that omits
`team` creates the workspace and dispatches no work at all; the caller must
issue `POST /api/workspaces/{workspace_id}/tasks/{task_id}/dispatch` per task
it wants to run.

**Provenance.** The preparation chat named by `prep_chat_kind`/
`prep_chat_session_id` is archived onto the workspace on every create that
names one — whether or not the request also names a `team`. A failed archive
is logged and never fails or rolls back the create.

**Seeded team validation.** Each `tasks[i].team` supplied on a multi-task
create is validated before the workspace exists: an unknown team, a team that
is not manually dispatchable (`decomposer`, `brief_refine`,
`dependency_sweep`), or a team that can reach the `decomposer` step all return
400 and create no workspace. This guard exists because the reconciler's
auto-advance enqueues each unblocked subtask's stored `team` verbatim with no
other check — it is the only point on that path where a seeded team is
validated at all.

**Acceptance-clause id validation.** A `requirements[]` entry that supplies a
non-empty `id` is rejected (422) before the workspace exists — the server
always derives clause ids from clause text, and a caller-supplied id would
otherwise be silently overwritten. Requests that omit `id` are unaffected.

### Per-task dispatch

`POST /api/workspaces/{workspace_id}/tasks/{task_id}/dispatch` (added in PR #234) queues
a team invocation scoped to a single task within the workspace. The legacy
`POST /api/workspaces/{id}/dispatch` is now a 308 redirect to the per-task route with
`task_id == workspace_id` (single-task backward-compat).

### Atomic create-and-dispatch (F3, 2026-07-21 dispatch-flow speed audit)

`POST /api/workspaces` also accepts an optional `team: str | None`,
`instructions: str | None`, and `counter_case: CounterCase | None` on
`CreateWorkspaceBody`. When `team` is omitted the route behaves byte-for-byte
as before — the two-call create-then-`.../tasks/{id}/dispatch` contract
headless drivers (roadmap-driver, recovery tooling, scripts) depend on keeps
working unchanged. When `team` is set, the route dispatches the root task
inline, in the same request, after the single create-time `scan_predispatch`
call:

1. Resolve the team via `alan/app/workspace_creation.py::resolve_dispatchable_team` (400 on unknown or
   internal-only team) and run `assert_team_dispatchable` (422 on a team not
   admitted at the workspace's pinned posture).
2. Call `dispatch_team_core` with `enqueue_with_pending_workflow` — the exact same
   enqueue path `dispatch_task_team` (the raw per-task dispatch route) uses.
3. Call `maybe_log_counter_case_waiver` — the exact same waiver-audit helper, so the
   waiver is written to `counter_case_waiver_audit.jsonl` exactly once and only after
   a successful enqueue, whether the call arrived via the atomic path or the raw
   two-call path.

`team` is rejected (422, via a `CreateWorkspaceBody` model validator) when combined
with a non-empty `tasks` list — the atomic leg only ever dispatches a single root
task, so it does not compose with the multi-task DAG path above.

**Rollback guard.** Steps 1–2 above run inside a try/except that calls
`workspace_mod.delete` and re-raises the ORIGINAL exception on ANY failure — an
unknown/invalid team, a posture rejection, or an unexpected `dispatch_team_core`
failure all roll the just-created workspace back rather than leaving an undispatched
orphan that would pollute the collision/predispatch corpora for every future check.
The rollback delete itself is best-effort: if it also fails, the delete failure is
logged (`logger.warning`) and swallowed so it can never mask the original
dispatch/admission error the caller needs to see. The `workspace_created` pubsub
event is published only after this guard exits successfully (or immediately,
unchanged, when `team` was never set) — a rolled-back create publishes no event.

This endpoint stays outside `HUMAN_DISPATCH_DOORS` (`tests/test_dispatch_gate_invariant.py`):
the commitment gate is at the ACTION layer (the chat propose flow that mints the
`counter_case`), not at this raw/scripted surface, so `counter_case` stays optional
here exactly like it is on `DispatchBody` — see `predispatch_scan.md` for the
one-scan-per-call wiring table.

### Frontend Create-and-dispatch loop (PR #238)

`WorkspaceProposalPreview` (`alan/web/src/components/WorkspaceProposalPreview.tsx`)
renders when the `/propose` endpoint returns a `WorkspaceProposal`. It shows an
`InterTaskDAG` SVG, a task list, and two action buttons:

- **Create** — calls `createWorkspace(title, description, tasks, dependencies)`; shows
  an inline notice; stays on the chat page.
- **Create and dispatch roots** — calls `createWorkspace`, then iterates `root_task_ids`
  and calls `dispatchTask(workspace_id, rootId, team, payload)` for each; navigates to
  the workspace via the "View →" link in the success notice.

`makeCreateAndDispatchRootsHandler` in `WorkspaceCreate.tsx` implements this loop.
Per-task dispatch uses `api.dispatchTask` (`alan/web/src/api.ts`).

### E2E coverage

`alan/web/e2e/multi-task-workspace-create.spec.ts` (added in Phase 4) covers
the full chat → split proposal → create-and-dispatch-roots round-trip against the stub
`claude` binary. The stub emits the multi-task `WorkspaceProposal` shape when the chat
session carries the `"build multi-task split sample"` sentinel.

## Import-linter layer slot

`chat` between `loop_guard` and `router`.
