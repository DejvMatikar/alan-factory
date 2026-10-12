from __future__ import annotations

from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION
from alan.teams._orchestrated import (
    FOUR_FIELD_REQUIREMENT,
    OrchestratedTeam,
)

PERSPECTIVE_ROLES: tuple[str, ...] = (
    "design_system_reviewer",
    "ux_reviewer",
    "accessibility_reviewer",
    "consistency_reviewer",
)

SEVERITY_LABELS: tuple[str, ...] = ("critical", "high", "medium", "low")
EFFORT_LABELS: tuple[str, ...] = ("trivial", "small", "medium", "large")

_SCOPE_STATEMENT = (
    "SCOPE — this capability reviews the EXISTING alan/web UI against the "
    "design system and the user's experience. It is strictly advisory and "
    "read-only: it never edits, writes, or otherwise changes a file, and "
    "never proposes building new UI. It is not a chat/consultant entry "
    "point and never auto-dispatches its own proposed briefs — a human "
    "reads the inventory and decides."
)

_PERSPECTIVE_CATALOG = (
    "Fixed catalogue of four review perspectives, in this order — select "
    "2 to 4 per run, matched to what this task actually calls for (never "
    "all four by default):\n"
    f"- {PERSPECTIVE_ROLES[0]}: conformance to docs/DESIGN_SYSTEM.md — "
    "tokens, shared primitives, the modal-key convention. Where the "
    "design system is silent on a pattern, report 'no norm exists, "
    "observed N variants' as evidence, never as a violation.\n"
    f"- {PERSPECTIVE_ROLES[1]}: flows, affordances, terminology, "
    "empty/loading/error states, cross-screen interaction consistency. "
    "Ground findings in component source under alan/web/src/ — a "
    "screenshot cannot show every state.\n"
    f"- {PERSPECTIVE_ROLES[2]}: WCAG-AA as a prose lens — keyboard and "
    "focus handling, ARIA semantics, color contrast, focus order. Read "
    "alan/web/src/ui-spec.ts for the semantic UI contract and any "
    "existing artifacts under alan/web/e2e/mobile-audit/ when present.\n"
    f"- {PERSPECTIVE_ROLES[3]}: cross-screen drift — spacing, casing, "
    "control variants, hand-rolled UI that should reuse a shared "
    "primitive instead."
)

_GROUNDING_RULE = (
    "GROUNDING-RUN DISCIPLINE — exactly one selected perspective holds "
    "this run's grounding duty: "
    f"{PERSPECTIVE_ROLES[2]} if it is among the selected roles, otherwise "
    "the first selected perspective in the catalogue order above. State "
    "in that agent's dispatched prompt that it holds grounding duty this "
    "run and may best-effort run the live screen-capture step and read "
    "existing alan/web/e2e/mobile-audit/ artifacts. Every other "
    "dispatched perspective must be told to skip live capture and read "
    "source and any already-existing artifacts only — concurrent live "
    "capture runs from the same review are forbidden by construction. "
    "Never dispatch an agent to invoke the live `audit:mobile` runner — "
    "reading its existing output is always the safe fallback."
)

_ORCH_PROMPT = (
    "You are the ui_review orchestrator.\n\n"
    f"{_SCOPE_STATEMENT}\n\n"
    f"{_PERSPECTIVE_CATALOG}\n\n"
    f"{_GROUNDING_RULE}\n\n"
    "Respond on your first turn with a single JSON object and stop. "
    "Schema:\n"
    '{"agents": [{"role": "<perspective_name>", "prompt": "<full 4-field '
    'prompt>"}]}\n'
    f"role must be one of: {', '.join(PERSPECTIVE_ROLES)}. Dispatch "
    "between 2 and 4 agents, never 1 and never all four unless the task "
    "genuinely calls for full coverage. No prose, no markdown fence, no "
    "tool calls.\n\n"
    f"{FOUR_FIELD_REQUIREMENT}"
)

_WORKER_PROMPT_TEMPLATE = (
    "You are a ui_review specialist with perspective '{role}'.\n\n"
    f"{_SCOPE_STATEMENT}\n\n"
    "Ground every finding in evidence — a file:line, a component name, or "
    "an observed-variant count — never a bare opinion. Where the design "
    "system is silent on a pattern, report 'no norm exists, observed N "
    "variants', never as a violation.\n\n"
    f"{PLANNING_SCREEN_CAPTURE_INSTRUCTION}\n\n"
    "GROUNDING DUTY — your dispatched prompt below states whether you "
    "hold this run's grounding duty. If you do, you may run the "
    "screen-capture step above and may read existing "
    "alan/web/e2e/mobile-audit/ baseline/report artifacts, but never "
    "invoke the live `audit:mobile` runner yourself. If you do not hold "
    "grounding duty, skip live capture entirely and review from "
    "component source and any artifacts already on disk."
)

_SYNTH_PROMPT = (
    "You are the ui_review synthesizer. Consolidate every dispatched "
    "perspective's findings, plus any verifier notes, into ONE inventory. "
    "Produce plain markdown with exactly these two sections, in order:\n\n"
    "## Findings Inventory — a table: Finding | Perspective | Severity | "
    "Effort | Evidence. Severity and Effort use only these labels — "
    f"Severity: {', '.join(SEVERITY_LABELS)}. Effort: "
    f"{', '.join(EFFORT_LABELS)}. Rank rows by severity then effort. "
    "Where a perspective reports 'no norm exists, observed N variants', "
    "keep that framing verbatim in the Evidence column — it is evidence, "
    "never a violation.\n\n"
    "## Proposed Briefs — one brief per group of related findings worth "
    "dispatching as fresh work, each in the standard six-part request "
    "shape: Goal, Definition of Done, Constraints, Out-of-scope, "
    "References, Suggested next team.\n\n"
    "NO LENGTH LIMIT — this inventory is the product. Include every "
    "finding from every dispatched perspective in the table; never "
    "summarize, cap, truncate, or drop findings to save space, no matter "
    "how long the output runs.\n\n"
    "VOCABULARY — this output is advisory only, never a gate: never use "
    "APPROVE, CHANGES_REQUESTED, GO, NO-GO, pass, or fail anywhere. "
    "Severity and effort exist purely to help a human rank work.\n\n"
    "Do not emit a 'Decisions' section or any fenced yaml decisions "
    "block — this run never forwards to another team; a human reads this "
    "inventory from the workspace and dispatches any accepted brief as "
    "fresh work."
)

_VERIFIER_PROMPT = (
    "You are the ui_review verifier. You receive the task and the full "
    "outputs of every dispatched perspective. Your ONLY job is quality "
    "control, not a verdict: check for drift (a perspective wandering "
    "outside its assigned lens), gaps (a finding with no concrete "
    "evidence — file:line, component name, or an observed-variant count), "
    "and vocabulary violations (APPROVE / CHANGES_REQUESTED / GO / NO-GO "
    "/ pass / fail must never appear — this is advisory, not a gate). "
    "Flag any design-system finding reported as a violation when the "
    "design system is actually silent on that pattern — it must be "
    "reported as 'no norm exists, observed N variants' instead. Summarise "
    "which findings you endorse, which you question, and why. Do not "
    "repeat findings — only evaluate."
)


TEAM = OrchestratedTeam(
    name="ui_review",
    orchestrator_prompt=_ORCH_PROMPT,
    synthesizer_prompt=_SYNTH_PROMPT,
    worker_system_template=_WORKER_PROMPT_TEMPLATE,
    to_team_suggestions=(),
    verifier_prompt=_VERIFIER_PROMPT,
    writes_report=False,
)
