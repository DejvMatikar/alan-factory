from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from alan import process_runner

_GIT_TIMEOUT_S = 60.0
_GIT_ENV: Mapping[str, str | None] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_DIR": None,
    "GIT_WORK_TREE": None,
}
_NAME = r"[A-Za-z0-9._-]+"
_HTTPS_RE = re.compile(rf"\Ahttps://(?:[^/]*@)?github\.com/({_NAME})/({_NAME})\Z")
_SSH_RE = re.compile(rf"\Assh://(?:[^/]*@)?github\.com/({_NAME})/({_NAME})\Z")
_SCP_RE = re.compile(rf"\A(?:[^/:@]*@)?github\.com:({_NAME})/({_NAME})\Z")
_OWNER_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_REPO_RE = re.compile(r"\A[A-Za-z0-9._-]{1,100}\Z")
_GIT_SUFFIX = ".git"
_DOT_NAMES = frozenset({".", ".."})
_PIN_KEY = "remote.origin.url"


def read_origin(repo: Path) -> str:
    result = process_runner.run_subprocess(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        cwd=None,
        timeout=_GIT_TIMEOUT_S,
        env=_GIT_ENV,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def _split(url: str) -> tuple[str, str] | None:
    match = _HTTPS_RE.fullmatch(url) or _SSH_RE.fullmatch(url) or _SCP_RE.fullmatch(url)
    if match is None:
        return None
    return match[1], match[2].removesuffix(_GIT_SUFFIX)


def _valid(owner: str, name: str) -> bool:
    return bool(
        _OWNER_RE.fullmatch(owner) and _REPO_RE.fullmatch(name) and name not in _DOT_NAMES
    )


def parse_origin(url: str) -> tuple[str, str] | None:
    split = _split(url)
    if split is None or not _valid(*split):
        return None
    return split


def resolve_origin(repo: Path) -> tuple[str, str] | None:
    return parse_origin(read_origin(repo))


def slot_repo_url(owner: str, name: str) -> str:
    return f"https://github.com/{owner}/{name}{_GIT_SUFFIX}"


def pinned_environment(owner: str, name: str) -> Mapping[str, str | None]:
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": _PIN_KEY,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": _PIN_KEY,
        "GIT_CONFIG_VALUE_1": slot_repo_url(owner, name),
    }
