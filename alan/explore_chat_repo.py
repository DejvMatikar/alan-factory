from __future__ import annotations

import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

from alan import engine_config, secrets_store
from alan.claude_cli_grants import OWN_REPO_LABEL
from alan.engine_config import ForgeTarget
from alan.process_runner import run_subprocess

__all__ = [
    "RepoProvisionError",
    "RepoProvisionStatus",
    "needs_repo",
    "provision",
    "repo_label",
]

_CLONE_TIMEOUT_S = 120.0


class RepoProvisionError(RuntimeError):
    pass


class RepoProvisionStatus(StrEnum):
    UNCHECKED = "unchecked"
    PENDING = "pending"
    READY = "ready"
    NOT_NEEDED = "not_needed"
    ERROR = "error"


def needs_repo(engine_key: str | None) -> str | None:
    if engine_key is None:
        return None
    if engine_config.lookup_by_slug(engine_key) is None:
        return None
    if engine_config.canonical_scope_key(engine_key) == engine_config.canonical_scope_key(None):
        return None
    return engine_key


def repo_label(engine_key: str | None) -> str:
    if engine_key is not None:
        cfg = engine_config.lookup_by_slug(engine_key)
        if cfg is not None:
            return cfg.repo.slug
    return OWN_REPO_LABEL


def _clone_repo(forge: ForgeTarget, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    remote_url = f"https://github.com/{forge.repo.slug}.git"
    try:
        env = secrets_store.transport_env(forge)
    except secrets_store.MissingSecretError as exc:
        raise RepoProvisionError(str(exc)) from exc
    try:
        result = run_subprocess(
            ["git", *secrets_store.GIT_CRED_RESET_ARGS, "clone", "--", remote_url, str(dest)],
            cwd=None,
            timeout=_CLONE_TIMEOUT_S,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RepoProvisionError(str(exc)) from exc
    if result.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise RepoProvisionError(
            f"git clone failed (rc={result.returncode}): {(result.stderr or '')[:300]}"
        )


def provision(engine_key: str, dest: Path) -> None:
    cfg = engine_config.lookup_by_slug(engine_key)
    if cfg is None or cfg.forge is None:
        raise RepoProvisionError(f"engine {engine_key!r} has no forge configured")
    _clone_repo(ForgeTarget.from_forge_config(cfg.forge), dest)
