from __future__ import annotations

from alan.agent_catalogue_helpers import ResolverGroup, runtime_slot
from alan.agent_catalogue_models import ConditionalDirOut, FlagOut
from alan.agent_catalogue_teams_static_common import (
    DEFAULT_BASE_TAIL,
    RoleDetails,
    static_team_group,
)
from alan.teams import architecture, brief_refine, decomposer, implementation_plan, maintenance
from alan.teams._maintenance_secrets import HOST_READ_MODES_HEADING
from alan.teams.maintenance import (
    _GUARDRAIL,  # pyright: ignore[reportPrivateUsage]
    _LIVE_STATE_ACCESS,  # pyright: ignore[reportPrivateUsage]
    _REPORT_CONTRACT,  # pyright: ignore[reportPrivateUsage]
)

CI_RUN_LIST_GRANT = "Bash(gh run list:*)"

_BRIEF_REFINE_SOURCE_DIR = ConditionalDirOut(
    label="WORKSPACE_DIR",
    condition=(
        "Only when the workspace carries the source-conversation file "
        f"({brief_refine.SOURCE_FILE_NAME})"
    ),
)

_BRIEF_REFINE_REASK_FLAG = FlagOut(
    code="other_mismatch",
    severity="warn",
    title="Correction re-ask uses the bare role prompt",
    detail=(
        "If the refinement block fails to parse, the correction re-ask resumes the session "
        "with this role's prompt only, without the terseness and untrusted-content blocks the "
        "first launch appends. Informational: the prompt shown here is the first launch's."
    ),
)

_ARCHITECTURE = {
    "structure_mapper": RoleDetails(
        "First role of the architecture team: maps module boundaries and the dependency graph "
        "as they bear on the task."
    ),
    "complexity_analyst": RoleDetails(
        "Second role of the architecture team: reports complexity and length hotspots in the "
        "modules the structure mapper called out."
    ),
    "debt_auditor": RoleDetails(
        "Third role of the architecture team: reports dead code and tech-debt density."
    ),
    "change_planner": RoleDetails(
        "Fourth role of the architecture team: turns the prior findings into an ordered change "
        "list, or emits the analysis-only sentinel when no change was requested."
    ),
    "devil_advocate": RoleDetails(
        "Fifth role of the architecture team: challenges the prior outputs at design-decision "
        "level before the judge synthesises."
    ),
    "judge": RoleDetails(
        "Last role of the architecture team: validates every prior output, writes the final "
        "architecture note and emits the decisions block that implementation planning reads."
    ),
}

_BRIEF_REFINE = {
    "brief_refiner": RoleDetails(
        "First thing that runs on a brand-new workspace, before any planning: splits the brief "
        "into requirements and assumptions. The workspace directory is granted as an extra "
        "directory only when the brief was distilled from a source conversation file.",
        conditional_dirs=(_BRIEF_REFINE_SOURCE_DIR,),
        flags=(_BRIEF_REFINE_REASK_FLAG,),
        base_tail=runtime_slot(
            "BRIEF_REFINE_PRIOR_REQUIREMENTS",
            "Requirements the workspace already carries, listed by id under '## Prior "
            "requirements'; present only when there are some",
        )
        + runtime_slot(
            "BRIEF_REFINE_SOURCE_CONVERSATION",
            "Reference to the source conversation file under '## Source conversation'; present "
            "only when the brief was distilled from a conversation",
        ),
    )
}

_DECOMPOSER = {
    "decomposer": RoleDetails(
        "Runs after implementation planning: decides whether the task ships whole or is split "
        "into dependency-linked subtasks.",
        base_tail=DEFAULT_BASE_TAIL
        + runtime_slot(
            "DECOMPOSER_PLAN_CONTEXT",
            "The plan's files to edit, decisions and parent test scenarios, plus the live "
            "acceptance clauses, each under its own heading; sections without content are "
            "left out",
        ),
    )
}

_IMPLEMENTATION_PLAN = {
    "architect": RoleDetails(
        "First role of the implementation planning team: proposes the recommended technical "
        "approach."
    ),
    "backend_analyst": RoleDetails(
        "Second role of the implementation planning team: details the concrete backend changes."
    ),
    "frontend_analyst": RoleDetails(
        "Runs in parallel with the QA planner after the backend analyst: details the frontend "
        "changes, or says there are none."
    ),
    "qa_planner": RoleDetails(
        "Runs in parallel with the frontend analyst after the backend analyst: plans the unit, "
        "integration and end-to-end test scenarios."
    ),
    "devil_advocate": RoleDetails(
        "Fourth stage of the implementation planning team: probes adjacent code, invariants, "
        "ripple effects and UX consistency in the prior outputs."
    ),
    "synthesizer": RoleDetails(
        "Last role of the implementation planning team: combines every prior output into the "
        "implementation plan and the decisions block, with scenario guidance built from this "
        "repository's engine configuration."
    ),
}

_MAINTENANCE = {
    "code_health_analyst": RoleDetails(
        "First role of the maintenance team: looks for orphaned worktrees, stale workspaces and "
        "stray lock or temp files on the live deployment."
    ),
    "ci_dependency_auditor": RoleDetails(
        "Second role of the maintenance team: reads recent CI run history and the "
        "dependency-sweep cadence.",
        claimed_grants=(CI_RUN_LIST_GRANT,),
    ),
    "log_repo_scanner": RoleDetails(
        "Third role of the maintenance team: finds recurring errors in the live error log and "
        "repository hygiene problems."
    ),
    "security_scanner": RoleDetails(
        "Last role of the maintenance team: checks permission bits of runtime secret files and "
        "scans runtime-written artifacts for leaked secret-shaped strings.",
        user_prefix=runtime_slot(
            "MAINTENANCE_SECRET_PERMISSIONS",
            f"Permission bits of the runtime secret files, read by the host and listed by label "
            f"under '{HOST_READ_MODES_HEADING}'",
        ),
    ),
}

_MAINTENANCE_SHARED_BLOCKS = (
    ("maintenance_report_contract", _REPORT_CONTRACT),
    ("maintenance_guardrail", _GUARDRAIL),
    ("maintenance_live_state_access", _LIVE_STATE_ACCESS),
)

GROUPS: tuple[ResolverGroup, ...] = (
    static_team_group(architecture.TEAM, _ARCHITECTURE),
    static_team_group(brief_refine.TEAM, _BRIEF_REFINE),
    static_team_group(decomposer.TEAM, _DECOMPOSER),
    static_team_group(implementation_plan.TEAM, _IMPLEMENTATION_PLAN),
    static_team_group(maintenance.TEAM, _MAINTENANCE, _MAINTENANCE_SHARED_BLOCKS),
)

__all__ = ["CI_RUN_LIST_GRANT", "GROUPS"]
