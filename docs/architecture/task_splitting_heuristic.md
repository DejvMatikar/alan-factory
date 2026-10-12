# Task splitting heuristic (chat-flow)

**Status:** RETIRED (decision logic) — 2026-06-03. The chat-time split-vs-keep **decision logic** has been removed: `_CHAT_PROMPT_SPLITTING` and the multi-task `tasks:`/`dependencies:` block of the `EXTRACT_TASK_YAML` contract are gone from `format_chat_system_prompt`, so the Create-Task chat no longer proposes a `WorkspaceProposal` — the `decomposer` team (`docs/architecture/decomposer.md`) now owns split-vs-keep at runtime. The DAG **substrate** is untouched (`Task`/`TaskDependency`/`validate_dag_inputs`/`apply_dependencies`/`Meta.tasks`, the multi-task `POST /api/workspaces` endpoint), and the `WorkspaceProposal` parse path in `alan/chat.py` + `alan/routes/chat.py` is intentionally left intact (back-compat for persisted chat sessions and the direct propose endpoint); a later sweep may delete it once such sessions age out. The rest of this document is historical.

**Status (original):** Accepted — 2026-05-25
**Prerequisite:** S1 `multi-task-workspace-dag-archite` inv_002 (closed 2026-05-24); D-08 of that closed workspace locks the `WorkspaceProposal` interface this document builds against (normative definition: `~/.alan/workspaces/multi-task-workspace-dag-archite/handovers/handover_*_002.yaml` §D-08). No corresponding file exists under `docs/architecture/`.
**Source:** `alan/chat.py`, `alan/server.py`. Note: `WorkspaceProposalPreview.tsx` and `WorkspaceCreate.tsx` were removed in Phase 1 (2026-06-02). The backend splitting contract remains.

This ADR locks the prompt-and-validator contract for chat-time decomposition of a user vision into a `WorkspaceProposal` of N task-DAG nodes, each sized to ≤ 200 lines / ≤ 5 files per `docs/AGENTIC_CODING_GUIDE.md` §2.

## Context

The current `EXTRACT_TASK_YAML` sentinel returns exactly one `TaskProposal`. For features that span multiple subsystems — e.g., backend model + server endpoint + frontend component + tests — a single 200-line budget is often insufficient. Forcing users to manually split proposals and re-enter the chat flow is the largest source of "workspace too large, agent gave up" failures observed in production.

S1 (`multi-task-workspace-dag-archite`, now closed) defined the structural workspace data model: `WorkspaceProposal { title, description, tasks: list[TaskProposal], dependencies: list[{from_id, to_id}] }`, the dispatcher ready-set semantics, and `ChatSession.proposal: TaskProposal | WorkspaceProposal | None`. Eight follow-on decisions — d1..d8 — were produced by the architecture team (inv_002 of this workspace, 2026-05-25). They are the inputs to this ADR.

## Out-of-scope

- Implementation code (separate task; this ADR's outputs become the spec for an `implementation_plan` → `implementation` chain).
- The structural changes covered by `multi-task-workspace-dag-archite` (workspace_id, dir layout, schema 1.5, dispatcher ready-set) — those are this task's prerequisite (S1, now closed), not its scope.
- Manual UI for editing dependencies (read-only in v1, per the structural workspace's out-of-scope).
- Cross-machine / distributed execution.
- Heuristics for automatic splitting of an already-created workspace (only chat-time split at proposal extraction is in scope).

## Locked decisions

### D1 — Sentinel: extend `EXTRACT_TASK_YAML`, no new sentinel literal

**Statement:** Extend the existing `EXTRACT_TASK_YAML` sentinel to emit either the legacy single-task YAML or the new `WorkspaceProposal` shape. The discriminator is the presence of a top-level `tasks:` key inside the `proposal:` sub-dict of the emitted YAML. Introduce no sibling `EXTRACT_WORKSPACE_YAML` literal and no new endpoint.

**Rationale:** One sentinel keeps the chat agent's emission path uniform. The `tasks:` key is an unambiguous discriminator because `TaskProposal` has no such field. A third status literal (`status: ready_workspace`) would silently break legacy parsers that compare `status == "ready"` without a fallback. Zero invasive change to `claude_cli.py`'s CCN=7 hotspots.

**Confidence:** 0.9 / **Reversibility:** high

### D2 — Budget validator: count unique paths in `## References`, ceiling = 5 files

**Statement:** The per-subtask budget validator parses each subtask's `TaskProposal.description`, extracts the `## References` markdown section, counts unique path-shaped tokens (heuristic: contains `/`, ends in `.py`/`.ts`/`.tsx`/`.md`, or starts with `alan/`/`tests/`/`docs/`/`scripts/`), and rejects via `ValueError → _proposal_parse_error → HTTP 422` when any subtask's file count exceeds 5. The 40-LoC-per-file proxy is explicitly dropped.

**Rationale:** `ScopeGapFinding.references` holds per-finding references (e.g., the file that triggered an `invariant_risk` alert), not the task's authoritative file list — using it would be a structural misuse. `## References` is the authoritative file list enforced by `_missing_layer1_headers`. Actual LoC is unknowable at proposal time; a proxy adds false precision aligned to neither SWE-bench Pro's actual ~26 LoC/file median nor any enforced rule. (`scope_audit.py` is a 24-line Pydantic `BaseModel` with field-level validation — not a bare dataclass; the validator runs at parse time.)

**Confidence:** 0.85 / **Reversibility:** medium

### D3 — Dependency-extraction rule: References ∩ DoD-files or Constraints naming DoD artifact

**Statement:** Dependency edge B → A holds if and only if: (a) B's `## References` section intersects the files A creates or renames as declared in A's `## Definition of Done`, OR (b) B's `## Constraints` section text names an artifact from A's `## Definition of Done`. The chat agent emits `dependencies: [{from_id, to_id}]` directly. Server-side validates via topological sort (Kahn's algorithm); a cycle is rejected with HTTP 422.

**Rationale:** Both signals are already part of the Layer-1 header contract enforced by `_missing_layer1_headers`. No new prompt fields are required. Topological-sort validation is O(V+E) and straightforward. A cycle is an outright bug — the dispatcher ready-set logic per S1 D-11 assumes a DAG and will deadlock on a cycle, so the 422 must fire before any workspace is persisted.

**Confidence:** 0.8 / **Reversibility:** medium

### D4 — Split rubric: N=1 when References ≤ 5 AND single subsystem AND no ordering keywords; else N≥2

**Statement:** The chat agent emits a monolithic `TaskProposal` (N=1) when ALL of the following hold: total `## References` file count across the whole proposal ≤ 5; all References are within a single subsystem (single top-level directory under `alan/`, where distinct subsystem = References spanning ≥ 2 of {`alan/server.py` + server tests, `alan/web/`, `alan/teams/`, `docs/architecture/`}); and no line in `## Constraints` contains ordering keywords ("after", "before", "depends on", "first"). Otherwise the agent emits a `WorkspaceProposal` (N≥2).

**Rationale:** Concrete thresholds the agent can self-apply in one turn. File count aligns with SWE-bench Pro median (4.1 files). The subsystem signal mirrors the codebase's actual layer boundaries enforced by `import-linter`. Avoids forcing artificial splits on genuinely small tasks; preserves the trivial-fix path bit-identically.

**Confidence:** 0.75 / **Reversibility:** high

### D5 — Scope-audit: interleaved per-subtask in the same `EXTRACT_TASK_YAML` turn; no code-flow change

**Statement:** Scope-audit runs interleaved per-subtask within the same `EXTRACT_TASK_YAML` turn. Each subtask's `TaskProposal` carries its own `scope_audit: list[ScopeGapFinding]` field. Ordering relative to budget validation is a prompt-level instruction only; `scope_audit.py` is a 24-line Pydantic `BaseModel` (`ScopeGapFinding`) with field-level validation and requires no code-flow modification (the validation runs at parse time, not in any server code path).

**Rationale:** Both gates execute inside the LLM's reasoning, not in server code. Per-subtask scope-audit keeps each subtask self-contained for downstream dispatch (a subtask's handover prompt assumes its own `scope_audit[]` exists). The symmetric workspace-chat scope-audit gate (`_WORKSPACE_CHAT_SCOPE_AUDIT`) is unchanged — splitting applies only to the task-creation chat.

**Confidence:** 0.9 / **Reversibility:** high

### D6 — CCN guard: extract `_dispatch_proposal_shape` helper, `_parse_proposal_yaml` drops to CCN=3

**Statement:** Extract `_dispatch_proposal_shape(obj_dict) -> ProposalReady | ProposalWorkspaceReady | ProposalNeedsInfo` from `_parse_proposal_yaml` in `server.py`. The helper branches on `status_val` and, within the `"ready"` branch, on `"tasks" in obj_dict`. `_parse_proposal_yaml` calls it once and drops to CCN=3. The legacy single-`TaskProposal` parse path is bit-identical when `tasks:` is absent.

**Rationale:** Adding the `tasks:` discriminator inline pushes `_parse_proposal_yaml` to CCN=7, which is the gate ceiling with zero headroom. `_parse_ws_dispatch_yaml` already sits at CCN=6, establishing a precedent that the parse-branch accumulation pattern is under pressure. Preemptive extraction avoids a future forced refactor on the next parse variant.

**Confidence:** 0.95 / **Reversibility:** high

### D7 — UI: `WorkspaceProposalPreview` wraps `WorkflowDAG` with a prop adapter

**Statement:** The UI surfaces dependencies via a new read-only `WorkspaceProposalPreview` component that wraps the existing `WorkflowDAG` component through a prop adapter. The adapter maps `{from_id, to_id}` proposal-time edges to `depends_on: string[]` on each `WorkflowTask`, which `WorkflowDAG` already accepts. `WorkspaceProposalPreview` adds a per-subtask details panel alongside the DAG.

**Rationale:** Reuses the existing DAG renderer, ensuring visual consistency between proposal-time modal and live workspace DAG view. Zero new visualization dependency. Preserves ESLint `complexity ≤ 8` headroom. Avoids a parallel badge-based representation that would drift from the runtime view. Aligns with S1 D-04 Phase F extraction.

**Confidence:** 0.8 / **Reversibility:** medium

### D8 — Splitter location: single-flow inside existing `propose_chat_task`, no two-step endpoint

**Statement:** Splitting runs inside the existing single-turn `propose_chat_task` flow in `chat.py`. The chat agent decides monolithic-vs-split in one `EXTRACT_TASK_YAML` emission. No separate two-step (Layer-1 task → optional split pass) endpoint, no new `ChatSession` field, no new `/sessions/{id}/propose-workspace` route.

**Rationale:** A two-step pass would require either a new endpoint (duplicating locking/streaming plumbing from `_run_chat_turn_background`) or a new `ChatSession` schema field, with no behavioural gain. The agent already has sufficient context in one turn to decide the split. Keeps the discriminator at `_parse_proposal_yaml` and contains the schema change to D-08's `ChatSession.proposal` union widening.

**Confidence:** 0.9 / **Reversibility:** medium

## Backward-compat wire format

`EXTRACT_TASK_YAML` is extended, not replaced. The legacy extraction path — `proposal:` sub-dict without a `tasks:` key — is unchanged. `_parse_proposal_yaml` (current CCN = 6) calls `_dispatch_proposal_shape` which returns the legacy `ProposalReady` type when `tasks:` is absent, routing through `_build_proposal_ready` exactly as before. The `tasks:` top-level key is the sole discriminator. D6 extraction yields `_parse_proposal_yaml 6→3`.

The `ChatSession.proposal` field widens from `TaskProposal | None` to `TaskProposal | WorkspaceProposal | None` per S1 D-08. Existing on-disk sessions that carry a `TaskProposal` in `proposal` remain valid: Pydantic's discriminated union reloads them correctly when `tasks:` is absent. Sessions carrying `None` are unaffected.

No `EXTRACT_*_YAML` migration cliff exists: any chat session that never emits `tasks:` continues to produce a single-`TaskProposal` response through the old code path, unmodified.

The legacy `INITIAL_DISPATCHABLE_TEAMS` check applies to root subtasks (those with no inbound `dependencies[]` edge). Non-root subtasks may carry any team in `MANUALLY_DISPATCHABLE_TEAMS` minus `principal_proxy`. Server-side code identifies roots by topological position before applying the team-validation gate.

The S1 D-08 wire shape (normative definition: `~/.alan/workspaces/multi-task-workspace-dag-archite/handovers/handover_*_002.yaml` §D-08):

```yaml
# EXTRACT_TASK_YAML — WorkspaceProposal shape (N≥2 path)
status: ready
proposal:
  title: "Short workspace title"
  description: |
    ## Goal
    …
  tasks:
    - id: subtask-a
      title: "Subtask A title"
      description: |
        ## Goal
        …
        ## Definition of Done
        …creates alan/foo.py…
        ## Constraints
        …
        ## Out-of-scope
        …
        ## References
        - alan/foo.py
        - tests/test_foo.py
      team: implementation_plan
      instructions: null
      scope_audit: []
    - id: subtask-b
      title: "Subtask B title"
      description: |
        ## Goal
        …
        ## References
        - alan/foo.py     # shared with subtask-a DoD → dependency
      team: implementation_plan
      instructions: null
      scope_audit: []
  dependencies:
    - from_id: subtask-a
      to_id: subtask-b
```

Each inner `TaskProposal.description` carries its own full Layer-1 header block (`## Goal / ## Definition of Done / ## Constraints / ## Out-of-scope / ## References`). The outer `WorkspaceProposal` carries `title` and `description` as a rollup summary only. This keeps each subtask self-contained for downstream dispatch.

## `CHAT_SYSTEM_PROMPT` delta (prompt contract)

The implementation PR must extend `CHAT_SYSTEM_PROMPT` with three additions inside the existing `EXTRACT_TASK_YAML` contract section:

1. **Split-vs-monolithic rubric** — quote D4's thresholds verbatim so the agent can self-apply them in one turn.
2. **Per-subtask `## References` as budget gate** — explicitly instruct the agent that each subtask's `## References` section is the authoritative file list the budget validator counts, and that any subtask exceeding 5 unique path-shaped tokens will be rejected with HTTP 422.
3. **Per-subtask scope-audit instruction** — instruct the agent to run the six scope-audit checks once per subtask within the same turn before flipping readiness; each subtask's `scope_audit: []` field records the findings.

The workspace-chat `WORKSPACE_CHAT_SYSTEM_PROMPT` is unchanged — splitting applies only to task-creation chat, not to the dispatch-advisor flow.

## Ordering relative to scope-audit (code flow)

Both gates are prompt-level instructions executed inside the LLM. The recommended prompt ordering is: (a) split rubric → (b) per-subtask scope-audit → (c) per-subtask budget validation. This matches the logical dependency: scope-audit may surface adjacent files that push a subtask over budget, so it precedes the budget check in the prompt instructions.

No server-side code change is needed for ordering. `_build_proposal_ready` and its sibling `_build_workspace_proposal_ready` each validate budget and scope-audit-header consistency after the YAML is parsed; they are sequentially ordered by their call positions in `_dispatch_proposal_shape`.

## `TEAM_TRANSITION_POLICY` impact

Splitting does not change team selection. Each subtask carries its own `team` field, validated against `INITIAL_DISPATCHABLE_TEAMS` (for root subtasks) or `MANUALLY_DISPATCHABLE_TEAMS` minus `principal_proxy` (for non-root subtasks). `TEAM_TRANSITION_POLICY` is unchanged; the router still governs team-to-team transitions after a workspace is dispatched. The splitter is chat-time only; it does not add new teams or transitions.

## Test obligations

The implementation PR must satisfy all of the following verifiable assertions:

### Backend unit tests (`tests/test_chat.py` or `tests/test_server.py`)

- **(a) Splitter emits N≥2 for a multi-area vision prompt.** Fixture: a proposal YAML whose outer `tasks:` list contains ≥ 2 entries spanning ≥ 2 distinct subsystems (e.g., one subtask references `alan/server.py`, another references `alan/web/src/`). Assert `_dispatch_proposal_shape` returns `ProposalWorkspaceReady`; assert `len(result.proposal.tasks) >= 2`.
- **(b) Splitter emits N=1 for a trivial-fix prompt.** Fixture: a proposal YAML with no top-level `tasks:` key. Assert `_dispatch_proposal_shape` returns `ProposalReady`; assert result is a `TaskProposal`.
- **(c) Budget validator rejects a subtask exceeding 5 files.** Fixture: a `WorkspaceProposal` YAML where one subtask's `## References` section lists 6 unique path-shaped tokens. Assert `_build_workspace_proposal_ready` raises `ValueError` (which the server maps to HTTP 422).
- **(d) `dependencies[]` form a valid DAG.** Fixture: a `WorkspaceProposal` with a 3-node chain A→B→C (two dependency edges). Assert topological sort succeeds. Second fixture: A→B and B→A (cycle). Assert `ValueError` is raised.
- **(e) Legacy `TaskProposal` round-trip unchanged.** Assert that a proposal YAML without `tasks:` parses identically before and after the D6 refactor (diff=zero on `ProposalReady` output fields).
- **(f) `ChatSession.proposal` union reload.** Serialize a `ChatSession` carrying a `WorkspaceProposal`, write to disk, reload via `load_session`, assert the reloaded `proposal` is an instance of `WorkspaceProposal` with correct `tasks` and `dependencies`.
- **(g) CCN gate.** Assert `_parse_proposal_yaml` has CCN ≤ 3 (CCN == 3) after the D6 extraction (lizard programmatic check or test that counts branches).

### Frontend unit tests (`alan/web/src/`)

- **(h) `WorkspaceProposalPreview` renders N subtasks and N-1 dependency edges.** Fixture: a `WorkspaceProposal` with 3 tasks and 2 dependency edges. Assert the component passes correctly adapted `WorkflowTask[]` (with `depends_on` populated) to `WorkflowDAG`. Assert no extra edges.
- **(i) `TaskProposalModal` renders a `WorkspaceProposalPreview` when proposal has `tasks`.** Assert the modal renders the DAG preview component when `proposal.tasks` is non-empty, and the single-task form otherwise.

## Open items (forwarded to `implementation_plan`)

These sub-decisions are downstream implementation concerns. This ADR does not close them; `implementation_plan` owns closure before code lands.

1. **Layer-1 headers per subtask vs workspace level** (devil_advocate A2) — Recommend: each inner `TaskProposal.description` carries its own full Layer-1 block. The outer `WorkspaceProposal.description` is a rollup summary only. `implementation_plan` owns: verify with a fixture prompt that `ALAN_CHAT_TURN_TIMEOUT_SECONDS=600` covers a 5-subtask emission.

2. **`INITIAL_DISPATCHABLE_TEAMS` for non-root subtasks** (devil_advocate A5) — Recommend: apply `INITIAL_DISPATCHABLE_TEAMS` only to topological-root subtasks; non-root subtasks accept any `MANUALLY_DISPATCHABLE_TEAMS` team minus `principal_proxy`. `implementation_plan` owns: `_build_workspace_proposal_ready` must compute topological roots before team validation.

3. **Server-side cycle detection** (devil_advocate inversion 4) — Hard 422 from `_build_workspace_proposal_ready` via Kahn's algorithm. Never let a cycle reach the dispatcher. `implementation_plan` owns: implement Kahn's algorithm in `_build_workspace_proposal_ready`.

4. **N=1 `WorkspaceProposal` normalization** (devil_advocate A6) — Recommend: server-side normalize to `TaskProposal` when `len(tasks) == 1 and not dependencies` to avoid two render paths for the same semantic content. `implementation_plan` owns.

5. **`ChatSession.load` union discriminator** (gap 3 from inv_002) — Confirm the existing JSON loader correctly discriminates `TaskProposal | WorkspaceProposal | None` on reload. If Pydantic cannot discriminate without a `model_fields` hint, add a discriminator annotation on the union field. `implementation_plan` owns.

6. **Layer-3 validator extension for architecture revisits** (devil_advocate inversion 2) — The validator exempts `architecture → architecture` revisits, which is exactly the shape where inv_002 had to be hand-patched. A follow-up task should enforce `decisions: []` rejection on those revisits too. `implementation_plan` owns.

7. **Subtask `id` scheme** — The `id` field of each `TaskProposal` within a `WorkspaceProposal` must be unique within the proposal and slug-safe (used as `from_id`/`to_id` in `dependencies[]`). Recommend: kebab-case, max 64 chars, validated by `_build_workspace_proposal_ready`. `implementation_plan` owns.

8. **Session re-entry UX** — When a session already carries a `WorkspaceProposal` and the user edits then re-proposes, define whether the new emission replaces or merges with the existing proposal. Recommend: replace (simpler; user has the chat transcript as undo). `implementation_plan` owns.

9. **`CHAT_SYSTEM_PROMPT` delta requirement** — The implementation PR must extend `CHAT_SYSTEM_PROMPT` with the split-vs-monolithic rubric (D4 thresholds verbatim), the per-subtask `## References`-as-budget-gate instruction, and the per-subtask scope-audit instruction. `implementation_plan` owns: exact prompt wording for the D4 split rubric, per-subtask budget gate, and per-subtask scope-audit block.

10. **Dispatcher ready-set ordering dependency** — S1 D-11 defines the ready-set semantics for multi-node `WorkspaceProposal` dispatch. `implementation_plan` owns: verify that the dispatcher's ready-set logic (which marks a subtask runnable when all its `dependencies[]` predecessors have `status: completed`) is in place before dispatch of a `WorkspaceProposal`; if not, sequence this as a prerequisite sub-task.

## Phase 3/4 wire-up (PRs #234, #235, #238 + Phase 4)

The chat-time `WorkspaceProposal` that this ADR defines now lands as a real persisted
multi-task workspace through the following pipeline:

1. **PR #234 (backend accept path)** — `POST /api/workspaces` accepts `tasks` +
   `dependencies`, persists the DAG, and returns `root_task_ids`.
2. **PR #235 (dispatcher DAG + `Status.FAILED`)** — the dispatcher reconciler reads
   tasks by `workspace_id` and respects DAG ordering; `Status.FAILED` propagates to
   dependent tasks.
3. **PR #238 (frontend wire)** — `WorkspaceProposalPreview` renders the
   `WorkspaceProposal` modal with `Create` and `Create and dispatch roots` buttons;
   `makeCreateAndDispatchRootsHandler` in `WorkspaceCreate.tsx` loops over
   `root_task_ids` calling `api.dispatchTask` for each root.
4. **Phase 4 (this doc update)** — `alan/web/e2e/multi-task-workspace-create.spec.ts`
   proves the full round-trip end-to-end against the stub `claude` binary.  The stub
   emits a three-task fan-out `WorkspaceProposal` (task-a → task-b, task-a → task-c)
   when the chat session seed contains `"build multi-task split sample"`.  The spec
   asserts: modal renders ≥ 2 task rows, `InterTaskDAG` SVG with ≥ 1 edge, both action
   buttons enabled, navigation to the workspace slug, and the task graph visible on the
   workspace page.

See `docs/architecture/chat_workspace_creation.md` for the full create-and-dispatch
endpoint contract.

## Consequences

**Positive.**
- Feature-spanning tasks no longer require manual split-and-re-enter. A single chat turn produces a DAG of correctly-budgeted subtasks.
- The legacy single-task path is bit-identical; no migration needed.
- `_parse_proposal_yaml` drops CCN headroom (D6); future parse variants have room.
- Proposal-time DAG preview (`WorkspaceProposalPreview`) reuses the runtime `WorkflowDAG` renderer — zero new visualization code.

**Negative.**
- `CHAT_SYSTEM_PROMPT` grows by ~40 lines for the split-rubric and per-subtask scope-audit instruction. Adds latency to the `EXTRACT_TASK_YAML` turn for N≥2 proposals (each subtask needs scope-audit tool calls).
- `_build_workspace_proposal_ready` is a new server-side validator that must be kept in sync with the prompt contract (prompt changes to the split rubric must also update the server-side threshold constants).
- `WorkspaceProposalPreview` is a new frontend component; it must be covered by vitest 100/100/100/100 thresholds.

**Neutral.**
- `WORKSPACE_CHAT_SYSTEM_PROMPT` is unchanged; workspace-chat splitting is out of scope for v1.
- Workspace-chat `scope_audit` (`_WORKSPACE_CHAT_SCOPE_AUDIT`) is unchanged and runs on the dispatch proposal, not on split subtasks.
- `TEAM_TRANSITION_POLICY` is unchanged.
