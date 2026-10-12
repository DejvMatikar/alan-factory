# Bounded block correction

## Why

A worker that emits a structured block the host must parse (a fenced `decisions.yaml`, a sentinel like `RCA_DRAFT_YAML`) sometimes gets the format wrong — a broken indent, a missing fence, an empty payload. Before this change the host either crash-parked the whole run (discarding every already-completed role's work), silently dropped the payload, or handed a human an opaque error. In every case the model that made the mistake was never told. This module rejects the bad block, hands the author the parser's own error, and demands a corrected re-emission — bounded, and never widening what counts as valid.

## The pure driver: `alan/block_correction.py`

Stdlib-only leaf, sitting at the very bottom of the `pyproject.toml` layers list beside `base_ref` (no `alan.*` imports). Exports:

- `FailureKind = Literal["parse_error", "validation_error"]` — a coarse classification a `validate` callable may attach by raising `BlockCorrectionError(message, kind=...)` instead of a bare exception; any other exception type defaults to `"validation_error"`.
- `Attempt(number, text, error, kind)` — one row per validate call in a single correction run.
- `CorrectionOutcome(outcome, value, attempts, final_error)` — `outcome` is the closed vocabulary `clean | repaired | corrected | exhausted | unreachable_author | unchanged | same_error`; `value` is the parsed result on success, `None` on any failure terminal.
- `run_correction(text, *, validate, re_ask, max_attempts) -> CorrectionOutcome[T]` — the loop.

`validate` is the caller's **existing, unmodified strict validate callable** — attempt 1 is always that exact function, called on the exact text supplied. There is no second, more lenient parse path anywhere in the loop; a correction round re-validates a re-emission through the identical `validate`. This is what makes `max_attempts=1` reproduce today's pre-existing behaviour exactly: with a budget of one, a failing attempt is `exhausted` immediately and `re_ask` is never invoked, matching a call site that has not (or cannot) opt into correction.

On a failing attempt, the loop stops **before** consuming the rest of the budget when:
- the new attempt's error string is identical to the immediately preceding attempt's error (`same_error`) — the model repeated the same mistake, so retrying again will not help;
- the re-emission is byte-identical to what was already rejected (`unchanged`) — nothing changed, so re-validating would just repeat the same failure.

Both stop the loop with the already-collected `attempts`, without ever incrementing past the current attempt. Otherwise, when the attempt cap is reached the outcome is `exhausted`; when `re_ask` is `None`, or invoking it returns `None` (the author's session cannot be reached), the outcome is `unreachable_author`.

`repaired` is never produced by the driver itself — it is a label a **call site** applies after the fact, when it independently detects that its own pre-existing deterministic repair (not this feature) is what made a `clean` first attempt succeed. See `alan/teams/_static_pipeline.py`'s `decisions_block_needed_repair` below.

## The two site families

### Fleet, reachable-author: `alan/teams/_block_correction_fleet.py`

Used by the three fleet sites where the author is a `StaticPipelineTeam`/`BriefRefineTeam` role that just finished on a live `WorkflowGraph` node with a captured `claude_session_id`. `build_re_ask` returns a `re_ask` closure that, per correction attempt:

1. Re-checks the node's captured `claude_session_id` is still present (`None` → `unreachable_author`, honest fallback).
2. `graph.mark_interrupted(node_id, error=...)` + `save_workflow` — the **same** re-ask channel `alan/workflow.py` already exposes for restart recovery (`graph.mark_interrupted` + `resume_workflow_node`), never `alan/workflow_interrupt.py` (that module answers "how do I recover from a killed process," a different question from "how do I re-ask the same, still-running author for a fix").
3. `resume_workflow_node(...)` — resumes the **same** Claude session by its captured id, with a new user turn asking for the corrected block. This extends the existing conversation; it never re-runs the node's role from scratch, and it never touches any other, already-completed node in the invocation — the single most load-bearing invariant in this feature ("never re-run upstream work").

`run_fleet_correction` wraps `run_correction` with `FLEET_MAX_ATTEMPTS = 3` (one original attempt plus up to two corrections) and writes exactly one audit row for the invocation's final outcome via `alan/teams/_block_correction_audit.py`, optionally relabelling a `clean` success to `repaired` when the caller supplies `detect_repair`.

Wired at:
- `alan/teams/_static_pipeline.py::StaticPipelineTeam._parse_or_invalidate` — decisions.yaml (`architecture.py`, `implementation_plan.py`, the latter also carrying the nested `test_scenarios:` key) and decomposition.yaml (`decomposer.py`). The method keeps a **legacy branch** (`block_name is None or emit is None`) that reproduces the pre-existing behaviour byte-for-byte with no correction attempted at all — this is what lets three tests in `tests/test_teams_static_pipeline.py` (a size-ratchet-pinned-adjacent file this change is not allowed to grow) keep calling the old four-positional-argument shape unmodified.
- `alan/teams/brief_refine.py::BriefRefineTeam.run` — brief_refine.yaml, wrapping `parse_brief_refine` (which returns `None` rather than raising, so the site's own `_validate` closure raises `BlockCorrectionError` on a `None` result).

On exhaustion, the fleet sites behave exactly as they did before this feature: `_static_pipeline.py`'s sites still `invalidate_node_result` + `fail_node` + `finalize(status="failed")` and raise `StaticPipelineParseError`, now carrying the attempt history (`"attempt 1: <err>; attempt 2: <err>"`) in the message instead of just the last error. `brief_refine.py` still proceeds with the unrefined brief on any failing terminal (`exhausted`, `unreachable_author`, `unchanged`, `same_error` are all just "no corrected payload" from `_apply_payload`'s point of view) — brief_refine's contract of never parking, blocking, or escalating survives unchanged.

### Interactive, reachable-author: `alan/routes/_chat_correction.py`

The dispatch-proposal door (`EXTRACT_DISPATCH_YAML`, `alan/dispatch_proposal.py`'s `DispatchProposal`) is a live human-in-the-loop chat turn, not a fleet worker — its author is the same chat session, reachable via a second `do_turn` call on a freshly-reloaded session (picking up whatever `cli_session_id` the first turn established), inside the **same lock entry** `run_dispatch_proposal` already holds. `run_dispatch_correction` bounds this to `MAX_ATTEMPTS = 2` (one original attempt, one correction) and reuses the in-flight predispatch-scan and collision-check tasks unchanged — they were already started before either turn and are awaited once, after correction settles.

**Format only, never judgment.** `alan/routes/_chat_router.py::_validate_dispatch_yaml_shape` is deliberately narrower than `parse_dispatch_proposal_yaml`: it only checks that the raw text extracts as YAML and that the top level is a mapping. A structurally valid proposal that fails a **business rule** (an unknown or non-dispatchable team, a title-bearing proposal for a team outside the allowed new-workspace set, a missing `counter_case`) is not a format defect — retrying would not fix a deliberate rejection, and the boundary this feature must not cross is "corrects output that does not conform to its contract," never "assesses whether conforming output is any good." `parse_dispatch_proposal_yaml` itself is called exactly once, after the format-level correction loop settles, with no further retry — so every existing judgment-rejection test (`unknown team`, `decomposer rejected`, `blank title`, ...) still gets exactly one `do_turn` call and its original 422, unchanged.

On exhaustion, `run_dispatch_proposal` re-raises the same `HTTPException(422, {"detail": DISPATCH_PROPOSAL_PARSE_DETAIL, "reason": ...})` shape it always has. No new response field, no new empty or error state — a correction only extends the door's existing pending state by one extra turn.

### Unreachable-author, audit-only

Four sites parse a block whose author's live session has already ended by the time the host sees it — a fleet re-ask is impossible, so the fix here is purely: stop discarding the failure silently, write an audit row naming what happened. Each wraps its existing parse function in a `validate` closure that raises `BlockCorrectionError` on an empty/malformed result, then calls `alan/teams/_block_correction_audit.py::record_unreachable_attempt(site=..., role=..., validate=..., text=...)`, which runs `run_correction(..., re_ask=None, max_attempts=2)` — `max_attempts=2` (not 1) is what makes a failing attempt classify as `unreachable_author` rather than `exhausted`: the loop only reaches the `re_ask is None` branch when the attempt budget has not yet run out.

- `alan/_dispatcher_scenario_rebind.py` — `scenario_rebind.yaml`, from `implementation_fix`. Optional block (most handovers carry none), so the site gates on `REBIND_BLOCK_RE.search(...)` before auditing at all — a block that was never attempted writes no row.
- `alan/teams/_fix_non_actionable.py` — two distinct blocks, both optional, both gated the same way: `test_scenarios.yaml` (via `_fix_forward.parse_fix_scenarios`, whose named-fence, heading, and salvage-only bare-YAML fallback are all covered by one gate) and `non_actionable_feedback.yaml` (parsed upstream in `alan/teams/implementation_fix.py`; this site only has the already-extracted `non_actionable_reason: str | None`, so its gate is a literal-substring check on `synthesis` rather than a re-parse, and the corresponding audit row is written directly rather than through `record_unreachable_attempt`).
- `alan/teams/_verification_review_post.py` — `INLINE_FINDINGS_YAML`, gated on `verdict == "REQUEST_CHANGES"` and a fence-presence check against `alan.teams._inline_findings`'s private regex.
- `alan/investigator.py` — `RCA_DRAFT_YAML`. Unlike the other three, this block is **mandatory** (the investigator's system prompt always asks for it), so there is no "was it attempted" gate — every investigation writes exactly one row.

## Audit trail: `alan/teams/_block_correction_audit.py` + `alan/health_repo_metrics.py`

The writer lives in `alan/teams/` (so every site above, whether in `alan/teams/` or a plain top-level `alan/` module sitting above `teams` in the layers list, can import it downward) but the schema — the audit filename constant, the `BlockCorrectionRow` model, the tail reader, and the two rate functions — lives in `alan/health_repo_metrics.py`, one layer below `teams`, matching the existing `read_audit_tail`/`AUDIT_TAIL_BYTES` precedent (the writer imports the schema down; it is never the reverse).

One row is written per **invocation** of the correction loop (not per attempt): `{site, block, role, outcome, attempts, failure_kind, task_id}`, where `attempts` is the number of validate calls the invocation actually made (`len(CorrectionOutcome.attempts)`) and `failure_kind` is the last attempt's classification (`None` on a clean/repaired/corrected success).

- `compute_format_error_rate(rows)` — of rows where `attempts == 1` (the invocation settled on its first try, whether that means immediate success or immediate `exhausted`/`unreachable_author`/`unchanged`/`same_error`), the fraction whose `outcome != "clean"`. `repaired` counts as an error here — a first attempt that needed silent deterministic repair still got the format wrong.
- `compute_recovery_rate(rows)` — of rows where `attempts == 2` (the invocation needed exactly one correction), the fraction whose `outcome == "corrected"`. This is deliberately scoped to a **single** correction, per the health-page framing ("how often a single correction recovers it") — a fleet site's rarer second correction round (`attempts == 3`) is a different, unmeasured population.
- Both follow the shared low-data convention: `sample == 0` → `value is None`, `low_data is True`. There is no minimum-sample suppression beyond that.

`format_correction_rates_from_audit_dir` composes the tail read with both computations, mirroring the existing `*_from_audit_dir` helpers in the same module. Surfacing these two numbers on the health page (reusing the existing quality-metrics presentation, with an explicit "not measured yet" empty state) is out of scope for this change — this module only supplies the two rate functions a health-page route can call.

## The declaration gate: `tests/host_parsed_blocks.py` + `tests/test_host_parsed_blocks.py`

A deliberate, human-edited inventory of the nine host-parsed blocks (`tests/host_parsed_blocks.py::HOST_PARSED_BLOCKS`), each naming its owning site and an optional discovery pattern. `tests/test_host_parsed_blocks.py` scans every `.py` file under `alan/` (excluding `alan/web/`) for two generic textual signatures — a named fence (`` ```yaml <name>.yaml ``, in either prompt-guidance prose or a compiled regex pattern) and an all-caps `..._YAML` sentinel — and asserts every token it finds is a substring of some declared entry's discovery pattern. A future host-parsed block introduced without a matching inventory entry fails this test with a message naming the undeclared token and pointing at the file to edit. This is the "cheapest form that achieves the goal" the brief asks for: no AST walking, no import-time registry, just two regexes over raw source text plus a table a human keeps honest.

Two of the nine rows are worth naming explicitly since they are easy to miss:
- `test_scenarios.yaml` covers **two** distinct call sites under one name and one discovery pattern — the `test_scenarios:` key nested inside `implementation_plan.py`'s `decisions.yaml` block, and the standalone fenced block `_fix_non_actionable.py` reads via `_fix_forward.py` (whose exact-fence, heading, and salvage-only bare-YAML fallback parsers all share the one literal token).
- `non_actionable_feedback.yaml` is emitted and parsed in `alan/teams/implementation_fix.py` (not touched by this change) but audited in `alan/teams/_fix_non_actionable.py`, which is why its site names both files.
