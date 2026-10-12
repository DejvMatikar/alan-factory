from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

import alan.build_chat as build_chat_mod
import alan.prep_chat as prep_chat_mod
from alan.queue import Queue
from alan.workspace import Status, create, transition
from alan.workspace_index import WorkspaceIndex

_UNKNOWN_SESSION_ID = "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def index(ws_root: Path, queue: Queue) -> WorkspaceIndex:
    return WorkspaceIndex(root=ws_root, queue=queue)


def _archive_session(ws_path: Path, bc_root: Path, *, title: str = "Prep") -> str:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title=title, root=bc_root
    )
    return session.session_id


def test_resolve_archived_prep_chat_returns_none_for_unknown_session_id(
    index: WorkspaceIndex,
) -> None:
    assert index.resolve_archived_prep_chat(_UNKNOWN_SESSION_ID) is None


def test_resolve_archived_prep_chat_finds_session_for_a_live_workspace(
    index: WorkspaceIndex, ws_root: Path, bc_root: Path
) -> None:
    ws = create("Live WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root)
    assert index.resolve_archived_prep_chat(session_id) == ws.task_id


def test_resolve_archived_prep_chat_returns_none_for_a_never_archived_session_id(
    index: WorkspaceIndex, ws_root: Path, bc_root: Path
) -> None:
    create("Live WS", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    assert index.resolve_archived_prep_chat(session.session_id) is None


def test_resolve_archived_prep_chat_self_heals_a_cold_discovered_archived_workspace(
    ws_root: Path, bc_root: Path, queue: Queue
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root)
    transition(ws, Status.ARCHIVED)

    fresh = WorkspaceIndex(root=ws_root, queue=queue)
    assert fresh.resolve_archived_prep_chat(session_id) == ws.task_id


def test_resolve_archived_prep_chat_survives_a_simulated_restart(
    ws_root: Path, bc_root: Path, queue: Queue
) -> None:
    ws = create("Archived WS", "d", root=ws_root)
    session_id = _archive_session(ws.path, bc_root)
    transition(ws, Status.ARCHIVED)

    before_restart = WorkspaceIndex(root=ws_root, queue=queue)
    assert before_restart.resolve_archived_prep_chat(session_id) == ws.task_id

    after_restart = WorkspaceIndex(root=ws_root, queue=queue)
    assert after_restart.resolve_archived_prep_chat(session_id) == ws.task_id


def test_resolve_archived_prep_chat_refreshes_after_a_second_workspace_is_created(
    index: WorkspaceIndex, ws_root: Path, bc_root: Path
) -> None:
    first = create("First WS", "d", root=ws_root)
    first_session = _archive_session(first.path, bc_root, title="First")
    assert index.resolve_archived_prep_chat(first_session) == first.task_id

    second = create("Second WS", "d", root=ws_root)
    second_session = _archive_session(second.path, bc_root, title="Second")
    assert index.resolve_archived_prep_chat(second_session) == second.task_id
    assert index.resolve_archived_prep_chat(first_session) == first.task_id
