from __future__ import annotations

import functools
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


@functools.cache
def served_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
