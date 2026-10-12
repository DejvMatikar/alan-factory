from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
from alan._chat_core import ChatMessage

_UNKNOWN_SESSION_ID = "12345678-1234-1234-1234-1234567890ab"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


@pytest.fixture
def ws_path(tmp_path: Path) -> Path:
    path = tmp_path / "ws" / "some-task"
    path.mkdir(parents=True)
    return path


def _chat_msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content, ts=datetime.now(UTC))


def test_archive_moves_build_session_out_of_drafts_root(bc_root: Path, ws_path: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="My WS", root=bc_root
    )
    assert not (bc_root / session.session_id).exists()
    assert (ws_path / "prep-chat" / session.session_id / "session.json").is_file()


def test_archive_explore_session_records_consultant_key(ec_root: Path, ws_path: Path) -> None:
    session = explore_chat_mod.create_session("architecture", root=ec_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE,
        session.session_id,
        ws_path,
        title="My WS",
        root=ec_root,
    )
    marker = prep_chat_mod.load_marker(ws_path, session.session_id)
    assert marker.kind == prep_chat_mod.PrepChatKind.EXPLORE
    assert marker.consultant_key == "architecture"


def test_archive_build_session_has_no_consultant_key(bc_root: Path, ws_path: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="My WS", root=bc_root
    )
    marker = prep_chat_mod.load_marker(ws_path, session.session_id)
    assert marker.consultant_key is None


def test_archive_marker_records_title_session_id_and_created_at(
    bc_root: Path, ws_path: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="My WS", root=bc_root
    )
    marker = prep_chat_mod.load_marker(ws_path, session.session_id)
    assert marker.title == "My WS"
    assert marker.session_id == session.session_id
    assert marker.created_at == session.created_at


def test_archive_unknown_session_raises_and_creates_no_dest(bc_root: Path, ws_path: Path) -> None:
    with pytest.raises(build_chat_mod.BuildChatError, match="not found"):
        prep_chat_mod.archive(
            prep_chat_mod.PrepChatKind.BUILD,
            _UNKNOWN_SESSION_ID,
            ws_path,
            title="x",
            root=bc_root,
        )
    assert not (ws_path / "prep-chat").exists()


def test_archive_raises_and_leaves_live_dir_when_dest_already_exists(
    bc_root: Path, ws_path: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    stale_dest = ws_path / "prep-chat" / session.session_id
    stale_dest.mkdir(parents=True)
    (stale_dest / "stray.txt").write_text("stale", encoding="utf-8")
    with pytest.raises(prep_chat_mod.PrepChatError, match="already exists"):
        prep_chat_mod.archive(
            prep_chat_mod.PrepChatKind.BUILD,
            session.session_id,
            ws_path,
            title="x",
            root=bc_root,
        )
    assert (bc_root / session.session_id / "session.json").is_file()
    assert [p.name for p in stale_dest.iterdir()] == ["stray.txt"]


def test_find_archived_session_id_none_when_no_dir(ws_path: Path) -> None:
    assert prep_chat_mod.find_archived_session_id(ws_path) is None


def test_find_archived_session_id_none_when_empty_dir(ws_path: Path) -> None:
    (ws_path / "prep-chat").mkdir()
    assert prep_chat_mod.find_archived_session_id(ws_path) is None


def test_find_archived_session_id_ignores_non_uuid_entries(ws_path: Path) -> None:
    root = ws_path / "prep-chat"
    root.mkdir()
    (root / "not-a-uuid").mkdir()
    (root / "stray-file.txt").write_text("x", encoding="utf-8")
    assert prep_chat_mod.find_archived_session_id(ws_path) is None


def test_find_archived_session_id_returns_the_archived_session(
    bc_root: Path, ws_path: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    assert prep_chat_mod.find_archived_session_id(ws_path) == session.session_id


def test_load_marker_not_found(ws_path: Path) -> None:
    with pytest.raises(prep_chat_mod.PrepChatError, match="not found"):
        prep_chat_mod.load_marker(ws_path, _UNKNOWN_SESSION_ID)


def test_load_marker_corrupt(bc_root: Path, ws_path: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    marker_path = ws_path / "prep-chat" / session.session_id / "prep_chat.json"
    marker_path.write_text("not json", encoding="utf-8")
    with pytest.raises(prep_chat_mod.PrepChatError, match="corrupt"):
        prep_chat_mod.load_marker(ws_path, session.session_id)


def test_load_ref_none_when_no_archive(ws_path: Path) -> None:
    assert prep_chat_mod.load_ref(ws_path) is None


def test_load_ref_returns_ref_after_archive(ec_root: Path, ws_path: Path) -> None:
    session = explore_chat_mod.create_session("research", root=ec_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path, title="x", root=ec_root
    )
    ref = prep_chat_mod.load_ref(ws_path)
    assert ref is not None
    assert ref.session_id == session.session_id
    assert ref.kind == prep_chat_mod.PrepChatKind.EXPLORE
    assert ref.consultant_key == "research"


def test_load_ref_none_when_marker_corrupt(
    bc_root: Path, ws_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    marker_path = ws_path / "prep-chat" / session.session_id / "prep_chat.json"
    marker_path.write_text("not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="alan.prep_chat"):
        assert prep_chat_mod.load_ref(ws_path) is None
    assert any("marker unreadable" in r.message for r in caplog.records)


def test_load_archived_session_returns_transcript_build(bc_root: Path, ws_path: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    session = build_chat_mod.append_user_message_and_mark_started(
        session, _chat_msg("user", "hello"), root=bc_root
    )
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    loaded = prep_chat_mod.load_archived_session(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path
    )
    assert [m.content for m in loaded.transcript] == ["hello"]


def test_load_archived_session_returns_transcript_explore(ec_root: Path, ws_path: Path) -> None:
    session = explore_chat_mod.create_session("research", root=ec_root)
    session = explore_chat_mod.append_user_message_and_mark_started(
        session, _chat_msg("user", "hi"), root=ec_root
    )
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path, title="x", root=ec_root
    )
    loaded = prep_chat_mod.load_archived_session(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path
    )
    assert [m.content for m in loaded.transcript] == ["hi"]


def test_load_archived_session_not_found_raises_prep_chat_error(ws_path: Path) -> None:
    with pytest.raises(prep_chat_mod.PrepChatError):
        prep_chat_mod.load_archived_session(
            prep_chat_mod.PrepChatKind.BUILD, _UNKNOWN_SESSION_ID, ws_path
        )


def test_archived_session_unreachable_from_drafts_root(bc_root: Path, ws_path: Path) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    assert build_chat_mod.list_sessions(root=bc_root) == []
    with pytest.raises(build_chat_mod.BuildChatError, match="not found"):
        build_chat_mod.load_session(session.session_id, root=bc_root)
    with pytest.raises(build_chat_mod.BuildChatError, match="not found"):
        build_chat_mod.delete_session(session.session_id, root=bc_root)


def test_load_archived_brief_returns_none_when_no_brief_was_saved(
    bc_root: Path, ws_path: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    assert (
        prep_chat_mod.load_archived_brief(
            prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path
        )
        is None
    )


def test_load_archived_brief_returns_the_brief_saved_before_archiving_build(
    bc_root: Path, ws_path: Path
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    build_chat_mod.save_brief(session.session_id, "distilled", root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    brief = prep_chat_mod.load_archived_brief(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path
    )
    assert brief is not None
    assert brief.content == "distilled"


def test_load_archived_brief_returns_the_brief_saved_before_archiving_explore(
    ec_root: Path, ws_path: Path
) -> None:
    session = explore_chat_mod.create_session("research", root=ec_root)
    explore_chat_mod.save_brief(session.session_id, "explore brief", root=ec_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path, title="x", root=ec_root
    )
    brief = prep_chat_mod.load_archived_brief(
        prep_chat_mod.PrepChatKind.EXPLORE, session.session_id, ws_path
    )
    assert brief is not None
    assert brief.content == "explore brief"


def test_load_archived_brief_returns_none_when_corrupt(
    bc_root: Path, ws_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = build_chat_mod.create_session(root=bc_root)
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path, title="x", root=bc_root
    )
    brief_path = ws_path / "prep-chat" / session.session_id / "brief.json"
    brief_path.write_text("not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="alan.build_chat"):
        brief = prep_chat_mod.load_archived_brief(
            prep_chat_mod.PrepChatKind.BUILD, session.session_id, ws_path
        )
    assert brief is None
