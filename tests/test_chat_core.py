from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import _chat_core, engine_config
from alan.build_chat import CONFIG as WS_CHAT_CONFIG

_ALAN_OWN_REPO_SLUG = engine_config.default_engine_config().repo.slug


class _TestChatLocked(_chat_core.ChatCoreError):
    pass


def _make_locks() -> _chat_core.ChatSessionLocks:
    return _chat_core.ChatSessionLocks(_TestChatLocked, "test-chat")


def _session_json_path(session: _chat_core.ChatSession, root: Path) -> Path:
    return root / session.session_id / "session.json"


def _write_raw(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_base_dir_invokes_sessions_root_callable_when_root_is_none(tmp_path: Path) -> None:
    calls: list[int] = []

    def _root() -> Path:
        calls.append(len(calls))
        return tmp_path / "from-callable"

    cfg = dataclasses.replace(WS_CHAT_CONFIG, sessions_root=_root)
    assert _chat_core._base_dir(cfg, None) == tmp_path / "from-callable"  # noqa: SLF001
    assert calls == [0]


def test_base_dir_prefers_explicit_root_over_sessions_root_callable(tmp_path: Path) -> None:
    def _root() -> Path:
        raise AssertionError("sessions_root() must not be called when root is given")

    cfg = dataclasses.replace(WS_CHAT_CONFIG, sessions_root=_root)
    explicit = tmp_path / "explicit"
    assert _chat_core._base_dir(cfg, explicit) == explicit  # noqa: SLF001


def test_base_dir_invokes_sessions_root_callable_lazily_per_call(tmp_path: Path) -> None:
    calls: list[int] = []

    def _root() -> Path:
        calls.append(len(calls))
        return tmp_path / f"call-{len(calls)}"

    cfg = dataclasses.replace(WS_CHAT_CONFIG, sessions_root=_root)
    first = _chat_core._base_dir(cfg, None)  # noqa: SLF001
    second = _chat_core._base_dir(cfg, None)  # noqa: SLF001
    assert calls == [0, 1]
    assert first != second


def test_dataclasses_replace_sessions_root_is_the_test_seam(tmp_path: Path) -> None:
    seam_root = tmp_path / "seam"
    cfg = dataclasses.replace(WS_CHAT_CONFIG, sessions_root=lambda: seam_root)
    assert _chat_core.resolve_sessions_root(cfg) == seam_root


def test_new_session_has_null_cli_session_id(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    assert session.cli_session_id is None


def test_new_session_json_persists_cli_session_id_key(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    raw = json.loads(_session_json_path(session, tmp_path).read_text(encoding="utf-8"))
    assert "cli_session_id" in raw
    assert raw["cli_session_id"] is None


def test_update_session_sets_cli_session_id(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    updated = _chat_core.update_session(
        WS_CHAT_CONFIG, session.session_id, {"cli_session_id": "cli-123"}, root=tmp_path
    )
    assert updated.cli_session_id == "cli-123"
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.cli_session_id == "cli-123"


def test_update_session_clears_cli_session_id(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    _chat_core.update_session(
        WS_CHAT_CONFIG, session.session_id, {"cli_session_id": "cli-123"}, root=tmp_path
    )
    cleared = _chat_core.update_session(
        WS_CHAT_CONFIG, session.session_id, {"cli_session_id": None}, root=tmp_path
    )
    assert cleared.cli_session_id is None


def test_load_session_valid_json_but_schema_invalid_is_corrupt(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    _write_raw(path, {"not": "a valid chat session"})
    with pytest.raises(WS_CHAT_CONFIG.error, match="corrupt"):
        _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)


def test_legacy_session_with_assistant_message_is_bridged(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "cli_session_id" in raw
    del raw["cli_session_id"]
    raw["transcript"] = [
        {"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "hello", "ts": "2026-01-01T00:00:01Z"},
    ]
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.cli_session_id == session.session_id
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["cli_session_id"] == session.session_id


def test_legacy_session_without_assistant_message_is_not_bridged(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["cli_session_id"]
    raw["transcript"] = [{"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00Z"}]
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.cli_session_id is None
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "cli_session_id" not in on_disk


def test_legacy_session_with_empty_transcript_is_not_bridged(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["cli_session_id"]
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.cli_session_id is None


def test_bridge_is_one_shot_second_load_is_a_noop(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["cli_session_id"]
    raw["transcript"] = [{"role": "assistant", "content": "hello", "ts": "2026-01-01T00:00:01Z"}]
    _write_raw(path, raw)

    first = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    second = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert first.cli_session_id == session.session_id
    assert second.cli_session_id == session.session_id


def test_session_json_with_explicit_null_cli_session_id_key_is_not_bridged(
    tmp_path: Path,
) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["transcript"] = [{"role": "assistant", "content": "hello", "ts": "2026-01-01T00:00:01Z"}]
    raw["cli_session_id"] = None
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.cli_session_id is None


def test_new_session_has_null_engine_key_by_default(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    assert session.engine_key is None


def test_create_session_stores_explicit_engine_key(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
    assert session.engine_key == "acme/widget"
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.engine_key == "acme/widget"


def test_new_session_json_persists_engine_key_key(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    raw = json.loads(_session_json_path(session, tmp_path).read_text(encoding="utf-8"))
    assert "engine_key" in raw
    assert raw["engine_key"] is None


def test_legacy_session_missing_engine_key_is_bridged_to_alan_own_repo(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "engine_key" in raw
    del raw["engine_key"]
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.engine_key == _ALAN_OWN_REPO_SLUG
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["engine_key"] == _ALAN_OWN_REPO_SLUG


def test_engine_key_bridge_is_one_shot_second_load_is_a_noop(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["engine_key"]
    _write_raw(path, raw)

    first = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    second = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert first.engine_key == _ALAN_OWN_REPO_SLUG
    assert second.engine_key == _ALAN_OWN_REPO_SLUG


def test_session_json_with_explicit_null_engine_key_is_not_bridged(tmp_path: Path) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["engine_key"] = None
    _write_raw(path, raw)

    loaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)

    assert loaded.engine_key is None


def test_sweep_legacy_engine_keys_empty_root_returns_empty(tmp_path: Path) -> None:
    assert _chat_core.sweep_legacy_engine_keys(WS_CHAT_CONFIG, root=tmp_path / "nope") == []


def test_sweep_legacy_engine_keys_backfills_and_reports_legacy_sessions(tmp_path: Path) -> None:
    legacy = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    legacy_path = _session_json_path(legacy, tmp_path)
    raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    del raw["engine_key"]
    _write_raw(legacy_path, raw)

    current = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")

    swept = _chat_core.sweep_legacy_engine_keys(WS_CHAT_CONFIG, root=tmp_path)

    assert swept == [legacy.session_id]
    on_disk = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert on_disk["engine_key"] == _ALAN_OWN_REPO_SLUG
    reloaded_current = _chat_core.load_session(WS_CHAT_CONFIG, current.session_id, root=tmp_path)
    assert reloaded_current.engine_key == "acme/widget"


def test_sweep_legacy_engine_keys_no_legacy_sessions_returns_empty(tmp_path: Path) -> None:
    _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    assert _chat_core.sweep_legacy_engine_keys(WS_CHAT_CONFIG, root=tmp_path) == []


def test_sweep_legacy_engine_keys_skips_corrupt_session_file(tmp_path: Path) -> None:
    legacy = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    legacy_path = _session_json_path(legacy, tmp_path)
    legacy_path.write_text("not json", encoding="utf-8")

    swept = _chat_core.sweep_legacy_engine_keys(WS_CHAT_CONFIG, root=tmp_path)

    assert swept == []


def test_sweep_legacy_engine_keys_logs_warning_when_backfill_load_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    path = _session_json_path(session, tmp_path)
    _write_raw(path, {"not": "a valid chat session"})

    with caplog.at_level("WARNING"):
        swept = _chat_core.sweep_legacy_engine_keys(WS_CHAT_CONFIG, root=tmp_path)

    assert swept == []
    assert any("skipping" in r.message for r in caplog.records)


def test_chat_message_interrupted_defaults_false() -> None:
    msg = _chat_core.ChatMessage(role="assistant", content="hi", ts=datetime.now(UTC))
    assert msg.interrupted is False


def test_chat_message_interrupted_round_trips_true() -> None:
    msg = _chat_core.ChatMessage(
        role="assistant", content="partial", ts=datetime.now(UTC), interrupted=True
    )
    restored = _chat_core.ChatMessage.model_validate(msg.model_dump())
    assert restored.interrupted is True


def test_chat_message_operations_defaults_empty() -> None:
    msg = _chat_core.ChatMessage(role="assistant", content="hi", ts=datetime.now(UTC))
    assert msg.operations == []


def test_chat_message_operations_round_trips() -> None:
    msg = _chat_core.ChatMessage(
        role="assistant",
        content="hi",
        ts=datetime.now(UTC),
        operations=["Read: a.py", "Bash: ls"],
    )
    restored = _chat_core.ChatMessage.model_validate(msg.model_dump())
    assert restored.operations == ["Read: a.py", "Bash: ls"]


def test_chat_message_legacy_json_without_operations_key_defaults_empty() -> None:
    raw = {"role": "assistant", "content": "hi", "ts": "2026-01-01T00:00:00Z"}
    restored = _chat_core.ChatMessage.model_validate(raw)
    assert restored.operations == []


class _FakeProc:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _BoomOnKillProc:
    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        raise OSError("already reaped")


def test_cancel_before_spawn_then_set_proc_terminates_immediately() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    assert locks.cancel("s1") is True
    fake = _FakeProc()
    locks.set_proc(entry, fake)  # type: ignore[arg-type]
    assert fake.terminated is True


def test_set_proc_without_cancel_does_not_terminate() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    fake = _FakeProc()
    locks.set_proc(entry, fake)  # type: ignore[arg-type]
    assert fake.terminated is False


def test_cancel_with_proc_already_set_terminates_immediately() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    fake = _FakeProc()
    locks.set_proc(entry, fake)  # type: ignore[arg-type]
    assert locks.cancel("s1") is True
    assert fake.terminated is True


def test_cancel_unknown_session_returns_false() -> None:
    locks = _make_locks()
    assert locks.cancel("nope") is False


def test_was_cancelled_true_after_cancel() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    locks.cancel("s1")
    assert locks.was_cancelled(entry) is True


def test_was_cancelled_false_before_cancel() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    assert locks.was_cancelled(entry) is False


def test_acquire_twice_raises_locked() -> None:
    locks = _make_locks()
    locks.acquire("s1")
    with pytest.raises(_TestChatLocked, match="already in progress"):
        locks.acquire("s1")


def test_acquire_after_release_succeeds() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    locks.release(entry)
    locks.acquire("s1")


def test_release_twice_is_noop() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    locks.release(entry)
    locks.release(entry)
    locks.acquire("s1")


def test_wait_released_true_when_never_acquired() -> None:
    locks = _make_locks()
    assert locks.wait_released("nope", 0.05) is True


def test_wait_released_true_once_released() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    locks.release(entry)
    assert locks.wait_released("s1", 1.0) is True


def test_wait_released_false_on_timeout_when_never_released() -> None:
    locks = _make_locks()
    locks.acquire("s1")
    assert locks.wait_released("s1", 0.05) is False


def test_force_release_pops_entry_and_kills_proc() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    fake = _FakeProc()
    locks.set_proc(entry, fake)  # type: ignore[arg-type]
    locks.force_release("s1")
    assert fake.killed is True
    locks.acquire("s1")


def test_force_release_sets_released_event() -> None:
    locks = _make_locks()
    locks.acquire("s1")
    locks.force_release("s1")
    assert locks.wait_released("s1", 0.0) is True


def test_force_release_without_proc_does_not_crash() -> None:
    locks = _make_locks()
    locks.acquire("s1")
    locks.force_release("s1")


def test_force_release_unknown_session_is_noop() -> None:
    locks = _make_locks()
    locks.force_release("nope")


def test_force_release_suppresses_kill_failure() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    locks.set_proc(entry, _BoomOnKillProc())  # type: ignore[arg-type]
    locks.force_release("s1")


def test_real_subprocess_cancel_terminates_it() -> None:
    locks = _make_locks()
    entry = locks.acquire("s1")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    locks.set_proc(entry, proc)
    assert locks.cancel("s1") is True
    proc.wait(timeout=5)
    locks.release(entry)


def _register_acme_engine() -> None:
    engine_config.register(
        engine_config.EngineConfig(
            repo=engine_config.RepoIdentity(owner="acme", repo="widget")
        ),
        validate_env=False,
    )


class TestListSessionsEngineKeyFilter:
    def test_no_engine_key_is_unfiltered(self, tmp_path: Path) -> None:
        a = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
        b = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
        result = _chat_core.list_sessions(WS_CHAT_CONFIG, root=tmp_path)
        assert {s.session_id for s in result} == {a.session_id, b.session_id}

    def test_filters_to_matching_registered_engine(self, tmp_path: Path) -> None:
        _register_acme_engine()
        matching = _chat_core.create_session(
            WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget"
        )
        _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key=_ALAN_OWN_REPO_SLUG)
        result = _chat_core.list_sessions(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
        assert [s.session_id for s in result] == [matching.session_id]

    def test_default_repository_includes_unattributed_sessions(self, tmp_path: Path) -> None:
        _register_acme_engine()
        unattributed = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
        default_named = _chat_core.create_session(
            WS_CHAT_CONFIG, root=tmp_path, engine_key=_ALAN_OWN_REPO_SLUG
        )
        _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
        result = _chat_core.list_sessions(
            WS_CHAT_CONFIG, root=tmp_path, engine_key=_ALAN_OWN_REPO_SLUG
        )
        assert {s.session_id for s in result} == {unattributed.session_id, default_named.session_id}

    def test_unknown_engine_key_returns_empty(self, tmp_path: Path) -> None:
        _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
        result = _chat_core.list_sessions(WS_CHAT_CONFIG, root=tmp_path, engine_key="ghost/repo")
        assert result == []

    def test_filter_applies_before_the_list_cap(self, tmp_path: Path) -> None:
        _register_acme_engine()
        old = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
        path = _session_json_path(old, tmp_path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["created_at"] = datetime(1990, 1, 1, tzinfo=UTC).isoformat()
        _write_raw(path, raw)
        for _ in range(30):
            _chat_core.create_session(
                WS_CHAT_CONFIG, root=tmp_path, engine_key=_ALAN_OWN_REPO_SLUG
            )
        result = _chat_core.list_sessions(WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget")
        assert [s.session_id for s in result] == [old.session_id]

    def test_combines_with_project_id_filter(self, tmp_path: Path) -> None:
        _register_acme_engine()
        matching = _chat_core.create_session(
            WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget", project_id="proj-a"
        )
        _chat_core.create_session(
            WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget", project_id="proj-b"
        )
        _chat_core.create_session(
            WS_CHAT_CONFIG, root=tmp_path, engine_key=_ALAN_OWN_REPO_SLUG, project_id="proj-a"
        )
        result = _chat_core.list_sessions(
            WS_CHAT_CONFIG, root=tmp_path, engine_key="acme/widget", project_id="proj-a"
        )
        assert [s.session_id for s in result] == [matching.session_id]


def test_force_release_then_reacquire_survives_stale_entry_was_cancelled_and_release() -> None:
    locks = _make_locks()
    entry_a = locks.acquire("s1")
    locks.cancel("s1")
    locks.force_release("s1")

    entry_b = locks.acquire("s1")

    assert locks.was_cancelled(entry_a) is True
    locks.release(entry_a)

    fake_b = _FakeProc()
    locks.set_proc(entry_b, fake_b)  # type: ignore[arg-type]
    assert locks.was_cancelled(entry_b) is False
    assert locks.cancel("s1") is True
    assert fake_b.terminated is True
    locks.release(entry_b)
    locks.acquire("s1")
