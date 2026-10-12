from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import signal
import struct
import subprocess
import termios
from collections.abc import Mapping, Sequence
from typing import Final

_READ_CHUNK: Final[int] = 65536
_TERMINATE_GRACE_S: Final[float] = 2.0


class PtySpawnError(RuntimeError):
    pass


def _set_window_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _mark_ready(ready: asyncio.Future[None]) -> None:
    with contextlib.suppress(asyncio.InvalidStateError):
        ready.set_result(None)


class PtyClient:
    def __init__(self, process: subprocess.Popen[bytes], master_fd: int) -> None:
        self._process = process
        self._master = master_fd
        self._closed = False

    async def read(self) -> bytes:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        loop.add_reader(self._master, _mark_ready, ready)
        try:
            await ready
        finally:
            loop.remove_reader(self._master)
        try:
            return os.read(self._master, _READ_CHUNK)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._master, view) :]

    def resize(self, rows: int, cols: int) -> None:
        _set_window_size(self._master, rows, cols)
        self._process.send_signal(signal.SIGWINCH)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_process()
        os.close(self._master)

    def _stop_process(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


def spawn(
    argv: Sequence[str], *, env: Mapping[str, str], rows: int, cols: int
) -> PtyClient:
    try:
        master, slave = os.openpty()
    except OSError as exc:
        raise PtySpawnError(f"could not allocate a pty: {exc}") from exc
    try:
        _set_window_size(master, rows, cols)
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=dict(env),
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        os.close(master)
        raise PtySpawnError(f"could not start {argv[0]}: {exc}") from exc
    finally:
        os.close(slave)
    return PtyClient(process, master)


__all__ = ["PtyClient", "PtySpawnError", "spawn"]
