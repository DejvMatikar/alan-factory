from __future__ import annotations

from alan.teams import (
    architecture,
    brainstorming,
    brief_refine,
    decomposer,
    dependency_sweep,
    deploy_verify,
    implementation,
    implementation_fix,
    implementation_plan,
    maintenance,
    opportunity_scout,
    pr_merge,
    research,
    ui_review,
    verification,
)
from alan.teams._base import Team, TeamEvent

TEAMS: dict[str, Team] = {
    architecture.TEAM.name: architecture.TEAM,
    brainstorming.TEAM.name: brainstorming.TEAM,
    brief_refine.TEAM.name: brief_refine.TEAM,
    decomposer.TEAM.name: decomposer.TEAM,
    deploy_verify.TEAM.name: deploy_verify.TEAM,
    dependency_sweep.TEAM.name: dependency_sweep.TEAM,
    implementation.TEAM.name: implementation.TEAM,
    implementation_fix.TEAM.name: implementation_fix.TEAM,
    implementation_plan.TEAM.name: implementation_plan.TEAM,
    research.TEAM.name: research.TEAM,
    maintenance.TEAM.name: maintenance.TEAM,
    opportunity_scout.TEAM.name: opportunity_scout.TEAM,
    pr_merge.TEAM.name: pr_merge.TEAM,
    ui_review.TEAM.name: ui_review.TEAM,
    verification.TEAM.name: verification.TEAM,
}

INTERNAL_ONLY_TEAMS: frozenset[str] = frozenset({
    brief_refine.TEAM.name,
    decomposer.TEAM.name,
    dependency_sweep.TEAM.name,
})

MANUALLY_DISPATCHABLE_TEAMS: frozenset[str] = frozenset(TEAMS) - INTERNAL_ONLY_TEAMS

INITIAL_DISPATCHABLE_TEAMS: frozenset[str] = MANUALLY_DISPATCHABLE_TEAMS - {
    deploy_verify.TEAM.name,
    implementation_fix.TEAM.name,
    pr_merge.TEAM.name,
}

# Teams alan.auto_continue may dispatch to without human approval, when a
# project's autonomy dial is set to "auto_continue". Deliberately a static
# allowlist (not a posture/heuristic) so "auto-continue only ever executes,
# never decides" stays a testable property — see
# tests/test_auto_continue.py::test_auto_continue_teams_is_subset_of_manually_dispatchable.
AUTO_CONTINUE_TEAMS: frozenset[str] = frozenset({
    deploy_verify.TEAM.name,
    implementation.TEAM.name,
    implementation_fix.TEAM.name,
    pr_merge.TEAM.name,
    verification.TEAM.name,
})

TEAM_ROLE_SUMMARIES: dict[str, str] = {
    architecture.TEAM.name: (
        "Design the shape of a change: ADR-style decisions, trade-off analysis, "
        "change_planner + judge."
    ),
    brainstorming.TEAM.name: (
        "Diverge on options the user has not yet narrowed down."
    ),
    brief_refine.TEAM.name: (
        "Internal first hop on every newly created workspace: splits the "
        "brief into traceable requirements vs. inferred assumptions before "
        "planning starts. Auto-dispatched by the host; not user-dispatchable."
    ),
    decomposer.TEAM.name: (
        "Internal step after implementation_plan: decides split-vs-keep and, on "
        "split, injects a dependency-linked sub-DAG via the injection primitive. "
        "Not user-dispatchable."
    ),
    deploy_verify.TEAM.name: (
        "Post-merge gate: polls GitHub Actions ci-deploy runs and independently "
        "probes the production health endpoint; confirms deployment or escalates."
    ),
    dependency_sweep.TEAM.name: (
        "Autonomous weekly sweep: discovers open Dependabot PRs, dispatches them "
        "to pr_merge or implementation_fix, and runs pip-audit. Self-rescheduling; "
        "not user-dispatchable."
    ),
    implementation.TEAM.name: (
        "Write the code for an existing implementation plan; pushes a branch and "
        "opens a PR."
    ),
    implementation_fix.TEAM.name: (
        "Address Copilot / verification REQUEST_CHANGES on an existing PR. "
        "Requires a prior handover that carries a pr_number."
    ),
    implementation_plan.TEAM.name: (
        "Turn a vague task into a concrete implementation plan before any code "
        "is written."
    ),
    research.TEAM.name: (
        "Research: orchestrator + workers + synthesizer gathers information from "
        "the codebase / web before deciding."
    ),
    maintenance.TEAM.name: (
        "Live-state health check: four roles report on environment residue, "
        "CI/dependency-sweep drift, log patterns, and runtime secret "
        "hygiene that no PR gate observes. Rolls every role's findings and "
        "any unverified (environment-blocked) checks into one leading "
        "verdict; a medium-or-worse run parks awaiting human triage "
        "instead of terminating silently."
    ),
    opportunity_scout.TEAM.name: (
        "Convergent GO/NO-GO screen of ONE candidate opportunity via four "
        "filters. Not for discovering candidates — use brainstorming/research "
        "for that."
    ),
    pr_merge.TEAM.name: (
        "Squash-merge the workspace's PR once gates and Copilot/human "
        "conversations are green. Requires a prior handover carrying a pr_number."
    ),
    "triage": (
        "Read-only recurring sweep that surfaces failure signals (stuck jobs, CI failures, "
        "stuck PRs). Auto-rescheduled; not user-dispatchable."
    ),
    ui_review.TEAM.name: (
        "Review the existing web UI against the design system and UX: ranked "
        "findings inventory + ready-to-dispatch briefs. Strictly advisory and "
        "read-only. Not for building UI — use implementation for that."
    ),
    verification.TEAM.name: (
        "Judge an existing PR via reasoning + scenario walkthrough; posts an "
        "approving or REQUEST_CHANGES review."
    ),
}

__all__ = [
    "AUTO_CONTINUE_TEAMS",
    "INITIAL_DISPATCHABLE_TEAMS",
    "INTERNAL_ONLY_TEAMS",
    "MANUALLY_DISPATCHABLE_TEAMS",
    "TEAMS",
    "TEAM_ROLE_SUMMARIES",
    "Team",
    "TeamEvent",
]
