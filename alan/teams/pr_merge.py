from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import alan.teams._pr_feedback as _pr_feedback
import alan.teams._pr_flake_ledger as _pr_flake_ledger
import alan.teams._pr_ready as _pr_ready
from alan import workflow as workflow_mod
from alan.claude_cli import OnProcStarted
from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, MergeAttempt, active_forge
from alan.handover import ArtifactRef, Decision, Handover, OpenQuestion
from alan.teams import (
    _fence,
    _forge_errors,
    _pr_audit_log,
    _pr_branch,
    _pr_forge,
    _pr_merge_conflict,
    _pr_merge_escalation,
    _pr_merge_no_signal,
    _pr_merge_post_update,
    _pr_state_paths,
)
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import (
    UNTRUSTED_CONTENT_NOTICE,
    EmitFn,
    build_terminal_handover,
    chain_id_for,
    emit_node,
    make_handover_id,
    reject_resume_inv_id,
    workflow_lifecycle,
)
from alan.teams._env import float_env, positive_int_env
from alan.teams._pr_handover import HandoverContext, carried_scenarios, escalation_skeleton
from alan.teams._pr_merge_checks import (
    PENDING_FALLBACK,
    ChecksKind,
    audit_advisory_tolerated,
    recheck_confirms_target,
    resolve_checks_kind,
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_TEAM_NAME = "pr_merge"
_NODE_ROLE = "pr_merge"
_MODEL = "none"
_AUDIT_FILE = "pr_merge_audit.jsonl"
_MERGE_GATE_AUDIT_FILE = "merge_gate_audit.jsonl"
_GH_TIMEOUT_S = 60.0
_DRY_RUN_ENV = "ALAN_PR_MERGE_DRY_RUN"
_AUDIT_DISABLE_ENV = "ALAN_PR_MERGE_AUDIT_DISABLE"
_TIMEOUT_ENV = "ALAN_PR_MERGE_TIMEOUT_S"
_POLL_INTERVAL_ENV = "ALAN_PR_MERGE_POLL_INTERVAL_S"
_MAX_POLLS_ENV = "ALAN_PR_MERGE_MAX_POLLS"
_DEFAULT_TIMEOUT_S = 600.0
_DEFAULT_POLL_INTERVAL_S = 30.0
_DEFAULT_MAX_POLLS = 20
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ALAN_SUBDIR = ".alan"
_COPILOT_ESCALATION_FILE = "copilot_escalation_count"
_ESCALATION_MAX_ENV = "ALAN_COPILOT_ESCALATION_MAX"
_ESCALATION_DEFAULT = 3

_MERGE_LOCK = threading.Lock()

PrStateKind = Literal["already_merged", "closed", "continue"]
OutcomeKind = Literal[
    "no_pr",
    "already_merged",
    "ready",
    "closed",
    "failed",
    "unresolved",
    "unresolved_ceiling",
    "copilot_unresolved",
    "timeout",
    "conflict",
    "error",
    "codeowners_blocked",
    "codeowners_unsupported",
]


@dataclass(frozen=True)
class PrMergeOutcome:
    kind: OutcomeKind
    pr_number: int | None
    reason: str
    pr_url: str | None = None
    copilot_threads: tuple[_pr_feedback.CopilotThread, ...] = ()
    copilot_comments: tuple[_pr_feedback.CopilotComment, ...] = ()
    credential_failure: _pr_branch.CredentialFailureClass | None = None
    diagnostics: str = ""
    timeout_blocker: str = ""


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in _TRUTHY


def _poll_interval_s() -> float:
    return float_env(_POLL_INTERVAL_ENV, _DEFAULT_POLL_INTERVAL_S)


def _timeout_s() -> float:
    return float_env(_TIMEOUT_ENV, _DEFAULT_TIMEOUT_S)


def _max_polls() -> int:
    return positive_int_env(_MAX_POLLS_ENV, _DEFAULT_MAX_POLLS)



def _audit_disabled() -> bool:
    return os.environ.get(_AUDIT_DISABLE_ENV, "").strip().lower() in _TRUTHY


def _audit(entry: dict[str, object]) -> None:
    if _audit_disabled():
        return
    append_jsonl_audit(_pr_audit_log.audit_dir() / _AUDIT_FILE, entry, log_label="pr_merge")


def _audit_and_collect(
    entry: dict[str, object], entries: list[dict[str, object]]
) -> None:
    _audit(entry)
    if not _audit_disabled():
        entries.append({"timestamp_utc": datetime.now(UTC).isoformat(), **entry})


class PrMergeTeam:
    name = _TEAM_NAME
    supports_resume = False
    restart_rederivable = True

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [PendingTaskSpec(id=_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)]

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del instructions, task_id  # pr_merge polls gh; no prompt to splice instructions into
        del on_proc_started  # pr_merge spawns gh subprocesses, not claude
        reject_resume_inv_id(self.name, resume_inv_id)
        prior_pr = prior.pr_number if prior is not None else None
        if prior_pr is None:
            return _no_pr_handover_with_workflow(workspace, prior, emit, inv_id)
        forge_target = _pr_forge.forge_target_for_ws(workspace)
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
            workflow_mod.save_workflow(workspace, graph.doc)
            emit_node(emit, "node_started", inv_id, node)
            outcome = _poll_until_done(workspace, prior_pr, forge_target=forge_target)
            done = graph.complete_node(_NODE_ROLE)
            workflow_mod.save_workflow(workspace, graph.doc)
            emit_node(emit, "node_completed", inv_id, done)
        return _build_handover(workspace, prior, inv_id, outcome)


TEAM = PrMergeTeam()


def _no_pr_handover_with_workflow(
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
) -> Handover:
    """Finalize the seeded workflow even when we bail out before polling."""
    with workflow_lifecycle(
        workspace, team_name=_TEAM_NAME, inv_id=inv_id, emit=emit
    ) as graph:
        node = graph.start_node(_NODE_ROLE, role=_NODE_ROLE, model=_MODEL)
        workflow_mod.save_workflow(workspace, graph.doc)
        emit_node(emit, "node_started", inv_id, node)
        done = graph.complete_node(_NODE_ROLE)
        workflow_mod.save_workflow(workspace, graph.doc)
        emit_node(emit, "node_completed", inv_id, done)
    outcome = PrMergeOutcome(
        kind="no_pr",
        pr_number=None,
        reason="verification approved without a PR number — manual review needed",
    )
    return _build_handover(workspace, prior, inv_id, outcome)


def _poll_until_done(
    workspace: Workspace, pr_number: int, *, forge_target: ForgeTarget,
) -> PrMergeOutcome:
    start = time.monotonic()
    polls = 0
    timeout = _timeout_s()
    max_polls = _max_polls()
    interval = _poll_interval_s()
    while True:
        outcome = _poll_once(
            workspace, pr_number, start, polls, timeout, max_polls, forge_target=forge_target
        )
        if outcome is not None:
            return outcome
        polls += 1
        time.sleep(interval)


def _poll_once(
    workspace: Workspace, pr_number: int, start: float, polls: int,
    timeout: float, max_polls: int, *, forge_target: ForgeTarget,
) -> PrMergeOutcome | None:
    state, merge_status = _pr_merge_conflict.gh_pr_state_and_merge_status(
        pr_number, forge_target=forge_target
    )
    pr_state_kind = _classify_pr_state(state)
    if pr_state_kind != "continue":
        return _outcome_from_pr_state(
            workspace, pr_number, pr_state_kind, state, forge_target=forge_target
        )
    if merge_status == "DIRTY":
        return _conflict_outcome(workspace, pr_number, forge_target=forge_target)
    checks_kind, ctx = _evaluate_checks(
        workspace, pr_number, start, polls, timeout, max_polls, forge_target=forge_target
    )
    if checks_kind == "pending":
        return None
    # None below = gh refused the merge on branch policy mid-race; caller re-polls.
    return _outcome_from_checks(
        workspace, pr_number, checks_kind, ctx, forge_target=forge_target
    )


def _evaluate_checks(
    workspace: Workspace,
    pr_number: int,
    start: float,
    polls: int,
    timeout: float,
    max_polls: int,
    *, forge_target: ForgeTarget,
) -> tuple[ChecksKind, dict[str, Any]]:
    # `timeout <= 0` fires immediately (guards monotonic-clock resolution
    # returning 0.0 elapsed on the first poll); compare with >= for the boundary.
    elapsed = time.monotonic() - start
    if timeout <= 0 or elapsed >= timeout or polls >= max_polls:
        return "timeout", _pr_merge_no_signal.timeout_ctx(
            workspace, pr_number, elapsed, polls, forge_target=forge_target
        )
    try:
        checks = _gh_pr_checks(pr_number, forge_target=forge_target)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        # subprocess.SubprocessError covers TimeoutExpired + CalledProcess-
        # Error; FileNotFoundError covers the gh-binary-missing case which
        # is NOT a SubprocessError subclass. Both surface cleanly as the
        # "error" outcome instead of crashing pr_merge.
        return "error", {"error": str(exc)}
    feedback = _pr_feedback.fetch_feedback(pr_number, forge_target=forge_target)
    if not feedback.ok:
        return _feedback_fetch_failed(workspace, pr_number, feedback.failure_reason)
    thread_info = feedback.thread_info
    blocking = feedback.blocking_comment_info
    _record_shadow_feedback(workspace, pr_number, thread_info, feedback.shadow_comment_info)
    kind, head_sha, advisory_readiness = resolve_checks_kind(
        workspace, pr_number, checks, thread_info, blocking, forge_target=forge_target,
    )
    ctx = _checks_ctx(checks, thread_info, blocking)
    ctx["head_sha"] = head_sha
    ctx["advisory_readiness"] = advisory_readiness
    return kind, ctx


def _feedback_fetch_failed(
    workspace: Workspace, pr_number: int, reason: str,
) -> tuple[ChecksKind, dict[str, Any]]:
    _audit({
        "task_id": workspace.task_id,
        "workspace_id": workspace.task_id,
        "action": "feedback_fetch_failed",
        "pr_number": pr_number,
        "reason": reason,
    })
    return "pending", {"reason": reason}


def _checks_ctx(
    checks: list[dict[str, str]],
    thread_info: _pr_feedback.ThreadInfo,
    blocking: _pr_feedback.CommentInfo,
) -> dict[str, Any]:
    return {
        "checks": checks,
        "threads": thread_info.human_unresolved,
        "total_threads": thread_info.total,
        "copilot_threads": list(thread_info.copilot_threads),
        "copilot_threads_count": len(thread_info.copilot_threads),
        "comments": blocking.human_items,
        "total_comments": blocking.total,
        "copilot_comments": list(blocking.copilot_items),
        "copilot_comments_count": len(blocking.copilot_items),
    }


def _record_shadow_feedback(
    workspace: Workspace,
    pr_number: int,
    thread_info: _pr_feedback.ThreadInfo,
    shadow_comment_info: _pr_feedback.CommentInfo,
) -> None:
    _audit({
        "task_id": workspace.task_id,
        "workspace_id": workspace.task_id,
        "action": "shadow_feedback_recorded",
        "pr_number": pr_number,
        "thread_other_bot_unresolved": thread_info.other_bot_unresolved,
        "shadow_comment_total": shadow_comment_info.total,
        "shadow_comment_human": shadow_comment_info.human_items,
        "shadow_comment_copilot": len(shadow_comment_info.copilot_items),
        "shadow_comment_other_bot": shadow_comment_info.other_bot_items,
    })


def _classify_pr_state(state: str) -> PrStateKind:
    if state == "MERGED":
        return "already_merged"
    if state == "CLOSED":
        return "closed"
    return "continue"


def _outcome_from_pr_state(
    workspace: Workspace, pr_number: int, kind: PrStateKind, state: str,
    *, forge_target: ForgeTarget,
) -> PrMergeOutcome:
    del workspace  # audit happens at outcome time, not here
    pr_url = _pr_url(pr_number, forge_target=forge_target)
    if kind == "already_merged":
        return PrMergeOutcome(
            kind="already_merged",
            pr_number=pr_number,
            reason=f"PR #{pr_number} already merged (idempotency short-circuit)",
            pr_url=pr_url,
        )
    return PrMergeOutcome(
        kind="closed",
        pr_number=pr_number,
        reason=f"PR #{pr_number} is CLOSED (state={state!r}) — escalating",
        pr_url=pr_url,
    )


def _conflict_outcome(
    workspace: Workspace, pr_number: int, *, forge_target: ForgeTarget,
) -> PrMergeOutcome:
    _audit({
        "task_id": workspace.task_id, "workspace_id": workspace.task_id,
        "action": "merge_gate_conflict_detected", "pr_number": pr_number,
    })
    reason = (
        f"conflict: PR #{pr_number} has mergeStateStatus=DIRTY; not a check-settling "
        f"timeout. {_pr_merge_conflict.CONFLICT_RECOVERY_RECIPE}"
    )
    return PrMergeOutcome(
        kind="conflict", pr_number=pr_number, reason=reason,
        pr_url=_pr_url(pr_number, forge_target=forge_target),
    )


def _outcome_from_checks(
    workspace: Workspace,
    pr_number: int,
    kind: ChecksKind,
    ctx: dict[str, Any],
    *, forge_target: ForgeTarget,
) -> PrMergeOutcome | None:
    ctx["repo"] = forge_target.repo.slug
    if kind == "ready":
        return _merge_or_dry_run(
            workspace, pr_number, str(ctx.get("head_sha", "")), forge_target=forge_target,
            advisory_readiness=cast(
                _pr_ready.PrReadiness | None, ctx.get("advisory_readiness")
            ),
        )
    if kind == "copilot_unresolved":
        return PrMergeOutcome(
            kind="copilot_unresolved",
            pr_number=pr_number,
            reason=_pr_merge_escalation.checks_reason(kind, pr_number, ctx),
            pr_url=_pr_url(pr_number, forge_target=forge_target),
            copilot_threads=_extract_copilot_threads(ctx),
            copilot_comments=_extract_copilot_comments(ctx),
        )
    return PrMergeOutcome(
        kind=cast(OutcomeKind, kind),
        pr_number=pr_number,
        reason=_pr_merge_escalation.checks_reason(kind, pr_number, ctx),
        pr_url=_pr_url(pr_number, forge_target=forge_target),
        timeout_blocker=str(ctx.get("timeout_blocker", "")),
    )


def _merge_or_dry_run(
    workspace: Workspace, pr_number: int, expected_head_sha: str = "",
    *, forge_target: ForgeTarget,
    advisory_readiness: _pr_ready.PrReadiness | None = None,
) -> PrMergeOutcome | None:
    if dry_run_enabled():
        _audit({
            "task_id": workspace.task_id,
            "workspace_id": workspace.task_id,
            "action": "dry_run_merge",
            "pr_number": pr_number,
        })
        if advisory_readiness is not None:
            audit_advisory_tolerated(workspace, pr_number, advisory_readiness, forge_target)
        _copilot_escalation_path(workspace).unlink(missing_ok=True)
        return PrMergeOutcome(
            kind="ready",
            pr_number=pr_number,
            reason=f"PR #{pr_number} would have been merged (dry run)",
            pr_url=_pr_url(pr_number, forge_target=forge_target),
        )
    return _execute_merge(
        workspace, pr_number, expected_head_sha, forge_target=forge_target,
        advisory_readiness=advisory_readiness,
    )


def _audit_head_mismatch(
    workspace: Workspace, pr_number: int, expected_head_sha: str,
    *, stage: str, observed_head_sha: str = "",
) -> None:
    append_jsonl_audit(
        _pr_audit_log.audit_dir() / _MERGE_GATE_AUDIT_FILE,
        {
            "task_id": workspace.task_id, "action": "merge_deferred_head_moved",
            "pr_number": pr_number, "expected_head_sha": expected_head_sha,
            "observed_head_sha": observed_head_sha, "stage": stage,
        },
        log_label="pr_merge",
    )


def _behind_during_merge_outcome(
    workspace: Workspace, pr_number: int, *, forge_target: ForgeTarget,
) -> PrMergeOutcome:
    outcome = _pr_branch.update_pr_branch_outcome(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    update_ok = outcome is _pr_branch.BranchUpdateOutcome.UPDATED
    _pr_merge_post_update.record_merge_time_system_head_if_updated(
        update_ok, workspace, pr_number, forge_target=forge_target
    )
    suffix = "update-branch triggered" if update_ok else "update-branch failed"
    append_jsonl_audit(
        _pr_audit_log.audit_dir() / _MERGE_GATE_AUDIT_FILE,
        {
            "task_id": workspace.task_id,
            "action": "merge_deferred_behind",
            "pr_number": pr_number,
            "update_ok": update_ok,
        },
        log_label="pr_merge",
    )
    return PrMergeOutcome(
        kind="error",
        pr_number=pr_number,
        reason=f"PR #{pr_number} became BEHIND during concurrent merge; {suffix}",
        pr_url=_pr_url(pr_number, forge_target=forge_target),
    )


def _handle_behind_recheck(
    workspace: Workspace, pr_number: int, expected_head_sha: str = "",
    *, forge_target: ForgeTarget,
) -> tuple[bool, PrMergeOutcome | None]:
    # proceed=False, outcome=None re-polls instead of escalating a moved head.
    readiness = _pr_ready.check_pr_merge_readiness(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    if _pr_ready.is_behind(readiness):
        return False, _behind_during_merge_outcome(workspace, pr_number, forge_target=forge_target)
    if recheck_confirms_target(expected_head_sha, readiness):
        return True, None
    _audit_head_mismatch(
        workspace, pr_number, expected_head_sha,
        stage="recheck", observed_head_sha=_pr_ready.observed_head_sha(readiness),
    )
    return False, None


_DIAGNOSTICS_STDERR_CAP = 4000


def _diagnostics_readiness_lines(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget
) -> list[str]:
    readiness = _pr_ready.readiness_or_unavailable(
        pr_number, task_id, forge_target=forge_target
    )
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return ["- readiness: unavailable (gh query failed)"]
    return [
        f"- mergeStateStatus: {readiness.merge_state_status or '?'}",
        f"- reviewDecision: {readiness.review_decision or '?'}",
        f"- failing checks: {', '.join(readiness.failing_checks) or 'none'}",
        f"- pending checks: {', '.join(readiness.pending_checks) or 'none'}",
        f"- quarantined checks: {', '.join(readiness.quarantined_checks) or 'none'}",
    ]


def _diagnostics_rerun_lines(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget
) -> list[str]:
    head_sha = _pr_branch.fetch_flake_guard_head_sha(
        pr_number, task_id, forge_target=forge_target
    )
    count = _pr_state_paths.gate_rerun_count_for_sha(
        pr_number, forge_target, head_sha=head_sha
    )
    survived = _pr_flake_ledger.load_gate_survived_checks(pr_number, forge_target)
    lines = [f"- gate reruns spent: {count}/{_pr_flake_ledger.max_gate_reruns()}"]
    rows = sorted(survived.items())
    lines.extend(f"- survivor evidence: {check} stayed red on {sha}" for check, sha in rows)
    return lines


def _diagnostics_e2e_lines(pr_number: int, *, forge_target: ForgeTarget) -> list[str]:
    tests = _pr_branch.fetch_failing_e2e_tests(pr_number, forge_target=forge_target)
    if tests is None:
        return ["- could not determine which E2E tests failed (no failing-job logs reachable)"]
    return [f"- failing spec: {t.spec} › {t.title}" for t in tests]


def _escalation_diagnostics(
    workspace: Workspace,
    pr_number: int,
    result: MergeAttempt,
    *, forge_target: ForgeTarget,
) -> str:
    lines = ["## Merge escalation diagnostics", f"gh pr merge rc={result.returncode}"]
    stderr = result.stderr[:_DIAGNOSTICS_STDERR_CAP]
    if stderr:
        lines.append(UNTRUSTED_CONTENT_NOTICE)
        lines.append(_fence.fence(stderr))
    lines.extend(
        _diagnostics_readiness_lines(
            pr_number, workspace.task_id, forge_target=forge_target
        )
    )
    lines.extend(
        _diagnostics_rerun_lines(pr_number, workspace.task_id, forge_target=forge_target)
    )
    lines.extend(_diagnostics_e2e_lines(pr_number, forge_target=forge_target))
    return "\n".join(lines)


def _merge_failure_outcome(
    workspace: Workspace,
    pr_number: int,
    result: MergeAttempt,
    *, forge_target: ForgeTarget,
) -> PrMergeOutcome:
    _audit({
        "task_id": workspace.task_id,
        "workspace_id": workspace.task_id,
        "action": "merge_failed",
        "pr_number": pr_number,
        "returncode": result.returncode,
        "stderr": result.stderr[:500],
    })
    failure_class = _forge_errors.raise_for_infra(
        f"gh pr merge PR #{pr_number}",
        returncode=result.returncode, stderr=result.stderr,
    )
    is_auth = failure_class == "auth"
    reason = f"gh pr merge rc={result.returncode}: {result.stderr[:200]}"
    if is_auth:
        reason += " " + _pr_branch.credential_recovery_recipe("transport", forge_target)
    return PrMergeOutcome(
        kind="error",
        pr_number=pr_number,
        reason=reason,
        pr_url=_pr_url(pr_number, forge_target=forge_target),
        credential_failure="expired_token" if is_auth else None,
        diagnostics=_escalation_diagnostics(
            workspace, pr_number, result, forge_target=forge_target
        ),
    )


_POLICY_REFUSAL_MARKER = "base branch policy prohibits the merge"
_HEAD_MISMATCH_MARKER = "head branch was modified"


def _is_policy_refusal(result: MergeAttempt) -> bool:
    return _POLICY_REFUSAL_MARKER in result.stderr.lower()


def _is_head_mismatch_refusal(result: MergeAttempt) -> bool:
    return _HEAD_MISMATCH_MARKER in result.stderr.lower()


def _handle_merge_failure(
    workspace: Workspace, pr_number: int, expected_head_sha: str,
    result: MergeAttempt, *, forge_target: ForgeTarget,
) -> PrMergeOutcome | None:
    if _is_policy_refusal(result):
        _audit({
            "task_id": workspace.task_id,
            "workspace_id": workspace.task_id,
            "action": "merge_policy_hold",
            "pr_number": pr_number,
            "stderr": result.stderr[:200],
        })
        return None
    if _is_head_mismatch_refusal(result):
        # gh's own atomic --match-head-commit refusal; retry, don't escalate.
        _audit_head_mismatch(workspace, pr_number, expected_head_sha, stage="gh_pr_merge_refusal")
        return None
    return _merge_failure_outcome(workspace, pr_number, result, forge_target=forge_target)


def _execute_merge(
    workspace: Workspace, pr_number: int, expected_head_sha: str = "",
    *, forge_target: ForgeTarget,
    advisory_readiness: _pr_ready.PrReadiness | None = None,
) -> PrMergeOutcome | None:
    with _MERGE_LOCK:
        proceed, blocked = _handle_behind_recheck(
            workspace, pr_number, expected_head_sha, forge_target=forge_target
        )
        if not proceed:
            return blocked
        try:
            result = active_forge().merge_pr(
                pr_number, expected_head_sha, forge_target=forge_target, timeout=_GH_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            _audit({
                "task_id": workspace.task_id,
                "workspace_id": workspace.task_id,
                "action": "merge_failed",
                "pr_number": pr_number,
                "error": str(exc),
            })
            raise _forge_errors.ForgeTransientError(
                f"gh pr merge PR #{pr_number} raised {exc}"
            ) from exc
        if not result.ok:
            return _handle_merge_failure(
                workspace, pr_number, expected_head_sha, result, forge_target=forge_target
            )
        _audit({
            "task_id": workspace.task_id,
            "workspace_id": workspace.task_id,
            "action": "merge_ok",
            "pr_number": pr_number,
        })
        if advisory_readiness is not None:
            audit_advisory_tolerated(workspace, pr_number, advisory_readiness, forge_target)
    _copilot_escalation_path(workspace).unlink(missing_ok=True)
    return PrMergeOutcome(
        kind="ready",
        pr_number=pr_number,
        reason=f"PR #{pr_number} merged via gh pr merge --squash --delete-branch",
        pr_url=_pr_url(pr_number, forge_target=forge_target),
    )


def _gh_pr_checks(
    pr_number: int, *, forge_target: ForgeTarget,
) -> list[dict[str, str]]:
    read = active_forge().read_checks(
        pr_number, forge_target=forge_target, timeout=_GH_TIMEOUT_S,
    )
    parsed = [_check_item(item) for item in read.checks]
    if not read.ok and not parsed:
        _forge_errors.raise_for_infra(
            f"gh pr checks PR #{pr_number}",
            returncode=read.returncode, stderr=read.stderr,
        )
        return PENDING_FALLBACK
    return parsed


def _check_item(item: JsonObject) -> dict[str, str]:
    name_raw = item.get("name", "")
    state_raw = item.get("state", "")
    return {
        "name": name_raw if isinstance(name_raw, str) else "",
        "state": state_raw if isinstance(state_raw, str) else "",
    }


def _pr_url(pr_number: int, *, forge_target: ForgeTarget) -> str:
    return f"https://github.com/{forge_target.repo.owner}/{forge_target.repo.repo}/pull/{pr_number}"


def _build_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    if outcome.kind in {"already_merged", "ready"}:
        return _terminal_handover(workspace, prior, inv_id, outcome)
    # `unresolved_ceiling` joins `closed`/`error` on the escalation path
    # per ADR §"Open items" §1 — we can't paginate past the first 100
    # threads, so a PR with >100 threads is an "I can't safely decide"
    # situation, not an "await human to answer" one.
    if outcome.kind in {"closed", "error", "unresolved_ceiling"}:
        return _pr_merge_post_update.forward_or(
            workspace, prior, inv_id, outcome,
            lambda: _escalation_handover(workspace, prior, inv_id, outcome),
        )
    if outcome.kind == "copilot_unresolved":
        return _copilot_handover_or_escalate(workspace, prior, inv_id, outcome)
    if (
        outcome.kind in {"failed", "timeout"}
        and prior is not None
        and prior.dependency_bot_authorized
    ):
        return _dep_bot_rebase_handover(workspace, prior, inv_id, outcome)
    return _pr_merge_post_update.forward_or(
        workspace, prior, inv_id, outcome,
        lambda: _await_human_handover(workspace, prior, inv_id, outcome),
    )


_DEP_REBASE_DIRECTIVE = (
    "\n\nThis is a Dependabot dependency-update PR dispatched by the autonomous "
    "dependency-update pipeline; its checks fail because the branch is behind main and/or its "
    "lockfile is stale (e.g. a `uv sync --locked` mismatch), or a flaky gate tripped. Bring the "
    "branch up to date and regenerate lockfiles — do not change application code or tests:\n1. "
    "Merge `origin/main` into the PR branch.\n2. Regenerate every changed lockfile: run `uv "
    "lock` when `uv.lock`/`pyproject.toml` changed; run `npm install --prefix alan/web` when "
    "`package-lock.json`/`package.json` changed. Resolve lockfile conflicts by regenerating, "
    "never by hand-editing.\n3. Confirm the gate passes locally (`uv sync --locked` succeeds), "
    "then commit the regenerated lockfiles.\n4. If `pyproject.toml`/`uv.lock` changed, also "
    "make a minimal edit to `docs/architecture/dep_sweep.md` in the same commit — CLAUDE.md's "
    "drift gate blocks pyproject-touching PRs that carry no doc change (docs are in scope for "
    "this task; application code and tests are not).\nIf a gate still fails for a real, "
    "non-transient reason after regeneration, stop and explain rather than weakening any gate."
)


def _dep_rebase_decision(inv_id: str, outcome: PrMergeOutcome) -> Decision:
    return Decision(
        id=f"dep-rebase-{outcome.pr_number}-{inv_id}",
        statement=(
            f"Route Dependabot PR #{outcome.pr_number} to implementation_fix to "
            "rebase onto main and regenerate stale lockfiles."
        ),
        rationale=outcome.reason,
        confidence=0.9,
        reversibility="high",
    )


def _dep_bot_rebase_handover(
    workspace: Workspace,
    prior: Handover,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=["implementation_fix"],
        iteration=prior.iteration + 1,
        revisit_of=prior.handover_id,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=outcome.reason + _DEP_REBASE_DIRECTIVE,
        pr_number=outcome.pr_number,
        decisions=[_dep_rebase_decision(inv_id, outcome)],
        dependency_bot_authorized=True,
        test_scenarios=carried_scenarios(prior),
    )


def _terminal_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    description = (
        f"PR #{outcome.pr_number} merged"
        if outcome.kind == "ready"
        else f"PR #{outcome.pr_number} already merged"
    )
    artifact = ArtifactRef(path=outcome.pr_url or "", description=description)
    return build_terminal_handover(
        workspace,
        from_team=_TEAM_NAME,
        summary=outcome.reason,
        prior=prior,
        invocation_id=inv_id,
        pr_number=outcome.pr_number,
        artifacts_produced=[artifact],
    )


def _escalation_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    ctx = HandoverContext.build(
        workspace, prior, inv_id,
        team_name=_TEAM_NAME, default_forbidden=(), next_team=(),
    )
    summary = "\n\n".join([outcome.reason, *filter(None, [outcome.diagnostics])])
    handover = escalation_skeleton(ctx, summary, outcome.pr_number)
    return handover.model_copy(update={"credential_failure": outcome.credential_failure})


def _await_human_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
    extra_artifacts: list[ArtifactRef] | None = None,
) -> Handover:
    repo_slug = _pr_forge.forge_target_for_ws(workspace).repo.slug
    options, recommended, option_resolvers = _pr_merge_escalation.merge_block_options(
        outcome.kind, repo_slug=repo_slug, timeout_blocker=outcome.timeout_blocker,
    )
    question = OpenQuestion(
        id=f"pr-merge-{outcome.kind}",
        question=outcome.reason,
        proposed_answer_space=options,
        recommended=recommended,
        question_class="merge_block_escalation",
        resolver_role="human",
        blocking=True,
        prior_attempts=[],
        option_resolvers=option_resolvers,
    )
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=[],
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=outcome.reason,
        pr_number=outcome.pr_number,
        open_questions=[question],
        artifacts_produced=list(extra_artifacts) if extra_artifacts else [],
        dependency_bot_authorized=prior.dependency_bot_authorized if prior is not None else False,
        test_scenarios=carried_scenarios(prior),
    )


def _extract_copilot_threads(
    ctx: dict[str, Any],
) -> tuple[_pr_feedback.CopilotThread, ...]:
    raw = ctx.get("copilot_threads", [])
    if not isinstance(raw, list):
        return ()
    out: list[_pr_feedback.CopilotThread] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, _pr_feedback.CopilotThread):
            out.append(item)
    return tuple(out)


def _extract_copilot_comments(
    ctx: dict[str, Any],
) -> tuple[_pr_feedback.CopilotComment, ...]:
    raw = ctx.get("copilot_comments", [])
    if not isinstance(raw, list):
        return ()
    out: list[_pr_feedback.CopilotComment] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, _pr_feedback.CopilotComment):
            out.append(item)
    return tuple(out)


def _copilot_escalation_max() -> int:
    raw = os.environ.get(_ESCALATION_MAX_ENV, "").strip()
    if not raw:
        return _ESCALATION_DEFAULT
    try:
        v = int(raw)
    except ValueError:
        return _ESCALATION_DEFAULT
    return v if v > 0 else _ESCALATION_DEFAULT


def _copilot_escalation_path(workspace: Workspace) -> Path:
    return workspace.path / _ALAN_SUBDIR / _COPILOT_ESCALATION_FILE


def _copilot_escalation_count(workspace: Workspace) -> int:
    try:
        return int(_copilot_escalation_path(workspace).read_text().strip())
    except (OSError, ValueError):
        return 0


def _increment_copilot_escalation(workspace: Workspace) -> None:
    p = _copilot_escalation_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(_copilot_escalation_count(workspace) + 1))


def _copilot_handover_or_escalate(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    count = _copilot_escalation_count(workspace)
    max_cap = _copilot_escalation_max()
    if count >= max_cap:
        repo_slug = _pr_forge.forge_target_for_ws(workspace).repo.slug
        cap_entries: list[dict[str, object]] = []
        _audit_and_collect({
            "task_id": workspace.task_id,
            "action": "copilot_escalation_cap_reached",
            "pr_number": outcome.pr_number,
            "count": count,
        }, cap_entries)
        cap_outcome = PrMergeOutcome(
            kind=outcome.kind,
            pr_number=outcome.pr_number,
            reason=_pr_merge_escalation.copilot_cap_reason(
                outcome.pr_number, count, max_cap, repo_slug=repo_slug,
            ),
            pr_url=outcome.pr_url,
            copilot_threads=outcome.copilot_threads,
            copilot_comments=outcome.copilot_comments,
        )
        audit_path = _write_copilot_audit_file(workspace, inv_id, cap_entries)
        extra: list[ArtifactRef] = []
        if audit_path is not None:
            extra.append(ArtifactRef(
                path=str(audit_path),
                description=(
                    f"Copilot review audit log for inv {inv_id} "
                    f"({len(cap_entries)} event(s))"
                ),
            ))
        return _await_human_handover(workspace, prior, inv_id, cap_outcome, extra_artifacts=extra)
    _increment_copilot_escalation(workspace)
    return _copilot_handover(workspace, prior, inv_id, outcome)


def _copilot_summary(
    reason: str,
    threads: tuple[_pr_feedback.CopilotThread, ...],
    comments: tuple[_pr_feedback.CopilotComment, ...] = (),
) -> str:
    if not threads and not comments:
        return reason
    parts = [reason, f"\n\n{UNTRUSTED_CONTENT_NOTICE}"]
    for i, t in enumerate(threads, 1):
        parts.append(
            f"\n\nThread {i} ({t.thread_id}, database_id={t.database_id}):\n"
            f"{_fence.fence(t.body)}"
        )
    for i, c in enumerate(comments, 1):
        parts.append(
            f"\n\nComment {i} ({c.source}, database_id={c.database_id}):\n"
            f"{_fence.fence(c.body)}"
        )
    return "".join(parts)


def _resolution_command(c: _pr_feedback.CopilotComment) -> str:
    return (
        "Add a row to the copilot_resolution.yaml block: "
        f"mode: comment_ack, database_id: {c.database_id}, "
        "body: <your acknowledgement text>."
    )


def _thread_block(i: int, t: _pr_feedback.CopilotThread) -> str:
    return (
        f"## Thread {i}\n\n"
        f"- thread_id: {t.thread_id}\n"
        f"- database_id: {t.database_id}\n"
        f"\n{_fence.fence(t.body)}\n"
    )


def _comment_block(i: int, c: _pr_feedback.CopilotComment) -> str:
    return (
        f"## Comment {i}\n\n"
        f"- database_id: {c.database_id}\n"
        f"- source: {c.source}\n"
        f"\n{_fence.fence(c.body)}\n"
        f"\nResolution:\n"
        f"  {_resolution_command(c)}\n"
    )


def _write_copilot_threads_file(
    workspace: Workspace,
    inv_id: str,
    threads: tuple[_pr_feedback.CopilotThread, ...],
    comments: tuple[_pr_feedback.CopilotComment, ...] = (),
) -> Path:
    alan_dir = workspace.path / _ALAN_SUBDIR
    alan_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = alan_dir / f"copilot_threads_{inv_id}.md"
    blocks: list[str] = []
    for i, t in enumerate(threads, 1):
        blocks.append(_thread_block(i, t))
    for i, c in enumerate(comments, 1):
        blocks.append(_comment_block(i, c))
    body = "\n---\n\n".join(blocks)
    content = f"{UNTRUSTED_CONTENT_NOTICE}\n\n{body}" if blocks else body
    artifact_path.write_text(content)
    return artifact_path


def _write_copilot_audit_file(
    workspace: Workspace,
    inv_id: str,
    entries: list[dict[str, object]],
) -> Path | None:
    if not entries:
        return None
    alan_dir = workspace.path / _ALAN_SUBDIR
    audit_path = alan_dir / f"copilot_audit_{inv_id}.jsonl"
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in entries)
    try:
        alan_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        logger.warning("pr_merge: failed to write copilot audit file %s", audit_path)
        return None
    return audit_path


def _copilot_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    outcome: PrMergeOutcome,
) -> Handover:
    threads = outcome.copilot_threads
    comments = outcome.copilot_comments
    decision = _copilot_decision(inv_id, outcome, threads, comments)
    entries = _emit_copilot_audits(workspace, outcome, threads, comments)
    artifact_path = _write_copilot_threads_file(workspace, inv_id, threads, comments)
    artifact = ArtifactRef(
        path=str(artifact_path),
        description=(
            f"{len(threads)} Copilot review thread(s) and "
            f"{len(comments)} Copilot comment(s) for inv {inv_id}"
        ),
    )
    artifacts: list[ArtifactRef] = [artifact]
    audit_path = _write_copilot_audit_file(workspace, inv_id, entries)
    if audit_path is not None:
        artifacts.append(ArtifactRef(
            path=str(audit_path),
            description=(
                f"Copilot review audit log for inv {inv_id} "
                f"({len(entries)} event(s))"
            ),
        ))
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=_TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=["implementation_fix"],
        iteration=(prior.iteration + 1 if prior is not None else 1),
        revisit_of=prior.handover_id if prior is not None else None,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=_copilot_summary(outcome.reason, threads, comments),
        pr_number=outcome.pr_number,
        decisions=[decision],
        artifacts_produced=artifacts,
        test_scenarios=carried_scenarios(prior),
    )


def _copilot_decision(
    inv_id: str,
    outcome: PrMergeOutcome,
    threads: tuple[_pr_feedback.CopilotThread, ...],
    comments: tuple[_pr_feedback.CopilotComment, ...],
) -> Decision:
    rationale_parts: list[str] = []
    rationale_parts.extend(t.body for t in threads)
    rationale_parts.extend(c.body for c in comments)
    return Decision(
        id=f"copilot-threads-{inv_id}",
        statement=(
            f"Route PR #{outcome.pr_number} to implementation_fix to address "
            f"{len(threads)} Copilot review thread(s) and "
            f"{len(comments)} Copilot comment(s)"
        ),
        rationale="\n\n".join(rationale_parts) or "no thread body",
        confidence=0.9,
        reversibility="high",
    )


def _emit_copilot_audits(
    workspace: Workspace,
    outcome: PrMergeOutcome,
    threads: tuple[_pr_feedback.CopilotThread, ...],
    comments: tuple[_pr_feedback.CopilotComment, ...],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for t in threads:
        _audit_and_collect({
            "task_id": workspace.task_id,
            "action": "copilot_thread_routed",
            "pr_number": outcome.pr_number,
            "thread_id": t.thread_id,
            "database_id": t.database_id,
            "body_preview": t.body[:200],
        }, entries)
    for c in comments:
        _audit_and_collect({
            "task_id": workspace.task_id,
            "action": "comment_unresolved",
            "pr_number": outcome.pr_number,
            "database_id": c.database_id,
            "source": c.source,
            "body_preview": c.body[:200],
        }, entries)
    return entries


__all__ = [
    "TEAM",
    "PrMergeOutcome",
    "PrMergeTeam",
    "dry_run_enabled",
]
