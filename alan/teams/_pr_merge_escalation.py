from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alan.teams import _pr_merge_conflict, _pr_merge_no_signal
from alan.teams._pr_merge_checks import ChecksKind, failed_check_names

UNRESOLVED_CEILING = 100

COPILOT_RESOLVE_SCRIPT = "scripts/alan_copilot_resolve.py"

CHECKS_CLASS_LABEL: dict[ChecksKind, str] = {
    "failed": "failed",
    "unresolved": "unresolved",
    "unresolved_ceiling": "unresolved-ceiling",
    "copilot_unresolved": "copilot-unresolved",
    "timeout": "timeout",
    "error": "error",
    "codeowners_blocked": "codeowners-blocked",
    "codeowners_unsupported": "codeowners-unsupported",
}

OWNERSHIP_KINDS: frozenset[str] = frozenset({"codeowners_blocked", "codeowners_unsupported"})

CHECKS_RECOVERY_RECIPE: dict[ChecksKind, str] = {
    "failed": (
        "Inspect the failed check's logs, fix the underlying failure, and push "
        "a new commit to re-trigger checks."
    ),
    "unresolved": (
        "Resolve or reply to each outstanding review thread and human comment "
        "on the PR, then re-dispatch pr_merge."
    ),
    "unresolved_ceiling": (
        "Page through the PR's review threads and comments on GitHub to "
        "confirm none block the merge, resolve them, then re-dispatch pr_merge."
    ),
    "error": (
        "Investigate the gh CLI failure (auth, rate limit, or network), then re-dispatch pr_merge."
    ),
    "codeowners_blocked": (
        "Have a file owner of the touched paths review the change on GitHub, "
        "then merge it by hand once approved."
    ),
    "codeowners_unsupported": (
        "A human must resolve this: .github/CODEOWNERS on the PR's base branch "
        "uses syntax the merge worker does not support. Fix the CODEOWNERS "
        "file, or merge the change by hand after manual review."
    ),
}


def copilot_recovery_recipe(repo_slug: str | None = None) -> str:
    script = COPILOT_RESOLVE_SCRIPT
    if repo_slug is not None:
        script += f" --repo {repo_slug}"
    return (
        "Address each Copilot review thread and comment via a "
        "copilot_resolution.yaml block (see the implementation_fix "
        f"guidance), or resolve it by hand with `{script}`, then "
        "re-dispatch pr_merge."
    )


def _has_unresolved_human(ctx: dict[str, Any]) -> bool:
    return int(ctx.get("threads", 0)) > 0 or int(ctx.get("comments", 0)) > 0


def _has_unresolved_copilot(ctx: dict[str, Any]) -> bool:
    copilot_threads = int(ctx.get("copilot_threads_count", 0))
    copilot_comments = int(ctx.get("copilot_comments_count", 0))
    return copilot_threads > 0 or copilot_comments > 0


def concurrent_blockers(kind: ChecksKind, ctx: dict[str, Any]) -> tuple[str, ...]:
    blockers: list[str] = []
    if failed_check_names(ctx.get("checks", [])):
        blockers.append(CHECKS_CLASS_LABEL["failed"])
    if int(ctx.get("total_threads", 0)) + int(ctx.get("total_comments", 0)) > UNRESOLVED_CEILING:
        blockers.append(CHECKS_CLASS_LABEL["unresolved_ceiling"])
    if _has_unresolved_human(ctx):
        blockers.append(CHECKS_CLASS_LABEL["unresolved"])
    if _has_unresolved_copilot(ctx):
        blockers.append(CHECKS_CLASS_LABEL["copilot_unresolved"])
    label = CHECKS_CLASS_LABEL.get(kind, kind)
    if label not in blockers:
        blockers.insert(0, label)
    return tuple(blockers)


def unresolved_human_reason(pr_number: int, ctx: dict[str, Any]) -> str:
    threads = int(ctx.get("threads", 0))
    comments = int(ctx.get("comments", 0))
    return (
        f"PR #{pr_number} has {threads} unresolved review thread(s) "
        f"and {comments} unresolved human comment(s)."
    )


def _failed_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    names = failed_check_names(ctx.get("checks", []))
    return f"PR #{pr_number} failed checks: {', '.join(names) or '?'}."


def _unresolved_ceiling_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    return (
        f"PR #{pr_number} has {int(ctx.get('total_threads', 0))} review "
        f"threads + {int(ctx.get('total_comments', 0))} comments "
        f"(> {UNRESOLVED_CEILING} ceiling); refusing to merge "
        "without paginated verification."
    )


def _copilot_unresolved_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    threads = int(ctx.get("copilot_threads_count", 0))
    comments = int(ctx.get("copilot_comments_count", 0))
    return (
        f"PR #{pr_number} has {threads} unresolved Copilot review thread(s) "
        f"and {comments} unresolved Copilot comment(s)."
    )


def _timeout_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    return f"PR #{pr_number} not mergeable after {ctx.get('elapsed')}s ({ctx.get('polls')} polls)."


def _codeowners_blocked_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    del ctx
    return (
        f"PR #{pr_number} was not self-merged: file ownership "
        "(.github/CODEOWNERS on the PR's base branch) requires owner review "
        "for a path it touches, or ownership could not be verified."
    )


def _codeowners_unsupported_detail(pr_number: int, ctx: dict[str, Any]) -> str:
    del ctx
    return (
        f"PR #{pr_number} was not self-merged: file ownership could not be "
        "evaluated because .github/CODEOWNERS on the PR's base branch uses "
        "syntax the merge worker does not support."
    )


_DETAIL_BUILDERS: dict[str, Callable[[int, dict[str, Any]], str]] = {
    "failed": _failed_detail,
    "unresolved": unresolved_human_reason,
    "unresolved_ceiling": _unresolved_ceiling_detail,
    "copilot_unresolved": _copilot_unresolved_detail,
    "timeout": _timeout_detail,
    "codeowners_blocked": _codeowners_blocked_detail,
    "codeowners_unsupported": _codeowners_unsupported_detail,
}


def checks_detail(kind: ChecksKind, pr_number: int, ctx: dict[str, Any]) -> str:
    builder = _DETAIL_BUILDERS.get(kind)
    if builder is not None:
        return builder(pr_number, ctx)
    return f"PR #{pr_number} merge gate hit an error: {ctx.get('error', '?')}."


def _repo_slug(ctx: dict[str, Any]) -> str | None:
    repo = ctx.get("repo")
    return repo if isinstance(repo, str) else None


def checks_reason(kind: ChecksKind, pr_number: int, ctx: dict[str, Any]) -> str:
    label = CHECKS_CLASS_LABEL.get(kind, kind)
    if kind == "copilot_unresolved":
        recipe = copilot_recovery_recipe(_repo_slug(ctx))
    else:
        recipe = CHECKS_RECOVERY_RECIPE.get(
            kind, "Investigate the failure, then re-dispatch pr_merge."
        )
    recipe = _pr_merge_no_signal.timeout_recipe(ctx, recipe)
    blockers = concurrent_blockers(kind, ctx)
    prefix = f"blockers: {', '.join(blockers)}. " if len(blockers) > 1 else ""
    return f"{label}: {prefix}{checks_detail(kind, pr_number, ctx)} {recipe}"


NO_PR_OPTIONS: tuple[str, ...] = (
    "Attach a PR number to this workspace and re-dispatch pr_merge.",
    "Merge the change manually on GitHub, then close out the task by hand.",
)
FAILED_OPTIONS: tuple[str, ...] = (
    "Inspect the failed check's logs, fix the underlying failure, and push a new "
    "commit to re-trigger checks.",
    "Re-dispatch pr_merge if the failure looks transient (flaky check).",
)
FAILED_OPTION_RESOLVERS: dict[str, str] = {FAILED_OPTIONS[0]: "implementation_fix"}
TIMEOUT_OPTIONS: tuple[str, ...] = (
    "Investigate why checks are not settling (stuck workflow run or a missing required check), "
    "then re-dispatch pr_merge once checks report a definitive state.",
    "Cancel and re-run the stuck workflow on GitHub, then re-dispatch pr_merge.",
)
SETTLED_REVIEW_OPTIONS: tuple[str, ...] = (
    "Address the requested changes and re-request review, then re-dispatch pr_merge.",
    "Dismiss the stale change-request review on GitHub if it no longer applies, "
    "then re-dispatch pr_merge.",
)
UNRESOLVED_OPTIONS: tuple[str, ...] = (
    "Resolve or reply to each outstanding review thread and human comment on "
    "the PR, then re-dispatch pr_merge.",
    "Dismiss the stale review threads on GitHub if they no longer apply, then "
    "re-dispatch pr_merge.",
)
CODEOWNERS_OPTIONS: tuple[str, ...] = (
    "Have an owner of the touched paths review the change, then merge it by "
    "hand on GitHub and close out the task.",
    "The owner declines the change: close the PR on GitHub without merging.",
)


def _ack_ledger_note(repo_slug: str | None) -> str:
    if repo_slug is None:
        return ""
    return f" and its ack-budget ledger (~/.alan/state/{repo_slug}/pr_<n>_ack_budget.json)"


def copilot_cap_recovery_recipe(repo_slug: str | None = None) -> str:
    script = COPILOT_RESOLVE_SCRIPT
    if repo_slug is not None:
        script += f" --repo {repo_slug}"
    return (
        f"Manually resolve the remaining Copilot review thread(s)/comment(s) on the PR "
        f"(`{script}`), delete .alan/copilot_escalation_count in the "
        f"workspace{_ack_ledger_note(repo_slug)}, then re-dispatch pr_merge."
    )


def copilot_ack_option(repo_slug: str | None = None) -> str:
    script = COPILOT_RESOLVE_SCRIPT
    if repo_slug is not None:
        script += f" --repo {repo_slug}"
    return (
        f"Acknowledge each remaining Copilot thread/comment via `{script}` "
        "(marks it reviewed-but-not-actioned), delete "
        f".alan/copilot_escalation_count{_ack_ledger_note(repo_slug)}, "
        "then re-dispatch pr_merge."
    )


def copilot_options(repo_slug: str | None = None) -> tuple[str, ...]:
    return (copilot_cap_recovery_recipe(repo_slug), copilot_ack_option(repo_slug))


COPILOT_CAP_RECOVERY_RECIPE = copilot_cap_recovery_recipe()
COPILOT_OPTIONS: tuple[str, ...] = copilot_options()


def copilot_cap_reason(
    pr_number: int | None, count: int, max_cap: int, *, repo_slug: str | None = None,
) -> str:
    label = CHECKS_CLASS_LABEL["copilot_unresolved"]
    recipe = copilot_cap_recovery_recipe(repo_slug)
    return (
        f"{label}: Copilot escalation cap reached ({count}/{max_cap}) on "
        f"PR #{pr_number}. {recipe}"
    )


def _timeout_options(timeout_blocker: str) -> tuple[str, ...]:
    return SETTLED_REVIEW_OPTIONS if timeout_blocker else TIMEOUT_OPTIONS


_STATIC_MERGE_BLOCK_OPTIONS: dict[str, tuple[tuple[str, ...], dict[str, str]]] = {
    "no_pr": (NO_PR_OPTIONS, {}),
    "failed": (FAILED_OPTIONS, dict(FAILED_OPTION_RESOLVERS)),
    "unresolved": (UNRESOLVED_OPTIONS, {}),
    "conflict": (_pr_merge_conflict.CONFLICT_OPTIONS, {}),
    "codeowners_blocked": (CODEOWNERS_OPTIONS, {}),
    "codeowners_unsupported": (CODEOWNERS_OPTIONS, {}),
}


def merge_block_options(
    kind: str, *, repo_slug: str | None = None, timeout_blocker: str = "",
) -> tuple[list[str], str, dict[str, str]]:
    if kind == "timeout":
        timeout_options = _timeout_options(timeout_blocker)
        return list(timeout_options), timeout_options[0], {}
    if kind == "copilot_unresolved":
        options = list(copilot_options(repo_slug))
        return options, options[0], {}
    static = _STATIC_MERGE_BLOCK_OPTIONS.get(kind)
    if static is None:
        raise ValueError(f"merge_block_options: unexpected kind {kind!r}")
    static_options, resolvers = static
    return list(static_options), static_options[0], dict(resolvers)
