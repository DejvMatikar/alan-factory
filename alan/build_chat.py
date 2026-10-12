from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from alan import _chat_core, roots
from alan._chat_core import (
    ChatBrief as BuildChatBrief,
)
from alan._chat_core import (
    ChatInFlightTurn as BuildChatInFlightTurn,
)
from alan._chat_core import (
    ChatMessage as BuildChatMessage,
)
from alan._chat_core import (
    ChatSession,
)
from alan._chat_core import (
    ChatSessionSummary as BuildChatSessionSummary,
)
from alan.explore_chat_repo import RepoProvisionStatus

__all__ = [
    "BuildChatBrief",
    "BuildChatError",
    "BuildChatInFlightTurn",
    "BuildChatLocked",
    "BuildChatMessage",
    "BuildChatSession",
    "BuildChatSessionLocks",
    "BuildChatSessionSummary",
    "RepoProvisionStatus",
    "append_assistant_and_clear_in_flight",
    "append_user_message_and_mark_started",
    "clear_in_flight_turn",
    "create_session",
    "delete_session",
    "list_sessions",
    "load_brief",
    "load_session",
    "logger",
    "reap_stale_repo_checkouts",
    "reconcile_in_flight_turns",
    "repo_dir",
    "resolve_sessions_root",
    "save_brief",
    "session_dir",
    "set_repo_provision",
    "stream_path",
    "sweep_legacy_engine_keys",
    "validate_session_id",
]

logger = logging.getLogger("alan.build_chat")

_write_json = _chat_core.write_json

_REPO_REAP_TTL_S = 24 * 3600

_REAPABLE_STATUSES = frozenset({RepoProvisionStatus.READY, RepoProvisionStatus.ERROR})


class BuildChatError(_chat_core.ChatCoreError):
    pass


class BuildChatLocked(BuildChatError):
    pass


class BuildChatSession(ChatSession):
    repo_provision: RepoProvisionStatus = RepoProvisionStatus.UNCHECKED


CONFIG: _chat_core.ChatCoreConfig[BuildChatSession] = _chat_core.ChatCoreConfig(
    sessions_root=roots.build_chat_sessions_root,
    logger=logger,
    label="build-chat",
    error=BuildChatError,
    session_type=BuildChatSession,
    session_adapter=TypeAdapter(BuildChatSession),
)


def validate_session_id(session_id: str) -> None:
    _chat_core.validate_session_id(CONFIG, session_id)


def resolve_sessions_root() -> Path:
    return _chat_core.resolve_sessions_root(CONFIG)


def session_dir(session_id: str, *, root: Path | None = None) -> Path:
    return _chat_core.session_dir(CONFIG, session_id, root=root)


def stream_path(session_id: str, *, root: Path | None = None) -> Path:
    return _chat_core.stream_path(CONFIG, session_id, root=root)


def repo_dir(session_id: str, *, root: Path | None = None) -> Path:
    return session_dir(session_id, root=root) / "repo"


def set_repo_provision(
    session_id: str, status: RepoProvisionStatus, *, root: Path | None = None
) -> BuildChatSession:
    return _chat_core.update_session(CONFIG, session_id, {"repo_provision": status}, root=root)


def create_session(
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> BuildChatSession:
    return _chat_core.create_session(
        CONFIG, root=root, project_id=project_id, engine_key=engine_key
    )


def load_session(session_id: str, *, root: Path | None = None) -> BuildChatSession:
    return _chat_core.load_session(CONFIG, session_id, root=root)


def append_user_message_and_mark_started(
    session: BuildChatSession, message: BuildChatMessage, *, root: Path | None = None
) -> BuildChatSession:
    return _chat_core.append_user_message_and_mark_started(CONFIG, session, message, root=root)


def append_assistant_and_clear_in_flight(
    session: BuildChatSession, message: BuildChatMessage, *, root: Path | None = None
) -> BuildChatSession:
    return _chat_core.append_assistant_and_clear_in_flight(CONFIG, session, message, root=root)


def clear_in_flight_turn(session_id: str, *, root: Path | None = None) -> BuildChatSession:
    return _chat_core.clear_in_flight_turn(CONFIG, session_id, root=root)


def list_sessions(
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> list[BuildChatSessionSummary]:
    return _chat_core.list_sessions(
        CONFIG, root=root, project_id=project_id, engine_key=engine_key
    )


def delete_session(session_id: str, *, root: Path | None = None) -> None:
    _chat_core.delete_session(CONFIG, session_id, root=root)


def load_brief(session_id: str, *, root: Path | None = None) -> BuildChatBrief | None:
    return _chat_core.load_brief(CONFIG, session_id, root=root)


def save_brief(session_id: str, content: str, *, root: Path | None = None) -> BuildChatBrief:
    return _chat_core.save_brief(CONFIG, session_id, content, root=root)


def reconcile_in_flight_turns(*, root: Path | None = None) -> list[str]:
    return _chat_core.reconcile_in_flight_turns(CONFIG, root=root)


def sweep_legacy_engine_keys(*, root: Path | None = None) -> list[str]:
    return _chat_core.sweep_legacy_engine_keys(CONFIG, root=root)


def _session_idle_at(session: BuildChatSession) -> datetime:
    if session.transcript:
        return session.transcript[-1].ts
    return session.created_at


def _is_reapable(session: BuildChatSession, now: datetime) -> bool:
    if session.repo_provision not in _REAPABLE_STATUSES:
        return False
    idle = _session_idle_at(session)
    return (now - idle).total_seconds() > _REPO_REAP_TTL_S


def reap_stale_repo_checkouts(
    *, root: Path | None = None, now: datetime | None = None
) -> list[str]:
    effective_now = now if now is not None else datetime.now(UTC)
    reaped: list[str] = []
    for session in _chat_core.iter_all_valid_sessions(CONFIG, root=root):
        if not _is_reapable(session, effective_now):
            continue
        shutil.rmtree(repo_dir(session.session_id, root=root), ignore_errors=True)
        set_repo_provision(session.session_id, RepoProvisionStatus.UNCHECKED, root=root)
        reaped.append(session.session_id)
    return reaped


class BuildChatSessionLocks(_chat_core.ChatSessionLocks):
    def __init__(self) -> None:
        super().__init__(BuildChatLocked, "build-chat")
