from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

import alan.project as project_mod
import alan.project_doc as project_doc_mod
import alan.workspace as workspace_mod
from alan import clause_withdrawal
from alan import merge_gate as merge_gate_mod
from alan import park as park_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.dispatch import (
    assert_team_dispatchable,
    dispatch_team_core,
    owning_task_id,
    resolver_team_for_answer,
    team_is_dispatchable,
)
from alan.dispatcher import (
    _BOUNDED_RETRY_REMEDIATION_CLASSES,  # pyright: ignore[reportPrivateUsage]
)
from alan.events import PubSub
from alan.gold_label import record_gold_label
from alan.handover import (
    Handover,
    OpenQuestion,
    OptionEffect,
    find_handover_by_id,
    find_recorded_answer,
    is_answer_recording,
    list_handovers,
    record_answer,
    unanswered_blocking_question_ids,
)
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.routes import _answer_waiver, _deps
from alan.routes._deps import PubSubDep, QueueDep, WorkspacesRootDep, read_workspace_or_404
from alan.teams import (
    MANUALLY_DISPATCHABLE_TEAMS,
    TEAMS,
    _pr_audit_log,
    _pr_forge,
    _pr_state_paths,
    _verification_capability,
)
from alan.teams._spiral_handover import FIX_CYCLE_OVERRIDE_OPTION
from alan.workspace import (
    InterventionAction,
    Requirement,
    Status,
    Workspace,
    record_intervention,
)

logger = logging.getLogger("alan.server")

_NO_PR_TO_GATE_REASON = (
    "resolver is not pr_merge, or the blocking handover carries no pull-request number"
)

answer_router = APIRouter(prefix="/api/workspaces/{workspace_id}/tasks/{task_id}")

_lock = threading.Lock()

_ANSWER_RESPONSES: dict[int | str, dict[str, str]] = {
    400: {"description": "derived resolver team is not manually dispatchable"},
    **_deps.GUARDED,
    404: {"description": "workspace, handover, or question not found"},
    409: {"description": "workspace is archived"},
    422: {
        "description": (
            "chosen_option is not a proposed option, no recommendation to "
            "accept, the resolver team is not admitted at posture, or a "
            "clause-stall withdrawal option has no recoverable clause ids "
            "(including on a repeat POST replaying a recorded withdraw ruling)"
        )
    },
}


class AnswerBody(BaseModel):
    question_id: str = Field(min_length=1, max_length=200)
    handover_id: str | None = Field(default=None, min_length=1, max_length=200)
    chosen_option: str | None = Field(default=None, min_length=1, max_length=2000)
    accept_recommendation: bool = False

    @model_validator(mode="after")
    def _validate_not_both_set(self) -> "AnswerBody":
        if self.accept_recommendation and self.chosen_option is not None:
            raise ValueError("cannot set both chosen_option and accept_recommendation")
        return self


class DispatchInfo(BaseModel):
    job_id: int
    invocation_id: str
    team: str


class AnswerResponse(BaseModel):
    question_id: str
    answer: str
    already_answered: bool
    dispatch: DispatchInfo | None = None
    pending_external: bool = False


class AnswerError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedAnswerTarget:
    team: str
    task_id: str


def _find_blocking_handover(ws: Workspace, handover_id: str | None, question_id: str) -> Handover:
    if handover_id is not None:
        handover = find_handover_by_id(ws, handover_id)
        if handover is None:
            raise AnswerError(f"handover not found: {handover_id!r}", status_code=404)
        return handover
    for handover in reversed(list_handovers(ws)):
        if any(q.id == question_id for q in handover.open_questions):
            return handover
    raise AnswerError(f"no handover carries question {question_id!r}", status_code=404)


def _resolve_chosen_answer(question: OpenQuestion, body: AnswerBody) -> str:
    if body.accept_recommendation:
        if question.recommended is None:
            raise AnswerError("question has no recommended option to accept", status_code=422)
        return question.recommended
    if body.chosen_option is None:
        raise AnswerError(
            "chosen_option or accept_recommendation is required", status_code=422
        )
    if body.chosen_option not in question.proposed_answer_space:
        raise AnswerError(
            f"chosen_option {body.chosen_option!r} is not one of the proposed options",
            status_code=422,
        )
    return body.chosen_option


def _decision_log_entry(question: OpenQuestion, answer: str, blocking: Handover) -> str:
    return (
        f"Answered question [{question.id}] on task {blocking.task_id} "
        f"(from {blocking.from_team}): {question.question!r} -> {answer!r}"
    )


def _append_decision_log(
    ws: Workspace, question: OpenQuestion, answer: str, blocking: Handover
) -> None:
    if ws.meta.project_id is None:
        return
    try:
        project = project_mod.read(ws.meta.project_id)
    except project_mod.ProjectError:
        logger.warning(
            "answer_question: project %r not found; skipping decision log",
            ws.meta.project_id,
        )
        return
    project_doc_mod.append_decision(project, _decision_log_entry(question, answer, blocking))


def _find_question(blocking: Handover, question_id: str) -> OpenQuestion:
    question = next((q for q in blocking.open_questions if q.id == question_id), None)
    if question is None:
        raise AnswerError(f"question not found: {question_id!r}", status_code=404)
    return question


def _is_last_blocking_question(
    blocking: Handover, ws: Workspace, question: OpenQuestion, answer: str
) -> bool:
    provisional_questions = [
        q.model_copy(update={"answer": answer}) if q.id == question.id else q
        for q in blocking.open_questions
    ]
    provisional = blocking.model_copy(update={"open_questions": provisional_questions})
    return not unanswered_blocking_question_ids(provisional, ws)


def _resolve_resolver_team(question: OpenQuestion, from_team: str, answer: str) -> str:
    ruling = clause_withdrawal.resolve_human_ruling(question, answer)
    if ruling is not None:
        return ruling.resolver
    return resolver_team_for_answer(question, from_team, answer)


def _resolved_effect_for(question: OpenQuestion, answer: str) -> OptionEffect | None:
    ruling = clause_withdrawal.resolve_human_ruling(question, answer)
    if ruling is not None:
        return ruling.effect
    return question.option_effects.get(answer)


def _pending_external_for(question: OpenQuestion, answer: str) -> bool:
    return _resolved_effect_for(question, answer) == "pending_external"


def _validate_resolver(
    *, ws: Workspace, task_id: str, question: OpenQuestion, blocking: Handover, answer: str
) -> str:
    resolver = _resolve_resolver_team(question, blocking.from_team, answer)
    if resolver not in MANUALLY_DISPATCHABLE_TEAMS:
        raise AnswerError(
            f"resolver team {resolver!r} is not manually dispatchable", status_code=400
        )
    assert_team_dispatchable(ws, task_id, resolver)
    return resolver


def _maybe_grant_fix_cycle_override(
    *, ws: Workspace, question: OpenQuestion, blocking: Handover, answer: str,
) -> None:
    if not question.id.startswith("fix_cycle_ceiling_"):
        return
    if answer != FIX_CYCLE_OVERRIDE_OPTION:
        return
    if blocking.pr_number is None:
        return
    forge_target = _pr_forge.forge_target_for_ws(ws)
    granted = _pr_state_paths.grant_fix_cycle_override(
        blocking.pr_number, question.id, forge_target
    )
    if not granted:
        return
    _pr_audit_log.append_audit({
        "task_id": ws.task_id,
        "action": "fix_cycle_override_granted",
        "pr_number": blocking.pr_number,
        "question_id": question.id,
    })


_DECISION_DIRECTIVE = "The human has already decided this — do not re-ask or re-litigate it."


def _decision_instructions(question: OpenQuestion, answer: str, blocking: Handover) -> str:
    return (
        f"Human answer to question [{question.id}] (from {blocking.from_team}):\n"
        f"Q: {question.question}\n"
        f"A: {answer}\n\n{_DECISION_DIRECTIVE}"
    )


def _dispatch(
    *,
    ws: Workspace,
    task_id: str,
    resolver: str,
    queue: Queue,
    pubsub: PubSub,
    instructions: str,
    merge_gate: merge_gate_mod.MergeGateDecision,
) -> DispatchInfo:
    outcome = dispatch_team_core(
        workspace=ws,
        task_id=task_id,
        team=TEAMS[resolver],
        queue=queue,
        pubsub=pubsub,
        enqueue_workflow=enqueue_with_pending_workflow,
        instructions=instructions,
        merge_gate=merge_gate,
        keep_sticky=True,
    )
    return DispatchInfo(job_id=outcome.job.id, invocation_id=outcome.inv_id, team=resolver)


def _resolve_answer_resolver(
    *,
    ws: Workspace,
    task_id: str,
    question: OpenQuestion,
    blocking: Handover,
    answer: str,
    pending_external: bool,
) -> ResolvedAnswerTarget | None:
    if pending_external or not _is_last_blocking_question(blocking, ws, question, answer):
        return None
    resolved_task_id = owning_task_id(ws, task_id, blocking=blocking)
    resolver = _validate_resolver(
        ws=ws, task_id=resolved_task_id, question=question, blocking=blocking, answer=answer
    )
    return ResolvedAnswerTarget(team=resolver, task_id=resolved_task_id)


@dataclass(frozen=True)
class _ClaimResult:
    ws: Workspace
    question: OpenQuestion
    blocking: Handover
    answer: str
    already_answered: bool
    pending_external: bool
    resolver: ResolvedAnswerTarget | None


def _clause_ids_unresolved(ws: Workspace, clause_ids: list[str]) -> bool:
    known = {r.id for r in ws.meta.requirements or []}
    return not (set(clause_ids) & known)


def _is_withdraw_replay_candidate(question: OpenQuestion, answer: str) -> bool:
    if clause_withdrawal.is_clause_stall_question(question) and answer in (
        clause_withdrawal.OPTION_WITHDRAW_WORDING_LEGACY,
        clause_withdrawal.OPTION_WITHDRAW_WORDING_CURRENT,
    ):
        return True
    return (
        clause_withdrawal.is_pr_state_escalation(question)
        and answer == clause_withdrawal.OPTION_CLAUSE_OVERRIDE_WORDING
    )


def _skip_for_escalation_resolution(handover: Handover, blocking: Handover) -> bool:
    return handover.handover_id == blocking.handover_id or is_answer_recording(handover)


def _carries_clause_outcome(handover: Handover) -> bool:
    return handover.from_team == "verification" and handover.clause_outcome is not None


def _skip_escalation_candidate(handover: Handover, blocking: Handover) -> bool:
    return (
        handover.handover_chain_id != blocking.handover_chain_id
        or _skip_for_escalation_resolution(handover, blocking)
    )


def _resolve_escalation_for_withdrawal(ws: Workspace, blocking: Handover) -> Handover:
    if blocking.clause_outcome is not None:
        return blocking
    for handover in reversed(list_handovers(ws)):
        if _skip_escalation_candidate(handover, blocking):
            continue
        if handover.handover_type == "revisit_amend":
            return blocking
        if _carries_clause_outcome(handover):
            return handover
    return blocking


def _clause_stall_fully_withdrawn(
    fresh: Workspace, blocking: Handover, question: OpenQuestion
) -> bool:
    escalation = _resolve_escalation_for_withdrawal(fresh, blocking)
    clause_ids = clause_withdrawal.clause_ids_for_withdrawal(fresh, escalation, question=question)
    if not clause_ids:
        return False
    withdrawn = {
        r.id for r in (fresh.meta.requirements or []) if r.withdrawn_by_question_id is not None
    }
    return set(clause_ids).issubset(withdrawn)


def _apply_human_ruling(
    *, ws: Workspace, question: OpenQuestion, answer: str, blocking: Handover,
) -> Workspace:
    ruling = clause_withdrawal.resolve_human_ruling(question, answer)
    if ruling is None:
        return ws
    if ruling.scope == "pr":
        if blocking.pr_number is not None:
            forge_target = _pr_forge.forge_target_for_ws(ws)
            _pr_state_paths.record_capability_boundary_waiver(
                blocking.pr_number,
                _verification_capability.capability_boundary_waiver_key_for_question(question),
                forge_target,
            )
        return ws
    escalation = _resolve_escalation_for_withdrawal(ws, blocking)
    clause_ids = clause_withdrawal.clause_ids_for_withdrawal(ws, escalation, question=question)
    if clause_ids is None or _clause_ids_unresolved(ws, clause_ids):
        if ruling.resolver == "verification":
            raise AnswerError(
                f"cannot recover acceptance clause ids to withdraw for question "
                f"{question.id!r}",
                status_code=422,
            )
        return ws
    return clause_withdrawal.withdraw(ws, clause_ids, question.id)


def _claim_already_answered(
    *,
    ws: Workspace,
    task_id: str,
    question: OpenQuestion,
    blocking: Handover,
    answer: str,
    workspaces_root: Path,
) -> _ClaimResult:
    pending_external = _pending_external_for(question, answer)
    if not _is_withdraw_replay_candidate(question, answer):
        return _ClaimResult(
            ws=ws, question=question, blocking=blocking, answer=answer,
            already_answered=True, pending_external=pending_external, resolver=None,
        )
    fresh = workspace_mod.read(ws.task_id, root=workspaces_root)
    if _clause_stall_fully_withdrawn(fresh, blocking, question):
        return _ClaimResult(
            ws=fresh, question=question, blocking=blocking, answer=answer,
            already_answered=True, pending_external=pending_external, resolver=None,
        )
    resolved = _resolve_answer_resolver(
        ws=fresh, task_id=task_id, question=question, blocking=blocking, answer=answer,
        pending_external=pending_external,
    )
    fresh = _apply_human_ruling(
        ws=fresh, question=question, answer=answer, blocking=blocking
    )
    return _ClaimResult(
        ws=fresh, question=question, blocking=blocking, answer=answer,
        already_answered=True, pending_external=pending_external, resolver=resolved,
    )


def _claim(
    *, ws: Workspace, task_id: str, body: AnswerBody, workspaces_root: Path
) -> _ClaimResult:
    # Serializes read-back + record + resolver-resolution so a second POST for
    # the same question always observes the first POST's recorded answer
    # instead of racing it — mirrors project_amendment.claim_pending_amendment.
    # The merge-gate attempt and the actual dispatch run outside this lock, in
    # the caller's act phase: neither may perform a forge read, a review
    # dismissal, or a rebase while holding the process-global answer lock.
    # The already-answered replay guard (_claim_already_answered) must read
    # committed workspace state taken after this lock is held — via a fresh
    # workspace_mod.read, never the caller's pre-lock snapshot — since that
    # same read doubles as the dispatch gate, not only an idempotence check.
    with _lock:
        blocking = _find_blocking_handover(ws, body.handover_id, body.question_id)
        question = _find_question(blocking, body.question_id)

        existing = question.answer or find_recorded_answer(
            ws, blocking.handover_chain_id, question.id
        )
        if existing is not None:
            return _claim_already_answered(
                ws=ws, task_id=task_id, question=question, blocking=blocking,
                answer=existing, workspaces_root=workspaces_root,
            )

        answer = _resolve_chosen_answer(question, body)
        pending_external = _pending_external_for(question, answer)
        _maybe_grant_fix_cycle_override(
            ws=ws, question=question, blocking=blocking, answer=answer
        )
        resolved = _resolve_answer_resolver(
            ws=ws, task_id=task_id, question=question, blocking=blocking, answer=answer,
            pending_external=pending_external,
        )
        ws = _apply_human_ruling(
            ws=ws, question=question, answer=answer, blocking=blocking
        )

        record_answer(
            ws, blocking, question.id, answer, from_team=blocking.from_team,
            effect_override=_resolved_effect_for(question, answer),
        )
        record_gold_label(ws=ws, blocking=blocking, question=question, answer=answer)
        _append_decision_log(ws, question, answer, blocking)
        ws = record_intervention(ws, InterventionAction.ANSWER)

        return _ClaimResult(
            ws=ws, question=question, blocking=blocking, answer=answer,
            already_answered=False, pending_external=pending_external, resolver=resolved,
        )


@dataclass(frozen=True)
class _UnrecoveredRoute:
    reparked: bool = False
    fix_instructions: str | None = None


def _conflict_fix_instructions(
    ws: Workspace,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
    blockers: list[park_mod.Blocker],
) -> str | None:
    if not merge_gate_mod.conflict_fix_decision(
        blockers, attempt.reconcile, block_class,
        attempt.readiness.head_sha, attempt.readiness.head_ref_name,
    ):
        return None
    if not team_is_dispatchable(ws, "implementation_fix"):
        return None
    return merge_gate_mod.render_remediation_instructions(
        block_class, "; ".join(attempt.readiness.failed_reasons), tuple(blockers)
    )


def _withheld_blocker(
    waiver: _answer_waiver.WaiverApplication,
    attempt: merge_gate_mod.MergeGateAttemptFailed | merge_gate_mod.MergeGateBlocked,
) -> park_mod.Blocker | None:
    if attempt.readiness.review_decision != "CHANGES_REQUESTED":
        return None
    return waiver.blocker


def _early_unrecovered_route(
    block_class: MergeBlockClass, *, waiver: _answer_waiver.WaiverApplication,
) -> _UnrecoveredRoute | None:
    if waiver.cleared and block_class is MergeBlockClass.STALE_REVIEW:
        return _UnrecoveredRoute()
    if merge_gate_mod.routes_to_merge_lane(block_class):
        return _UnrecoveredRoute()
    return None


def _should_repark_unrecovered_block(
    ws: Workspace,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
    blockers: list[park_mod.Blocker],
) -> _UnrecoveredRoute:
    fix_instructions = _conflict_fix_instructions(ws, block_class, attempt, blockers)
    if fix_instructions is not None:
        return _UnrecoveredRoute(fix_instructions=fix_instructions)
    repark = block_class in _BOUNDED_RETRY_REMEDIATION_CLASSES or block_class in (
        MergeBlockClass.SONAR_HOTSPOT, MergeBlockClass.CLOSING_LINKS_MISSING,
    )
    return _UnrecoveredRoute(reparked=repark)


def _repark_unrecovered_merge_block(
    ws: Workspace, bc: str | None, blockers: list[park_mod.Blocker],
) -> None:
    workspace_mod.transition(
        ws, Status.AWAITING_INPUT,
        merge_block_class=bc,
        clear_merge_block_class=True,
        park_reason=park_mod.ParkReason.MERGE_BLOCK,
        blockers=blockers,
    )


def _park_withheld_waiver(
    ws: Workspace,
    blocking: Handover,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
    withheld: park_mod.Blocker,
) -> _UnrecoveredRoute:
    _reason, bc, blockers = merge_gate_mod.pr_merge_remediation_context(
        blocking, attempt.readiness, block_class, attempt.forge_target,
    )
    _repark_unrecovered_merge_block(ws, bc, [*blockers, withheld])
    return _UnrecoveredRoute(reparked=True)


def _remediation_route(
    ws: Workspace,
    blocking: Handover,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
) -> _UnrecoveredRoute:
    _reason, bc, blockers = merge_gate_mod.pr_merge_remediation_context(
        blocking, attempt.readiness, block_class, attempt.forge_target,
    )
    route = _should_repark_unrecovered_block(ws, block_class, attempt, blockers)
    if route.reparked:
        _repark_unrecovered_merge_block(ws, bc, blockers)
    return route


def _route_unrecovered_block(
    *,
    ws: Workspace,
    blocking: Handover,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
    waiver: _answer_waiver.WaiverApplication,
) -> _UnrecoveredRoute:
    withheld = _withheld_blocker(waiver, attempt)
    if withheld is not None:
        return _park_withheld_waiver(ws, blocking, block_class, attempt, withheld)
    early = _early_unrecovered_route(block_class, waiver=waiver)
    if early is not None:
        return early
    return _remediation_route(ws, blocking, block_class, attempt)


def _audit_outcome(route: _UnrecoveredRoute, resolver: str) -> str:
    if route.reparked:
        return "re_park"
    if route.fix_instructions is not None:
        return "implementation_fix"
    return resolver


def _handle_unrecovered_block(
    *,
    ws: Workspace,
    task_id: str,
    blocking: Handover,
    pr_number: int,
    resolver: str,
    block_class: MergeBlockClass,
    attempt: merge_gate_mod.MergeGateBlocked,
    waiver: _answer_waiver.WaiverApplication,
) -> _UnrecoveredRoute:
    route = _route_unrecovered_block(
        ws=ws, blocking=blocking, block_class=block_class, attempt=attempt, waiver=waiver,
    )
    merge_gate_mod.record_merge_gate_audit(
        task_id, pr_number, block_class, False, _audit_outcome(route, resolver),
        merge_gate_mod.reconcile_outcome_str(attempt.reconcile),
    )
    return route


def _park_unclassified_withheld_waiver(
    *,
    ws: Workspace,
    task_id: str,
    pr_number: int,
    attempt: merge_gate_mod.MergeGateAttemptFailed,
    waiver: _answer_waiver.WaiverApplication,
) -> _UnrecoveredRoute:
    withheld = _withheld_blocker(waiver, attempt)
    if withheld is None:
        return _UnrecoveredRoute()
    _repark_unrecovered_merge_block(ws, None, [withheld])
    merge_gate_mod.record_merge_gate_audit(task_id, pr_number, None, False, "re_park")
    return _UnrecoveredRoute(reparked=True)


def _route_for_attempt(
    *,
    ws: Workspace,
    task_id: str,
    blocking: Handover,
    pr_number: int,
    resolver: str,
    attempt: merge_gate_mod.MergeGateAttempt,
    waiver: _answer_waiver.WaiverApplication,
) -> _UnrecoveredRoute:
    match attempt:
        case merge_gate_mod.MergeGateAttemptFailed():
            return _park_unclassified_withheld_waiver(
                ws=ws, task_id=task_id, pr_number=pr_number, attempt=attempt, waiver=waiver,
            )
        case merge_gate_mod.MergeGateBlocked():
            return _handle_unrecovered_block(
                ws=ws, task_id=task_id, blocking=blocking, pr_number=pr_number,
                resolver=resolver, block_class=attempt.block_class, attempt=attempt,
                waiver=waiver,
            )
        case (
            merge_gate_mod.MergeGateNotAttempted() | merge_gate_mod.MergeGateRecovered()
        ):  # pragma: no branch
            return _UnrecoveredRoute()


def _apply_merge_gate(
    *,
    ws: Workspace,
    task_id: str,
    blocking: Handover,
    resolver: str,
    waiver: _answer_waiver.WaiverApplication,
) -> tuple[merge_gate_mod.MergeGateDecision, _UnrecoveredRoute]:
    pr_number = blocking.pr_number
    if resolver != "pr_merge" or pr_number is None:
        return (
            merge_gate_mod.MergeGateDecision.skipped(_NO_PR_TO_GATE_REASON),
            _UnrecoveredRoute(),
        )
    attempt = merge_gate_mod.attempt_recovery(ws, pr_number, recovered_outcome=resolver)
    route = _route_for_attempt(
        ws=ws, task_id=task_id, blocking=blocking, pr_number=pr_number,
        resolver=resolver, attempt=attempt, waiver=waiver,
    )
    return merge_gate_mod.MergeGateDecision.attempted(attempt), route


def _act_replay(*, claim: _ClaimResult, queue: Queue, pubsub: PubSub) -> AnswerResponse:
    if claim.resolver is None:
        return AnswerResponse(
            question_id=claim.question.id, answer=claim.answer, already_answered=True,
            dispatch=None, pending_external=claim.pending_external,
        )
    dispatch = _dispatch(
        ws=claim.ws,
        task_id=claim.resolver.task_id,
        resolver=claim.resolver.team,
        queue=queue,
        pubsub=pubsub,
        instructions=_decision_instructions(claim.question, claim.answer, claim.blocking),
        merge_gate=merge_gate_mod.MergeGateDecision.skipped(_NO_PR_TO_GATE_REASON),
    )
    return AnswerResponse(
        question_id=claim.question.id, answer=claim.answer, already_answered=True,
        dispatch=dispatch, pending_external=claim.pending_external,
    )


def _dispatch_target(
    claim: _ClaimResult, resolver: ResolvedAnswerTarget, route: _UnrecoveredRoute,
) -> tuple[str, str]:
    if route.fix_instructions is not None:
        return "implementation_fix", route.fix_instructions
    return resolver.team, _decision_instructions(claim.question, claim.answer, claim.blocking)


def _act(
    *, claim: _ClaimResult, task_id: str, queue: Queue, pubsub: PubSub,
) -> AnswerResponse:
    if claim.already_answered:
        return _act_replay(claim=claim, queue=queue, pubsub=pubsub)
    resolver = claim.resolver
    if resolver is None:
        return AnswerResponse(
            question_id=claim.question.id, answer=claim.answer, already_answered=False,
            dispatch=None, pending_external=claim.pending_external,
        )
    waiver = _answer_waiver.apply(
        ws=claim.ws, question=claim.question, answer=claim.answer, blocking=claim.blocking,
    )
    decision, route = _apply_merge_gate(
        ws=claim.ws, task_id=task_id, blocking=claim.blocking,
        resolver=resolver.team, waiver=waiver,
    )
    if route.reparked:
        return AnswerResponse(
            question_id=claim.question.id, answer=claim.answer, already_answered=False,
            dispatch=None, pending_external=claim.pending_external,
        )
    team, instructions = _dispatch_target(claim, resolver, route)
    dispatch = _dispatch(
        ws=claim.ws,
        task_id=resolver.task_id,
        resolver=team,
        queue=queue,
        pubsub=pubsub,
        instructions=instructions,
        merge_gate=decision,
    )
    return AnswerResponse(
        question_id=claim.question.id, answer=claim.answer, already_answered=False,
        dispatch=dispatch, pending_external=claim.pending_external,
    )


def _answer_and_maybe_dispatch(
    *,
    ws: Workspace,
    task_id: str,
    body: AnswerBody,
    queue: Queue,
    pubsub: PubSub,
    workspaces_root: Path,
) -> AnswerResponse:
    claim = _claim(ws=ws, task_id=task_id, body=body, workspaces_root=workspaces_root)
    return _act(claim=claim, task_id=task_id, queue=queue, pubsub=pubsub)


@answer_router.post(
    "/answer",
    responses=_ANSWER_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def answer_question(
    workspace_id: str,
    task_id: str,
    body: AnswerBody,
    queue: QueueDep,
    pubsub: PubSubDep,
    workspaces_root: WorkspacesRootDep,
) -> AnswerResponse:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    try:
        return await run_in_threadpool(
            _answer_and_maybe_dispatch,
            ws=ws,
            task_id=task_id,
            body=body,
            queue=queue,
            pubsub=pubsub,
            workspaces_root=workspaces_root,
        )
    except AnswerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


operator_clause_router = APIRouter(prefix="/api/workspaces")

_CLAUSE_WITHDRAWAL_RESPONSES: dict[int | str, dict[str, str]] = {
    **_deps.GUARDED,
    404: {"description": "workspace not found, or clause_id does not resolve"},
    409: {"description": "workspace is archived, or clause is already withdrawn"},
}


class ClauseWithdrawalBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ClauseWithdrawalResponse(BaseModel):
    requirements: list[Requirement]


def _require_withdrawable_clause(ws: Workspace, clause_id: str) -> None:
    target = _answer_waiver.find_live_requirement(ws.meta.requirements or [], clause_id)
    if target is None:
        raise HTTPException(status_code=404, detail="clause_id does not resolve")
    if target.withdrawn_by_question_id is not None:
        raise HTTPException(status_code=409, detail="clause is already withdrawn")


def _withdraw_clause_locked(
    ws: Workspace, clause_id: str, reason: str, workspaces_root: Path,
) -> Workspace:
    with _lock:
        fresh = workspace_mod.read(ws.task_id, root=workspaces_root)
        if fresh.meta.status == Status.ARCHIVED:
            raise HTTPException(status_code=409, detail="workspace is archived")
        _require_withdrawable_clause(fresh, clause_id)
        fresh = clause_withdrawal.withdraw(
            fresh, [clause_id], clause_withdrawal.OPERATOR_WITHDRAWAL_SENTINEL
        )
        _answer_waiver.append_operator_clause_audit(
            ws.task_id, clause_id, reason,
            kind="clause_withdrawal", log_label="clause_withdrawal_override",
        )
        return fresh


@operator_clause_router.put(
    "/{workspace_id}/clauses/{clause_id}/withdrawal",
    responses=_CLAUSE_WITHDRAWAL_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def withdraw_clause_as_operator(
    workspace_id: str,
    clause_id: str,
    body: ClauseWithdrawalBody,
    workspaces_root: WorkspacesRootDep,
) -> ClauseWithdrawalResponse:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    ws = await run_in_threadpool(
        _withdraw_clause_locked, ws, clause_id, body.reason, workspaces_root
    )
    return ClauseWithdrawalResponse(requirements=ws.meta.requirements or [])


@operator_clause_router.delete(
    "/{workspace_id}/clauses/{clause_id}/withdrawal",
    responses=_answer_waiver.CLAUSE_REINSTATEMENT_RESPONSES,
    dependencies=[Depends(_deps.require_control_token)],
)
async def reinstate_clause_as_operator(
    workspace_id: str,
    clause_id: str,
    body: ClauseWithdrawalBody,
    workspaces_root: WorkspacesRootDep,
) -> ClauseWithdrawalResponse:
    ws = await read_workspace_or_404(workspace_id, workspaces_root)
    ws = await run_in_threadpool(
        _answer_waiver.reinstate_clause_locked, _lock, ws, clause_id, body.reason, workspaces_root
    )
    return ClauseWithdrawalResponse(requirements=ws.meta.requirements or [])


__all__ = [
    "AnswerBody",
    "AnswerError",
    "AnswerResponse",
    "ClauseWithdrawalBody",
    "ClauseWithdrawalResponse",
    "DispatchInfo",
    "ResolvedAnswerTarget",
    "answer_question",
    "answer_router",
    "operator_clause_router",
    "reinstate_clause_as_operator",
    "withdraw_clause_as_operator",
]
