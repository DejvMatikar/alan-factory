from __future__ import annotations

import asyncio
import functools
import logging
import threading
from collections.abc import MutableMapping
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Query, WebSocket
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

from alan import operator_pty, operator_session, operator_ticket, operator_tmux
from alan.errors import AppError
from alan.events import PubSub
from alan.operator_prompt import InvalidScopeTarget, Scope
from alan.routes._deps import GUARDED_EXPIRABLE, PubSubDep, WorkspacesRootDep, require_control_token
from alan.teams._base import TeamEvent

logger = logging.getLogger("alan.operator_terminal")

operator_terminal_router = APIRouter(prefix="/api/operator")

OPERATOR_EVENT_KIND: Final[str] = "operator_session_changed"
CLOSE_SUPERSEDED: Final[int] = 4001
CLOSE_ENDED: Final[int] = 4002
CLOSE_BAD_TICKET: Final[int] = 4401
CLOSE_NO_SESSION: Final[int] = 4404
CLOSE_ATTACH_FAILED: Final[int] = 1011
_MAX_DIMENSION: Final[int] = 1000
_NO_SESSION_DETAIL: Final[str] = "no operator session"

_START_ERRORS: Final[tuple[type[Exception], ...]] = (
    InvalidScopeTarget,
    operator_session.OperatorTargetNotFound,
    operator_session.OperatorSessionActive,
    operator_session.OperatorUnavailable,
    operator_session.OperatorStartFailed,
    operator_session.OperatorEndFailed,
)

_START_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED_EXPIRABLE,
    404: {
        "description": (
            "repo scope names a repository that is not connected or is hidden, or workspace "
            "scope names an unknown workspace or one whose repository is not connected"
        )
    },
    409: {"description": "a session is already running and replace was not requested"},
    422: {"description": "scope or target invalid"},
    500: {"description": "control token not configured, or the session failed to start"},
    503: {
        "description": (
            "operator terminal unavailable, including a failed checkout clone; body names the "
            "exact reason"
        )
    },
}
_END_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED_EXPIRABLE,
    404: {"description": _NO_SESSION_DETAIL},
    500: {"description": "control token not configured, or the session would not stop"},
}
_TICKET_RESPONSES: dict[int | str, dict[str, str]] = {
    **GUARDED_EXPIRABLE,
    404: {"description": _NO_SESSION_DETAIL},
}


class OperatorStartBody(BaseModel):
    scope: Scope
    target: str | None = Field(default=None, max_length=200)
    replace: bool = False


class OperatorUnavailableOut(BaseModel):
    reason: str
    detail: str


class OperatorSessionOut(BaseModel):
    scope: Scope
    target: str | None
    started_at: str | None
    update_warning: str | None
    exited: bool


class OperatorStatusOut(BaseModel):
    session: OperatorSessionOut | None
    unavailable: OperatorUnavailableOut | None


class OperatorAttachTicketOut(BaseModel):
    ticket: str
    expires_in_s: float


class _ResizeMessage(BaseModel):
    type: Literal["resize"]
    cols: int = Field(ge=1, le=_MAX_DIMENSION)
    rows: int = Field(ge=1, le=_MAX_DIMENSION)


def _session_out(info: operator_session.SessionInfo) -> OperatorSessionOut:
    return OperatorSessionOut(
        scope=info.scope,
        target=info.target,
        started_at=info.started_at,
        update_warning=info.update_warning,
        exited=info.exited,
    )


def _start_failure(exc: Exception) -> AppError:
    if isinstance(exc, operator_session.OperatorUnavailable):
        return AppError(
            503, {"reason": exc.unavailable.reason, "detail": exc.unavailable.detail}
        )
    if isinstance(exc, operator_session.OperatorSessionActive):
        return AppError(409, "an operator session is already running; ask to replace it")
    if isinstance(exc, operator_session.OperatorTargetNotFound):
        return AppError(404, str(exc))
    if isinstance(exc, InvalidScopeTarget):
        return AppError(422, str(exc))
    return AppError(500, str(exc))


def _publish_changed(pubsub: PubSub) -> None:
    pubsub.publish_global(TeamEvent(OPERATOR_EVENT_KIND, {}))


@operator_terminal_router.get("/status")
async def get_operator_status() -> OperatorStatusOut:
    status = await run_in_threadpool(operator_session.status)
    unavailable = status.unavailable
    return OperatorStatusOut(
        session=_session_out(status.session) if status.session else None,
        unavailable=(
            OperatorUnavailableOut(reason=unavailable.reason, detail=unavailable.detail)
            if unavailable
            else None
        ),
    )


@operator_terminal_router.post(
    "/session", dependencies=[Depends(require_control_token)], responses=_START_RESPONSES
)
async def start_operator_session(
    body: OperatorStartBody, workspaces_root: WorkspacesRootDep, pubsub: PubSubDep
) -> OperatorSessionOut:
    start = functools.partial(
        operator_session.start,
        body.scope,
        body.target,
        replace=body.replace,
        workspaces_root=workspaces_root,
    )
    try:
        info = await run_in_threadpool(start)
    except _START_ERRORS as exc:
        if body.replace:
            _publish_changed(pubsub)
        raise _start_failure(exc) from exc
    _publish_changed(pubsub)
    return _session_out(info)


@operator_terminal_router.delete(
    "/session",
    status_code=204,
    dependencies=[Depends(require_control_token)],
    responses=_END_RESPONSES,
)
async def end_operator_session(workspaces_root: WorkspacesRootDep, pubsub: PubSubDep) -> None:
    end = functools.partial(operator_session.end, workspaces_root=workspaces_root)
    try:
        ended = await run_in_threadpool(end)
    except operator_session.OperatorEndFailed as exc:
        raise AppError(500, str(exc)) from exc
    if not ended:
        raise AppError(404, _NO_SESSION_DETAIL)
    _publish_changed(pubsub)


@operator_terminal_router.post(
    "/attach-ticket", dependencies=[Depends(require_control_token)], responses=_TICKET_RESPONSES
)
async def issue_attach_ticket() -> OperatorAttachTicketOut:
    if await run_in_threadpool(operator_session.current) is None:
        raise AppError(404, _NO_SESSION_DETAIL)
    return OperatorAttachTicketOut(
        ticket=operator_ticket.issue(), expires_in_s=operator_ticket.TICKET_TTL_S
    )


class _Attachment:
    def __init__(self) -> None:
        self.superseded = asyncio.Event()


class _AttachmentRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: _Attachment | None = None

    def take_over(self) -> _Attachment:
        attachment = _Attachment()
        with self._lock:
            previous, self._current = self._current, attachment
        if previous is not None:
            previous.superseded.set()
        return attachment

    def release(self, attachment: _Attachment) -> None:
        with self._lock:
            if self._current is attachment:
                self._current = None


_registry = _AttachmentRegistry()


def _apply_control(client: operator_pty.PtyClient, text: str) -> None:
    try:
        message = _ResizeMessage.model_validate_json(text)
    except ValidationError:
        return
    client.resize(message.rows, message.cols)


async def _pump_output(websocket: WebSocket, client: operator_pty.PtyClient) -> int:
    while True:
        chunk = await client.read()
        if not chunk:
            return CLOSE_ENDED
        await websocket.send_bytes(chunk)


async def _pump_input(websocket: WebSocket, client: operator_pty.PtyClient) -> None:
    while True:
        message: MutableMapping[str, Any] = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is not None:
            await run_in_threadpool(client.write, data)
        else:
            _apply_control(client, message.get("text") or "")


async def _serve(
    websocket: WebSocket, client: operator_pty.PtyClient, attachment: _Attachment
) -> int | None:
    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(_pump_output(websocket, client)),
        asyncio.create_task(_pump_input(websocket, client)),
        asyncio.create_task(attachment.superseded.wait()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    if attachment.superseded.is_set():
        return CLOSE_SUPERSEDED
    return results[0] if isinstance(results[0], int) else None


async def _attach(websocket: WebSocket, rows: int, cols: int) -> int | None:
    attachment = _registry.take_over()
    try:
        client = operator_pty.spawn(
            operator_tmux.attach_argv(), env=operator_tmux.session_env(), rows=rows, cols=cols
        )
    except operator_pty.PtySpawnError:
        logger.warning("operator attach client did not start", exc_info=True)
        _registry.release(attachment)
        return CLOSE_ATTACH_FAILED
    try:
        return await _serve(websocket, client, attachment)
    finally:
        _registry.release(attachment)
        client.close()


@operator_terminal_router.websocket("/terminal")
async def operator_terminal_socket(
    websocket: WebSocket,
    ticket: str = "",
    cols: Annotated[int, Query(ge=1, le=_MAX_DIMENSION)] = operator_tmux.DEFAULT_COLUMNS,
    rows: Annotated[int, Query(ge=1, le=_MAX_DIMENSION)] = operator_tmux.DEFAULT_ROWS,
) -> None:
    if not operator_ticket.consume(ticket):
        await websocket.close(code=CLOSE_BAD_TICKET)
        return
    await websocket.accept()
    if await run_in_threadpool(operator_session.current) is None:
        await websocket.close(code=CLOSE_NO_SESSION)
        return
    code = await _attach(websocket, rows, cols)
    if code is not None:
        await websocket.close(code=code)


__all__ = ["OPERATOR_EVENT_KIND", "operator_terminal_router"]
