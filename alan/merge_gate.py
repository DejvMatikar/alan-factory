from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from typing import Literal, TypeAlias, cast

from alan import merge_recovery
from alan import park as park_mod
from alan.engine_config import ForgeTarget
from alan.handover import Handover
from alan.roots import logs_root
from alan.teams import (
    _forge_errors,
    _pr_branch,
    _pr_closing,
    _pr_freshness,
    _pr_ready,
    _pr_reconcile,
    _pr_sonar,
)
from alan.teams._audit import append_jsonl_audit
from alan.teams._pr_forge import forge_target_for_ws
from alan.teams._pr_ready import PrReadiness, ReadinessResult, ReadinessUnavailable
from alan.workspace import Workspace

logger = logging.getLogger("alan.merge_gate")

_MERGE_GATE_AUDIT_FILE = "merge_gate_audit.jsonl"

_E2E_DETAIL_UNAVAILABLE_MESSAGE = (
    "could not determine which E2E tests failed — open the failing check's run log"
)

_CONFLICT_PATH_CAP = 10
_BEHIND_COMMIT_CAP = 10

MERGE_LANE_POLL_CLASSES: frozenset[merge_recovery.MergeBlockClass] = frozenset({
    merge_recovery.MergeBlockClass.STILL_COMPUTING,
    merge_recovery.MergeBlockClass.BLOCKED_PENDING,
    merge_recovery.MergeBlockClass.BLOCKED_NO_SIGNAL,
})


def routes_to_merge_lane(block_class: merge_recovery.MergeBlockClass) -> bool:
    return block_class in MERGE_LANE_POLL_CLASSES


def record_merge_gate_audit(
    task_id: str,
    pr_number: int,
    block_class: merge_recovery.MergeBlockClass | None,
    recovered: bool,
    outcome: str,
    reconcile_outcome: str | None = None,
    *,
    brief_attached: bool = False,
) -> None:
    append_jsonl_audit(
        logs_root() / _MERGE_GATE_AUDIT_FILE,
        {
            "task_id": task_id,
            "pr_number": pr_number,
            "block_class": block_class.value if block_class is not None else None,
            "recovered": recovered,
            "outcome": outcome,
            **({"reconcile_outcome": reconcile_outcome} if reconcile_outcome else {}),
            "brief_attached": brief_attached,
        },
        log_label="merge_gate",
    )


def stale_measured_baseline_forces_check(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> bool:
    return _pr_freshness.is_measured_baseline_stale(
        pr_number, task_id, forge_target=forge_target
    )


def readiness_after_freshness_check(
    ws: Workspace,
    pr_number: int,
    readiness: PrReadiness,
    forge_target: ForgeTarget,
) -> PrReadiness:
    if not (readiness.ok or not readiness.failed_reasons):
        return readiness
    if not stale_measured_baseline_forces_check(pr_number, ws.task_id, forge_target):
        return readiness
    return cast(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        PrReadiness,
        dataclasses.replace(
            readiness,
            ok=False,
            failed_reasons=readiness.failed_reasons
            + (_pr_freshness.MEASURED_BASELINE_STALE_REASON,),
        ),
    )


def _readiness_with_missing_closing_links(
    readiness: PrReadiness, missing: tuple[int, ...],
) -> PrReadiness:
    missing_str = ",".join(f"#{n}" for n in missing)
    return cast(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        PrReadiness,
        dataclasses.replace(
            readiness,
            ok=False,
            failed_reasons=readiness.failed_reasons
            + (f"{_pr_closing.CLOSING_LINKS_MISSING_REASON} {missing_str}",),
        ),
    )


def _closing_issue_numbers_or_none(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> frozenset[int] | None:
    try:
        return _pr_closing.gh_pr_closing_issue_numbers(pr_number, forge_target=forge_target)
    except (_forge_errors.ForgeRateLimited, _forge_errors.ForgeTransientError):
        logger.warning(
            "readiness_after_closing_links_check: closing-link read failed for pr=%s task=%s",
            pr_number, task_id, exc_info=True,
        )
        return None


def _missing_closing_issues(
    issues: tuple[int, ...], closing: frozenset[int],
) -> tuple[int, ...]:
    return tuple(n for n in issues if n not in closing)


def readiness_after_closing_links_check(
    ws: Workspace,
    pr_number: int,
    readiness: PrReadiness,
    forge_target: ForgeTarget,
) -> PrReadiness:
    if not (readiness.ok or not readiness.failed_reasons):
        return readiness
    trailer = _pr_closing.parse_delivers_trailer(ws.meta.description)
    if not trailer.issues:
        return readiness
    closing = _closing_issue_numbers_or_none(pr_number, ws.task_id, forge_target)
    if closing is None:
        return readiness
    missing = _missing_closing_issues(trailer.issues, closing)
    if not missing:
        return readiness
    return _readiness_with_missing_closing_links(readiness, missing)


def pr_merge_remediation_context(
    handover: Handover,
    readiness: PrReadiness,
    block_class: merge_recovery.MergeBlockClass,
    forge_target: ForgeTarget,
) -> tuple[str, str, list[park_mod.Blocker]]:
    reasons = "; ".join(readiness.failed_reasons)
    bc = block_class.value
    reason = f"pr_merge gate [{bc}] PR #{handover.pr_number}: {reasons}"
    blockers = park_mod.blockers_from_readiness(
        failing_checks=readiness.failing_checks,
        pending_checks=readiness.pending_checks,
        failed_reasons=readiness.failed_reasons,
        review_decision=readiness.review_decision,
        merge_state_status=readiness.merge_state_status,
        quarantined_checks=readiness.quarantined_checks,
    )
    append_unblock_instruction(blockers, block_class, handover.pr_number)
    enrich_transient_flake_blockers(blockers, block_class, handover.pr_number, forge_target)
    return reason, bc, blockers


def append_unblock_instruction(
    blockers: list[park_mod.Blocker],
    block_class: merge_recovery.MergeBlockClass,
    pr_number: int | None,
) -> None:
    extra = merge_recovery.unblock_instruction(block_class, pr_number=pr_number)
    if extra is not None:
        blockers.append(extra)


def enrich_transient_flake_blockers(
    blockers: list[park_mod.Blocker],
    block_class: merge_recovery.MergeBlockClass,
    pr_number: int | None,
    forge_target: ForgeTarget,
) -> None:
    if block_class is not merge_recovery.MergeBlockClass.TRANSIENT_FLAKE:
        return
    if pr_number is None:
        return
    tests = _pr_branch.fetch_failing_e2e_tests(pr_number, forge_target=forge_target)
    if tests:
        for t in tests:
            blockers.append(park_mod.Blocker(kind="failing_test", name=t.spec, detail=t.title))
        return
    blockers.append(
        park_mod.Blocker(
            kind="reason", name="e2e_detail_unavailable", detail=_E2E_DETAIL_UNAVAILABLE_MESSAGE,
        )
    )


def _is_pending_blocker(b: park_mod.Blocker) -> bool:
    return b.kind == "pending_check" or (
        b.kind == "failed_reason" and b.detail.startswith("pending checks: ")
    )


def _append_capped(
    blockers: list[park_mod.Blocker],
    items: Sequence[str],
    kind: park_mod.BlockerKind,
    detail_prefix: str,
    truncated_name: str,
    cap: int,
) -> None:
    for item in items[:cap]:
        blockers.append(
            park_mod.Blocker(kind=kind, name=item, detail=f"{detail_prefix}{item}")
        )
    remainder = len(items) - cap
    if remainder > 0:
        noun = truncated_name.removesuffix("_truncated").replace("_", " ")
        blockers.append(
            park_mod.Blocker(
                kind="reason", name=truncated_name,
                detail=f"{remainder} additional {noun} not shown",
            )
        )


def _push_constraint_instruction(branch: str, starting_sha: str | None) -> str | None:
    if not branch or not starting_sha:
        return None
    return f"git push --force-with-lease={branch}:{starting_sha} origin {branch}"


def _branch_head_blocker(detail_sha: str | None, branch: str = "") -> park_mod.Blocker:
    return park_mod.Blocker(
        kind="reason", name="branch_head",
        detail=f"branch head: {detail_sha or 'unknown'}",
        instruction=_push_constraint_instruction(branch, detail_sha),
    )


def enrich_conflict_blockers(
    blockers: list[park_mod.Blocker],
    rec: _pr_reconcile.ReconcileResult | None,
    head_sha: str,
    branch: str = "",
) -> None:
    if rec is None:
        blockers.append(_branch_head_blocker(head_sha, branch))
        return
    blockers[:] = [b for b in blockers if not _is_pending_blocker(b)]
    _append_capped(
        blockers, rec.conflicted_paths, "conflicted_path",
        "conflicts with base: ", "conflicted_paths_truncated", _CONFLICT_PATH_CAP,
    )
    _append_capped(
        blockers, rec.behind_commits, "behind_commit",
        "base has moved past this branch: ", "behind_commits_truncated", _BEHIND_COMMIT_CAP,
    )
    blockers.append(_branch_head_blocker(rec.pre_sha or head_sha, branch))


def conflict_fix_decision(
    blockers: list[park_mod.Blocker],
    rec: _pr_reconcile.ReconcileResult | None,
    block_class: merge_recovery.MergeBlockClass,
    head_sha: str,
    branch: str = "",
) -> bool:
    if block_class is not merge_recovery.MergeBlockClass.DIRTY:
        return False
    if not (rec is None or merge_recovery.reconcile_verdict_is_terminal(rec)):
        return False
    enrich_conflict_blockers(blockers, rec, head_sha, branch)
    return True


def _blocker_line(b: park_mod.Blocker) -> str:
    line = f"- [{b.kind}] {b.name}: {b.detail}"
    if b.instruction:
        line += f" — {b.instruction}"
    return line


def render_remediation_instructions(
    block_class: merge_recovery.MergeBlockClass,
    reasons: str,
    blockers: tuple[park_mod.Blocker, ...],
) -> str:
    lines = [
        f"The pr_merge gate classified this PR's block as `{block_class.value}` and could "
        "not recover it mechanically — this is the specific blocker behind this dispatch, "
        "not a re-derivation of the prior approving review.",
    ]
    if reasons:
        lines.append(f"Reasons: {reasons}")
    lines.extend(_blocker_line(b) for b in blockers)
    return "\n".join(lines)


def reconcile_outcome_str(rec: _pr_reconcile.ReconcileResult | None) -> str | None:
    return rec.outcome.value if rec else None


def _fetch_sonar_annotations_if_needed(
    readiness: PrReadiness, pr_number: int, forge_target: ForgeTarget,
) -> tuple[_pr_sonar.SonarAnnotation, ...]:
    if not merge_recovery.needs_sonar_annotations(readiness, forge_target):
        return ()
    return _pr_sonar.list_sonarcloud_annotations(pr_number, forge_target=forge_target)


@dataclasses.dataclass(frozen=True)
class MergeGateNotAttempted:
    readiness: ReadinessResult


@dataclasses.dataclass(frozen=True)
class MergeGateAttemptFailed:
    readiness: PrReadiness


@dataclasses.dataclass(frozen=True)
class MergeGateRecovered:
    readiness: PrReadiness
    block_class: merge_recovery.MergeBlockClass
    reconcile: _pr_reconcile.ReconcileResult | None


@dataclasses.dataclass(frozen=True)
class MergeGateBlocked:
    readiness: PrReadiness
    block_class: merge_recovery.MergeBlockClass
    reconcile: _pr_reconcile.ReconcileResult | None
    forge_target: ForgeTarget


MergeGateAttempt: TypeAlias = (
    MergeGateNotAttempted | MergeGateAttemptFailed | MergeGateRecovered | MergeGateBlocked
)


def attempt_recovery(
    ws: Workspace, pr_number: int, *, recovered_outcome: str,
) -> MergeGateAttempt:
    forge_target = forge_target_for_ws(ws)
    readiness = _pr_ready.readiness_or_unavailable(
        pr_number, ws.task_id, forge_target=forge_target
    )
    if isinstance(readiness, ReadinessUnavailable):
        return MergeGateNotAttempted(readiness)
    return _attempt_recovery_for_readiness(
        ws, pr_number, readiness, forge_target, recovered_outcome=recovered_outcome,
    )


def _attempt_recovery_for_readiness(
    ws: Workspace, pr_number: int, readiness: PrReadiness, forge_target: ForgeTarget,
    *, recovered_outcome: str,
) -> MergeGateAttempt:
    readiness = _pr_ready.merge_lane_without_advisory(
        readiness, pr_number, ws.task_id, lane="merge_gate", forge_target=forge_target,
    )
    readiness = readiness_after_freshness_check(ws, pr_number, readiness, forge_target)
    readiness = readiness_after_closing_links_check(ws, pr_number, readiness, forge_target)
    if readiness.ok or not readiness.failed_reasons:
        return MergeGateNotAttempted(readiness)
    try:
        annotations = _fetch_sonar_annotations_if_needed(readiness, pr_number, forge_target)
        recovered, block_class, rec = merge_recovery.attempt_mechanical_recovery(
            pr_number, ws.task_id, readiness, annotations, forge_target=forge_target
        )
    except (_forge_errors.ForgeRateLimited, _forge_errors.ForgeTransientError):
        logger.warning(
            "merge_gate: recovery attempt failed for pr=%s task=%s",
            pr_number, ws.task_id, exc_info=True,
        )
        record_merge_gate_audit(ws.task_id, pr_number, None, False, "attempt_failed")
        return MergeGateAttemptFailed(readiness)
    if not recovered:
        return MergeGateBlocked(readiness, block_class, rec, forge_target)
    record_merge_gate_audit(
        ws.task_id, pr_number, block_class, True, recovered_outcome,
        reconcile_outcome_str(rec),
    )
    return MergeGateRecovered(readiness, block_class, rec)


MergeGatePolicy = Literal["attempted", "skipped"]


@dataclasses.dataclass(frozen=True)
class MergeGateDecision:
    policy: MergeGatePolicy
    attempt: MergeGateAttempt | None = None
    skip_reason: str | None = None

    @staticmethod
    def attempted(attempt: MergeGateAttempt) -> "MergeGateDecision":
        return MergeGateDecision(policy="attempted", attempt=attempt)

    @staticmethod
    def skipped(reason: str) -> "MergeGateDecision":
        return MergeGateDecision(policy="skipped", skip_reason=reason)
