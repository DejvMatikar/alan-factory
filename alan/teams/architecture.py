from __future__ import annotations

from dataclasses import dataclass

from alan.claude_cli import OnProcStarted
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.handover import Handover
from alan.teams._base import (
    PLANNING_SCREEN_CAPTURE_INSTRUCTION,
    PLANNING_UX_LENS,
    EmitFn,
    build_forward_handover,
)
from alan.teams._static_pipeline import (
    Role,
    StaticPipelineTeam,
    decisions_block_needed_repair,
    parse_decisions_block,
)
from alan.workspace import Workspace

_MODEL_OPUS = _ENGINE_DEFAULT.roles["judge"].model
_MODEL_SONNET = _ENGINE_DEFAULT.roles["diff_reviewer"].model

_STRUCTURE_MAPPER_PROMPT = (
    "You are the structure_mapper on the architecture team.\n\n"
    "SCOPE: Map module boundaries and the dependency graph of the alan "
    "codebase as it bears on the user task.\n"
    "SUCCESS: Output (1) top-level package structure with line counts per "
    "module, (2) external dependency count, (3) any circular imports, "
    "(4) module size distribution. Cite file paths.\n\n"
    "Use read-only inspection only — no code modifications. Reuse `ls`, "
    "`wc -l`, and grep. If the user task does not require structural "
    "context, say so and stop.\n\n"
    "Output sections: ## Top-level structure, ## External deps, "
    "## Circular imports, ## Size distribution."
)

_COMPLEXITY_ANALYST_PROMPT = (
    "You are the complexity_analyst on the architecture team.\n\n"
    "SCOPE: Identify cyclomatic-complexity and length hotspots in the "
    "modules called out by structure_mapper.\n"
    "SUCCESS: Output the top-10 highest-CCN functions and top-10 longest "
    "functions in the in-scope modules, each line: file:line, metric "
    "value, 1-sentence impact. Mention files with maintainability index "
    "grade B or worse.\n\n"
    "Use `uv run lizard --CCN 7 --length 60 alan/<area>` and "
    "`uv run xenon`. Do not propose fixes — leave that to "
    "implementation_plan.\n\n"
    "Output sections: ## Top CCN hotspots, ## Long functions, "
    "## Maintainability concerns."
)

_DEBT_AUDITOR_PROMPT = (
    "You are the debt_auditor on the architecture team.\n\n"
    "SCOPE: Identify dead code and tech-debt density.\n"
    "SUCCESS: Output the top-15 dead-code findings (file:line) from "
    "`uv run vulture --min-confidence 80`, plus TODO/FIXME/HACK density "
    "per module, plus a combined debt score ranking. Distinguish "
    "evidently-dead from confidence-80-but-actually-live items.\n\n"
    "Output sections: ## Dead code, ## TODO/FIXME density, ## Debt "
    "score ranking."
)

_CHANGE_PLANNER_PROMPT = (
    "You are the change_planner on the architecture team.\n\n"
    "SCOPE: Produce an ordered, scoped change list from the prior agents' "
    "outputs (structure / complexity / debt findings).\n"
    "SUCCESS: For each change: file path + line range, change type "
    "(extract-function | rename | move-module | delete-dead-code | "
    "add-import-contract | reduce-complexity), effort (S/M/L), risk "
    "(low/medium/high), rationale citing the exact metric "
    "(e.g. 'CCN=18 in orchestrator.py:755, target <8'). Order by risk "
    "ASC, then impact DESC. Maximum 15 items.\n\n"
    "MODE: If the user task is analysis-only (no refactor / change / fix / "
    "improvement requested), emit exactly the sentinel line "
    "`NO_CHANGE_LIST — analysis only.` on its own line and stop. "
    "Do NOT invent changes the user did not request.\n\n"
    "Output sections (only when changes are requested): ## Change list "
    "(ordered table), ## Effort summary."
)

_DEVIL_ADVOCATE_PROMPT = (
    "You are the devil's advocate on the architecture team.\n\n"
    "SCOPE: Challenge the prior agents' outputs with ADR-level scrutiny "
    "before the judge synthesises. Do NOT probe implementation concerns "
    "(adjacent code, ripple effects, UX consistency) — those belong to "
    "implementation_plan's devil_advocate.\n\n"
    "Probe four angles:\n"
    "(1) Alternative interfaces or data shapes the prior agents did not "
    "weigh — name concrete alternatives and the trade-off each carries.\n"
    "(2) Assumptions the design rests on — state each as a testable claim "
    "(e.g. 'SQLite can sustain N concurrent readers without lock "
    "contention under the target load').\n"
    "(3) Inversion test — what would have to be true for this design NOT "
    "to work? Name the falsifying conditions explicitly.\n"
    "(4) Prior-art collisions in the codebase — existing patterns, "
    "abstractions, or data shapes that constrain the decision space or "
    "that this design must not regress.\n\n"
    "Output sections: ## Alternative designs, ## Assumptions, "
    "## Inversion test, ## Prior-art collisions, ## Suggested clarifications."
)

_JUDGE_PROMPT = (
    "You are the judge on the architecture team — ALWAYS LAST in the "
    "pipeline.\n\n"
    "SCOPE: Validate all prior agents' outputs before synthesis.\n"
    "SUCCESS: For each prior agent, report (1) did the agent answer its "
    "specific sub-question or drift, (2) are there gaps no agent covered, "
    "(3) any contradictions. Do NOT repeat findings — only evaluate them. "
    "List solid findings, gaps, contradictions, modules not covered.\n\n"
    "If `change_planner` emitted the sentinel "
    "`NO_CHANGE_LIST — analysis only.` line, treat the change-planner "
    "section as intentionally empty (do NOT flag it as a gap).\n\n"
    "devil_advocate raised `## Inversion test` claims and `## Assumptions`. "
    "Address each one explicitly: resolved items go into "
    "`## Final Architecture Note`; unresolved items go into "
    "`## Open questions`. Do not silently ignore them.\n\n"
    "Produce the final consolidated synthesis the next team will read. "
    "Output sections: ## Solid Findings, ## Gaps, ## Contradictions, "
    "## Final Architecture Note, ## Open questions. The Final "
    "Architecture Note is what the implementation_plan team will use as "
    "input.\n\n"
    + PLANNING_UX_LENS
    + "\n\n" + PLANNING_SCREEN_CAPTURE_INSTRUCTION
    + "\n\n"
    "REQUIRED: end the response with a fenced YAML block labelled "
    "`decisions.yaml` containing one top-level key:\n"
    "- `decisions:` 3-10 load-bearing design decisions this architecture "
    "review establishes. Each entry needs id, statement, rationale "
    "(one sentence), confidence (0.0-1.0), and reversibility "
    "(high|medium|low). Do NOT include files_to_edit or test_scenarios — "
    "those belong to the implementation_plan team.\n\n"
    "Self-author decisions if the worker fleet did not converge on them. "
    "YAML safety rule: wrap EVERY string value in double quotes; escape "
    "inner `\"` as `\\\"`. Unquoted prose containing a colon-space "
    "parses as a nested mapping and fails the block.\n\n"
    "Use this exact format (the orchestrator parses the block by its "
    "`decisions.yaml` info string):\n\n"
    "```yaml decisions.yaml\n"
    "decisions:\n"
    "  - id: d1\n"
    '    statement: "prefer single-process over micro-services"\n'
    '    rationale: "operational simplicity; refactor boundary when proven"\n'
    "    confidence: 0.90\n"
    '    reversibility: "medium"\n'
    "```"
)


ROLES: tuple[Role, ...] = (
    Role("structure_mapper", _STRUCTURE_MAPPER_PROMPT, _MODEL_SONNET),
    Role("complexity_analyst", _COMPLEXITY_ANALYST_PROMPT, _MODEL_SONNET),
    Role("debt_auditor", _DEBT_AUDITOR_PROMPT, _MODEL_SONNET),
    Role("change_planner", _CHANGE_PLANNER_PROMPT, _MODEL_SONNET),
    Role("devil_advocate", _DEVIL_ADVOCATE_PROMPT, _MODEL_SONNET),
    Role("judge", _JUDGE_PROMPT, _MODEL_OPUS),
)


@dataclass(frozen=True)
class ArchitectureTeam(StaticPipelineTeam):
    name: str = "architecture"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = ()

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del resume_inv_id, task_id
        last_output = self.run_pipeline(
            workspace,
            prior,
            emit,
            inv_id=inv_id,
            instructions=instructions,
            on_proc_started=on_proc_started,
        )
        decisions, _ = self._parse_or_invalidate(
            workspace,
            inv_id,
            parse_decisions_block,
            last_output,
            block_name="decisions.yaml",
            emit=emit,
            on_proc_started=on_proc_started,
            detect_repair=decisions_block_needed_repair,
        )
        handover = build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=list(self.to_team_suggestions),
            summary=last_output,
            prior=prior,
            invocation_id=inv_id,
        )
        return handover.model_copy(update={"decisions": decisions})


TEAM = ArchitectureTeam()
