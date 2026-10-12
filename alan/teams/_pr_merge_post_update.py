from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from alan.engine_config import ForgeTarget
from alan.handover import Handover
from alan.teams import _pr_branch, _pr_forge, _pr_state_paths
from alan.teams._base import chain_id_for, make_handover_id
from alan.teams._pr_handover import carried_scenarios
from alan.workspace import Workspace

if TYPE_CHECKING:
    from alan.teams.pr_merge import PrMergeOutcome

_SYSTEM_HEAD_FORWARD_DIRECTIVE = (
    "\n\nThis PR's branch was updated by the system itself (rung: {rung}) after "
    "the prior approval, and the resulting head commit {head_sha} was never seen "
    "by that approval. Re-verify against the current head — confirm the update "
    "did not introduce a genuine problem — and either continue toward merge or "
    "route the real failure for a fix. Do not treat the prior approval as "
    "covering this commit."
)


_MERGE_TIME_BEHIND_RUNG = "merge_time_behind"


def record_merge_time_system_head_if_updated(
    update_ok: bool, workspace: Workspace, pr_number: int, *, forge_target: ForgeTarget,
) -> None:
    if not update_ok:
        return
    head_sha = _pr_branch.fetch_flake_guard_head_sha(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    _pr_state_paths.save_system_head(pr_number, head_sha, _MERGE_TIME_BEHIND_RUNG, forge_target)


def is_system_authored_head(
    pr_number: int, head_sha: str, *, forge_target: ForgeTarget,
) -> bool:
    marker = _pr_state_paths.load_system_head(pr_number, forge_target)
    return marker is not None and marker.head_sha == head_sha and not marker.forwarded


def _forwardable_marker(
    pr_number: int, forge_target: ForgeTarget
) -> _pr_state_paths.SystemHeadMarker | None:
    marker = _pr_state_paths.load_system_head(pr_number, forge_target)
    if marker is None or marker.forwarded or marker.head_sha is None:
        return None
    return marker


def forward_if_system_head(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: "PrMergeOutcome",
) -> Handover | None:
    if prior is None or outcome.pr_number is None:
        return None
    forge_target = _pr_forge.forge_target_for_ws(workspace)
    marker = _forwardable_marker(outcome.pr_number, forge_target)
    if marker is None:
        return None
    head_sha = _pr_branch.fetch_flake_guard_head_sha(
        outcome.pr_number, workspace.task_id, forge_target=forge_target
    )
    if head_sha is None or not is_system_authored_head(
        outcome.pr_number, head_sha, forge_target=forge_target
    ):
        return None
    _pr_state_paths.mark_system_head_forwarded(outcome.pr_number, forge_target)
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="pr_merge",
        from_invocation_id=inv_id,
        to_team_suggestions=["pr_merge"],
        iteration=prior.iteration + 1,
        revisit_of=prior.handover_id,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=(
            outcome.reason
            + _SYSTEM_HEAD_FORWARD_DIRECTIVE.format(rung=marker.rung, head_sha=head_sha)
        ),
        pr_number=outcome.pr_number,
        dependency_bot_authorized=prior.dependency_bot_authorized,
        test_scenarios=carried_scenarios(prior),
    )


def forward_or(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: "PrMergeOutcome",
    fallback: Callable[[], Handover],
) -> Handover:
    forwarded = forward_if_system_head(workspace, prior, inv_id, outcome)
    return forwarded if forwarded is not None else fallback()
