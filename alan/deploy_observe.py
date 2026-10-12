from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

from alan.deploy_restart import is_safe_path
from alan.deploy_verify import DeployArm, DeployObservation
from alan.served_sha import served_sha

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIR = _REPO_ROOT / "alan" / "web"
_DEPLOY_SHA_FILENAME = ".deploy_sha"
_GIT_TIMEOUT_S = 10.0
_HEALTH_TIMEOUT_S = 10.0


def checkout_head(repo: Path) -> str | None:
    return _rev_parse(repo, "HEAD")


def _rev_parse(repo: Path, rev: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", rev],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def active_slot_sha(web_dir: Path) -> str | None:
    dist = web_dir / "dist"
    try:
        slot_dir = (web_dir / os.readlink(dist)) if dist.is_symlink() else dist
        content = (slot_dir / _DEPLOY_SHA_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


def live_served_sha() -> str | None:
    return served_sha()


def probe_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=_HEALTH_TIMEOUT_S) as resp:  # noqa: S310  # nosec B310
            if resp.status != 200:
                return False
            body = resp.read()
            data: object = json.loads(body)
            if not isinstance(data, dict):
                return False
            return bool(cast("dict[str, object]", data).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _diff_tree_paths(repo: Path, sha: str) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo), "diff-tree",
                "--no-commit-id", "--name-only", "-r", "-z", sha,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.split("\0") if p]


def classify_arm(repo: Path, merge_sha: str) -> DeployArm:
    paths = _diff_tree_paths(repo, merge_sha)
    if paths and all(is_safe_path(p) for p in paths):
        return "restart_free"
    return "restart_required"


def observe(
    merge_sha: str,
    *,
    health_url: str | None,
    repo: Path = _REPO_ROOT,
    web_dir: Path = _WEB_DIR,
) -> DeployObservation:
    arm = classify_arm(repo, merge_sha)
    if arm == "restart_required":
        served = live_served_sha()
        health_ok = probe_health(health_url) if health_url is not None else True
        return DeployObservation(
            arm=arm,
            checkout_contains_merge=False,
            slot_contains_merge=False,
            served_contains_merge=served is not None and is_ancestor(repo, merge_sha, served),
            health_ok=health_ok,
        )
    checkout = checkout_head(repo)
    slot = active_slot_sha(web_dir)
    return DeployObservation(
        arm=arm,
        checkout_contains_merge=checkout is not None and is_ancestor(repo, merge_sha, checkout),
        slot_contains_merge=slot is not None and is_ancestor(repo, merge_sha, slot),
        served_contains_merge=False,
        health_ok=True,
    )


__all__ = [
    "active_slot_sha",
    "checkout_head",
    "classify_arm",
    "is_ancestor",
    "live_served_sha",
    "observe",
    "probe_health",
]
