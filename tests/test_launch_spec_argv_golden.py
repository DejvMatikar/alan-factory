from __future__ import annotations

import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from alan import claude_cli, driver_loop_cmd, launch_spec, operator_launch
from alan import headless_prompt as headless_prompt_mod
from alan.claude_cli_grants import (
    NO_TOOLS_BLOCK,
    credential_deny_rules,
    disallowed_tools_args,
    grant_args,
)
from alan.driver_loop_registry import DriverLoopEntry
from alan.operator_prompt import Scope


def _legacy_appended(system_prompt: str, deny_all_tools: bool) -> str:
    if deny_all_tools:
        return f"{system_prompt}\n\n{NO_TOOLS_BLOCK}"
    block = headless_prompt_mod.HEADLESS_EXECUTION_BLOCK
    return f"{system_prompt}\n\n{block}{headless_prompt_mod.project_doc_block()}"


def _legacy_build_cmd(
    system_prompt: str,
    *,
    output_format: str,
    max_turns: int,
    model: str = "sonnet",
    effort: str | None = None,
    extra: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    add_dirs: list[str] | None = None,
    permission_mode: str = "auto",
    deny_all_tools: bool = False,
    session_id: str | None = None,
    resume_session_id: str | None = None,
) -> list[str]:
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        output_format,
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        permission_mode,
        "--append-system-prompt",
        _legacy_appended(system_prompt, deny_all_tools),
    ]
    if effort is not None:
        cmd.extend(["--effort", effort])
    cmd.extend(
        grant_args(
            allowed_tools=allowed_tools,
            add_dirs=add_dirs,
            deny_all_tools=deny_all_tools,
            permission_mode=permission_mode,
        )
    )
    cmd.extend(
        disallowed_tools_args(deny_all_tools=deny_all_tools, permission_mode=permission_mode)
    )
    if resume_session_id is not None:
        cmd.extend(["--resume", resume_session_id])
    elif session_id is not None:
        cmd.extend(["--session-id", session_id])
    if extra is not None:
        cmd.extend(extra)
    return cmd


def _legacy_chat_cmd(
    *,
    model: str,
    session_id: str,
    resume: bool,
    max_turns: int,
    allowed_tools: list[str],
    system_prompt: str,
    effort: str | None = None,
) -> list[str]:
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "auto",
    ]
    cmd.extend(grant_args(allowed_tools=allowed_tools, add_dirs=None, deny_all_tools=False))
    cmd.extend(disallowed_tools_args(deny_all_tools=False, permission_mode="auto"))
    if effort is not None:
        cmd.extend(["--effort", effort])
    if resume:
        cmd.extend(["--resume", session_id])
    else:
        cmd.extend(["--session-id", session_id])
    cmd.extend(["--append-system-prompt", system_prompt])
    return cmd


def _legacy_driver_argv(entry: DriverLoopEntry) -> list[str]:
    prompt = f"{entry.command} {entry.args}" if entry.args else entry.command
    argv: list[str] = [
        "claude",
        "-p",
        prompt,
        "--permission-mode",
        "dontAsk",
        "--tools",
        *driver_loop_cmd._effective_tool_gate(entry),  # noqa: SLF001
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    allowed = driver_loop_cmd._effective_allowed_tools(entry)  # noqa: SLF001
    if allowed:
        argv.extend(["--allowedTools", *allowed])
    argv.extend(["--disallowedTools", " ".join(driver_loop_cmd._CODE_OWNED_DISALLOWED_TOOLS)])  # noqa: SLF001
    add_dirs = driver_loop_cmd._effective_add_dirs(entry)  # noqa: SLF001
    if add_dirs:
        argv.extend(["--add-dir", *add_dirs])
    if entry.model:
        argv.extend(["--model", entry.model])
    return argv


def _legacy_operator_argv(prompt: str | None, add_dirs: Sequence[Path]) -> tuple[str, ...]:
    return (
        "claude",
        *([prompt] if prompt is not None else []),
        *(["--add-dir", *(str(path.absolute()) for path in add_dirs)] if add_dirs else []),
        "--disallowedTools",
        *credential_deny_rules(),
    )


_PROMPT = "role prompt"
_TOOL_SETS: list[list[str] | None] = [
    None,
    [],
    ["Read", "Bash(uv run:*)", "Bash(npx:*)", "Bash(git log:*)"],
]
_DIR_SETS: list[list[str] | None] = [None, [], ["/work/a", "/work/b"]]
_SESSIONS: list[tuple[str | None, str | None]] = [
    (None, None),
    ("sid-1", None),
    (None, "rid-1"),
    ("sid-1", "rid-1"),
]


def _build_cmd_combinations() -> list[dict[str, Any]]:
    combos = itertools.product(
        ("json", "stream-json"),
        ("sonnet", "opus", "haiku"),
        (None, "high"),
        (None, ["--verbose"]),
        _TOOL_SETS,
        _DIR_SETS,
        ("auto", "acceptEdits"),
        (False, True),
        _SESSIONS,
    )
    return [
        {
            "output_format": fmt,
            "max_turns": 7,
            "model": model,
            "effort": effort,
            "extra": extra,
            "allowed_tools": tools,
            "add_dirs": dirs,
            "permission_mode": mode,
            "deny_all_tools": deny,
            "session_id": sid,
            "resume_session_id": rid,
        }
        for fmt, model, effort, extra, tools, dirs, mode, deny, (sid, rid) in combos
    ]


@pytest.mark.parametrize("project_doc", ["", "\n\nPROJECT DOC block\n"])
def test_build_cmd_is_byte_identical_for_every_flag_combination(
    monkeypatch: pytest.MonkeyPatch, project_doc: str
) -> None:
    monkeypatch.setattr(claude_cli, "_project_doc_block", lambda: project_doc)
    monkeypatch.setattr(headless_prompt_mod, "project_doc_block", lambda: project_doc)
    for kwargs in _build_cmd_combinations():
        assert claude_cli._build_cmd(_PROMPT, **kwargs) == _legacy_build_cmd(  # noqa: SLF001
            _PROMPT, **kwargs
        ), kwargs


def test_build_cmd_turn_budget_prompt_is_byte_identical() -> None:
    prompt = f"{_PROMPT}{launch_spec.TURN_BUDGET_PREFIX}150"
    for deny in (False, True):
        kwargs: dict[str, Any] = {
            "output_format": "stream-json",
            "max_turns": 3,
            "deny_all_tools": deny,
        }
        assert claude_cli._build_cmd(prompt, **kwargs) == _legacy_build_cmd(  # noqa: SLF001
            prompt, **kwargs
        )


def test_chat_cmd_is_byte_identical_for_every_flag_combination() -> None:
    combos = itertools.product(
        ("sonnet", "opus", "haiku"),
        (True, False),
        _TOOL_SETS[1:],
        (None, "xhigh"),
    )
    for model, resume, tools, effort in combos:
        kwargs: dict[str, Any] = {
            "model": model,
            "session_id": "chat-sid",
            "resume": resume,
            "max_turns": 80,
            "allowed_tools": tools,
            "system_prompt": "chat prompt",
            "effort": effort,
        }
        assert claude_cli._chat_cmd(**kwargs) == _legacy_chat_cmd(**kwargs), kwargs  # noqa: SLF001


def _driver_entries(tmp_path: Path) -> list[DriverLoopEntry]:
    base: dict[str, Any] = {"name": "loop", "command": "/loop", "cwd": str(tmp_path)}
    variants: list[dict[str, Any]] = [
        {},
        {"args": "--flag value"},
        {"model": "opus"},
        {"allowed_tools": ["Read", "Bash(git log:*)"]},
        {"own_state_write": True},
        {"web_fetch_domains": ["example.com"]},
        {"add_dirs": ["~/docs"]},
        {
            "args": "go",
            "model": "sonnet",
            "own_state_write": True,
            "allowed_tools": ["Bash(gh issue view:*)"],
            "web_fetch_domains": ["docs.example.org"],
            "add_dirs": [str(tmp_path / "extra")],
        },
    ]
    return [DriverLoopEntry.model_validate({**base, **variant}) for variant in variants]


def test_driver_loop_argv_is_byte_identical(tmp_path: Path) -> None:
    for entry in _driver_entries(tmp_path):
        assert driver_loop_cmd.build_argv(entry) == _legacy_driver_argv(entry)


def test_operator_argv_is_byte_identical(tmp_path: Path) -> None:
    dirs = [tmp_path / "a", tmp_path / "b"]
    for prompt, add_dirs in itertools.product((None, "operator prompt"), ([], dirs[:1], dirs)):
        for scope in Scope:
            actual = operator_launch._argv(scope, prompt, add_dirs)  # noqa: SLF001
            assert actual == _legacy_operator_argv(prompt, add_dirs)


def test_to_argv_dispatches_on_layout(tmp_path: Path) -> None:
    entry = _driver_entries(tmp_path)[0]
    spec = driver_loop_cmd.build_spec(entry)
    assert launch_spec.to_argv(spec)[0] == launch_spec.CLAUDE_EXECUTABLE
    assert spec.layout == "driver_loop"
