# PARTIAL Workspace Status — Design Note / ADR

**Status:** Accepted  
**Relates to:** `alan/dispatcher.py:_workspace_terminal_status`, `alan/workspace.py:Status`, `alan/terminal_outcome.py`, `alan/trajectory.py`  
**Problem addressed by #338:** reason-string credit (appending "Already merged: PR #N."); workspace status itself still said FAILED.  
**This ADR:** defines `Status.PARTIAL` — the next step, covering enum semantics, trigger, routing, learning-loop, and exhaustive-map enforcement.

---

## Context

`_workspace_terminal_status` collapses every multi-task workspace that has at least one non-COMPLETED leaf into `Status.FAILED`. When a sibling subtask has already merged a PR, that classification is wrong: it feeds a false failure signal into health/triage, poisons learning-loop reward labels, and leaves the failed sibling invisible behind the parent FAILED banner with no clear re-dispatch path.

---

## Decision: introduce `Status.PARTIAL`

### (a) Why a dedicated `Status.PARTIAL` over the alternatives

**COMPLETED-with-caveats** — `Status.COMPLETED` semantics mean "all agreed work shipped." Stuffing partial delivery into that value requires a parallel caveats field and breaks every consumer that guards on `== COMPLETED` to unlock the next action (e.g. `/api/workspaces/{id}/delivery`, which returns 409 when the workspace is not completed). The gate invariant is worth preserving.

**Reusing `Status.ESCALATED`** — escalation means "handed to a human for a decision outside the automated loop." A partially-shipped workspace still has failed leaves that the automated loop can re-dispatch; it is not a human-decision state. Collapsing the two conflates distinct semantics and would hide partial-delivery workspaces in the escalation queue.

**`Status.PARTIAL`** is a new *recoverable-terminal* value: the workspace has stopped progressing automatically (hence terminal from the dispatcher's standpoint) but the failed leaves remain re-dispatchable and the workspace auto-heals to COMPLETED when they succeed (hence recoverable). It is the only value that is simultaneously truthful, distinct, and preserves the existing COMPLETED / ESCALATED / FAILED semantics unchanged.

### (b) Trigger semantics

`Status.PARTIAL` is set by `_workspace_terminal_status` when and only when **all** of the following hold:

1. All task leaves are in a terminal `TaskStatus` (`COMPLETED` or `FAILED`).
2. At least one leaf has `TaskStatus.COMPLETED` **and** carries `dod_clauses` with non-empty `evidence` (i.e. at least one `"PR #N"` stamp written by `_stamp_evidence`). A COMPLETED task with no evidence (e.g. a planning-only node) does **not** satisfy the "merged a PR" bar.
3. At least one leaf has `TaskStatus.FAILED`.

A workspace where all leaves completed but `all_clauses_satisfied` returns False continues to map to `Status.FAILED` (no PR ever merged; the evidence predicate distinguishes these cases).

Condition 2 reads from in-memory `Workspace.meta.tasks[*].dod_clauses[*].evidence` — no network or `gh` call. The evidence is already stamped by `_stamp_evidence` before `_workspace_terminal_status` is evaluated, so this is a pure predicate over persisted `Meta`, consistent with the acceptance-gate layering rule.

### (c) Recoverable-terminal invariant

A workspace in `Status.PARTIAL`:

- Is **not** archived — its failed leaf tasks remain with `TaskStatus.FAILED`, which the dispatcher's re-dispatch path treats as eligible for a new invocation when the human (or triage) dispatches against that subtask.
- **Auto-heals** to `Status.COMPLETED` on the next call to `_workspace_terminal_status` once the formerly-failed leaf completes and `all_clauses_satisfied` passes. No new status transition logic is required; the existing terminal-status evaluator runs after every leaf update and will naturally select COMPLETED when conditions are met.
- A workspace that transitions PARTIAL → COMPLETED writes a normal COMPLETED event to the event stream; consumers (SSE subscribers, health, triage) see it as a clean resolution.

The failed leaf's re-dispatchability requires that `_TERMINAL_STATUSES` in `alan/routes/workspaces.py` does **not** include `Status.PARTIAL` (so the API continues to accept `/dispatch` calls targeting the failed subtask). The workspace-level stop gate in `routes/workspaces.py:685` (`{ARCHIVED, COMPLETED, PAUSED}`) is similarly unaffected.

### (d) Routing implications

**Triage / health:** `_STUCK_STATUSES` in `alan/teams/triage.py` (`{AWAITING_INPUT, ESCALATED, FAILED}`) must include `Status.PARTIAL`. A partially-shipped workspace is stuck: it cannot auto-progress without a re-dispatch of the failed leaf, and the triage sweep is the mechanism that detects and surfaces this. Health (`compute_health_state`, `HealthState`) consumes triage findings; it inherits the correct classification once triage includes PARTIAL in its sweep.

**`terminal_outcome.py`:** `resolve_terminal_outcome` currently handles two branches: `Status.ESCALATED` and `Status.FAILED`. It must gain a third branch for `Status.PARTIAL`, returning a new `TerminalOutcome(outcome="partial", ...)` that surfaces the reason string with merged-PR credit. The `outcome` literal union in `TerminalOutcome` widens from `Literal["escalated", "failed"]` to `Literal["escalated", "failed", "partial"]`. The reason string for PARTIAL should mirror the FAILED path (workflow → jobs → fallback) with the `_with_merged_credit` suffix applied, because the workspace *did* partially fail even if siblings succeeded.

**`_TERMINAL_STATUSES` in `alan/routes/workspaces.py`:** does NOT include PARTIAL; only `{AWAITING_INPUT, COMPLETED, ESCALATED, FAILED}`. This keeps the workspace open for re-dispatch targeting the failed subtask.

**`WorkspaceList` tab filter:** the frontend's `filterByTab` separates workspaces into `active`, `backlog`, and `archived`. A PARTIAL workspace has invocations (it ran), so it will naturally land in the `active` tab (the `invocations_total > 0` predicate). No tab-filter change is required.

### (e) Learning-loop implications

`trajectory.py` operates at the **task** level, not workspace level. `TrajectoryRecord` is keyed on `(task_id, inv_id)`. The already-merged subtask's record already has `reward=True` (derived from `merge_survived`). The failed subtask's record has `reward=False` (or `None` if no PR was opened). Neither record is incorrect today — the PARTIAL status change does not alter individual task trajectory records, and no changes to `trajectory.py` are required.

`scripts/learning_report.py` operates at **invocation/PR granularity** with no workspace-status dimension. Its metrics (`first_pass_approval_rate`, `mean_fix_cycles_to_merge`, `merge_survival_rate`) are computed from `node_turns_audit.jsonl` and `pr_merge_audit.jsonl` records directly — workspace status does not feed into any of them. **No change to `learning_report.py` is required** when `Status.PARTIAL` is introduced. The implementer should confirm this before making any workspace-level aggregation changes, as that would be scope creep.

### (f) Exhaustive-map enforcement rationale

Two consumers use exhaustive mappings that will be caught at build time:

**Frontend (TypeScript):** `StatusBadge.tsx` uses `Record<WorkspaceStatus, string>` for both `STATUS_LABELS` and `STATUS_STYLES`. Adding `"partial"` to the `WorkspaceStatus` union in `api.ts` immediately causes a TypeScript compile error in `StatusBadge.tsx` (`tsc --strict`, active gate). This turns a missed consumer into a build failure — no runtime surprise.

Similarly, `WorkspaceList.tsx`'s `statusForBadge` function maps `WorkflowStatus → NodeStatus`; PARTIAL is a `WorkspaceStatus` (not a `WorkflowStatus`), so no change is needed there.

**Backend (Python):** `pyright --strict` does not automatically flag missing `Status` branches in an `if/elif` ladder, and a `dict[Status, ...]` lookup does not produce a static "missing key" warning. `terminal_outcome.py` already uses an `if ws.meta.status == Status.ESCALATED / Status.FAILED` ladder; the implementation adds `Status.PARTIAL` as a distinct branch. To achieve build-time exhaustiveness enforcement, any new `Status` branch should use one of: (a) a `match` statement with a `typing.assert_never` terminal arm — `pyright` will flag the unhandled value; or (b) a total `dict[Status, ...]` mapping **plus** an explicit test asserting `set(the_dict) == set(Status)`, catching a missing key at test time rather than build time.

---

## Consumers requiring updates (implementation scope, not this ADR)

| File | Change |
|---|---|
| `alan/workspace.py` | Add `PARTIAL = "partial"` to `Status` StrEnum |
| `alan/dispatcher.py` | `_workspace_terminal_status`: new PARTIAL branch when merged-PR evidence exists + >=1 FAILED |
| `alan/terminal_outcome.py` | Widen `outcome` literal; add PARTIAL branch in `resolve_terminal_outcome` |
| `alan/teams/triage.py` | Add `Status.PARTIAL` to `_STUCK_STATUSES` |
| `alan/teams/triage_pregate.py` | Add `Status.PARTIAL` to the pregate stuck-status set |
| `alan/routes/workspaces.py` | Ensure PARTIAL is NOT in `_TERMINAL_STATUSES`; add to `/health` / terminal-outcome path |
| `alan/web/src/api.ts` | Add `"partial"` to `WorkspaceStatus` union |
| `alan/web/src/components/StatusBadge.tsx` | Add `"partial"` entry to `STATUS_LABELS` + `STATUS_STYLES` |
| `docs/architecture/acceptance_gate.md` | Cross-reference this ADR |
| `CLAUDE.md` | Add index line |

Legacy on-disk `meta.json` files will never contain `"partial"` (the value did not exist before this change), so the existing `_heal_legacy_status` field validator requires no update. If a future rollback leaves `"partial"` on disk, it will be caught by Pydantic's StrEnum validation and raise a `ValidationError` on load — an appropriate loud failure that prevents silent data corruption.

---

## Cross-references

- [docs/architecture/acceptance_gate.md](acceptance_gate.md) — `_workspace_terminal_status` dispatcher integration, `all_clauses_satisfied` gate
- `alan/dispatcher.py:_workspace_terminal_status` (~:261) — current FAILED-only implementation
- `alan/terminal_outcome.py:resolve_terminal_outcome` — reason-string credit (#338)
- `alan/trajectory.py:derive_trajectories` — per-task reward labels
- `alan/teams/triage.py:_STUCK_STATUSES` — routing to triage sweep
