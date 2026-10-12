from __future__ import annotations

import fnmatch
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal, TypeAlias, cast

from alan.engine_config import ForgeTarget, is_authoritative_check
from alan.forge import active_forge
from alan.teams import _forge_errors, _pr_branch
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_json import (
    _JsonList,  # pyright: ignore[reportPrivateUsage]
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _str_field,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

_MERGE_STATE_OK: frozenset[str] = frozenset({"CLEAN", "HAS_HOOKS"})

QuarantineReason = Literal["pre_existing", "known_flake", "blocking"]


class CheckOutcome(str, Enum):
    PASS = "pass"  # noqa: S105  # nosec B105
    FAIL_OR_PENDING = "fail_or_pending"
    ABSENT = "absent"


@dataclass(frozen=True)
class QuarantineEvidence:
    check_name: str
    base_result: str
    head_result: str
    reason: QuarantineReason


_KNOWN_FLAKE_REGISTRY: dict[str, frozenset[str]] = {}
_CHECK_FAIL_STATES: frozenset[str] = frozenset({"FAILURE", "ERROR", "TIMED_OUT"})
_CHECK_PENDING_STATES: frozenset[str] = frozenset(
    {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING"}
)


@dataclass(frozen=True)
class PrReadiness:
    ok: bool
    merge_state_status: str
    review_decision: str
    failing_checks: tuple[str, ...] = ()
    pending_checks: tuple[str, ...] = ()
    passing_checks: tuple[str, ...] = ()
    failed_reasons: tuple[str, ...] = ()
    quarantined_checks: tuple[str, ...] = ()
    head_sha: str = ""
    head_ref_name: str = ""
    check_count: int = 0
    failing_status_contexts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadinessUnavailable:
    transient_reason: str


ReadinessResult: TypeAlias = PrReadiness | ReadinessUnavailable


def is_blocked_without_signal(readiness: PrReadiness) -> bool:
    return (
        readiness.merge_state_status == "BLOCKED"
        and not readiness.failing_checks
        and not readiness.pending_checks
        and readiness.review_decision != "CHANGES_REQUESTED"
    )


def is_behind(readiness: ReadinessResult) -> bool:
    return isinstance(readiness, PrReadiness) and readiness.merge_state_status == "BEHIND"


def observed_head_sha(readiness: ReadinessResult) -> str:
    return "" if isinstance(readiness, ReadinessUnavailable) else readiness.head_sha


def results_absent(readiness: PrReadiness) -> bool:
    return (
        readiness.check_count == 0
        and not readiness.failing_checks
        and not readiness.pending_checks
        and not readiness.passing_checks
    )


def resolve_named_check_outcome(check_name: str, readiness: PrReadiness) -> CheckOutcome:
    if check_name in readiness.failing_checks or check_name in readiness.pending_checks:
        return CheckOutcome.FAIL_OR_PENDING
    if check_name in readiness.passing_checks:
        return CheckOutcome.PASS
    return CheckOutcome.ABSENT


def resolve_check_outcome(
    check_name: str, readiness: PrReadiness, forge_target: ForgeTarget
) -> CheckOutcome:
    if not is_authoritative_check(forge_target, check_name):
        return CheckOutcome.ABSENT
    return resolve_named_check_outcome(check_name, readiness)


def _transient_readiness(reason: str) -> ReadinessUnavailable:
    return ReadinessUnavailable(transient_reason=reason)


_CHECK_PASS_STATES: frozenset[str] = frozenset({"SUCCESS"})


def _classify_rollup_item(item: _JsonObject) -> tuple[str, str]:
    state = _str_field(item, "conclusion", "state")
    status = _str_field(item, "status")
    name = str(item.get("name") or item.get("context") or "?")
    if state in _CHECK_FAIL_STATES:
        return ("fail", name)
    if status in _CHECK_PENDING_STATES or state in _CHECK_PENDING_STATES:
        return ("pending", name)
    if state in _CHECK_PASS_STATES:
        return ("pass", name)
    return ("", name)


def _bucketed_check_names_from_rollup(
    rollup: object,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], int]:
    if not isinstance(rollup, list):
        return ((), (), (), 0)
    failing: list[str] = []
    pending: list[str] = []
    passing: list[str] = []
    count = 0
    for raw in cast(_JsonList, rollup):
        if not isinstance(raw, dict):
            continue
        count += 1
        bucket, name = _classify_rollup_item(cast("_JsonObject", raw))
        if bucket == "fail":
            failing.append(name)
        elif bucket == "pending":
            pending.append(name)
        elif bucket == "pass":
            passing.append(name)
    return (tuple(failing), tuple(pending), tuple(passing), count)


def _failing_status_context(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    item = cast("_JsonObject", raw)
    if item.get("name") or not item.get("context"):
        return None
    bucket, name = _classify_rollup_item(item)
    return name if bucket == "fail" else None


def _failing_status_context_names(rollup: object) -> tuple[str, ...]:
    if not isinstance(rollup, list):
        return ()
    found = (_failing_status_context(raw) for raw in cast(_JsonList, rollup))
    return tuple(name for name in found if name is not None)


def _build_failed_reasons(
    merge_state_status: str,
    review_decision: str,
    failing: tuple[str, ...],
    pending: tuple[str, ...],
    results_absent: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if merge_state_status and merge_state_status not in _MERGE_STATE_OK:
        reasons.append(f"mergeStateStatus={merge_state_status}")
    if review_decision == "CHANGES_REQUESTED":
        reasons.append("review decision CHANGES_REQUESTED")
    if failing:
        reasons.append(f"failing checks: {', '.join(failing)}")
    if pending:
        reasons.append(f"pending checks: {', '.join(pending)}")
    if results_absent:
        reasons.append("no check results")
    return tuple(reasons)


def _build_verifier_blocker_reasons(
    merge_state_status: str,
    failing: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if merge_state_status == "DIRTY":
        reasons.append(f"mergeStateStatus={merge_state_status}")
    if failing:
        reasons.append(f"failing checks: {', '.join(failing)}")
    return tuple(reasons)


_ReasonBuilder: TypeAlias = Callable[
    [str, str, tuple[str, ...], tuple[str, ...], bool], tuple[str, ...]
]


def _opt_str_field(payload: _JsonObject, key: str) -> str:
    return str(payload.get(key) or "")


def _readiness_from_payload(
    payload: _JsonObject, reason_builder: _ReasonBuilder,
) -> PrReadiness:
    merge_state_status = _opt_str_field(payload, "mergeStateStatus").upper()
    review_decision = _opt_str_field(payload, "reviewDecision").upper()
    failing, pending, passing, count = _bucketed_check_names_from_rollup(
        payload.get("statusCheckRollup")
    )
    head_sha = _opt_str_field(payload, "headRefOid")
    head_ref_name = _opt_str_field(payload, "headRefName")
    absent = count == 0 and not failing and not pending and not passing
    reasons = reason_builder(merge_state_status, review_decision, failing, pending, absent)
    return PrReadiness(
        ok=not reasons,
        merge_state_status=merge_state_status,
        review_decision=review_decision,
        failing_checks=failing,
        pending_checks=pending,
        passing_checks=passing,
        failed_reasons=reasons,
        head_sha=head_sha,
        head_ref_name=head_ref_name,
        check_count=count,
        failing_status_contexts=_failing_status_context_names(payload.get("statusCheckRollup")),
    )


def _parse_readiness_payload(payload: _JsonObject) -> PrReadiness | None:
    if not payload:
        return None
    return _readiness_from_payload(payload, _build_failed_reasons)


def _latest_review_login(review: _JsonObject) -> str:
    author = review.get("author")
    if not isinstance(author, dict):
        return ""
    return str(cast(_JsonObject, author).get("login") or "")


def _is_human_changes_requested(
    item: object, verifier: _pr_branch.VerifierIdentity,
) -> bool:
    if not isinstance(item, dict):
        return False
    review = cast(_JsonObject, item)
    if str(review.get("state") or "").upper() != "CHANGES_REQUESTED":
        return False
    return not verifier.matches(_latest_review_login(review))


def _human_changes_requested(
    latest_reviews: object, verifier: _pr_branch.VerifierIdentity,
) -> bool | None:
    if not isinstance(latest_reviews, list):
        return None
    return any(
        _is_human_changes_requested(item, verifier)
        for item in cast(_JsonList, latest_reviews)
    )


def _verifier_lane_review_decision(payload: _JsonObject) -> str:
    decision = str(payload.get("reviewDecision") or "").upper()
    if decision != "CHANGES_REQUESTED":
        return decision
    verifier = _pr_branch.verifier_identity()
    if verifier is None:
        return decision
    human_block = _human_changes_requested(payload.get("latestReviews"), verifier)
    if human_block is False:
        return ""
    return decision


def _parse_verifier_readiness_payload(payload: _JsonObject) -> PrReadiness | None:
    if not payload:
        return None
    readiness = _readiness_from_payload(
        payload,
        lambda mss, _rd, fail, _pend, _absent: _build_verifier_blocker_reasons(mss, fail),
    )
    replaced = replace(readiness, review_decision=_verifier_lane_review_decision(payload))
    return cast(PrReadiness, replaced)  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def _readiness_row_fields(readiness: ReadinessResult) -> dict[str, object]:
    if isinstance(readiness, ReadinessUnavailable):
        return {
            "ok": False,
            "gh_ok": False,
            "mergeStateStatus": "",
            "reviewDecision": "",
            "failing": [],
            "pending": [],
            "reasons": [],
            "transient_reason": readiness.transient_reason,
            "head_sha": "",
            "check_count": 0,
        }
    return {
        "ok": readiness.ok,
        "gh_ok": True,
        "mergeStateStatus": readiness.merge_state_status,
        "reviewDecision": readiness.review_decision,
        "failing": list(readiness.failing_checks),
        "pending": list(readiness.pending_checks),
        "reasons": list(readiness.failed_reasons),
        "transient_reason": "",
        "head_sha": readiness.head_sha,
        "check_count": readiness.check_count,
    }


def _audit_readiness(task_id: str, pr_number: int, readiness: ReadinessResult) -> None:
    append_audit({
        "task_id": task_id,
        "action": "pr_readiness_check",
        "pr_number": pr_number,
        **_readiness_row_fields(readiness),
    })


_READINESS_FIELDS = (
    "mergeStateStatus", "reviewDecision", "statusCheckRollup", "headRefOid", "headRefName",
)
_VERIFIER_READINESS_FIELDS = (*_READINESS_FIELDS, "latestReviews")


def _gh_pr_view_readiness(
    pr_number: int, task_id: str, label: str,
    *, forge_target: ForgeTarget,
    fields: tuple[str, ...] = _READINESS_FIELDS,
) -> _JsonObject | ReadinessUnavailable:
    context = f"gh pr view ({label}) PR #{pr_number} [{forge_target.repo.slug}]"
    try:
        read = active_forge().read_pr(
            pr_number, fields, forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh pr view (%s) raised %s", label, exc)
        raise _forge_errors.ForgeTransientError(f"{context} raised {exc}") from exc
    if not read.ok:
        logger.warning(
            "gh pr view (%s) failed (rc=%s): %s", label, read.returncode, read.stderr,
        )
        _forge_errors.raise_for_infra(
            context, returncode=read.returncode, stderr=read.stderr,
        )
        readiness = _transient_readiness(
            f"gh returncode={read.returncode}: {read.stderr[:200]}"
        )
        _audit_readiness(task_id, pr_number, readiness)
        return readiness
    return read.fields


def _resolve_readiness(
    pr_number: int, task_id: str, label: str,
    parser: Callable[[_JsonObject], PrReadiness | None],
    *, forge_target: ForgeTarget,
    fields: tuple[str, ...] = _READINESS_FIELDS,
) -> ReadinessResult:
    payload = _gh_pr_view_readiness(
        pr_number, task_id, label, forge_target=forge_target, fields=fields,
    )
    if isinstance(payload, ReadinessUnavailable):
        return payload
    parsed = parser(payload)
    if parsed is None:
        readiness = _transient_readiness("gh pr view returned unparseable JSON")
        _audit_readiness(task_id, pr_number, readiness)
        return readiness
    _audit_readiness(task_id, pr_number, parsed)
    return parsed


def _fetch_pr_base_sha(
    pr_number: int, *, forge_target: ForgeTarget,
) -> str | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("baseRefOid",), forge_target=forge_target, timeout=GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("_fetch_pr_base_sha raised %s for PR #%s", exc, pr_number)
        return None
    if not read.ok:
        logger.warning("_fetch_pr_base_sha rc=%s for PR #%s", read.returncode, pr_number)
        return None
    sha = read.fields.get("baseRefOid")
    if not isinstance(sha, str):
        return None
    return sha.strip() or None


def _is_pending_state(state: str, status: str) -> bool:
    return status in _CHECK_PENDING_STATES or state in _CHECK_PENDING_STATES


def _classify_check_run_item(item: _JsonObject) -> str:
    state = _str_field(item, "conclusion", "state")
    status = _str_field(item, "status")
    if state in _CHECK_FAIL_STATES:
        return "fail"
    if _is_pending_state(state, status):
        return "pending"
    return "pass" if state else "unknown"


def pending_check_run_names(check_runs: list[_JsonObject]) -> tuple[str, ...]:
    return tuple(
        str(item.get("name", ""))
        for item in check_runs
        if _classify_check_run_item(item) == "pending"
    )


def _check_run_result_for_name(check_runs: list[_JsonObject], check_name: str) -> str:
    for item in check_runs:
        if str(item.get("name", "")) == check_name:
            return _classify_check_run_item(item)
    return "unknown"


def _is_known_flake_quarantined(check_name: str, diff_files: frozenset[str] | None) -> bool:
    owned = _KNOWN_FLAKE_REGISTRY.get(check_name)
    if not owned:
        return False
    if diff_files is None:
        return False
    return not any(fnmatch.fnmatch(f, pattern) for f in diff_files for pattern in owned)


def _classify_one_check(
    check_name: str,
    base_check_runs: list[_JsonObject] | None,
    diff_files: frozenset[str] | None,
) -> QuarantineEvidence:
    base_result = "unknown"
    if base_check_runs is not None:
        base_result = _check_run_result_for_name(base_check_runs, check_name)
        if base_result == "fail":
            return QuarantineEvidence(
                check_name=check_name, base_result="fail", head_result="fail",
                reason="pre_existing",
            )
    if _is_known_flake_quarantined(check_name, diff_files):
        return QuarantineEvidence(
            check_name=check_name, base_result=base_result, head_result="fail",
            reason="known_flake",
        )
    return QuarantineEvidence(
        check_name=check_name, base_result=base_result, head_result="fail",
        reason="blocking",
    )


def _audit_quarantine_query_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "quarantine_check_runs_query_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def _gather_quarantine_inputs(
    pr_number: int, *, forge_target: ForgeTarget,
) -> tuple[list[_JsonObject] | None, frozenset[str] | None]:
    base_check_runs = None
    base_sha = _fetch_pr_base_sha(pr_number, forge_target=forge_target)
    if base_sha is not None:
        base_check_runs = _pr_branch._iter_check_runs(  # pyright: ignore[reportPrivateUsage]
            forge_target, base_sha, pr_number,
            on_fail=_audit_quarantine_query_failure,
        )
    diff_files: frozenset[str] | None = None
    if _KNOWN_FLAKE_REGISTRY:
        diff_files = active_forge().changed_files(pr_number, forge_target=forge_target)
    return base_check_runs, diff_files


def _audit_quarantine(
    task_id: str, pr_number: int, evidence: list[QuarantineEvidence]
) -> None:
    for ev in evidence:
        append_audit({
            "task_id": task_id,
            "action": "flake_quarantine_decision",
            "pr_number": pr_number,
            "check_name": ev.check_name,
            "base_result": ev.base_result,
            "head_result": ev.head_result,
            "reason": ev.reason,
            "quarantined": ev.reason != "blocking",
        })


def classify_flake_quarantine(
    pr_number: int,
    failing_checks: tuple[str, ...],
    pending_checks: tuple[str, ...],
    *, forge_target: ForgeTarget,
) -> tuple[tuple[str, ...], list[QuarantineEvidence]]:
    if pending_checks:
        return (), []
    if not failing_checks:
        return (), []
    base_check_runs, diff_files = _gather_quarantine_inputs(pr_number, forge_target=forge_target)
    quarantined: list[str] = []
    evidence: list[QuarantineEvidence] = []
    for check_name in failing_checks:
        ev = _classify_one_check(check_name, base_check_runs, diff_files)
        evidence.append(ev)
        if ev.reason != "blocking":
            quarantined.append(check_name)
    return tuple(quarantined), evidence


def check_pr_merge_readiness(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> ReadinessResult:
    readiness = _resolve_readiness(
        pr_number, task_id, "readiness", _parse_readiness_payload, forge_target=forge_target
    )
    if isinstance(readiness, ReadinessUnavailable):
        return readiness
    if not readiness.failing_checks:
        return readiness
    quarantined, evidence = classify_flake_quarantine(
        pr_number, readiness.failing_checks, readiness.pending_checks, forge_target=forge_target
    )
    _audit_quarantine(task_id, pr_number, evidence)
    if not quarantined:
        return readiness
    return cast(PrReadiness, replace(readiness, quarantined_checks=quarantined))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def readiness_or_unavailable(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> ReadinessResult:
    try:
        return check_pr_merge_readiness(pr_number, task_id, forge_target=forge_target)
    except (_forge_errors.ForgeRateLimited, _forge_errors.ForgeTransientError) as exc:
        readiness = _transient_readiness(f"forge unavailable: {exc}")
        _audit_readiness(task_id, pr_number, readiness)
        return readiness


def check_pr_verifier_blockers(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> ReadinessResult:
    readiness = _resolve_readiness(
        pr_number, task_id, "verifier blockers", _parse_verifier_readiness_payload,
        forge_target=forge_target, fields=_VERIFIER_READINESS_FIELDS,
    )
    if isinstance(readiness, ReadinessUnavailable):
        return readiness
    if not readiness.failing_checks:
        return readiness
    quarantined, evidence = classify_flake_quarantine(
        pr_number, readiness.failing_checks, readiness.pending_checks, forge_target=forge_target
    )
    _audit_quarantine(task_id, pr_number, evidence)
    if not quarantined:
        return readiness
    quarantined_set = frozenset(quarantined)
    effective = tuple(c for c in readiness.failing_checks if c not in quarantined_set)
    new_reasons = _build_verifier_blocker_reasons(readiness.merge_state_status, effective)
    replaced = replace(
        readiness, quarantined_checks=quarantined, failed_reasons=new_reasons, ok=not new_reasons
    )
    return cast(PrReadiness, replaced)  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


_ACTIONS_APP_SLUG = "github-actions"

AdvisoryLane = Literal["verification", "merge_gate", "pr_merge"]


def _check_app_slug(run: _JsonObject) -> str:
    app = run.get("app")
    if not isinstance(app, dict):
        return ""
    slug = cast(_JsonObject, app).get("slug")
    return slug if isinstance(slug, str) else ""


def _is_third_party_run(run: _JsonObject) -> bool:
    slug = _check_app_slug(run)
    return bool(slug) and slug != _ACTIONS_APP_SLUG


def _named_runs_are_third_party(name: str, check_runs: list[_JsonObject]) -> bool:
    named = [run for run in check_runs if str(run.get("name", "")) == name]
    return bool(named) and all(_is_third_party_run(run) for run in named)


def _may_be_advisory(name: str, readiness: PrReadiness, forge_target: ForgeTarget) -> bool:
    return (
        name not in readiness.failing_status_contexts
        and not is_authoritative_check(forge_target, name)
    )


def _possible_advisory(readiness: PrReadiness, forge_target: ForgeTarget) -> frozenset[str]:
    if not readiness.head_sha:
        return frozenset()
    return frozenset(
        name for name in readiness.failing_checks
        if _may_be_advisory(name, readiness, forge_target)
    )


def _has_blocking_red(readiness: PrReadiness, advisory: frozenset[str]) -> bool:
    exempt = advisory | frozenset(readiness.quarantined_checks)
    return any(name not in exempt for name in readiness.failing_checks)


def _advisory_query_failure_auditor(task_id: str) -> Callable[[int, str], None]:
    def _on_fail(pr_number: int, reason: str) -> None:
        append_audit({
            "task_id": task_id,
            "action": "advisory_check_runs_query_failed",
            "pr_number": pr_number,
            "reason": reason,
        })

    return _on_fail


def _third_party_named(
    candidates: frozenset[str], check_runs: list[_JsonObject],
) -> frozenset[str]:
    return frozenset(
        name for name in candidates if _named_runs_are_third_party(name, check_runs)
    )


def _ignored_if_nothing_blocks(
    readiness: PrReadiness, advisory: frozenset[str],
) -> tuple[str, ...]:
    if _has_blocking_red(readiness, advisory):
        return ()
    return tuple(name for name in readiness.failing_checks if name in advisory)


def _read_check_runs(
    readiness: PrReadiness, pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> list[_JsonObject] | None:
    on_fail = _advisory_query_failure_auditor(task_id)
    check_runs = _pr_branch._iter_check_runs(  # pyright: ignore[reportPrivateUsage]
        forge_target, readiness.head_sha, pr_number, on_fail=on_fail, strict=True,
    )
    if check_runs is None:
        return None
    if all(isinstance(run, dict) for run in cast("list[object]", check_runs)):
        return check_runs
    on_fail(pr_number, "check_runs_element_malformed")
    return None


def third_party_advisory_checks(
    readiness: PrReadiness, pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> frozenset[str]:
    possible = _possible_advisory(readiness, forge_target)
    if not possible:
        return frozenset()
    check_runs = _read_check_runs(readiness, pr_number, task_id, forge_target)
    if check_runs is None:
        return frozenset()
    return _third_party_named(possible, check_runs)


def advisory_failing_checks(
    readiness: PrReadiness, pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> tuple[str, ...]:
    possible = _possible_advisory(readiness, forge_target)
    if not possible or _has_blocking_red(readiness, possible):
        return ()
    return _ignored_if_nothing_blocks(
        readiness,
        third_party_advisory_checks(readiness, pr_number, task_id, forge_target=forge_target),
    )


def audit_advisory_ignored(
    lane: AdvisoryLane, task_id: str, pr_number: int,
    readiness: PrReadiness, ignored: tuple[str, ...],
) -> None:
    append_audit({
        "task_id": task_id,
        "action": "advisory_check_failures_ignored",
        "lane": lane,
        "pr_number": pr_number,
        "head_sha": readiness.head_sha,
        "ignored_checks": list(ignored),
    })


def _settled_merge_state(
    merge_state_status: str, failing_checks: tuple[str, ...], pending_checks: tuple[str, ...],
) -> str:
    if merge_state_status != "UNSTABLE" or failing_checks or pending_checks:
        return merge_state_status
    return "CLEAN"


def _merge_lane_reasons(readiness: PrReadiness) -> tuple[str, ...]:
    return _build_failed_reasons(
        readiness.merge_state_status, readiness.review_decision,
        readiness.failing_checks, readiness.pending_checks, False,
    )


def _verifier_lane_reasons(readiness: PrReadiness) -> tuple[str, ...]:
    quarantined = frozenset(readiness.quarantined_checks)
    effective = tuple(c for c in readiness.failing_checks if c not in quarantined)
    return _build_verifier_blocker_reasons(readiness.merge_state_status, effective)


def _view_without_advisory(
    readiness: PrReadiness, ignored: tuple[str, ...],
    lane_reasons: Callable[[PrReadiness], tuple[str, ...]],
) -> PrReadiness:
    dropped = frozenset(ignored)
    failing = tuple(c for c in readiness.failing_checks if c not in dropped)
    stripped = cast(PrReadiness, replace(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        readiness,
        failing_checks=failing,
        quarantined_checks=tuple(c for c in readiness.quarantined_checks if c not in dropped),
        merge_state_status=_settled_merge_state(
            readiness.merge_state_status, failing, readiness.pending_checks,
        ),
    ))
    reasons = lane_reasons(stripped)
    return cast(PrReadiness, replace(stripped, failed_reasons=reasons, ok=not reasons))  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def _lane_view(
    readiness: PrReadiness, pr_number: int, task_id: str, lane: AdvisoryLane,
    lane_reasons: Callable[[PrReadiness], tuple[str, ...]],
    *, forge_target: ForgeTarget,
) -> PrReadiness:
    ignored = advisory_failing_checks(readiness, pr_number, task_id, forge_target=forge_target)
    if not ignored:
        return readiness
    audit_advisory_ignored(lane, task_id, pr_number, readiness, ignored)
    return _view_without_advisory(readiness, ignored, lane_reasons)


def merge_lane_without_advisory(
    readiness: PrReadiness, pr_number: int, task_id: str,
    *, lane: AdvisoryLane, forge_target: ForgeTarget,
) -> PrReadiness:
    return _lane_view(
        readiness, pr_number, task_id, lane, _merge_lane_reasons, forge_target=forge_target,
    )


def verifier_lane_without_advisory(
    readiness: ReadinessResult, pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> ReadinessResult:
    if isinstance(readiness, ReadinessUnavailable):
        return readiness
    return _lane_view(
        readiness, pr_number, task_id, "verification", _verifier_lane_reasons,
        forge_target=forge_target,
    )
