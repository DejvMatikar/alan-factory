from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

from alan import deploy_restart, process_runner, roots, secrets_store
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import JsonObject, active_forge

_LAUNCHCTL_TIMEOUT_S = 2.0
_GH_TIMEOUT_S = 60.0
_FETCH_TIMEOUT_S = 30.0
_SERVICE_LABEL = "ai.alan"
_MARKER_FILENAME = "restarted_sha"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _service_loaded() -> bool:
    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{_SERVICE_LABEL}"],
            capture_output=True,
            text=True,
            timeout=_LAUNCHCTL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _parse_merge_commit_sha(fields: JsonObject) -> str | None:
    merge_commit = fields.get("mergeCommit")
    if not isinstance(merge_commit, dict):
        return None
    oid = cast(JsonObject, merge_commit).get("oid")
    return oid if isinstance(oid, str) and oid else None


def _merge_commit_sha(pr_number: int) -> str | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("mergeCommit",),
            forge_target=DEFAULT_FORGE_TARGET, timeout=_GH_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not read.ok:
        return None
    return _parse_merge_commit_sha(read.fields)


def _fetch_commit(repo: Path, sha: str) -> bool:
    try:
        result = process_runner.run_subprocess(
            [
                "git", *secrets_store.GIT_CRED_RESET_ARGS, "-C", str(repo),
                "fetch", "--no-tags", "--quiet", "origin", sha,
            ],
            cwd=None,
            timeout=_FETCH_TIMEOUT_S,
            env=secrets_store.transport_env(forge_target=DEFAULT_FORGE_TARGET),
        )
    except secrets_store.MissingSecretError:
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def classify_merge(pr_number: int) -> tuple[bool, str, str | None]:
    try:
        marker = roots.deploy_marker_root() / _MARKER_FILENAME
        if not marker.is_file():
            return False, deploy_restart.REASON_MISSING_MARKER, None
        sha = _merge_commit_sha(pr_number)
        if sha is None:
            return False, "merge_sha_unavailable", None
        repo = _repo_root()
        if not _fetch_commit(repo, sha):
            return False, "fetch_unavailable", sha
        restart_required, reason, _paths = deploy_restart.decide(
            repo, marker, _service_loaded(), rev=sha
        )
        return not restart_required, reason, sha
    except Exception:  # noqa: BLE001 - classification must never crash the deploy gate
        return False, "classifier_error", None


__all__ = ["classify_merge"]
