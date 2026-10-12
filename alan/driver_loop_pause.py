from __future__ import annotations

from pathlib import Path

from alan.detached_mode import resolve_detached_mode
from alan.roots import driver_loop_state_root


class DriverLoopPauseError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def pause_dir() -> Path:
    return driver_loop_state_root() / "driver_loops" / "pause"


def pause_file(name: str) -> Path:
    return pause_dir() / f"{name}.pause"


def is_paused(name: str) -> bool:
    return pause_file(name).is_file()


def pause(name: str) -> None:
    if not resolve_detached_mode():
        raise DriverLoopPauseError("not_supervised")
    path = pause_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def resume(name: str) -> None:
    if not resolve_detached_mode():
        raise DriverLoopPauseError("not_supervised")
    pause_file(name).unlink(missing_ok=True)


__all__ = [
    "DriverLoopPauseError",
    "is_paused",
    "pause",
    "pause_dir",
    "pause_file",
    "resume",
]
