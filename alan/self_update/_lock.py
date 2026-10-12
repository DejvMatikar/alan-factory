from __future__ import annotations

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager

from alan import roots


@contextmanager
def acquire() -> Generator[bool, None, None]:
    root = roots.deploy_marker_root()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(root), os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        _make_inheritable(fd)
        yield True
    finally:
        os.close(fd)


def _make_inheritable(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
