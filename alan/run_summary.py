from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from alan import store
from alan.claude_cli import _effort_for_model  # pyright: ignore[reportPrivateUsage]

DEFAULT_CHAT_MODEL = "opus"


class ToolUseCount(BaseModel):
    name: str
    count: int


class RunSummary(BaseModel):
    model: str
    effort: str | None
    system_prompt: str
    total_cost_usd: float | None = None
    total_output_tokens: int | None = None
    total_input_tokens: int | None = None
    tool_counts: list[ToolUseCount] = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list
    )


def _read_stream_events(stream_path: Path) -> list[dict[str, Any]]:
    try:
        text = stream_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return store.parse_jsonl_rows(text)


def _assistant_content_blocks(event: dict[str, Any]) -> list[object]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    message = cast(dict[str, Any], message)
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return cast("list[object]", content)


def _tool_use_name(block: object) -> str | None:
    if not isinstance(block, dict):
        return None
    block = cast(dict[str, Any], block)
    if block.get("type") != "tool_use":
        return None
    name = block.get("name")
    return name if isinstance(name, str) else None


def _tool_use_names(event: dict[str, Any]) -> list[str]:
    names = (_tool_use_name(block) for block in _assistant_content_blocks(event))
    return [name for name in names if name is not None]


_TOOL_USE_LABEL_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Bash": "command",
    "Grep": "pattern",
    "Glob": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
}
_TOOL_USE_LABEL_MAX_LEN = 80


def tool_use_label(name: str, input: dict[str, Any] | None) -> str:
    key = _TOOL_USE_LABEL_KEYS.get(name)
    if key is not None and input is not None:
        value = input.get(key)
        if isinstance(value, str) and value:
            return f"{name}: {value[:_TOOL_USE_LABEL_MAX_LEN]}"
    return name


def _first_tool_use_block(event: dict[str, Any]) -> dict[str, Any] | None:
    for block in _assistant_content_blocks(event):
        if not isinstance(block, dict):
            continue
        block = cast(dict[str, Any], block)
        if block.get("type") == "tool_use":
            return block
    return None


def tool_use_event_label(event: dict[str, Any]) -> str | None:
    block = _first_tool_use_block(event)
    if block is None:
        return None
    name = block.get("name")
    if not isinstance(name, str):
        return "?"
    input_obj = block.get("input")
    if isinstance(input_obj, dict):
        return tool_use_label(name, cast(dict[str, Any], input_obj))
    return tool_use_label(name, None)


def _accumulate_tool_counts(events: list[dict[str, Any]]) -> list[ToolUseCount]:
    counts: dict[str, int] = {}
    for event in events:
        for name in _tool_use_names(event):
            counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [ToolUseCount(name=name, count=count) for name, count in ordered]


def _int_usage_field(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _add_float(total: float | None, amount: float) -> float:
    return (total or 0.0) + amount


def _add_int(total: int | None, amount: int) -> int:
    return (total or 0) + amount


def _result_cost(event: dict[str, Any]) -> float | None:
    cost = event.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        return float(cost)
    return None


def _result_usage_tokens(event: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None, None
    usage = cast(dict[str, Any], usage)
    return (
        _int_usage_field(usage, "input_tokens"),
        _int_usage_field(usage, "output_tokens"),
    )


def _apply_result_event(
    event: dict[str, Any],
    total_cost: float | None,
    total_output: int | None,
    total_input: int | None,
) -> tuple[float | None, int | None, int | None]:
    cost = _result_cost(event)
    if cost is not None:
        total_cost = _add_float(total_cost, cost)
    input_tokens, output_tokens = _result_usage_tokens(event)
    if output_tokens is not None:
        total_output = _add_int(total_output, output_tokens)
    if input_tokens is not None:
        total_input = _add_int(total_input, input_tokens)
    return total_cost, total_output, total_input


def _accumulate_usage(
    events: list[dict[str, Any]],
) -> tuple[float | None, int | None, int | None]:
    total_cost: float | None = None
    total_output: int | None = None
    total_input: int | None = None
    for event in events:
        if event.get("type") != "result":
            continue
        total_cost, total_output, total_input = _apply_result_event(
            event, total_cost, total_output, total_input
        )
    return total_cost, total_output, total_input


def run_summary_placeholder(
    model: str,
    effort: str | None,
    system_prompt: str,
) -> RunSummary:
    return RunSummary(
        model=model,
        effort=effort,
        system_prompt=system_prompt,
        total_cost_usd=0.0,
        total_output_tokens=0,
        total_input_tokens=0,
        tool_counts=[],
    )


def derive_run_summary(
    stream_path: Path,
    *,
    system_prompt: str,
    model: str = DEFAULT_CHAT_MODEL,
) -> RunSummary | None:
    events = _read_stream_events(stream_path)
    if not events:
        return None
    total_cost, total_output, total_input = _accumulate_usage(events)
    return RunSummary(
        model=model,
        effort=_effort_for_model(model),
        system_prompt=system_prompt,
        total_cost_usd=total_cost,
        total_output_tokens=total_output,
        total_input_tokens=total_input,
        tool_counts=_accumulate_tool_counts(events),
    )
