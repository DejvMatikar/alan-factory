from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard, cast

from alan import deploy_restart
from alan import served_sha as served_sha_mod

SETTLE_WINDOW_S: float = 1200.0
_GIT_TIMEOUT_S = 10.0
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHA_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class StaleLiveProcess:
    served_sha: str
    target_sha: str
    hot_paths: list[str]


def _is_sha(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return result.returncode == 0


def _committer_seconds_ago(repo: Path, sha: str, now: datetime) -> float:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%ct", sha],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=True,
    )
    committed_at = datetime.fromtimestamp(int(result.stdout.strip()), tz=UTC)
    return (now - committed_at).total_seconds()


def _cheap_silence_reason(
    *,
    served: str | None,
    target_sha: str | None,
    main_ci_status: str | None,
    deploy_status: str | None,
    deploy_obs_empty: bool,
) -> str:
    if served is None:
        return "no_served_sha"
    if not _is_sha(target_sha):
        return "target_not_sha"
    if served == target_sha:
        return "no_divergence"
    if main_ci_status != "completed":
        return "main_ci_incomplete"
    if deploy_obs_empty:
        return "deploy_unknown"
    if deploy_status != "completed":
        return "deploy_in_flight"
    return ""


def _git_silence_reason(repo: Path, served: str, target: str, now: datetime) -> str:
    if not deploy_restart.marker_reachable(repo, target):
        return "target_unreachable"
    if not _is_ancestor(repo, served, target):
        return "not_ancestor"
    if _committer_seconds_ago(repo, target, now) < SETTLE_WINDOW_S:
        return "within_settle_window"
    return ""


def detect(
    *,
    target_sha: str | None,
    main_ci_status: str | None,
    deploy_status: str | None,
    deploy_obs_empty: bool,
    now: datetime,
    repo: Path = _REPO_ROOT,
) -> tuple[StaleLiveProcess | None, str]:
    try:
        served = served_sha_mod.served_sha()
        cheap = _cheap_silence_reason(
            served=served,
            target_sha=target_sha,
            main_ci_status=main_ci_status,
            deploy_status=deploy_status,
            deploy_obs_empty=deploy_obs_empty,
        )
        if cheap:
            return None, cheap
        served_str = cast(str, served)
        target_str = cast(str, target_sha)
        git_reason = _git_silence_reason(repo, served_str, target_str, now)
        if git_reason:
            return None, git_reason
        is_hot, reason, paths = deploy_restart.classify_range(repo, served_str, target_str)
        if not is_hot:
            return None, reason
        hot_paths = [p for p in paths if not deploy_restart.is_safe_path(p)]
        return StaleLiveProcess(served_str, target_str, hot_paths), ""
    except Exception:  # noqa: BLE001 - detection must fail silent, never crash the sweep
        return None, "error"


__all__ = ["SETTLE_WINDOW_S", "StaleLiveProcess", "detect"]
