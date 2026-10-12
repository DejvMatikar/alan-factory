# Pre-dispatch scan

## Purpose

`alan/predispatch_scan.py` runs a deterministic, model-free scope check at workspace creation time. It replaces the LLM-side scope-audit step that was previously part of the create-task chat prompt.

The scan is **advisory only**: findings are attached to the workspace as structured data and appended to a log. It never blocks or rejects a creation request.

## Entry point

```python
scan_predispatch(
    title: str,
    description: str,
    *,
    workspaces_root: Path | None = None,
    exclude_task_id: str | None = None,
    anti_scope: str | None = None,
) -> list[ScopeGapFinding]
```

- Never raises. Returns `[]` on any internal error.
- Self-capped at 10 findings to keep the `Meta.scope_audit` field well within its `max_length=20` Pydantic constraint.
- Query is `f"{title}\n{description}"` so both parameters are load-bearing.
- `anti_scope` is the project's durable Scope/Anti-scope section text (P4.2), resolved by the caller — `scan_predispatch` itself does no file I/O and stays a pure text-in function. The anti_scope check runs **first**, ahead of `in_flight_pr`/`prior_workspace`, so a real guardrail violation survives the `[:10]` cap.

## Checks

### `anti_scope` — project anti-scope violation

Threads P4.2's project doc Scope/Anti-scope section into the scan (P4.3). This is a **negative** constraint check, not a similarity check: appending the section to the Jaccard-trigram query would both miss real violations (they share few trigrams with a short "must not X" bullet) and false-positive on on-scope work (the same section also contains positive scope text).

Instead `_scan_anti_scope(query, anti_scope)` runs a two-step deterministic pipeline:

1. **Isolate negative constraints** — split the Scope/Anti-scope text into lines and keep only those matching a negation-marker regex (`must not`, `do not`, `don't`, `cannot`, `never`, `avoid`, `out of scope`, `anti-scope`, `shall not`, `should not`, `not allowed`). Lines without a negation marker (including all positive "in scope" text) are dropped entirely — this is what guarantees on-scope work never produces a false-positive finding, since positive text never reaches the overlap check.
2. **Salient-token overlap** — for each isolated constraint, compare its content tokens (lowercased words ≥3 chars, minus a small stopword list) against the query's content tokens. A constraint fires as a finding when its token overlap ratio is ≥ 0.6.

A hit emits `ScopeGapFinding(kind="anti_scope", suggested_action="out_of_scope", ...)`.

**No-op conditions:** `anti_scope` is `None` or empty (skips the check entirely, byte-identical to pre-P4.3 behavior); the section has no negatively-marked lines; no constraint reaches the overlap threshold. The check runs in its own try/except inside `scan_predispatch`, so an internal failure degrades to no anti-scope contribution rather than breaking the other two checks.

**Resolving the text:** callers use `alan.project_doc.read_scope_section(project_id, *, root=None) -> str | None`, which reads `Project.doc_ref` → parses the doc's anchors → returns the Scope/Anti-scope section (or `None`). It deliberately does **not** call `ensure_doc` — resolving the anti-scope text at predispatch time must never scaffold a project doc as a side effect — and it swallows every failure mode (`ProjectError`, `ProjectDocError`, `ValueError`, `OSError`, missing/empty file, empty section) to `None`. No project, no doc, or an empty Scope/Anti-scope section is therefore byte-identical to today's scan behavior.

### `in_flight_pr` — open PR overlap

Calls `_pr_branch.list_open_prs()`, which runs `gh pr list --state open --json number,title --limit 100`. Each PR title is scored against the scan query via `jaccard_trigrams` (from `loop_guard`). PRs with similarity ≥ 0.12 surface as findings.

**No-op conditions:** `gh` binary absent; auth failure; timeout; no remote. All return `[]` silently.

### `prior_workspace` — duplicate workspace

Lists all workspaces under `workspaces_root` via `workspace.list_workspaces`. Scores each `meta.title + meta.description` against the query. Skips `exclude_task_id` (the just-created workspace, if applicable). Similarity threshold: 0.12.

**Threshold rationale:** 0.12 is intentionally lower than `loop_guard.py`'s 0.4 stall-detection threshold. The loop guard blocks future dispatches on a match, so it requires high precision. The predispatch scan is advisory-only — false positives surface extra context but never block work — so a lower threshold that catches weak overlaps is appropriate.

**No-op conditions:** empty workspace root (returns `[]` on empty dir).

## Wiring

| Surface | Scan runs? | Log written? | `anti_scope` resolved from |
|---|---|---|---|
| `POST /api/workspaces` (`create_workspace`), `team` omitted (two-call contract) | Yes, before workspace is created | Yes — one line to `predispatch_findings.jsonl` | `body.project_id` via `read_scope_section` |
| `POST /api/workspaces` (`create_workspace`), `team` set (atomic create-and-dispatch, F3) | Yes — exactly once, on the SAME create-time call; the inline dispatch leg never scans again | Yes — the same one line as above, written regardless of whether the inline dispatch that follows succeeds or rolls back | `body.project_id` via `read_scope_section` |
| `POST .../tasks/{id}/dispatch` (direct dispatch) | No — intentionally unaudited (D1 default) | No | n/a |

Both call sites resolve `anti_scope` in their own fail-open `try/except` before calling `scan_predispatch` — a project lookup or doc-read failure degrades silently to `anti_scope=None` and never blocks the surrounding request. `check_collision` (the workspace-creation overlap check) is intentionally untouched: it answers a different question (overlap with other in-flight work) and threading anti-scope into it would add LLM surface for no DoD benefit.

The log path resolves as: `ALAN_PREDISPATCH_LOG` (full file path) if set, else `$ALAN_LOGS_ROOT/predispatch_findings.jsonl` if `ALAN_LOGS_ROOT` is set, else `~/.alan/logs/predispatch_findings.jsonl`. The `ALAN_LOGS_ROOT` layer was added 2026-07-03: without it every CI/local pytest run appended fixture rows to the production file (the conftest and CI env only pin `ALAN_LOGS_ROOT`, which this path formerly ignored).

## Data model

`Meta.scope_audit: list[ScopeGapFinding] = Field(default_factory=list, max_length=20)`

Added as an **additive optional field** with `default_factory=list` so legacy `meta.json` files (written before this field existed) are backfilled to `[]` on read by Pydantic. No migration is needed.

The `WorkspaceDetail` API response exposes `scope_audit` as a read-only list. The workspace detail page renders it via `ScopeAuditPanel` (read-only mode, no toggle controls).

## What this replaces

The six scope-audit kinds previously ran as LLM steps inside the create-task chat prompt:

| Kind | New home |
|---|---|
| `in_flight_pr` | `predispatch_scan` deterministic check |
| `prior_workspace` | `predispatch_scan` deterministic check |
| `dod_untestable` | **Retired** — surfaced as an open question by the Build flow's existing readiness gate |
| `invariant_risk` | `_DEVIL_ADVOCATE_PROMPT` in `implementation_plan` team |
| `sibling_code` | `_DEVIL_ADVOCATE_PROMPT` in `implementation_plan` team |
| `handover_relative` | No longer produced — was the now-removed workspace-chat advisor surface's own scope-audit gate; the `ScopeGapKind` member is retained unused |

`dod_untestable` has been removed from `ScopeGapKind` entirely. The Pydantic `Literal` and the TypeScript union are the enforcement layer: any reference to the removed kind fails at compile/validation time.
