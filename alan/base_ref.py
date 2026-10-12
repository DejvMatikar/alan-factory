from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

RESOLVED_BASE_REF = "origin/main"

_CRED_RESET_ARGS: tuple[str, ...] = ("-c", "credential.helper=")
_DEFAULT_TIMEOUT_S = 30.0


def resolve(
    cwd: Path, *, env: Mapping[str, str] | None = None, timeout_s: float = _DEFAULT_TIMEOUT_S
) -> str | None:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    argv = (
        ["git", *_CRED_RESET_ARGS, "fetch", "origin", "main"]
        if env is not None
        else ["git", "fetch", "origin", "main"]
    )
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=full_env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return RESOLVED_BASE_REF


__all__ = ["RESOLVED_BASE_REF", "resolve"]
