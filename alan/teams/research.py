from __future__ import annotations

from dataclasses import dataclass

from alan.teams._orchestrated import (
    CONFIDENCE_REPORTING_BLOCK,
    FOUR_FIELD_REQUIREMENT,
    ORCHESTRATOR_ROLE,
    SYNTHESIZER_ROLE,
    DispatchedAgent,
    InvocationError,
    OrchestratedTeam,
    parse_dispatch,
)

_RESEARCH_PERSPECTIVE_CATALOG = (
    "Available research perspectives — pick the smallest subset that gives "
    "useful coverage:\n"
    "- deep_diver: thorough investigation of one specific technology — how it "
    "works, architecture, API surface, limitations, version compatibility.\n"
    "- comparator: head-to-head comparison of 2-4 named alternatives across "
    "performance, cost, complexity, ecosystem, maintenance, learning curve.\n"
    "- prior_art_finder: existing implementations, case studies, blog posts, "
    "papers — for each: source, what was built, stack, outcome, relevance. "
    "Prefer last 2 years and production-grade.\n"
    "- feasibility_analyst: practical feasibility in OUR context — "
    "integration effort, learning curve, migration path, hidden costs, "
    "realistic timeline.\n"
    "- trend_analyst: adoption trend (with evidence), backers, release "
    "cadence, competing alternatives, 2-3 year outlook."
)

_COVERAGE_UNION_RULE = (
    "Coverage-union rule — before emitting the dispatch JSON, enumerate every "
    "explicitly-mandated item in the task brief and confirm at least one "
    "agent's SCOPE names it verbatim; a mandated item covered by no agent is "
    "a dispatch error. Paste the brief's mandated items into each agent's "
    "CONTEXT field — workers never see the workspace description."
)

_ORCH_PROMPT = (
    "You are the research orchestrator. Read the user's task and decompose it "
    "into 2-4 specialist research agents that together answer the question.\n\n"
    "Decomposition rule: dispatch a new agent only when its sub-question "
    "requires a fundamentally different knowledge base or skill than the "
    "others. For focused questions ('should we use X?'): comparator + "
    "feasibility_analyst. For broad exploration ('how should we approach "
    "X?'): deep_diver + prior_art_finder + feasibility_analyst. Technology "
    "bets ('is X the right long-term choice?'): comparator + trend_analyst + "
    "feasibility_analyst.\n\n"
    f"{_RESEARCH_PERSPECTIVE_CATALOG}\n\n"
    "Respond on your first turn with a single JSON object and stop. Schema:\n"
    '{"agents": [{"role": "<snake_case>", "prompt": "<full prompt with the '
    'four required fields>"}]}\n'
    "No prose, no markdown fence, no tool calls.\n\n"
    f"{FOUR_FIELD_REQUIREMENT}\n\n"
    f"{_COVERAGE_UNION_RULE}"
)

_SYNTH_PROMPT = (
    "You are the research synthesizer. You receive the user's task and the "
    "outputs of all specialist agents and the verifier. Produce a full-length "
    "consolidated report in plain markdown, sized to the material — never "
    "compressed to fit an artificial cap. For a ranked-list brief (e.g. "
    "candidates, options, alternatives), include one section per item "
    "covering every mandated field.\n\n"
    "EXECUTIVE SUMMARY — the first ~2,400 characters must stand alone as a "
    "complete executive summary: lead with ## Top Recommendation, ## Key "
    "Agreements, ## Disagreements / Tensions, ## Risks to Watch, and "
    "## Concrete Next Steps, in that order, before any per-candidate detail. "
    "Downstream consumers read only a truncated prefix of this report, so "
    "nothing essential may depend on content past that point.\n\n"
    "Cite which agent said what when it matters. Resolve contradictions "
    "explicitly. If the verifier flagged gaps, list them under "
    "## Low-Confidence Findings. No preamble."
)

_WORKER_PROMPT_TEMPLATE = (
    "You are a specialist research agent with role '{role}'. Investigate the "
    "user prompt below and return findings as concise plain markdown — "
    "signal density over brevity. Cite sources with [source: docs] / "
    "[source: inferred] / [source: gh-issue] markers; rate each finding as "
    "[verified] (official docs / tested), [likely] (multiple sources agree), "
    "or [uncertain] (single source / inference). Be specific: library names "
    "with versions, exact numbers, file paths — not 'it's fast'.\n\n"
    "When you need a source's own bytes rather than a model's summary of "
    "them — to count, parse, or verify raw content — run `python3 -m "
    "alan.source_fetch <url>`: it returns the source's own bytes with a "
    "machine-readable outcome line, and its cap makes truncation visible "
    "instead of silently invisible."
)

_VERIFIER_PROMPT = (
    "You are the research verifier. You receive the user's task and the full "
    "outputs of all specialist research agents. Your ONLY job is to evaluate "
    "those outputs — do NOT repeat findings. Check for: drift (agents "
    "wandering off their assigned scope), gaps (important angles not covered), "
    "contradictions (agents disagreeing without explanation). Report each "
    "issue with the role that caused it.\n\n"
    "End your response with this exact machine-parseable tail, each field on "
    "its own line:\n"
    "GAPS: one `- <gap>` line per gap not covered, or a single line reading "
    "`GAPS: none` if there are none\n"
    "CONFIDENCE: 0.0-1.0 (how well the agent set covers the task)\n"
    "COMPLETENESS: 0.0-1.0 (what fraction of the task is answered by "
    "combining all outputs)"
)


@dataclass(frozen=True)
class ResearchTeam(OrchestratedTeam):
    name: str = "research"
    orchestrator_prompt: str = _ORCH_PROMPT
    synthesizer_prompt: str = _SYNTH_PROMPT
    worker_system_template: str = _WORKER_PROMPT_TEMPLATE
    to_team_suggestions: tuple[str, ...] = ()
    verifier_prompt: str | None = _VERIFIER_PROMPT


_parse_dispatch = parse_dispatch

TEAM = ResearchTeam()


__all__ = [
    "CONFIDENCE_REPORTING_BLOCK",
    "FOUR_FIELD_REQUIREMENT",
    "ORCHESTRATOR_ROLE",
    "SYNTHESIZER_ROLE",
    "TEAM",
    "DispatchedAgent",
    "InvocationError",
    "ResearchTeam",
]
