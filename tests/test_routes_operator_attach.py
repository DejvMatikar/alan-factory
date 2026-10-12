from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from alan import (
    control_plane,
    operator_pty,
    operator_session,
    operator_ticket,
    operator_tmux,
    server,
)
from alan.clock import Epoch, Monotonic
from alan.operator_prompt import Scope
from alan.routes import operator_terminal
from tests.conftest import FakeTmux

_TERMINAL = "/api/operator/terminal"
_TICKET = "/api/operator/attach-ticket"
_WAIT_S = 10.0


class _FakePty:
    def __init__(self, argv: Sequence[str], env: dict[str, str], rows: int, cols: int) -> None:
        self.argv = list(argv)
        self.env = env
        self.rows = rows
        self.cols = cols
        self.output: queue.Queue[bytes] = queue.Queue()
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = threading.Event()

    async def read(self) -> bytes:
        while True:
            try:
                return self.output.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.002)

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    def close(self) -> None:
        self.closed.set()


class _Spawner:
    def __init__(self) -> None:
        self.spawned: list[_FakePty] = []
        self.error: operator_pty.PtySpawnError | None = None

    def __call__(
        self, argv: Sequence[str], *, env: dict[str, str], rows: int, cols: int
    ) -> _FakePty:
        if self.error is not None:
            raise self.error
        fake = _FakePty(argv, env, rows, cols)
        self.spawned.append(fake)
        return fake


class _StoppedClock:
    def monotonic(self) -> Monotonic:
        return Monotonic(0.0)

    def now(self) -> Epoch:
        return Epoch(0.0)


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.005)


@pytest.fixture
def spawner(monkeypatch: pytest.MonkeyPatch) -> _Spawner:
    fake = _Spawner()
    monkeypatch.setattr(operator_pty, "spawn", fake)
    return fake


@pytest.fixture
def client(
    operator_state: Path, spawner: _Spawner, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    del operator_state, spawner
    monkeypatch.setattr(operator_session, "availability", lambda: None)
    operator_ticket.clear()
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        operator_ticket.clear()


def _live_session() -> None:
    operator_session.start(Scope.FLEET, None)


def _ticket(client: TestClient) -> str:
    headers = {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}
    ticket: str = client.post(_TICKET, headers=headers).json()["ticket"]
    return ticket


def _url(ticket: str, query: str = "") -> str:
    return f"{_TERMINAL}?ticket={ticket}{query}"


@contextmanager
def _attached(
    client: TestClient, spawner: _Spawner, *, query: str = "", ticket: str | None = None
) -> Iterator[tuple[WebSocketTestSession, _FakePty]]:
    index = len(spawner.spawned)
    with client.websocket_connect(_url(ticket or _ticket(client), query)) as ws:
        _wait_until(lambda: len(spawner.spawned) > index)
        fake = spawner.spawned[index]
        try:
            yield ws, fake
        finally:
            ws.close()
            assert fake.closed.wait(_WAIT_S)


def _refused(client: TestClient, url: str) -> int:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(url):
            pass
    return int(excinfo.value.code)


def test_a_valid_ticket_attaches_the_tmux_client_with_the_requested_size(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner, query="&cols=132&rows=41") as (_ws, fake):
        assert fake.argv == operator_tmux.attach_argv()
        assert fake.env == operator_tmux.session_env()
        assert (fake.rows, fake.cols) == (41, 132)


def test_the_default_size_applies_when_none_is_given(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (_ws, fake):
        assert (fake.rows, fake.cols) == (
            operator_tmux.DEFAULT_ROWS,
            operator_tmux.DEFAULT_COLUMNS,
        )


@pytest.mark.parametrize("query", ["&cols=0", "&rows=0", "&cols=1001", "&rows=abc"])
def test_an_out_of_range_size_is_refused_at_the_handshake(
    client: TestClient, spawner: _Spawner, query: str
) -> None:
    _live_session()
    assert _refused(client, _url(_ticket(client), query)) == 1008
    assert spawner.spawned == []


def test_terminal_output_reaches_the_browser_as_binary_frames(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (ws, fake):
        fake.output.put(b"\x1b[2Jhello")
        fake.output.put(b" world")
        assert ws.receive_bytes() == b"\x1b[2Jhello"
        assert ws.receive_bytes() == b" world"


def test_keystrokes_from_the_browser_reach_the_terminal_in_order(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (ws, fake):
        ws.send_bytes(b"ls")
        ws.send_bytes(b"\r")
        _wait_until(lambda: len(fake.written) == 2)
        assert fake.written == [b"ls", b"\r"]


def test_a_resize_message_resizes_the_terminal(client: TestClient, spawner: _Spawner) -> None:
    _live_session()
    with _attached(client, spawner) as (ws, fake):
        ws.send_text('{"type": "resize", "cols": 90, "rows": 40}')
        _wait_until(lambda: fake.resizes == [(40, 90)])


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        '{"type": "unknown"}',
        '{"type": "resize", "cols": 0, "rows": 10}',
        '{"type": "resize", "cols": 10, "rows": 5000}',
        '{"type": "resize", "cols": "wide", "rows": 10}',
        '{"type": "resize"}',
        "",
    ],
)
def test_a_malformed_control_message_is_ignored_and_the_stream_keeps_working(
    client: TestClient, spawner: _Spawner, message: str
) -> None:
    _live_session()
    with _attached(client, spawner) as (ws, fake):
        ws.send_text(message)
        ws.send_text('{"type": "resize", "cols": 70, "rows": 30}')
        _wait_until(lambda: fake.resizes == [(30, 70)])
        fake.output.put(b"still alive")
        assert ws.receive_bytes() == b"still alive"


def test_leaving_the_page_closes_the_attach_client_but_not_the_session(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (_ws, fake):
        assert not fake.closed.is_set()
    assert fake.closed.is_set()
    assert operator_session.current() is not None


def test_a_session_that_ends_closes_the_socket_with_4002(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (ws, fake):
        fake.output.put(b"")
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_bytes()
    assert excinfo.value.code == 4002


def test_a_newer_attachment_supersedes_the_older_one_with_4001(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner) as (older, first):
        with _attached(client, spawner) as (newer, second):
            with pytest.raises(WebSocketDisconnect) as excinfo:
                older.receive_bytes()
            assert excinfo.value.code == 4001
            assert first.closed.wait(_WAIT_S)
            second.output.put(b"for the newer view")
            assert newer.receive_bytes() == b"for the newer view"
    assert operator_session.current() is not None


def test_superseding_never_creates_a_second_session(
    client: TestClient, spawner: _Spawner, fake_tmux: FakeTmux
) -> None:
    _live_session()
    with _attached(client, spawner):
        with _attached(client, spawner):
            pass
    assert len(fake_tmux.calls_of("new-session")) == 1


def test_after_the_newer_view_leaves_a_fresh_attachment_still_works(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    with _attached(client, spawner):
        pass
    with _attached(client, spawner) as (ws, fake):
        fake.output.put(b"second")
        assert ws.receive_bytes() == b"second"


def test_a_missing_ticket_is_refused_before_accept_with_4401(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    assert _refused(client, _TERMINAL) == 4401
    assert spawner.spawned == []


def test_an_unknown_ticket_is_refused_before_accept_with_4401(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    assert _refused(client, _url("never-issued")) == 4401
    assert spawner.spawned == []


def test_a_reused_ticket_is_refused_with_4401(client: TestClient, spawner: _Spawner) -> None:
    _live_session()
    ticket = _ticket(client)
    with _attached(client, spawner, ticket=ticket):
        pass
    assert _refused(client, _url(ticket)) == 4401
    assert len(spawner.spawned) == 1


def test_an_expired_ticket_is_refused_with_4401(client: TestClient, spawner: _Spawner) -> None:
    _live_session()
    expired = operator_ticket.issue(_StoppedClock())
    assert _refused(client, _url(expired)) == 4401
    assert spawner.spawned == []


def test_a_valid_ticket_with_no_session_is_accepted_then_closed_with_4404(
    client: TestClient, spawner: _Spawner
) -> None:
    ticket = operator_ticket.issue()
    with client.websocket_connect(_url(ticket)) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_bytes()
    assert excinfo.value.code == 4404
    assert spawner.spawned == []


def test_the_ticket_is_spent_even_when_there_is_no_session(client: TestClient) -> None:
    ticket = operator_ticket.issue()
    with client.websocket_connect(_url(ticket)) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    assert _refused(client, _url(ticket)) == 4401


def test_an_attach_client_that_cannot_start_closes_the_socket_with_1011(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    spawner.error = operator_pty.PtySpawnError("no ptys left")
    with client.websocket_connect(_url(_ticket(client))) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_bytes()
    assert excinfo.value.code == 1011


def test_a_failed_attach_leaves_room_for_the_next_one(
    client: TestClient, spawner: _Spawner
) -> None:
    _live_session()
    spawner.error = operator_pty.PtySpawnError("no ptys left")
    with client.websocket_connect(_url(_ticket(client))) as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()
    spawner.error = None
    with _attached(client, spawner) as (ws, fake):
        fake.output.put(b"recovered")
        assert ws.receive_bytes() == b"recovered"


def test_close_codes_are_the_documented_values() -> None:
    assert operator_terminal.CLOSE_SUPERSEDED == 4001
    assert operator_terminal.CLOSE_ENDED == 4002
    assert operator_terminal.CLOSE_BAD_TICKET == 4401
    assert operator_terminal.CLOSE_NO_SESSION == 4404
