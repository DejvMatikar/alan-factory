from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli
from alan.claude_cli import run_agent_streaming
from tests.test_claude_cli_streaming import _lines, _patch_popen, _result_event


def _rejected_rate_limit_line(resets_at: float = 1_000_000.0) -> str:
    return json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "rejected", "resetsAt": resets_at},
    })


def _allowed_rate_limit_line() -> str:
    return json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {"status": "allowed", "resetsAt": 1_000_000.0},
    })


def test_handle_raises_usage_limit_error_on_rejected_rate_limit_event() -> None:
    state = claude_cli._StreamPumpState(  # pyright: ignore[reportPrivateUsage]
        on_event=None, sink=None, on_session_id=None
    )
    events: list[tuple[str, dict[str, Any]]] = []
    with pytest.raises(claude_cli.UsageLimitError) as exc_info:
        state.handle(_rejected_rate_limit_line(1_000_000.0), events)
    assert exc_info.value.reset_at > 1_000_000.0
    assert len(events) == 1


def test_handle_does_not_raise_on_non_rejected_rate_limit_event() -> None:
    state = claude_cli._StreamPumpState(  # pyright: ignore[reportPrivateUsage]
        on_event=None, sink=None, on_session_id=None
    )
    events: list[tuple[str, dict[str, Any]]] = []
    state.handle(_allowed_rate_limit_line(), events)
    assert len(events) == 1


@pytest.mark.parametrize(
    "line",
    [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sid-1"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}),
    ],
)
def test_handle_leaves_non_rate_limit_lines_unaffected(line: str) -> None:
    state = claude_cli._StreamPumpState(  # pyright: ignore[reportPrivateUsage]
        on_event=None, sink=None, on_session_id=None
    )
    events: list[tuple[str, dict[str, Any]]] = []
    state.handle(line, events)
    assert len(events) == 1


def test_handle_tees_and_emits_before_raising_on_rejected_rate_limit(
    tmp_path: Path,
) -> None:
    sink_path = tmp_path / "sink.jsonl"
    seen: list[dict[str, Any]] = []
    with sink_path.open("w", encoding="utf-8") as sink:
        state = claude_cli._StreamPumpState(  # pyright: ignore[reportPrivateUsage]
            on_event=seen.append, sink=sink, on_session_id=None
        )
        events: list[tuple[str, dict[str, Any]]] = []
        with pytest.raises(claude_cli.UsageLimitError):
            state.handle(_rejected_rate_limit_line(), events)
    assert len(seen) == 1
    assert sink_path.read_text(encoding="utf-8").strip() == _rejected_rate_limit_line()


def test_run_agent_streaming_raises_on_mid_stream_rejection_despite_later_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "session_id": "sid-1"},
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": 1_000_000.0},
        },
        {"type": "assistant", "message": {"content": "retrying internally"}},
        _result_event("succeeded after internal retry"),
    ]
    _patch_popen(monkeypatch, stdout_lines=_lines(*events))
    with pytest.raises(claude_cli.UsageLimitError) as exc_info:
        run_agent_streaming("sys", "user")
    assert exc_info.value.reset_at > 1_000_000.0
