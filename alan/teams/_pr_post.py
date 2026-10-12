from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from alan import secrets_store, store
from alan.engine_config import ForgeTarget
from alan.forge import active_forge
from alan.teams import _pr_branch
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_json import _JsonObject  # pyright: ignore[reportPrivateUsage]

logger = logging.getLogger(__name__)

_DISMISSAL_MESSAGE = "superseded by re-review"


def _parse_review_user_login(review: _JsonObject) -> str | None:
    user = review.get("user")
    if not isinstance(user, dict):
        return None
    login = cast(_JsonObject, user).get("login")
    return login if isinstance(login, str) else None


def _extract_verifier_review_id(
    raw: object, identity: _pr_branch.VerifierIdentity, current_head_sha: str
) -> int | None:
    if not isinstance(raw, dict):
        return None
    review = cast(_JsonObject, raw)
    if str(review.get("state") or "").upper() != "CHANGES_REQUESTED":
        return None
    if not identity.matches(_parse_review_user_login(review)):
        return None
    if review.get("commit_id") == current_head_sha:
        return None
    rid = review.get("id")
    return rid if isinstance(rid, int) else None


def parse_review_objects(stdout: str) -> tuple[_JsonObject, ...]:
    return tuple(store.parse_jsonl_rows(stdout))


def _parse_changes_requested_ids(
    stdout: str, identity: _pr_branch.VerifierIdentity, current_head_sha: str
) -> tuple[int, ...]:
    ids: list[int] = []
    for raw in parse_review_objects(stdout):
        rid = _extract_verifier_review_id(raw, identity, current_head_sha)
        if rid is not None:
            ids.append(rid)
    return tuple(ids)


def fetch_review_stream(pr_number: int, *, forge_target: ForgeTarget) -> str | None:
    return active_forge().reviews(pr_number, forge_target=forge_target)


def _fetch_changes_requested_ids(
    pr_number: int, forge_target: ForgeTarget, current_head_sha: str,
) -> tuple[int, ...]:
    identity = _pr_branch.verifier_identity()
    if identity is None:
        return ()
    stdout = fetch_review_stream(pr_number, forge_target=forge_target)
    if stdout is None:
        return ()
    return _parse_changes_requested_ids(stdout, identity, current_head_sha)


def _dismiss_one_review(
    pr_number: int, review_id: int, *, forge_target: ForgeTarget,
    message: str = _DISMISSAL_MESSAGE,
) -> bool:
    return active_forge().dismiss_review(
        pr_number, review_id, forge_target=forge_target, message=message,
    )


def dismiss_stale_verifier_reviews(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> bool:
    def _on_fail(pr: int, reason: str) -> None:
        append_audit({
            "task_id": task_id,
            "action": "dismiss_stale_reviews_failed",
            "pr_number": pr,
            "reason": reason,
        })

    current_head_sha = _pr_branch._fetch_pr_head_sha(  # pyright: ignore[reportPrivateUsage]
        pr_number, forge_target=forge_target, on_fail=_on_fail
    )
    if current_head_sha is None:
        append_audit({
            "task_id": task_id,
            "action": "dismiss_stale_reviews_failed",
            "pr_number": pr_number,
            "reason": "head_sha_unavailable",
        })
        return False
    review_ids = _fetch_changes_requested_ids(pr_number, forge_target, current_head_sha)
    if not review_ids:
        append_audit({
            "task_id": task_id,
            "action": "dismiss_stale_reviews_skipped",
            "pr_number": pr_number,
        })
        return False
    dismissed = 0
    for rid in review_ids:
        ok = _dismiss_one_review(pr_number, rid, forge_target=forge_target)
        append_audit({
            "task_id": task_id,
            "action": "review_dismissed" if ok else "review_dismiss_failed",
            "pr_number": pr_number,
            "review_id": rid,
        })
        if ok:
            dismissed += 1
    return dismissed == len(review_ids)


ReviewEvent: TypeAlias = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]


@dataclass(frozen=True)
class InlineFinding:
    path: str
    line: int
    body: str


def _build_review_payload(
    body: str, event: ReviewEvent, findings: Sequence[InlineFinding]
) -> _JsonObject:
    return {
        "body": body,
        "event": event,
        "comments": [
            {"path": f.path, "line": f.line, "side": "RIGHT", "body": f.body}
            for f in findings
        ],
    }


@dataclass(frozen=True)
class ReviewPostOutcome:
    ok: bool
    returncode: int | None
    stderr: str | None
    error: str | None


def _post_review_payload(
    pr_number: int, payload: _JsonObject, *, forge_target: ForgeTarget, as_verifier: bool,
) -> ReviewPostOutcome:
    try:
        result = active_forge().post_review(
            pr_number, payload, forge_target=forge_target, as_verifier=as_verifier,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh api review post raised %s", exc)
        return ReviewPostOutcome(ok=False, returncode=None, stderr=None, error=str(exc))
    return ReviewPostOutcome(
        ok=result.ok, returncode=result.returncode, stderr=result.stderr, error=None
    )


def post_pr_review_with_inline_comments(
    pr_number: int,
    body: str,
    event: ReviewEvent,
    findings: Sequence[InlineFinding],
    *,
    as_verifier: bool = False,
    forge_target: ForgeTarget,
) -> ReviewPostOutcome:
    payload = _build_review_payload(body, event, findings)
    return _post_review_payload(
        pr_number, payload, forge_target=forge_target, as_verifier=as_verifier,
    )


def verifier_identity_available(forge_target: ForgeTarget) -> bool:
    return bool(secrets_store.verifier_gh_env(forge_target))
