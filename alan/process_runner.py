from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence


def _merged_env(env: Mapping[str, str | None] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = {**os.environ, **env}
    return {name: value for name, value in merged.items() if value is not None}


def run_subprocess(
    cmd: Sequence[str], *, cwd: str | None, timeout: float,
    env: Mapping[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(cmd),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_merged_env(env),
    )
