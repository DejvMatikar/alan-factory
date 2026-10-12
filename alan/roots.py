from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

STATE_ROOT_SUBDIRS: Mapping[str, str] = {
    "ALAN_WORKSPACES_ROOT": "workspaces",
    "ALAN_LOGS_ROOT": "logs",
    "ALAN_STATE_ROOT": "state",
    "ALAN_BUILD_CHAT_SESSIONS_ROOT": "build-chat-sessions",
    "ALAN_EXPLORE_CHAT_SESSIONS_ROOT": "explore-chat-sessions",
    "ALAN_PROJECTS_ROOT": "projects",
    "ALAN_SECRETS_STORE_ROOT": "secrets",
    "ALAN_ENGINES_ROOT": "engines",
    "ALAN_PLANS_ROOT": "plans",
    "ALAN_DRIVER_LOOP_STATE_ROOT": ".",
}

STATE_ISOLATION_ROOTS: frozenset[str] = frozenset(STATE_ROOT_SUBDIRS)

SLOT_POOL_ROOT_ENV: str = "ALAN_SLOT_POOL_ROOT"

WORKTREE_REPO_ROOT_ENV: str = "ALAN_WORKTREE_REPO_ROOT"

CI_ISOLATION_ROOTS: frozenset[str] = STATE_ISOLATION_ROOTS | {
    SLOT_POOL_ROOT_ENV, WORKTREE_REPO_ROOT_ENV,
}

# Real, documented ALAN_*_ROOT tokens deliberately outside CI_ISOLATION_ROOTS:
# ALAN_SLOT_REPO_ROOT is the slot pool's clone source, unread in CI because
# ALAN_SLOT_POOL_ROOT="" already short-circuits slot_pool_root() before it is
# ever consulted.
INFRA_ROOTS: frozenset[str] = frozenset({
    "ALAN_SLOT_REPO_ROOT",
})

ALL_KNOWN_ROOTS: frozenset[str] = CI_ISOLATION_ROOTS | INFRA_ROOTS


def _state_root(token: str, subdir: str) -> Path:
    override = os.environ.get(token, "").strip()
    return Path(override) if override else Path.home() / ".alan" / subdir


def workspaces_root() -> Path:
    return _state_root("ALAN_WORKSPACES_ROOT", "workspaces")


def logs_root() -> Path:
    return _state_root("ALAN_LOGS_ROOT", "logs")


def state_root() -> Path:
    return _state_root("ALAN_STATE_ROOT", "state")


def cache_root() -> Path:
    return state_root().parent / "cache"


def deploy_marker_root() -> Path:
    return state_root().parent / "deploy"


def build_chat_sessions_root() -> Path:
    return _state_root("ALAN_BUILD_CHAT_SESSIONS_ROOT", "build-chat-sessions")


def explore_chat_sessions_root() -> Path:
    return _state_root("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", "explore-chat-sessions")


def projects_root() -> Path:
    return _state_root("ALAN_PROJECTS_ROOT", "projects")


def secrets_store_root() -> Path:
    return _state_root("ALAN_SECRETS_STORE_ROOT", "secrets")


def engines_root() -> Path:
    return _state_root("ALAN_ENGINES_ROOT", "engines")


def plans_root() -> Path:
    return _state_root("ALAN_PLANS_ROOT", "plans")


def driver_loop_state_root() -> Path:
    return _state_root("ALAN_DRIVER_LOOP_STATE_ROOT", ".")


def _optional_root(token: str) -> Path | None:
    raw = os.environ.get(token, "").strip()
    return Path(raw) if raw else None


def _optional_root_str(token: str) -> str | None:
    raw = os.environ.get(token, "").strip()
    return raw or None


def slot_pool_root() -> Path | None:
    return _optional_root(SLOT_POOL_ROOT_ENV)


def worktree_repo_root() -> Path | None:
    return _optional_root(WORKTREE_REPO_ROOT_ENV)


def slot_repo_root() -> str | None:
    return _optional_root_str("ALAN_SLOT_REPO_ROOT")
