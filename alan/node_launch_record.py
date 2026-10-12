from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast, get_args

from typing_extensions import TypedDict

from alan.launch_spec import (
    EnvCategory,
    LaunchSpec,
    PromptSegment,
    SessionMode,
    flat_denied_tools,
)


class SegmentRecord(TypedDict):
    label: str
    text: str
    shared: bool


class EnvRecord(TypedDict):
    name: str
    category: str


class LaunchRecord(TypedDict):
    agent: str | None
    model: str | None
    effort: str | None
    max_turns: int | None
    permission_mode: str | None
    prompt_segments: list[SegmentRecord]
    allowed_tools: list[str]
    denied_tools: list[str]
    add_dirs: list[str]
    deny_all_tools: bool
    session_mode: str
    cwd: str | None
    idle_timeout_seconds: float | None
    abs_timeout_seconds: float | None
    fleet: bool
    detached: bool
    priority_delta: int | None
    env: list[EnvRecord]


def _segment_record(segment: PromptSegment) -> SegmentRecord:
    return SegmentRecord(label=segment.label, text=segment.text, shared=segment.shared)


def record_from_spec(spec: LaunchSpec) -> LaunchRecord:
    process = spec.process
    return LaunchRecord(
        agent=spec.agent.id if spec.agent is not None else None,
        model=spec.model,
        effort=spec.effort,
        max_turns=spec.max_turns,
        permission_mode=spec.permission_mode,
        prompt_segments=[_segment_record(segment) for segment in spec.prompt],
        allowed_tools=list(spec.allowed_tools),
        denied_tools=list(flat_denied_tools(spec.denied)),
        add_dirs=list(spec.add_dirs),
        deny_all_tools=spec.deny_all_tools,
        session_mode=spec.session_mode,
        cwd=process.cwd,
        idle_timeout_seconds=process.idle_timeout_seconds,
        abs_timeout_seconds=process.abs_timeout_seconds,
        fleet=process.fleet,
        detached=process.detached,
        priority_delta=process.priority_delta,
        env=[EnvRecord(name=entry.name, category=entry.category) for entry in process.env],
    )


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _optional(check: Callable[[object], bool]) -> Callable[[object], bool]:
    def _check(value: object) -> bool:
        return value is None or check(value)

    return _check


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _list_of(check: Callable[[object], bool]) -> Callable[[object], bool]:
    def _check(value: object) -> bool:
        if not isinstance(value, list):
            return False
        return all(check(item) for item in cast(list[Any], value))  # type: ignore[redundant-cast]

    return _check


def _one_of(allowed: tuple[str, ...]) -> Callable[[object], bool]:
    def _check(value: object) -> bool:
        return isinstance(value, str) and value in allowed

    return _check


def _dict_with(fields: dict[str, Callable[[object], bool]]) -> Callable[[object], bool]:
    def _check(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        item = cast(dict[str, Any], value)
        if set(item) != set(fields):
            return False
        return all(check(item[name]) for name, check in fields.items())

    return _check


_FIELD_CHECKS: dict[str, Callable[[object], bool]] = {
    "agent": _optional(_is_str),
    "model": _optional(_is_str),
    "effort": _optional(_is_str),
    "max_turns": _optional(_is_int),
    "permission_mode": _optional(_is_str),
    "prompt_segments": _list_of(
        _dict_with({"label": _is_str, "text": _is_str, "shared": _is_bool})
    ),
    "allowed_tools": _list_of(_is_str),
    "denied_tools": _list_of(_is_str),
    "add_dirs": _list_of(_is_str),
    "deny_all_tools": _is_bool,
    "session_mode": _one_of(get_args(SessionMode)),
    "cwd": _optional(_is_str),
    "idle_timeout_seconds": _optional(_is_number),
    "abs_timeout_seconds": _optional(_is_number),
    "fleet": _is_bool,
    "detached": _is_bool,
    "priority_delta": _optional(_is_int),
    "env": _list_of(
        _dict_with({"name": _is_str, "category": _one_of(get_args(EnvCategory))})
    ),
}


def parse_launch(raw: object) -> LaunchRecord | None:
    return cast(LaunchRecord, raw) if _dict_with(_FIELD_CHECKS)(raw) else None


__all__ = [
    "EnvRecord",
    "LaunchRecord",
    "SegmentRecord",
    "parse_launch",
    "record_from_spec",
]
