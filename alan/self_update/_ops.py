from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from alan import process_runner, roots, store
from alan.self_update import _origin

SERVICE_LABEL = "ai.alan"
RENDER_REFUSED_EXIT = 78
RENDER_ACCEPTED_EXITS = frozenset({0, RENDER_REFUSED_EXIT})
MARKER_FILE = "restarted_sha"
GIT_TIMEOUT_S = 60.0
LAUNCHCTL_TIMEOUT_S = 15.0
RENDER_TIMEOUT_S = 60.0
SWAP_TIMEOUT_S = 60.0
SYNC_TIMEOUT_S = 900.0
HEALTH_TIMEOUT_S = 5.0
HEALTH_BODY_LIMIT = 1024 * 1024
SLOTS = ("dist.a", "dist.b")
DEPLOY_SHA_FILE = ".deploy_sha"
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}
_RUNS_RE = re.compile(r"^\s*runs\s*=\s*(\d+)\s*$", re.MULTILINE)

monotonic: Callable[[], float] = time.monotonic
sleep: Callable[[float], None] = time.sleep
urlopen = urllib.request.urlopen


def domain() -> str:
    return f"gui/{os.getuid()}"


def label() -> str:
    return f"{domain()}/{SERVICE_LABEL}"


def poll(probe: Callable[[], bool], bound_s: float, interval_s: float) -> bool:
    deadline = monotonic() + bound_s
    while not probe():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep(min(interval_s, remaining))
    return True


def run_step(
    argv: list[str],
    timeout_s: float,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    return process_runner.run_subprocess(
        argv, cwd=str(cwd) if cwd is not None else None, timeout=timeout_s, env=env
    )


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return run_step(["launchctl", *args], LAUNCHCTL_TIMEOUT_S)


def service_status() -> tuple[bool, int | None]:
    result = launchctl("print", label())
    if result.returncode != 0:
        return False, None
    match = _RUNS_RE.search(result.stdout)
    return True, (int(match.group(1)) if match is not None else None)


def service_loaded() -> bool:
    return service_status()[0]


UNLOAD_BOUND_S = 15.0
UNLOAD_POLL_S = 1.0


def teardown_service() -> None:
    if not service_loaded():
        return
    launchctl("bootout", label())
    if not poll(lambda: not service_loaded(), UNLOAD_BOUND_S, UNLOAD_POLL_S):
        raise RuntimeError("service did not unload")


def bootstrap_service(plist_path: Path) -> None:
    launchctl("enable", label())
    result = launchctl("bootstrap", domain(), str(plist_path))
    if result.returncode != 0:
        raise RuntimeError("launchctl bootstrap failed")


def kickstart_service() -> None:
    result = launchctl("kickstart", "-p", label())
    if result.returncode != 0:
        raise RuntimeError("launchctl kickstart failed")


def restart_service(repo: Path, plist_path: Path) -> None:
    render_launch_config(repo)
    teardown_service()
    bootstrap_service(plist_path)
    kickstart_service()


def sync_dependencies(repo: Path) -> None:
    result = run_step(["uv", "sync", "--locked"], SYNC_TIMEOUT_S, cwd=repo)
    if result.returncode != 0:
        raise RuntimeError("uv sync failed during rollback")


def render_launch_config(repo: Path) -> None:
    resolved = _origin.resolve_origin(repo)
    if resolved is None:
        return
    script = repo / "deploy" / "render_plist.sh"
    result = run_step(
        ["bash", str(script)],
        RENDER_TIMEOUT_S,
        cwd=repo,
        env=_origin.pinned_environment(*resolved),
    )
    if result.returncode not in RENDER_ACCEPTED_EXITS:
        raise RuntimeError("render_plist.sh failed")


def is_dirty(repo: Path) -> bool:
    status = git(repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=normal")
    return bool(status.strip())


def git(repo: Path, *args: str) -> str:
    result = process_runner.run_subprocess(
        ["git", "-C", str(repo), *args], cwd=None, timeout=GIT_TIMEOUT_S, env=_GIT_ENV
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args])
    return result.stdout


def web_dir(repo: Path) -> Path:
    return repo / "alan" / "web"


def previous_slot(web: Path) -> str | None:
    dist = web / "dist"
    if not dist.is_symlink():
        return None
    target = os.readlink(dist)
    return target if target in SLOTS else None


def active_slot_sha(web: Path) -> str | None:
    try:
        text = (web / "dist" / DEPLOY_SHA_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def swap(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = repo / "scripts" / "swap_web_dist.py"
    argv = [sys.executable, str(script), "--web-dir", str(web_dir(repo)), *args]
    return run_step(argv, SWAP_TIMEOUT_S)


def activate_slot(repo: Path, slot: str, sha: str) -> None:
    result = swap(repo, "activate", "--slot", slot, "--sha", sha)
    if result.returncode != 0:
        raise RuntimeError("web slot activation failed")


def _valid_health_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _fetch_health(url: str) -> bytes | None:
    if not _valid_health_url(url):
        return None
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_S) as response:
            if response.status != 200:
                return None
            return cast("bytes", response.read(HEALTH_BODY_LIMIT + 1))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return None


def _reports_target(body: bytes | None, target: str) -> bool:
    if body is None or len(body) > HEALTH_BODY_LIMIT:
        return False
    try:
        payload: object = json.loads(body)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    fields = cast("dict[str, object]", payload)
    return fields.get("ok") is True and fields.get("served_sha") == target


def health_ok(url: str, target: str) -> bool:
    return _reports_target(_fetch_health(url), target)


def marker_path() -> Path:
    return roots.deploy_marker_root() / MARKER_FILE


def write_marker(sha: str) -> None:
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_atomic(path, f"{sha}\n")
