from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
from alan._chat_core import ChatMessage
from alan.prep_chat import PrepChatKind
from alan.routes._brief_refine_source import write_brief_refine_source
from alan.teams.brief_refine import SOURCE_FILE_NAME
from alan.workspace import create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


def test_missing_kind_writes_no_file(ws_root: Path, bc_root: Path, ec_root: Path) -> None:
    ws = create("T", "d", root=ws_root)
    asyncio.run(write_brief_refine_source(None, "abc", ws, bc_root, ec_root))
    assert not (ws.path / SOURCE_FILE_NAME).exists()


def test_missing_session_id_writes_no_file(ws_root: Path, bc_root: Path, ec_root: Path) -> None:
    ws = create("T", "d", root=ws_root)
    asyncio.run(write_brief_refine_source(PrepChatKind.BUILD, None, ws, bc_root, ec_root))
    assert not (ws.path / SOURCE_FILE_NAME).exists()


def test_build_session_with_messages_writes_transcript(
    ws_root: Path, bc_root: Path, ec_root: Path
) -> None:
    ws = create("T", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    build_chat_mod.append_user_message_and_mark_started(
        session,
        ChatMessage(role="user", content="please add X", ts=datetime.now(UTC)),
        root=bc_root,
    )
    asyncio.run(
        write_brief_refine_source(PrepChatKind.BUILD, session.session_id, ws, bc_root, ec_root)
    )
    written = (ws.path / SOURCE_FILE_NAME).read_text(encoding="utf-8")
    assert "please add X" in written


def test_explore_session_with_messages_writes_transcript(
    ws_root: Path, bc_root: Path, ec_root: Path
) -> None:
    ws = create("T", "d", root=ws_root)
    session = explore_chat_mod.create_session("architecture", root=ec_root)
    explore_chat_mod.append_user_message_and_mark_started(
        session,
        ChatMessage(role="user", content="explore this", ts=datetime.now(UTC)),
        root=ec_root,
    )
    asyncio.run(
        write_brief_refine_source(PrepChatKind.EXPLORE, session.session_id, ws, bc_root, ec_root)
    )
    written = (ws.path / SOURCE_FILE_NAME).read_text(encoding="utf-8")
    assert "explore this" in written


def test_mixed_role_transcript_excludes_assistant_content(
    ws_root: Path, bc_root: Path, ec_root: Path
) -> None:
    ws = create("T", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    session = build_chat_mod.append_user_message_and_mark_started(
        session,
        ChatMessage(role="user", content="please add X", ts=datetime.now(UTC)),
        root=bc_root,
    )
    build_chat_mod.append_assistant_and_clear_in_flight(
        session,
        ChatMessage(role="assistant", content="I will also add Y unasked", ts=datetime.now(UTC)),
        root=bc_root,
    )
    asyncio.run(
        write_brief_refine_source(PrepChatKind.BUILD, session.session_id, ws, bc_root, ec_root)
    )
    written = (ws.path / SOURCE_FILE_NAME).read_text(encoding="utf-8")
    assert "please add X" in written
    assert "I will also add Y unasked" not in written


def test_empty_transcript_writes_no_file(ws_root: Path, bc_root: Path, ec_root: Path) -> None:
    ws = create("T", "d", root=ws_root)
    session = build_chat_mod.create_session(root=bc_root)
    asyncio.run(
        write_brief_refine_source(PrepChatKind.BUILD, session.session_id, ws, bc_root, ec_root)
    )
    assert not (ws.path / SOURCE_FILE_NAME).exists()


def test_unknown_session_id_is_swallowed_and_writes_no_file(
    ws_root: Path, bc_root: Path, ec_root: Path
) -> None:
    ws = create("T", "d", root=ws_root)
    asyncio.run(
        write_brief_refine_source(
            PrepChatKind.BUILD,
            "12345678-1234-1234-1234-1234567890ab",
            ws,
            bc_root,
            ec_root,
        )
    )
    assert not (ws.path / SOURCE_FILE_NAME).exists()
