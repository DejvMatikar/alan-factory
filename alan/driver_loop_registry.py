from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alan.roots import STATE_ROOT_SUBDIRS, driver_loop_state_root

_CONFIG_DIRNAME = "driver_loops.d"
_ENGINE_FILE_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

ISSUE_DRIVER_NAMES: frozenset[str] = frozenset({"alan-issue-driver"})

_RESERVED_NAMES: frozenset[str] = frozenset(STATE_ROOT_SUBDIRS.values()) | {"secrets"}

_BASE_ALLOWED_TOOLS: frozenset[str] = frozenset({"Read", "Grep", "Glob"})

_GIT_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git show:*)",
    "Bash(git branch --list:*)",
    "Bash(git branch --show-current:*)",
    "Bash(git fetch:*)",
    "Bash(git merge-base:*)",
    "Bash(git rev-parse:*)",
})

_GH_ISSUE_TOOLS: frozenset[str] = frozenset({
    "Bash(gh issue view:*)",
    "Bash(gh issue list:*)",
    "Bash(gh issue comment:*)",
    "Bash(gh issue close:*)",
    "Bash(gh issue edit:*)",
    "Bash(gh issue create:*)",
    "Bash(gh label create:*)",
})

_GH_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Bash(gh pr list:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr checks:*)",
    "Bash(gh run list:*)",
    "Bash(gh run view:*)",
    "Bash(gh label list:*)",
})

LOCAL_API_TOOL = "Bash(curl http://127.0.0.1:8585/api/*)"

_EXACT_ALLOWED_TOOLS: frozenset[str] = (
    _BASE_ALLOWED_TOOLS | _GIT_READ_ONLY_TOOLS | _GH_ISSUE_TOOLS | _GH_READ_ONLY_TOOLS
    | frozenset({LOCAL_API_TOOL})
)

_WEBFETCH_DOMAIN_RE = re.compile(
    r"^WebFetch\(domain:([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,})\)$"
)

_FORBIDDEN_TOOL_SUBSTRINGS: tuple[str, ...] = (
    "gh api",
    "gh pr merge",
    "gh repo delete",
    "gh auth",
)


class DriverLoopValidationError(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class DriverLoopEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    command: str = Field(min_length=1)
    args: str = ""
    cwd: str = Field(min_length=1)
    dedicated_clone: bool = False
    interval_seconds: int | None = None
    self_paced: bool = False
    enabled: bool = False
    timeout_seconds: int = Field(default=3600, gt=0)
    model: str | None = None
    add_dirs: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    own_state_write: bool = False
    web_fetch_domains: tuple[str, ...] = ()

    @property
    def state_dir(self) -> Path:
        return driver_loop_state_root() / self.name


@dataclass(frozen=True)
class QuarantinedDriverLoop:
    path: Path
    error: str


def config_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / _CONFIG_DIRNAME


def _config_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix in _ENGINE_FILE_SUFFIXES
    )


def _check_forbidden_substrings(tool: str) -> None:
    low = tool.lower()
    for bad in _FORBIDDEN_TOOL_SUBSTRINGS:
        if bad in low:
            raise DriverLoopValidationError("raw_gh_api_forbidden", tool)


def _check_tool_in_family(tool: str) -> None:
    if tool in _EXACT_ALLOWED_TOOLS:
        return
    if _WEBFETCH_DOMAIN_RE.match(tool):
        return
    raise DriverLoopValidationError("tool_not_in_allow_list", tool)


def _validate_tools(entry: DriverLoopEntry) -> None:
    for tool in entry.allowed_tools:
        _check_forbidden_substrings(tool)
        _check_tool_in_family(tool)


def _validate_web_fetch_domains(entry: DriverLoopEntry) -> None:
    for domain in entry.web_fetch_domains:
        if not _WEBFETCH_DOMAIN_RE.match(f"WebFetch(domain:{domain})"):
            raise DriverLoopValidationError("web_fetch_domain_invalid", domain)


def _check_not_inside_alan_home(path_str: str, *, field: str) -> None:
    home_alan = Path.home() / ".alan"
    expanded = Path(path_str).expanduser()
    if not expanded.is_absolute():
        raise DriverLoopValidationError(f"{field}_not_absolute", path_str)
    if expanded == home_alan or home_alan in expanded.parents:
        raise DriverLoopValidationError(f"{field}_inside_alan_home", path_str)


def _validate_paths(entry: DriverLoopEntry) -> None:
    _check_not_inside_alan_home(entry.cwd, field="cwd")
    for add_dir in entry.add_dirs:
        _check_not_inside_alan_home(add_dir, field="add_dir")


def _validate_name(entry: DriverLoopEntry) -> None:
    if not _NAME_RE.match(entry.name):
        raise DriverLoopValidationError("invalid_name", entry.name)
    if entry.name in _RESERVED_NAMES:
        raise DriverLoopValidationError("name_collides_with_reserved_subdir", entry.name)


def _validate_schedule(entry: DriverLoopEntry) -> None:
    has_interval = entry.interval_seconds is not None
    if has_interval == entry.self_paced:
        detail = f"interval_seconds={entry.interval_seconds!r} self_paced={entry.self_paced!r}"
        raise DriverLoopValidationError("schedule_ambiguous", detail)
    interval = entry.interval_seconds
    if interval is not None and interval <= 0:
        raise DriverLoopValidationError("schedule_interval_not_positive", str(interval))


def _validate_grants(entry: DriverLoopEntry) -> None:
    has_grants = bool(entry.allowed_tools) or bool(entry.add_dirs) or entry.own_state_write or bool(
        entry.web_fetch_domains
    )
    if has_grants and entry.name not in ISSUE_DRIVER_NAMES:
        raise DriverLoopValidationError("grants_on_non_issue_driver", entry.name)


def validate_entry(entry: DriverLoopEntry) -> None:
    _validate_name(entry)
    _validate_schedule(entry)
    _validate_grants(entry)
    _validate_paths(entry)
    _validate_tools(entry)
    _validate_web_fetch_domains(entry)


def _parse_entry(path: Path) -> DriverLoopEntry:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{path.name}: cannot read file") from exc
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected mapping at root")
    try:
        return DriverLoopEntry.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def load_registry(
    directory: Path | None = None,
) -> tuple[list[DriverLoopEntry], list[QuarantinedDriverLoop]]:
    target = directory if directory is not None else config_dir()
    entries: list[DriverLoopEntry] = []
    quarantined: list[QuarantinedDriverLoop] = []
    seen_names: set[str] = set()
    for path in _config_paths(target):
        try:
            entry = _parse_entry(path)
            validate_entry(entry)
        except (ValueError, DriverLoopValidationError) as exc:
            quarantined.append(QuarantinedDriverLoop(path=path, error=str(exc)))
            continue
        if entry.name in seen_names:
            quarantined.append(
                QuarantinedDriverLoop(path=path, error=f"duplicate loop name {entry.name!r}")
            )
            continue
        seen_names.add(entry.name)
        entries.append(entry)
    return entries, quarantined


__all__ = [
    "ISSUE_DRIVER_NAMES",
    "LOCAL_API_TOOL",
    "DriverLoopEntry",
    "DriverLoopValidationError",
    "QuarantinedDriverLoop",
    "config_dir",
    "load_registry",
    "validate_entry",
]
