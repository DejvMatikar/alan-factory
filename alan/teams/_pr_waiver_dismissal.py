from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from alan.engine_config import ForgeTarget
from alan.teams import _pr_branch, _pr_post
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_json import _JsonObject  # pyright: ignore[reportPrivateUsage]

WaiverOutcome = Literal["dismissed", "withheld_floor", "failed", "none_found"]

AUDIT_ACTION = "waiver_review_dismissal"

_BLOCKING_OTHER_AUTHOR_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


@dataclass(frozen=True)
class WaiverDismissal:
    outcome: WaiverOutcome
    review_ids: tuple[int, ...]
    other_author_blocking: bool


def _review_state(review: _JsonObject) -> str:
    return str(review.get("state") or "").upper()


def _review_login(review: _JsonObject) -> str | None:
    return _pr_post._parse_review_user_login(review)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def _latest_other_author_states(
    reviews: tuple[_JsonObject, ...], verifier: _pr_branch.VerifierIdentity | None,
) -> dict[str, str]:
    latest: dict[str, str] = {}
    for review in reviews:
        login = _review_login(review)
        if login is None or _pr_branch.is_verifier(verifier, login):
            continue
        state = _review_state(review)
        if state not in _BLOCKING_OTHER_AUTHOR_STATES:
            continue
        latest[login] = state
    return latest


def _other_author_blocking(
    reviews: tuple[_JsonObject, ...], verifier: _pr_branch.VerifierIdentity | None,
) -> bool:
    latest = _latest_other_author_states(reviews, verifier)
    return any(state == "CHANGES_REQUESTED" for state in latest.values())


def _waiver_target_ids(
    reviews: tuple[_JsonObject, ...], verifier: _pr_branch.VerifierIdentity | None,
) -> tuple[int, ...]:
    ids: list[int] = []
    for review in reviews:
        if _review_state(review) != "CHANGES_REQUESTED":
            continue
        if not _pr_branch.is_verifier(verifier, _review_login(review)):
            continue
        rid = review.get("id")
        if isinstance(rid, int):
            ids.append(rid)
    return tuple(ids)


def dismiss_for_waiver(
    pr_number: int,
    *,
    forge_target: ForgeTarget,
    question_id: str,
    floor_reason: str | None,
) -> WaiverDismissal:
    if floor_reason is not None:
        return WaiverDismissal("withheld_floor", (), False)
    stdout = _pr_post.fetch_review_stream(pr_number, forge_target=forge_target)
    if stdout is None:
        return WaiverDismissal("failed", (), False)
    reviews = _pr_post.parse_review_objects(stdout)
    verifier = _pr_branch.verifier_identity()
    other_author_blocking = _other_author_blocking(reviews, verifier)
    review_ids = _waiver_target_ids(reviews, verifier)
    if not review_ids:
        return WaiverDismissal("none_found", (), other_author_blocking)
    message = f"Waived by human answer to {question_id}"
    results: list[bool] = []
    for rid in review_ids:
        results.append(
            _pr_post._dismiss_one_review(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                pr_number, rid, forge_target=forge_target, message=message,
            )
        )
    outcome: WaiverOutcome = "dismissed" if all(results) else "failed"
    return WaiverDismissal(outcome, review_ids, other_author_blocking)


def record_waiver_dismissal(
    *,
    task_id: str,
    pr_number: int,
    question_id: str,
    invocation_id: str,
    reviewer_count: int | None,
    floor_reason: str | None,
    result: WaiverDismissal,
) -> None:
    append_audit({
        "task_id": task_id,
        "action": AUDIT_ACTION,
        "pr_number": pr_number,
        "question_id": question_id,
        "invocation_id": invocation_id,
        "outcome": result.outcome,
        "review_ids": list(result.review_ids),
        "reviewer_count": reviewer_count,
        "floor_reason": floor_reason,
        "other_author_blocking": result.other_author_blocking,
    })
