from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli, launch_spec
from alan.agent_catalogue_helpers import ResolvedAgent
from alan.agent_catalogue_models import PromptSegmentOut
from alan.agent_key import AgentKey
from alan.engine_config import EngineConfig
from alan.launch_spec import LaunchSpec

ALAN_ROOT = Path(__file__).resolve().parent.parent / "alan"

LAUNCH_FUNCTIONS: frozenset[str] = frozenset({"run_agent", "run_agent_streaming", "run_chat_turn"})

SPEC_FIELDS: tuple[str, ...] = (
    "layout",
    "model",
    "effort",
    "output_format",
    "max_turns",
    "permission_mode",
    "deny_all_tools",
    "tool_gate",
    "session_mode",
    "extra",
    "requested_tools",
    "allowed_tools",
    "denied",
)

PROCESS_FIELDS: tuple[str, ...] = (
    "idle_timeout_seconds",
    "abs_timeout_seconds",
    "fleet",
    "detached",
    "priority_delta",
    "env",
)

DEFAULT_RESULT_TEXT = "ok"


_WILDCARD = "(?s:.*)"


def template_regex(segments: Sequence[PromptSegmentOut]) -> re.Pattern[str]:
    parts: list[str] = []
    for segment in segments:
        part = re.escape(segment.text) if segment.kind == "text" else _WILDCARD
        if not (part == _WILDCARD and parts and parts[-1] == _WILDCARD):
            parts.append(part)
    return re.compile("".join(parts), re.DOTALL)


@dataclass
class Captured:
    spec: LaunchSpec
    user_text: str | None = None


def _no_reply(_spec: LaunchSpec) -> str:
    return DEFAULT_RESULT_TEXT


@dataclass
class LaunchRecorder:
    launches: list[Captured] = field(default_factory=lambda: [])
    reply: Callable[[LaunchSpec], str] = _no_reply

    def for_agent(self, key: AgentKey) -> list[Captured]:
        return [captured for captured in self.launches if captured.spec.agent == key]

    def agents(self) -> set[AgentKey]:
        return {c.spec.agent for c in self.launches if c.spec.agent is not None}

    def attach_user_text(self, user_text: str) -> None:
        self.launches[-1].user_text = user_text


def _result_event(text: str) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "result": text, "usage": {}}


def install_recorder(monkeypatch: pytest.MonkeyPatch) -> LaunchRecorder:
    recorder = LaunchRecorder()
    real_to_argv = launch_spec.to_argv

    def spy(spec: LaunchSpec) -> list[str]:
        recorder.launches.append(Captured(spec))
        return real_to_argv(spec)

    def fake_stream(cmd: list[str], user_text: str, **_kwargs: Any) -> list[Any]:
        recorder.attach_user_text(user_text)
        event = _result_event(recorder.reply(recorder.launches[-1].spec))
        return [(json.dumps(event), event)]

    def fake_invoke(cmd: list[str], user_text: str) -> subprocess.CompletedProcess[str]:
        recorder.attach_user_text(user_text)
        payload = {"result": recorder.reply(recorder.launches[-1].spec)}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(launch_spec, "to_argv", spy)
    monkeypatch.setattr(claude_cli, "_stream_subprocess", fake_stream)
    monkeypatch.setattr(claude_cli, "_invoke_cli", fake_invoke)
    return recorder


@dataclass(frozen=True)
class ParityEnv:
    monkeypatch: pytest.MonkeyPatch
    tmp_path: Path
    recorder: LaunchRecorder
    cfg: EngineConfig


DriverFn = Callable[[ParityEnv], None]


@dataclass(frozen=True)
class Driver:
    name: str
    covers: Callable[[EngineConfig], Sequence[AgentKey]]
    run: DriverFn


DRIVERS: list[Driver] = []


def register(driver: Driver) -> Driver:
    DRIVERS.append(driver)
    return driver


def covering(key: AgentKey, cfg: EngineConfig) -> Driver | None:
    return next((d for d in DRIVERS if key in d.covers(cfg)), None)


def _spec_values(spec: LaunchSpec, *, with_cwd: bool) -> dict[str, Any]:
    values: dict[str, Any] = {name: getattr(spec, name) for name in SPEC_FIELDS}
    values.update({name: getattr(spec.process, name) for name in PROCESS_FIELDS})
    if with_cwd:
        values["cwd"] = spec.process.cwd
    return values


def spec_differences(
    expected: LaunchSpec, actual: LaunchSpec, resolved: ResolvedAgent
) -> dict[str, tuple[Any, Any]]:
    with_cwd = not resolved.cwd_is_runtime
    want = _spec_values(expected, with_cwd=with_cwd)
    got = _spec_values(actual, with_cwd=with_cwd)
    found = {name: (want[name], got[name]) for name in want if want[name] != got[name]}
    found.update(_add_dir_differences(expected, actual, resolved))
    return found


def _dir_pattern(expected: str) -> re.Pattern[str]:
    parts = re.split("\u27e6slot:[A-Z0-9_]+\u27e7", expected)
    return re.compile(".+".join(re.escape(part) for part in parts), re.DOTALL)


def _dirs_match(expected: Sequence[str], actual: Sequence[str]) -> bool:
    return len(expected) == len(actual) and all(
        _dir_pattern(want).fullmatch(got) is not None
        for want, got in zip(expected, actual, strict=True)
    )


def _add_dir_differences(
    expected: LaunchSpec, actual: LaunchSpec, resolved: ResolvedAgent
) -> dict[str, tuple[Any, Any]]:
    base = len(expected.add_dirs)
    extra = len(resolved.conditional_add_dirs)
    lengths = {base, base + extra}
    ok = len(actual.add_dirs) in lengths and _dirs_match(expected.add_dirs, actual.add_dirs[:base])
    return {} if ok else {"add_dirs": (tuple(expected.add_dirs), tuple(actual.add_dirs))}


def prompt_mismatch(resolved: ResolvedAgent, entry_prompt: Sequence[PromptSegmentOut],
                    actual: LaunchSpec) -> str | None:
    appended = launch_spec.appended_prompt(actual)
    if appended is None:
        return None if not entry_prompt else "entry has a prompt but the launch appended none"
    if template_regex(entry_prompt).fullmatch(appended) is None:
        return f"template does not match the appended prompt of {resolved.key.id}"
    return None


@dataclass(frozen=True)
class CallSite:
    path: str
    line: int
    function: str
    has_agent: bool
    agent_is_none: bool


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _agent_keyword(node: ast.Call) -> ast.keyword | None:
    return next((kw for kw in node.keywords if kw.arg == "agent"), None)


def production_call_sites(root: Path = ALAN_ROOT) -> list[CallSite]:
    sites: list[CallSite] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if "web" in relative.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or _callee_name(node) not in LAUNCH_FUNCTIONS:
                continue
            keyword = _agent_keyword(node)
            is_none = (
                keyword is not None
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is None
            )
            sites.append(
                CallSite(
                    path=f"alan/{relative.as_posix()}",
                    line=node.lineno,
                    function=str(_callee_name(node)),
                    has_agent=keyword is not None,
                    agent_is_none=is_none,
                )
            )
    return sites
