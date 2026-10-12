from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from alan import roots
from alan.driver_loop_cmd import build_env
from alan.store import write_text_atomic

SESSION_NAME: Final[str] = "alan-operator"
TERM_VALUE: Final[str] = "xterm-256color"
DEFAULT_COLUMNS: Final[int] = 200
DEFAULT_ROWS: Final[int] = 50
_COMMAND_TIMEOUT_S: Final[float] = 15.0
_SESSION_TARGET: Final[str] = f"={SESSION_NAME}"
_PANE_TARGET: Final[str] = f"={SESSION_NAME}:"

TMUX_CONF: Final[str] = "\n".join(
    (
        "set -g prefix None",
        "unbind-key C-b",
        "set -g status off",
        "set -g mouse off",
        "set -g escape-time 0",
        "set -g history-limit 100000",
        "set -g remain-on-exit on",
        "set -g window-size latest",
        'set -g default-terminal "xterm-256color"',
        "",
    )
)


def state_dir() -> Path:
    return roots.state_root() / "operator"


def socket_path() -> Path:
    return state_dir() / "tmux.sock"


def conf_path() -> Path:
    return state_dir() / "tmux.conf"


def session_env() -> dict[str, str]:
    return {**build_env(), "TERM": TERM_VALUE}


def run_tmux(
    args: Sequence[str], *, cwd: str | None = None, timeout: float = _COMMAND_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["tmux", *args],  # noqa: S607
        cwd=cwd,
        env=session_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _server_args() -> list[str]:
    return ["-f", str(conf_path()), "-S", str(socket_path())]


def _run_quiet(args: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return run_tmux([*_server_args(), *args])
    except (OSError, subprocess.TimeoutExpired):
        return None


def prepare_state_dir() -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    write_text_atomic(conf_path(), TMUX_CONF, mode=0o600)


def attach_argv() -> list[str]:
    return ["tmux", *_server_args(), "attach-session", "-d", "-t", _SESSION_TARGET]


def probe_session() -> bool | None:
    result = _run_quiet(["has-session", "-t", _SESSION_TARGET])
    return None if result is None else result.returncode == 0


def has_session() -> bool:
    return probe_session() is True


def pane_exited() -> bool:
    result = _run_quiet(["list-panes", "-t", _PANE_TARGET, "-F", "#{pane_dead}"])
    return result is not None and result.returncode == 0 and result.stdout.strip() == "1"


def new_session(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    prepare_state_dir()
    return run_tmux(
        [
            *_server_args(),
            "new-session",
            "-d",
            "-s",
            SESSION_NAME,
            "-c",
            str(cwd),
            "-x",
            str(DEFAULT_COLUMNS),
            "-y",
            str(DEFAULT_ROWS),
            shlex.join(command),
        ]
    )


def capture_pane() -> str | None:
    result = _run_quiet(["capture-pane", "-p", "-J", "-S", "-", "-t", _PANE_TARGET])
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def kill_session() -> bool:
    result = _run_quiet(["kill-session", "-t", _SESSION_TARGET])
    return result is not None and result.returncode == 0


__all__ = [
    "DEFAULT_COLUMNS",
    "DEFAULT_ROWS",
    "SESSION_NAME",
    "TERM_VALUE",
    "TMUX_CONF",
    "attach_argv",
    "capture_pane",
    "conf_path",
    "has_session",
    "kill_session",
    "new_session",
    "pane_exited",
    "prepare_state_dir",
    "probe_session",
    "run_tmux",
    "session_env",
    "socket_path",
    "state_dir",
]
