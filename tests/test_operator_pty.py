from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import cast

import pytest

from alan import operator_pty
from alan.operator_pty import PtyClient, PtySpawnError, spawn

_TIMEOUT_S = 10.0
_ENV = {"PATH": os.environ.get("PATH", "")}

_RESIZE_REPORTER = """
import os, signal, time
def report(*_):
    size = os.get_terminal_size(1)
    os.write(1, b'SIZE %d %d\\n' % (size.lines, size.columns))
signal.signal(signal.SIGWINCH, report)
report()
time.sleep(60)
"""

_IGNORES_TERMINATE = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write('ready\\n')
sys.stdout.flush()
time.sleep(60)
"""

_SLEEPER = "import time; time.sleep(60)"


@pytest.fixture
def clients() -> Iterator[list[PtyClient]]:
    opened: list[PtyClient] = []
    yield opened
    for client in opened:
        client.close()


def _start(clients: list[PtyClient], script: str, *, rows: int = 24, cols: int = 80) -> PtyClient:
    client = spawn([sys.executable, "-c", script], env=_ENV, rows=rows, cols=cols)
    clients.append(client)
    return client


async def _read_until(client: PtyClient, needle: bytes) -> bytes:
    collected = b""
    while needle not in collected:
        chunk = await asyncio.wait_for(client.read(), _TIMEOUT_S)
        assert chunk, f"stream ended before {needle!r}: {collected!r}"
        collected += chunk
    return collected


def _process(client: PtyClient) -> subprocess.Popen[bytes]:
    return cast("subprocess.Popen[bytes]", vars(client)["_process"])


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _TIMEOUT_S
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)


async def test_input_written_to_the_client_reaches_the_child(clients: list[PtyClient]) -> None:
    client = _start(
        clients,
        "import sys; sys.stdout.write(sys.stdin.readline().strip().upper() + '!\\n');"
        " sys.stdout.flush()",
    )
    client.write(b"abc\n")
    assert b"ABC!" in await _read_until(client, b"ABC!")


async def test_a_large_write_is_delivered_completely(clients: list[PtyClient]) -> None:
    client = _start(
        clients,
        "import sys; data = sys.stdin.buffer.readline();"
        " sys.stdout.write('LEN %d\\n' % len(data)); sys.stdout.flush()",
    )
    payload = b"x" * 900 + b"\n"
    client.write(payload)
    assert b"LEN 901" in await _read_until(client, b"LEN 901")


async def test_the_initial_window_size_is_applied_before_the_child_starts(
    clients: list[PtyClient],
) -> None:
    client = _start(clients, _RESIZE_REPORTER, rows=31, cols=101)
    assert b"SIZE 31 101" in await _read_until(client, b"SIZE 31 101")


async def test_resize_changes_the_window_size_and_wakes_the_child(
    clients: list[PtyClient],
) -> None:
    client = _start(clients, _RESIZE_REPORTER, rows=24, cols=80)
    await _read_until(client, b"SIZE 24 80")
    client.resize(40, 120)
    assert b"SIZE 40 120" in await _read_until(client, b"SIZE 40 120")


async def test_the_child_leads_its_own_session_apart_from_the_service(
    clients: list[PtyClient],
) -> None:
    client = _start(
        clients,
        "import os, sys; sys.stdout.write('LEADER %s\\n' % (os.getsid(0) == os.getpid()));"
        " sys.stdout.flush()",
    )
    assert b"LEADER True" in await _read_until(client, b"LEADER True")


async def test_the_child_sees_only_the_environment_it_was_given(
    clients: list[PtyClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SERVICE_ONLY_VARIABLE", "service-value")
    client = spawn(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write('KEYS ' + ','.join(sorted(os.environ)) + '\\n');"
            " sys.stdout.flush()",
        ],
        env={**_ENV, "TERM": "xterm-256color"},
        rows=24,
        cols=80,
    )
    clients.append(client)
    seen = (await _read_until(client, b"KEYS ")).split(b"KEYS ")[1].decode()
    assert "SERVICE_ONLY_VARIABLE" not in seen
    assert "TERM" in seen.split("\r")[0].split(",")


def test_close_terminates_a_running_child(clients: list[PtyClient]) -> None:
    client = _start(clients, _SLEEPER)
    process = _process(client)
    assert process.poll() is None
    client.close()
    assert process.poll() is not None


def test_close_is_idempotent(clients: list[PtyClient]) -> None:
    client = _start(clients, _SLEEPER)
    client.close()
    client.close()


async def test_close_kills_a_child_that_ignores_terminate(
    clients: list[PtyClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(operator_pty, "_TERMINATE_GRACE_S", 0.2)
    client = _start(clients, _IGNORES_TERMINATE)
    await _read_until(client, b"ready")
    process = _process(client)
    client.close()
    assert process.returncode == -signal.SIGKILL


def test_close_of_a_child_that_already_exited_only_releases_the_pty(
    clients: list[PtyClient],
) -> None:
    client = _start(clients, "pass")
    process = _process(client)
    _wait_for_exit(process)
    client.close()
    assert process.returncode == 0


def test_resize_of_a_child_that_already_exited_is_harmless(clients: list[PtyClient]) -> None:
    client = _start(clients, "pass")
    _wait_for_exit(_process(client))
    client.resize(30, 90)


def test_an_unstartable_program_is_a_spawn_error() -> None:
    with pytest.raises(PtySpawnError, match="no-such-program-anywhere"):
        spawn(["no-such-program-anywhere"], env=_ENV, rows=24, cols=80)


def test_a_failing_pty_allocation_is_a_spawn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> tuple[int, int]:
        raise OSError("out of ptys")

    monkeypatch.setattr(os, "openpty", refuse)
    with pytest.raises(PtySpawnError, match="out of ptys"):
        spawn([sys.executable, "-c", "pass"], env=_ENV, rows=24, cols=80)


def test_spawn_error_is_its_own_runtime_error_type() -> None:
    assert issubclass(PtySpawnError, RuntimeError)
    assert PtySpawnError.__module__ == "alan.operator_pty"
