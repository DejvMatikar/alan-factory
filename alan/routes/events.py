from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from alan.events import PubSub
from alan.routes._deps import SSE_HEADERS, SSE_MEDIA, PubSubDep
from alan.teams._base import TeamEvent

events_router = APIRouter(prefix="/api")

_COALESCE_S = 0.25
_HEARTBEAT_S = 15.0
_QUEUE_MAX = 256

_HEALTH_KINDS = frozenset(
    {"health_state_saved", "driver_loop_changed", "operator_session_changed"}
)
_IGNORED_KINDS = frozenset({"stream"})
_ENGINE_KINDS = frozenset({
    "engine_created",
    "engine_updated",
    "engine_deleted",
    "engine_posture_changed",
    "engine_accent_changed",
    "engine_hidden",
    "engine_unhidden",
    "pool_lifecycle_changed",
    "preflight_completed",
    "dep_sweep_changed",
    "engine_scaffolded",
    "engine_protected",
    "repo_status_refreshed",
    "recurring_status_refreshed",
    "secret_written",
})


def _kinds_for(event: TeamEvent) -> frozenset[str]:
    if event.kind in _IGNORED_KINDS:
        return frozenset()
    if event.kind in _HEALTH_KINDS:
        return frozenset({"health"})
    if event.kind in _ENGINE_KINDS:
        return frozenset({"engines"})
    return frozenset({"workspace_list", "health"})


def _changed_frame(kinds: frozenset[str]) -> str:
    return f"event: changed\ndata: {json.dumps({'kinds': sorted(kinds)})}\n\n"


_HEARTBEAT_FRAME = ": ping\n\n"


async def _drain_coalesced(
    queue: asyncio.Queue[frozenset[str]], first: frozenset[str]
) -> frozenset[str]:
    kinds = first
    deadline = asyncio.get_running_loop().time() + _COALESCE_S
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return kinds
        try:
            kinds = kinds | await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            return kinds


async def global_event_stream(pubsub: PubSub) -> AsyncGenerator[str, None]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[frozenset[str]] = asyncio.Queue(maxsize=_QUEUE_MAX)

    def _enqueue(kinds: frozenset[str]) -> None:
        try:
            queue.put_nowait(kinds)
        except asyncio.QueueFull:
            pass

    def on_event(event: TeamEvent) -> None:
        kinds = _kinds_for(event)
        if kinds:
            loop.call_soon_threadsafe(_enqueue, kinds)

    unsubscribe = pubsub.subscribe_all(on_event)
    try:
        while True:
            try:
                first = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
            except TimeoutError:
                yield _HEARTBEAT_FRAME
                continue
            yield _changed_frame(await _drain_coalesced(queue, first))
    finally:
        unsubscribe()


@events_router.get("/events")
async def get_events(pubsub: PubSubDep) -> StreamingResponse:
    return StreamingResponse(
        global_event_stream(pubsub), media_type=SSE_MEDIA, headers=SSE_HEADERS
    )
