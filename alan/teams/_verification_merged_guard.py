from __future__ import annotations

import subprocess
from collections.abc import Callable

from alan.engine_config import ForgeTarget
from alan.forge import active_forge
from alan.handover import Handover
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import EmitFn, build_terminal_handover, workflow_lifecycle
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.workspace import Workspace

_AUDIT_FILE = "verification_audit.jsonl"
_MERGED_STATE = "MERGED"


def _append_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(logs_root() / _AUDIT_FILE, entry, log_label="verification")


def pr_state_name(pr_number: int, *, forge_target: ForgeTarget) -> str | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("state", "mergeStateStatus"),
            forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not read.ok:
        return None
    state = read.fields.get("state")
    return state if isinstance(state, str) and state else None


def terminal_if_merged(
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
    pr_number: int | None,
    *,
    team_name: str,
    forge_target: ForgeTarget,
) -> Handover | None:
    if pr_number is None:
        return None
    if pr_state_name(pr_number, forge_target=forge_target) != _MERGED_STATE:
        return None
    with workflow_lifecycle(workspace, team_name=team_name, inv_id=inv_id, emit=emit):
        _append_audit({
            "task_id": workspace.task_id,
            "inv_id": inv_id,
            "action": "verification_merged_short_circuit",
            "pr_number": pr_number,
        })
    return build_terminal_handover(
        workspace,
        from_team=team_name,
        summary=f"PR #{pr_number} already merged — verification has nothing left to do.",
        prior=prior,
        invocation_id=inv_id,
        pr_number=pr_number,
    )


def resolve_pr_gate(
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
    pr_number: int | None,
    branch: str | None,
    *,
    team_name: str,
    forge_target: ForgeTarget,
    precondition_escalation: Callable[[int | None], Handover],
) -> tuple[int, str] | Handover:
    merged_handover = terminal_if_merged(
        workspace, prior, emit, inv_id, pr_number,
        team_name=team_name, forge_target=forge_target,
    )
    if merged_handover is not None:
        return merged_handover
    if pr_number is None or branch is None:
        return precondition_escalation(pr_number)
    return pr_number, branch


__all__ = ["pr_state_name", "resolve_pr_gate", "terminal_if_merged"]
