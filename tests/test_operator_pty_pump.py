from __future__ import annotations

import asyncio
import errno
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import cast

import pytest

from alan import operator_pty
from alan.operator_pty import PtyClient, spawn

_TIMEOUT_S = 10.0


class _Process:
    def poll(self) -> int | None:
        return 0


def _client(master: int) -> PtyClient:
    return PtyClient(cast("subprocess.Popen[bytes]", _Process()), master)


async def test_read_returns_the_bytes_written_to_the_other_end() -> None:
    read_end, write_end = os.pipe()
    client = _client(read_end)
    try:
        os.write(write_end, b"hello")
        assert await asyncio.wait_for(client.read(), _TIMEOUT_S) == b"hello"
    finally:
        os.close(write_end)
        client.close()


async def test_read_waits_until_data_arrives() -> None:
    read_end, write_end = os.pipe()
    client = _client(read_end)
    try:
        pending = asyncio.ensure_future(client.read())
        await asyncio.sleep(0.05)
        assert not pending.done()
        os.write(write_end, b"late")
        assert await asyncio.wait_for(pending, _TIMEOUT_S) == b"late"
    finally:
        os.close(write_end)
        client.close()


async def test_read_returns_empty_bytes_at_end_of_file() -> None:
    read_end, write_end = os.pipe()
    client = _client(read_end)
    os.close(write_end)
    try:
        assert await asyncio.wait_for(client.read(), _TIMEOUT_S) == b""
    finally:
        client.close()


async def test_read_returns_empty_bytes_when_the_fd_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(fd: int, size: int) -> bytes:
        raise OSError(errno.EIO, "input/output error")

    read_end, write_end = os.pipe()
    client = _client(read_end)
    os.write(write_end, b"data")
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(operator_pty, "os", SimpleNamespace(read=refuse))
            assert await asyncio.wait_for(client.read(), _TIMEOUT_S) == b""
    finally:
        os.close(write_end)
        client.close()


async def test_cancelling_a_read_removes_its_reader_and_the_next_read_still_works() -> None:
    read_end, write_end = os.pipe()
    client = _client(read_end)
    try:
        first = asyncio.ensure_future(client.read())
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        os.write(write_end, b"after-cancel")
        assert await asyncio.wait_for(client.read(), _TIMEOUT_S) == b"after-cancel"
    finally:
        os.close(write_end)
        client.close()


async def test_a_pty_child_output_arrives_through_the_pump() -> None:
    client = spawn(
        [sys.executable, "-c", "import sys; sys.stdout.write('pumped\\n'); sys.stdout.flush()"],
        env={"PATH": os.environ["PATH"]},
        rows=24,
        cols=80,
    )
    collected = b""
    try:
        while b"pumped" not in collected:
            chunk = await asyncio.wait_for(client.read(), _TIMEOUT_S)
            assert chunk
            collected += chunk
    finally:
        client.close()
    assert b"pumped" in collected
