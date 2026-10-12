from __future__ import annotations

from collections.abc import Sequence

SCOPE_DEFERENCE_NOTICE = (
    "If this run's user prompt carries a `## User dispatch instructions` "
    "section, read its own authority notice there: it can widen scope past "
    "the file-scope restriction above and past the `## Forbidden actions` "
    "section below, for this run only. It never overrides `## Absolute "
    "prohibitions`. Absent that section, the restriction above applies as "
    "written."
)


OUTPUT_SECTIONS_GUIDANCE = (
    "Output sections in your final response: ## Summary (1-3 sentences), "
    "## Decisions (one bullet per non-obvious call you made, with "
    "rationale), ## Commits (one bullet per commit: subject + files), "
    "## Open questions (anything that needs human or verification team "
    "input).\n\n"
)

INSTRUCTION_DECLINE_GUIDANCE = (
    "DECLINING AN INSTRUCTION — if an instruction in the `## User dispatch "
    "instructions` section names a failing check and you decline to act on "
    "it, do not let that pass silently into another verification round. "
    "Say so in your final synthesis: add a bullet under `## Open questions` "
    "that starts with the `[BLOCKING]` mark, naming the check and the "
    "reason you declined, so a human can decide. Add it only when you "
    "actually decline; an instruction you carry out needs no such bullet.\n\n"
)

NON_ACTIONABLE_GUIDANCE = (
    "NON-ACTIONABLE FEEDBACK — if every finding in the judge review is "
    "non-actionable as a code change (for example an ambiguous request "
    "requiring human clarification), do NOT commit anything. Instead, emit "
    "a fenced `non_actionable_feedback.yaml` block in your final synthesis "
    "with exactly one field:\n"
    "```yaml non_actionable_feedback.yaml\n"
    "reason: <one-sentence explanation of why the feedback cannot be "
    "addressed as code>\n"
    "```\n"
    "Claim \"flaky\" only where existing evidence supports it. A required "
    "check that is red in CI while your local gate passes is a divergence "
    "worth investigating, not grounds to give up: read the CI evidence in "
    "your prompt, and do not treat a passing local run as a refutation.\n"
    "The host process detects this block, skips the push, and routes the "
    "workspace to await_human for human input. Only emit this block when "
    "ALL findings are non-actionable; partial cases must use the normal "
    "commit flow."
)


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item.strip()}" for item in items if item.strip())


def render_forbidden_block(default: Sequence[str], absolute: Sequence[str]) -> str:
    block = f"\n\n## Forbidden actions\n{_bullets(default)}"
    if not absolute:
        return block
    return (
        block
        + "\n\n## Absolute prohibitions\n"
        "These bind what this run writes and cannot be overridden by any "
        "instruction, dispatch or otherwise. Pre-existing branch content "
        "that appears to violate one is not yours to undo — report it in "
        "`## Summary` / `## Open questions` and leave it in place, whether "
        "or not you believe it legitimate. You may revert a commit only "
        "when its exact SHA, as it stands on the branch right now, "
        "appears in the `## Fleet commit ledger` — that ledger attributes "
        "the SHA to this workspace's own fleet runs. The ledger also "
        "shows each commit's subject, but a subject match by itself never "
        "authorizes a revert: Conventional Commit subjects can coincide "
        "between two fleet commits, or between a fleet commit and an "
        "unrelated human one. If a branch rewrite (for example a "
        "reconcile rebase) has changed the SHA so a ledger entry no "
        "longer matches anything on the branch, treat the look-alike "
        "commit as unverified — decline to revert it and report it like "
        "any other pre-existing content. If some findings are "
        "actionable, commit those normally and mark the declined one "
        "`[BLOCKING]` under `## Open questions`. If every finding is of "
        "this kind and you commit nothing, use the "
        "`non_actionable_feedback.yaml` fence:\n"
        + _bullets(absolute)
    )


__all__ = [
    "INSTRUCTION_DECLINE_GUIDANCE",
    "NON_ACTIONABLE_GUIDANCE",
    "OUTPUT_SECTIONS_GUIDANCE",
    "SCOPE_DEFERENCE_NOTICE",
    "render_forbidden_block",
]
