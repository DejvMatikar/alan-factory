from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Literal

import alan.teams._inline_findings as _inline_findings_mod
import alan.teams._verdict as _verdict
from alan.block_correction import BlockCorrectionError
from alan.clause_withdrawal import (
    CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID,
    CLAUSE_AUDIT_PR_STATE_QUESTION_ID,
    CLAUSE_STALL_QUESTION_PREFIX,
    OPTION_CLAUSE_OVERRIDE_WORDING,
    OPTION_OPERATOR_ONLY_WORDING_CURRENT,
    OPTION_OPERATOR_ONLY_WORDING_LEGACY,
    OPTION_WAIVE_WORDING,
    OPTION_WITHDRAW_WORDING_CURRENT,
)
from alan.engine_config import ForgeTarget
from alan.handover import (
    ClauseOutcome,
    Handover,
    OpenQuestion,
    OptionEffect,
    find_recorded_answer,
)
from alan.teams import _pr_branch, _pr_post
from alan.teams._base import (
    EmitFn,
    TeamEvent,
    build_forward_handover,
)
from alan.teams._block_correction_audit import record_unreachable_attempt
from alan.teams._clause_actionability import ClauseActionability, DemotedClause, cause_text
from alan.teams._clause_stall import ClauseStall
from alan.teams._env import float_env
from alan.teams._inline_findings import (
    parse_inline_findings,
    strip_confidence_footer,
    strip_inline_findings_block,
)
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # pyright: ignore[reportPrivateUsage]
    append_audit,
)
from alan.teams._verification_capability import capability_boundary_question_text
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_JUDGE_PARSE_FAIL_QUESTION = (
    "Judge node failed to emit a parseable VERDICT line on both the initial "
    "run and a targeted retry. Verification could not post a review. "
    "Re-dispatch verification to retry, or review the PR manually."
)
_JUDGE_PARSE_FAIL_OPTIONS: tuple[str, ...] = (
    "Re-dispatch verification to retry the judge node.",
    "Review the PR manually on GitHub and post the verdict by hand.",
)
_JUDGE_PARSE_FAIL_RECOMMENDED = _JUDGE_PARSE_FAIL_OPTIONS[0]
_CLAUSE_PR_STATE_QUESTION = (
    "Acceptance clause audit found a FAIL whose only evidence is PR state "
    "(draft/head/base) — a fix worker cannot mutate PR state."
)
_CLAUSE_PR_STATE_OPTIONS: tuple[str, ...] = (
    "Update the PR's state on GitHub, then re-dispatch verification.",
    OPTION_CLAUSE_OVERRIDE_WORDING,
)
_CLAUSE_OVERRIDE_OPTION = OPTION_CLAUSE_OVERRIDE_WORDING
_CAPABILITY_BOUNDARY_OPTION_VERIFICATION = (
    "Resolve this outside the fix-worker loop, then re-dispatch verification."
)
_CAPABILITY_BOUNDARY_OPTION_WAIVE = "Waive this finding and proceed to merge."
_CAPABILITY_BOUNDARY_OPTIONS: tuple[str, ...] = (
    _CAPABILITY_BOUNDARY_OPTION_VERIFICATION,
    _CAPABILITY_BOUNDARY_OPTION_WAIVE,
)
_CAPABILITY_BOUNDARY_RECOMMENDED = _CAPABILITY_BOUNDARY_OPTION_VERIFICATION
_CAPABILITY_BOUNDARY_OPTION_RESOLVERS: Mapping[str, str] = MappingProxyType({
    _CAPABILITY_BOUNDARY_OPTION_VERIFICATION: "verification",
    _CAPABILITY_BOUNDARY_OPTION_WAIVE: "pr_merge",
})
_CAPABILITY_BOUNDARY_OPTION_EFFECTS: Mapping[str, OptionEffect] = MappingProxyType({
    _CAPABILITY_BOUNDARY_OPTION_VERIFICATION: "pending_external",
    _CAPABILITY_BOUNDARY_OPTION_WAIVE: "closes",
})
_CLAUSE_STALL_OPTION_REWRITE = OPTION_WITHDRAW_WORDING_CURRENT
_CLAUSE_STALL_OPTION_WAIVE = OPTION_WAIVE_WORDING
_CLAUSE_STALL_OPTIONS: tuple[str, ...] = (
    _CLAUSE_STALL_OPTION_REWRITE,
    _CLAUSE_STALL_OPTION_WAIVE,
)
_CLAUSE_STALL_RECOMMENDED = _CLAUSE_STALL_OPTION_REWRITE
_CLAUSE_STALL_OPTION_RESOLVERS: Mapping[str, str] = MappingProxyType({
    _CLAUSE_STALL_OPTION_REWRITE: "verification",
    _CLAUSE_STALL_OPTION_WAIVE: "pr_merge",
})
_CLAUSE_STALL_OPTION_EFFECTS: Mapping[str, OptionEffect] = MappingProxyType({
    _CLAUSE_STALL_OPTION_REWRITE: "pending_external",
    _CLAUSE_STALL_OPTION_WAIVE: "closes",
})
_CLASS_REMEDIATION_OPTION: dict[ClauseActionability, str] = {
    ClauseActionability.PR_STATE: "Update the PR's state on GitHub, then re-dispatch verification.",
    ClauseActionability.HOST_ENV: (
        "Clean or re-dispatch onto a fresh worker checkout, then re-dispatch verification."
    ),
    ClauseActionability.CHECK_CONTRADICTED: (
        "Investigate why the local run diverged from the green required check, "
        "then re-dispatch verification."
    ),
    ClauseActionability.OPERATOR_ONLY: OPTION_OPERATOR_ONLY_WORDING_CURRENT,
}


def _clause_escalation_options(
    unactionable_rows: tuple[DemotedClause, ...],
) -> tuple[tuple[str, ...], str]:
    classes = sorted({d.classification for d in unactionable_rows}, key=lambda c: c.value)
    specific = [_CLASS_REMEDIATION_OPTION[c] for c in classes]
    options = (*specific, _CLAUSE_OVERRIDE_OPTION)
    return options, options[0]


def _validate_inline_findings_block(text: str) -> list[_pr_post.InlineFinding]:
    result = parse_inline_findings(text)
    if not result:
        raise BlockCorrectionError(
            "INLINE_FINDINGS_YAML block present but empty or malformed"
        )
    return result


def _maybe_audit_inline_findings_attempt(
    task_id: str, verdict: _verdict.Verdict, body: str, findings: list[_pr_post.InlineFinding]
) -> None:
    if verdict != "REQUEST_CHANGES":
        return
    has_block = (
        _inline_findings_mod._INLINE_FINDINGS_RE.search(body) is not None  # pyright: ignore[reportPrivateUsage]
    )
    if not findings and not has_block:
        return
    record_unreachable_attempt(
        site="INLINE_FINDINGS_YAML",
        role="verification",
        validate=_validate_inline_findings_block,
        text=body,
        task_id=task_id,
    )


def _apply_pr_review(
    pr_number: int, verdict: _verdict.Verdict, body: str, task_id: str,
    event: _pr_post.ReviewEvent,
    inv_id: str,
    *, forge_target: ForgeTarget, as_verifier: bool,
) -> _pr_post.ReviewPostOutcome:
    _pr_post.dismiss_stale_verifier_reviews(pr_number, task_id, forge_target=forge_target)
    human_body = strip_confidence_footer(strip_inline_findings_block(body))
    findings = parse_inline_findings(body) if verdict == "REQUEST_CHANGES" else []
    _maybe_audit_inline_findings_attempt(task_id, verdict, body, findings)
    if findings:
        outcome = _apply_inline_pr_review(
            pr_number, verdict, human_body, task_id, findings, event, inv_id,
            as_verifier=as_verifier, forge_target=forge_target,
        )
        if not outcome.ok:
            append_audit({
                "task_id": task_id, "action": "review_inline_fallback",
                "pr_number": pr_number, "verdict": verdict,
                "event": event, "inv_id": inv_id,
            })
            return _apply_legacy_pr_review(
                pr_number, verdict, human_body, task_id, event, inv_id,
                as_verifier=as_verifier, forge_target=forge_target,
            )
        return outcome
    return _apply_legacy_pr_review(
        pr_number, verdict, human_body, task_id, event, inv_id,
        as_verifier=as_verifier, forge_target=forge_target,
    )


def _add_failure_detail(entry: dict[str, object], outcome: _pr_post.ReviewPostOutcome) -> None:
    if outcome.error is not None:
        entry["error"] = outcome.error
    else:
        entry["returncode"] = outcome.returncode
        entry["stderr"] = (outcome.stderr or "")[:500]


def _apply_legacy_pr_review(
    pr_number: int, verdict: _verdict.Verdict, body: str, task_id: str,
    event: _pr_post.ReviewEvent,
    inv_id: str,
    *, forge_target: ForgeTarget, as_verifier: bool,
) -> _pr_post.ReviewPostOutcome:
    outcome = _pr_post.post_pr_review_with_inline_comments(
        pr_number, body, event, (), as_verifier=as_verifier, forge_target=forge_target,
    )
    entry: dict[str, object] = {
        "task_id": task_id,
        "action": "review_ok" if outcome.ok else "review_failed",
        "pr_number": pr_number,
        "verdict": verdict,
        "event": event,
        "gh_user_override": as_verifier,
    }
    if outcome.ok:
        entry["inv_id"] = inv_id
    else:
        logger.warning(
            "review post failed (rc=%s): %s", outcome.returncode, outcome.stderr,
        )
        _add_failure_detail(entry, outcome)
    append_audit(entry)
    return outcome


def _apply_inline_pr_review(
    pr_number: int,
    verdict: _verdict.Verdict,
    body: str,
    task_id: str,
    findings: list[_pr_post.InlineFinding],
    event: _pr_post.ReviewEvent,
    inv_id: str,
    *, forge_target: ForgeTarget, as_verifier: bool,
) -> _pr_post.ReviewPostOutcome:
    outcome = _pr_post.post_pr_review_with_inline_comments(
        pr_number, body, event, findings,
        as_verifier=as_verifier, forge_target=forge_target,
    )
    entry: dict[str, object] = {
        "task_id": task_id,
        "action": "review_ok_inline" if outcome.ok else "review_failed_inline",
        "pr_number": pr_number,
        "verdict": verdict,
        "event": event,
        "inline_count": len(findings),
        "gh_user_override": as_verifier,
        "inv_id": inv_id,
    }
    if not outcome.ok:
        _add_failure_detail(entry, outcome)
    append_audit(entry)
    return outcome


ReviewPostFailureClass = _pr_branch.CredentialFailureClass

_REVIEW_POST_RETRY_MAX_ENV = "ALAN_REVIEW_POST_RETRY_MAX"
_REVIEW_POST_RETRY_INTERVAL_ENV = "ALAN_REVIEW_POST_RETRY_INTERVAL_S"
_DEFAULT_REVIEW_POST_RETRY_MAX = 3
_DEFAULT_REVIEW_POST_RETRY_INTERVAL_S = 5.0


def _review_post_retry_max() -> int:
    raw = os.environ.get(_REVIEW_POST_RETRY_MAX_ENV, "").strip()
    if not raw:
        return _DEFAULT_REVIEW_POST_RETRY_MAX
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_REVIEW_POST_RETRY_MAX
    return value if value >= 0 else _DEFAULT_REVIEW_POST_RETRY_MAX


def _review_post_retry_interval_s() -> float:
    return float_env(_REVIEW_POST_RETRY_INTERVAL_ENV, _DEFAULT_REVIEW_POST_RETRY_INTERVAL_S)


def _post_review_with_retries(  # pyright: ignore[reportUnusedFunction]
    pr_number: int, verdict: _verdict.Verdict, body: str, task_id: str,
    event: _pr_post.ReviewEvent, inv_id: str, *, forge_target: ForgeTarget,
) -> tuple[_pr_post.ReviewPostOutcome, ReviewPostFailureClass | None, int]:
    as_verifier = _pr_post.verifier_identity_available(forge_target)
    max_retries = _review_post_retry_max()
    interval = _review_post_retry_interval_s()
    outcome = _apply_pr_review(
        pr_number, verdict, body, task_id, event, inv_id,
        forge_target=forge_target, as_verifier=as_verifier,
    )
    attempts = 1
    while not outcome.ok:
        failure_class = _pr_branch.classify_credential_failure(
            token_present=as_verifier, returncode=outcome.returncode, stderr=outcome.stderr,
        )
        if failure_class != "transient" or attempts > max_retries:
            return outcome, failure_class, attempts
        append_audit({
            "task_id": task_id, "action": "review_post_retry",
            "pr_number": pr_number, "attempt": attempts, "inv_id": inv_id,
        })
        time.sleep(interval)
        outcome = _apply_pr_review(
            pr_number, verdict, body, task_id, event, inv_id,
            forge_target=forge_target, as_verifier=as_verifier,
        )
        attempts += 1
    return outcome, None, attempts


@dataclass(frozen=True)
class ReviewPostResult:
    status: Literal["posted", "skipped", "failed"]
    failure_class: ReviewPostFailureClass | None = None
    attempts: int = 0


def _review_post_failed_body(
    failure_class: ReviewPostFailureClass, pr_number: int | None, attempts: int,
    *, forge_target: ForgeTarget,
) -> str:
    pr_tag = f"PR #{pr_number}" if pr_number is not None else "this PR"
    preserved = "The judge verdict is preserved in the workspace review artifact."
    if failure_class == "missing_token":
        return (
            f"Verifier review POST failed for {pr_tag} — missing credential. "
            "ALAN_VERIFIER_GH_TOKEN was not set for this worker, so `gh` had "
            f"no verifier identity to post as. {preserved} "
            "Set ALAN_VERIFIER_GH_TOKEN and re-dispatch verification, or "
            "review the PR manually."
        )
    if failure_class == "expired_token":
        return (
            f"Verifier review POST failed for {pr_tag} — expired credential. "
            "`gh` rejected the request as unauthenticated/unauthorized. "
            f"{preserved} "
            + _pr_branch.credential_recovery_recipe("reviewer", forge_target)
        )
    return (
        f"Verifier review POST failed for {pr_tag} — transient failure. "
        f"Attempted {attempts} time(s) (network hiccup or a GitHub-side "
        f"blip) without success. {preserved} Re-dispatch verification to "
        "retry, or review the PR manually."
    )


def _emit_review_post_failed(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
    judge_output: str,
    *,
    failure_class: ReviewPostFailureClass = "transient",
    attempts: int = 1,
    forge_target: ForgeTarget,
) -> Handover:
    pr_number = prior.pr_number if prior is not None else None
    task_id = workspace.task_id
    body = _review_post_failed_body(failure_class, pr_number, attempts, forge_target=forge_target)
    append_audit({
        "task_id": task_id, "action": "review_post_failed",
        "pr_number": pr_number, "inv_id": inv_id,
        "failure_class": failure_class, "attempts": attempts,
        "reason": body,
    })
    pr_str = str(pr_number) if pr_number is not None else ""
    emit(TeamEvent("review_post_failed", {
        "pr_number": pr_str, "task_id": task_id, "failure_class": failure_class,
    }))
    review_artifact = _write_review_artifact(workspace, inv_id, judge_output)
    handover = build_forward_handover(
        workspace, from_team="verification", to_team_suggestions=[],
        summary=body, prior=prior, invocation_id=inv_id,
    )
    updates: dict[str, object] = {
        "pr_number": pr_number, "decisions": [],
        "artifacts_produced": [review_artifact],
    }
    if failure_class == "expired_token":
        updates["credential_failure"] = failure_class
    return handover.model_copy(update=updates)


def _emit_verification_escalation(
    workspace: Workspace, prior: Handover | None, emit: EmitFn, inv_id: str, judge_output: str,
    *, question_id: str, question: str, options: tuple[str, ...], recommended: str, action: str,
    option_resolvers: Mapping[str, str] = MappingProxyType({}),
    option_effects: Mapping[str, OptionEffect] = MappingProxyType({}),
) -> Handover:
    pr_number = prior.pr_number if prior is not None else None
    task_id = workspace.task_id
    append_audit({"task_id": task_id, "action": action, "pr_number": pr_number, "inv_id": inv_id})
    pr_str = str(pr_number) if pr_number is not None else ""
    emit(TeamEvent(action, {"pr_number": pr_str, "task_id": task_id}))
    review_artifact = _write_review_artifact(workspace, inv_id, judge_output)
    oq = OpenQuestion(
        id=question_id, question=question, proposed_answer_space=list(options),
        recommended=recommended, question_class="verification_escalation",
        resolver_role="human", blocking=True,
        option_resolvers=dict(option_resolvers),
        option_effects=dict(option_effects),
    )
    handover = build_forward_handover(
        workspace, from_team="verification", to_team_suggestions=[],
        summary=question, prior=prior, invocation_id=inv_id,
    )
    return handover.model_copy(update={
        "pr_number": pr_number, "decisions": [],
        "artifacts_produced": [review_artifact], "open_questions": [oq],
    })


def _emit_judge_parse_failed(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, prior: Handover | None, emit: EmitFn, inv_id: str, judge_output: str,
) -> Handover:
    return _emit_verification_escalation(
        workspace, prior, emit, inv_id, judge_output,
        question_id="verif-judge-parse", question=_JUDGE_PARSE_FAIL_QUESTION,
        options=_JUDGE_PARSE_FAIL_OPTIONS, recommended=_JUDGE_PARSE_FAIL_RECOMMENDED,
        action="judge_parse_failed",
    )


def _emit_capability_boundary_escalation(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, prior: Handover | None, emit: EmitFn, inv_id: str, judge_output: str,
    *, reason: str,
) -> Handover:
    question = capability_boundary_question_text(reason)
    return _emit_verification_escalation(
        workspace, prior, emit, inv_id, judge_output,
        question_id=f"verif-capability-boundary-{inv_id[:8]}", question=question,
        options=_CAPABILITY_BOUNDARY_OPTIONS, recommended=_CAPABILITY_BOUNDARY_RECOMMENDED,
        action="capability_boundary_escalation",
        option_resolvers=_CAPABILITY_BOUNDARY_OPTION_RESOLVERS,
        option_effects=_CAPABILITY_BOUNDARY_OPTION_EFFECTS,
    )


_CLAUSE_STALL_QUESTION_TEMPLATE = (
    "Acceptance clause(s) {repeated_ids} failed as actionable on two "
    "consecutive verification runs on this handover chain, regardless of "
    "what ran in between.\n\n"
    "Your answer acts on every clause listed below — the currently "
    "actionable set {clause_ids} — not only the repeating one(s).\n\n"
    "{clause_blocks}"
    "Judge's reasoning: {judge_rationale}"
)
_CLAUSE_STALL_CLAUSE_BLOCK_TEMPLATE = "Clause {req_id}: {text}\n\nEvidence: {evidence}\n\n"
_CLAUSE_STALL_ID_HASH_LENGTH = 16


def _clause_stall_question(stall: ClauseStall) -> str:
    clause_blocks = "".join(
        _CLAUSE_STALL_CLAUSE_BLOCK_TEMPLATE.format(
            req_id=clause.req_id, text=clause.text, evidence=clause.evidence,
        )
        for clause in stall.clauses
    )
    return _CLAUSE_STALL_QUESTION_TEMPLATE.format(
        clause_ids=", ".join(stall.clause_ids),
        repeated_ids=", ".join(stall.repeated_ids),
        clause_blocks=clause_blocks, judge_rationale=stall.judge_rationale,
    )


def _clause_stall_question_id(clause_ids: tuple[str, ...]) -> str:
    if len(clause_ids) == 1:
        return f"{CLAUSE_STALL_QUESTION_PREFIX}{clause_ids[0]}"
    joined = ",".join(sorted(clause_ids))
    digest = sha256(joined.encode()).hexdigest()[:_CLAUSE_STALL_ID_HASH_LENGTH]
    return f"{CLAUSE_STALL_QUESTION_PREFIX}{digest}"


def _stall_question_id_for(
    workspace: Workspace, prior: Handover | None, stall: ClauseStall
) -> str:
    overlap_id = _clause_stall_question_id(stall.repeated_ids)
    if prior is None:
        return overlap_id
    if find_recorded_answer(workspace, prior.handover_chain_id, overlap_id) is None:
        return overlap_id
    clause_id = _clause_stall_question_id(stall.clause_ids)
    if clause_id == overlap_id:
        return clause_id
    if find_recorded_answer(workspace, prior.handover_chain_id, clause_id) is None:
        return clause_id
    digest = sha256(
        f"{clause_id}{prior.from_invocation_id}".encode()
    ).hexdigest()[:_CLAUSE_STALL_ID_HASH_LENGTH]
    return f"{CLAUSE_STALL_QUESTION_PREFIX}{digest}-{prior.from_invocation_id[:8]}"


def _emit_clause_stall_escalation(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, prior: Handover | None, emit: EmitFn, inv_id: str, judge_output: str,
    *, stall: ClauseStall, clause_outcome: ClauseOutcome | None = None,
) -> Handover:
    question = _clause_stall_question(stall)
    handover = _emit_verification_escalation(
        workspace, prior, emit, inv_id, judge_output,
        question_id=_stall_question_id_for(workspace, prior, stall), question=question,
        options=_CLAUSE_STALL_OPTIONS, recommended=_CLAUSE_STALL_RECOMMENDED,
        action="clause_stall_escalation",
        option_resolvers=_CLAUSE_STALL_OPTION_RESOLVERS,
        option_effects=_CLAUSE_STALL_OPTION_EFFECTS,
    )
    if clause_outcome is not None:
        return handover.model_copy(update={"clause_outcome": clause_outcome})
    return handover


_OPERATOR_ONLY_RESOLVABLE_WORDINGS: frozenset[str] = frozenset({
    OPTION_OPERATOR_ONLY_WORDING_LEGACY,
    OPTION_OPERATOR_ONLY_WORDING_CURRENT,
    OPTION_CLAUSE_OVERRIDE_WORDING,
})


def _operator_only_option_maps(
    question_id: str, options: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, OptionEffect]]:
    if question_id != CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID:
        return {}, {}
    resolvable = tuple(o for o in options if o in _OPERATOR_ONLY_RESOLVABLE_WORDINGS)
    resolvers = dict.fromkeys(resolvable, "pr_merge")
    effects: dict[str, OptionEffect] = dict.fromkeys(resolvable, "closes")
    return resolvers, effects


def _emit_clause_pr_state_escalation(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, prior: Handover | None, emit: EmitFn, inv_id: str, judge_output: str,
    *, unactionable_rows: tuple[DemotedClause, ...] = (),
    clause_outcome: ClauseOutcome | None = None,
) -> Handover:
    if unactionable_rows:
        question = (
            f"Acceptance clause audit found FAIL(s) not attributable to the "
            f"change under review — {cause_text(unactionable_rows)}. "
            "A fix worker cannot address this."
        )
        options, recommended = _clause_escalation_options(unactionable_rows)
        question_id = (
            CLAUSE_AUDIT_OPERATOR_ONLY_QUESTION_ID
            if any(
                d.classification is ClauseActionability.OPERATOR_ONLY
                for d in unactionable_rows
            )
            else CLAUSE_AUDIT_PR_STATE_QUESTION_ID
        )
    else:
        question = _CLAUSE_PR_STATE_QUESTION
        options, recommended = _CLAUSE_PR_STATE_OPTIONS, _CLAUSE_PR_STATE_OPTIONS[0]
        question_id = CLAUSE_AUDIT_PR_STATE_QUESTION_ID
    resolvers, effects = _operator_only_option_maps(question_id, options)
    handover = _emit_verification_escalation(
        workspace, prior, emit, inv_id, judge_output,
        question_id=question_id, question=question,
        options=options, recommended=recommended,
        action="clause_audit_pr_state_escalation",
        option_resolvers=resolvers, option_effects=effects,
    )
    if clause_outcome is not None:
        return handover.model_copy(update={"clause_outcome": clause_outcome})
    return handover
