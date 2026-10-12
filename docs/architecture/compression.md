# Compression / cross-team context

**Source:** `alan/compression.py`.

Helpers for shrinking handover content before it's stuffed into the next team's user_text. The structured sections (`Decisions:`, `Open questions:`, `Forbidden:`, `PR: #<n>`) are always rendered deterministically; only the prose `Summary:` body may be routed through an optional Haiku compaction call (see [LLM compaction](#llm-compaction) below).

## `summarize_handover(handover) -> str`

Extracts `decisions`, blocking `open_questions`, `full_synthesis`, and renders a compact text block. Specifics:

- Renders a `Forbidden:` bullet block between Open questions and Summary when `forbidden_actions` is populated; blank entries are dropped.
- Appends `PR: #<n>` when `pr_number` is populated (schema 1.3+).

## `load_cross_team_prior_outputs(workspace, target_team) -> str`

Concatenates compressed summaries of prior handovers whose `to_team_suggestions` include `target_team`. Newest-first. Total capped at 2500 chars.

Teams call this only when `prior.iteration > 1` — the first visit stays cheap. `_static_pipeline._build_base_user_text` (see [teams_taxonomy.md](teams_taxonomy.md)) is the canonical call site for static teams; `SingleAgentTeam` / `OrchestratedTeam` follow the same iteration-gated pattern.

## Decision-only fast-path

On *revisit* hops — when a team has already run at least once on this chain — the `load_cross_team_prior_outputs` caller passes `decisions_only=True`. The prior block then drops the `Summary:` section (which the receiving team already produced and holds), retaining only `Decisions:`, blocking `Open questions:`, `Forbidden:`, and the `PR: #<n>` line. First-pass behavior (no revisit count yet) is byte-identical to today.

Implementation:
- `_assemble(handover, *, include_summary, max_chars)` is the single rendering path; `summarize_handover` and `summarize_handover_decisions_only` both delegate to it.
- `load_cross_team_prior_outputs` and `_format_prior_block` accept a keyword-only `decisions_only: bool = False`; callers outside `_base.py`/`_static_pipeline.py` are unaffected (default `False`).
- Revisit detection lives in `alan/teams/_base.is_revisit(workspace, team, chain_id)`, keyed on `workspace.meta.revisit_counts` via the shared `revisit_key` builder from `alan/workspace.py`. `compression.py` never learns about `revisit_counts`.
- Measurement: the ≥20% `cache_read_input_tokens` reduction on revisit-heavy workspaces is an **operational measurement** (compare `WorkflowTask.cache_read_input_tokens` before/after across ≥10 revisit-heavy workspaces against the PR #153 usage block) — it is **not** a blocking CI gate. Deterministic gates (pytest 100%, ruff, mypy, pyright, lizard, vulture) remain the merge criteria.

This **supersedes** the earlier completed Axis 8 plan in the `ship-axis-2-and-8-token-compacti` workspace, which (a) detected revisit via the `Handover.revisit_of` field, (b) mutated `summarize_handover` in place, and (c) bundled Axis 8 with the LLM-based Axis 2 compaction in one PR. The differences are deliberate: `revisit_of` is set only on the immediately-preceding hop (null on the seed/first forward), so it under-detects multi-hop revisits, whereas the per-`(team, chain)` `revisit_counts` counter is monotonic and already maintained by the loop guard; and a separate `summarize_handover_decisions_only` keeps first-pass output byte-identical instead of branching inside `summarize_handover`. Axis 2 (LLM compaction, documented below) now lives in the same module: the two compose cleanly because `decisions_only=True` skips the `Summary:` section entirely and so short-circuits before any LLM call — the fast-path stays pure-lexical, and the LLM path only fires on first-pass / non-revisit hops where `include_summary` is true.


## LLM compaction

When `len(full_synthesis) > _LLM_COMPACT_THRESHOLD` (default 1500 chars) and a `Workspace` is passed to `summarize_handover`, `_compact_synthesis` routes the prose body through a Haiku call via `run_agent_streaming(model="haiku", max_turns=1, deny_all_tools=True, timeout_seconds=15)`.

**Helper chain:** `summarize_handover` → `_compact_synthesis` → `_read_compact_cache` (hit) or `_run_compact` → `_call_compactor_llm` + `_write_compact_cache`.

**Cache layout:** `<workspace>/handovers/.compact/<handover_id>.txt`. The first line is a versioned header `# v=<sha256(_COMPACTOR_SYSTEM_PROMPT)[:8]> target=<chars>`; the body follows on line 2+. A header mismatch (stale prompt) is treated as a miss and triggers a fresh Haiku call. The on-disk handover YAML is never modified.

**Fallback path:** any `ClaudeCliError` (including timeout after 15 s) is caught in `_run_compact`, logged at WARNING (`alan.compression` logger), and the original `full_synthesis` text is returned unchanged. The `ALAN_LLM_COMPACT_DISABLE` env var skips the LLM path entirely (ops kill-switch).

**Invariant:** `decisions[]`, `open_questions[]`, `forbidden_actions[]`, and `pr_number` are rendered via the existing `_format_*` helpers and are **never** passed to the Haiku prompt.

**New eval metric:** `decision_survival_rate_llm_compact` (in `tests/eval/test_decision_survival.py`) runs the 2-/4-/6-hop chain through the LLM-active path using a stub `claude` binary. Acceptance gate: `>= 0.95` at every depth. Because Decision IDs are rendered structurally by `_format_decisions`, they are independent of the Haiku output and always survive.

### Hardening: `deny_all_tools` + preserve-verbatim prompt (since 2026-07-12, PA-05/PA-29)

Production logs showed 25 `"LLM compactor failed … falling back to lexical"` warnings (2026-06-09→07-10, clustering in early July) — every one traceable to the same self-inflicted bug: `_call_compactor_llm` ran with `max_turns=1` but *without* `deny_all_tools=True`, so `_build_cmd` appended the project-doc block ("Read it for context before you start") whenever a project doc was active for the workspace. The compactor's single turn was then spent attempting a tool call to read that doc, hit the turn ceiling (`TurnCeilingReached`), and `_run_compact` caught it and silently fell back to the raw, uncompacted `full_synthesis` — degrading exactly the prose context every downstream team relies on.

Two changes close this:

1. **`_call_compactor_llm` now passes `deny_all_tools=True`.** Combined with the `_build_cmd` change described in `docs/architecture/claude_cli_streaming.md` (append composition under `deny_all_tools`), the compactor no longer receives the project-doc block *or* the headless-execution block — it has no tools to act on either instruction, so its one turn is spent producing the summary. Its system prompt is no longer passed to `--append-system-prompt` unmodified, though: `_build_cmd` appends the shared `NO_TOOLS_BLOCK` (`alan/claude_cli_grants.py`) telling the compactor it has no tools and must not call, describe or simulate one. The compactor is a verbatim-preservation transform, so the block's absent-evidence clause — strictly conditional on a role whose contract asks for a repository-grounded judgement — does not apply, and the block instructs a verbatim or short-summary role never to mention missing evidence or missing tools; the compacted text must not gain an "evidence absent" sentence. `tests/test_compression_llm.py` pins the append as containing `NO_TOOLS_BLOCK` and omitting both other blocks, and the compactor identifier-survival sweep below is the behavioural check that the block did not disturb survival. `--disallowedTools '*'` is now set on every compactor call, matching the pattern `manager_summary.py` already used (see `docs/architecture/manager_summary.md`).
2. **`_COMPACTOR_SYSTEM_PROMPT`'s preserve clause is now a concrete verbatim-preserve list** instead of the old generic "preserve the key technical context, progress, and next-step guidance":

   > Preserve verbatim: file paths, commands, error messages and gate names, PR/issue numbers, unresolved blockers, and the concrete next step. Prefer dropping narrative and adjectives over dropping any identifier. If you must cut, cut oldest progress detail first.

   This tells the model exactly which tokens are load-bearing (the identifiers a downstream team greps for or pastes into a shell) versus which are safe to trim (narrative color, adjectives, older progress detail) when the `_COMPACTOR_TARGET_CHARS` (1200) budget is tight.

Because `_COMPACTOR_VERSION` is `sha256(_COMPACTOR_SYSTEM_PROMPT)[:8]`, changing the prompt string automatically invalidates every existing on-disk `.compact/<handover_id>.txt` cache entry (the version header no longer matches, so `_read_compact_cache` treats it as a miss) — this is the cache's designed self-invalidation mechanism working as intended, not a change to the cache format itself.

**Post-deploy signal to watch:** the operational expectation is a measurable drop in the `"LLM compactor failed … falling back to lexical"` WARNING count in `~/.alan/logs/`. This is observed operationally, not asserted by any test or CI gate.

### Compactor identifier-survival eval suite (PA-05, PA-29 goldens)

`alan/compactor_eval.py` is a golden-suite eval with its own layout (per-case directory with `case.json` + `context.txt`, plus a `rubric.yaml` suite manifest) that scores identifier survival instead of verdict classification:

- **Fixtures:** `tests/eval/golden/compactor/case_0{1..5}/` — five long, realistic handover syntheses (secrets scrubbed), each `case.json` declaring a `must_survive: [...]` list of exact-substring identifiers (file paths, PR numbers, commands, error/gate text, a blocking open question, a next-step instruction) and a `citation` explaining why that identifier set matters.
- **Manifest:** `tests/eval/golden/compactor/rubric.yaml` carries `min_cases: 5` — the compactor has no situation taxonomy (unlike the judge suite's six `SITUATIONS`), so a bare case-count floor is the manifest's only structural check, validated by `validate_case_count`.
- **Scoring:** `compactor_case(case, run_compact)` calls `run_compact(case.context)` — defaulting to `_default_compact`, which calls the real `alan.compression._call_compactor_llm` (including its pre-existing `[:1200]` output slice, so the suite measures production reality, truncation included) — and computes `survival_rate` as the fraction of `must_survive` identifiers present verbatim in the output. A `ClaudeCliError` drops the case from scoring (fail-open, matching `judge_eval.judge_case`) rather than counting it as 0.0. `score_compactor_cases` averages `survival_rate` over scored cases; an empty scored set is `HARNESS_ERROR`, a case-count shortfall is `GOLDEN_SUITE_ERROR`, matching the judge/planner suites' signal vocabulary.
- **Wiring:** `run_sweep`/`main()` are invoked by the weekly `.github/workflows/behavioral_eval.yml` sweep (`uv run python -m alan.compactor_eval`, alongside the planner and judge sweeps) and file/update a `behavioral-eval-compactor-harness`-labeled issue on `HARNESS_ERROR`/`GOLDEN_SUITE_ERROR` only — a low survival rate is printed (`EVAL_METRIC compactor_identifier_survival_rate=...` plus one `IDENTIFIER_DROPPED` line per case with a miss) but never fails the step, since the compactor model is non-deterministic and this suite is advisory by design. `tests/test_compactor_eval.py` covers the module with a fully mocked `run_compact`/`_call_compactor_llm` for the standard 100%-coverage `uv run pytest` run. `tests/eval/test_compactor_survival.py` is the opt-in (`@pytest.mark.eval`) counterpart that exercises the *real* Haiku compactor and asserts each case's identifiers survive — auto-skipped from the default suite by `tests/eval/conftest.py`, run via `uv run pytest -m eval --no-cov -s`, and never coupled to the pull-request gate.
- **Import-layer placement:** `alan/compactor_eval.py` imports `alan.claude_cli` (for `ClaudeCliError`) and `alan.compression` (for the real `_call_compactor_llm`), both lower layers than `judge_eval` in `pyproject.toml`'s exhaustive layers list — so `compactor_eval` sits immediately below `judge_eval` and above `teams`/`compression`/`claude_cli`, mirroring `judge_eval`'s own placement rationale in `docs/architecture/eval_scaffolding.md`.

## Where the 2500-char cap lives now (schema 1.6)

Until schema 1.6 the cap was applied at the producer — `summary_for_downstream` was sliced to 2500 chars before the handover was saved, which silently lost the tail of every long synthesis. Schema 1.6 renamed the field to `full_synthesis` and dropped that producer-side slice. The cap now lives at the five consumer sites that actually need a prompt-budget guard:

1. `summarize_handover` — joined-output `text[:max_chars]` slice (this file).
2. `_base.SingleAgentTeam._build_user_text` — `prior.full_synthesis[:DEFAULT_MAX_CHARS]`.
3. `_orchestrated._orchestrator_user_text` — same.
4. `_static_pipeline._build_base_user_text` — same.
5. `implementation._prior_plan_block` — same.

`loop_guard._is_stalled` reads `full_synthesis` untruncated. Jaccard itself is a set-cardinality ratio, but `_trigrams()` builds an O(n) set per input — so untruncated comparison is still linear in `full_synthesis` length (acceptable in practice; bound it here too if synthesis sizes ever grow into the megabyte range). `HandoverDetailPanel` (frontend) displays the full text — no cap, no scroll wrapper.
