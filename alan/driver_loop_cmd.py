from __future__ import annotations

import os
import shlex
from pathlib import Path

from alan import agent_key, launch_spec
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.driver_loop_registry import DriverLoopEntry
from alan.engine_config import registered_secret_env_names
from alan.launch_spec import EnvEntry, LaunchSpec, ProcessSpec

VERIFIER_GH_TOKEN_ENV = "ALAN_VERIFIER_GH_TOKEN"  # noqa: S105  # nosec B105

_CODE_OWNED_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash(gh api:*)",
    "Bash(gh pr merge:*)",
    "Bash(gh repo delete:*)",
    "Bash(gh auth:*)",
    "Bash(rm -rf:*)",
    "NotebookEdit",
    "WebSearch",
)

def _prompt(entry: DriverLoopEntry) -> str:
    if entry.args:
        return f"{entry.command} {entry.args}"
    return entry.command


_ALWAYS_AVAILABLE_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")


def _bash_family_granted(entry: DriverLoopEntry) -> bool:
    return any(tool.startswith("Bash(") for tool in entry.allowed_tools)


def _effective_tool_gate(entry: DriverLoopEntry) -> tuple[str, ...]:
    families = list(_ALWAYS_AVAILABLE_TOOLS)
    if _bash_family_granted(entry):
        families.append("Bash")
    if entry.own_state_write:
        families.extend(("Write", "Edit"))
    if entry.web_fetch_domains:
        families.append("WebFetch")
    return tuple(families)


def _state_write_tools(entry: DriverLoopEntry) -> tuple[str, ...]:
    if not entry.own_state_write:
        return ()
    state_dir = str(entry.state_dir)
    return (f"Write(/{state_dir}/**)", f"Edit(/{state_dir}/**)")


def _web_fetch_tools(entry: DriverLoopEntry) -> tuple[str, ...]:
    return tuple(f"WebFetch(domain:{domain})" for domain in entry.web_fetch_domains)


def _effective_allowed_tools(entry: DriverLoopEntry) -> tuple[str, ...]:
    return (*entry.allowed_tools, *_state_write_tools(entry), *_web_fetch_tools(entry))


def _effective_add_dirs(entry: DriverLoopEntry) -> tuple[str, ...]:
    add_dirs = tuple(str(Path(d).expanduser()) for d in entry.add_dirs)
    if not entry.own_state_write:
        return add_dirs
    return (*add_dirs, str(entry.state_dir))


def build_spec(entry: DriverLoopEntry) -> LaunchSpec:
    return LaunchSpec(
        layout="driver_loop",
        agent=agent_key.driver_loop(entry.name),
        model=entry.model or None,
        output_format="stream-json",
        permission_mode="dontAsk",
        prompt_arg=_prompt(entry),
        requested_tools=tuple(entry.allowed_tools),
        allowed_tools=_effective_allowed_tools(entry),
        add_dirs=_effective_add_dirs(entry),
        denied=(" ".join(_CODE_OWNED_DISALLOWED_TOOLS),),
        tool_gate=_effective_tool_gate(entry),
        process=ProcessSpec(
            cwd=str(Path(entry.cwd).expanduser()),
            abs_timeout_seconds=float(entry.timeout_seconds),
            detached=True,
            env=env_policy(),
        ),
    )


def build_argv(entry: DriverLoopEntry) -> list[str]:
    return launch_spec.to_argv(build_spec(entry))


def render_for_doc(entry: DriverLoopEntry) -> str:
    return shlex.join(build_argv(entry))


def _forbidden_env_names() -> frozenset[str]:
    return registered_secret_env_names() | {VERIFIER_GH_TOKEN_ENV, CONTROL_TOKEN_ENV}


def _allowlisted_env_candidates() -> dict[str, str | None]:
    return {
        "PATH": os.environ.get("PATH"),
        "HOME": os.environ.get("HOME"),
        "LANG": os.environ.get("LANG"),
        "LC_ALL": os.environ.get("LC_ALL"),
        "TERM": os.environ.get("TERM"),
        "USER": os.environ.get("USER"),
        "SHELL": os.environ.get("SHELL"),
    }


def build_env() -> dict[str, str]:
    forbidden = _forbidden_env_names()
    return {
        name: value
        for name, value in _allowlisted_env_candidates().items()
        if value is not None and name not in forbidden
    }


def env_policy() -> tuple[EnvEntry, ...]:
    forbidden = _forbidden_env_names()
    return tuple(
        EnvEntry(name, "allowlisted")
        for name in _allowlisted_env_candidates()
        if name not in forbidden
    )


__all__ = [
    "VERIFIER_GH_TOKEN_ENV",
    "build_argv",
    "build_env",
    "build_spec",
    "env_policy",
    "render_for_doc",
]
