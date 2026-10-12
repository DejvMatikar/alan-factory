from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast

from alan import process_runner, roots

_GH_TIMEOUT_S = 30.0
_FAILED = "github read failed"
_TRANSPORT_PURPOSE = "transport"


class GithubReadError(RuntimeError):
    pass


def _record_path(owner: str, repo: str) -> Path:
    digest = hashlib.sha256(f"{owner}/{repo}\x00{_TRANSPORT_PURPOSE}".encode()).hexdigest()
    return roots.secrets_store_root() / f"{digest}.json"


def _stored_value(owner: str, repo: str) -> object:
    try:
        payload: object = json.loads(_record_path(owner, repo).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast("dict[str, object]", payload).get("value")


def transport_token(owner: str, repo: str) -> str | None:
    value = _stored_value(owner, repo)
    token = value.strip() if isinstance(value, str) else ""
    return token or None


def _transport_env(owner: str, repo: str) -> dict[str, str]:
    token = transport_token(owner, repo)
    if token is None:
        raise GithubReadError(f"no transport credential for {owner}/{repo}")
    return {"GH_TOKEN": token}


def _decode(stdout: str) -> object:
    try:
        payload: object = json.loads(stdout)
    except ValueError:
        raise GithubReadError(_FAILED) from None
    return payload


def _get(owner: str, repo: str, path: str) -> object:
    env = _transport_env(owner, repo)
    try:
        result = process_runner.run_subprocess(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", path],
            cwd=None,
            timeout=_GH_TIMEOUT_S,
            env=env,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise GithubReadError(_FAILED) from None
    if result.returncode != 0:
        raise GithubReadError(_FAILED)
    return _decode(result.stdout)


def _items(payload: object, key: str) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        raise GithubReadError(_FAILED)
    items = cast("dict[str, object]", payload).get(key)
    if not isinstance(items, list):
        raise GithubReadError(_FAILED)
    rows = cast("list[object]", items)
    if not all(isinstance(row, dict) for row in rows):
        raise GithubReadError(_FAILED)
    return cast("list[dict[str, object]]", rows)


def push_runs(owner: str, repo: str, sha: str) -> list[dict[str, object]]:
    query = f"event=push&branch=main&head_sha={sha}&per_page=100"
    path = f"repos/{owner}/{repo}/actions/workflows/tests.yml/runs?{query}"
    return _items(_get(owner, repo, path), "workflow_runs")


def gate_check_runs(owner: str, repo: str, suite_id: int) -> list[dict[str, object]]:
    query = "check_name=gate&filter=latest&per_page=100"
    path = f"repos/{owner}/{repo}/check-suites/{suite_id}/check-runs?{query}"
    return _items(_get(owner, repo, path), "check_runs")
