# Investigator ritual (P3.4)

## Purpose

On a workspace escalation/failure/park incident, `trigger_investigator` fires a **read-only** Sonnet sidecar that attaches a structured, evidence-linked root-cause draft to the incident (keyed by its fingerprint), so a human's review drops from archaeology to verification. It never fixes anything and never dispatches a team — enforced by wiring (absent from `TEAMS`, unreachable from the router/handover/dispatch paths, no write tools), not by prompt discipline.

This doc covers the T2 slice: the daemon trigger, the dispatcher-side incident derivation + class gate, the deterministic citation resolver, and the cost-guard dedupe. The `rca_drafts_v1` store itself (`alan/rca_store.py`) and its `RcaDraft`/`Citation`/`TimelineEntry` schema predate this doc (P3.4 T1).

## Store: `alan/rca_store.py` (P3.4 T1)

Fingerprint-keyed `rca_drafts_v1` queue_meta store — the structural sibling of `alan/incident_store.py`, but importing only `alan.queue` + stdlib (never `alan.incident`). It joins a later human disposition purely by sharing the same fingerprint key, so it survives `incident_store.reconcile_reopen`'s disposition rewrite untouched. `RcaDraft.source` is `investigator | human`. `load_rca_drafts`/`_record_from_dict` defensively drop malformed/unknown stored fields without crashing (mirroring `incident_store`'s load tolerance), and a module-level `threading.Lock` wraps `save_rca_draft`'s load→mutate→save so concurrent daemon-thread writers never clobber each other's fingerprint. Its import-linter layer sits directly below `investigator` rather than beside `manager_summary` (first floated), since it needs the `queue` import and `manager_summary`'s layer sits below `queue` — see "Import-linter layer" below.

## Module: `alan/investigator.py`

Exact sibling of `alan/manager_summary.py`: a module-level `trigger_investigator(...)` spawns a daemon `threading.Thread` running `_do_investigate`, which is fire-and-forget from the dispatcher's perspective.

```python
def trigger_investigator(
    ws_path: Path,
    fingerprint: str,
    evidence: dict[str, Any],
    valid_dispositions: frozenset[str],
    queue: Queue,
) -> None: ...
```

The dispatcher passes **primitives only** — `ws_path`, `fingerprint`, `evidence`, `valid_dispositions`, `queue` — never a `Workspace` or `Incident` object. `alan/investigator.py` does not import `alan.incident`; all incident derivation and class-gating happens at the dispatcher call site (see below), which is free to import `alan.incident` since it sits far above it in the layer stack.

### Cost guard: self-contained dedupe

`_do_investigate` loads the existing `RcaDraft` for the fingerprint (if any) via `rca_store.get_rca_draft` and computes `_compute_recurrence_signal(evidence)` — a stable JSON serialization of the evidence dict. If a draft already exists **and** its stored `recurrence_signal` matches, generation is skipped with **no model call**. First occurrence always generates; a genuine recurrence (evidence has changed — a different note, a different blocker, a different outcome reason) writes a fresh draft. This mirrors the fd-exhaustion/LLM-herd cost incidents referenced in `docs/architecture/triage.md` and `docs/architecture/inbox.md`.

### Model and tools

Runs `model="sonnet"` (not Opus — a diagnostic sidecar, not a fix) via `run_agent_streaming` with a fixed read-only allowlist:

```python
_INVESTIGATOR_ALLOWED_TOOLS = (
    "Read", "Grep", "Glob",
    "Bash(grep:*)", "Bash(cat:*)", "Bash(git log:*)",
    "Bash(gh pr view:*)", "Bash(gh issue view:*)", "Bash(gh run view:*)",
)
```

No `Edit`, `Write`, `NotebookEdit`, and no `Bash(git:*)`/`Bash(gh:*)` wildcard that would admit a write subcommand — only the specific read-only shapes above. `test_investigator_allowed_tools_no_write` pins this.

The allowlist is pre-approval, not the boundary: Claude Code's own working-directory sandbox is, and it silently cost this sidecar a month of incident coverage. The investigator's subprocess is confined to the repo checkout, but the evidence it is asked to cite lives under `~/.alan/workspaces/<id>/` — so every read of the incident's own artifacts came back `may only list files in the allowed working directories for this session: '<repo checkout>'`, the model spent the rest of its budget groping through repo source instead, and the run died at the turn ceiling without ever emitting `## RCA_DRAFT_YAML`. Between 2026-07-22 and 2026-08-19 that happened 27 times across 21 distinct fingerprints. It was a partial failure, not a dead subsystem: 5 of those 21 eventually produced a draft on a later invocation, and drafts kept landing throughout the window (the store holds 39). The cost is the other **16 fingerprints, which have no RCA draft at all** — incidents whose root cause was never drafted and never will be. That partial-success shape is exactly why nothing surfaced it: the store kept growing, so no "investigator produces nothing" signal ever existed, and `_do_investigate`'s bare `except Exception` logged every cause under one generic message.

`run_agent_streaming` is therefore called with `add_dirs=[str(ws_path)]` — `--add-dir` scoped to the single workspace under investigation, never the whole `~/.alan` tree the way `maintenance` grants it (`alan/teams/maintenance.py`). `_MAX_TURNS` is 40, not 20: even with the reads succeeding, the fail-closed citation protocol asks for more tool calls than the original budget allowed. `TurnCeilingReached` is now caught and logged distinctly from every other failure, so a budget death is legible in `alan.error.log` on sight rather than after reading a traceback by hand.

### Output contract: `## RCA_DRAFT_YAML`

The system prompt is a structural port of the `remediation_verify` anti-cheat invariant tombstoned at `docs/architecture/health.md` ("Tombstone — remediation_verify anti-cheat invariant"), re-targeted from a `VERDICT:`/`DIFF_FINDINGS_CRITICAL:` line pair to timeline/citation/falsification: **never present a guess as a confirmed fact — an unconfirmable citation is silently dropped and the draft fails closed to not-verified.** The model never emits a `VERDICT:` line itself; verification is computed mechanically downstream.

The model is asked to end its response with:

```
## RCA_DRAFT_YAML
```yaml
proposed_disposition: root_caused
falsification_test: rerun the sweep and confirm the failure no longer reproduces
recurrence_signature: fd-exhaustion-seed
recipe: prune invocation slots before reseeding the sweep
timeline:
  - description: sweep exhausted file descriptors
    citation:
      kind: log_line
      file: ~/.alan/logs/alan.log
      grep_pattern: "Too many open files"
  - description: confirmed via replay
    citation:
      kind: replay_command
      argv: ["git", "log", "--oneline", "-5"]
```
```

`_parse_rca_yaml` extracts and `yaml.safe_load`s this block (same pattern as `verification.py`'s `## INLINE_FINDINGS_YAML`); a missing marker, unparseable YAML, or a non-mapping payload all fail closed to `None` — `_do_investigate` logs and returns without writing anything, leaving any prior draft untouched.

### Deterministic, LLM-free citation resolver

Every timeline entry's `citation` first has to pass `rca_store.Citation`'s own schema (a concrete locator per kind, or `RcaSchemaError` — `_citation_from_raw` drops the entire entry when construction fails). A schema-valid citation is then independently **re-resolved against reality**, with no model call:

- `audit_row` / `log_line` — `_file_contains` reads the cited `file` and checks the `json_path` / `grep_pattern` string actually appears in it. A citation naming a real file but a pattern that never occurs (a fabricated citation) is dropped.
- `replay_command` — `_replay_command_resolves` checks the citation's `argv` against a fixed allowlist of read-only command *prefixes* (`grep`, `cat`, `git log`, `gh pr view`, `gh issue view`, `gh run view`) — deliberately a full-prefix check (not just `argv[0]`) so `git push`/`gh pr merge` can never be "confirmed" just because `git`/`gh` themselves are read-only-capable binaries.

`_resolve_timeline` drops every entry that fails to resolve and reports `verified` (`_build_draft`'s `all_resolved`) `True` **iff every originally-submitted entry resolved** — a schema-dropped entry counts against verification exactly like a resolver-dropped one, and an empty timeline is never verified. Combined with `_build_draft`'s hard requirement for a non-empty `falsification_test`, this is the fail-closed invariant made mechanical: a draft can only end up `verified=True` when every claim in it was independently confirmed by code, never by the model's own say-so.

**Relocation and new persisted fields (unblocker, slice 0).** The fail-closed `all_resolved` semantics above are unchanged in behaviour, but `_citation_resolves`/`_resolve_timeline` move out of this module into `alan/citation_resolver.py`, which `investigator.py` now delegates to rather than owning directly — a production measurement (`docs/architecture/unblocker.md`'s ADR re-open figures) found only 3 of 37 stored `verified=True` drafts still resolve against today's tree, so a second caller needed the same resolver against a live tree without duplicating it. `RcaDraft` additionally persists `citation_total`, `citation_failures` (the per-citation failure reason, previously discarded the moment `_resolve_timeline` dropped a non-resolving entry — there was no way to recover *why* a draft failed `all_resolved` from the store), `cost_usd` — `run_agent_streaming`'s `AgentStreamingResult` carries only `result_text` and `usage`, no cost field (`alan/claude_cli.py:389-395`); `_generate_rca_text` must instead derive it the way `alan/run_summary.py::derive_run_summary` already does for chat turns, by re-reading the stream file's own `result`-type events (`_result_cost`'s `event.get("total_cost_usd")`) after generation completes, against the same `_stream_path(ws_path)` file `_generate_rca_text` already writes — and `base_sha`. All four are additive and defaulted on read, following `_record_from_dict`'s existing tolerant-load pattern. The live-tree re-check this evidence feeds — re-resolving a plan's citations against the current tree immediately before an autonomous executor acts on it — is an **additional entry point** this module never calls itself; the investigator's own read-only, fire-and-forget generation path is unchanged. See [unblocker.md](unblocker.md)'s "Executor" section (Ruling 9) for the caller.

## Dispatcher wiring: `alan/dispatcher.py`

`_schedule_manager_summary` (the existing manager-summary daemon trigger, called at every one of the dispatcher's 7 terminal/park call sites) now also calls `_maybe_trigger_investigator(ws, queue)` after triggering the manager summary. The function signature grew a `queue: Queue` parameter (threaded through from `self._queue` at each call site) since `_schedule_manager_summary` previously had no `Queue` handle.

`_maybe_trigger_investigator` does all incident derivation and class-gating — the one place in this feature allowed to import `alan.incident`:

```python
def _maybe_trigger_investigator(ws: Workspace, queue: Queue) -> None:
    if ws.meta.team == "triage":
        return
    handovers = list_handovers(ws)
    incidents = derive_incidents(workspaces=[ws], handovers_by_workspace={ws.task_id: handovers})
    if not incidents:
        return
    incident = incidents[0]
    if incident.incident_class not in _INVESTIGATOR_INCIDENT_CLASSES:
        return
    valid_dispositions = frozenset(d.value for d in Disposition)
    investigator.trigger_investigator(ws.path, incident.fingerprint, incident.evidence, valid_dispositions, queue)
```

It reuses `alan.incident.derive_incidents` unmodified rather than re-deriving the classification rules: a single-workspace call (`workspaces=[ws]`) returns 0 or 1 incidents, already filtered through `_STUCK_WS_STATUSES` (so a `COMPLETED` workspace never produces an incident) and `_is_exempt` (so a `ParkReason.QUESTION` park, a `ParkReason.UNARCHIVED` park — a workspace the operator restored from archive — and an awaiting-harvest design-family workspace never produce one either — these exemptions are structural properties of `derive_incidents` itself, not special-cased here). `_INVESTIGATOR_INCIDENT_CLASSES` then narrows to exactly the six DoD classes: `escalation`, `terminal_failed`, `terminal_partial`, `park_crash`, `park_routing_error`, `park_stall`. Any other class `derive_incidents` might return for this workspace (`merge_block`, `residue`, …) is left alone — the investigator never fires for those.

## Import-linter layer

`alan.investigator` needs to import both `alan.rca_store` (to read/write drafts) and `alan.queue` (the `Queue` type it's handed), so it cannot sit at `alan.manager_summary`'s position in the layered contract — `manager_summary` sits *below* `queue`, which would make importing `Queue` a forbidden upward import. Instead `investigator` is inserted immediately **above** `rca_store` (between `incident_store` and `rca_store`):

```
"incident_store",
"investigator",
"rca_store",
"incident",
"teams",
...
```

This makes `rca_store`, `incident`, `teams`, `queue`, `manager_summary`, and `claude_cli` all legally importable from `investigator` (it only actually imports `rca_store`, `queue`, and `claude_cli`) while `investigator` itself is unreachable from any of the many modules below it, including every team and the router. `dispatcher` — already far above everything in this stack — imports `investigator` directly to call `trigger_investigator`.

## Wiring-enforced non-dispatch

`investigator` has no `TEAM` attribute and is absent from `alan.teams.TEAMS`, so it can never be selected by `select_next_team`, dispatched via `POST /api/workspaces/*/dispatch`, or targeted by a handover's `to_team_suggestions`. Combined with the read-only tool allowlist, "never fixes, never dispatches" is a structural property of the wiring, verified by `test_investigator_not_in_TEAMS_unreachable` and `test_investigator_allowed_tools_no_write` rather than relied upon as prompt discipline.

## Readers of an `RcaDraft`

The `pending_disposition` finding (`alan.digest.pending_rca_findings`) and `IncidentDispositionControl.tsx`, which used to be the store's reader and the human half that recorded a disposition, are deleted (see [health.md](health.md)). With no UI caller of `POST /api/health/incidents/{fingerprint}/disposition`, `disposition_agreement_rate` in `scripts/investigator_report.py` stops gaining data points: it goes dark, which is expected and documented rather than a regression. The investigator's own wiring is unchanged — still absent from `alan.teams.TEAMS`, still read-only, still firing only on the same six incident classes.

## `RcaDraft.workspace_key` — the display-only join to the unblocker activity view

`RcaDraft` carries `workspace_key: str | None = None` and `created_at: float | None = None`, round-tripped through `_draft_to_dict`/`_record_from_dict` with the store's tolerant-decode posture: a draft written before the fields existed (or with a non-string key / non-numeric stamp) decodes to `None`. `alan/unblock_activity.py` joins a draft to a workspace on `workspace_key` so the Health page's unblocker activity view can show "latest diagnosis for this workspace" beside that workspace's events, and counts how many of the listed workspaces had one.

- **The join key is `ws.task_id`, stamped in `_dispatcher_base._maybe_trigger_investigator`.** `Incident.workspace_id` is unusable for this: `alan/incident.py::_ws_and_pr` sets it to `None` whenever a PR exists, so joining on it would leave every PR-scoped park undiagnosed. `trigger_investigator`/`_do_investigate` take `workspace_key` as an explicit argument and pass it, with `created_at = time.time()`, to `_build_draft`, which builds the stamped draft directly before `save_rca_draft`. Nothing else about when the investigator fires or what it writes changed.
- **Historical drafts carry `None`**, so diagnosis coverage ("N of M rescued workspaces had a diagnosis") reads near 0% on day one and fills in only as new incidents are investigated.
- **The join is per workspace, not per attempt.** When several drafts share a workspace the latest by `created_at` wins (a draft with no `created_at` sorts oldest), and the draft may predate the event it is shown beside — the UI labels it "latest diagnosis for this workspace".
- **Display-only.** No recovery path may read `workspace_key` or any other draft field: unblocker recipes stay gated on deterministic evidence, never a model's opinion (`docs/architecture/unblocker.md`).

## Tests: `tests/test_investigator.py`

Covers the resolver (fabricated/off-allowlist/locatorless citations dropped, on-allowlist and real citations resolved), the YAML parse/build pipeline (missing marker, malformed YAML, non-mapping payload, missing/empty `falsification_test`, invalid `proposed_disposition`), the dedupe cost guard (`test_dedupe_skip_unchanged_recurrence_no_llm`, `test_recurrence_advanced_writes_new_draft`), the generation lifecycle (model failure / no parseable block / malformed draft all leave the store untouched), the tool allowlist and `TEAMS`-absence wiring checks, the dispatcher-side class gate (`test_trigger_fires_six_dod_classes_skips_triage_question_harvest`, plus dedicated no-incident and non-target-class skip cases), and the workspace-key stamp end to end through `_maybe_trigger_investigator` → `trigger_investigator` → `save_rca_draft` (including an incident whose PR makes `Incident.workspace_id` `None`).

`tests/conftest.py` gained `_no_real_investigator_spawn`, the exact sibling of the pre-existing `_no_real_manager_summary_spawn` autouse fixture — without it, every dispatcher test that reaches a terminal status would now also start a real investigator daemon thread that execs the real `claude` binary, reproducing the same OOM-inducing process-swarm failure documented on `_no_real_manager_summary_spawn`.
