from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import TypeAdapter

from alan import _chat_core, roots
from alan._chat_core import (
    ChatBrief as ExploreChatBrief,
)
from alan._chat_core import (
    ChatInFlightTurn as ExploreChatInFlightTurn,
)
from alan._chat_core import (
    ChatMessage as ExploreChatMessage,
)
from alan._chat_core import (
    ChatSession,
    ChatSessionSummary,
)
from alan.explore_chat_repo import RepoProvisionStatus

__all__ = [
    "ExploreChatBrief",
    "ExploreChatError",
    "ExploreChatInFlightTurn",
    "ExploreChatLocked",
    "ExploreChatMessage",
    "ExploreChatSession",
    "ExploreChatSessionLocks",
    "ExploreChatSessionSummary",
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
    "reconcile_in_flight_turns",
    "repo_dir",
    "resolve_sessions_root",
    "save_brief",
    "session_dir",
    "set_repo_provision",
    "stream_path",
    "sweep_legacy_engine_keys",
    "validate_consultant_key",
    "validate_session_id",
]

_CONSULTANT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PREVIEW_MAX = 120

logger = logging.getLogger("alan.explore_chat")

_write_json = _chat_core.write_json


class ExploreChatError(_chat_core.ChatCoreError):
    pass


class ExploreChatLocked(ExploreChatError):
    pass


class ExploreChatSession(ChatSession):
    consultant_key: str
    repo_provision: RepoProvisionStatus = RepoProvisionStatus.UNCHECKED


class ExploreChatSessionSummary(ChatSessionSummary):
    consultant_key: str


CONFIG: _chat_core.ChatCoreConfig[ExploreChatSession] = _chat_core.ChatCoreConfig(
    sessions_root=roots.explore_chat_sessions_root,
    logger=logger,
    label="explore-chat",
    error=ExploreChatError,
    session_type=ExploreChatSession,
    session_adapter=TypeAdapter(ExploreChatSession),
)


def validate_session_id(session_id: str) -> None:
    _chat_core.validate_session_id(CONFIG, session_id)


def validate_consultant_key(key: str) -> None:
    if not _CONSULTANT_KEY_RE.fullmatch(key):
        raise ExploreChatError(f"invalid consultant_key: {key}")


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
) -> ExploreChatSession:
    return _chat_core.update_session(CONFIG, session_id, {"repo_provision": status}, root=root)


def create_session(
    consultant_key: str,
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> ExploreChatSession:
    validate_consultant_key(consultant_key)
    return _chat_core.create_session(
        CONFIG,
        root=root,
        consultant_key=consultant_key,
        project_id=project_id,
        engine_key=engine_key,
    )


def load_session(session_id: str, *, root: Path | None = None) -> ExploreChatSession:
    return _chat_core.load_session(CONFIG, session_id, root=root)


def append_user_message_and_mark_started(
    session: ExploreChatSession, message: ExploreChatMessage, *, root: Path | None = None
) -> ExploreChatSession:
    return _chat_core.append_user_message_and_mark_started(CONFIG, session, message, root=root)


def append_assistant_and_clear_in_flight(
    session: ExploreChatSession, message: ExploreChatMessage, *, root: Path | None = None
) -> ExploreChatSession:
    return _chat_core.append_assistant_and_clear_in_flight(CONFIG, session, message, root=root)


def clear_in_flight_turn(session_id: str, *, root: Path | None = None) -> ExploreChatSession:
    return _chat_core.clear_in_flight_turn(CONFIG, session_id, root=root)


def list_sessions(
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> list[ExploreChatSessionSummary]:
    def _mapper(s: ExploreChatSession) -> ExploreChatSessionSummary:
        preview = s.transcript[-1].content[:_PREVIEW_MAX] if s.transcript else ""
        return ExploreChatSessionSummary(
            session_id=s.session_id,
            created_at=s.created_at,
            last_message_preview=preview,
            in_flight=s.in_flight_turn is not None,
            consultant_key=s.consultant_key,
            engine_key=s.engine_key,
        )
    return _chat_core.list_sessions_mapped(
        CONFIG, _mapper, root=root, project_id=project_id, engine_key=engine_key
    )


def delete_session(session_id: str, *, root: Path | None = None) -> None:
    _chat_core.delete_session(CONFIG, session_id, root=root)


def load_brief(session_id: str, *, root: Path | None = None) -> ExploreChatBrief | None:
    return _chat_core.load_brief(CONFIG, session_id, root=root)


def save_brief(session_id: str, content: str, *, root: Path | None = None) -> ExploreChatBrief:
    return _chat_core.save_brief(CONFIG, session_id, content, root=root)


def reconcile_in_flight_turns(*, root: Path | None = None) -> list[str]:
    return _chat_core.reconcile_in_flight_turns(CONFIG, root=root)


def sweep_legacy_engine_keys(*, root: Path | None = None) -> list[str]:
    return _chat_core.sweep_legacy_engine_keys(CONFIG, root=root)


class ExploreChatSessionLocks(_chat_core.ChatSessionLocks):
    def __init__(self) -> None:
        super().__init__(ExploreChatLocked, "explore-chat")
