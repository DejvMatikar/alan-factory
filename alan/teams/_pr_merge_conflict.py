from __future__ import annotations

import subprocess

from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge
from alan.teams import _forge_errors
from alan.teams._pr_forge import GH_TIMEOUT_S

MERGEABLE_MERGE_STATES: frozenset[str] = frozenset(
    {"CLEAN", "HAS_HOOKS", "UNSTABLE", "BEHIND"}
)

CONFLICT_RECOVERY_RECIPE = (
    "This PR has a merge conflict with its base branch (mergeStateStatus=DIRTY) — "
    "checks cannot run on an unmergeable branch, this is not a stuck workflow or a "
    "missing required check. Rebase or merge the base branch in, resolve the "
    "conflicts, force-push, then re-dispatch pr_merge."
)
CONFLICT_OPTIONS: tuple[str, ...] = (
    CONFLICT_RECOVERY_RECIPE,
    "If the conflict was already resolved elsewhere (a separate rebase or merge "
    "landed on this branch), re-dispatch pr_merge to re-check mergeability.",
)


def _normalize_field(value: object) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _parse_state_and_merge_status(fields: JsonObject) -> tuple[str, str]:
    return _normalize_field(fields.get("state")), _normalize_field(fields.get("mergeStateStatus"))


def gh_pr_state_and_merge_status(
    pr_number: int, *, forge_target: ForgeTarget,
) -> tuple[str, str]:
    try:
        read = active_forge().read_pr(
            pr_number, ("state", "mergeStateStatus"),
            forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise _forge_errors.ForgeTransientError(
            f"gh pr view state/mergeStateStatus raised {exc}"
        ) from exc
    if not read.ok:
        _forge_errors.raise_for_infra(
            f"gh pr view state/mergeStateStatus PR #{pr_number}",
            returncode=read.returncode, stderr=read.stderr,
        )
        return "", ""
    return _parse_state_and_merge_status(read.fields)
