from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from alan import merge_gate as merge_gate_mod
from alan import sticky_instructions
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.engine_config import (
    EngineConfig,
    Posture,
    admitted_teams,
    default_engine_config,
    get_registered,
    live_floored_posture,
    lookup_by_slug,
)
from alan.engine_preflight import assert_engine_ready
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import (
    Handover,
    OpenQuestion,
    find_handover_by_id,
    list_handovers,
    unanswered_blocking_question_ids,
)
from alan.loop_guard import append_spiral_audit, fix_cycle_ceiling_reached
from alan.queue import Job, Queue
from alan.teams import _pr_forge, _pr_state_paths
from alan.teams._base import Team
from alan.workflow import WorkflowError
from alan.workspace import Status, Task, Workspace

_WAKEABLE_STATUSES: frozenset[Status] = frozenset(
    {Status.AWAITING_INPUT, Status.COMPLETED, Status.ESCALATED, Status.FAILED, Status.PARTIAL}
)

EnqueueWorkflowFn = Callable[..., tuple[Job, str]]

_RESOLVER_BY_CLASS: dict[str, str] = {
    "merge_block_escalation": "pr_merge",
    "verification_escalation": "verification",
    "fix_escalation": "implementation_fix",
}
_ORCHESTRATED_COMPLETENESS_CLASS = "orchestrated_completeness"


@dataclass(frozen=True)
class DispatchOutcome:
    job: Job
    inv_id: str
    merge_gate: merge_gate_mod.MergeGateDecision


def wake_terminal_workspace(ws: Workspace) -> Workspace:
    if ws.meta.status not in _WAKEABLE_STATUSES:
        return ws
    return workspace_mod.transition(ws, Status.ACTIVE)


def _source_task_id(ws: Workspace, inv_id: str) -> str | None:
    try:
        return workflow_mod.load_workflow_cached(
            workflow_mod.workflow_path(ws, inv_id)
        ).source_task_id
    except (WorkflowError, OSError, UnicodeDecodeError):
        return None


def _attribution(ws: Workspace, handovers: list[Handover]) -> dict[str, str]:
    resolved = {inv: _source_task_id(ws, inv) for inv in {h.from_invocation_id for h in handovers}}
    return {inv: task for inv, task in resolved.items() if task}


def _pin_of(ws: Workspace, task_id: str) -> str | None:
    subtask = next((t for t in ws.meta.tasks if t.id == task_id), None)
    return None if subtask is None else subtask.blocked_handover_id


def _fallback(ws: Workspace, handovers: list[Handover], pin: str | None) -> Handover | None:
    if pin is not None:
        return find_handover_by_id(ws, pin)
    return handovers[-1] if handovers else None


def _index_of(handovers: list[Handover], handover_id: str) -> int | None:
    return max((i for i, h in enumerate(handovers) if h.handover_id == handover_id), default=None)


def _prior_index(handovers: list[Handover], own: list[bool], pin: str | None) -> int | None:
    if pin is not None:
        return _index_of(handovers, pin)
    return max((i for i, mine in enumerate(own) if mine), default=None)


def _recover_pr(handovers: list[Handover], own: list[bool], index: int) -> Handover:
    selected = handovers[index]
    if selected.pr_number is not None:
        return selected
    donors = [
        h.pr_number
        for h, mine in zip(handovers[:index], own[:index], strict=True)
        if mine and h.pr_number is not None
    ]
    return selected.model_copy(update={"pr_number": donors[-1]}) if donors else selected


def _own_flags(
    attribution: dict[str, str], handovers: list[Handover], task_id: str
) -> list[bool]:
    return [attribution.get(h.from_invocation_id) == task_id for h in handovers]


def prior_handover_for_task(ws: Workspace, task_id: str) -> Handover | None:
    handovers = list_handovers(ws)
    attribution = _attribution(ws, handovers)
    known = {t.id for t in ws.meta.tasks} or {ws.task_id}
    pin = _pin_of(ws, task_id)
    if not set(attribution.values()) & known:
        return _fallback(ws, handovers, pin)
    own = _own_flags(attribution, handovers, task_id)
    index = _prior_index(handovers, own, pin)
    return None if index is None else _recover_pr(handovers, own, index)


def recover_prior_pr(ws: Workspace, task_id: str, prior: Handover) -> Handover:
    if prior.pr_number is not None:
        return prior
    handovers = list_handovers(ws)
    index = _index_of(handovers, prior.handover_id)
    if index is None:
        return prior
    own = _own_flags(_attribution(ws, handovers), handovers, task_id)
    return _recover_pr(handovers, own, index)


OWNERLESS_DECOMPOSED_DETAIL = (
    "workspace {task_id!r} has been split into subtasks, but resolving this "
    "blocking question does not identify a unique owning subtask; expected "
    "exactly one match, found {count}"
)

OWNERLESS_DECOMPOSED_NO_QUESTION_DETAIL = (
    "workspace {task_id!r} has been split into subtasks, but resolving the "
    "owning subtask does not identify a unique match; expected exactly one "
    "match, found {count}"
)


def _is_owner_of(ws: Workspace, task: Task, blocking: Handover | None) -> bool:
    if task.blocked_handover_id is None:
        return False
    if blocking is not None:
        return task.blocked_handover_id == blocking.handover_id
    candidate = find_handover_by_id(ws, task.blocked_handover_id)
    return candidate is not None and bool(unanswered_blocking_question_ids(candidate, ws))


def _needs_owner_resolution(ws: Workspace, task_id: str, multi_task_ids: set[str]) -> bool:
    return bool(multi_task_ids) and task_id not in multi_task_ids and task_id == ws.task_id


def owning_task_id(ws: Workspace, task_id: str, blocking: Handover | None = None) -> str:
    multi_task_ids = {t.id for t in ws.meta.tasks}
    if not _needs_owner_resolution(ws, task_id, multi_task_ids):
        return task_id
    owners = [t.id for t in ws.meta.tasks if _is_owner_of(ws, t, blocking)]
    if len(owners) != 1:
        template = (
            OWNERLESS_DECOMPOSED_DETAIL if blocking is not None
            else OWNERLESS_DECOMPOSED_NO_QUESTION_DETAIL
        )
        raise AppError(
            status_code=404,
            detail=template.format(task_id=task_id, count=len(owners)),
        )
    return owners[0]


def _bump_operator_fix_cycle(team: Team, prior: Handover | None, ws: Workspace) -> None:
    if team.name != "implementation_fix" or prior is None or prior.pr_number is None:
        return
    _pr_state_paths.bump_fix_cycle_count(prior.pr_number, _pr_forge.forge_target_for_ws(ws))


@dataclass(frozen=True)
class _EnqueueInstructions:
    text: str | None
    origin: str | None
    persist_text: str | None


def _keeps_sticky(ws: Workspace, task_id: str, team_name: str, keep_sticky: bool) -> bool:
    return (
        keep_sticky
        and sticky_instructions.should_persist(team_name)
        and sticky_instructions.resolve(ws, task_id) is not None
    )


def _enqueue_instructions(
    ws: Workspace,
    task_id: str,
    team: Team,
    instructions: str | None,
    origin: Literal["dispatch", "generated"],
    *,
    keep_sticky: bool,
) -> _EnqueueInstructions:
    cleaned = instructions.strip() if instructions else ""
    if _keeps_sticky(ws, task_id, team.name, keep_sticky):
        text, composed_origin = sticky_instructions.for_enqueue(ws, task_id, team.name, cleaned)
        return _EnqueueInstructions(text, composed_origin, None)
    if not cleaned:
        return _EnqueueInstructions(instructions, None, None)
    persist_text = cleaned if sticky_instructions.should_persist(team.name) else None
    return _EnqueueInstructions(instructions, origin, persist_text)


def dispatch_team_core(
    *,
    workspace: Workspace,
    task_id: str,
    team: Team,
    queue: Queue,
    pubsub: PubSub,
    enqueue_workflow: EnqueueWorkflowFn,
    merge_gate: merge_gate_mod.MergeGateDecision,
    instructions: str | None = None,
    instructions_origin: Literal["dispatch", "generated"] = "dispatch",
    keep_sticky: bool = False,
) -> DispatchOutcome:
    """Wake, resolve prior handover, reset the revisit chain, and enqueue.

    The shared core behind a human's manual dispatch (`alan.routes.workspaces
    .dispatch_task_team`), a propose-mode approval (`resolve_proposed_dispatch`
    + the approve route), and an auto-continue dispatch (`alan.auto_continue`).
    Deliberately synchronous and free of HTTP concerns so it can be called from
    the dispatcher's worker thread as well as from an async route handler via
    `run_in_threadpool`. `instructions_origin` defaults to "dispatch" (a
    human typed or approved the text); a caller whose `instructions` is a
    system-generated template — e.g. `auto_continue`'s boilerplate — passes
    "generated" so the job and any persisted sticky entry are labeled
    accurately rather than as if a human had typed them.
    """
    ws = wake_terminal_workspace(workspace)
    prior = prior_handover_for_task(ws, task_id)
    if prior is not None:
        ws = workspace_mod.reset_revisit_chain(ws, prior.handover_chain_id)
        append_spiral_audit({
            "kind": "chain_reset",
            "task_id": ws.task_id,
            "handover_chain_id": prior.handover_chain_id,
        })
    _bump_operator_fix_cycle(team, prior, ws)
    resolved = _enqueue_instructions(
        ws, task_id, team, instructions, instructions_origin, keep_sticky=keep_sticky
    )
    job, inv_id = enqueue_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws,
        team=team,
        prior=prior,
        parent_handover_id=prior.handover_id if prior is not None else None,
        instructions=resolved.text,
        instructions_origin=resolved.origin,
        task_id=task_id,
    )
    if resolved.persist_text is not None:
        workspace_mod.set_sticky_instructions(
            ws, task_id, resolved.persist_text, origin=instructions_origin
        )
    return DispatchOutcome(job=job, inv_id=inv_id, merge_gate=merge_gate)


def resolver_team_for_question(question_class: str | None, from_team: str) -> str:
    if question_class == _ORCHESTRATED_COMPLETENESS_CLASS:
        return from_team
    resolver = _RESOLVER_BY_CLASS.get(question_class) if question_class is not None else None
    if resolver is None:
        raise AppError(
            status_code=400,
            detail=f"no resolver mapped for question_class {question_class!r}",
        )
    return resolver


def resolver_team_for_answer(question: OpenQuestion, from_team: str, answer: str) -> str:
    mapped = question.option_resolvers.get(answer)
    if mapped is not None:
        return mapped
    return resolver_team_for_question(question.question_class, from_team)


def _pinned_posture(ws: Workspace) -> Posture:
    snapshot = ws.meta.engine_snapshot
    if snapshot is not None:
        return live_floored_posture(snapshot.posture, ws.meta.engine_key)
    if ws.meta.engine_key is not None:
        cfg = lookup_by_slug(ws.meta.engine_key)
        if cfg is not None:
            return cfg.posture
    cfg = get_registered() or default_engine_config()
    return cfg.posture


def _pinned_transition_graph(ws: Workspace) -> dict[str, list[str]]:
    # Deliberately NOT live-floored like _pinned_posture: in-flight work pinned
    # to a prior snapshot keeps that snapshot's roster verbatim, so a live
    # roster edit only ever takes effect for a fresh dispatch/re-stamp.
    snapshot = ws.meta.engine_snapshot
    if snapshot is not None:
        return dict(snapshot.transition_graph)
    if ws.meta.engine_key is not None:
        cfg = lookup_by_slug(ws.meta.engine_key)
        if cfg is not None:
            return dict(cfg.transition_graph)
    cfg = get_registered() or default_engine_config()
    return dict(cfg.transition_graph)


def _pinned_engine_config(ws: Workspace) -> EngineConfig:
    snapshot = ws.meta.engine_snapshot
    if snapshot is not None and snapshot.config is not None:
        return snapshot.config
    if ws.meta.engine_key is not None:
        cfg = lookup_by_slug(ws.meta.engine_key)
        if cfg is not None:
            return cfg
    return get_registered() or default_engine_config()


def _pinned_engine_slug(ws: Workspace) -> str:
    return _pinned_engine_config(ws).repo.slug


def _transition_graph_nodes(graph: dict[str, list[str]]) -> set[str]:
    nodes: set[str] = set(graph)
    for destinations in graph.values():
        nodes.update(destinations)
    return nodes


def _assert_task_known(ws: Workspace, task_id: str) -> None:
    multi_task_ids = {t.id for t in ws.meta.tasks}
    known = task_id in multi_task_ids if multi_task_ids else task_id == ws.task_id
    if not known:
        raise AppError(
            status_code=404,
            detail=f"task {task_id!r} not found in workspace {ws.task_id!r}",
        )


def _assert_posture_admits(ws: Workspace, team_name: str) -> None:
    posture = _pinned_posture(ws)
    if team_name not in admitted_teams({team_name}, posture):
        raise AppError(
            status_code=422,
            detail=f"team {team_name!r} is not admitted at posture {posture!r}",
        )


def _assert_on_roster(ws: Workspace, team_name: str) -> None:
    roster = _transition_graph_nodes(_pinned_transition_graph(ws))
    if roster and team_name not in roster:
        engine_slug = _pinned_engine_slug(ws)
        raise AppError(
            status_code=422,
            detail=f"team {team_name!r} is not on the roster for engine {engine_slug!r}",
        )


def _assert_fix_ceiling_not_reached(ws: Workspace, task_id: str) -> None:
    prior = prior_handover_for_task(ws, task_id)
    if prior is None or prior.pr_number is None:
        return
    forge_target = _pr_forge.forge_target_for_ws(ws)
    if not fix_cycle_ceiling_reached(prior.pr_number, forge_target):
        return
    raise AppError(
        status_code=409,
        detail=(
            f"fix-cycle ceiling reached for PR #{prior.pr_number}; answer the "
            "pending fix_cycle_ceiling question with its override option to "
            "grant three more cycles, then dispatch implementation_fix again"
        ),
    )


def assert_team_dispatchable(ws: Workspace, task_id: str, team_name: str) -> None:
    _assert_task_known(ws, task_id)
    if ws.meta.status == Status.ARCHIVED:
        raise AppError(status_code=409, detail="workspace is archived")
    assert_engine_ready(_pinned_engine_config(ws))
    if team_name == "implementation_fix":
        _assert_fix_ceiling_not_reached(ws, task_id)
    _assert_posture_admits(ws, team_name)
    _assert_on_roster(ws, team_name)


def team_is_dispatchable(ws: Workspace, team_name: str) -> bool:
    posture = _pinned_posture(ws)
    if team_name not in admitted_teams({team_name}, posture):
        return False
    roster = _transition_graph_nodes(_pinned_transition_graph(ws))
    return not roster or team_name in roster


__all__ = [
    "DispatchOutcome",
    "EnqueueWorkflowFn",
    "OWNERLESS_DECOMPOSED_DETAIL",
    "OWNERLESS_DECOMPOSED_NO_QUESTION_DETAIL",
    "assert_team_dispatchable",
    "dispatch_team_core",
    "owning_task_id",
    "prior_handover_for_task",
    "recover_prior_pr",
    "resolver_team_for_answer",
    "resolver_team_for_question",
    "team_is_dispatchable",
    "wake_terminal_workspace",
]
