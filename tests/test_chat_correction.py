from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException

from alan import _chat_core
from alan import collision as collision_mod
from alan.block_correction import BlockCorrectionError
from alan.build_chat import CONFIG as WS_CHAT_CONFIG
from alan.build_chat import BuildChatSession
from alan.claude_cli import ChatTurnResult, ClaudeCliError
from alan.dispatcher import PubSub
from alan.routes import _chat_correction, _chat_router
from alan.scope_audit import ScopeGapFinding
from alan.teams import _block_correction_audit as bca

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
    return _chat_core.ChatSessionLocks(_TestChatLocked, "test-chat-correction")


def _make_ctx() -> _chat_router.ChatRouterCtx[BuildChatSession]:
    return _chat_router.ChatRouterCtx(
        core=WS_CHAT_CONFIG,
        channel_prefix="test-chat-correction",
        label="test-chat-correction",
        summarize_sentinel="",
        system_prompt=lambda _session: "sys",
    )


async def _empty_scan() -> list[ScopeGapFinding]:
    return []


async def _empty_collision() -> collision_mod.CollisionResult:
    return collision_mod.SAFE_RESULT


def _stub_run_chat_turn_sequence(
    monkeypatch: pytest.MonkeyPatch, texts: list[str]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    results = iter(texts)

    def fake(**kwargs: Any) -> ChatTurnResult:
        calls.append(kwargs)
        return ChatTurnResult(result_text=next(results), claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)
    return calls


@pytest.fixture(autouse=True)
def _isolate_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


def _read_bc_rows() -> list[dict[str, object]]:
    path = bca.audit_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_correction_user_text_mentions_the_parser_error() -> None:
    text = _chat_correction.correction_user_text("top-level YAML value is not a mapping")
    assert "top-level YAML value is not a mapping" in text


def test_run_dispatch_correction_clean_on_first_success() -> None:
    outcome = _chat_correction.run_dispatch_correction(
        "ready text",
        validate=lambda t: t.upper(),
        do_turn_again=lambda _e: "never called",
        role="workspace_chat",
    )
    assert outcome.outcome == "clean"
    assert outcome.value == "READY TEXT"


def test_run_dispatch_correction_recovers_via_do_turn_again() -> None:
    def _validate(text: str) -> str:
        if text != "good":
            raise ValueError("bad")
        return text.upper()

    outcome = _chat_correction.run_dispatch_correction(
        "bad", validate=_validate, do_turn_again=lambda _e: "good", role="workspace_chat",
    )
    assert outcome.outcome == "corrected"
    assert outcome.value == "GOOD"


def test_run_dispatch_correction_treats_claude_cli_error_as_unreachable() -> None:
    def _validate(text: str) -> str:
        raise ValueError("bad")

    def _boom(error: str) -> str | None:
        raise ClaudeCliError("session dead")

    outcome = _chat_correction.run_dispatch_correction(
        "bad", validate=_validate, do_turn_again=_boom, role="workspace_chat",
    )
    assert outcome.outcome == "unreachable_author"


def test_run_dispatch_correction_writes_audit_row(tmp_path: Path) -> None:
    _chat_correction.run_dispatch_correction(
        "ready text", validate=lambda t: t, do_turn_again=lambda _e: None, role="workspace_chat",
        task_id="t9",
    )
    rows = _read_bc_rows()
    assert rows[0]["site"] == "EXTRACT_DISPATCH_YAML"
    assert rows[0]["outcome"] == "clean"
    assert rows[0]["task_id"] == "t9"


def test_validate_dispatch_yaml_shape_raises_parse_error_on_bad_yaml() -> None:
    with pytest.raises(BlockCorrectionError) as exc_info:
        _chat_router._validate_dispatch_yaml_shape("status: ready\n\tproposal: bad\n")  # noqa: SLF001
    assert exc_info.value.kind == "parse_error"


def test_validate_dispatch_yaml_shape_raises_on_non_mapping() -> None:
    with pytest.raises(BlockCorrectionError, match="not a mapping"):
        _chat_router._validate_dispatch_yaml_shape("not yaml at all")  # noqa: SLF001


def test_validate_dispatch_yaml_shape_returns_dict_on_success() -> None:
    obj = _chat_router._validate_dispatch_yaml_shape(_ready_yaml())  # noqa: SLF001
    assert obj["status"] == "ready"


async def test_run_dispatch_proposal_recovers_from_malformed_yaml_via_second_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn_sequence(
        monkeypatch, ["not yaml at all", _ready_yaml()]
    )
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    result = await _chat_router.run_dispatch_proposal(
        ctx=ctx,
        session=session,
        root=tmp_path,
        pubsub=PubSub(),
        locks=_make_locks(),
        scan=_empty_scan,
        collision=_empty_collision,
    )
    assert isinstance(result, _chat_router.DispatchProposalReady)
    assert len(calls) == 2
    assert calls[1]["user_text"] != calls[0]["user_text"]
    rows = _read_bc_rows()
    assert rows[0]["outcome"] == "corrected"
    assert rows[0]["attempts"] == 2


async def test_run_dispatch_proposal_exhausts_correction_and_raises_original_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn_sequence(
        monkeypatch, ["not yaml at all", "status: ready\n\tproposal: bad\n"]
    )
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
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
    detail = cast(dict[str, str], exc_info.value.detail)
    assert detail["detail"] == _chat_router.DISPATCH_PROPOSAL_PARSE_DETAIL
    assert len(calls) == 2
    rows = _read_bc_rows()
    assert rows[0]["outcome"] == "exhausted"


async def test_run_dispatch_proposal_logs_the_last_attempt_not_the_first_when_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _stub_run_chat_turn_sequence(
        monkeypatch,
        ["first-turn-garbage", "status: ready\n\tsecond-turn-garbage: bad\n"],
    )
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()
    with caplog.at_level(logging.DEBUG, logger="alan.server"):
        with pytest.raises(HTTPException):
            await _chat_router.run_dispatch_proposal(
                ctx=ctx,
                session=session,
                root=tmp_path,
                pubsub=PubSub(),
                locks=_make_locks(),
                scan=_empty_scan,
                collision=_empty_collision,
            )
    logged = "\n".join(
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    )
    assert "second-turn-garbage" in logged
    assert "first-turn-garbage" not in logged


async def test_run_dispatch_proposal_unreachable_session_falls_through_to_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn_sequence(monkeypatch, ["not yaml at all"])
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
    ctx = _make_ctx()

    def _boom(*_a: Any, **_kw: Any) -> _chat_core.ChatSession:
        raise _chat_core.ChatCoreError("session gone")

    monkeypatch.setattr(_chat_core, "load_session", _boom)
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
    assert len(calls) == 1
    rows = _read_bc_rows()
    assert rows[0]["outcome"] == "unreachable_author"


async def test_run_dispatch_proposal_judgment_failure_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_run_chat_turn_sequence(
        monkeypatch, [_ready_yaml(team="nonexistent_team"), _ready_yaml()]
    )
    session = _chat_core.create_session(WS_CHAT_CONFIG, root=tmp_path)
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
    assert len(calls) == 1
    rows = _read_bc_rows()
    assert rows[0]["outcome"] == "clean"
