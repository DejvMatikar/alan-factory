from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alan import _chat_core
from alan.build_chat import CONFIG as WS_CHAT_CONFIG
from alan.build_chat import BuildChatSession
from alan.claude_cli import ChatTurnResult, ClaudeCliError
from alan.dispatcher import PubSub
from alan.routes import _chat_router


class _TestChatLocked(_chat_core.ChatCoreError):
    pass


def _make_locks() -> _chat_core.ChatSessionLocks:
    return _chat_core.ChatSessionLocks(_TestChatLocked, "test-chat")


def _make_ctx() -> _chat_router.ChatRouterCtx[BuildChatSession]:
    return _chat_router.ChatRouterCtx(
        core=WS_CHAT_CONFIG,
        channel_prefix="test-chat-router-ops",
        label="test-chat",
        summarize_sentinel="",
        system_prompt=lambda _session: "sys",
    )


_READ_TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {
        "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}]
    },
}

_BASH_TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {
        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]
    },
}


def test_partial_text_tracker_observe_operation_appends_label() -> None:
    tracker = _chat_router._PartialTextTracker()
    tracker.observe_operation(_READ_TOOL_USE_EVENT)
    assert tracker.operations == ["Read: a.py"]


def test_partial_text_tracker_observe_operation_ignores_non_tool_use_event() -> None:
    tracker = _chat_router._PartialTextTracker()
    tracker.observe_operation(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    )
    assert tracker.operations == []


def test_partial_text_tracker_observe_operation_drops_oldest_at_max_stored() -> None:
    tracker = _chat_router._PartialTextTracker()
    tracker.operations = [f"Read: {i}.py" for i in range(_chat_router._MAX_STORED_OPERATIONS)]
    tracker.observe_operation(_READ_TOOL_USE_EVENT)
    assert len(tracker.operations) == _chat_router._MAX_STORED_OPERATIONS
    assert tracker.operations[0] == "Read: 1.py"
    assert tracker.operations[-1] == "Read: a.py"


def test_execute_turn_completed_persists_operations_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
        on_event = kwargs["on_event"]
        on_event(_READ_TOOL_USE_EVENT)
        on_event(_BASH_TOOL_USE_EVENT)
        return ChatTurnResult(result_text="done", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.transcript[-1].operations == ["Read: a.py", "Bash: ls"]


def test_execute_turn_completed_with_no_tool_use_persists_empty_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
        return ChatTurnResult(result_text="done", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.transcript[-1].operations == []


def test_execute_turn_stopped_persists_operations_observed_before_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
        on_event = kwargs["on_event"]
        on_event(_READ_TOOL_USE_EVENT)
        locks.cancel(session.session_id)
        raise ClaudeCliError("killed by cancel")

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.transcript[-1].operations == ["Read: a.py"]


def test_execute_turn_error_path_persists_no_assistant_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def boom(**kwargs: Any) -> ChatTurnResult:
        on_event = kwargs["on_event"]
        on_event(_READ_TOOL_USE_EVENT)
        raise ClaudeCliError("raw backend detail")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    kind, _data = _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    assert kind == "error"
    reloaded = _chat_core.load_session(WS_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.transcript == []
