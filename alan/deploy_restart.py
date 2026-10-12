from __future__ import annotations

import re
import subprocess
from pathlib import Path

SAFE_DIR_PREFIXES: tuple[str, ...] = ("docs/", "tests/", "alan/web/")
SAFE_SUFFIXES: tuple[str, ...] = (".md",)

REASON_MISSING_MARKER: str = "missing_marker"


def is_safe_path(path: str) -> bool:
    return path.endswith(SAFE_SUFFIXES) or path.startswith(SAFE_DIR_PREFIXES)


def changed_paths(repo: Path, marker_sha: str, rev: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-renames", "-z", "--name-only", marker_sha, rev],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in result.stdout.split("\0") if p]


def marker_reachable(repo: Path, sha: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        return False
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _paths_verdict(paths: list[str]) -> tuple[bool, str]:
    if not paths:
        return False, "no_changes"
    if all(is_safe_path(p) for p in paths):
        return False, "safe_paths_only"
    return True, "hot_paths"


def classify_range(repo: Path, base: str, rev: str = "HEAD") -> tuple[bool, str, list[str]]:
    paths = changed_paths(repo, base, rev)
    return (*_paths_verdict(paths), paths)


def decide(
    repo: Path, marker: Path, service_loaded: bool, *, rev: str = "HEAD"
) -> tuple[bool, str, list[str]]:
    try:
        if not service_loaded:
            return True, "service_not_loaded", []
        if not marker.is_file():
            return True, REASON_MISSING_MARKER, []
        marker_sha = marker.read_text(encoding="utf-8").strip()
        if not marker_reachable(repo, marker_sha):
            return True, "unknown_marker_sha", []
        return classify_range(repo, marker_sha, rev)
    except Exception:  # noqa: BLE001 - classification must never crash the deploy
        return True, "classifier_error", []
