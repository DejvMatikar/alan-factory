from __future__ import annotations

from collections.abc import Sequence

from alan.lifecycle import Blocker as Blocker
from alan.lifecycle import BlockerKind as BlockerKind
from alan.lifecycle import ParkReason as ParkReason

WAIVER_WITHHELD_BLOCKER = "waiver-dismissal-withheld"


def synthetic_blocker(reason_text: str) -> list[Blocker]:
    text = reason_text.strip() or "no details available"
    return [Blocker(kind="reason", name="reason", detail=text)]


def blockers_from_readiness(
    *,
    failing_checks: Sequence[str] = (),
    pending_checks: Sequence[str] = (),
    failed_reasons: Sequence[str] = (),
    review_decision: str = "",
    merge_state_status: str = "",
    quarantined_checks: Sequence[str] = (),
) -> list[Blocker]:
    blockers = [
        Blocker(
            kind="failing_check", name=check, detail=f"check failing: {check}"
        )
        for check in failing_checks
    ]
    blockers += [
        Blocker(
            kind="pending_check", name=check, detail=f"check pending: {check}"
        )
        for check in pending_checks
    ]
    blockers += [
        Blocker(kind="failed_reason", name=reason, detail=reason)
        for reason in failed_reasons
    ]
    if review_decision:
        blockers.append(
            Blocker(
                kind="review_decision",
                name=review_decision,
                detail=f"PR review decision: {review_decision}",
            )
        )
    if merge_state_status:
        blockers.append(
            Blocker(
                kind="merge_state_status",
                name=merge_state_status,
                detail=f"mergeStateStatus: {merge_state_status}",
            )
        )
    blockers += [
        Blocker(
            kind="quarantined_check",
            name=check,
            detail=f"quarantined as flaky, set aside: {check}",
        )
        for check in quarantined_checks
    ]
    return blockers


def park_reason_for_decision(kind: str, block_class: str | None) -> ParkReason:
    if block_class == "credential":
        return ParkReason.CREDENTIAL
    if block_class is not None:
        return ParkReason.MERGE_BLOCK
    if kind == "escalate":
        return ParkReason.ESCALATED
    return ParkReason.QUESTION
