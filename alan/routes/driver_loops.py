from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from alan import driver_loop_pause as pause_mod
from alan import driver_loop_status as status_mod
from alan.detached_mode import resolve_detached_mode
from alan.driver_loop_runs import RunOutcome
from alan.driver_loop_schedule import Decision, DecisionAction, HoldReason
from alan.events import PubSub
from alan.queue import Queue
from alan.routes._deps import GUARDED, PubSubDep, QueueDep, require_control_token
from alan.teams._base import TeamEvent

driver_loops_router = APIRouter(prefix="/api/driver-loops")

_UNKNOWN_LOOP_DETAIL = "unknown_loop"

_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")

_MUTATION_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED,
    409: {"description": "loop refused: detail names the reason"},
}

_RUN_NOW_RESPONSES: dict[int | str, dict[str, str]] = {
    **_MUTATION_RESPONSES,
    404: {"description": _UNKNOWN_LOOP_DETAIL},
}


class DriverLoopDecisionOut(BaseModel):
    action: DecisionAction
    reason: HoldReason | None
    detail: str | None
    next_wake_at: float | None


class DriverLoopStatusOut(BaseModel):
    name: str
    enabled: bool
    dedicated_clone: bool
    paused: bool
    decision: DriverLoopDecisionOut
    last_outcome: RunOutcome | None
    final_message: str
    denial_count: int
    run_now_pending: bool


class DriverLoopsListOut(BaseModel):
    supervised: bool
    loops: list[DriverLoopStatusOut]


class DriverLoopPauseAck(BaseModel):
    name: str
    paused: bool


def _redact_detail(detail: str | None) -> str | None:
    if detail is None:
        return None
    return _URL_RE.sub("[redacted-url]", detail)


def _decision_out(decision: Decision) -> DriverLoopDecisionOut:
    return DriverLoopDecisionOut(
        action=decision.action,
        reason=decision.reason,
        detail=_redact_detail(decision.detail),
        next_wake_at=decision.next_wake_at,
    )


def _status_out(view: status_mod.LoopStatusView) -> DriverLoopStatusOut:
    return DriverLoopStatusOut(
        name=view.name,
        enabled=view.enabled,
        dedicated_clone=view.dedicated_clone,
        paused=pause_mod.is_paused(view.name),
        decision=_decision_out(view.decision),
        last_outcome=view.last_outcome,
        final_message=view.final_message,
        denial_count=view.denial_count,
        run_now_pending=view.run_now_pending,
    )


def _list_payload(queue: Queue) -> DriverLoopsListOut:
    views = status_mod.snapshot(queue=queue)
    return DriverLoopsListOut(
        supervised=resolve_detached_mode(),
        loops=[_status_out(view) for view in views],
    )


def _find_view(name: str, queue: Queue) -> status_mod.LoopStatusView | None:
    return next((view for view in status_mod.snapshot(queue=queue) if view.name == name), None)


def _publish_driver_loop_changed(pubsub: PubSub) -> None:
    pubsub.publish_global(TeamEvent("driver_loop_changed", {}))


@driver_loops_router.get("")
async def list_driver_loops(queue: QueueDep) -> DriverLoopsListOut:
    return await run_in_threadpool(_list_payload, queue)


@driver_loops_router.post(
    "/{name}/pause",
    dependencies=[Depends(require_control_token)],
    responses=_MUTATION_RESPONSES,
)
async def pause_driver_loop(name: str, pubsub: PubSubDep) -> DriverLoopPauseAck:
    try:
        await run_in_threadpool(pause_mod.pause, name)
    except pause_mod.DriverLoopPauseError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    _publish_driver_loop_changed(pubsub)
    return DriverLoopPauseAck(name=name, paused=True)


@driver_loops_router.post(
    "/{name}/resume",
    dependencies=[Depends(require_control_token)],
    responses=_MUTATION_RESPONSES,
)
async def resume_driver_loop(name: str, pubsub: PubSubDep) -> DriverLoopPauseAck:
    try:
        await run_in_threadpool(pause_mod.resume, name)
    except pause_mod.DriverLoopPauseError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    _publish_driver_loop_changed(pubsub)
    return DriverLoopPauseAck(name=name, paused=False)


@driver_loops_router.post(
    "/{name}/run-now",
    dependencies=[Depends(require_control_token)],
    responses=_RUN_NOW_RESPONSES,
)
async def run_now_driver_loop(
    name: str, queue: QueueDep, pubsub: PubSubDep
) -> DriverLoopStatusOut:
    reason = await run_in_threadpool(status_mod.request_run_now, name, queue=queue)
    if reason == _UNKNOWN_LOOP_DETAIL:
        raise HTTPException(status_code=404, detail=_UNKNOWN_LOOP_DETAIL)
    if reason is not None:
        raise HTTPException(status_code=409, detail=reason)
    _publish_driver_loop_changed(pubsub)
    view = await run_in_threadpool(_find_view, name, queue)
    if view is None:
        await run_in_threadpool(status_mod.clear_run_now_request, name)
        raise HTTPException(status_code=404, detail=_UNKNOWN_LOOP_DETAIL)
    return _status_out(view)
