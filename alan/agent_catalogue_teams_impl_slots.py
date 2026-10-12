from __future__ import annotations

from alan.agent_catalogue_helpers import runtime_slot
from alan.teams import _fix_forward  # pyright: ignore[reportPrivateUsage]
from alan.teams import implementation as _impl

_IMPLEMENTATION_PROHIBITIONS = "; ".join(
    _impl._DEFAULT_FORBIDDEN_ACTIONS  # pyright: ignore[reportPrivateUsage]
)
_FIX_PROHIBITIONS = "; ".join(_fix_forward.DEFAULT_FORBIDDEN_ACTIONS)

WORK_ITEM_TITLE = runtime_slot("WORK_ITEM_TITLE", "Title of the workspace's task")
WORK_ITEM_DESCRIPTION = runtime_slot("WORK_ITEM_DESCRIPTION", "Description of the workspace's task")
DISPATCH_INSTRUCTIONS = runtime_slot(
    "DISPATCH_INSTRUCTIONS",
    "The '## User dispatch instructions' section carrying the operator's free-form "
    "instructions for this run, with a note on how it ranks against the scope; left out when "
    "none were given",
)
SUBTASK_SECTION = runtime_slot(
    "SUBTASK_SECTION",
    "The '## Your task' section for the subtask this run was dispatched for: its title, "
    "instructions, files and Definition of Done. Left out when the plan was not split into "
    "subtasks",
)
IMPLEMENTATION_PLAN_SECTIONS = runtime_slot(
    "IMPLEMENTATION_PLAN_SECTIONS",
    "The upstream handover as sections, each left out when empty: the dependency-bot "
    "authorization, the plan to implement ('## Prior plan', or '## Previous FAILED attempt' "
    "after a failure), inherited decisions, the files to edit and the prior artifacts",
)
IMPLEMENTATION_FORBIDDEN_ACTIONS = runtime_slot(
    "IMPLEMENTATION_FORBIDDEN_ACTIONS",
    "The '## Forbidden actions' list, present whenever there is an upstream handover: "
    f"prohibitions the plan carries over, then the built-in ones ({_IMPLEMENTATION_PROHIBITIONS})",
)
FIX_REVIEW_SECTIONS = runtime_slot(
    "FIX_REVIEW_SECTIONS",
    "The judge's review of the open pull request under '## Judge review', followed by each of "
    "these when present: failing acceptance clauses, unresolved review threads, SonarCloud "
    "findings, failing CI checks, the live pull request body, refused thread resolutions, "
    "current test scenarios and the fleet commit ledger",
)
FIX_UPSTREAM_SECTIONS = runtime_slot(
    "FIX_UPSTREAM_SECTIONS",
    "The '## Files to edit' and '## Prior artifacts' sections from the upstream handover, "
    "each left out when empty",
)
FIX_FORBIDDEN_ACTIONS = runtime_slot(
    "FIX_FORBIDDEN_ACTIONS",
    "The '## Forbidden actions' list followed by the '## Absolute prohibitions' section, "
    f"present whenever there is an upstream handover; built-in prohibitions: {_FIX_PROHIBITIONS}",
)
REPOSITORY_GUIDANCE = runtime_slot(
    "REPOSITORY_GUIDANCE",
    "A pointer to the repository's own CLAUDE.md contributor guidance, included only when "
    "the checkout contains that file",
)
IMPLEMENTER_SESSION = runtime_slot(
    "IMPLEMENTER_SESSION",
    "Id of the implementer node's recorded CLI session, which the backstop resumes",
)
IMPLEMENTATION_HANDOVER = runtime_slot(
    "IMPLEMENTATION_HANDOVER",
    "The 'Prior handover from ...' section with the implementation team's synthesis for the "
    "pull request under review and, from the second iteration on, earlier cross-team "
    "context; left out when there is no upstream handover",
)
REVIEW_INPUTS = runtime_slot(
    "REVIEW_INPUTS",
    "Run-specific review inputs, each left out when absent: the in-scope acceptance "
    "outcomes, the test scenarios (or a marker that they were waived or covered by the "
    "Definition of Done) and an advisory manifest-deviation note",
)
DIFF_REVIEW_CONTEXT = runtime_slot(
    "DIFF_REVIEW_CONTEXT",
    "Host-supplied review evidence: ratchet-loosening notices, the pull request's live "
    "description and diff, UI-consistency instructions when the checkout has a web UI, and "
    "the targeted test scope",
)
SCENARIO_EVIDENCE = runtime_slot(
    "SCENARIO_EVIDENCE",
    "The host-run scenario results block and the targeted test scope for this checkout",
)
CLAUSE_AUDIT_EVIDENCE = runtime_slot(
    "CLAUSE_AUDIT_EVIDENCE",
    "Host-verified ground truth for the audit: the pull request's diff file list, draft and "
    "branch state, live description, required check names and the clauses to audit",
)
CLAUSE_AUDITOR_COVERAGE = runtime_slot(
    "CLAUSE_AUDITOR_COVERAGE",
    "A note naming the other roles in the pipeline and keeping the auditor to its own scope; "
    "on the one retry it also carries the first attempt's output and a retry nudge",
)
CLAUSE_TEST_OWNERSHIP = runtime_slot(
    "CLAUSE_TEST_OWNERSHIP",
    "The paragraph telling the auditor which test and gate runs it owns and must capture "
    "exactly once, derived from the engine's gate commands; the one retry after an "
    "unparseable answer receives a variant that forbids running them again",
)
JUDGE_CLAUSE_AUDITOR_SECTION = runtime_slot(
    "JUDGE_CLAUSE_AUDITOR_SECTION",
    "The clause_auditor's output under '### clause_auditor'; present only when the "
    "acceptance-clause audit ran and is enforcing",
)
JUDGE_RETRY_NUDGE = runtime_slot(
    "JUDGE_RETRY_NUDGE",
    "A retry nudge asking for the complete review again; present only on the single retry "
    "after an incomplete judge output",
)

__all__ = [
    "CLAUSE_AUDITOR_COVERAGE",
    "CLAUSE_AUDIT_EVIDENCE",
    "CLAUSE_TEST_OWNERSHIP",
    "DIFF_REVIEW_CONTEXT",
    "DISPATCH_INSTRUCTIONS",
    "FIX_FORBIDDEN_ACTIONS",
    "FIX_REVIEW_SECTIONS",
    "FIX_UPSTREAM_SECTIONS",
    "IMPLEMENTATION_FORBIDDEN_ACTIONS",
    "IMPLEMENTATION_HANDOVER",
    "IMPLEMENTATION_PLAN_SECTIONS",
    "IMPLEMENTER_SESSION",
    "JUDGE_CLAUSE_AUDITOR_SECTION",
    "JUDGE_RETRY_NUDGE",
    "REPOSITORY_GUIDANCE",
    "REVIEW_INPUTS",
    "SCENARIO_EVIDENCE",
    "SUBTASK_SECTION",
    "WORK_ITEM_DESCRIPTION",
    "WORK_ITEM_TITLE",
]
