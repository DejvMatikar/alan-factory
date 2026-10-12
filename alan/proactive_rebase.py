from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from alan import workspace as workspace_mod
from alan.engine_config import ForgeTarget
from alan.handover import list_handovers
from alan.queue import Queue
from alan.teams import _pr_branch, _pr_forge, _pr_state_paths
from alan.teams._audit import append_jsonl_audit
from alan.teams._pr_branch import BranchUpdateOutcome
from alan.workspace import Status, Workspace

logger = logging.getLogger("alan.proactive_rebase")

_REBASE_CAP_ENV = "ALAN_PROACTIVE_REBASE_CAP"
_DEFAULT_REBASE_CAP = 5
_AUDIT_FILE = "merge_gate_audit.jsonl"
_PROACTIVE_REBASE_RUNG = "proactive_rebase"

_ELIGIBLE_STATUSES: frozenset[Status] = frozenset({
    Status.ACTIVE,
    Status.AWAITING_INPUT,
    Status.ESCALATED,
    Status.PAUSED,
    Status.PARTIAL,
})

_ACTION_BY_OUTCOME: dict[BranchUpdateOutcome, str] = {
    BranchUpdateOutcome.UPDATED: "proactive_rebase_ok",
    BranchUpdateOutcome.CONFLICT: "proactive_rebase_conflict",
    BranchUpdateOutcome.FAILED: "proactive_rebase_failed",
}


@dataclass(frozen=True)
class _RebaseTarget:
    task_id: str
    pr_number: int
    forge_target: ForgeTarget


def _record_system_head(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> None:
    head_sha = _pr_branch.fetch_flake_guard_head_sha(
        pr_number, task_id, forge_target=forge_target
    )
    _pr_state_paths.save_system_head(pr_number, head_sha, _PROACTIVE_REBASE_RUNG, forge_target)


def _rebase_cap() -> int:
    raw = os.environ.get(_REBASE_CAP_ENV, "").strip()
    if not raw:
        return _DEFAULT_REBASE_CAP
    try:
        v = int(raw)
    except ValueError:
        return _DEFAULT_REBASE_CAP
    return v if v > 0 else _DEFAULT_REBASE_CAP


def _latest_pr_number(ws: Workspace) -> int | None:
    try:
        handovers = list_handovers(ws)
    except Exception:  # noqa: BLE001
        return None
    for h in reversed(handovers):
        if h.pr_number is not None:
            return h.pr_number
    return None


def _ws_eligible(ws: Workspace, skip_workspace_id: str, queue: Queue) -> bool:
    if ws.workspace_id == skip_workspace_id:
        return False
    if ws.meta.status not in _ELIGIBLE_STATUSES:
        return False
    return not queue.has_active_work(ws.workspace_id)


def _target_for(ws: Workspace) -> _RebaseTarget | None:
    pr_number = _latest_pr_number(ws)
    if pr_number is None:
        return None
    forge_target = _pr_forge.forge_target_for_ws(ws)
    if _pr_branch.pr_is_open(pr_number, ws.task_id, forge_target=forge_target) is False:
        return None
    return _RebaseTarget(task_id=ws.task_id, pr_number=pr_number, forge_target=forge_target)


def _sort_key(ws: Workspace) -> tuple[int, object]:
    return (0 if ws.meta.status is Status.ACTIVE else 1, ws.meta.created_at)


def _resolve_target_safe(ws: Workspace) -> _RebaseTarget | None:
    try:
        return _target_for(ws)
    except Exception:  # noqa: BLE001
        logger.warning(
            "proactive_rebase: error resolving candidate task=%s", ws.task_id, exc_info=True,
        )
        return None


def _collect_targets(
    skip_workspace_id: str,
    workspaces_root: Path | None,
    queue: Queue,
    cap: int,
) -> list[_RebaseTarget]:
    workspaces = workspace_mod.list_workspaces(root=workspaces_root)
    workspaces.sort(key=_sort_key)
    targets: list[_RebaseTarget] = []
    for ws in workspaces:
        if not _ws_eligible(ws, skip_workspace_id, queue):
            continue
        target = _resolve_target_safe(ws)
        if target is None:
            continue
        targets.append(target)
        if len(targets) >= cap:
            break
    return targets


def _do_fan_out(
    skip_workspace_id: str,
    workspaces_root: Path | None,
    queue: Queue,
    audit_dir: Path,
    cap: int,
    triggered_by_pr: int | None,
) -> None:
    try:
        targets = _collect_targets(skip_workspace_id, workspaces_root, queue, cap)
    except Exception:  # noqa: BLE001
        logger.warning("proactive_rebase: error collecting targets", exc_info=True)
        return
    for target in targets:
        try:
            outcome = _pr_branch.update_pr_branch_outcome(
                target.pr_number, target.task_id, forge_target=target.forge_target
            )
            if outcome is BranchUpdateOutcome.UPDATED:
                _record_system_head(
                    target.pr_number, target.task_id, forge_target=target.forge_target
                )
        except Exception:  # noqa: BLE001
            outcome = BranchUpdateOutcome.FAILED
            logger.warning(
                "proactive_rebase: error for pr=%s task=%s",
                target.pr_number, target.task_id, exc_info=True,
            )
        action = _ACTION_BY_OUTCOME[outcome]
        logger.info(
            "proactive_rebase: pr=%s task=%s action=%s triggered_by=%s",
            target.pr_number, target.task_id, action, triggered_by_pr,
        )
        append_jsonl_audit(
            audit_dir / _AUDIT_FILE,
            {
                "task_id": target.task_id,
                "pr_number": target.pr_number,
                "action": action,
                "repo": target.forge_target.repo.slug,
                "triggered_by_merge_of": triggered_by_pr,
            },
            log_label="proactive_rebase",
        )


def maybe_trigger_proactive_rebase(
    *,
    skip_workspace_id: str,
    workspaces_root: Path | None,
    queue: Queue,
    audit_dir: Path,
    triggered_by_pr: int | None,
) -> None:
    cap = _rebase_cap()
    thread = threading.Thread(
        target=_do_fan_out,
        args=(skip_workspace_id, workspaces_root, queue, audit_dir, cap, triggered_by_pr),
        daemon=True,
        name="alan-proactive-rebase",
    )
    thread.start()
