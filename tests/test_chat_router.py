from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException

from alan import _chat_core
from alan import collision as collision_mod
from alan.build_chat import CONFIG as TEST_CHAT_CONFIG
from alan.build_chat import BuildChatSession
from alan.claude_cli import (
    DISPATCH_PROPOSAL_SENTINEL,
    ChatTurnResult,
    ClaudeCliError,
    ClaudeCliTimeout,
    ResumeSessionLostError,
    UsageLimitError,
)
from alan.dispatcher import PubSub
from alan.routes import _chat_router
from alan.scope_audit import ScopeGapFinding


def _detail(exc: HTTPException) -> dict[str, str]:
    return cast(dict[str, str], exc.detail)


_COUNTER_CASE = {
    "against": "This locks in scope before the shop has bandwidth to review it.",
    "cheapest_80": "Ship the smallest read-only slice first; defer the rest.",
    "cost_estimate": "insufficient data",
}


def _ready_yaml(**proposal_overrides: Any) -> str:
    proposal = {
        "team": "implementation_plan",
        "rationale": "go",
        "counter_case": _COUNTER_CASE,
        **proposal_overrides,
    }
    return json.dumps({"status": "ready", "proposal": proposal})


class _TestChatLocked(_chat_core.ChatCoreError):
    pass


def _make_locks() -> _chat_core.ChatSessionLocks:
    return _chat_core.ChatSessionLocks(_TestChatLocked, "test-chat")


def _make_ctx() -> _chat_router.ChatRouterCtx[BuildChatSession]:
    return _chat_router.ChatRouterCtx(
        core=TEST_CHAT_CONFIG,
        channel_prefix="test-chat-router",
        label="test-chat",
        summarize_sentinel="",
        system_prompt=lambda _session: "sys",
    )


def test_parse_dispatch_proposal_yaml_ready() -> None:
    parsed = _chat_router.parse_dispatch_proposal_yaml(_ready_yaml())
    assert isinstance(parsed, _chat_router.DispatchProposalReady)
    assert parsed.proposal.team == "implementation_plan"
    assert parsed.proposal.counter_case.cost_estimate == "insufficient data"


def test_dispatch_proposal_ready_default_collision_is_green() -> None:
    parsed = _chat_router.parse_dispatch_proposal_yaml(_ready_yaml())
    assert isinstance(parsed, _chat_router.DispatchProposalReady)
    assert parsed.collision.state == "green"
    assert parsed.collision.sentence == "No overlapping work found."
    assert parsed.collision.match is None


class TestCollisionVerdictOut:
    def test_green_no_match(self) -> None:
        out = _chat_router.collision_verdict_out(collision_mod.SAFE_RESULT)
        assert out.state == "green"
        assert out.sentence == "No overlapping work found."
        assert out.match is None

    def test_yellow_with_workspace_match(self) -> None:
        match = collision_mod.CollisionMatch(
            label="auth ws", kind="workspace", deep_link="/workspaces/ws-1"
        )
        result = collision_mod.CollisionResult(state="yellow", sentence="similar", match=match)
        out = _chat_router.collision_verdict_out(result)
        assert out.state == "yellow"
        assert out.sentence == "similar"
        assert out.match is not None
        assert out.match.kind == "workspace"
        assert out.match.label == "auth ws"
        assert out.match.deep_link == "/workspaces/ws-1"

    def test_red_with_pr_match(self) -> None:
        match = collision_mod.CollisionMatch(label="PR #1", kind="pr", deep_link="https://gh/1")
        result = collision_mod.CollisionResult(state="red", sentence="live overlap", match=match)
        out = _chat_router.collision_verdict_out(result)
        assert out.state == "red"
        assert out.match is not None
        assert out.match.kind == "pr"


class TestSafeCollisionCheck:
    async def test_returns_result_on_success(self) -> None:
        red = collision_mod.CollisionResult(state="red", sentence="overlap", match=None)

        async def collision() -> collision_mod.CollisionResult:
            return red

        result = await _chat_router._safe_collision_check(collision)
        assert result is red

    async def test_degrades_to_safe_on_exception(self) -> None:
        async def boom() -> collision_mod.CollisionResult:
            raise RuntimeError("collision exploded")

        result = await _chat_router._safe_collision_check(boom)
        assert result is collision_mod.SAFE_RESULT


class TestCancelWithGrace:
    async def test_awaits_already_done_task(self) -> None:
        async def quick() -> str:
            return "ok"

        task = asyncio.create_task(quick())
        await task
        await _chat_router._cancel_with_grace(task)
        assert task.done()

    async def test_suppresses_exception_on_already_done_task(self) -> None:
        async def boom() -> None:
            raise RuntimeError("already failed")

        task = asyncio.create_task(boom())
        with contextlib.suppress(RuntimeError):
            await task
        await _chat_router._cancel_with_grace(task)
        assert task.done()

    async def test_cancels_pending_task_within_grace(self) -> None:
        cancelled = False

        async def slow() -> None:
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0.01)
        await _chat_router._cancel_with_grace(task)
        assert cancelled
        assert task.cancelled()


def test_parse_dispatch_proposal_yaml_strips_code_fence() -> None:
    fenced = f"```yaml\n{_ready_yaml()}```"
    parsed = _chat_router.parse_dispatch_proposal_yaml(fenced)
    assert isinstance(parsed, _chat_router.DispatchProposalReady)


def test_parse_dispatch_proposal_yaml_empty_raises() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml("")
    assert exc_info.value.status_code == 422
    assert "empty response" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_need_more_info() -> None:
    raw = json.dumps({"status": "need_more_info", "questions": ["Which team exactly?"]})
    parsed = _chat_router.parse_dispatch_proposal_yaml(raw)
    assert isinstance(parsed, _chat_router.DispatchProposalNeedsInfo)
    assert parsed.questions == ["Which team exactly?"]


def test_parse_dispatch_proposal_yaml_non_mapping_top_level() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml("not yaml at all")
    assert exc_info.value.status_code == 422
    detail = _detail(exc_info.value)
    assert detail["detail"] == "dispatch_proposal_parse_failed"
    assert detail["reason"] == "top-level YAML value is not a mapping"


def test_parse_dispatch_proposal_yaml_syntax_error() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml("status: ready\n\tproposal: bad\n")
    assert exc_info.value.status_code == 422
    assert "yaml extract failed" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_unknown_status() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(json.dumps({"status": "weird"}))
    assert exc_info.value.status_code == 422
    assert "unknown status" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_missing_proposal() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(json.dumps({"status": "ready"}))
    assert exc_info.value.status_code == 422
    assert "proposal missing" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_unknown_team_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(_ready_yaml(team="nonexistent_team"))
    assert exc_info.value.status_code == 422
    assert "not manually dispatchable" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_decomposer_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(_ready_yaml(team="decomposer"))
    assert exc_info.value.status_code == 422
    assert "not manually dispatchable" in _detail(exc_info.value)["reason"]


def test_parse_dispatch_proposal_yaml_missing_counter_case_rejected() -> None:
    raw = json.dumps(
        {"status": "ready", "proposal": {"team": "implementation_plan", "rationale": "x"}}
    )
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(raw)
    assert exc_info.value.status_code == 422
    assert _detail(exc_info.value)["detail"] == "dispatch_proposal_parse_failed"


def test_parse_dispatch_proposal_yaml_questions_not_a_list() -> None:
    raw = json.dumps({"status": "need_more_info", "questions": "not_a_list"})
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(raw)
    assert exc_info.value.status_code == 422
    assert "questions must be a list of strings" in _detail(exc_info.value)["reason"]


def test_dispatch_proposal_parse_detail_constant() -> None:
    assert _chat_router.DISPATCH_PROPOSAL_PARSE_DETAIL == "dispatch_proposal_parse_failed"


def test_parse_dispatch_proposal_yaml_allowed_new_workspace_teams_accepts_consultant_team() -> None:
    raw = _ready_yaml(team="brainstorming", title="explore-topic")
    parsed = _chat_router.parse_dispatch_proposal_yaml(
        raw, allowed_new_workspace_teams=frozenset({"brainstorming"})
    )
    assert isinstance(parsed, _chat_router.DispatchProposalReady)
    assert parsed.proposal.team == "brainstorming"
    assert parsed.proposal.title == "explore-topic"


def test_parse_dispatch_proposal_yaml_without_context_rejects_consultant_team_with_title() -> None:
    raw = _ready_yaml(team="brainstorming", title="explore-topic")
    with pytest.raises(HTTPException) as exc_info:
        _chat_router.parse_dispatch_proposal_yaml(raw)
    assert exc_info.value.status_code == 422


async def _empty_scan() -> list[ScopeGapFinding]:
    return []


async def _empty_collision() -> collision_mod.CollisionResult:
    return collision_mod.SAFE_RESULT


def _stub_run_chat_turn(monkeypatch: pytest.MonkeyPatch, result_text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> ChatTurnResult:
        calls.append(kwargs)
        return ChatTurnResult(result_text=result_text, claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    return calls


async def test_run_dispatch_proposal_ready_merges_scan_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="Open PR overlaps",
        references=["PR #99"],
        suggested_action="needs_user_call",
    )

    async def scan() -> list[ScopeGapFinding]:
        return [finding]

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=scan,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert len(result.proposal.scope_audit) == 1
    assert result.proposal.scope_audit[0].kind == "in_flight_pr"


async def test_run_dispatch_proposal_sends_dispatch_proposal_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=_empty_collision,
    )
    assert calls[0]["user_text"] == DISPATCH_PROPOSAL_SENTINEL


async def test_run_dispatch_proposal_need_more_info_awaits_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps({"status": "need_more_info", "questions": ["Which team exactly?"]})
    _stub_run_chat_turn(monkeypatch, raw)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    scan_called: list[bool] = []

    async def scan() -> list[ScopeGapFinding]:
        scan_called.append(True)
        return []

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=scan,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalNeedsInfo)
    assert scan_called == [True]


async def test_run_dispatch_proposal_scan_failure_falls_back_to_model_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()

    async def boom() -> list[ScopeGapFinding]:
        raise RuntimeError("scan exploded")

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=boom,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert result.proposal.scope_audit == []


async def test_run_dispatch_proposal_cli_failure_raises_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ClaudeCliError("CLI exploded")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    with pytest.raises(HTTPException) as exc_info:
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=_make_locks(),
            scan=_empty_scan,
            collision=_empty_collision,
        )
    assert exc_info.value.status_code == 502


async def test_run_dispatch_proposal_409_when_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    locks = _make_locks()
    locks.acquire(session.session_id)
    with pytest.raises(HTTPException) as exc_info:
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=locks,
            scan=_empty_scan,
            collision=_empty_collision,
        )
    assert exc_info.value.status_code == 409


async def test_run_dispatch_proposal_releases_lock_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    locks = _make_locks()
    await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=locks,
        scan=_empty_scan,
        collision=_empty_collision,
    )
    entry = locks.acquire(session.session_id)
    locks.release(entry)


async def test_run_dispatch_proposal_releases_lock_on_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ClaudeCliError("CLI exploded")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    locks = _make_locks()
    with pytest.raises(HTTPException):
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=locks,
            scan=_empty_scan,
            collision=_empty_collision,
        )
    entry = locks.acquire(session.session_id)
    locks.release(entry)


async def test_run_dispatch_proposal_scan_starts_before_turn_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    def fake(**kwargs: Any) -> ChatTurnResult:
        time.sleep(0.05)
        events.append("turn_done")
        return ChatTurnResult(result_text=_ready_yaml(), claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()

    async def scan() -> list[ScopeGapFinding]:
        events.append("scan_started")
        return []

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=scan,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert events == ["scan_started", "turn_done"]


async def test_run_dispatch_proposal_scope_audit_merge_order_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal_finding = {
        "kind": "anti_scope",
        "summary": "from model",
        "references": [],
        "suggested_action": "out_of_scope",
    }
    _stub_run_chat_turn(monkeypatch, _ready_yaml(scope_audit=[proposal_finding]))
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    scan_finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="from scan",
        references=["PR #1"],
        suggested_action="needs_user_call",
    )

    async def scan() -> list[ScopeGapFinding]:
        return [scan_finding]

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=scan,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert [f.summary for f in result.proposal.scope_audit] == ["from scan", "from model"]


async def test_run_dispatch_proposal_cancels_pending_scan_on_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ClaudeCliError("CLI exploded")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    locks = _make_locks()
    cancelled = False

    async def slow_scan() -> list[ScopeGapFinding]:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return []

    with pytest.raises(HTTPException):
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=locks,
            scan=slow_scan,
            collision=_empty_collision,
        )
    assert cancelled
    entry = locks.acquire(session.session_id)
    locks.release(entry)


async def test_run_dispatch_proposal_ready_attaches_collision_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    match = collision_mod.CollisionMatch(
        label="dup ws", kind="workspace", deep_link="/workspaces/dup"
    )
    red = collision_mod.CollisionResult(state="red", sentence="Live overlap.", match=match)

    async def collision() -> collision_mod.CollisionResult:
        return red

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert result.collision.state == "red"
    assert result.collision.sentence == "Live overlap."
    assert result.collision.match is not None
    assert result.collision.match.label == "dup ws"


async def test_run_dispatch_proposal_collision_starts_before_turn_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    def fake(**kwargs: Any) -> ChatTurnResult:
        time.sleep(0.05)
        events.append("turn_done")
        return ChatTurnResult(result_text=_ready_yaml(), claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()

    async def collision() -> collision_mod.CollisionResult:
        events.append("collision_started")
        return collision_mod.SAFE_RESULT

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert events == ["collision_started", "turn_done"]


async def test_run_dispatch_proposal_need_more_info_awaits_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps({"status": "need_more_info", "questions": ["Which team exactly?"]})
    _stub_run_chat_turn(monkeypatch, raw)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    collision_called: list[bool] = []

    async def collision() -> collision_mod.CollisionResult:
        collision_called.append(True)
        return collision_mod.SAFE_RESULT

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalNeedsInfo)
    assert collision_called == [True]


async def test_run_dispatch_proposal_collision_failure_falls_back_to_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml())
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()

    async def boom() -> collision_mod.CollisionResult:
        raise RuntimeError("collision exploded")

    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=boom,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert result.collision.state == "green"


async def test_run_dispatch_proposal_cancels_pending_collision_on_cli_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ClaudeCliError("CLI exploded")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    locks = _make_locks()
    cancelled = False

    async def slow_collision() -> collision_mod.CollisionResult:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return collision_mod.SAFE_RESULT

    with pytest.raises(HTTPException):
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=locks,
            scan=_empty_scan,
            collision=slow_collision,
        )
    assert cancelled
    entry = locks.acquire(session.session_id)
    locks.release(entry)


async def test_run_dispatch_proposal_threads_allowed_new_workspace_teams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml(team="brainstorming", title="explore-topic"))
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=_empty_collision,
        allowed_new_workspace_teams=frozenset({"brainstorming"}),
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert result.proposal.team == "brainstorming"
    assert result.proposal.title == "explore-topic"


async def test_run_dispatch_proposal_default_allowed_teams_rejects_consultant_team_with_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_chat_turn(monkeypatch, _ready_yaml(team="brainstorming", title="explore-topic"))
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    with pytest.raises(HTTPException) as exc_info:
        await _chat_router.run_dispatch_proposal(
            ctx=ctx,
            session=session,
            root=tmp_path,
            pubsub=PubSub(),
            locks=_make_locks(),
            scan=_empty_scan,
            collision=_empty_collision,
        )
    assert exc_info.value.status_code == 422


def test_do_turn_hookless_ctx_uses_static_cwd_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, "hi")
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    ctx = dataclasses.replace(_make_ctx(), cwd=Path("/static/cwd"))
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=ctx,
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert calls[0]["cwd"] == Path("/static/cwd")


def test_do_turn_pre_turn_runs_and_resolve_cwd_overrides_static_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, "hi")
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    pre_turn_calls: list[tuple[str, Path]] = []

    def pre_turn(sess: Any, root: Path) -> None:
        pre_turn_calls.append((sess.session_id, root))

    def resolve_cwd(session_id: str, root: Path) -> Path | None:
        assert session_id == session.session_id
        assert root == tmp_path
        return Path("/resolved/cwd")

    ctx = dataclasses.replace(
        _make_ctx(), cwd=Path("/static/cwd"), pre_turn=pre_turn, resolve_cwd=resolve_cwd
    )
    locks = _make_locks()
    result = _chat_router.do_turn(
        ctx=ctx,
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert result.result_text == "hi"
    assert pre_turn_calls == [(session.session_id, tmp_path)]
    assert calls[0]["cwd"] == Path("/resolved/cwd")


def test_do_turn_pre_turn_failure_short_circuits_before_run_chat_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run_chat_turn(**kwargs: Any) -> ChatTurnResult:
        raise AssertionError("run_chat_turn must not be called when pre_turn raises")

    monkeypatch.setattr(_chat_router, "run_chat_turn", unexpected_run_chat_turn)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)

    def boom_pre_turn(sess: Any, root: Path) -> None:
        raise ClaudeCliError("provisioning failed")

    ctx = dataclasses.replace(_make_ctx(), pre_turn=boom_pre_turn)
    locks = _make_locks()
    with pytest.raises(ClaudeCliError, match="provisioning failed"):
        _chat_router.do_turn(
            ctx=ctx,
            session=session,
            user_text="hi",
            pubsub=PubSub(),
            root=tmp_path,
            locks=locks,
            entry=locks.acquire(session.session_id),
        )


def test_do_turn_no_cli_session_id_does_not_resume_and_mints_fresh_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, "hi")
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert calls[0]["resume"] is False
    assert calls[0]["session_id"] != session.session_id
    _chat_core.validate_session_id(TEST_CHAT_CONFIG, calls[0]["session_id"])


def test_do_turn_with_cli_session_id_resumes_using_recorded_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, "hi")
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    session = _chat_core.update_session(
        TEST_CHAT_CONFIG, session.session_id, {"cli_session_id": "cli-abc"}, root=tmp_path
    )
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert calls[0]["resume"] is True
    assert calls[0]["session_id"] == "cli-abc"


def test_do_turn_transcript_history_no_longer_drives_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn(monkeypatch, "hi")
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    session = _chat_core.append_assistant_and_clear_in_flight(
        TEST_CHAT_CONFIG,
        session,
        _chat_core.ChatMessage(role="assistant", content="prior reply", ts=datetime.now(UTC)),
        root=tmp_path,
    )
    assert session.cli_session_id is None
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert calls[0]["resume"] is False


def test_do_turn_emits_chat_event_without_partial_tracker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(**kwargs: Any) -> ChatTurnResult:
        kwargs["on_event"]({"type": "system"})
        return ChatTurnResult(result_text="hi", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    pubsub = PubSub()
    received: list[Any] = []
    pubsub.subscribe(f"test-chat-router:{session.session_id}", received.append)
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=pubsub,
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    assert [e.kind for e in received] == ["chat_event"]


def test_do_turn_on_session_id_persists_cli_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(**kwargs: Any) -> ChatTurnResult:
        kwargs["on_session_id"]("cli-live-123")
        return ChatTurnResult(result_text="hi", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    locks = _make_locks()
    _chat_router.do_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=locks.acquire(session.session_id),
    )
    reloaded = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.cli_session_id == "cli-live-123"


def test_do_turn_clears_cli_session_id_on_resume_session_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    session = _chat_core.update_session(
        TEST_CHAT_CONFIG, session.session_id, {"cli_session_id": "cli-gone"}, root=tmp_path
    )

    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ResumeSessionLostError("cli-gone")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    locks = _make_locks()
    with pytest.raises(ResumeSessionLostError):
        _chat_router.do_turn(
            ctx=_make_ctx(),
            session=session,
            user_text="hi",
            pubsub=PubSub(),
            root=tmp_path,
            locks=locks,
            entry=locks.acquire(session.session_id),
        )
    reloaded = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.cli_session_id is None


def test_plain_chat_error_text_usage_limit() -> None:
    exc = UsageLimitError("raw usage limit detail", 0.0)
    assert _chat_router._plain_chat_error_text(exc) == _chat_router._USAGE_LIMIT_CHAT_ERROR_TEXT


def test_plain_chat_error_text_timeout() -> None:
    exc = ClaudeCliTimeout("raw timeout detail")
    assert _chat_router._plain_chat_error_text(exc) == _chat_router._TIMEOUT_CHAT_ERROR_TEXT


def test_plain_chat_error_text_resume_session_lost() -> None:
    exc = ResumeSessionLostError("sid-1")
    assert _chat_router._plain_chat_error_text(exc) == _chat_router._SESSION_LOST_CHAT_ERROR_TEXT


def test_plain_chat_error_text_generic_fallback() -> None:
    exc = ClaudeCliError("raw detail nobody should see")
    assert _chat_router._plain_chat_error_text(exc) == _chat_router._GENERIC_CHAT_ERROR_TEXT


def test_assistant_event_text_extracts_text_blocks() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
        },
    }
    assert _chat_router._assistant_event_text(event) == "hello world"


def test_assistant_event_text_ignores_non_text_blocks() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Read"}, {"type": "text", "text": "ok"}]
        },
    }
    assert _chat_router._assistant_event_text(event) == "ok"


def test_assistant_event_text_ignores_non_dict_blocks() -> None:
    event = {"type": "assistant", "message": {"content": ["not-a-dict"]}}
    assert _chat_router._assistant_event_text(event) == ""


def test_assistant_event_text_non_assistant_type_returns_empty() -> None:
    assert _chat_router._assistant_event_text({"type": "system"}) == ""


def test_assistant_event_text_missing_message_returns_empty() -> None:
    assert _chat_router._assistant_event_text({"type": "assistant"}) == ""


def test_assistant_event_text_non_dict_message_returns_empty() -> None:
    assert _chat_router._assistant_event_text({"type": "assistant", "message": "oops"}) == ""


def test_assistant_event_text_non_list_content_returns_empty() -> None:
    event = {"type": "assistant", "message": {"content": "oops"}}
    assert _chat_router._assistant_event_text(event) == ""


def test_partial_text_tracker_joins_multiple_events() -> None:
    tracker = _chat_router._PartialTextTracker()
    tracker.observe(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}
    )
    tracker.observe({"type": "system"})
    tracker.observe(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}}
    )
    assert tracker.text() == "first\n\nsecond"


def test_partial_text_tracker_empty_when_no_text_observed() -> None:
    tracker = _chat_router._PartialTextTracker()
    tracker.observe({"type": "system"})
    assert tracker.text() == ""


def test_execute_turn_cancelled_turn_yields_turn_stopped_with_partial_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
        on_event = kwargs["on_event"]
        on_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "partial reply"}]},
            }
        )
        locks.cancel(session.session_id)
        raise ClaudeCliError("killed by cancel")

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    kind, data = _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    assert kind == "turn_stopped"
    assert data == {"content": "partial reply"}
    reloaded = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert reloaded.transcript[-1].content == "partial reply"
    assert reloaded.transcript[-1].interrupted is True
    assert reloaded.transcript[-1].role == "assistant"
    assert reloaded.in_flight_turn is None


def test_execute_turn_cancelled_turn_with_no_partial_text_yields_empty_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
        locks.cancel(session.session_id)
        raise ClaudeCliError("killed before any output")

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    kind, data = _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    assert kind == "turn_stopped"
    assert data == {"content": ""}


def test_execute_turn_uncancelled_cli_error_maps_to_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def boom(**kwargs: Any) -> ChatTurnResult:
        raise ClaudeCliError("raw backend detail")

    monkeypatch.setattr(_chat_router, "run_chat_turn", boom)
    kind, data = _chat_router.execute_turn(
        ctx=_make_ctx(),
        session=session,
        user_text="hi",
        pubsub=PubSub(),
        root=tmp_path,
        locks=locks,
        entry=entry,
    )
    assert kind == "error"
    assert data["detail"] == _chat_router._GENERIC_CHAT_ERROR_TEXT
    assert "raw backend detail" not in data["detail"]


def test_execute_turn_releases_lock_after_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locks = _make_locks()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    entry = locks.acquire(session.session_id)

    def fake(**kwargs: Any) -> ChatTurnResult:
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
    locks.acquire(session.session_id)


async def test_run_interrupt_204_when_idle(tmp_path: Path) -> None:
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    res = await _chat_router.run_interrupt(_make_ctx(), session.session_id, tmp_path, _make_locks())
    assert res.status_code == 204


async def test_run_interrupt_404_for_unknown_session(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _chat_router.run_interrupt(
            _make_ctx(), "12345678-1234-1234-1234-1234567890ab", tmp_path, _make_locks()
        )
    assert exc_info.value.status_code == 404


async def test_run_interrupt_204_waits_for_prompt_release(tmp_path: Path) -> None:
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    locks = _make_locks()
    entry = locks.acquire(session.session_id)

    def release_soon() -> None:
        time.sleep(0.02)
        locks.release(entry)

    thread = threading.Thread(target=release_soon)
    thread.start()
    res = await _chat_router.run_interrupt(_make_ctx(), session.session_id, tmp_path, locks)
    thread.join()
    assert res.status_code == 204


async def test_run_interrupt_force_releases_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    locks = _make_locks()
    locks.acquire(session.session_id)
    monkeypatch.setattr(_chat_router, "_RELEASE_WAIT_SECONDS", 0.02)
    res = await _chat_router.run_interrupt(_make_ctx(), session.session_id, tmp_path, locks)
    assert res.status_code == 204
    locks.acquire(session.session_id)
