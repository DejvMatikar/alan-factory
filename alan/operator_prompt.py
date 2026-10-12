from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path


class Scope(StrEnum):
    FLEET = "fleet"
    REPO = "repo"
    WORKSPACE = "workspace"


_TARGET_PATTERNS: dict[Scope, re.Pattern[str]] = {
    Scope.REPO: re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}"),
    Scope.WORKSPACE: re.compile(r"[a-z0-9][a-z0-9-]{0,63}"),
}


class InvalidScopeTarget(ValueError):
    pass


_SAFETY_RULES = (
    "Rules for this session:",
    "- The credential files of this install are blocked for you. Do not try to read them another "
    "way.",
    "- Inspect before you change anything, and say what you found before you act on it.",
    "- Act only on what the operator asks for in this terminal.",
    "- Never print, copy or transmit secrets or the control token.",
    "- Do not merge pull requests, delete branches or rewrite history unless the operator asks.",
    "- Leave the workspace and log directories listed above alone unless the operator asks you "
    "to edit them.",
)

_DOC_POINTERS = (
    ("architecture/README.md", "index of every subsystem"),
    ("architecture/unblocker.md", "how stuck work is unblocked"),
    ("architecture/deploy.md", "runbook, logs, restart"),
    ("architecture/operator_terminal.md", "this terminal"),
)


def validate_target(scope: Scope, target: str | None) -> str | None:
    if scope is Scope.FLEET:
        if target is not None:
            raise InvalidScopeTarget("fleet scope takes no target")
        return None
    if target is None or _TARGET_PATTERNS[scope].fullmatch(target) is None:
        raise InvalidScopeTarget(f"{scope.value} scope needs a valid target")
    return target


def workspace_prompt(
    *, task_id: str, repo_slug: str, workspace_dir: Path, logs_dir: Path, docs_dir: Path
) -> str:
    lines = [
        "You are the operator terminal of this alan install, running with the operator's own "
        "authority. Help the operator diagnose and unblock the fleet.",
        f"Scope: workspace {task_id}, in repository {repo_slug}. This session started in that "
        "repository's checkout.",
        f"Look at {workspace_dir} first: meta.json, the handovers and the worker stream logs.",
        "Where to look:",
        f"- workspace: {workspace_dir} (meta.json, the handovers and the operator transcripts)",
        f"- logs: {logs_dir}",
        *(f"- {docs_dir / name} ({purpose})" for name, purpose in _DOC_POINTERS),
        *_SAFETY_RULES,
    ]
    return "\n".join(lines)


__all__ = ["InvalidScopeTarget", "Scope", "validate_target", "workspace_prompt"]
