from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Generator
from pathlib import Path

from alan import slot_pool, worktree
from alan import workflow as _workflow_mod
from alan.claude_cli import ServerErrorRetryable, UsageLimitError
from alan.engine_config import ForgeTarget
from alan.handover import Handover
from alan.teams import _pr_post
from alan.teams._base import EmitFn, TeamEvent, build_forward_handover
from alan.teams._recovery_fault import is_recovery_fault
from alan.teams._slot_affinity import leased_with_affinity
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # pyright: ignore[reportPrivateUsage]
    append_audit,
    dry_run_enabled,
)
from alan.workflow import WorkflowError as _WorkflowError
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_CANNOT_VERIFY_FILE_ENV = "ALAN_E2E_VERIFICATION_CANNOT_VERIFY"
_CANNOT_VERIFY_BODY = (
    "Verification cannot access the PR code for this run — "
    "grounded checkout failed (tooling issue, not a code defect). "
    "This run did not review your changes. Needs human attention."
)


def _cannot_verify_trigger_active(task_id: str) -> bool:
    trigger_path = os.environ.get(_CANNOT_VERIFY_FILE_ENV, "").strip()
    if not trigger_path:
        return False
    try:
        scope = Path(trigger_path).read_text().strip()
    except OSError:
        return False
    return scope in ("", task_id)


def _grounded_slot_graph(
    workspace: Workspace, inv_id: str
) -> _workflow_mod.WorkflowGraph | None:
    if slot_pool.get_active_pool() is None:
        return None
    try:
        return _workflow_mod.load_graph(workspace, inv_id)
    except (_WorkflowError, OSError):
        logger.warning(
            "verification: workflow graph unreadable for slot affinity "
            "(task_id=%s inv=%s); leasing without preferred slot",
            workspace.task_id, inv_id,
        )
        return None


@contextlib.contextmanager
def _try_acquire_grounded_checkout(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace,
    pr_number: int,
    branch: str,
    task_id: str,
    inv_id: str,
    emit: EmitFn,
) -> Generator[Path | None, None, None]:
    if _cannot_verify_trigger_active(task_id):
        logger.warning(
            "verification: E2E cannot-verify trigger active; skipping grounded checkout"
        )
        yield None
        return
    graph = _grounded_slot_graph(workspace, inv_id)
    with contextlib.ExitStack() as stack:
        try:
            handle = stack.enter_context(
                leased_with_affinity(
                    workspace, graph, task_id, inv_id, emit,
                    existing_branch=branch,
                    worktree_factory=worktree.implementation_worktree,
                )
            )
        except (UsageLimitError, ServerErrorRetryable):
            raise
        except Exception as exc:  # noqa: BLE001
            if is_recovery_fault(exc):
                raise
            logger.exception("verification: grounded checkout failed")
            append_audit({
                "task_id": task_id, "action": "verifier_checkout_failed",
                "pr_number": pr_number, "error": str(exc),
            })
            yield None
            return
        append_audit({
            "task_id": task_id, "action": "verifier_checkout_ok",
            "pr_number": pr_number, "path": str(handle.path),
            "slot_index": handle.slot_index,
        })
        yield handle.path


def _post_cannot_verify_comment(
    pr_number: int, task_id: str, inv_id: str, *, forge_target: ForgeTarget
) -> None:
    if dry_run_enabled():
        append_audit({
            "task_id": task_id, "action": "dry_run", "pr_number": pr_number,
            "verdict": "COMMENT", "event": "COMMENT", "inv_id": inv_id,
        })
        return
    outcome = _pr_post.post_pr_review_with_inline_comments(
        pr_number, _CANNOT_VERIFY_BODY, "COMMENT", (),
        as_verifier=_pr_post.verifier_identity_available(forge_target),
        forge_target=forge_target,
    )
    if not outcome.ok:
        logger.warning("cannot_verify review post failed rc=%s", outcome.returncode)


def _emit_cannot_verify(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
    *, forge_target: ForgeTarget,
) -> Handover:
    pr_number = prior.pr_number if prior is not None else None
    task_id = workspace.task_id
    append_audit({
        "task_id": task_id, "action": "cannot_verify",
        "pr_number": pr_number, "inv_id": inv_id,
    })
    emit(TeamEvent("cannot_verify", {"pr_number": str(pr_number or ""), "task_id": task_id}))
    if pr_number is not None:
        _post_cannot_verify_comment(pr_number, task_id, inv_id, forge_target=forge_target)
    handover = build_forward_handover(
        workspace, from_team="verification", to_team_suggestions=[],
        summary=_CANNOT_VERIFY_BODY, prior=prior, invocation_id=inv_id,
    )
    review_artifact = _write_review_artifact(workspace, inv_id, _CANNOT_VERIFY_BODY)
    return handover.model_copy(update={
        "pr_number": pr_number, "decisions": [],
        "artifacts_produced": [review_artifact],
    })
