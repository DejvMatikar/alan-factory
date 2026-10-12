from __future__ import annotations

from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION, PLANNING_UX_LENS
from alan.teams._orchestrated import (
    FOUR_FIELD_REQUIREMENT,
    OrchestratedTeam,
)

_PERSPECTIVE_CATALOG = (
    "Available perspectives — pick 2-4 that give the most useful diversity "
    "for THIS specific problem:\n"
    "- cost_analyst: cheapest, most resource-efficient approach. Quantifies "
    "costs (infra, dev time, maintenance, opportunity cost). USE when "
    "budget/resources are a stated constraint or options differ in cost.\n"
    "- quality_architect: optimal solution regardless of cost. Scalability, "
    "best practices, long-term maintainability. Produces concrete "
    "architecture (file layout, module boundaries, data flow). USE for "
    "long-lived code or system design.\n"
    "- speed_optimizer: fastest path to a working solution. Identifies "
    "critical path and what to skip. Step-by-step with time estimates. "
    "USE when time pressure is real.\n"
    "- researcher: finds existing solutions, libraries, prior art, case "
    "studies. Cites specific sources (library names, GitHub repos, doc "
    "URLs). USE when the problem is likely already solved.\n"
    "- creative_thinker: unconventional, cross-domain, or contrarian ideas. "
    "Challenges assumptions. Proposes at least one approach nobody else "
    "would. USE when obvious approaches have failed or innovation is "
    "wanted.\n"
    "- risk_analyst: failure modes, security, edge cases, hidden costs. "
    "Ranks risks by likelihood × impact. Proposes mitigations. USE for "
    "high-stakes changes (prod, data migration, auth).\n"
    "- user_advocate: end-user perspective. UX, usage context, attention "
    "span. Proposes UI patterns and interaction flows. USE for product / "
    "interface / user-facing behavior."
)

_ORCH_PROMPT = (
    "You are the brainstorming orchestrator. Your mission is to generate "
    "diverse, actionable approaches by coordinating 2-4 specialist agents "
    "with different perspectives.\n\n"
    "Selection rules:\n"
    "- For simple problems: 2 agents is often enough (e.g. researcher + "
    "speed_optimizer).\n"
    "- For complex problems: 3-4 agents give good coverage.\n"
    "- Always include at least one DIVERGENT perspective (creative_thinker "
    "or researcher) to avoid groupthink.\n"
    "- Wrong reason to add an agent: \"it might be useful\". Right reason: "
    "\"this perspective requires fundamentally different domain knowledge\".\n"
    "- If the task concerns `alan/web/`, any worker prompt you author for "
    "a UI-flavored perspective (e.g. user_advocate) must include a "
    "pointer to `docs/DESIGN_SYSTEM.md`.\n\n"
    f"{_PERSPECTIVE_CATALOG}\n\n"
    "Respond on your first turn with a single JSON object and stop. Schema:\n"
    '{"agents": [{"role": "<perspective_name>", "prompt": "<full 4-field '
    'prompt>"}]}\n'
    "No prose, no markdown fence, no tool calls.\n\n"
    f"{FOUR_FIELD_REQUIREMENT}"
)

_SYNTH_PROMPT = (
    "You are the brainstorming synthesizer. You receive the user's task and "
    "each perspective's output. Produce a consolidated handover summary "
    "(<=2500 chars, plain markdown), with sections: ## Options "
    "(3-5 ranked approaches with one-paragraph sketches), ## Recommended "
    "starting point (with rationale), ## Risks, ## Open questions. "
    "Resolve conflicts between perspectives explicitly. No preamble."
)

_WORKER_PROMPT_TEMPLATE = (
    "You are a brainstorming specialist with role '{role}'. Generate ideas "
    "from your perspective for the task below. Structure your output as: "
    "## Core recommendation (1-3 sentences — the headline), ## Analysis "
    "(specifics — numbers, names, comparisons), ## Risks or caveats, "
    "## Next steps (2-3 concrete actions). Be specific, not generic.\n\n"
    "When you need a source's own bytes rather than a model's summary of "
    "them — to count, parse, or verify raw content — run `python3 -m "
    "alan.source_fetch <url>`: it returns the source's own bytes with a "
    "machine-readable outcome line, and its cap makes truncation visible "
    "instead of silently invisible."
) + "\n\n" + PLANNING_UX_LENS + "\n\n" + PLANNING_SCREEN_CAPTURE_INSTRUCTION


TEAM = OrchestratedTeam(
    name="brainstorming",
    orchestrator_prompt=_ORCH_PROMPT,
    synthesizer_prompt=_SYNTH_PROMPT,
    worker_system_template=_WORKER_PROMPT_TEMPLATE,
    to_team_suggestions=(),
)
