from __future__ import annotations

import dataclasses
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import alan.build_chat as bc
import alan.prep_chat as prep_chat_mod
from alan.build_chat import (
    BuildChatError,
    BuildChatLocked,
    BuildChatMessage,
    BuildChatSessionLocks,
    append_assistant_and_clear_in_flight,
    append_user_message_and_mark_started,
    clear_in_flight_turn,
    create_session,
    delete_session,
    list_sessions,
    load_brief,
    load_session,
    reconcile_in_flight_turns,
    save_brief,
    session_dir,
    stream_path,
    validate_session_id,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "bc-sessions"


def _uuid() -> str:
    return "12345678-1234-1234-1234-1234567890ab"


def test_validate_session_id_valid():
    validate_session_id(_uuid())


def test_validate_session_id_invalid():
    with pytest.raises(BuildChatError, match="invalid session_id"):
        validate_session_id("not-a-uuid")


def test_create_session_returns_uuid_and_persists(root):
    s = create_session(root=root)
    validate_session_id(s.session_id)
    assert s.transcript == []
    assert s.in_flight_turn is None
    loaded = load_session(s.session_id, root=root)
    assert loaded.session_id == s.session_id


def test_default_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", str(tmp_path / "env-root"))
    s = create_session()
    assert (tmp_path / "env-root" / s.session_id).is_dir()


def test_default_root_falls_back_to_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", raising=False)
    monkeypatch.setattr(
        bc, "CONFIG", dataclasses.replace(bc.CONFIG, sessions_root=lambda: tmp_path / "cfg")
    )
    s = bc.create_session()
    assert (tmp_path / "cfg" / s.session_id).is_dir()


def test_create_session_creates_root_dir(root):
    assert not root.exists()
    create_session(root=root)
    assert root.is_dir()


def test_create_session_collision(monkeypatch, root):
    fixed = ["00000000-0000-0000-0000-000000000001"] * 2 + ["00000000-0000-0000-0000-000000000002"]

    class _Gen:
        def __init__(self) -> None:
            self._i = -1

        def __call__(self) -> str:
            self._i += 1
            return fixed[self._i]

    monkeypatch.setattr(uuid, "uuid4", _Gen())
    a = create_session(root=root)
    b = create_session(root=root)
    assert a.session_id == "00000000-0000-0000-0000-000000000001"
    assert b.session_id == "00000000-0000-0000-0000-000000000002"


def test_create_session_exhausted(monkeypatch, root):
    fixed = ["00000000-0000-0000-0000-000000000001"] * 9

    class _Gen:
        def __init__(self) -> None:
            self._i = -1

        def __call__(self) -> str:
            self._i += 1
            return fixed[self._i]

    monkeypatch.setattr(uuid, "uuid4", _Gen())
    create_session(root=root)
    with pytest.raises(BuildChatError, match="could not allocate"):
        create_session(root=root)


def test_load_session_not_found(root):
    with pytest.raises(BuildChatError, match="not found"):
        load_session(_uuid(), root=root)


def test_load_session_corrupt(root):
    s = create_session(root=root)
    (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
    with pytest.raises(BuildChatError, match="corrupt"):
        load_session(s.session_id, root=root)


def test_session_dir_invalid_id(root):
    with pytest.raises(BuildChatError, match="invalid session_id"):
        session_dir("bad-id", root=root)


def test_stream_path_returns_jsonl(root):
    s = create_session(root=root)
    assert stream_path(s.session_id, root=root).name == "stream.jsonl"


def test_collision_stream_path_returns_collision_jsonl(root):
    from alan import _chat_core

    s = create_session(root=root)
    path = _chat_core.collision_stream_path(bc.CONFIG, s.session_id, root=root)
    assert path.name == "collision_stream.jsonl"
    assert path.parent == root / s.session_id


def test_collision_stream_path_resolves_through_root_override(monkeypatch, tmp_path):
    from alan import _chat_core

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    override_root = tmp_path / "override-root"
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", str(override_root))

    sid = "12345678-1234-1234-1234-1234567890ab"
    path = _chat_core.collision_stream_path(bc.CONFIG, sid, root=None)

    assert path == override_root / sid / "collision_stream.jsonl"
    assert not any(fake_home.rglob("*"))


def test_append_user_message_and_mark_started(root):
    s = create_session(root=root)
    msg = BuildChatMessage(role="user", content="hello", ts=datetime.now(UTC))
    updated = append_user_message_and_mark_started(s, msg, root=root)
    assert len(updated.transcript) == 1
    assert updated.in_flight_turn is not None
    assert updated.in_flight_turn.user_text == "hello"


def test_append_assistant_and_clear_in_flight(root):
    s = create_session(root=root)
    umsg = BuildChatMessage(role="user", content="q", ts=datetime.now(UTC))
    s = append_user_message_and_mark_started(s, umsg, root=root)
    amsg = BuildChatMessage(role="assistant", content="a", ts=datetime.now(UTC))
    updated = append_assistant_and_clear_in_flight(s, amsg, root=root)
    assert [m.role for m in updated.transcript] == ["user", "assistant"]
    assert updated.in_flight_turn is None


def test_clear_in_flight_turn(root):
    s = create_session(root=root)
    umsg = BuildChatMessage(role="user", content="q", ts=datetime.now(UTC))
    s = append_user_message_and_mark_started(s, umsg, root=root)
    cleared = clear_in_flight_turn(s.session_id, root=root)
    assert cleared.in_flight_turn is None


def test_delete_session_removes_dir(root):
    s = create_session(root=root)
    delete_session(s.session_id, root=root)
    with pytest.raises(BuildChatError, match="not found"):
        load_session(s.session_id, root=root)


def test_delete_session_not_found(root):
    with pytest.raises(BuildChatError, match="not found"):
        delete_session(_uuid(), root=root)


def test_delete_session_rmtree_fnf(root, monkeypatch):
    import shutil as shutil_mod
    s = create_session(root=root)

    def boom(path: Any, **_: Any) -> None:
        raise FileNotFoundError("gone")

    monkeypatch.setattr(shutil_mod, "rmtree", boom)
    with pytest.raises(BuildChatError, match="not found"):
        delete_session(s.session_id, root=root)


def test_load_brief_absent(root):
    s = create_session(root=root)
    assert load_brief(s.session_id, root=root) is None


def test_load_brief_corrupt(root, caplog):
    s = create_session(root=root)
    (session_dir(s.session_id, root=root) / "brief.json").write_text("bad")
    with caplog.at_level("WARNING", logger="alan.build_chat"):
        result = load_brief(s.session_id, root=root)
    assert result is None
    assert any("corrupt" in r.message for r in caplog.records)


def test_save_and_load_brief_roundtrip(root):
    s = create_session(root=root)
    b = save_brief(s.session_id, "my brief", root=root)
    assert b.content == "my brief"
    loaded = load_brief(s.session_id, root=root)
    assert loaded is not None and loaded.content == "my brief"


def test_write_json_oserror(tmp_path, monkeypatch):
    from pathlib import Path as PathCls

    orig_open = PathCls.open

    def fake_open(self: PathCls, *args: Any, **kwargs: Any) -> Any:
        if self.suffix == ".tmp":
            raise OSError("disk full")
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(PathCls, "open", fake_open)
    with pytest.raises(OSError, match="disk full"):
        bc._write_json(tmp_path / "x.json", {"k": "v"})


class TestPrepChatArchival:
    def test_archived_session_absent_from_list(self, root: Path, tmp_path: Path) -> None:
        s = create_session(root=root)
        prep_chat_mod.archive(
            prep_chat_mod.PrepChatKind.BUILD,
            s.session_id,
            tmp_path / "ws-task",
            title="t",
            root=root,
        )
        assert list_sessions(root=root) == []

    def test_archived_session_cannot_be_loaded(self, root: Path, tmp_path: Path) -> None:
        s = create_session(root=root)
        prep_chat_mod.archive(
            prep_chat_mod.PrepChatKind.BUILD,
            s.session_id,
            tmp_path / "ws-task",
            title="t",
            root=root,
        )
        with pytest.raises(BuildChatError, match="not found"):
            load_session(s.session_id, root=root)

    def test_archived_session_cannot_be_deleted(self, root: Path, tmp_path: Path) -> None:
        s = create_session(root=root)
        prep_chat_mod.archive(
            prep_chat_mod.PrepChatKind.BUILD,
            s.session_id,
            tmp_path / "ws-task",
            title="t",
            root=root,
        )
        with pytest.raises(BuildChatError, match="not found"):
            delete_session(s.session_id, root=root)


class TestBuildChatSessionLocks:
    def test_acquire_and_release(self):
        locks = BuildChatSessionLocks()
        entry = locks.acquire("s1")
        locks.release(entry)
        locks.acquire("s1")

    def test_acquire_twice_raises(self):
        locks = BuildChatSessionLocks()
        locks.acquire("s1")
        with pytest.raises(BuildChatLocked, match="already in progress"):
            locks.acquire("s1")

    def test_set_proc_while_busy(self):
        locks = BuildChatSessionLocks()
        entry = locks.acquire("s1")
        p = subprocess.Popen(
            ["echo", "x"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
        )
        locks.set_proc(entry, p)
        p.wait()

    def test_cancel_with_proc(self):
        locks = BuildChatSessionLocks()
        entry = locks.acquire("s1")
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        locks.set_proc(entry, p)
        assert locks.cancel("s1") is True
        p.wait()

    def test_cancel_without_proc(self):
        locks = BuildChatSessionLocks()
        assert locks.cancel("s1") is False

    def test_release_unknown_noop(self):
        locks = BuildChatSessionLocks()
        locks.force_release("nonexistent")


class TestListSessions:
    def test_empty_when_no_dir(self, root: Path) -> None:
        assert list_sessions(root=root) == []

    def test_returns_summaries_sorted_desc(self, root: Path) -> None:
        import json as json_mod
        older = create_session(root=root)
        newer = create_session(root=root)
        target = session_dir(newer.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        raw["created_at"] = datetime(2999, 1, 1, tzinfo=UTC).isoformat()
        target.write_text(json_mod.dumps(raw), encoding="utf-8")
        result = list_sessions(root=root)
        assert [s.session_id for s in result] == [newer.session_id, older.session_id]

    def test_skips_corrupt(self, root: Path) -> None:
        s = create_session(root=root)
        (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
        assert list_sessions(root=root) == []

    def test_skips_non_uuid_dir(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "not-a-uuid").mkdir()
        (root / "stray-file").write_text("noise", encoding="utf-8")
        assert list_sessions(root=root) == []

    def test_caps_at_25(self, root: Path) -> None:
        import json as json_mod
        for i in range(30):
            s = create_session(root=root)
            target = session_dir(s.session_id, root=root) / "session.json"
            raw = json_mod.loads(target.read_text())
            raw["created_at"] = datetime(2000 + i, 1, 1, tzinfo=UTC).isoformat()
            target.write_text(json_mod.dumps(raw), encoding="utf-8")
        assert len(list_sessions(root=root)) == 25

    def test_list_sessions_filters_by_project_id(self, root: Path) -> None:
        matching = create_session(root=root, project_id="proj-a")
        create_session(root=root, project_id="proj-b")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_list_sessions_excludes_sessions_without_project_id(self, root: Path) -> None:
        create_session(root=root)
        matching = create_session(root=root, project_id="proj-a")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_list_sessions_project_filter_unaffected_by_global_cap(self, root: Path) -> None:
        import json as json_mod
        older_match = create_session(root=root, project_id="proj-a")
        target = session_dir(older_match.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        raw["created_at"] = datetime(1990, 1, 1, tzinfo=UTC).isoformat()
        target.write_text(json_mod.dumps(raw), encoding="utf-8")
        for _ in range(30):
            create_session(root=root, project_id="proj-b")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [older_match.session_id]

    def test_list_sessions_project_filter_caps_at_list_cap(self, root: Path) -> None:
        for _ in range(30):
            create_session(root=root, project_id="proj-a")
        result = list_sessions(root=root, project_id="proj-a")
        assert len(result) == 25

    def test_list_sessions_unknown_project_id_returns_empty(self, root: Path) -> None:
        create_session(root=root, project_id="proj-a")
        assert list_sessions(root=root, project_id="does-not-exist") == []

    def test_list_sessions_no_project_id_unchanged(self, root: Path) -> None:
        a = create_session(root=root, project_id="proj-a")
        b = create_session(root=root)
        result = list_sessions(root=root)
        assert {s.session_id for s in result} == {a.session_id, b.session_id}


def _register_acme_engine() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")), validate_env=False)


class TestListSessionsEngineKeyFilter:
    def test_no_engine_key_is_unfiltered(self, root: Path) -> None:
        a = create_session(root=root, engine_key="acme/widget")
        b = create_session(root=root)
        result = list_sessions(root=root)
        assert {s.session_id for s in result} == {a.session_id, b.session_id}

    def test_filters_to_matching_registered_engine(self, root: Path) -> None:
        _register_acme_engine()
        matching = create_session(root=root, engine_key="acme/widget")
        create_session(root=root, engine_key="other/repo")
        result = list_sessions(root=root, engine_key="acme/widget")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_unknown_engine_key_returns_empty(self, root: Path) -> None:
        create_session(root=root)
        assert list_sessions(root=root, engine_key="ghost/repo") == []

    def test_combines_with_project_id_filter(self, root: Path) -> None:
        _register_acme_engine()
        matching = create_session(root=root, engine_key="acme/widget", project_id="proj-a")
        create_session(root=root, engine_key="acme/widget", project_id="proj-b")
        create_session(root=root, engine_key="other/repo", project_id="proj-a")
        result = list_sessions(root=root, engine_key="acme/widget", project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]


class TestReconcileInFlightTurns:
    def test_no_dir(self, root: Path) -> None:
        assert reconcile_in_flight_turns(root=root) == []

    def test_empty_dir(self, root: Path) -> None:
        root.mkdir(parents=True)
        assert reconcile_in_flight_turns(root=root) == []

    def test_non_dir_entry(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "file.txt").write_text("x")
        assert reconcile_in_flight_turns(root=root) == []

    def test_invalid_name_dir(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "not-a-uuid").mkdir()
        assert reconcile_in_flight_turns(root=root) == []

    def test_no_in_flight(self, root: Path) -> None:
        create_session(root=root)
        assert reconcile_in_flight_turns(root=root) == []

    def test_clears_in_flight(self, root: Path) -> None:
        s = create_session(root=root)
        msg = BuildChatMessage(role="user", content="q", ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)
        cleared = reconcile_in_flight_turns(root=root)
        assert s.session_id in cleared
        loaded = load_session(s.session_id, root=root)
        assert loaded.in_flight_turn is None
        assert len(loaded.transcript) == 2

    def test_skips_corrupt(self, root: Path, caplog: Any) -> None:
        s = create_session(root=root)
        (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
        with caplog.at_level("WARNING", logger="alan.build_chat"):
            assert reconcile_in_flight_turns(root=root) == []

    def test_handles_append_exception(self, root: Path, monkeypatch: Any, caplog: Any) -> None:
        s = create_session(root=root)
        msg = BuildChatMessage(role="user", content="q", ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)

        def boom(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("disk full")

        monkeypatch.setattr("alan._chat_core.append_assistant_and_clear_in_flight", boom)
        with caplog.at_level("ERROR", logger="alan.build_chat"):
            cleared = reconcile_in_flight_turns(root=root)
        assert cleared == []
        assert any("sweep failed" in r.message for r in caplog.records)


class TestEngineKeyBinding:
    def test_create_session_default_engine_key_is_none(self, root: Path) -> None:
        s = create_session(root=root)
        assert s.engine_key is None

    def test_create_session_stores_engine_key(self, root: Path) -> None:
        s = create_session(root=root, engine_key="acme/widget")
        assert s.engine_key == "acme/widget"
        loaded = load_session(s.session_id, root=root)
        assert loaded.engine_key == "acme/widget"

    def test_list_sessions_exposes_engine_key(self, root: Path) -> None:
        s = create_session(root=root, engine_key="acme/widget")
        result = list_sessions(root=root)
        row = next(r for r in result if r.session_id == s.session_id)
        assert row.engine_key == "acme/widget"


class TestSweepLegacyEngineKeys:
    def test_no_dir(self, root: Path) -> None:
        assert bc.sweep_legacy_engine_keys(root=root) == []

    def test_no_legacy_sessions(self, root: Path) -> None:
        create_session(root=root)
        assert bc.sweep_legacy_engine_keys(root=root) == []

    def test_backfills_legacy_session(self, root: Path) -> None:
        import json as json_mod

        s = create_session(root=root)
        target = session_dir(s.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        del raw["engine_key"]
        target.write_text(json_mod.dumps(raw), encoding="utf-8")

        swept = bc.sweep_legacy_engine_keys(root=root)

        assert swept == [s.session_id]
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.engine_key is not None
