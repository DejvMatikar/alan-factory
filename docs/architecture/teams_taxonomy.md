# Teams taxonomy

**Source:** `alan/teams/_base.py`, `_orchestrated.py`, `_static_pipeline.py`, `__init__.py` (TEAMS registry), per-team modules.

Hybrid setup: each charter team is either a "dynamic" team (orchestrator dispatches an LLM-chosen worker set) or a "static" team (pre-declared role pipeline).

## Three-layer prompt contract

- **Layer 1** — Task description rubric. Six fields (`Goal` / `Definition of Done` / `Constraints` / `Out-of-scope` / `References` / `Suggested team`). The first five are server-enforced on workspace creation by the chat-driven flow (`_missing_layer1_headers` regex check; see [chat_workspace_creation.md](chat_workspace_creation.md)). `Suggested team` is conveyed structurally via `proposal.team` (validated against `alan.teams.TEAMS`), so the markdown header is encouraged in the chat draft but not enforced in the extracted task description (the extraction contract is YAML — `EXTRACT_TASK_YAML` parsed via `yaml.safe_load`). Note: the frontend entry point for the chat-driven creation flow was removed in Phase 1 (2026-06-02); the backend enforcement remains.
- **Layer 2** — Worker-prompt markers. Dynamic-team orchestrators emit per-worker prompts that must include `SCOPE:`, `COVERAGE:`, `SUCCESS:`, and `CONTEXT:` on their own lines with non-whitespace content following the colon. `parse_dispatch` in `_orchestrated.py` rejects orchestrator output that misses any marker (`(?m)^\s*<FIELD>:\s*\S`). `CONFIDENCE_REPORTING_BLOCK` is appended to every worker prompt automatically.
- **Layer 3** — `Handover.decisions` non-empty for `forward → implementation|verification`. Enforced at save time by `_validate_layer3_decisions` (see [handover.md](handover.md)).

## Primitives

### `SingleAgentTeam`

One LLM node. Default model `sonnet` (configurable on the dataclass).

### `OrchestratedTeam` (dynamic)

`_orchestrated.py`. Frozen dataclass parameterised by `name`, `orchestrator_prompt`, `synthesizer_prompt`, `worker_system_template`, `to_team_suggestions`, model defaults, and the opt-in `verifier_prompt: str | None = None`.

**Run phases:** orchestrator → dispatched workers → (optional) verifier → synthesizer.

Orchestrator returns `{"agents":[{"role", "prompt"}]}`; `parse_dispatch` validates against the Layer-2 marker contract. `"verifier"` is a reserved role and is rejected if the orchestrator tries to dispatch it.

**Worker tool grants.** `_phase_workers` resolves the workspace's governing `EngineConfig` (`governing_config_for_ws`) and passes `WorkerOverrides(allowed_tools=render_allowed_tools(cfg, team.name))` to every worker node. With the default engine config this grants `WebSearch`/`WebFetch` to `research`, `brainstorming`, and `opportunity_scout` workers (explicit per-team grant, not ambient user settings — PA-12). Orchestrator, verifier, and synthesizer nodes run with no overrides, i.e. base tools only.

**Untrusted-content fence.** Worker, verifier, and synthesizer system prompts are composed with the shared `UNTRUSTED_CONTENT_NOTICE` (PA-11): worker output — which may quote arbitrary web content — is pasted verbatim into the verifier's and synthesizer's `user_text`, so those prompts must treat quoted content as data, never instructions. The orchestrator does not get the notice (its input is the task description plus prior handover, and it must emit JSON only). The notice literal lives in `alan/claude_cli.py` (the prompt-assembly hub) and is re-exported from `_base.py` via `UNTRUSTED_CONTENT_NOTICE as UNTRUSTED_CONTENT_NOTICE` — one shared constant, no second competing notice. Coverage beyond the orchestrated teams: `_static_pipeline.py`'s `assemble_role_system_prompt` appends it to every `StaticPipelineTeam` role's system prompt (so verification's `diff_reviewer`/`scenario_runner`/`judge` all carry it, alongside maintenance/architecture/implementation_plan), and `claude_cli.py` appends it to the two human-facing planning/advisory chat prompts (`format_build_system_prompt`, `format_explore_system_prompt`). Those notices are the whole guard for the orchestrated teams and the two chat prompts — notice-only, no hard containment fence — since they either compose the system's own semi-trusted structured output or are read-only advisory chats; hard fencing of genuinely external bodies (review/comment text, subprocess-output tails) is a separate, narrower mechanism scoped to the surfaces that actually interpolate that text verbatim. Static-pipeline roles are no longer notice-only: the notice stays as the second layer, and every earlier role's output reaches a later role as a fenced role output (see `StaticPipelineTeam` below).

**Opt-in verifier phase.** When `verifier_prompt` is set, `_phase_verify` runs a single `verifier` node after all workers complete and before the synthesizer. Its `user_text` is assembled by `_peer_outputs_text` — the same helper used by the synthesizer — so the verifier's input provably contains the complete output text of every dispatched worker. The verifier depends on all worker nodes; the synthesizer in turn depends on all worker nodes plus `verifier`. The verifier's output is appended as a `## verifier` block to the synthesizer's `user_text`, so verifier findings flow into the consolidated summary.

Teams that leave `verifier_prompt=None` (e.g. `brainstorming`) are unaffected — no verifier node is created and the synthesizer depends only on the worker nodes.

**Completeness gate.** When a `verifier_prompt` is set, `run()` parses the verifier's `GAPS`/`CONFIDENCE`/`COMPLETENESS` tail (`parse_verifier_metrics`) and surfaces the three values as first-class `Handover.verifier_completeness` / `verifier_confidence` / `verifier_gaps` fields (schema 1.16, see [handover.md](handover.md)). A completeness score below `ALAN_COMPLETENESS_THRESHOLD` (default `0.7`) attaches a blocking `low_completeness` `OpenQuestion` to the outgoing handover, which the router turns into a structured park `Blocker` instead of silently forwarding an under-covered run (see [router.md](router.md)).

**Report artifact.** After synthesis, `run()` saves the synthesizer's full (untruncated) output to `invocations/<inv_id>/report.md` via `alan.workflow.save_invocation_report` — the same atomic tmp-file-then-`Path.replace` idiom `workflow.py` already uses for `workflow.yaml` — and appends an `ArtifactRef(path=f"invocations/{inv_id}/report.md", description=f"Full {name} report")` to the outgoing handover's `artifacts_produced` (surfaced in the UI's Deliverables section and downloadable via `GET /api/workspaces/{id}/artifacts?path=...`). This exists because research-family synthesizer prompts can no longer fit their full output in the `DEFAULT_MAX_CHARS`-truncated handover slice consumers read — the report file is the durable, full-length surface; the handover's `full_synthesis` remains the prompt-budget-capped summary. Controlled by a `writes_report: bool = True` dataclass field; the write is wrapped in `try`/`except OSError` — a failed write is logged at warning and the run always continues with `artifacts_produced` simply omitting the ref (never a dangling reference to a file that doesn't exist). `ui_review` sets `writes_report=False` since its scope statement promises the team never writes any file.

**Direct instances:** `brainstorming` (charter-derived prompts, no verifier phase), `opportunity_scout` (`verifier_prompt=_VERIFIER_PROMPT`; the verifier validates four-filter verdicts after workers complete), and `ui_review` (`verifier_prompt=_VERIFIER_PROMPT`; read-only advisory UI audit — see below).

**`ui_review`.** Strictly advisory and read-only: base tools only, no `team_tools` grant, so it can never `Edit`/`Write`/`NotebookEdit`. Each run selects 2-4 perspectives from a fixed four-item catalogue — design-system/convention conformance, UX quality, WCAG-AA accessibility, and cross-screen consistency drift — and consolidates their findings into a severity/effort-ranked inventory plus a proposed-briefs section, each brief in the standard six-part request shape (Goal / Definition of Done / Constraints / Out-of-scope / References / Suggested next team) so it is ready to dispatch. Findings never feed any gate or verdict; `SEVERITY_LABELS`/`EFFORT_LABELS` are guarded by a disjointness test against verdict vocabulary (`APPROVE`/`CHANGES_REQUESTED`/`GO`/`NO-GO`/`pass`/`fail`). **Grounding-run discipline:** exactly one selected perspective per run — `accessibility_reviewer` if selected, else the first selected perspective in catalogue order — best-effort runs the live `capture:screens` step and reads existing `alan/web/e2e/mobile-audit/` artifacts; every other perspective reads source and artifacts only. The team never invokes the live `audit:mobile` runner itself — reading its existing output is always the safe fallback, since [screen_capture.md](screen_capture.md) documents `audit:mobile` as deliberately not inter-run concurrency-safe. `to_team_suggestions=()`, and `ui_review` is registered with an empty `_TRANSITION_GRAPH` entry and as a member of the default `FlowGraphPolicy.terminal_teams` — the same "stops cleanly, no auto-forward" shape as `maintenance`/`triage`/`deploy_verify` — so a completed run always resolves to a clean stop rather than `select_next_team` treating an unregistered `from_team` as an escalation. **Interim consumption path (until a dedicated front door lands):** a human reads the findings inventory and proposed briefs directly from the workspace's handover panel in the UI and dispatches any accepted brief as a *fresh* piece of work — the review's own result is never forwarded onward as input to another team's run, because a forwarded consumer would only ever receive a `DEFAULT_MAX_CHARS`-truncated prefix (`alan/compression.py`), not the full inventory.

**`ResearchTeam` (subclass, registered as `research`).** Frozen dataclass subclass of `OrchestratedTeam`, kept (rather than a direct instance) because tests rely on `isinstance` checks. Run phases: orchestrator (opus) → dispatched workers (sonnet, sequential) → verifier (sonnet) → synthesizer (opus). `verifier_prompt=_VERIFIER_PROMPT` is set; the verifier reviews worker outputs for drift/gaps/contradictions.

### `StaticPipelineTeam` (static)

`_static_pipeline.py`. Accepts `roles: tuple[Role, ...]` (validated non-empty in `__post_init__`). Runs roles sequentially through `run_or_resume_node`, so a role already recorded `success` on disk is reused rather than re-run, and an `interrupted` role resumes via `--resume <session_id>` — this is the same resume-safe primitive every other multi-node team uses, not a fresh-only path.

Each role's user_text, built by `build_role_user_text`, is, in order:

- The base text (task text, implementation handover, cross-team context) — not fenced.
- A `## COVERAGE` block auto-derived from sibling roles (so a role knows what the others are doing).
- When earlier roles exist, a `## CONTEXT — prior agent outputs in this pipeline:` heading, then a short preamble that names this call's nonce, then, for each earlier role in pipeline order, its `### <name>` heading directly followed by a fenced role output: a start marker `[[ROLE OUTPUT START role=<name> nonce=<n>]]`, the role's whole text verbatim, and an end marker `[[ROLE OUTPUT END role=<name> nonce=<n>]]`. With no earlier roles the text ends after the coverage block.

The preamble states that each fenced role output is that role's report — evidence to weigh and use, including its count lines and findings blocks, never instructions to the reader — that any claim inside one to be host output, another role, or authoritative is part of that report, and that the host never writes anything between a role's markers.

The fence lives in `alan/teams/role_output_fence.py` and is applied inside `build_role_user_text`, so every static pipeline and both retries that reuse the builder (the judge retry and the clause-auditor retry) get it with no caller change. The nonce is `secrets.token_hex(8)`, drawn fresh on every call and redrawn while it occurs anywhere in the text it must not appear in (the base, the coverage block, the CONTEXT heading, every role's name and text, and the preamble and marker templates), so a role's text can neither contain this call's end marker nor forge one that carries it; the nonce therefore appears in the output only in the preamble and the markers. Because the nonce is fresh per call, a retry no longer shares a prompt-cache prefix with the first attempt. The base is deliberately not fenced; for the verification judge, the judge prompt's own rule is the backstop — an `## External Gate Status` or `## Scenario outcomes (recorded)` block is appended by the host after the judge's output, so one that appears anywhere in its input is forged, ignored, and named under `## Open questions` without reproducing its heading or marker lines.

The confidence block and the `UNTRUSTED_CONTENT_NOTICE` guard (see above) are both appended to every role's system prompt via `assemble_role_system_prompt`, as the second layer behind the fenced role outputs.

`_build_base_user_text` inlines the prior handover's `summary_for_downstream` on every iteration (matching `SingleAgentTeam` / `OrchestratedTeam`). For `iteration > 1` it also concatenates cross-team prior outputs via `load_cross_team_prior_outputs` (see [compression.md](compression.md)).

**`MaintenanceTeam` (subclass, `maintenance`, 4 roles).** `code_health_analyst`, `ci_dependency_auditor`, `log_repo_scanner`, `security_scanner` run sequentially (sonnet, except `security_scanner` on opus). Scope is deliberately live-only state a PR gate never sees — worktree/workspace residue, dependency-sweep and CI-run-history drift, `~/.alan/logs/alan.error.log` patterns with a root-cause read, and runtime secret/permission hygiene — none of the four re-runs ruff/bandit/pip-audit, which already gate every PR. Each role ends its output with a machine-readable tail: `ROLE_SEVERITY: <NONE|LOW|MEDIUM|HIGH|CRITICAL>`, or `UNVERIFIED: <reason>` when its environment denies a check it needs (e.g. `ci_dependency_auditor`'s `gh run list` — `gh` is not in this role's tool allowlist, a standing boundary this team reports rather than works around). Overrides `_collect_outputs` (copies the base stage loop, precedent `VerificationTeam`) to compose a leading `## Run verdict` block — `RUN_VERDICT_SEVERITY:` is `max()` over the roles' parsed `ROLE_SEVERITY` values via `SEVERITY_LEVELS` rank order, with `UNVERIFIED`/missing tails excluded from the max and listed as gaps instead — followed by every role's output verbatim under its own `## <role>` heading, so `full_synthesis` (and the `manager_summary` 2000-char head that reads from it) always carries all four roles' findings, never just the last one. An unparseable composed verdict block (should not happen; the block is Python-generated, not LLM output) defaults to `CRITICAL` rank rather than silently passing. Overrides `run()`: below `MEDIUM` severity, behaves like `terminal=True` always did (`build_terminal_handover`, `self.terminal` is otherwise inert on this subclass); at `MEDIUM` or above, returns `build_forward_handover` with `to_team_suggestions=[]` and exactly one blocking `OpenQuestion(resolver_role="human")` — `router._resolve_blocking` runs before `_resolve_forward`, so this reaches `await_human` (a surface the user already monitors) with no `router.py`/`engine_config.py` change needed. A run where every role is a gap (e.g. `ci_dependency_auditor`'s permanent `gh` denial) never parks on its own — gaps are excluded from the severity roll-up, so only a real `MEDIUM`+ finding escalates.

**`ArchitectureTeam` (subclass, `architecture`, 6 roles).** In order: `structure_mapper`, `complexity_analyst`, `debt_auditor`, `change_planner`, `devil_advocate`, `judge`. `change_planner` is the 4th role (between `debt_auditor` and `devil_advocate`); short-circuits with `NO_CHANGE_LIST — analysis only.` when the task is analysis-only. No mode-detection in code — the role's prompt does it. `judge` runs last (opus model, the other five run sonnet): it validates each prior role's output for drift/gaps/contradictions, resolves `devil_advocate`'s `## Inversion test` and `## Assumptions` claims into either `## Final Architecture Note` or `## Open questions`, and produces the final consolidated synthesis — including the same fenced ```yaml decisions.yaml``` block the other `StaticPipelineTeam` synthesizers emit — that `implementation_plan` reads as its input.

**`VerificationTeam` (subclass, `verification`, 3 roles).** Subclass for `_maybe_post_review` — see [implementation_pipeline.md](implementation_pipeline.md).

**`ImplementationPlanTeam` (subclass, `implementation_plan`, 6 roles).** `architect`/`synthesizer` → opus; the four analysts (`backend_analyst`, `frontend_analyst`, `qa_planner`, `devil_advocate`) → sonnet. Synthesizer emits a fenced ```yaml decisions.yaml``` block (carries `decisions:` 3-10 entries + optional `files_to_edit:`). `run()` overrides the base `StaticPipelineTeam.run` to parse the block via `_parse_decisions_block` (`tuple[list[Decision], list[str]]`) and populate `Handover.decisions` + `Handover.files_to_edit`. Missing/malformed blocks raise `ImplementationPlanError`; `files_to_edit:` is optional (default `[]`), but a present-but-malformed value (non-list, blank entry) is rejected. Legacy module-level `run_team()` (used by `/api/plan`) keeps the old `run_agent` synchronous path.

### Bespoke primitives

Teams whose run/dispatch logic doesn't fit the three shared shapes above get a hand-rolled class satisfying the `Team` protocol directly.

**`ImplementationTeam`** (`implementation`). See [implementation_pipeline.md](implementation_pipeline.md).

## Registered teams

Generated from `alan.teams.TEAMS` / `TEAM_ROLE_SUMMARIES` (plus the dispatchability sets) by [`scripts/gen_teams_table.py`](../../scripts/gen_teams_table.py) — run `uv run python scripts/gen_teams_table.py --check` to verify this block is current, or `--write` to regenerate it after the registry changes. `tests/test_gen_teams_table.py` fails CI on drift.

<!-- BEGIN GENERATED: teams-table -->
| Name | Primitive | Dispatchability | Summary |
|------|-----------|------------------|---------|
| `architecture` | ArchitectureTeam | dispatchable | Design the shape of a change: ADR-style decisions, trade-off analysis, change_planner + judge. |
| `brainstorming` | OrchestratedTeam | dispatchable | Diverge on options the user has not yet narrowed down. |
| `brief_refine` | BriefRefineTeam | internal-only | Internal first hop on every newly created workspace: splits the brief into traceable requirements vs. inferred assumptions before planning starts. Auto-dispatched by the host; not user-dispatchable. |
| `decomposer` | DecomposerTeam | internal-only | Internal step after implementation_plan: decides split-vs-keep and, on split, injects a dependency-linked sub-DAG via the injection primitive. Not user-dispatchable. |
| `dependency_sweep` | DependencySweepTeam | internal-only | Autonomous weekly sweep: discovers open Dependabot PRs, dispatches them to pr_merge or implementation_fix, and runs pip-audit. Self-rescheduling; not user-dispatchable. |
| `deploy_verify` | DeployVerifyTeam | dispatchable (post-initial) | Post-merge gate: polls GitHub Actions ci-deploy runs and independently probes the production health endpoint; confirms deployment or escalates. |
| `implementation` | ImplementationTeam | dispatchable | Write the code for an existing implementation plan; pushes a branch and opens a PR. |
| `implementation_fix` | ImplementationFixTeam | dispatchable (post-initial) | Address Copilot / verification REQUEST_CHANGES on an existing PR. Requires a prior handover that carries a pr_number. |
| `implementation_plan` | ImplementationPlanTeam | dispatchable | Turn a vague task into a concrete implementation plan before any code is written. |
| `maintenance` | MaintenanceTeam | dispatchable | Live-state health check: four roles report on environment residue, CI/dependency-sweep drift, log patterns, and runtime secret hygiene that no PR gate observes. Rolls every role's findings and any unverified (environment-blocked) checks into one leading verdict; a medium-or-worse run parks awaiting human triage instead of terminating silently. |
| `opportunity_scout` | OrchestratedTeam | dispatchable | Convergent GO/NO-GO screen of ONE candidate opportunity via four filters. Not for discovering candidates — use brainstorming/research for that. |
| `pr_merge` | PrMergeTeam | dispatchable (post-initial) | Squash-merge the workspace's PR once gates and Copilot/human conversations are green. Requires a prior handover carrying a pr_number. |
| `research` | ResearchTeam | dispatchable | Research: orchestrator + workers + synthesizer gathers information from the codebase / web before deciding. |
| `triage` | — (no `TEAMS` instance) | not user-dispatchable (no `TEAMS` entry) | Read-only recurring sweep that surfaces failure signals (stuck jobs, CI failures, stuck PRs). Auto-rescheduled; not user-dispatchable. |
| `ui_review` | OrchestratedTeam | dispatchable | Review the existing web UI against the design system and UX: ranked findings inventory + ready-to-dispatch briefs. Strictly advisory and read-only. Not for building UI — use implementation for that. |
| `verification` | VerificationTeam | dispatchable | Judge an existing PR via reasoning + scenario walkthrough; posts an approving or REQUEST_CHANGES review. |
<!-- END GENERATED: teams-table -->

## `Team` Protocol

```python
class Team(Protocol):
    name: str
    def run(self, workspace, prior, emit, *, inv_id) -> Handover: ...
    def plan_workflow(self, workspace, prior) -> list[PendingTaskSpec]: ...
```

## Legacy `/api/plan` path

`POST /api/plan` is the original 6-role implementation-planning endpoint. It still uses the legacy module-level `run_team()` shim in `alan/teams/implementation_plan.py`, which calls the synchronous `claude_cli.run_agent` and streams per-role progress as SSE (`text/event-stream`). The final synthesizer output is written to `~/.alan/plans/<slug>.md` (slug derived from title; collisions get `-2`, `-3`, … suffixes).

The SSE generator captures a `closed` flag so the background thread stops enqueueing events on client disconnect. `claude_cli.format_user_text(title, description)` is the shared helper that builds the stdin payload for both this endpoint and the orchestrated/static team pipelines.

Charter-team events shifted from `role_started`/`role_completed` to `node_started`/`node_completed` (carrying the full `WorkflowTask` JSON); the legacy `/api/plan` SSE path is untouched.
