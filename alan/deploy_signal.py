from __future__ import annotations

import subprocess
from pathlib import Path

from alan.served_sha import served_sha

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GIT_TIMEOUT_S = 10.0


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return result.returncode == 0


def merge_is_served(merge_sha: str, *, repo: Path = _REPO_ROOT) -> bool:
    served = served_sha()
    if served is None:
        return False
    try:
        return _is_ancestor(repo, merge_sha, served)
    except (OSError, subprocess.TimeoutExpired):
        return False


__all__ = ["merge_is_served"]
