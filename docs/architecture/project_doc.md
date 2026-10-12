# Project doc / decision-log spine (P4.2 T1)

## Store

`alan/project_doc.py` gives every `Project` (`alan/project.py`) a durable markdown doc,
co-located beside its `project.json` under the same projects root
(`ALAN_PROJECTS_ROOT`, no new isolation root): `~/.alan/projects/<id>/project.md`. There is no
separate versioning store and no promote-to-doc automation — the file is the single source of
truth, mutated in place.

The doc has four fixed, ordered anchors (markdown `## ` headers), always in this order:

1. `Vision`
2. `Scope/Anti-scope`
3. `Decision Log`
4. `Discovered Facts`

### Lazy scaffold + backfill

`ensure_doc(project, *, root=None) -> tuple[Project, Path]` is the single entry point. It creates
`project.md` with the four empty anchors on first access if the file does not yet exist, and — if
the project's `doc_ref` is still `None` (every project created before this feature, since nothing
set it before now) — persists `doc_ref="project.md"` via `alan.project.update`. A project that
predates this feature therefore backfills with zero migration: its `project.json` is unchanged
except for gaining that one relative `doc_ref`. `Project.doc_ref` (already present on `Project`
since P4.1) is deliberately kept relative; every caller resolves it to an absolute path only at the
point of use, via `doc_path(project, root=...)` (which composes `alan.project.project_dir(project.id,
root=...)` with `doc_ref`).

### Pure primitives

- `parse_anchors(text) -> ProjectDocSections` — parses the four anchors out of the raw file text
  into a frozen dataclass (`vision`, `scope`, `decision_log`, `discovered_facts`). A stray
  non-anchor header line (e.g. a hand-added `## Notes`) is not itself recognized as a boundary — its
  content stays attached to whichever anchor section it falls inside, so nothing is silently
  dropped from the parsed view.
- `append_to_section(text, anchor, content) -> str` / `replace_section(text, anchor, content) -> str`
  — generic, pure text transforms. Both operate on a single anchor's byte span (found via
  `_anchor_spans`) and splice in the new content; every other anchor's bytes in the returned text
  are untouched, and repeated appends land in call order.
- `append_discovered_fact(project, fact, *, root=None) -> Path` — writes **only** the Discovered
  Facts anchor (append).
- `apply_vision_scope_amendment(project, *, vision, scope, decision_entry, root=None) -> Path` —
  replaces the Vision and Scope/Anti-scope anchors wholesale and appends one entry to the Decision
  Log; Discovered Facts is never touched. This is the pure primitive a later structural
  two-write-tier amendment gate (P4.2 T3) calls — T1 does not add an HTTP surface for it.

### Concurrency

All file mutation (`ensure_doc`'s first-access scaffold write, `append_discovered_fact`,
`apply_vision_scope_amendment`) is serialized through a module-level `threading.Lock` guarding the
full load → mutate → save cycle, so two concurrent daemon-thread writers can never lose an append to
a whole-file overwrite — the same precedent as `alan/rca_store.py`'s `save_rca_draft`. Writes
themselves are atomic (`tmp` file + `Path.replace`), mirroring `alan/project.py`'s `_write_json`.

### Layering — workers-can't-write is CI-enforced, not prompt discipline

`project_doc` sits in `pyproject.toml`'s `[tool.importlinter]` layered-architecture contract
directly **above** `teams` (and therefore above `project`, which sits further below `teams`
already). Import-linter's layers contract only allows a higher layer to import a lower one, never
the reverse, so `alan.teams.*` (the module tree every worker-facing team lives in) importing
`alan.project_doc` is a **forbidden edge** — CI fails the `lint-imports` gate if any team module
ever tries. This is the structural half of the "workers get the doc @-injected READ-ONLY"
invariant from the parent roadmap item: the read-only injection mechanism a future subtask wires up
for workers is a separate, low-layer ContextVar primitive (sitting at/below `claude_cli`'s layer),
deliberately not `project_doc` itself — the only code paths that can call `project_doc`'s mutators
are `routes`-layer (or higher) callers, never a team.

## Injection + harvest (P4.2 T2)

### Spike outcome

Before wiring the injection, a live round-trip was attempted: spawn a headless `claude -p`
worker in an empty cwd and ask it to resolve `@<absolute-path-outside-the-cwd>`. The round trip
could not be completed in the authoring session — this harness refuses to spawn `claude` as a
nested subprocess (a blanket recursion guard, unrelated to the question being tested) — so the
question was answered from indirect evidence instead:

- `claude --help` documents `--add-dir <directories...>` as "Additional directories to allow
  **tool** access to" — directory sandboxing in headless mode gates *tool calls* (Read, Edit,
  Bash), not the `@file` reference mechanism itself.
- `@file` references (already used elsewhere in this repo — see `repo_guidance_block`'s
  `@CLAUDE.md` in `alan/teams/implementation.py`) are resolved by the CLI's own
  prompt-composition step, reading the referenced file directly into context before the agent
  loop starts, independent of the tool-access sandbox that gates the model's own tool calls
  during the conversation.
- The dispatcher resolves and sets the doc path at the run seam (`Dispatcher._execute`, beside
  `set_active_pool`) — a point strictly *before* the worker's worktree/cwd is chosen (that
  happens later, deep inside `team.run()`, e.g. via `SlotPool.lease()`). A materialize-into-cwd
  design cannot be driven from this seam without threading `cwd` back through
  `claude_cli._build_cmd` (out of this task's scope) — a signal that the intended mechanism is a
  path-independent `@`-reference, not a cwd-relative copy.

**Chosen mechanism: absolute-`@` reference**, injected existence-guarded. If a future
observation shows a headless worker cannot resolve an external absolute `@`-path after all, the
documented fallback is materialize-into-worktree; the seam for that change is
`claude_cli.run_agent_streaming`'s `ProcessConfig.cwd`, which is already threaded through
`_stream_subprocess` but deliberately not into `_build_cmd`.

### Low-layer ContextVar

`alan/project_doc_context.py` holds a single `ContextVar[str | None]` — the resolved absolute
doc path for the *currently executing* job, or `None`. It sits directly below `claude_cli` in
`pyproject.toml`'s import-linter layers list, so `claude_cli` can import it while the module
itself stays free of any dependency on `project_doc`/`project` — the same "workers-can't-write"
layering invariant this doc's own store section describes, extended one hop further down to
"workers can't even import the write-capable module."

`Dispatcher._execute` resolves `workspace.meta.project_id` to an absolute doc path (or `None`)
via `project_doc.doc_path`, and sets the ContextVar beside the existing `set_active_pool(...)`
call — same synchronous worker-thread chain, no `copy_context`, always overwritten (never
conditionally skipped) so two jobs processed back-to-back on the same worker thread each see
only their own job's value. A `finally` block around the rest of `_execute` (prior-handover load
+ `_run_team`) resets the ContextVar to `None` once the job is done, so the resolved path can
never leak into anything that runs on the same worker thread outside the currently executing
job — including the early-return paths (missing parent handover, team crash).

`claude_cli._build_cmd` reads *only* that string (`get_active_project_doc_path()`) and
existence-guards it (`Path(doc_path).is_file()`) before splicing an `@`-reference block into the
system prompt. A workspace with no `project_id`, an unresolvable project, or a project whose
`project.md` has never been scaffolded via `ensure_doc` all collapse to the same outcome — the
ContextVar resolves to `None` or to a path that does not yet exist, `_build_cmd`'s guard skips
the block, and the composed system prompt is byte-identical to the pre-feature baseline. Workers
never get a tool that can write the doc — the injected block is explicitly labelled "READ-ONLY
reference material" and no team's `--allowedTools` grants a path back to `project_doc`'s
mutators; the only writers are the dispatcher-side fact harvest below and the (T3) amendment
endpoint.

### Fact harvest

Generic across every team, not team-prompt-specific: after any team's `run()` returns, the
dispatcher (`Dispatcher._run_team`, right after the handover is saved) scans
`handover.full_synthesis` for a fenced block headed by `## PROJECT_FACT`:

    ## PROJECT_FACT
    ```
    <free-text fact>
    ```

`_extract_project_fact` is a pure regex parser: no block, an unterminated fence, or an empty
fenced body all return `None` and the dispatcher appends nothing — a missing or malformed block
never raises. The fence match tolerates an optional language tag on the opening fence (e.g.
` ```text `), CRLF line endings (normalized to `\n` before matching, mirroring how a worker's
CLI output can arrive), and leading whitespace on the closing fence line — a stricter parser here
would silently drop otherwise-valid facts, unlike `alan/investigator.py`'s more tolerant
`_YAML_BLOCK_RE`, which this pattern now mirrors. The captured body's internal whitespace
(including embedded newlines from a multi-line fact) is collapsed to single spaces via
`" ".join(text.split())` before formatting, so a multi-line fact still lands as one well-formed
markdown bullet rather than splicing raw newlines into the Discovered Facts list. A well-formed
block is wrapped into an attributed, timestamped bullet
(`_format_project_fact_bullet`, e.g. `- **2026-07-08T12:00:00Z** (implementation): <fact>`) and
appended to the project's Discovered Facts anchor via `project_doc.append_discovered_fact`
(`_maybe_harvest_project_fact`). The whole call is wrapped in a broad `except Exception` — an
unknown `project_id`, a raised `ProjectDocError`, or any other fault is logged and swallowed,
never propagated into the worker's own run.

## Two-write-tier Vision/Scope amendment gate (P4.2 T3)

Vision/Scope is load-bearing — unlike a Discovered Fact, an amendment rewrites the project's
stated intent, so it goes through a structural propose → surface → approve gate instead of a
direct write. "Structural" means the separation is enforced by distinct code paths and section
scopes, not by prompt discipline: the fact-harvest path (above) can only ever call
`append_discovered_fact`, and the amendment-approve path (below) is the *only* caller of
`apply_vision_scope_amendment` in the whole codebase.

### Pending-amendment store

`alan/project_amendment.py` is a queue_meta-backed, fingerprint-keyed store for amendments that
have been proposed but not yet approved — the same `threading.Lock`-guarded load → mutate → save
shape as `alan/rca_store.py`, under its own meta key (`project_pending_amendments_v1`, distinct
from `project_doc`'s own store). `propose_amendment(queue, project_id, *, vision, scope,
decision_entry, ...)` computes a stable 16-hex-char fingerprint (`amendment_fingerprint`,
`sha256(json.dumps([project_id, vision, scope, decision_entry]))[:16]` — still a 16-lowercase-hex
string, so it satisfies `require_valid_fingerprint`'s own format check) and writes a
`PendingAmendment` record. The four fields are JSON-encoded rather than `|`-joined like
`alan.incident.incident_fingerprint` does: vision/scope/decision_entry are free-form text, so a
bare `|` separator can't disambiguate field boundaries when the text itself contains `|` — two
distinct amendments could otherwise hash to the same fingerprint and clobber each other in the
pending store. This mirrors `alan.teams.triage_pregate.surface_fingerprint`'s
`json.dumps(..., sort_keys=True)` precedent instead. **Proposing never touches `project.md`** —
the doc is untouched until a human approves.

`resolve_pending_amendment(queue, fingerprint, *, root=None)` re-derives the amendment fresh from
durable state on every call — never trusts a cached view. It refuses (returns `None`) in exactly three
cases: the fingerprint was never proposed or has already been approved (record gone — "applied"),
the referenced project no longer resolves (`ProjectError` — "stale"), or the project has been
archived (`ProjectStatus.ARCHIVED` — "closed"). The approve endpoint below is now its only caller —
see the tombstone note immediately after this section.

### Tombstone — inbox surfacing (removed, 2026-09)

A pending amendment used to surface in `GET /api/inbox` (`_pending_amendment_items`,
`alan/inbox_items.py`) as a workspace-less, project-scoped `type="decision"` `InboxItem`. That
whole surface — the aggregator function, the deleted `alan/routes/inbox.py`'s signature-guarded
cache that keyed off `PENDING_AMENDMENTS_META_KEY`, and the `GET /api/inbox` endpoint itself — is
gone (delete-the-inbox-http-surface, 2026-09), and was not ported anywhere: a pending amendment now
has no presentation surface at all. `propose_amendment`/`resolve_pending_amendment`/
`claim_pending_amendment` and the approve endpoint below are otherwise unaffected — proposing and
approving an amendment still works exactly as before, there is just nowhere a human sees a pending
one listed before approving it.

### Approve endpoint — the one Vision/Scope write path

`POST /api/projects/{project_id}/amendments/{fingerprint}/approve` (defined directly in
`alan/server.py`, decorated on the imported `projects_router`). It: validates the fingerprint
format via `alan.project_amendment.require_valid_fingerprint` (public — the same check the deleted
inbox route module used to duplicate as a private `_require_valid_fingerprint`), calls
`resolve_pending_amendment` and 404s if it refuses or if the resolved project's id doesn't match
the path's `project_id`, then — mirroring P3.3's `resolve_proposed_dispatch` →
`record_proposal_approval` ordering — calls `record_amendment_approval` (writes
`project_amendment_audit.jsonl` under `ALAN_LOGS_ROOT`) **before** calling
`project_doc.apply_vision_scope_amendment`, and finally deletes the now-applied pending record.
The audit-before-mutate ordering means a crash between the two calls leaves an audit row for an
amendment that never actually landed, never the reverse (a silent doc mutation with no audit
trail). `apply_vision_scope_amendment` is called from exactly this one place in the entire
codebase — the fact-harvest path (above) only ever calls `append_discovered_fact`, so Vision/Scope
and Discovered Facts can never collide on a write, by construction rather than convention.

Between validating the pending record and mutating the doc, the endpoint calls
`project_amendment.claim_pending_amendment(queue, fingerprint)` — an atomic pop guarded by the
same module-level lock as every other pending-store mutation. Only the request whose claim
actually removes the record proceeds to record the audit row and apply the mutation; a second,
concurrent approve for the same fingerprint finds the record already gone and 404s without ever
mutating the doc or writing a second audit row. `delete_pending_amendment` (`resolve_pending_amendment`'s
"already applied" refusal path reads its absence, not its behavior) is now a thin wrapper over the
same claim primitive.

### Layering

`project_amendment` sits in `pyproject.toml`'s import-linter layers list directly above
`project_doc` (between `incident` and `project_doc`) — like `project_doc` itself, this places it
above `teams`, so no worker module can import it (a forbidden edge, CI-enforced). It imports only
`alan.project` and `alan.queue` (plus `alan.teams._audit.append_jsonl_audit`, a `teams`-layer
leaf import already used by several higher layers, e.g. `alan/retention.py`) — it does **not**
import `project_doc`; only `alan/server.py`'s approve endpoint composes the two.
