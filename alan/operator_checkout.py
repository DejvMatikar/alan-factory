from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from alan.operator_tmux import session_env

CLONE_TIMEOUT_S: Final[float] = 600.0
UPDATE_TIMEOUT_S: Final[float] = 120.0
PROBE_TIMEOUT_S: Final[float] = 15.0
_WARNING_MAX_CHARS: Final[int] = 300
_USERINFO_END: Final[frozenset[str]] = frozenset("/@ ")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HTTP_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_GIT_ENV: Final[dict[str, str]] = {"GIT_TERMINAL_PROMPT": "0"}


class CheckoutCloneFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckoutResult:
    path: Path
    warning: str | None


def install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def legacy_checkout_path() -> Path:
    root = install_root()
    return root.parent / f"{root.name}-operator"


def run_git(
    args: Sequence[str], *, cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env={**session_env(), **_GIT_ENV},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _mask_userinfo(after_scheme: str) -> str:
    stops = (i for i, char in enumerate(after_scheme) if char in _USERINFO_END)
    end = next(stops, len(after_scheme))
    if end == 0 or after_scheme[end : end + 1] != "@":
        return after_scheme
    return "***" + after_scheme[end:]


def _mask_credentials(text: str) -> str:
    head, *after_schemes = text.split("://")
    return "://".join([head, *map(_mask_userinfo, after_schemes)])


def redact(text: str) -> str:
    collapsed = " ".join(_CONTROL_CHARS.sub(" ", text).split())
    return _mask_credentials(collapsed)[:_WARNING_MAX_CHARS]


def _attempt(
    args: Sequence[str], *, cwd: Path, timeout: float
) -> tuple[bool, str]:
    try:
        result = run_git(args, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as exc:
        return False, exc.strerror or type(exc).__name__
    if result.returncode != 0:
        return False, redact(result.stderr or result.stdout)
    return True, result.stdout.strip()


def install_origin_url() -> str:
    ok, output = _attempt(
        ["config", "--get", "remote.origin.url"], cwd=install_root(), timeout=PROBE_TIMEOUT_S
    )
    if not ok or not output:
        raise CheckoutCloneFailed("the install has no origin remote to clone from")
    return output


def _embeds_credentials(url: str) -> bool:
    try:
        parts = urlsplit(url)
        password = parts.password
        username = parts.username
    except ValueError:
        return True
    return password is not None or (parts.scheme in _HTTP_SCHEMES and username is not None)


def clone(url: str, path: Path) -> None:
    if _embeds_credentials(url):
        raise CheckoutCloneFailed(
            "the origin remote embeds credentials; use a credential helper or an "
            "SSH remote so they stay out of the process list"
        )
    ok, output = _attempt(["clone", "--", url, str(path)], cwd=path.parent, timeout=CLONE_TIMEOUT_S)
    if not ok:
        raise CheckoutCloneFailed(f"clone failed: {output}")


def fast_forward(path: Path) -> str | None:
    for args in (["fetch", "--quiet"], ["merge", "--ff-only", "--quiet", "@{upstream}"]):
        ok, output = _attempt(args, cwd=path, timeout=UPDATE_TIMEOUT_S)
        if not ok:
            return redact(f"checkout not updated ({args[0]} failed): {output}")
    return None


__all__ = [
    "CheckoutCloneFailed",
    "CheckoutResult",
    "clone",
    "fast_forward",
    "install_origin_url",
    "install_root",
    "legacy_checkout_path",
    "redact",
    "run_git",
]
