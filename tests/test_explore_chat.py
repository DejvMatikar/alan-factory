from __future__ import annotations

import dataclasses
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import alan.explore_chat as ec
from alan.explore_chat import (
    ExploreChatError,
    ExploreChatLocked,
    ExploreChatMessage,
    ExploreChatSessionLocks,
    ExploreChatSessionSummary,
    RepoProvisionStatus,
    append_assistant_and_clear_in_flight,
    append_user_message_and_mark_started,
    clear_in_flight_turn,
    create_session,
    delete_session,
    list_sessions,
    load_brief,
    load_session,
    reconcile_in_flight_turns,
    repo_dir,
    save_brief,
    session_dir,
    set_repo_provision,
    stream_path,
    validate_consultant_key,
    validate_session_id,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "ec-sessions"


def _uuid() -> str:
    return "12345678-1234-1234-1234-1234567890ab"


def test_validate_session_id_valid():
    validate_session_id(_uuid())


def test_validate_session_id_invalid():
    with pytest.raises(ExploreChatError, match="invalid session_id"):
        validate_session_id("not-a-uuid")


def test_validate_consultant_key_valid():
    validate_consultant_key("brainstorming")


def test_validate_consultant_key_invalid():
    with pytest.raises(ExploreChatError, match="invalid consultant_key"):
        validate_consultant_key("INVALID KEY!")


def test_default_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", str(tmp_path / "env-root"))
    s = create_session("brainstorming")
    assert (tmp_path / "env-root" / s.session_id).is_dir()


def test_default_root_falls_back_to_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", raising=False)
    monkeypatch.setattr(
        ec, "CONFIG", dataclasses.replace(ec.CONFIG, sessions_root=lambda: tmp_path / "cfg")
    )
    s = ec.create_session("brainstorming")
    assert (tmp_path / "cfg" / s.session_id).is_dir()


def test_create_session_returns_uuid_and_persists(root):
    s = create_session("brainstorming", root=root)
    validate_session_id(s.session_id)
    assert s.consultant_key == "brainstorming"
    assert s.transcript == []
    assert s.in_flight_turn is None
    loaded = load_session(s.session_id, root=root)
    assert loaded.session_id == s.session_id
    assert loaded.consultant_key == "brainstorming"


def test_create_session_creates_root_dir(root):
    assert not root.exists()
    create_session("research", root=root)
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
    a = create_session("brainstorming", root=root)
    b = create_session("brainstorming", root=root)
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
    create_session("brainstorming", root=root)
    with pytest.raises(ExploreChatError, match="could not allocate"):
        create_session("brainstorming", root=root)


def test_load_session_not_found(root):
    with pytest.raises(ExploreChatError, match="not found"):
        load_session(_uuid(), root=root)


def test_load_session_corrupt(root):
    s = create_session("brainstorming", root=root)
    (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
    with pytest.raises(ExploreChatError, match="corrupt"):
        load_session(s.session_id, root=root)


def test_session_dir_invalid_id(root):
    with pytest.raises(ExploreChatError, match="invalid session_id"):
        session_dir("bad-id", root=root)


def test_stream_path_returns_jsonl(root):
    s = create_session("brainstorming", root=root)
    assert stream_path(s.session_id, root=root).name == "stream.jsonl"


def test_append_user_message_and_mark_started(root):
    s = create_session("brainstorming", root=root)
    msg = ExploreChatMessage(role="user", content="hello", ts=datetime.now(UTC))
    updated = append_user_message_and_mark_started(s, msg, root=root)
    assert len(updated.transcript) == 1
    assert updated.in_flight_turn is not None
    assert updated.in_flight_turn.user_text == "hello"


def test_append_assistant_and_clear_in_flight(root):
    s = create_session("brainstorming", root=root)
    umsg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
    s = append_user_message_and_mark_started(s, umsg, root=root)
    amsg = ExploreChatMessage(role="assistant", content="a", ts=datetime.now(UTC))
    updated = append_assistant_and_clear_in_flight(s, amsg, root=root)
    assert [m.role for m in updated.transcript] == ["user", "assistant"]
    assert updated.in_flight_turn is None


def test_clear_in_flight_turn(root):
    s = create_session("brainstorming", root=root)
    umsg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
    s = append_user_message_and_mark_started(s, umsg, root=root)
    cleared = clear_in_flight_turn(s.session_id, root=root)
    assert cleared.in_flight_turn is None


def test_delete_session_removes_dir(root):
    s = create_session("brainstorming", root=root)
    delete_session(s.session_id, root=root)
    with pytest.raises(ExploreChatError, match="not found"):
        load_session(s.session_id, root=root)


def test_delete_session_not_found(root):
    with pytest.raises(ExploreChatError, match="not found"):
        delete_session(_uuid(), root=root)


def test_delete_session_rmtree_fnf(root, monkeypatch):
    import shutil as shutil_mod
    s = create_session("brainstorming", root=root)

    def boom(path: Any, **_: Any) -> None:
        raise FileNotFoundError("gone")

    monkeypatch.setattr(shutil_mod, "rmtree", boom)
    with pytest.raises(ExploreChatError, match="not found"):
        delete_session(s.session_id, root=root)


def test_load_brief_absent(root):
    s = create_session("brainstorming", root=root)
    assert load_brief(s.session_id, root=root) is None


def test_load_brief_corrupt(root, caplog):
    s = create_session("brainstorming", root=root)
    (session_dir(s.session_id, root=root) / "brief.json").write_text("bad")
    with caplog.at_level("WARNING", logger="alan.explore_chat"):
        result = load_brief(s.session_id, root=root)
    assert result is None
    assert any("corrupt" in r.message for r in caplog.records)


def test_save_and_load_brief_roundtrip(root):
    s = create_session("brainstorming", root=root)
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
        ec._write_json(tmp_path / "x.json", {"k": "v"})


class TestRepoProvision:
    def test_repo_provision_status_is_the_shared_explore_chat_repo_enum(self) -> None:
        import alan.explore_chat_repo as ec_repo_mod

        assert RepoProvisionStatus is ec_repo_mod.RepoProvisionStatus

    def test_defaults_to_unchecked(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        assert s.repo_provision == RepoProvisionStatus.UNCHECKED
        loaded = load_session(s.session_id, root=root)
        assert loaded.repo_provision == RepoProvisionStatus.UNCHECKED

    def test_repo_dir_is_session_dir_slash_repo(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        assert repo_dir(s.session_id, root=root) == session_dir(s.session_id, root=root) / "repo"

    def test_set_repo_provision_persists_and_returns_updated(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        updated = set_repo_provision(s.session_id, RepoProvisionStatus.READY, root=root)
        assert updated.repo_provision == RepoProvisionStatus.READY
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.repo_provision == RepoProvisionStatus.READY

    def test_set_repo_provision_transitions_do_not_touch_transcript(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        msg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
        s = append_user_message_and_mark_started(s, msg, root=root)
        updated = set_repo_provision(s.session_id, RepoProvisionStatus.PENDING, root=root)
        assert len(updated.transcript) == 1
        assert updated.in_flight_turn is not None


class TestListSessions:
    def test_empty_when_no_dir(self, root: Path) -> None:
        assert list_sessions(root=root) == []

    def test_returns_summaries_with_consultant_key(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        result = list_sessions(root=root)
        assert len(result) == 1
        summary = result[0]
        assert isinstance(summary, ExploreChatSessionSummary)
        assert summary.session_id == s.session_id
        assert summary.consultant_key == "brainstorming"
        assert summary.last_message_preview == ""
        assert summary.in_flight is False

    def test_preview_truncated_to_120(self, root: Path) -> None:
        s = create_session("research", root=root)
        msg = ExploreChatMessage(role="user", content="x" * 200, ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)
        [summary] = list_sessions(root=root)
        assert len(summary.last_message_preview) == 120

    def test_in_flight_flag(self, root: Path) -> None:
        s = create_session("architecture", root=root)
        msg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)
        [summary] = list_sessions(root=root)
        assert summary.in_flight is True

    def test_skips_corrupt(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
        assert list_sessions(root=root) == []

    def test_sorted_newest_first(self, root: Path) -> None:
        import json as json_mod
        older = create_session("brainstorming", root=root)
        newer = create_session("research", root=root)
        target = session_dir(newer.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        raw["created_at"] = datetime(2999, 1, 1, tzinfo=UTC).isoformat()
        target.write_text(json_mod.dumps(raw), encoding="utf-8")
        ids = [s.session_id for s in list_sessions(root=root)]
        assert ids == [newer.session_id, older.session_id]

    def test_list_sessions_filters_by_project_id(self, root: Path) -> None:
        matching = create_session("brainstorming", root=root, project_id="proj-a")
        create_session("brainstorming", root=root, project_id="proj-b")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_list_sessions_excludes_sessions_without_project_id(self, root: Path) -> None:
        create_session("brainstorming", root=root)
        matching = create_session("brainstorming", root=root, project_id="proj-a")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_list_sessions_project_filter_unaffected_by_global_cap(self, root: Path) -> None:
        import json as json_mod
        older_match = create_session("brainstorming", root=root, project_id="proj-a")
        target = session_dir(older_match.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        raw["created_at"] = datetime(1990, 1, 1, tzinfo=UTC).isoformat()
        target.write_text(json_mod.dumps(raw), encoding="utf-8")
        for _ in range(30):
            create_session("brainstorming", root=root, project_id="proj-b")
        result = list_sessions(root=root, project_id="proj-a")
        assert [s.session_id for s in result] == [older_match.session_id]

    def test_list_sessions_project_filter_caps_at_list_cap(self, root: Path) -> None:
        for _ in range(30):
            create_session("brainstorming", root=root, project_id="proj-a")
        result = list_sessions(root=root, project_id="proj-a")
        assert len(result) == 25

    def test_list_sessions_unknown_project_id_returns_empty(self, root: Path) -> None:
        create_session("brainstorming", root=root, project_id="proj-a")
        assert list_sessions(root=root, project_id="does-not-exist") == []

    def test_list_sessions_no_project_id_unchanged(self, root: Path) -> None:
        a = create_session("brainstorming", root=root, project_id="proj-a")
        b = create_session("brainstorming", root=root)
        result = list_sessions(root=root)
        assert {s.session_id for s in result} == {a.session_id, b.session_id}


def _register_acme_engine() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")), validate_env=False)


class TestListSessionsEngineKeyFilter:
    def test_no_engine_key_is_unfiltered(self, root: Path) -> None:
        a = create_session("brainstorming", root=root, engine_key="acme/widget")
        b = create_session("brainstorming", root=root)
        result = list_sessions(root=root)
        assert {s.session_id for s in result} == {a.session_id, b.session_id}

    def test_filters_to_matching_registered_engine(self, root: Path) -> None:
        _register_acme_engine()
        matching = create_session("brainstorming", root=root, engine_key="acme/widget")
        create_session("brainstorming", root=root, engine_key="other/repo")
        result = list_sessions(root=root, engine_key="acme/widget")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_unknown_engine_key_returns_empty(self, root: Path) -> None:
        create_session("brainstorming", root=root)
        assert list_sessions(root=root, engine_key="ghost/repo") == []

    def test_combines_with_project_id_filter(self, root: Path) -> None:
        _register_acme_engine()
        matching = create_session(
            "brainstorming", root=root, engine_key="acme/widget", project_id="proj-a"
        )
        create_session(
            "brainstorming", root=root, engine_key="acme/widget", project_id="proj-b"
        )
        create_session(
            "brainstorming", root=root, engine_key="other/repo", project_id="proj-a"
        )
        result = list_sessions(root=root, engine_key="acme/widget", project_id="proj-a")
        assert [s.session_id for s in result] == [matching.session_id]


class TestExploreChatSessionLocks:
    def test_acquire_and_release(self):
        locks = ExploreChatSessionLocks()
        entry = locks.acquire("s1")
        locks.release(entry)
        locks.acquire("s1")

    def test_acquire_twice_raises(self):
        locks = ExploreChatSessionLocks()
        locks.acquire("s1")
        with pytest.raises(ExploreChatLocked, match="already in progress"):
            locks.acquire("s1")

    def test_set_proc_while_busy(self):
        locks = ExploreChatSessionLocks()
        entry = locks.acquire("s1")
        p = subprocess.Popen(
            ["echo", "x"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
        )
        locks.set_proc(entry, p)
        p.wait()

    def test_cancel_with_proc(self):
        locks = ExploreChatSessionLocks()
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
        locks = ExploreChatSessionLocks()
        assert locks.cancel("s1") is False

    def test_release_unknown_noop(self):
        locks = ExploreChatSessionLocks()
        locks.force_release("nonexistent")


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
        create_session("brainstorming", root=root)
        assert reconcile_in_flight_turns(root=root) == []

    def test_clears_in_flight(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        msg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)
        cleared = reconcile_in_flight_turns(root=root)
        assert s.session_id in cleared
        loaded = load_session(s.session_id, root=root)
        assert loaded.in_flight_turn is None
        assert len(loaded.transcript) == 2

    def test_skips_corrupt(self, root: Path, caplog: Any) -> None:
        s = create_session("brainstorming", root=root)
        (session_dir(s.session_id, root=root) / "session.json").write_text("bad")
        with caplog.at_level("WARNING", logger="alan.explore_chat"):
            assert reconcile_in_flight_turns(root=root) == []

    def test_handles_append_exception(self, root: Path, monkeypatch: Any, caplog: Any) -> None:
        s = create_session("brainstorming", root=root)
        msg = ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
        append_user_message_and_mark_started(s, msg, root=root)

        def boom(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("disk full")

        monkeypatch.setattr("alan._chat_core.append_assistant_and_clear_in_flight", boom)
        with caplog.at_level("ERROR", logger="alan.explore_chat"):
            cleared = reconcile_in_flight_turns(root=root)
        assert cleared == []
        assert any("sweep failed" in r.message for r in caplog.records)


class TestEngineKeyBinding:
    def test_create_session_default_engine_key_is_none(self, root: Path) -> None:
        s = create_session("brainstorming", root=root)
        assert s.engine_key is None

    def test_create_session_stores_engine_key(self, root: Path) -> None:
        s = create_session("brainstorming", root=root, engine_key="acme/widget")
        assert s.engine_key == "acme/widget"
        loaded = load_session(s.session_id, root=root)
        assert loaded.engine_key == "acme/widget"

    def test_list_sessions_exposes_engine_key(self, root: Path) -> None:
        s = create_session("brainstorming", root=root, engine_key="acme/widget")
        result = list_sessions(root=root)
        row = next(r for r in result if r.session_id == s.session_id)
        assert row.engine_key == "acme/widget"


class TestSweepLegacyEngineKeys:
    def test_no_dir(self, root: Path) -> None:
        assert ec.sweep_legacy_engine_keys(root=root) == []

    def test_no_legacy_sessions(self, root: Path) -> None:
        create_session("brainstorming", root=root)
        assert ec.sweep_legacy_engine_keys(root=root) == []

    def test_backfills_legacy_session(self, root: Path) -> None:
        import json as json_mod

        s = create_session("brainstorming", root=root)
        target = session_dir(s.session_id, root=root) / "session.json"
        raw = json_mod.loads(target.read_text())
        del raw["engine_key"]
        target.write_text(json_mod.dumps(raw), encoding="utf-8")

        swept = ec.sweep_legacy_engine_keys(root=root)

        assert swept == [s.session_id]
        reloaded = load_session(s.session_id, root=root)
        assert reloaded.engine_key is not None
