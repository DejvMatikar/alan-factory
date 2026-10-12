from __future__ import annotations

from enum import Enum
from typing import cast

from alan.engine_config import ForgeTarget
from alan.park import Blocker
from alan.teams import (
    _pr_audit_log,
    _pr_branch,
    _pr_closing,
    _pr_flake_ledger,
    _pr_freshness,
    _pr_post,
    _pr_reconcile,
    _pr_sonar,
    _pr_state_paths,
)
from alan.teams._pr_ready import (
    CheckOutcome,
    PrReadiness,
    is_blocked_without_signal,
    resolve_check_outcome,
    results_absent,
)
from alan.teams._pr_sonar import SonarAnnotation

_HOTSPOT_TITLE_PREFIX = "make sure"
_MERGE_STATE_ALLOWS_STALE: frozenset[str] = frozenset({"CLEAN", "HAS_HOOKS", ""})

_MAX_RECONCILE_ATTEMPTS = 2
_RECONCILE_LIFETIME_CAP = 6


class MergeBlockClass(str, Enum):
    BEHIND_PLUS_TRANSIENT = "behind_plus_transient"
    STALE_REVIEW = "stale_review"
    STALE_VERIFIER_REVIEW = "stale_verifier_review"
    PRE_EXISTING_RED = "pre_existing_red"
    TRANSIENT_FLAKE = "transient_flake"
    SONAR_HOTSPOT = "sonar_hotspot"
    SONAR_NO_ANNOTATIONS = "sonar_no_annotations"
    SONAR_GENUINE = "sonar_genuine"
    DIRTY = "dirty"
    STALE_MEASURED_BASELINE = "stale_measured_baseline"
    STALE_MEASURED_BASELINE_CONFLICT = "stale_measured_baseline_conflict"
    STILL_COMPUTING = "still_computing"
    BLOCKED_PENDING = "blocked_pending"
    BLOCKED_NO_SIGNAL = "blocked_no_signal"
    POST_UPDATE_RED = "post_update_red"
    NO_CHECK_RESULTS = "no_check_results"
    CLOSING_LINKS_MISSING = "closing_links_missing"
    GENUINE = "genuine"


CI_RESET_DELAY_CLASSES: frozenset[MergeBlockClass] = frozenset(
    {
        MergeBlockClass.BEHIND_PLUS_TRANSIENT,
        MergeBlockClass.TRANSIENT_FLAKE,
        MergeBlockClass.SONAR_HOTSPOT,
        MergeBlockClass.DIRTY,
        MergeBlockClass.STALE_MEASURED_BASELINE,
    }
)


def _classify_block_head(readiness: PrReadiness) -> MergeBlockClass | None:
    if readiness.merge_state_status == "DIRTY":
        return MergeBlockClass.DIRTY
    if _pr_freshness.MEASURED_BASELINE_STALE_REASON in readiness.failed_reasons:
        return MergeBlockClass.STALE_MEASURED_BASELINE
    if results_absent(readiness):
        return MergeBlockClass.NO_CHECK_RESULTS
    if any(
        reason.startswith(_pr_closing.CLOSING_LINKS_MISSING_REASON)
        for reason in readiness.failed_reasons
    ):
        return MergeBlockClass.CLOSING_LINKS_MISSING
    return None


def classify_block(
    readiness: PrReadiness,
    annotations: tuple[SonarAnnotation, ...],
    forge_target: ForgeTarget,
    *,
    survived_at_head: frozenset[str] = frozenset(),
    system_head: bool = False,
) -> MergeBlockClass:
    readiness = _pr_flake_ledger.aggregate_set_aside(readiness, forge_target)
    head = _classify_block_head(readiness)
    if head is not None:
        return head
    recoverable = _classify_recoverable_block(readiness, survived_at_head)
    if recoverable is not None:
        return recoverable
    if _is_authoritative_check_only_failing(readiness, forge_target):
        return _classify_sonar_block(annotations)
    pending_cls = _classify_pending_state(readiness)
    if pending_cls is not None:
        return pending_cls
    return MergeBlockClass.POST_UPDATE_RED if system_head else MergeBlockClass.GENUINE


def _classify_recoverable_block(
    readiness: PrReadiness, survived_at_head: frozenset[str]
) -> MergeBlockClass | None:
    # Order is load-bearing: PRE_EXISTING_RED before TRANSIENT_FLAKE before the
    # sonar/authoritative-check rung in classify_block (see docs/architecture/
    # merge_recovery.md) — a quarantined check is handled by the pre-existing
    # rung even if it also happens to be on the flaky allowlist, and a lone
    # authoritative-check (e.g. SonarCloud) failure is never on the flaky
    # allowlist so it always falls through unchanged.
    if _is_behind_plus_transient(readiness):
        return MergeBlockClass.BEHIND_PLUS_TRANSIENT
    if _is_stale_review_only(readiness):
        return MergeBlockClass.STALE_REVIEW
    if _is_all_quarantined_failing(readiness):
        return MergeBlockClass.PRE_EXISTING_RED
    if _is_transient_flake(readiness, survived_at_head):
        return MergeBlockClass.TRANSIENT_FLAKE
    return None


def _classify_sonar_block(
    annotations: tuple[SonarAnnotation, ...]
) -> MergeBlockClass:
    if not annotations:
        return MergeBlockClass.SONAR_NO_ANNOTATIONS
    if all(_is_hotspot_annotation(a) for a in annotations):
        return MergeBlockClass.SONAR_HOTSPOT
    return MergeBlockClass.SONAR_GENUINE


def _is_behind_plus_transient(readiness: PrReadiness) -> bool:
    if readiness.merge_state_status != "BEHIND":
        return False
    if readiness.review_decision == "CHANGES_REQUESTED":
        return False
    if _has_genuine_failing_check(readiness):
        return False
    return _behind_reasons_all_benign(readiness.failed_reasons)


def _has_genuine_failing_check(readiness: PrReadiness) -> bool:
    return bool(_pr_flake_ledger.rebase_blocking_failing_checks(readiness))


def _behind_reasons_all_benign(failed_reasons: tuple[str, ...]) -> bool:
    for reason in failed_reasons:
        if reason == "mergeStateStatus=BEHIND":
            continue
        if reason.startswith("pending checks:"):
            continue
        if reason.startswith("failing checks:"):
            continue
        return False
    return True


def _is_transient_flake(
    readiness: PrReadiness, survived_at_head: frozenset[str] = frozenset()
) -> bool:
    if any(c in survived_at_head for c in readiness.failing_checks):
        return False
    return _pr_flake_ledger.flaky_only_failing(readiness)


def _is_all_quarantined_failing(readiness: PrReadiness) -> bool:
    if not readiness.quarantined_checks:
        return False
    if not readiness.failing_checks:
        return False
    if readiness.review_decision == "CHANGES_REQUESTED":
        return False
    quarantined_set = frozenset(readiness.quarantined_checks)
    return all(c in quarantined_set for c in readiness.failing_checks)


def _is_blocked_stale_review_only(readiness: PrReadiness) -> bool:
    return (
        readiness.merge_state_status == "BLOCKED"
        and not readiness.pending_checks
    )


def _is_stale_review_only(readiness: PrReadiness) -> bool:
    return (
        readiness.review_decision == "CHANGES_REQUESTED"
        and not readiness.failing_checks
        and (
            readiness.merge_state_status in _MERGE_STATE_ALLOWS_STALE
            or _is_blocked_stale_review_only(readiness)
        )
    )


def _is_authoritative_check_only_failing(
    readiness: PrReadiness, forge_target: ForgeTarget
) -> bool:
    if len(readiness.failing_checks) != 1:
        return False
    outcome = resolve_check_outcome(readiness.failing_checks[0], readiness, forge_target)
    return (
        outcome is CheckOutcome.FAIL_OR_PENDING
        and readiness.review_decision != "CHANGES_REQUESTED"
        and readiness.merge_state_status in _MERGE_STATE_ALLOWS_STALE
    )


def _is_pending_only(readiness: PrReadiness) -> bool:
    return (
        not readiness.failing_checks
        and readiness.review_decision != "CHANGES_REQUESTED"
        and readiness.merge_state_status in _MERGE_STATE_ALLOWS_STALE
        and bool(readiness.pending_checks)
    )


def _is_blocked_pending_only(readiness: PrReadiness) -> bool:
    return (
        readiness.merge_state_status == "BLOCKED"
        and not readiness.failing_checks
        and readiness.review_decision != "CHANGES_REQUESTED"
        and bool(readiness.pending_checks)
    )


def _is_unstable_pending_only(readiness: PrReadiness) -> bool:
    return (
        readiness.merge_state_status == "UNSTABLE"
        and not readiness.failing_checks
        and readiness.review_decision != "CHANGES_REQUESTED"
        and bool(readiness.pending_checks)
    )


def _classify_pending_state(readiness: PrReadiness) -> MergeBlockClass | None:
    if _is_pending_only(readiness):
        return MergeBlockClass.STILL_COMPUTING
    if _is_unstable_pending_only(readiness):
        return MergeBlockClass.STILL_COMPUTING
    if _is_blocked_pending_only(readiness):
        return MergeBlockClass.BLOCKED_PENDING
    if is_blocked_without_signal(readiness):
        return MergeBlockClass.BLOCKED_NO_SIGNAL
    return None


def _is_hotspot_annotation(a: SonarAnnotation) -> bool:
    return a.title.lower().startswith(_HOTSPOT_TITLE_PREFIX)


def needs_sonar_annotations(
    readiness: PrReadiness, forge_target: ForgeTarget
) -> bool:
    readiness = _pr_flake_ledger.aggregate_set_aside(readiness, forge_target)
    return _is_authoritative_check_only_failing(readiness, forge_target)


def _audit_recovery(
    task_id: str, pr_number: int, block_class: MergeBlockClass, recovered: bool,
    reconcile_outcome: str | None = None,
) -> None:
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "merge_recovery_attempt",
        "pr_number": pr_number,
        "block_class": block_class.value,
        "recovered": recovered,
        **({"reconcile_outcome": reconcile_outcome} if reconcile_outcome is not None else {}),
    })


def _rerun_concluded_at_head(
    pr_number: int, readiness: PrReadiness, forge_target: ForgeTarget, head_sha: str | None,
) -> bool:
    return (
        head_sha is not None
        and not readiness.pending_checks
        and _pr_state_paths.gate_rerun_count_for_sha(
            pr_number, forge_target, head_sha=head_sha
        ) >= 1
    )


def _record_merge_lane_survival(
    pr_number: int, readiness: PrReadiness, forge_target: ForgeTarget, head_sha: str | None,
) -> None:
    targeted = _pr_state_paths.gate_rerun_checks_for_sha(
        pr_number, forge_target, head_sha=head_sha
    )
    candidates = tuple(
        c for c in readiness.failing_checks
        if c in _pr_flake_ledger.SHARED_FLAKY_ALLOWLIST and c in targeted
    )
    if not candidates:
        return
    specs_before = _pr_state_paths.gate_rerun_specs_for_sha(
        pr_number, forge_target, head_sha=head_sha
    )
    specs_after = _pr_flake_ledger.failing_e2e_specs(
        pr_number, readiness, forge_target=forge_target
    )
    gated = _pr_flake_ledger.spec_gated_survivors(
        candidates, specs_before=specs_before, specs_after=specs_after,
    )
    _pr_flake_ledger.record_flake_survival(
        pr_number, gated, cast(str, head_sha), forge_target=forge_target,
    )


def _merge_lane_survivor_evidence(
    pr_number: int, readiness: PrReadiness, forge_target: ForgeTarget, head_sha: str | None,
) -> tuple[str | None, frozenset[str]]:
    if not _pr_flake_ledger.flaky_only_failing(readiness):
        return None, frozenset()
    survived = _pr_flake_ledger.survived_at_head_checks(
        pr_number, head_sha, forge_target=forge_target
    )
    if _rerun_concluded_at_head(pr_number, readiness, forge_target, head_sha):
        _record_merge_lane_survival(pr_number, readiness, forge_target, head_sha)
    return head_sha, survived


def _mark_system_head(
    pr_number: int, head_sha: str | None, rung: MergeBlockClass, forge_target: ForgeTarget,
) -> None:
    _pr_state_paths.save_system_head(pr_number, head_sha, rung.value, forge_target)


def _recover_behind_plus_transient(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    outcome = _pr_branch.update_pr_branch_outcome(pr_number, task_id, forge_target=forge_target)
    ok = outcome is _pr_branch.BranchUpdateOutcome.UPDATED
    _audit_recovery(task_id, pr_number, MergeBlockClass.BEHIND_PLUS_TRANSIENT, ok)
    if ok:
        head_sha = _pr_branch.fetch_flake_guard_head_sha(
            pr_number, task_id, forge_target=forge_target
        )
        _mark_system_head(
            pr_number, head_sha, MergeBlockClass.BEHIND_PLUS_TRANSIENT, forge_target
        )
    return (ok, MergeBlockClass.BEHIND_PLUS_TRANSIENT, None)


def _recover_stale_review(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    dismissed = _pr_post.dismiss_stale_verifier_reviews(
        pr_number, task_id, forge_target=forge_target
    )
    if dismissed:
        _audit_recovery(task_id, pr_number, MergeBlockClass.STALE_VERIFIER_REVIEW, True)
        return (True, MergeBlockClass.STALE_VERIFIER_REVIEW, None)
    _audit_recovery(task_id, pr_number, MergeBlockClass.STALE_REVIEW, False)
    return (False, MergeBlockClass.STALE_REVIEW, None)


def _recover_stale_measured_baseline(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    outcome = _pr_branch.update_pr_branch_outcome(pr_number, task_id, forge_target=forge_target)
    if outcome is _pr_branch.BranchUpdateOutcome.UPDATED:
        _audit_recovery(task_id, pr_number, MergeBlockClass.STALE_MEASURED_BASELINE, True)
        head_sha = _pr_branch.fetch_flake_guard_head_sha(
            pr_number, task_id, forge_target=forge_target
        )
        _mark_system_head(
            pr_number, head_sha, MergeBlockClass.STALE_MEASURED_BASELINE, forge_target
        )
        return (True, MergeBlockClass.STALE_MEASURED_BASELINE, None)
    if outcome is _pr_branch.BranchUpdateOutcome.CONFLICT:
        _audit_recovery(
            task_id, pr_number, MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, False
        )
        return (False, MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, None)
    _audit_recovery(task_id, pr_number, MergeBlockClass.STALE_MEASURED_BASELINE, False)
    return (False, MergeBlockClass.STALE_MEASURED_BASELINE, None)


def _recover_sonar_hotspot(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    block_class = MergeBlockClass.SONAR_HOTSPOT
    if not _pr_sonar.sonar_ack_hotspots(pr_number, task_id):
        _audit_recovery(task_id, pr_number, block_class, False)
        return (False, block_class, None)
    retrigger_ok = _pr_sonar.retrigger_sonar_analysis(
        pr_number, task_id, forge_target=forge_target
    )
    _audit_recovery(task_id, pr_number, block_class, retrigger_ok)
    return (retrigger_ok, block_class, None)


def reconcile_attempts_exhausted(
    pr_number: int, head_sha: str | None,
    *, forge_target: ForgeTarget,
) -> bool:
    state = _pr_state_paths.load_reconcile_attempt_state(pr_number, forge_target)
    if state.lifetime >= _RECONCILE_LIFETIME_CAP:
        return True
    return (
        _pr_state_paths._reset_aware_count(state, head_sha)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        >= _MAX_RECONCILE_ATTEMPTS
    )


_RECONCILE_BUDGET_SPENDING_OUTCOMES = frozenset(
    {_pr_reconcile.ReconcileOutcome.RECONCILED, _pr_reconcile.ReconcileOutcome.CONFLICT}
)

_TERMINAL_RECONCILE_OUTCOMES = frozenset({_pr_reconcile.ReconcileOutcome.CONFLICT})


def reconcile_verdict_is_terminal(rec: _pr_reconcile.ReconcileResult | None) -> bool:
    return rec is not None and rec.outcome in _TERMINAL_RECONCILE_OUTCOMES


def _recover_dirty(
    pr_number: int, task_id: str, forge_target: ForgeTarget,
    head_sha: str | None = None,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    if head_sha is None:
        head_sha = _pr_branch.fetch_flake_guard_head_sha(
            pr_number, task_id, forge_target=forge_target
        )
    if reconcile_attempts_exhausted(pr_number, head_sha, forge_target=forge_target):
        _audit_recovery(task_id, pr_number, MergeBlockClass.DIRTY, False)
        return (False, MergeBlockClass.DIRTY, None)
    result = _pr_reconcile.reconcile_branch(pr_number, task_id, forge_target=forge_target)
    if result.outcome in _RECONCILE_BUDGET_SPENDING_OUTCOMES:
        _pr_state_paths.bump_reconcile_attempt_count(
            pr_number, forge_target, head_sha=head_sha
        )
    recovered = result.outcome is _pr_reconcile.ReconcileOutcome.RECONCILED
    _audit_recovery(
        task_id, pr_number, MergeBlockClass.DIRTY, recovered,
        reconcile_outcome=result.outcome.value,
    )
    if recovered:
        _mark_system_head(pr_number, result.post_sha, MergeBlockClass.DIRTY, forge_target)
    return (recovered, MergeBlockClass.DIRTY, result)


def _resolve_shared_head_sha(
    pr_number: int, task_id: str, readiness: PrReadiness, forge_target: ForgeTarget,
    marker: _pr_state_paths.SystemHeadMarker | None,
) -> str | None:
    if not (_pr_flake_ledger.flaky_only_failing(readiness) or marker is not None):
        return None
    return _pr_branch.fetch_flake_guard_head_sha(
        pr_number, task_id, forge_target=forge_target
    )


def _is_system_head_match(
    marker: _pr_state_paths.SystemHeadMarker | None, head_sha: str | None
) -> bool:
    return marker is not None and head_sha is not None and marker.head_sha == head_sha


def attempt_mechanical_recovery(
    pr_number: int,
    task_id: str,
    readiness: PrReadiness,
    annotations: tuple[SonarAnnotation, ...] = (),
    *,
    forge_target: ForgeTarget,
) -> tuple[bool, MergeBlockClass, _pr_reconcile.ReconcileResult | None]:
    readiness = _pr_flake_ledger.aggregate_set_aside(readiness, forge_target)
    marker = _pr_state_paths.load_system_head(pr_number, forge_target)
    head_sha = _resolve_shared_head_sha(pr_number, task_id, readiness, forge_target, marker)
    flake_head_sha, survived = _merge_lane_survivor_evidence(
        pr_number, readiness, forge_target, head_sha
    )
    system_head = _is_system_head_match(marker, head_sha)
    block_class = classify_block(
        readiness, annotations, forge_target,
        survived_at_head=survived, system_head=system_head,
    )
    if block_class == MergeBlockClass.DIRTY:
        return _recover_dirty(pr_number, task_id, forge_target, head_sha)
    if block_class == MergeBlockClass.BEHIND_PLUS_TRANSIENT:
        return _recover_behind_plus_transient(pr_number, task_id, forge_target)
    if block_class == MergeBlockClass.STALE_MEASURED_BASELINE:
        return _recover_stale_measured_baseline(pr_number, task_id, forge_target)
    if block_class == MergeBlockClass.STALE_REVIEW:
        return _recover_stale_review(pr_number, task_id, forge_target)
    if block_class == MergeBlockClass.TRANSIENT_FLAKE:
        ok, cls = _recover_transient_flake(
            pr_number, task_id, readiness, forge_target, flake_head_sha
        )
        return (ok, cls, None)
    if block_class == MergeBlockClass.SONAR_HOTSPOT:
        return _recover_sonar_hotspot(pr_number, task_id, forge_target)
    _audit_recovery(task_id, pr_number, block_class, False)
    return (False, block_class, None)


def _recover_transient_flake(
    pr_number: int, task_id: str, readiness: PrReadiness, forge_target: ForgeTarget,
    head_sha: str | None,
) -> tuple[bool, MergeBlockClass]:
    if _pr_flake_ledger.gate_reruns_exhausted(pr_number, head_sha, forge_target=forge_target):
        _audit_recovery(task_id, pr_number, MergeBlockClass.TRANSIENT_FLAKE, False)
        return (False, MergeBlockClass.TRANSIENT_FLAKE)
    specs = _pr_flake_ledger.failing_e2e_specs(pr_number, readiness, forge_target=forge_target)
    triggered = _pr_branch.rerun_flaky_checks(
        pr_number, task_id, readiness.failing_checks, forge_target=forge_target
    )
    if not triggered:
        _audit_recovery(task_id, pr_number, MergeBlockClass.TRANSIENT_FLAKE, False)
        return (False, MergeBlockClass.TRANSIENT_FLAKE)
    _pr_state_paths.bump_gate_rerun_count(
        pr_number, forge_target, head_sha=head_sha,
        checks=tuple(
            c for c in readiness.failing_checks if c in _pr_flake_ledger.SHARED_FLAKY_ALLOWLIST
        ),
        specs=specs,
    )
    _audit_recovery(task_id, pr_number, MergeBlockClass.TRANSIENT_FLAKE, True)
    return (True, MergeBlockClass.TRANSIENT_FLAKE)


_STATIC_UNBLOCK_RECIPES: dict[MergeBlockClass, Blocker] = {
    MergeBlockClass.SONAR_GENUINE: Blocker(
        kind="reason", name="unblock", detail="sonar rule violation",
        instruction=(
            "Open the SonarCloud Code Analysis check on the PR, address "
            "the flagged rule violation(s) and push."
        ),
    ),
    MergeBlockClass.SONAR_NO_ANNOTATIONS: Blocker(
        kind="reason", name="unblock",
        detail="sonar gate failed with no annotations",
        instruction=(
            "SonarCloud failed with no line annotations — open the "
            "SonarCloud PR analysis to see the gate condition (usually "
            "new-code coverage/duplication), address it, push."
        ),
    ),
    MergeBlockClass.PRE_EXISTING_RED: Blocker(
        kind="reason", name="unblock", detail="pre-existing red on main",
        instruction=(
            "main is red on a pre-existing failure — green main, then "
            "re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.STALE_REVIEW: Blocker(
        kind="reason", name="unblock",
        detail="outstanding changes-requested review",
        instruction=(
            "An outstanding CHANGES_REQUESTED review is blocking — "
            "dismiss/resolve it on the PR, then re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.TRANSIENT_FLAKE: Blocker(
        kind="reason", name="unblock",
        detail="flaky check reruns exhausted",
        instruction=(
            "Flaky required check(s) exhausted auto-reruns — gh run "
            "rerun --failed (or investigate if not actually flaky), "
            "then re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.DIRTY: Blocker(
        kind="reason", name="unblock",
        detail="branch conflicts with base — automatic reconciliation exhausted",
        instruction=(
            "Automatic rebase onto the base branch could not clear this "
            "conflict (genuine content conflict, or a concurrent push "
            "repeatedly raced ours). Bring this branch up to date "
            "manually — rebase or merge the base in, resolve the "
            "conflicts, force-push — then re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.STALE_MEASURED_BASELINE: Blocker(
        kind="reason", name="unblock",
        detail="branch behind main on a measured-baseline file — automatic update failed",
        instruction=(
            "A measured-baseline ratchet file is implicated (either this "
            "PR's own diff touches it, or main has moved it since this "
            "branch was cut) and the branch fell behind main; automatic "
            "gh pr update-branch could not bring it forward. Bring this "
            "branch up to date manually — rebase or merge main in, "
            "force-push — then re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT: Blocker(
        kind="reason", name="unblock",
        detail="branch update for a measured-baseline file hit a merge conflict",
        instruction=(
            "A measured-baseline ratchet file is implicated (either this "
            "PR's own diff touches it, or main has moved it since this "
            "branch was cut), and bringing the branch up to date with "
            "main hit a conflict. Rebase or merge main in, resolve the "
            "conflicts, force-push — then re-dispatch pr_merge."
        ),
    ),
    MergeBlockClass.POST_UPDATE_RED: Blocker(
        kind="reason", name="unblock",
        detail="check failed after a system-initiated branch update",
        instruction=(
            "This PR's branch was updated by the system itself (not a human "
            "push) after it was already approved, and a check is now failing "
            "on the resulting commit — the prior approval never saw this "
            "commit. Establish whether the failure comes from what the update "
            "pulled in from the base branch or from this PR's own diff before "
            "changing anything, then fix the real cause and push, or confirm "
            "the base-branch problem and re-dispatch pr_merge once it clears."
        ),
    ),
    MergeBlockClass.CLOSING_LINKS_MISSING: Blocker(
        kind="reason", name="unblock",
        detail="declared issue(s) not present in the forge's closing record",
        instruction=(
            "This PR's brief declares issue(s) it fixes, but the forge's own "
            "closing-issue record does not list all of them — the closing "
            "line may have been truncated or wrapped in a code span. Append "
            "the missing `Fixes #N` line(s) to the PR body and re-dispatch "
            "pr_merge."
        ),
    ),
    MergeBlockClass.NO_CHECK_RESULTS: Blocker(
        kind="reason", name="unblock",
        detail="no check results yet for the measured commit",
        instruction=(
            "The merge lane measured this PR's current commit and its check "
            "rollup came back empty — checks for this commit have not been "
            "created yet. This is expected to resolve on its own shortly; "
            "re-dispatch pr_merge once the checks list is populated. If it "
            "stays empty, confirm the required workflows are actually "
            "configured to run on this branch."
        ),
    ),
}


def unblock_instruction(
    block_class: MergeBlockClass, *, pr_number: int | None
) -> Blocker | None:
    if block_class == MergeBlockClass.SONAR_HOTSPOT:
        hotspot_url = (
            _pr_sonar.sonar_hotspots_pr_url(pr_number) if pr_number is not None else None
        )
        return Blocker(
            kind="reason", name="unblock", detail="sonar hotspot review required",
            instruction=(
                "Open the SonarCloud security-hotspots view, mark each hotspot "
                "Safe (or fix it), then re-dispatch pr_merge."
            ),
            action_url=hotspot_url,
        )
    return _STATIC_UNBLOCK_RECIPES.get(block_class)
